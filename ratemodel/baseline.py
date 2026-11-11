"""Baseline rate paths: the centre every simulated path is built around (whitepaper sections 3.2 and 12, A1).

    forward  implied forwards from the as-of Treasury curve (default). 1M SOFR = forward 1-month rate + today's
             SOFR-bill basis; mortgage = forward 10-year + today's mortgage-10y spread; both held constant.
    swap     implied forwards from a SOFR swap (OIS) curve, as swap-curve vendor models build their zero-vol path.
             The curve is read from a file (tenor,rate in percent, on the as-of date) or, without one, ESTIMATED as
             Treasury par yields + assumed swap spreads (swap minus Treasury, bp; bills use today's SOFR-bill basis).
             1M SOFR = forward 1-month swap rate + (today's SOFR - 1M swap rate); mortgage = today's mortgage rate +
             the change in the forward 10-year swap rate. The Treasury columns (y1..y30, y10) stay Treasury forwards.
    flat     today's rates held for the whole horizon.
    file     a house view: CSV with a `month` column (1..H) and columns short, y10, mort (required) and any of
             y1, y2, y5, y30, in PERCENT. Missing optional tenors are filled from the forward baseline.

Returned frame: index month 0..H (month 0 = today's rates), columns short, y1, y2, y5, y10, y30, mort, decimals.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .curve import TENOR_YEARS, bootstrap, par_yield
from .data import Spot

COLS = ["short", "y1", "y2", "y5", "y10", "y30", "mort"]
TENOR_Y = {"y1": 1.0, "y2": 2.0, "y5": 5.0, "y10": 10.0, "y30": 30.0}


def _spot_row(spot: Spot) -> dict:
    c = spot.curve
    return {"short": spot.sofr_1m, "y1": c["1Y"], "y2": c["2Y"], "y5": c["5Y"], "y10": c["10Y"], "y30": c["30Y"],
            "mort": spot.mort}


def forward_baseline(spot: Spot, H: int) -> pd.DataFrame:
    par = {k: v for k, v in spot.curve.items() if k != "20Y"}
    aux = {"20Y": spot.curve["20Y"]} if "20Y" in spot.curve else None
    dc = bootstrap(par, aux)
    basis = spot.sofr_1m - spot.curve["1M"]
    mspread = spot.mort - spot.curve["10Y"]
    rows = []
    for m in range(H + 1):
        h = m / 12.0
        r = {k: par_yield(dc, h, T) for k, T in TENOR_Y.items()}
        r["short"] = par_yield(dc, h, 1 / 12) + basis
        r["mort"] = r["y10"] + mspread
        rows.append(r)
    df = pd.DataFrame(rows)[COLS]
    df.iloc[0] = pd.Series(_spot_row(spot))[COLS]           # month 0 is today's observed rates exactly
    df.index.name = "month"
    return df / 100.0


DEFAULT_SWAP_SPREADS_BP = {"2Y": -18, "3Y": -22, "5Y": -30, "7Y": -37, "10Y": -45, "20Y": -75, "30Y": -80}


def read_swap_curve(path: str) -> dict:
    """SOFR swap/OIS par curve from a CSV with columns tenor, rate (percent); tenors as 1M, 3M, 6M, 1Y ... 30Y."""
    f = pd.read_csv(path)
    f.columns = [c.strip().lower() for c in f.columns]
    if not {"tenor", "rate"} <= set(f.columns):
        raise ValueError(f"swap curve file {path} needs columns tenor, rate (percent)")
    curve = {str(t).strip().upper(): float(r) for t, r in zip(f["tenor"], f["rate"]) if pd.notna(r)}
    bad = [t for t in curve if t not in TENOR_YEARS]
    if bad:
        raise ValueError(f"swap curve file {path}: unknown tenor(s) {bad}; use {list(TENOR_YEARS)}")
    if not {"1Y", "5Y", "10Y"} <= set(curve):
        raise ValueError(f"swap curve file {path} needs at least 1Y, 5Y and 10Y")
    return curve


def swap_curve(spot: Spot, curve_file: str | None = None, spreads_bp: dict | None = None) -> tuple[dict, str]:
    """The SOFR swap par curve on the as-of date (percent) and a description of where it came from."""
    basis_bp = 100 * (spot.sofr_1m - spot.curve["1M"])
    if curve_file:
        c = read_swap_curve(curve_file)
        for t in ("1M", "3M", "6M"):                 # short end missing from the file: bills + today's SOFR-bill basis
            if t not in c and t in spot.curve:
                c[t] = spot.curve[t] + basis_bp / 100
        return c, f"SOFR swap curve from {Path(curve_file).name}"
    sp = {**DEFAULT_SWAP_SPREADS_BP, **{str(k).upper(): float(v) for k, v in (spreads_bp or {}).items()}}
    c = {}
    for t, y in spot.curve.items():
        T = TENOR_YEARS[t]
        c[t] = y + (basis_bp if T <= 1.0 else float(np.interp(T, [TENOR_YEARS[k] for k in sorted(sp, key=TENOR_YEARS.get)],
                                                                [sp[k] for k in sorted(sp, key=TENOR_YEARS.get)]))) / 100
    return c, ("SOFR swap curve ESTIMATED as Treasury + assumed swap spreads ("
               + ", ".join(f"{k} {v:+.0f}" for k, v in sorted(sp.items(), key=lambda kv: TENOR_YEARS[kv[0]])) + " bp)")


def swap_baseline(spot: Spot, H: int, curve_file: str | None = None, spreads_bp: dict | None = None) -> pd.DataFrame:
    sc, _ = swap_curve(spot, curve_file, spreads_bp)
    par = {k: v for k, v in sc.items() if k != "20Y"}
    aux = {"20Y": sc["20Y"]} if "20Y" in sc else None
    dc = bootstrap(par, aux, max_maturity=max(15.0, min(30.0, max(TENOR_YEARS[k] for k in sc))))
    df = forward_baseline(spot, H) * 100                     # Treasury columns stay Treasury forwards
    one = par_yield(dc, 0.0, 1 / 12)
    ten = par_yield(dc, 0.0, 10.0)
    for m in range(1, H + 1):
        h = m / 12.0
        df.loc[m, "short"] = par_yield(dc, h, 1 / 12) - one + spot.sofr_1m
        df.loc[m, "mort"] = spot.mort + par_yield(dc, h, 10.0) - ten
    return df / 100.0


def flat_baseline(spot: Spot, H: int) -> pd.DataFrame:
    df = pd.DataFrame([_spot_row(spot)] * (H + 1))[COLS]
    df.index.name = "month"
    return df / 100.0


def file_baseline(path: str, spot: Spot, H: int) -> pd.DataFrame:
    f = pd.read_csv(path)
    f.columns = [c.strip().lower() for c in f.columns]
    missing = [c for c in ("month", "short", "y10", "mort") if c not in f.columns]
    if missing:
        raise ValueError(f"baseline file {path} is missing column(s) {missing}")
    f = f.set_index("month").sort_index()
    need = set(range(1, H + 1))
    if not need.issubset(set(f.index)):
        raise ValueError(f"baseline file {path} must have months 1..{H}")
    fwd = forward_baseline(spot, H) * 100
    df = fwd.copy()
    for c in COLS:
        if c in f.columns:
            df.loc[1:H, c] = f.loc[1:H, c].to_numpy(float)
    return df / 100.0


def build_baseline(kind: str, spot: Spot, H: int, path: str | None = None, swap: dict | None = None) -> pd.DataFrame:
    if kind == "forward":
        return forward_baseline(spot, H)
    if kind == "swap":
        swap = swap or {}
        return swap_baseline(spot, H, swap.get("curve_file"), swap.get("spreads_bp"))
    if kind == "flat":
        return flat_baseline(spot, H)
    if kind == "file":
        if not path:
            raise ValueError("baseline.type: file needs baseline.file")
        return file_baseline(path, spot, H)
    raise ValueError(f"unknown baseline type {kind!r} (forward | swap | flat | file)")


def describe(kind: str, path: str | None = None, spot: Spot | None = None, swap: dict | None = None) -> str:
    if kind == "swap" and spot is not None:
        swap = swap or {}
        return ("Implied forwards from the " + swap_curve(spot, swap.get("curve_file"), swap.get("spreads_bp"))[1]
                + "; SOFR basis and mortgage spread held at today's level")
    return {"forward": "Implied forwards from the as-of Treasury curve; SOFR basis and mortgage spread held at today's level",
            "swap": "Implied forwards from a SOFR swap curve",
            "flat": "Today's rates held flat",
            "file": f"Path from {Path(path).name if path else 'file'} (house view or another model's zero-vol path)"}.get(kind, kind)

