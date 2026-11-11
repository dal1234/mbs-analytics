#!/usr/bin/env python
"""Market-implied volatility inputs for config/rates.yaml (volatility.market) from what another model reports.

Two ways in (README, "Two approaches"):

  1. The other model's percentiles of a rate at some horizons (months), e.g. 1M SOFR as of July 2026:
         python tools/vols_from_percentiles.py --pct 12:3.0:4.0:5.0 --pct 60:0.75:3.9:7.0
     Each --pct is horizon_months:P5:P50:P95 in percent (P50 may be left empty: 12:3.0::5.0). The fan st.dev. at that
     horizon is (P95 - P5) / 3.29; with P50 the two halves are also reported, since a large difference means the other
     model is skewed (lognormal, or a floor binding) and a normal width is an approximation.

  2. Normal (bp) implied vols by option expiry, e.g. SOFR caplet or swaption vols from a market data screen:
         python tools/vols_from_percentiles.py --implied 1:61 --implied 5:85
     Each --implied is expiry_years:vol_bp_per_year; the st.dev. at expiry T is vol x sqrt(T).

Either way the cumulative variance is interpolated linearly in time between the points (zero at time zero), and the
volatility for each year k is sqrt(variance(k) - variance(k - 1)): the forward vol that config expects, one value
per year. Paste the printed list into volatility.market.sofr_bp (or y10_bp for the 10-year).
"""
from __future__ import annotations

import argparse
import sys

import numpy as np

Z95 = 1.6448536269514722


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pct", action="append", default=[], help="horizon_months:P5:P50:P95 (percent; P50 optional)")
    ap.add_argument("--implied", action="append", default=[], help="expiry_years:normal_vol_bp_per_year")
    ap.add_argument("--years", type=int, default=5, help="number of yearly values to print (default 5)")
    a = ap.parse_args(argv)
    if bool(a.pct) == bool(a.implied):
        ap.error("give either --pct or --implied (one or more of the same kind)")
    pts = {}
    if a.pct:
        for x in a.pct:
            h, p5, p50, p95 = (x.split(":") + ["", "", ""])[:4]
            h, p5, p95 = float(h) / 12.0, float(p5), float(p95)
            sd = (p95 - p5) / (2 * Z95)
            line = f"  {h * 12:4.0f} months: P5 {p5:.2f}%, P95 {p95:.2f}% -> st.dev. {sd * 100:.0f} bp"
            if p50.strip():
                lo, hi = (float(p50) - p5) / Z95, (p95 - float(p50)) / Z95
                line += f" (lower half {lo * 100:.0f} bp, upper half {hi * 100:.0f} bp"
                line += "; skewed: treat the normal width as an approximation)" if abs(hi - lo) > 0.25 * sd else ")"
            print(line)
            pts[h] = sd ** 2
    else:
        for x in a.implied:
            t, v = (float(y) for y in x.split(":"))
            pts[t] = (v / 100.0) ** 2 * t
            print(f"  expiry {t:g}y: {v:.0f} bp/yr -> st.dev. {v * np.sqrt(t):.0f} bp")
    T = np.array([0.0, *sorted(pts)])
    V = np.array([0.0, *[pts[t] for t in sorted(pts)]])
    if np.any(np.diff(V) < 0):
        print("The st.dev. falls with the horizon: no forward volatility fits. Check the inputs.", file=sys.stderr)
        return 2
    years = np.arange(0, a.years + 1, dtype=float)
    Vy = np.interp(years, T, V, right=np.nan)
    if np.isnan(Vy).any():                                   # beyond the last point: hold the last forward vol
        last = np.sqrt(np.diff(V)[-1] / np.diff(T)[-1])
        for i in np.where(np.isnan(Vy))[0]:
            Vy[i] = Vy[i - 1] + last ** 2
    vols = np.sqrt(np.diff(Vy)) * 100                         # bp per year
    print(f"\nvolatility.market list (bp per year, years 1..{a.years}): [{', '.join(f'{v:.0f}' for v in vols)}]")
    print("fan st.dev. it produces at 1..%d years (bp): [%s]" % (a.years, ", ".join(f"{np.sqrt(v) * 100:.0f}" for v in Vy[1:])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
