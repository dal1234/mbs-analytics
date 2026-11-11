"""Run history: one row per as-of date, measure (cumulative | annual) and year, appended by every run and read back
for the "over time" chart. A run replaces earlier rows for the same as-of date (including mock rows).

The file is a plain CSV (history/ear_history.csv by default). It also records the calibration id, path counts and
baseline type of every run (whitepaper section 13.3), so a change of calibration is visible over time.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

PC = ["p1", "p5", "p10", "p25", "p50", "p75", "p90", "p95", "p99"]
COLS = ["as_of", "run_at", "source", "portfolio", "positions", "upb", "measure", "years", "n_paths", "tail_paths",
        "calibration_id", "baseline_type", "baseline", "mean", *PC, "income95", "shortfall95", "shortfall95_pct",
        "ci_low95", "ci_high95", "shortfall99", "shortfall99_pct", "es95", "es95_pct", "mean_minus_baseline_pct",
        "cpr_mean", "cpr_baseline", "sofr_1m", "mtg_30y"]


def rows(meta: dict, official: dict, tail: dict) -> pd.DataFrame:
    out = []
    for measure, rs in official.items():
        for r, t in zip(rs, tail[measure]):
            c95 = r["conf"]["95"]
            out.append({**meta, "measure": measure, "years": r["years"], "baseline": r["baseline"], "mean": r["mean"],
                        **{f"p{k}": v for k, v in r["pct"].items()}, "income95": c95["income"],
                        "shortfall95": c95["shortfall"], "shortfall95_pct": c95["shortfall_pct"],
                        "ci_low95": c95["ci_low"], "ci_high95": c95["ci_high"],
                        "shortfall99": t["conf"]["99"]["shortfall"], "shortfall99_pct": t["conf"]["99"]["shortfall_pct"],
                        "es95": t["es"]["95"]["shortfall"], "es95_pct": t["es"]["95"]["shortfall_pct"],
                        "mean_minus_baseline_pct": r["mean_minus_baseline_pct"], "cpr_mean": r["cpr"]["mean"],
                        "cpr_baseline": r["cpr"]["baseline"]})
    return pd.DataFrame(out).reindex(columns=COLS)


def append(path: str, new: pd.DataFrame) -> pd.DataFrame:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    old = pd.read_csv(p) if p.exists() else pd.DataFrame(columns=COLS)
    if len(old) and set(COLS) - set(old.columns):     # file from an earlier version of the model: start afresh
        old = pd.DataFrame(columns=COLS)
    keep = old[~old["as_of"].astype(str).isin(new["as_of"].astype(str).unique())]
    allr = pd.concat([keep, new], ignore_index=True) if len(keep) else new.copy()
    allr = allr.sort_values(["as_of", "measure", "years"]).reset_index(drop=True)
    allr.to_csv(p, index=False, float_format="%.6g")
    return allr


def window(path: str, as_of, months: int = 12) -> pd.DataFrame:
    """Rows for the ``months`` most recent as-of months up to and including ``as_of``."""
    p = Path(path)
    if not p.exists():
        return pd.DataFrame(columns=COLS)
    h = pd.read_csv(p)
    if set(COLS) - set(h.columns):
        return pd.DataFrame(columns=COLS)
    h["_d"] = pd.to_datetime(h["as_of"]).dt.to_period("M")
    a = pd.Timestamp(as_of).to_period("M")
    h = h[(h["_d"] <= a) & (h["_d"] > a - months)]                  # calendar months, not days
    return h.drop(columns="_d").sort_values(["as_of", "measure", "years"])
