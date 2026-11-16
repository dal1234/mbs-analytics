"""Whole loans (single-family), ILLUSTRATIVE: a cohort-level runoff model with constant monthly delinquency transitions.
Separate from the MBS income and EaR for now (dashboard "Whole loans" view).

Cohort file (config whole_loans.file), one row per cohort, UPB in $ millions:
    id, behavior, status, upb, note_rate, servicing_bp, deferred_share, product, wala, wam, ltv, fico, avg_loan
    behavior = a key of whole_loans.behavior (transition rates, prepayment multiplier)
    status   = current | dq (30-89 days) | sdq (90+ days or in foreclosure, nonaccrual): the starting state
    deferred_share = share of UPB that earns no interest (deferred under a modification), repaid at payoff

Each month t, per cohort and rate path, balances C (current), D (30-89) and S (90+, nonaccrual):
    interest      = (C + D) x (1 - deferred_share) x (note_rate - servicing) / 1200      cash coupon; S accrues nothing
    scheduled     = level payment on C over the remaining term at the note rate
    prepaid       = (C - scheduled) x SMM, SMM from the S-curve (prepay.py, mortgage rate) x behavior.prepay_mult
    C -> D  roll_cd x u(t)     D -> C  cure_dc     D -> S  roll_ds x u(t)
    S -> C  cure_sc (cures and modifications)       S -> liquidation  liq_s
    loss          = liquidated UPB x severity,  severity = max(min_severity, 1 - (1 - liquidation_cost) / LTV(t))
    LTV(t)        = LTV at the start / house price index(t)          (index from the baseline house price path)
    u(t)          = (unemployment(t) / unemployment at the start) ^ unemployment_elasticity   (1 on a flat baseline)
    cash          = scheduled + prepaid + recoveries, earning the reinvestment rate (1M SOFR) from the next month
Interest income and expected credit losses are reported as separate lines. Effective yield = interest income /
average loan UPB, annualised; loss-adjusted yield subtracts the losses.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .engine import _level_sched
from .prepay import SCurvePrepay, cpr_to_smm

STATES = ("current", "dq", "sdq")
DEFAULT_BEHAVIOR = {"roll_cd": 0.008, "cure_dc": 0.50, "roll_ds": 0.07, "cure_sc": 0.135, "liq_s": 0.025, "prepay_mult": 1.0}


class LoanFileError(ValueError):
    pass


def load_cohorts(path: str, behaviors: dict) -> pd.DataFrame:
    df = pd.read_csv(path)
    need = {"id", "behavior", "status", "upb", "note_rate", "wam", "ltv"}
    if need - set(df.columns):
        raise LoanFileError(f"whole-loan file {path} lacks columns: {', '.join(sorted(need - set(df.columns)))}")
    defaults = {"servicing_bp": 25.0, "deferred_share": 0.0, "product": "30Y", "wala": 60, "fico": np.nan, "avg_loan": 250000}
    for c, v in defaults.items():
        df[c] = df[c].fillna(v) if c in df.columns else v
    errs = []
    if sorted(set(df["status"]) - set(STATES)):
        errs.append(f"status must be one of {', '.join(STATES)}")
    if sorted(set(df["behavior"]) - set(behaviors)):
        errs.append(f"behavior {sorted(set(df['behavior']) - set(behaviors))} is not defined in whole_loans.behavior")
    if (df["upb"] <= 0).any() or (df["ltv"] <= 0).any() or (df["wam"] <= 0).any():
        errs.append("upb, ltv and wam must be positive")
    if errs:
        raise LoanFileError(f"whole-loan file {path}: " + "; ".join(errs))
    return df.reset_index(drop=True)


def macro_path(spec, H: int, start: float | None = None) -> np.ndarray:
    """Annual values (year 1..Y) -> monthly path for months 0..H (held after the last year); a number = flat."""
    if np.isscalar(spec):
        return np.full(H + 1, float(spec))
    a = np.asarray(spec, float)
    return np.concatenate([[a[0] if start is None else start], a[np.minimum(np.arange(H) // 12, len(a) - 1)]])


def project(df: pd.DataFrame, cube: np.ndarray, rates, prepay: SCurvePrepay, inc: dict, H: int, wcfg: dict) -> dict:
    """Cohorts x paths, months 1..H. Returns (P, H, N) arrays."""
    P, N = cube.shape[0], len(df)
    beh = {k: {**DEFAULT_BEHAVIOR, **(v or {})} for k, v in (wcfg.get("behavior") or {}).items()}
    par = lambda k: np.array([beh[b][k] for b in df["behavior"]], float)[None, :]   # noqa: E731
    roll_cd, cure_dc, roll_ds, cure_sc, liq_s, pmult = (par(k) for k in ("roll_cd", "cure_dc", "roll_ds", "cure_sc", "liq_s", "prepay_mult"))
    sev = wcfg.get("severity") or {}
    min_sev, cost = float(sev.get("min_severity", 0.05)), float(sev.get("liquidation_cost", 0.25))
    mac = wcfg.get("macro") or {}
    hpi_g = macro_path(mac.get("hpi_growth_pct", 3.0), H)                # % a year
    hpi = np.concatenate([[1.0], np.cumprod((1 + hpi_g[1:] / 100.0) ** (1 / 12))])   # index, month 0 = 1
    ur = macro_path(mac.get("unemployment_pct", 4.3), H)
    u = (ur / ur[0]) ** float(mac.get("unemployment_elasticity", 1.0))
    note = df["note_rate"].to_numpy(float)
    net = (note - df["servicing_bp"].to_numpy(float) / 100.0) * (1 - df["deferred_share"].to_numpy(float))
    ltv0 = df["ltv"].to_numpy(float) / 100.0
    st = prepay.static(df["product"].to_numpy(), np.array(["Generic"] * N), df["avg_loan"].to_numpy(float))
    upb = df["upb"].to_numpy(float) * 1e6
    C = np.tile(np.where(df["status"] == "current", upb, 0.0), (P, 1))
    Dq = np.tile(np.where(df["status"] == "dq", upb, 0.0), (P, 1))
    S = np.tile(np.where(df["status"] == "sdq", upb, 0.0), (P, 1))
    wam = np.tile(df["wam"].to_numpy(float), (P, 1))
    age = np.tile(df["wala"].to_numpy(float), (P, 1))
    burn = np.zeros((P, N))
    cash = np.zeros(P)
    imtg, irr = rates.col("MTG_30Y"), rates.col(inc.get("reinvest_rate_series", "SOFR_1M"))
    spread = float(inc.get("reinvest_spread_bp", 0.0)) / 100.0
    obs = rates.mtg_observed
    moy = [(rates.as_of.month - 1 + m) % 12 + 1 for m in range(1, H + 1)]
    keys = ("interest", "sched", "prepaid", "liquidated", "loss", "bal_c", "bal_d", "bal_s", "prepay_base")
    out = {k: np.zeros((P, H, N)) for k in keys}
    out["cash_income"], out["cash_bal"] = np.zeros((P, H)), np.zeros((P, H))
    for m in range(1, H + 1):
        t = m - 1
        mtg = lambda k: obs[k + 2] if k <= 0 else cube[:, k, imtg][:, None]   # noqa: E731
        out["bal_c"][:, t], out["bal_d"][:, t], out["bal_s"][:, t] = C, Dq, S
        out["interest"][:, t] = (C + Dq) * net / 1200.0
        out["cash_income"][:, t] = cash * (cube[:, m - 1, irr] + spread) / 1200.0
        out["cash_bal"][:, t] = cash
        sched = np.where(wam > 0, _level_sched(C, note[None, :], wam), 0.0)
        base = C - sched
        smm = cpr_to_smm(prepay.cpr(st, note[None, :], age, burn, mtg(m - 1), mtg(m - 2), moy[t])) * pmult
        pre = base * np.clip(smm, 0, 1)
        c_rem = base - pre
        cd, dc, ds = c_rem * roll_cd * u[m], Dq * cure_dc, Dq * roll_ds * u[m]
        sc, sl = S * cure_sc, S * liq_s
        sev_t = np.maximum(min_sev, 1.0 - (1.0 - cost) / (ltv0 / hpi[m]))[None, :]
        loss = sl * sev_t
        C, Dq, S = c_rem - cd + dc + sc, Dq - dc - ds + cd, S - sc - sl + ds
        out["sched"][:, t], out["prepaid"][:, t], out["liquidated"][:, t], out["loss"][:, t] = sched, pre, sl, loss
        out["prepay_base"][:, t] = base
        cash = cash + (sched + pre + sl - loss).sum(axis=1)
        burn = burn + np.where(note[None, :] - mtg(m - 1) > 0.5, 1 / 12, 0.0)
        age, wam = age + 1, np.maximum(wam - 1, 0)
    out["hpi"], out["unemployment"] = hpi, ur
    return out


def _ann(x: np.ndarray, H: int) -> np.ndarray:
    """(..., H) -> (..., Y) sums by year."""
    Y = int(np.ceil(H / 12))
    return np.stack([x[..., 12 * y:min(12 * (y + 1), H)].sum(axis=-1) for y in range(Y)], axis=-1)


def run(wcfg: dict, rates, prepay: SCurvePrepay, inc: dict, scen_cube: np.ndarray, catalog: list, H: int, log=print) -> dict | None:
    if not wcfg or not wcfg.get("enabled", True) or not wcfg.get("file"):
        return None
    beh = {k: {**DEFAULT_BEHAVIOR, **(v or {})} for k, v in (wcfg.get("behavior") or {}).items()}
    df = load_cohorts(wcfg["file"], beh)
    log(f"Whole loans {wcfg['file']}: {len(df)} cohorts, ${df['upb'].sum() / 1e3:,.1f}bn (illustrative) ...")
    o = project(df, scen_cube, rates, prepay, inc, H, wcfg)
    ids = [c["id"] for c in catalog]
    r0 = lambda x: np.round(np.asarray(x, float), 0).tolist()   # noqa: E731
    r3 = lambda x: np.round(np.asarray(x, float), 3).tolist()   # noqa: E731
    loans = o["bal_c"] + o["bal_d"] + o["bal_s"]                 # (S, H, N) start-of-month loan UPB
    from .engine import cpr_from
    sc = {}
    for i, sid in enumerate(ids):
        tot = lambda k: o[k][i].sum(axis=1)                     # noqa: E731  (H,)
        lb = loans[i].sum(axis=1)
        sc[sid] = {
            "interest": r0(_ann(tot("interest"), H)), "cash_income": r0(_ann(o["cash_income"][i], H)), "loss": r0(_ann(tot("loss"), H)),
            "avg_loans": r0(_ann(lb, H) / np.array([min(12, H - 12 * y) for y in range(int(np.ceil(H / 12)))])),
            "sched": r0(_ann(tot("sched"), H)), "prepaid": r0(_ann(tot("prepaid"), H)), "liquidated": r0(_ann(tot("liquidated"), H)),
            "cpr": r3(cpr_from(_ann(tot("prepaid"), H), _ann(tot("prepay_base"), H))),
            "m_interest": r0(tot("interest")), "m_cash_income": r0(o["cash_income"][i]), "m_loss": r0(tot("loss")),
            "m_bal": {"current": r0(tot("bal_c")), "dq": r0(tot("bal_d")), "sdq": r0(tot("bal_s")), "cash": r0(o["cash_bal"][i])},
            "cohort_interest": [r0(_ann(o["interest"][i][:, n], H)) for n in range(len(df))],
            "cohort_loss": [r0(_ann(o["loss"][i][:, n], H)) for n in range(len(df))],
        }
    d = df
    w = d["upb"].to_numpy(float)
    return {
        "name": wcfg.get("name", ""), "source": wcfg.get("source", ""), "years": int(np.ceil(H / 12)), "months": H,
        "upb": float(w.sum() * 1e6), "note_rate": float((d["note_rate"] * w).sum() / w.sum()),
        "accruing_share": float(w[d["status"] != "sdq"].sum() / w.sum()),
        "cohorts": [{"id": r.id, "behavior": r.behavior, "status": r.status, "upb": float(r.upb * 1e6), "note_rate": float(r.note_rate),
                     "servicing_bp": float(r.servicing_bp), "deferred_share": float(r.deferred_share), "product": r.product,
                     "wala": float(r.wala), "wam": float(r.wam), "ltv": float(r.ltv), "fico": None if pd.isna(r.fico) else float(r.fico)}
                    for r in d.itertuples()],
        "behavior": beh, "severity": {"min_severity": float((wcfg.get("severity") or {}).get("min_severity", 0.05)),
                                      "liquidation_cost": float((wcfg.get("severity") or {}).get("liquidation_cost", 0.25))},
        "macro": {"hpi_growth_pct": (wcfg.get("macro") or {}).get("hpi_growth_pct", 3.0), "unemployment_pct": (wcfg.get("macro") or {}).get("unemployment_pct", 4.3),
                  "unemployment_elasticity": float((wcfg.get("macro") or {}).get("unemployment_elasticity", 1.0)),
                  "hpi_index": r3(o["hpi"]), "unemployment": r3(o["unemployment"])},
        "reported": wcfg.get("reported") or {}, "scenarios": sc, "_o": o, "_ids": ids, "_df": df,
    }


def frames(res: dict) -> dict:
    H, ids, o = res["months"], res["_ids"], res["_o"]
    rows = [{"scenario_id": sid, "year": y + 1, "interest_income": v["interest"][y], "cash_income_sofr": v["cash_income"][y],
             "expected_credit_losses": v["loss"][y], "income_after_losses": v["interest"][y] + v["cash_income"][y] - v["loss"][y],
             "average_loan_upb": v["avg_loans"][y], "scheduled_principal": v["sched"][y], "prepaid": v["prepaid"][y],
             "liquidated": v["liquidated"][y], "cpr": v["cpr"][y]} for sid, v in res["scenarios"].items() for y in range(res["years"])]
    mon = pd.concat([pd.DataFrame({"scenario_id": sid, "month": np.arange(1, H + 1), "interest_income": o["interest"][i].sum(axis=1),
                                   "cash_income_sofr": o["cash_income"][i], "expected_credit_losses": o["loss"][i].sum(axis=1),
                                   "current": o["bal_c"][i].sum(axis=1), "dq_30_89": o["bal_d"][i].sum(axis=1),
                                   "sdq_90_plus": o["bal_s"][i].sum(axis=1), "cash": o["cash_bal"][i]}) for i, sid in enumerate(ids)])
    return {"wholeloan_scenarios.csv": pd.DataFrame(rows), "wholeloan_monthly.csv": mon}


def public(res: dict | None) -> dict | None:
    return None if res is None else {k: v for k, v in res.items() if not k.startswith("_")}
