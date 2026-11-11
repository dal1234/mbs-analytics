"""Bootstrap the as-of Treasury curve and derive implied-forward constant-maturity yields: the forward baseline.

Method (industry standard par-curve bootstrap)
* CMT yields <= 3M are bond-equivalent bill yields -> treated as semiannually
  compounded zero rates.
* CMT yields >= 6M are par yields of semiannual-coupon bonds.  The par curve is
  interpolated to a semiannual grid with a monotone cubic (PCHIP) and bootstrapped
  recursively into discount factors.
* Between knots the log discount factor is interpolated with a monotone cubic
  (PCHIP), which keeps discount factors decreasing and forwards smooth;
  beyond the last knot the instantaneous forward is held at the 25-30Y average.
* The forward CMT curve at horizon h is the par (or zero, for bills) yield of a
  security issued at h, priced off the forward discount factors P(h+s)/P(h).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.interpolate import PchipInterpolator

CURVE_TENORS = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "30Y"]   # Treasury CMT points bootstrapped
TENOR_YEARS = {"1M": 1 / 12, "3M": 0.25, "6M": 0.5, "1Y": 1.0, "2Y": 2.0, "3Y": 3.0,
               "5Y": 5.0, "7Y": 7.0, "10Y": 10.0, "20Y": 20.0, "30Y": 30.0}


class DiscountCurve:
    def __init__(self, knot_t: np.ndarray, knot_logp: np.ndarray, tail_fwd: float):
        self.t = knot_t
        self.lp = knot_logp
        self.tail_fwd = tail_fwd
        # monotone cubic in log-discount space: smooth forwards, P stays decreasing
        self._interp = PchipInterpolator(knot_t, knot_logp, extrapolate=False)

    def logP(self, t):
        t = np.atleast_1d(np.asarray(t, dtype=float))
        out = self._interp(np.clip(t, self.t[0], self.t[-1]))
        beyond = t > self.t[-1]
        out[beyond] = self.lp[-1] - self.tail_fwd * (t[beyond] - self.t[-1])
        return out

    def P(self, t):
        return np.exp(self.logP(t))

    def zero_rate(self, t, comp: int = 2):
        """Continuously-bootstrapped zero rate expressed with `comp` compounding (percent)."""
        P = self.P(t)
        t = np.atleast_1d(np.asarray(t, dtype=float))
        return 100 * comp * (P ** (-1 / (comp * t)) - 1)


def bootstrap(par_yields: dict[str, float], aux: dict[str, float] | None = None,
              max_maturity: float = 30.0) -> DiscountCurve:
    pts = {TENOR_YEARS[k]: float(v) for k, v in par_yields.items()}
    for k, v in (aux or {}).items():
        pts.setdefault(TENOR_YEARS[k], float(v))
    knot_t, knot_lp = [0.0], [0.0]
    for T in sorted(pts):
        if T < 0.5 - 1e-9:                       # bills: semiannual zero yields
            P = (1 + pts[T] / 200.0) ** (-2 * T)
            knot_t.append(T)
            knot_lp.append(np.log(P))
    coupon = {T: y for T, y in pts.items() if T >= 0.5 - 1e-9}
    xs = np.array(sorted(coupon))
    ys = np.array([coupon[T] for T in xs])
    pch = PchipInterpolator(xs, ys, extrapolate=True)
    grid = np.arange(0.5, max_maturity + 1e-9, 0.5)
    par_grid = pch(grid)
    cum = 0.0
    for T, y in zip(grid, par_grid):
        c = y / 200.0
        P = (1 - c * cum) / (1 + c)
        if P <= 0:
            raise ValueError(f"bootstrap produced non-positive discount factor at T={T}")
        cum += P
        knot_t.append(float(T))
        knot_lp.append(float(np.log(P)))
    knot_t = np.array(knot_t)
    knot_lp = np.array(knot_lp)
    i25 = int(np.searchsorted(knot_t, max_maturity - 5.0))
    tail_fwd = -(knot_lp[-1] - knot_lp[i25]) / (knot_t[-1] - knot_t[i25])
    return DiscountCurve(knot_t, knot_lp, float(tail_fwd))


def par_yield(dc: DiscountCurve, h: float, T: float) -> float:
    """CMT-style yield of tenor T (years) for a security issued at horizon h (years)."""
    Ph = float(dc.P(h)[0])
    if T < 0.5 - 1e-9:
        Pt = float(dc.P(h + T)[0]) / Ph
        return 200.0 * (Pt ** (-1.0 / (2.0 * T)) - 1.0)
    n = int(round(2 * T))
    times = h + np.arange(1, n + 1) / 2.0
    annuity = dc.P(times).sum() / Ph
    PT = float(dc.P(h + T)[0]) / Ph
    return 200.0 * (1.0 - PT) / annuity


def forward_curve(dc: DiscountCurve, h_years: float, tenors=CURVE_TENORS) -> np.ndarray:
    return np.array([par_yield(dc, h_years, TENOR_YEARS[t]) for t in tenors])


def zero_vol_path(par_yields: dict[str, float], aux: dict[str, float] | None,
                  horizon_months: int) -> pd.DataFrame:
    """Implied-forward CMT curve for each month 0..H (rows) and tenor (columns)."""
    dc = bootstrap(par_yields, aux)
    rows = [forward_curve(dc, m / 12.0) for m in range(horizon_months + 1)]
    out = pd.DataFrame(rows, columns=[f"TSY_{t}" for t in CURVE_TENORS])
    out.index.name = "month"
    return out
