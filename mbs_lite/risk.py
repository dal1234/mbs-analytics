"""Risk measures (whitepaper section 8): income distribution and Earnings at Risk against the baseline, by horizon,
cumulative and annual; representative-outcome cash flows; tail attribution by cohort; the rate fan.

    income at confidence c = (1 - c) quantile of simulated income       (5th percentile at 95%)
    shortfall ($)          = baseline income - income at confidence
    shortfall (%)          = shortfall ($) / baseline income
    expected shortfall     = baseline income - mean income of the worst (1 - c) of paths
    mean - baseline        = convexity of income (reported, not an error)
Each percentile carries a 95% bootstrap interval in which antithetic pairs are resampled together.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .engine import cpr_from

PCTS = [1, 5, 10, 25, 50, 75, 90, 95, 99]
FAN_SERIES = ["SOFR_1M", "TSY_2Y", "TSY_10Y", "MTG_30Y"]
MEASURES = ("cumulative", "annual")


def window(measure: str, y: int, H: int) -> slice:
    """Months (0-based) of year ``y``: cumulative = months 1..12y, annual = months 12(y-1)+1..12y."""
    return slice(0 if measure == "cumulative" else 12 * (y - 1), min(12 * y, H))


def sums(monthly: np.ndarray, measure: str, y: int, H: int) -> np.ndarray:
    return monthly[..., window(measure, y, H)].sum(axis=-1)


def _boot_idx(n: int, antithetic: bool, n_boot: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    if antithetic and n % 2 == 0:
        pairs = rng.integers(0, n // 2, size=(n_boot, n // 2))
        return np.concatenate([pairs, pairs + n // 2], axis=1)
    return rng.integers(0, n, size=(n_boot, n))


def ear(proj, base_proj, confs=(0.95,), es_confs=(0.95,), n_boot: int = 2000, antithetic: bool = True,
        bins: int = 30, seed: int = 7, detail: bool = True) -> dict:
    """EaR by horizon for both measures from a projection over many paths and one over the baseline path."""
    a, b, H, Y = proj.agg, base_proj.agg, proj.months, proj.n_years
    P = a["income"].shape[0]
    idx = _boot_idx(P, antithetic, n_boot, seed)
    out = {}
    for measure in MEASURES:
        rows = []
        for y in range(1, Y + 1):
            v = sums(a["income"], measure, y, H)
            base = float(sums(b["income"], measure, y, H)[0])
            qs = [1 - c for c in confs]
            pq = np.quantile(v, qs)
            boot = np.quantile(v[idx], qs, axis=1)                          # (len(qs), n_boot)
            r = {"years": y, "measure": measure, "baseline": base, "mean": float(v.mean()),
                 "mean_minus_baseline_pct": float(v.mean() / base - 1.0) if base else None, "conf": {}, "es": {}}
            for c, p, bq in zip(confs, pq, boot):
                lo, hi = np.quantile(bq, [0.025, 0.975])
                r["conf"][f"{round(c * 100):d}"] = {
                    "income": float(p), "shortfall": base - float(p), "shortfall_pct": (base - float(p)) / base if base else None,
                    "ci_low": float(lo), "ci_high": float(hi),
                    "shortfall_ci": [base - float(hi), base - float(lo)]}
            for c in es_confs:
                k = max(1, int(np.ceil((1 - c) * P)))
                tail = np.sort(v)[:k].mean()
                r["es"][f"{round(c * 100):d}"] = {"income": float(tail), "shortfall": base - float(tail),
                                                  "shortfall_pct": (base - float(tail)) / base if base else None, "paths": k}
            if detail:
                q = np.percentile(v, PCTS)
                hist, edges = np.histogram(v, bins=bins)
                sd = v.std(ddof=1)
                pre, ex = sums(a["prepaid"], measure, y, H), sums(a["prepay_base"], measure, y, H)
                cpr = cpr_from(pre, ex)
                bcpr = float(cpr_from(sums(b["prepaid"], measure, y, H), sums(b["prepay_base"], measure, y, H))[0])
                r.update({"std": float(sd), "skew": float(((v - v.mean()) ** 3).mean() / sd ** 3) if sd > 0 else 0.0,
                          "pct": {str(p): float(x) for p, x in zip(PCTS, q)}, "hist": hist.tolist(), "edges": edges.tolist(),
                          "interest_mean": float(sums(a["interest"], measure, y, H).mean()),
                          "reinv_mean": float(sums(a["reinv_income"], measure, y, H).mean()),
                          "interest_base": float(sums(b["interest"], measure, y, H)[0]),
                          "reinv_base": float(sums(b["reinv_income"], measure, y, H)[0]),
                          "cpr": {"mean": float(cpr.mean()), "p5": float(np.percentile(cpr, 5)),
                                  "p95": float(np.percentile(cpr, 95)), "baseline": bcpr}})
            rows.append(r)
        out[measure] = rows
    return out


def path_table(proj) -> pd.DataFrame:
    a, H, Y = proj.agg, proj.months, proj.n_years
    t = pd.DataFrame({"path": np.arange(1, a["income"].shape[0] + 1)})
    for m in MEASURES:
        for y in range(1, Y + 1):
            t[f"income_{m}_y{y}"] = sums(a["income"], m, y, H)
            t[f"interest_{m}_y{y}"] = sums(a["interest"], m, y, H)
            t[f"reinv_income_{m}_y{y}"] = sums(a["reinv_income"], m, y, H)
    return t


def band(values: np.ndarray, q: float, band_pct: float, min_paths: int = 5) -> np.ndarray:
    """Indices of the paths ranked closest to percentile ``q`` of ``values``: about ``band_pct``% of all paths."""
    P = len(values)
    order = np.argsort(values, kind="stable")
    k = int(min(P, max(min_paths, round(band_pct / 100 * P))))
    centre = q / 100 * (P - 1)
    lo = int(np.clip(round(centre - (k - 1) / 2), 0, P - k))
    return order[lo: lo + k]


def _series(agg: dict, idx, sl: slice, cube: np.ndarray, rates) -> dict:
    avg = lambda arr: np.round(arr[idx, sl].mean(axis=0), 2).tolist()  # noqa: E731
    pre, base = agg["prepaid"][idx, sl].mean(axis=0), agg["prepay_base"][idx, sl].mean(axis=0)
    H = agg["income"].shape[1]
    months = np.arange(H)[sl]
    return {"interest": avg(agg["interest"]), "reinv": avg(agg["reinv_income"]), "income": avg(agg["income"]),
            "prepaid": avg(agg["prepaid"]), "sched": avg(agg["sched"]), "end_upb": avg(agg["end_upb"]),
            "reinv_bal": avg(agg["reinv_end"]), "cpr": np.round(cpr_from(pre, base), 3).tolist(),
            "sofr": np.round(cube[idx][:, months, rates.col("SOFR_1M")].mean(axis=0), 4).tolist(),
            "mtg": np.round(cube[idx][:, months + 1, rates.col("MTG_30Y")].mean(axis=0), 4).tolist()}


def outcomes(proj, base_proj, rates, quantiles: list, band_pct: float) -> dict:
    """Representative-outcome monthly cash flows for each measure and year, with the baseline path for comparison.

    Paths are ranked by income over the horizon (cumulative, or the single year for the annual measure); the outcome
    "P10" is the average of the paths ranked around the 10th percentile (``band_pct``% of all paths). Averaging whole
    paths keeps coupon + reinvestment = income and the balance identity month by month.
    """
    a, H, Y = proj.agg, proj.months, proj.n_years
    out = {"quantiles": quantiles, "band_pct": band_pct, "paths_per_band": int(len(band(a["income"][:, 0], 50, band_pct)))}
    for measure in MEASURES:
        res = []
        for y in range(1, Y + 1):
            sl = window(measure, y, H)
            v = sums(a["income"], measure, y, H)
            rows = {}
            for q in quantiles:
                idx = band(v, q, band_pct)
                rows[str(q)] = {**_series(a, idx, sl, rates.mc, rates), "total": float(v[idx].mean()),
                                "pctl": float(np.percentile(v, q))}
            bl = {**_series(base_proj.agg, [0], sl, rates.baseline_cube(), rates),
                  "total": float(sums(base_proj.agg["income"], measure, y, H)[0])}
            res.append({"years": y, "start": sl.start, "months": sl.stop - sl.start, "rows": rows, "baseline": bl})
        out[measure] = res
    return out


def tail_attribution(df: pd.DataFrame, codes: np.ndarray, labels, proj, base_proj, tail_pct: float) -> dict:
    """For each measure and year: each cohort's average income in the worst ``tail_pct``% of paths minus its income on
    the baseline path. The cohort differences add up to the tail's average shortfall against the baseline."""
    g, gb, H, Y = proj.group, base_proj.group, proj.months, proj.n_years
    G = len(labels)
    w = df["current_upb"].to_numpy(float)
    upb = np.bincount(codes, weights=w, minlength=G)
    wavg = lambda col: np.divide(np.bincount(codes, weights=df[col].to_numpy(float) * w, minlength=G), upb,  # noqa: E731
                                 out=np.full(G, np.nan), where=upb > 0)
    cpn, wac, n = wavg("coupon"), wavg("wac"), np.bincount(codes, minlength=G)
    out = {}
    for measure in MEASURES:
        res = []
        for y in range(1, Y + 1):
            ys = slice(0, y) if measure == "cumulative" else slice(y - 1, y)
            tot = sums(proj.agg["income"], measure, y, H)
            k = max(1, int(np.ceil(tail_pct / 100 * len(tot))))
            tail = np.argsort(tot)[:k]
            inc = g["income"][:, :, ys].sum(axis=2)
            binc = gb["income"][0, :, ys].sum(axis=1)
            pre, ex = g["prepaid"][:, :, ys].sum(axis=2), g["prepay_base"][:, :, ys].sum(axis=2)
            bpre, bex = gb["prepaid"][0, :, ys].sum(axis=1), gb["prepay_base"][0, :, ys].sum(axis=1)
            tmean = inc[tail].mean(axis=0)
            cpr_b, cpr_t = cpr_from(bpre, bex), cpr_from(pre[tail].sum(axis=0), ex[tail].sum(axis=0))
            short = float(binc.sum() - tot[tail].mean())
            yrs = y if measure == "cumulative" else 1
            rows = [{"cohort": labels[i], "positions": int(n[i]), "upb": float(upb[i]), "coupon": float(cpn[i]),
                     "wac": float(wac[i]), "cpr_base": float(cpr_b[i]), "cpr_tail": float(cpr_t[i]),
                     "income_base": float(binc[i]), "income_tail": float(tmean[i]), "delta": float(tmean[i] - binc[i]),
                     "share": float((binc[i] - tmean[i]) / short) if short > 0 else None,
                     "delta_bp_of_upb": float(1e4 * (tmean[i] - binc[i]) / upb[i] / yrs) if upb[i] > 0 else None}
                    for i in range(G)]
            rows.sort(key=lambda r: r["delta"])
            res.append({"years": y, "tail_paths": int(k), "shortfall": short, "rows": rows})
        out[measure] = res
    return out


