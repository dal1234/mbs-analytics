#!/usr/bin/env python
"""MOCK-UP of the run history behind the dashboard's "EaR over time" chart.

    python tools/mock_history.py [--months 11] [--config config/model.yaml]

For each of the previous ``--months`` month-ends before the portfolio date it
  1. builds that month's baseline and paths with run_rates.py (real rates for that date, the CURRENT stored
     calibration, into rates_history/<date>/), and
  2. reruns TODAY's CUSIPs as if it were that date: ages rolled back (WALA - k, WAM + k), same balances,
and appends the result to the history file with source = "mock". V5/V8 are not run for mock dates.

This only stands in for real history. In production, run run_model.py once per month-end with that month's CUSIPs:
each run appends its own rows (source = "run") and replaces any mock row for the same date. See README section 7.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mbs_lite.config import load_config            # noqa: E402
from mbs_lite.portfolio import PRODUCT_TERM         # noqa: E402
from mbs_lite.run import run                        # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "config" / "model.yaml"))
    ap.add_argument("--months", type=int, default=11, help="month-ends to mock before the portfolio date")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    src = pd.read_csv(cfg["portfolio"]["file"], dtype={"cusip": str, "pool_number": str})
    as_of = pd.Timestamp(src["as_of_date"].iloc[0])
    work = ROOT / "history" / "mock_inputs"
    work.mkdir(parents=True, exist_ok=True)
    for k in range(a.months, 0, -1):
        d = (as_of - pd.DateOffset(months=k)) + pd.offsets.MonthEnd(0)
        ds = str(d.date())
        rdir = ROOT / "rates_history" / ds
        print(f"[{ds}] rates ...", flush=True)
        cmd = [sys.executable, str(ROOT / "run_rates.py"), "--as-of", ds, "--output", str(rdir)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if not (rdir / "mc_paths.csv").exists():
            print(r.stdout[-2000:], r.stderr[-2000:])
            return 1
        p = src.copy()
        term = p["product"].str.upper().map(PRODUCT_TERM).fillna(360)
        p["wala"] = (p["wala"] - k).clip(lower=0)
        p["wam"] = (p["wam"] + k).clip(upper=term)
        p["as_of_date"] = ds
        if "vintage" in p:
            p = p.drop(columns="vintage")             # re-derived from the rolled-back age
        pf = work / f"portfolio_{ds}.csv"
        p.to_csv(pf, index=False)
        c = load_config(a.config)
        c["portfolio"]["file"], c["rates"]["dir"] = str(pf), str(rdir)
        res = run(c, log=lambda *x: None, write_outputs=False, write_dashboard=False, source="mock", income_validation=False)
        d1 = res["ear"]["cumulative"][0]
        print(f"[{ds}] baseline 12m income ${d1['baseline'] / 1e6:,.0f}m, EaR95 ${d1['conf']['95']['shortfall'] / 1e6:,.1f}m "
              f"({100 * d1['conf']['95']['shortfall_pct']:.2f}%)", flush=True)
    print("Done. Now run `python run_model.py run` to rebuild the dashboard with the history.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
