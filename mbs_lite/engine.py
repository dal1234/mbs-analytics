"""Monthly cash-flow and income projection, vectorised over CUSIPs and Monte Carlo rate paths.

For every CUSIP i, path p and month t (t = 1 .. horizon):

    scheduled_t   = level payment on balance_{t-1} over the remaining term at the WAC, less interest at the WAC
    prepaid_t     = (balance_{t-1} - scheduled_t) x SMM_t                      (SMM from the prepayment model)
    balance_t     = balance_{t-1} - prepaid_t - scheduled_t
    interest_t    = balance_{t-1} x coupon / 1200                              (interest on the held security)
    reinv_bal_t   = reinv_bal_{t-1} + prepaid_t                                (prepaid UPB is reinvested)
    reinv_inc_t   = reinv_bal_{t-1} x (designated rate_{t-1} + spread) / 1200  (1M SOFR at the start of the month)
    income_t      = interest_t + reinv_inc_t

Principal prepaid in month t starts earning in month t+1. Scheduled principal leaves the book unless
income.reinvest_scheduled_principal is true.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .prepay import SCurvePrepay, cpr_to_smm

AGG = ("begin_upb", "sched", "prepaid", "end_upb", "interest", "reinv_bal", "reinv_end", "reinv_income", "income",
       "prepay_base")
DETAIL = ("begin_upb", "sched", "prepaid", "end_upb", "interest", "reinv_bal", "reinv_income", "income", "cpr",
          "reinv_rate", "mtg_rate")
GROUP = ("income", "interest", "reinv_income", "prepaid", "prepay_base")


@dataclass
class Projection:
    months: int
    agg: dict                          # name -> (P, H) portfolio totals per path and month
    group: dict | None = None          # name -> (P, G, Y) totals per path, cohort and year
    detail: dict = field(default_factory=dict)   # name -> (N, H) CUSIP-month values averaged over the paths given

    @property
    def n_years(self) -> int:
        return int(np.ceil(self.months / 12))


def _level_sched(upb, wac, wam):
    """Scheduled principal of a level-payment loan with ``wam`` months left at ``wac``."""
    r = wac / 1200.0
    n = np.maximum(wam, 1).astype(float)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        annuity = np.where(r > 1e-12, r / (1.0 - (1.0 + r) ** (-n)), 1.0 / n)
    sched = np.clip(upb * annuity - upb * r, 0.0, upb)
    return np.where(wam <= 1, upb, sched)


def project(df, cube: np.ndarray, rates, prepay: SCurvePrepay, income_cfg: dict, horizon: int,
            groups: np.ndarray | None = None, mean_detail: bool = False, max_cells: int = 4_000_000) -> Projection:
    """Project every CUSIP in ``df`` along each rate path in ``cube`` (paths x months 0..H x series).

    ``groups``      integer cohort code per CUSIP: also return income per path, cohort and year
    ``mean_detail`` also return CUSIP-month values averaged over all paths in ``cube``
    Paths are processed in blocks of about ``max_cells`` CUSIP-paths, which bounds memory.
    """
    P, N, H = cube.shape[0], len(df), horizon
    Y = int(np.ceil(H / 12))
    wac = df["wac"].to_numpy(float)
    cpn = df["coupon"].to_numpy(float)
    upb0 = df["current_upb"].to_numpy(float)
    wam0 = df["wam"].to_numpy(float).round()
    age0 = df["wala"].to_numpy(float)
    burn0 = df["burnout_years"].to_numpy(float)
    st = prepay.static(df["product"].to_numpy(), df["story_group"].to_numpy(), df["avg_loan_size"].to_numpy(float))
    imtg = rates.col("MTG_30Y")
    irr = rates.col(income_cfg.get("reinvest_rate_series", "SOFR_1M"))
    spread = float(income_cfg.get("reinvest_spread_bp", 0.0)) / 100.0
    reinv_sched = bool(income_cfg.get("reinvest_scheduled_principal", False))
    moy = [(rates.as_of.month - 1 + m) % 12 + 1 for m in range(1, H + 1)]   # calendar month of accrual month m
    obs = rates.mtg_observed                                                 # months -2, -1, 0

    agg = {k: np.zeros((P, H)) for k in AGG}
    G = int(groups.max()) + 1 if groups is not None else 0
    grp = {k: np.zeros((P, G, Y)) for k in GROUP} if groups is not None else None
    det = {k: np.zeros((N, H)) for k in DETAIL} if mean_detail else {}
    chunk = max(1, int(max_cells // max(N, 1)))

    for a in range(0, P, chunk):
        b = min(P, a + chunk)
        c = cube[a:b]
        p = b - a
        upb = np.tile(upb0, (p, 1))
        wam = np.tile(wam0, (p, 1))
        age = np.tile(age0, (p, 1))
        burn = np.tile(burn0, (p, 1))
        reinv = np.zeros((p, N))
        yacc = {k: np.zeros((p, N)) for k in GROUP} if grp is not None else None
        gidx = (np.arange(p)[:, None] * G + groups[None, :]).ravel() if grp is not None else None

        def mtg(month):                                  # borrowers see actual rates for months <= 0
            return obs[month + 2] if month <= 0 else c[:, month, imtg][:, None]

        for m in range(1, H + 1):
            t = m - 1
            lag1, lag2 = mtg(m - 1), mtg(m - 2)
            cpr = prepay.cpr(st, wac, age, burn, lag1, lag2, moy[t])
            smm = cpr_to_smm(cpr)
            live = (upb > 0) & (wam > 0)
            begin = np.where(live, upb, 0.0)
            sched = np.where(live, _level_sched(begin, wac, wam), 0.0)
            base = begin - sched
            prepaid = base * smm
            interest = begin * cpn / 1200.0
            rate = (c[:, m - 1, irr] + spread)[:, None]
            reinv_begin = reinv
            reinv_inc = reinv_begin * rate / 1200.0
            reinv = reinv_begin + prepaid + (sched if reinv_sched else 0.0)
            end = np.maximum(begin - sched - prepaid, 0.0)
            income = interest + reinv_inc
            vals = {"begin_upb": begin, "sched": sched, "prepaid": prepaid, "end_upb": end, "interest": interest,
                    "reinv_bal": reinv_begin, "reinv_end": reinv, "reinv_income": reinv_inc, "income": income,
                    "prepay_base": base}
            for k in AGG:
                agg[k][a:b, t] = vals[k].sum(axis=1)
            if yacc is not None:
                for k in GROUP:
                    yacc[k] += vals[k]
                if m % 12 == 0 or m == H:
                    y = t // 12
                    for k in GROUP:
                        grp[k][a:b, :, y] = np.bincount(gidx, weights=yacc[k].ravel(), minlength=p * G).reshape(p, G)
                        yacc[k][:] = 0.0
            if mean_detail:
                for k in ("begin_upb", "sched", "prepaid", "end_upb", "interest", "reinv_bal", "reinv_income", "income"):
                    det[k][:, t] += vals[k].sum(axis=0) / P
                det["cpr"][:, t] += np.broadcast_to(cpr, (p, N)).sum(axis=0) / P
                det["reinv_rate"][:, t] += float(rate.sum()) / P
                det["mtg_rate"][:, t] += float(np.broadcast_to(lag1, (p, 1)).sum()) / P

            upb = end
            burn = burn + np.where(wac - lag1 > 0.5, 1.0 / 12.0, 0.0)
            age = age + 1
            wam = np.maximum(wam - 1, 0)
    return Projection(H, agg, grp, det)


def cum_by_year(monthly: np.ndarray, months: int) -> np.ndarray:
    """Cumulative sums at the end of years 1..Y from a (..., H) monthly array -> (..., Y)."""
    Y = int(np.ceil(months / 12))
    c = np.cumsum(monthly, axis=-1)
    return np.stack([c[..., min(12 * (y + 1), months) - 1] for y in range(Y)], axis=-1)


def cpr_from(prepaid, base) -> np.ndarray:
    """Annualised CPR from summed prepaid principal and the balance exposed to prepayment (average SMM)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        smm = np.where(base > 0, prepaid / base, 0.0)
    return 100.0 * (1.0 - (1.0 - smm) ** 12)