def fan(rates, months: int, hist_months: int = 24) -> dict:
    H = months
    out = {"months": list(range(H + 1)), "dates": [str(d.date()) for d in rates.dates[: H + 1]], "series": {}}
    h = rates.history[rates.history.index < rates.as_of.replace(day=1)].tail(hist_months)
    out["hist_dates"] = [str(d.date()) for d in h.index]
    for name in FAN_SERIES:
        j = rates.col(name)
        x = rates.mc[:, : H + 1, j]
        e = {f"p{p:02d}": np.percentile(x, p, axis=0).round(4).tolist() for p in (5, 25, 50, 75, 95)}
        e["mean"] = x.mean(axis=0).round(4).tolist()
        e["baseline"] = rates.baseline[: H + 1, j].round(4).tolist()
        e["hist"] = h[name].round(4).tolist() if name in h else []
        out["series"][name] = e
    return out


def cohort_codes(df: pd.DataFrame, keys: list):
    def fmt(v):
        if isinstance(v, (float, np.floating)) and float(v).is_integer():
            return str(int(v)) if v > 100 else f"{v:.1f}"
        if isinstance(v, (float, np.floating)):
            return f"{v:g}"
        return str(v)
    lab = df.apply(lambda r: " ".join(fmt(r[k]) for k in keys), axis=1) if keys else pd.Series(["All"] * len(df))
    codes, uniq = pd.factorize(lab)
    return codes.astype(int), list(uniq)
