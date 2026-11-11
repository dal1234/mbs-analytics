"""Historical forward baselines: what each month's Treasury curve implied for the rates h months later.

Calibration target (model 1.2.0, whitepaper 6.1): the simulated fan is a deviation from a forward baseline, so its
width is fitted to the historical dispersion of DEVIATIONS FROM THE FORWARD, not of raw changes:

    deviation_k(t, h) = r_k(t + h) - r_k(t) - [ f_k(t, h) - f_k(t, 0) ]

where f_k(t, h) is the rate implied by month t's Treasury curve for month t + h, built exactly as the forward baseline
is built for a run (curve.py): the 1-month forward for the short rate, the 10-year par forward for the 10-year yield
and the mortgage rate (the baseline mortgage rate is the forward 10-year plus a constant spread).

Curves are month-end Treasury CMT yields from the FRED cache. Before 1982 there are no bill yields, so the short end of
those curves is extrapolated from the 1-year point (this affects only the high_vol calibration window).
"""
from __future__ import annotations

import pandas as pd

from .curve import TENOR_YEARS, bootstrap, par_yield
from .data import CURVE_SERIES, read_series

FWD_HORIZONS = (3, 12, 24, 36, 48, 60)
TENOR = {"short": 1 / 12, "y10": 10.0, "y1": 1.0, "y2": 2.0, "y5": 5.0, "y30": 30.0}
FWD_KEY = {"short": "short", "y10": "y10", "mort": "y10", "y1": "y1", "y2": "y2", "y5": "y5", "y30": "y30"}


def monthly_curves(data_dir: str, as_of, refresh: bool = False) -> pd.DataFrame:
    """Month-end Treasury par yields (percent), one row per month through the last complete month at the as-of date."""
    as_of = pd.Timestamp(as_of)
    cur = pd.DataFrame({t: read_series(s, data_dir, refresh).loc[:as_of].resample("ME").last() for t, s in CURVE_SERIES.items()})
    last = as_of if as_of == as_of + pd.offsets.MonthEnd(0) else as_of - pd.offsets.MonthEnd(1)
    cur = cur[cur.index <= last]
    cur.index = cur.index.to_period("M")
    return cur


def forward_changes(curves: pd.DataFrame, horizons=FWD_HORIZONS) -> pd.DataFrame:
    """Forward-implied change f(t, h) - f(t, 0), decimals; columns (rate, h) for every rate in TENOR."""
    rows = {}
    for t, row in curves.iterrows():
        par = {k: float(v) for k, v in row.dropna().items() if k != "20Y"}
        if not {"1Y", "5Y", "10Y"} <= set(par):
            continue
        aux = {"20Y": float(row["20Y"])} if pd.notna(row.get("20Y")) else None
        top = max(TENOR_YEARS[k] for k in [*par, *(aux or {})])
        try:   # 10-year forwards 5 years out need maturities to 15 years; beyond the last quoted tenor is extrapolated
            dc = bootstrap(par, aux, max_maturity=max(15.0, min(30.0, top)))
        except ValueError:
            continue
        r = {}
        for k, T in TENOR.items():
            f0 = par_yield(dc, 0.0, T)
            for h in horizons:
                r[(k, h)] = (par_yield(dc, h / 12.0, T) - f0) / 100.0
        rows[t] = r
    out = pd.DataFrame(rows).T
    out.columns = pd.MultiIndex.from_tuples(out.columns)
    return out.sort_index()


def deviations(hist: pd.DataFrame, fwd: pd.DataFrame | None, col: str, h: int) -> pd.Series:
    """Overlapping h-month deviations from the forward for one history column (decimals), indexed by start month.

    The mortgage rate uses the 10-year forward (the baseline mortgage rate is the forward 10-year plus a constant
    spread). fwd None = raw changes (a flat forward: synthetic histories and tests). NaN where either side is missing."""
    y = hist[col]
    dev = y.shift(-h) - y
    if fwd is not None:
        key = (FWD_KEY[col], h)
        if key not in fwd.columns:
            raise ValueError(f"no forward change for {key[0]} at {h} months")
        dev = dev - fwd[key].reindex(y.index)
    return dev
