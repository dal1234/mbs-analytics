"""
Deterministic rate scenarios built from the same baseline and calibration as the Monte Carlo (whitepaper section 13.4).

A Monte Carlo path is the baseline plus a random deviation from the two-factor model (model.simulate_paths):

    r_k(t) = max( b_k(t) + d_k(t), floor ),   d_short = x_S,  d_10y = x_L,  d_mort = beta * x_L + s,
                                              d_tenor = a_S x_S + a_L x_L

A deterministic scenario replaces the random deviation with a fixed one and sets the mortgage spread deviation s to 0:

    parallel  +X   every Treasury tenor and 1M SOFR move X; the mortgage rate moves beta * X (the calibrated
                   pass-through of the 10-year to the mortgage rate). Immediate (month 1 on) or ramped linearly
                   over ramp_months and then held.
    shape  (S, L)  the two model factors move: x_S = S on the short rate (1M SOFR), x_L = L on the 10-year; the
                   1/2/5/30-year yields follow their calibrated loadings a_S S + a_L L; the mortgage rate moves
                   beta * L. Immediate.
    base           the baseline itself (deviation 0): the centre of the Monte Carlo fan and the EaR reference.
    flat           today's rates held for the whole horizon (reference row; not a shock of the baseline).

Every scenario rate is floored at the simulation floor, as the Monte Carlo paths are.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .model import Calibration

KEYS = ("short", "y1", "y2", "y5", "y10", "y30", "mort")
TENOR_KEYS = ("y1", "y2", "y5", "y30")

DEFAULT_SPEC = {
    "parallel_bp": [100, 200, 300, 400],
    "ramp_months": 12,
    "shapes": [
        {"id": "BULL_STEEP", "name": "Bull steepener", "short_bp": -200, "long_bp": -50,
         "desc": "Short rates fall more than long rates (easing cycle); the curve steepens."},
        {"id": "BULL_FLAT", "name": "Bull flattener", "short_bp": -50, "long_bp": -200,
         "desc": "Long rates fall more than short rates (flight to quality); the curve flattens."},
        {"id": "BEAR_STEEP", "name": "Bear steepener", "short_bp": 50, "long_bp": 200,
         "desc": "Long rates rise more than short rates (inflation or term-premium shock); the curve steepens."},
        {"id": "BEAR_FLAT", "name": "Bear flattener", "short_bp": 200, "long_bp": 50,
         "desc": "Short rates rise more than long rates (tightening cycle); the curve flattens."},
        {"id": "TWIST_STEEP", "name": "Twist steepener", "short_bp": -100, "long_bp": 100,
         "desc": "Short rates fall while long rates rise."},
        {"id": "TWIST_FLAT", "name": "Twist flattener", "short_bp": 100, "long_bp": -100,
         "desc": "Short rates rise while long rates fall."},
    ],
    "include_flat": True,
}


def _sbp(v) -> str:
    """Signed basis points for labels, with a typographic minus."""
    return f"{int(v):+d}".replace("-", "−")


def spec_with_defaults(spec: Optional[dict]) -> dict:
    out = {**DEFAULT_SPEC, **(spec or {})}
    out["parallel_bp"] = sorted({abs(int(x)) for x in out["parallel_bp"]})
    return out


def catalog(spec: Optional[dict] = None) -> List[dict]:
    """Scenario definitions in display order: base, flat, parallel (immediate, ramped), shape."""
    s = spec_with_defaults(spec)
    ramp = int(s.get("ramp_months") or 0)
    rows = [{"id": "BASE", "name": "Baseline", "family": "base", "short_bp": 0, "long_bp": 0, "ramp": 0,
             "desc": "The Monte Carlo baseline (centre of the fan): the EaR reference."}]
    if s.get("include_flat", True):
        rows.append({"id": "FLAT", "name": "Flat (today's rates held)", "family": "reference", "short_bp": None,
                     "long_bp": None, "ramp": 0, "desc": "Today's rates held for the whole horizon."})
    for timing in ("parallel", "parallel_ramp") if ramp else ("parallel",):
        for bp in [b for b in s["parallel_bp"]][::-1] + [-b for b in s["parallel_bp"]]:
            sid = f"PAR_{'UP' if bp > 0 else 'DN'}{abs(bp)}" + (f"_R{ramp}" if timing == "parallel_ramp" else "")
            name = f"{'+' if bp > 0 else '−'}{abs(bp)} bp" + (f" ramp {ramp}m" if timing == "parallel_ramp" else "")
            desc = (f"Every Treasury tenor and 1M SOFR {'+' if bp > 0 else '−'}{abs(bp)} bp"
                    + (f", phased in linearly over {ramp} months, then held" if timing == "parallel_ramp" else " from month 1")
                    + "; the mortgage rate moves beta × the shock.")
            rows.append({"id": sid, "name": name, "family": timing, "short_bp": bp, "long_bp": bp,
                         "ramp": ramp if timing == "parallel_ramp" else 0, "desc": desc})
    for sh in s["shapes"]:
        rows.append({"id": sh["id"], "name": f"{sh['name']} ({_sbp(sh['short_bp'])}/{_sbp(sh['long_bp'])})",
                     "family": "shape", "short_bp": int(sh["short_bp"]), "long_bp": int(sh["long_bp"]), "ramp": 0,
                     "desc": sh.get("desc") or (f"1M SOFR {_sbp(sh['short_bp'])} bp and the 10-year {_sbp(sh['long_bp'])} bp "
                                                "from month 1; the 1/2/5/30-year yields follow their factor loadings, the "
                                                "mortgage rate beta × the 10-year move.")})
    return rows


def deviation(cal: Calibration, family: str, short_bp: float, long_bp: float) -> Dict[str, float]:
    """Full-size deviation of each rate (decimal) for one scenario, before timing and floor."""
    S, L = short_bp / 1e4, long_bp / 1e4
    if family in ("parallel", "parallel_ramp"):
        d = {k: S for k in KEYS}
        d["mort"] = cal.beta_mort * S
        return d
    if family == "shape":
        d = {"short": S, "y10": L, "mort": cal.beta_mort * L}
        for t in TENOR_KEYS:
            a_s, a_l = cal.tenor_loadings.get(t, (0.0, 1.0))
            d[t] = a_s * S + a_l * L
        return d
    return {k: 0.0 for k in KEYS}


def timing(n_months: int, ramp: int) -> np.ndarray:
    """Weight of the full shock in months 1..n: 1 throughout (immediate) or a linear ramp to 1 at month ``ramp``."""
    m = np.arange(1, n_months + 1, dtype=float)
    return np.minimum(1.0, m / ramp) if ramp else np.ones(n_months)


def build(baseline: pd.DataFrame, spot: Dict[str, float], cal: Calibration, spec: Optional[dict] = None,
          floor: float = 0.0) -> tuple[List[dict], Dict[str, pd.DataFrame]]:
    """
    Scenario paths for months 1..n. ``baseline``: rows = months 1..n, columns KEYS (decimal), as simulate_paths takes;
    ``spot``: today's value of each key (decimal), used by the flat scenario. Returns (catalog, {id: frame}).
    The base scenario is the baseline exactly (no floor applied), so its income equals the EaR baseline income.
    """
    cat = catalog(spec)
    n = len(baseline)
    out = {}
    for row in cat:
        if row["family"] == "base":
            out[row["id"]] = baseline[list(KEYS)].copy()
            continue
        if row["family"] == "reference":
            out[row["id"]] = pd.DataFrame({k: np.full(n, spot[k]) for k in KEYS}, index=baseline.index)
            continue
        dev = deviation(cal, row["family"], row["short_bp"], row["long_bp"])
        w = timing(n, row["ramp"])
        out[row["id"]] = pd.DataFrame({k: np.maximum(baseline[k].to_numpy(float) + w * dev[k], floor) for k in KEYS},
                                      index=baseline.index)
    return cat, out


def mortgage_spread(path: np.ndarray, spread_bp: float, ramp_months: int = 3) -> np.ndarray:
    """Overlay on a mortgage-rate path (months 1..n, decimal): + spread_bp, phased in linearly over ramp_months.
    Treasuries and SOFR are unchanged; the mortgage rate is not floored again (the spread is a separate input)."""
    return path + spread_bp / 1e4 * timing(len(path), ramp_months)
