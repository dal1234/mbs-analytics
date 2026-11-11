#!/usr/bin/env python
"""Side-by-side comparison of two finished runs, e.g. the official set-up and the vendor emulation.

    python tools/compare_runs.py                                   # outputs/ + rates_output/ vs outputs_vendor/ + rates_output_vendor/
    python tools/compare_runs.py --a outputs --a-rates rates_output --b outputs_vendor --b-rates rates_output_vendor \
                                 [--csv comparison.csv]

Prints, for each run: the set-up (baseline and volatility), cumulative EaR 95% / 99% and baseline income by year, and
the 5th / 50th / 95th percentiles of 1M SOFR, the 10-year and the 30-year mortgage rate at 12 and 60 months.
Both runs should use the same portfolio and as-of date; the tool warns when they do not.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def load(out_dir: Path, rates_dir: Path) -> dict:
    rec = json.loads((rates_dir / "run_record.json").read_text())
    e = pd.read_csv(out_dir / "ear_by_horizon.csv")
    e = e[e["measure"] == "cumulative"].set_index("years")
    m = pd.read_csv(rates_dir / "mc_paths.csv")
    pct = {}
    for s in ("SOFR_1M", "TSY_10Y", "MTG_30Y"):
        a = m.pivot(index="path", columns="month", values=s)
        pct[s] = {h: np.percentile(a[h].to_numpy(), [5, 50, 95]) for h in (12, 60) if h in a.columns}
    vol = rec.get("volatility") or {"type": "historical", "description": "historical"}
    port = pd.read_csv(out_dir / "holdings_projected.csv", usecols=["current_upb"]) if (out_dir / "holdings_projected.csv").exists() else None
    return {"rec": rec, "ear": e, "pct": pct, "vol": vol, "upb": None if port is None else float(port["current_upb"].sum())}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--a", default=str(ROOT / "outputs"))
    ap.add_argument("--a-rates", default=str(ROOT / "rates_output"))
    ap.add_argument("--a-label", default="Official")
    ap.add_argument("--b", default=str(ROOT / "outputs_vendor"))
    ap.add_argument("--b-rates", default=str(ROOT / "rates_output_vendor"))
    ap.add_argument("--b-label", default="Vendor emulation")
    ap.add_argument("--csv", help="also write the comparison table here")
    a = ap.parse_args(argv)
    try:
        A = load(Path(a.a), Path(a.a_rates))
        B = load(Path(a.b), Path(a.b_rates))
    except FileNotFoundError as ex:
        print(f"Missing run output: {ex}. Run both set-ups first (README, 'Two approaches').", file=sys.stderr)
        return 2
    for lab, R in ((a.a_label, A), (a.b_label, B)):
        print(f"{lab}: as of {R['rec']['as_of']} · baseline {R['rec']['baseline']['type']}: {R['rec']['baseline']['description']}")
        print(f"   volatility: {R['vol'].get('description', R['vol']['type'])}")
    if A["rec"]["as_of"] != B["rec"]["as_of"]:
        print(f"WARNING: different as-of dates ({A['rec']['as_of']} vs {B['rec']['as_of']})")
    if A["upb"] and B["upb"] and abs(A["upb"] - B["upb"]) > 1:
        print("WARNING: different portfolios (total UPB differs)")
    rows = []
    for y in sorted(set(A["ear"].index) & set(B["ear"].index)):
        ra, rb = A["ear"].loc[y], B["ear"].loc[y]
        rows.append({"years": y,
                     f"{a.a_label} baseline $m": ra["baseline_income"] / 1e6, f"{a.b_label} baseline $m": rb["baseline_income"] / 1e6,
                     f"{a.a_label} EaR95 $m": ra["shortfall95_usd"] / 1e6, f"{a.b_label} EaR95 $m": rb["shortfall95_usd"] / 1e6,
                     f"{a.a_label} EaR95 %": 100 * ra["shortfall95_pct"], f"{a.b_label} EaR95 %": 100 * rb["shortfall95_pct"],
                     f"{a.a_label} EaR99 $m": ra["shortfall99_usd_5k"] / 1e6, f"{a.b_label} EaR99 $m": rb["shortfall99_usd_5k"] / 1e6,
                     "EaR95 ratio b/a": rb["shortfall95_usd"] / ra["shortfall95_usd"]})
    t = pd.DataFrame(rows).set_index("years")
    print("\nCumulative earnings at risk")
    print(t.round(2).to_string())
    print("\nRate percentiles P5 / P50 / P95 (%)")
    for s in ("SOFR_1M", "TSY_10Y", "MTG_30Y"):
        for h in (12, 60):
            fa, fb = A["pct"][s].get(h), B["pct"][s].get(h)
            if fa is not None and fb is not None:
                print(f"   {s:8s} {h:2d}m   {a.a_label}: {fa[0]:5.2f} / {fa[1]:5.2f} / {fa[2]:5.2f}    {a.b_label}: {fb[0]:5.2f} / {fb[1]:5.2f} / {fb[2]:5.2f}")
    if a.csv:
        t.to_csv(a.csv)
        print(f"\nwrote {a.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
