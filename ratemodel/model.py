"""
Real-world, baseline-centred, two-factor Gaussian interest-rate path generator for Earnings at Risk (EaR) on MBS
interest income. Methodology, decisions and references: the whitepaper (dashboard page "Rate model",
template mbs_lite/templates/whitepaper.html).

Ported from ear_rate_model.py v1.0.0 (revision of 2026-11-10: horizon matching targets the fan width,
ar1_conditional_std; V2 compares fan width with history; V7 reports dispersion relative to its bootstrap mean).
Version 1.1.0 (2026-11-10): the mortgage rate is calibrated like the factors (beta from 12-month changes, the spread
fitted to the mortgage fan width). Version 1.2.0 (2026-11-11): every calibration target is the historical dispersion
of DEVIATIONS FROM THE FORWARD BASELINE (forwards.py), the quantity the simulated fan represents, instead of raw rate
changes; each factor's shock is itself AR(1) ("momentum": rate moves persist for a few months, as in easing and
tightening cycles), which the v1.1.0 first-order factor could not reproduce (its short-rate phi sat at the upper bound);
the shock correlation is solved so the model's 12-month deviation correlation equals history's; beta, the spread and
the tenor loadings use forward deviations too. Plain AR(1) is the special case momentum = 0. The reference income engine
was dropped (the app's own engine in mbs_lite is used). The FRED download is in ratemodel/data.py, which reads the
cached files in data/fred. Validation is split into the rate-only tests here (V1-V4, V7) and the income tests in
mbs_lite/validation.py (V5, V6, V8).

All rates are decimals (0.045 = 4.5%). The time step is one month.

    r_k(t) = max( b_k(t) + d_k(t), floor )            k = short, 10y, mortgage, other tenors
    x_S(t) = phi_S x_S(t-1) + u_S(t),  u_S(t) = m_S u_S(t-1) + sigma_S e_S(t)    short-rate deviation (1m SOFR)
    x_L(t) = phi_L x_L(t-1) + u_L(t),  u_L(t) = m_L u_L(t-1) + sigma_L e_L(t)    long-rate deviation (10y)
    s(t)   = phi_M s(t-1)   + sigma_M e_M(t)                                     primary/secondary spread deviation
    corr(e_S, e_L) = rho,  e_M independent,  x(0) = u(0) = s(0) = 0
    d_short = x_S,  d_10y = x_L,  d_mort = beta * x_L + s,  d_tenor = a_S x_S + a_L x_L
    all fitted to the historical dispersion of deviations from the forward baseline at 3-60 months (v1.2.0)
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Dict, Optional, Sequence

import numpy as np
import pandas as pd
from scipy import optimize

from .forwards import deviations

MODEL_VERSION = "1.2.0"

# --------------------------------------------------------------------------- #
# Calibration settings (fixed by design: whitepaper section 10.1; changing them is a model change)
# --------------------------------------------------------------------------- #
CALIBRATION_WINDOWS: Dict[str, tuple] = {
    "base": ("1990-01", None),       # multiple cycles; excludes 1979-82 reserve-targeting regime
    "recent": (120, None),           # trailing 10 years
    "high_vol": ("1976-01", None),   # includes the Volcker period: conservative bound
}
OFFICIAL_CALIBRATION = "base"
CALIBRATION_HORIZONS = (3, 12, 24, 36, 60)   # months; change horizons matched in calibration
CORR_HORIZON = 12                            # months; horizon at which short/long correlation is matched
REGRESSION_HORIZON = 3                       # months; change horizon for beta and tenor loadings
PHI_BOUNDS = (0.80, 0.9995)                  # monthly AR(1) coefficient bounds
MOM_BOUNDS = (-0.50, 0.95)                   # monthly momentum (shock persistence) bounds
TARGET_HORIZONS = (3, 12, 24, 36, 48, 60)    # months; deviation dispersion stored for V2
MIN_WINDOW_MONTHS = 96
OPTIONAL_TENORS = ("y1", "y2", "y5", "y30")


@dataclass
class Calibration:
    name: str
    model_version: str
    window_start: str
    window_end: str
    n_obs: int
    phi_short: float
    sigma_short: float      # monthly shock st.dev., decimal
    phi_long: float
    sigma_long: float
    rho: float              # shock correlation short/long
    beta_mort: float        # mortgage-rate sensitivity to the 10y deviation
    phi_spread: float
    sigma_spread: float
    tenor_loadings: Dict[str, tuple] = field(default_factory=dict)   # tenor -> (a_S, a_L)
    diagnostics: Dict[str, object] = field(default_factory=dict)
    mom_short: float = 0.0  # v1.2.0: shock persistence (momentum); 0 = plain AR(1), the v1.0/1.1 model
    mom_long: float = 0.0

    def factor_std(self, k: str, h) -> np.ndarray:
        """St.dev. of factor k ('short' | 'long') h months ahead from a zero start."""
        if k == "short":
            return factor_conditional_std(self.phi_short, self.mom_short, self.sigma_short, h)
        return factor_conditional_std(self.phi_long, self.mom_long, self.sigma_long, h)

    def to_dict(self) -> dict:
        return json.loads(json.dumps(asdict(self), default=float))

    @staticmethod
    def from_dict(d: dict) -> "Calibration":
        d = dict(d)
        d["tenor_loadings"] = {k: tuple(v) for k, v in d["tenor_loadings"].items()}
        return Calibration(**d)

    def summary(self) -> pd.DataFrame:
        """Parameters in reporting units (annualized bp, half-life in years)."""
        def hl(phi):
            return np.log(0.5) / np.log(phi) / 12.0
        rows = [
            ("Short factor: annualized shock vol (bp)", self.sigma_short * np.sqrt(12) * 1e4),
            ("Short factor: mean-reversion half-life (yrs)", hl(self.phi_short)),
            ("Short factor: momentum (monthly)", self.mom_short),
            ("Long factor: annualized shock vol (bp)", self.sigma_long * np.sqrt(12) * 1e4),
            ("Long factor: mean-reversion half-life (yrs)", hl(self.phi_long)),
            ("Long factor: momentum (monthly)", self.mom_long),
            ("Short/long shock correlation", self.rho),
            ("Mortgage beta to 10y", self.beta_mort),
            ("Spread: annualized shock vol (bp)", self.sigma_spread * np.sqrt(12) * 1e4),
            ("Spread: mean-reversion half-life (yrs)", hl(self.phi_spread)),
        ]
        return pd.DataFrame(rows, columns=["parameter", "value"]).set_index("parameter")


def _change_std(y: np.ndarray, h: int) -> float:
    """Sample st.dev. of overlapping h-month changes."""
    return float(np.std(y[h:] - y[:-h], ddof=1))


def ar1_change_std(phi: float, sigma: float, h) -> np.ndarray:
    """Unconditional st.dev. of the h-month change of a stationary AR(1)."""
    h = np.asarray(h, dtype=float)
    g0 = sigma ** 2 / (1.0 - phi ** 2)
    return np.sqrt(2.0 * g0 * (1.0 - phi ** h))


def ar1_conditional_std(phi: float, sigma: float, h) -> np.ndarray:
    """St.dev. of an AR(1) h months ahead, conditional on starting at zero deviation."""
    h = np.asarray(h, dtype=float)
    return np.sqrt(sigma ** 2 * (1.0 - phi ** (2.0 * h)) / (1.0 - phi ** 2))


def fit_ar1_ols(y: np.ndarray) -> dict:
    """OLS AR(1) with intercept, plus Kendall (1954) small-sample bias correction."""
    x, z = y[:-1], y[1:]
    T = len(x)
    phi = float(np.cov(x, z, ddof=1)[0, 1] / np.var(x, ddof=1))
    phi_bc = float(min(phi + (1.0 + 3.0 * phi) / T, PHI_BOUNDS[1]))
    resid = (z - z.mean()) - phi_bc * (x - x.mean())
    return {"phi_ols": phi, "phi_bias_corrected": phi_bc, "sigma": float(np.std(resid, ddof=2))}


def fit_ar1_horizon(y: np.ndarray, horizons: Sequence[int] = CALIBRATION_HORIZONS) -> dict:
    """
    v1.0/1.1 estimator, kept as a challenger (diagnostics 'v1_1_challenger') and for tools/estimator_study.py.
    Fit (phi, sigma) so the simulated fan width matches history: the st.dev. of the deviation h months ahead
    (starting from zero, ar1_conditional_std) is matched to the empirical st.dev. of overlapping h-month changes
    across horizons. Weighted least squares on log st.dev.; weight = T/h (approx. independent observations).

    The whole historical change is treated as unpredictable relative to the baseline. Under an exactly AR(1)
    history part of the change is predictable mean reversion, so this target is at or above the AR(1)'s own
    conditional dispersion (by sqrt(2 / (1 + phi^h))): a deliberate, conservative choice (whitepaper 6.1).
    """
    horizons = [h for h in horizons if h < len(y) // 3]
    emp = np.array([_change_std(y, h) for h in horizons])
    w = np.sqrt(np.array([len(y) / h for h in horizons], dtype=float))
    hs = np.array(horizons, dtype=float)

    def resid(p):
        return w * (np.log(ar1_conditional_std(p[0], p[1], hs)) - np.log(emp))

    ols = fit_ar1_ols(y)
    x0 = [float(np.clip(ols["phi_bias_corrected"], PHI_BOUNDS[0] + 1e-4, PHI_BOUNDS[1] - 1e-4)),
          max(ols["sigma"], 1e-5)]
    sol = optimize.least_squares(resid, x0, bounds=([PHI_BOUNDS[0], 1e-6], [PHI_BOUNDS[1], 0.05]))
    phi, sigma = float(sol.x[0]), float(sol.x[1])
    fit = pd.DataFrame({"horizon_m": horizons, "empirical_bp": emp * 1e4,
                        "model_bp": ar1_conditional_std(phi, sigma, hs) * 1e4})
    fit["ratio"] = fit["model_bp"] / fit["empirical_bp"]
    return {"phi": phi, "sigma": sigma, "fit": fit.to_dict("records"), "ols_challenger": ols,
            "phi_at_bound": bool(min(phi - PHI_BOUNDS[0], PHI_BOUNDS[1] - phi) < 1e-6)}


def psi_weights(phi: float, mom: float, n: int) -> np.ndarray:
    """Moving-average weights of x(t) = phi x(t-1) + u(t), u(t) = mom u(t-1) + e(t): x(h) = sum_j psi_j e(h - j)."""
    ps = np.zeros(max(int(n), 2))
    ps[0], ps[1] = 1.0, phi + mom
    for j in range(2, len(ps)):
        ps[j] = (phi + mom) * ps[j - 1] - phi * mom * ps[j - 2]
    return ps[: int(n)]


def factor_conditional_std(phi: float, mom: float, sigma: float, h) -> np.ndarray:
    """St.dev. of the factor h months ahead from a zero start (x = u = 0). mom = 0 gives ar1_conditional_std."""
    h = np.atleast_1d(np.asarray(h)).astype(int)
    c = np.cumsum(psi_weights(phi, mom, int(h.max())) ** 2)
    out = sigma * np.sqrt(c[h - 1])
    return out if out.size > 1 else out.reshape(())


def deviation_targets(hist: pd.DataFrame, fwd: Optional[pd.DataFrame], col: str,
                      horizons: Sequence[int] = TARGET_HORIZONS) -> list:
    """Historical dispersion of h-month deviations from the forward (whitepaper 6.1): the calibration target."""
    rows = []
    for h in horizons:
        d = deviations(hist, fwd, col, h).dropna().to_numpy()
        if len(d) + h > 3 * h and len(d) > 24:        # window longer than 3h, as in v1.0/1.1
            rows.append({"horizon_m": int(h), "sd_bp": float(np.std(d, ddof=1) * 1e4), "mean_bp": float(d.mean() * 1e4),
                         "rms_bp": float(np.sqrt(np.mean(d ** 2)) * 1e4), "n": int(len(d))})
    return rows


def _pick(targets: list, horizons: Sequence[int] = CALIBRATION_HORIZONS) -> tuple:
    t = [r for r in targets if r["horizon_m"] in horizons]
    hs = np.array([r["horizon_m"] for r in t], dtype=float)
    return hs, np.array([r["sd_bp"] for r in t]) / 1e4, np.sqrt(np.array([r["n"] / r["horizon_m"] for r in t], dtype=float))


def fit_factor_horizon(targets: list, horizons: Sequence[int] = CALIBRATION_HORIZONS) -> dict:
    """
    v1.2.0 production estimator (whitepaper 6.1): fit (phi, momentum, sigma) so the factor's fan width h months ahead
    (factor_conditional_std, zero start) matches the historical st.dev. of h-month deviations from the forward, at the
    calibration horizons. Weighted least squares on log st.dev., weight = sqrt(T/h) (approx. independent observations).
    A plain AR(1) (momentum fixed at 0) fitted to the same targets is reported as the challenger.
    """
    hs, emp, w = _pick(targets, horizons)

    def resid(p):
        return w * (np.log(factor_conditional_std(p[0], p[1], p[2], hs)) - np.log(emp))

    lo, hi = [PHI_BOUNDS[0], MOM_BOUNDS[0], 1e-6], [PHI_BOUNDS[1], MOM_BOUNDS[1], 0.05]
    best = None
    for x0 in ([0.98, 0.0, emp[0] / np.sqrt(hs[0])], [0.97, 0.4, emp[0] / hs[0]], [0.95, 0.7, emp[0] / hs[0]]):
        sol = optimize.least_squares(resid, x0, bounds=(lo, hi))
        if best is None or sol.cost < best.cost - 1e-12:
            best = sol
    phi, mom, sigma = (float(x) for x in best.x)
    ar = optimize.least_squares(lambda p: w * (np.log(ar1_conditional_std(p[0], p[1], hs)) - np.log(emp)),
                                [0.98, emp[0] / np.sqrt(hs[0])], bounds=([PHI_BOUNDS[0], 1e-6], [PHI_BOUNDS[1], 0.05]))
    model = factor_conditional_std(phi, mom, sigma, hs)
    ar_model = ar1_conditional_std(ar.x[0], ar.x[1], hs)
    fit = [{"horizon_m": int(h), "empirical_bp": float(e * 1e4), "model_bp": float(m * 1e4), "ratio": float(m / e),
            "ar1_ratio": float(a / e)} for h, e, m, a in zip(hs, emp, model, ar_model)]
    gap = lambda v, b: min(v - b[0], b[1] - v) < 1e-6  # noqa: E731
    return {"phi": phi, "mom": mom, "sigma": sigma, "fit": fit,
            "ar1_challenger": {"phi": float(ar.x[0]), "sigma": float(ar.x[1]), "phi_at_bound": bool(gap(ar.x[0], PHI_BOUNDS))},
            "phi_at_bound": bool(gap(phi, PHI_BOUNDS)), "mom_at_bound": bool(gap(mom, MOM_BOUNDS))}


def mortgage_fan_std(beta: float, long_std, phi_m: float, sig_m: float, h) -> np.ndarray:
    """St.dev. of the mortgage-rate deviation h months ahead from a zero start: beta x 10y factor + independent spread.
    long_std: the 10-year factor's st.dev. at the same horizons."""
    return np.sqrt((beta * np.asarray(long_std)) ** 2 + ar1_conditional_std(phi_m, sig_m, h) ** 2)


def fit_mortgage_spread(targets: list, beta: float, long_std_fn, horizons: Sequence[int] = CALIBRATION_HORIZONS) -> dict:
    """
    Whitepaper 6.3: with beta and the 10-year factor fixed, fit the spread's (phi, sigma) so the total mortgage fan width
    h months ahead matches the historical st.dev. of h-month mortgage-rate deviations from the forward, with the same
    horizons and weights as fit_factor_horizon. long_std_fn(h) = the 10-year factor st.dev. at horizons h.
    """
    hs, emp, w = _pick(targets, horizons)
    ls = long_std_fn(hs)

    def resid(p):
        return w * (np.log(mortgage_fan_std(beta, ls, p[0], p[1], hs)) - np.log(emp))

    sol = optimize.least_squares(resid, [0.95, 0.0015], bounds=([PHI_BOUNDS[0], 1e-6], [PHI_BOUNDS[1], 0.05]))
    phi, sigma = float(sol.x[0]), float(sol.x[1])
    fan = mortgage_fan_std(beta, ls, phi, sigma, hs)
    fit = [{"horizon_m": int(h), "empirical_bp": float(e * 1e4), "model_bp": float(m * 1e4), "ratio": float(m / e)}
           for h, e, m in zip(hs, emp, fan)]
    return {"phi": phi, "sigma": sigma, "fit": fit,
            "phi_at_bound": bool(min(phi - PHI_BOUNDS[0], PHI_BOUNDS[1] - phi) < 1e-6)}


def _ols_no_intercept(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.linalg.lstsq(X, y, rcond=None)[0]


def resolve_window(hist: pd.DataFrame, window: tuple) -> pd.DataFrame:
    start, end = window
    h = hist if end is None else hist[hist.index <= pd.Period(end, "M")]
    if isinstance(start, int):
        return h.iloc[-start:]
    return h if start is None else h[h.index >= pd.Period(start, "M")]


def _cov_beta(x: np.ndarray, y: np.ndarray) -> float:
    return float(np.cov(x, y, ddof=1)[0, 1] / np.var(x, ddof=1))


def calibrate(hist: pd.DataFrame, name: str = OFFICIAL_CALIBRATION, window: Optional[tuple] = None,
              fwd: Optional[pd.DataFrame] = None) -> Calibration:
    """
    Estimate all parameters on a named window (whitepaper section 6). fwd: forward changes (forwards.forward_changes)
    for the history; every target is then a deviation from the forward baseline. fwd None = raw changes (a flat
    forward), used for synthetic histories in tests; production calibrations always pass fwd.
    """
    window = window or CALIBRATION_WINDOWS[name]
    h = resolve_window(hist, window)
    if len(h) < MIN_WINDOW_MONTHS:
        raise ValueError(f"Calibration window '{name}' has {len(h)} months; at least {MIN_WINDOW_MONTHS} required.")
    dev = lambda col, x: deviations(h, fwd, col, x)  # noqa: E731
    targets = {c: deviation_targets(h, fwd, c) for c in ("short", "y10", "mort")}

    # 6.1 factor dynamics: fan width matched to the dispersion of forward deviations
    fs, fl = fit_factor_horizon(targets["short"]), fit_factor_horizon(targets["y10"])
    sd = lambda f, x: factor_conditional_std(f["phi"], f["mom"], f["sigma"], x)  # noqa: E731

    # 6.2 shock correlation: solved so the model's 12-month deviation correlation equals history's
    k = CORR_HORIZON
    pair = pd.concat([dev("short", k), dev("y10", k), dev("mort", k)], axis=1, keys=["s", "l", "m"]).dropna()
    c12 = float(pair["s"].corr(pair["l"]))
    ps, pl = psi_weights(fs["phi"], fs["mom"], k), psi_weights(fl["phi"], fl["mom"], k)
    shape = float(np.sum(ps * pl) / np.sqrt(np.sum(ps ** 2) * np.sum(pl ** 2)))
    rho = float(np.clip(c12 / shape, -0.99, 0.99))

    # 6.3 mortgage rate: beta from 12-month deviations, then the spread fitted to the total mortgage fan width
    beta = _cov_beta(pair["l"].to_numpy(), pair["m"].to_numpy())
    mf = fit_mortgage_spread(targets["mort"], beta, lambda x: sd(fl, x))

    # 6.4 other tenors: loadings on the two factors, from 3-month deviations
    q = REGRESSION_HORIZON
    loadings = {}
    dq = pd.concat([dev("short", q), dev("y10", q)], axis=1, keys=["s", "l"])
    for t in OPTIONAL_TENORS:
        if t not in h.columns:
            continue
        try:
            dt = pd.concat([dq, dev(t, q).rename("t")], axis=1).dropna()   # gaps (e.g. DGS30 2002-06) dropped
        except ValueError:
            continue
        if len(dt) >= 60:
            X = (dt[["s", "l"]] - dt[["s", "l"]].mean()).to_numpy()
            a = _ols_no_intercept(X, (dt["t"] - dt["t"].mean()).to_numpy())
            loadings[t] = (float(a[0]), float(a[1]))

    # challengers: the v1.1.0 calibration (raw changes, AR(1)) measured against the same forward-deviation targets
    s, l, m = h["short"].values, h["y10"].values, h["mort"].values
    v11 = {}
    for key, y, tg in (("short", s, targets["short"]), ("y10", l, targets["y10"])):
        f = fit_ar1_horizon(y)
        hs, emp, _ = _pick(tg, TARGET_HORIZONS)
        v11[key] = {"phi": f["phi"], "sigma": f["sigma"], "phi_at_bound": f["phi_at_bound"],
                    "horizons": [int(x) for x in hs], "fan_over_target": [float(x) for x in ar1_conditional_std(f["phi"], f["sigma"], hs) / emp]}
    dl_k, dm_k = l[k:] - l[:-k], m[k:] - m[:-k]
    v11["mort_beta"] = float(_ols_no_intercept(dl_k[:, None], dm_k)[0])

    diag = {
        "target": "forward_deviation" if fwd is not None else "raw_change",
        "targets": targets,
        "short_fit": fs, "long_fit": fl,
        "corr_12m_target": c12, "corr_shape_factor": shape,
        "corr_12m_changes": float(np.corrcoef(s[k:] - s[:-k], l[k:] - l[:-k])[0, 1]),       # raw changes, for reference
        "corr_monthly_changes": float(np.corrcoef(np.diff(s), np.diff(l))[0, 1]),
        "hist_dev_corr_h": {str(x): float(pd.concat([dev("short", x), dev("y10", x)], axis=1).dropna().corr().iloc[0, 1])
                            for x in (3, 12, 36)},
        "mort_fit": mf,
        "mort_beta_r2": float(np.corrcoef(pair["l"], pair["m"])[0, 1] ** 2),
        "mort_corr_12m_target": float(pair["m"].corr(pair["l"])),
        "mort_beta_by_horizon": {str(x): _cov_beta(*[d.to_numpy() for _, d in
                                                     pd.concat([dev("y10", x), dev("mort", x)], axis=1).dropna().items()])
                                 for x in (3, 12, 36, 60)},
        "spread_mean_bp": float((m - beta * l).mean() * 1e4),
        "v1_1_challenger": v11,
        "hist_inversion_freq": float(np.mean(l < s)),
    }
    return Calibration(
        name=name, model_version=MODEL_VERSION,
        window_start=str(h.index[0]), window_end=str(h.index[-1]), n_obs=len(h),
        phi_short=fs["phi"], sigma_short=fs["sigma"], phi_long=fl["phi"], sigma_long=fl["sigma"],
        rho=rho, beta_mort=beta, phi_spread=mf["phi"], sigma_spread=mf["sigma"],
        tenor_loadings=loadings, diagnostics=diag, mom_short=fs["mom"], mom_long=fl["mom"])


# --------------------------------------------------------------------------- #
# Simulation
# --------------------------------------------------------------------------- #
@dataclass
class SimConfig:
    n_paths: int = 1000
    n_months: int = 60
    antithetic: bool = True
    moment_match: bool = True
    sampler: str = "pseudo"      # "pseudo" (PCG normal draws) or "sobol" (scrambled)
    seed: int = 20261110
    floor: float = 0.0           # applied to every simulated rate
    vol: Optional[dict] = None   # None or {"type": "historical"} = the calibrated model (official). {"type": "market",
                                 # ...} = the market-implied comparison width (market_vol_monthly); not the validated model


def _draw_normals(cfg: SimConfig) -> np.ndarray:
    """Standard normal shocks, shape (n_paths, n_months, 3)."""
    n, T = cfg.n_paths, cfg.n_months
    if cfg.sampler == "sobol":
        from scipy.stats import norm, qmc
        u = qmc.Sobol(d=3 * T, scramble=True, seed=cfg.seed).random(n)
        z = norm.ppf(np.clip(u, 1e-12, 1 - 1e-12)).reshape(n, T, 3)
    else:
        rng = np.random.default_rng(cfg.seed)
        if cfg.antithetic:
            if n % 2:
                raise ValueError("n_paths must be even when antithetic=True")
            half = rng.standard_normal((n // 2, T, 3))
            z = np.concatenate([half, -half], axis=0)     # path i and i + n/2 are a pair
        else:
            z = rng.standard_normal((n, T, 3))
    if cfg.moment_match:
        z = (z - z.mean(axis=0, keepdims=True)) / z.std(axis=0, keepdims=True)
    return z


# --------------------------------------------------------------------------- #
# Market-implied comparison width (not the whitepaper model)
# --------------------------------------------------------------------------- #
def is_market(vol: Optional[dict]) -> bool:
    return bool(vol) and str(vol.get("type", "historical")).lower() == "market"


def market_vol_monthly(vol: dict, n_months: int) -> Dict[str, np.ndarray]:
    """Monthly shock st.dev. (decimal) for the short rate and the 10-year from yearly normal vols in bp per year.
    vol["sofr_bp"][k] / vol["y10_bp"][k] apply to the months of year k + 1 (forward vols); the last value repeats."""
    out = {}
    for key, name in (("short", "sofr_bp"), ("long", "y10_bp")):
        v = [float(x) for x in vol[name]]
        if not v or min(v) < 0:
            raise ValueError(f"volatility.market.{name} needs one non-negative value per year (bp per year)")
        yr = np.minimum(np.arange(n_months) // 12, len(v) - 1)
        out[key] = np.array(v)[yr] / 1e4 / np.sqrt(12.0)
    return out


def market_std(vol: dict, k: str, h) -> np.ndarray:
    """Fan st.dev. h months ahead under the market-implied width (random walk with time-dependent vol)."""
    h = np.atleast_1d(np.asarray(h)).astype(int)
    s = market_vol_monthly(vol, int(h.max()))["short" if k == "short" else "long"]
    out = np.sqrt(np.cumsum(s ** 2)[h - 1])
    return out if out.size > 1 else out.reshape(())


def market_corr(cal: "Calibration", vol: dict) -> float:
    c = vol.get("corr")
    return float(cal.diagnostics.get("corr_12m_target", cal.rho) if c is None else c)


def factor_std(cal: "Calibration", cfg: Optional["SimConfig"], k: str, h) -> np.ndarray:
    """Analytic fan st.dev. of factor k ('short' | 'long') h months ahead, for whichever width the run uses."""
    return market_std(cfg.vol, k, h) if cfg is not None and is_market(cfg.vol) else cal.factor_std(k, h)


def simulate_deviations(cal: Calibration, cfg: SimConfig) -> Dict[str, np.ndarray]:
    """Zero-mean factor deviations, each shape (n_paths, n_months)."""
    z = _draw_normals(cfg)
    if is_market(cfg.vol):
        # Comparison width only: each factor is a random walk (no mean reversion) whose monthly shock st.dev. follows
        # the yearly normal vols in cfg.vol, the shape of a risk-neutral normal model calibrated to option prices.
        # The shock correlation defaults to the historical 12-month deviation correlation; the mortgage spread and the
        # tenor loadings stay calibrated (no market data for them). Same normals as the historical model.
        sv, rho = market_vol_monthly(cfg.vol, cfg.n_months), market_corr(cal, cfg.vol)
        x_s = np.cumsum(sv["short"] * z[:, :, 0], axis=1)
        x_l = np.cumsum(sv["long"] * (rho * z[:, :, 0] + np.sqrt(1 - rho ** 2) * z[:, :, 1]), axis=1)
        sp = np.zeros_like(x_s)
        pm = 0.0
        for t in range(cfg.n_months):
            pm = cal.phi_spread * pm + cal.sigma_spread * z[:, t, 2]
            sp[:, t] = pm
        return {"x_short": x_s, "x_long": x_l, "spread": sp}
    e_s = cal.sigma_short * z[:, :, 0]
    e_l = cal.sigma_long * (cal.rho * z[:, :, 0] + np.sqrt(1 - cal.rho ** 2) * z[:, :, 1])
    e_m = cal.sigma_spread * z[:, :, 2]
    x_s, x_l, sp = (np.zeros_like(e_s) for _ in range(3))
    ps = pl = pm = us = ul = 0.0
    for t in range(cfg.n_months):
        us = cal.mom_short * us + e_s[:, t]          # momentum: the shock itself persists (v1.2.0; 0 = AR(1))
        ul = cal.mom_long * ul + e_l[:, t]
        ps = cal.phi_short * ps + us
        pl = cal.phi_long * pl + ul
        pm = cal.phi_spread * pm + e_m[:, t]
        x_s[:, t], x_l[:, t], sp[:, t] = ps, pl, pm
    return {"x_short": x_s, "x_long": x_l, "spread": sp}


def simulate_paths(cal: Calibration, baseline: pd.DataFrame, cfg: Optional[SimConfig] = None) -> Dict[str, np.ndarray]:
    """
    Rate paths = baseline + deviation, floored. baseline: rows = months 1..n_months, columns 'short' (1m SOFR), 'y10',
    'mort' and optionally tenors with calibrated loadings (y1, y2, y5, y30). Returns {rate: (n_paths, n_months)} plus
    the unfloored deviations under keys prefixed 'dev_'.
    """
    cfg = cfg or SimConfig()
    if len(baseline) < cfg.n_months:
        raise ValueError("baseline is shorter than n_months")
    for c in ("short", "y10", "mort"):
        if c not in baseline.columns:
            raise ValueError(f"baseline is missing column '{c}'")
    b = baseline.iloc[:cfg.n_months]
    d = simulate_deviations(cal, cfg)
    dev = {"short": d["x_short"], "y10": d["x_long"], "mort": cal.beta_mort * d["x_long"] + d["spread"]}
    for tenor, (a_s, a_l) in cal.tenor_loadings.items():
        if tenor in b.columns:
            dev[tenor] = a_s * d["x_short"] + a_l * d["x_long"]
    out = {}
    for k, v in dev.items():
        out[k] = np.maximum(b[k].values[None, :] + v, cfg.floor)
        out["dev_" + k] = v
    return out


def fan_chart(paths: np.ndarray, pcts: Sequence[float] = (5, 25, 50, 75, 95)) -> pd.DataFrame:
    """Percentile bands by month for a fan chart of any simulated rate or income array."""
    df = pd.DataFrame(np.percentile(paths, pcts, axis=0).T, columns=[f"p{int(p)}" for p in pcts])
    df.index = pd.RangeIndex(1, paths.shape[1] + 1, name="month")
    return df


# --------------------------------------------------------------------------- #
# Validation: rate-only tests (V1-V4, V7). V5, V6 and V8 need income: mbs_lite/validation.py
# --------------------------------------------------------------------------- #
def rate_validation(cal: Calibration, hist: pd.DataFrame, baseline: pd.DataFrame, paths: Dict[str, np.ndarray],
                    cfg: SimConfig) -> Dict[str, list]:
    """V1 centering, V2 dispersion, V3 co-movement, V4 floor incidence, on the official paths."""
    n, T = paths["short"].shape
    b = baseline.iloc[:T]
    months = [m for m in (12, 24, 36, 48, 60) if m <= T]
    rep = {}

    rows = []
    for k in ("short", "y10", "mort"):
        for m in months:
            dev, flo = paths["dev_" + k][:, m - 1], paths[k][:, m - 1]
            se = dev.std(ddof=1) / np.sqrt(n)
            rows.append({"rate": k, "month": m, "mean_dev_unfloored_bp": dev.mean() * 1e4, "mc_se_bp": se * 1e4,
                         "floor_bias_bp": (flo.mean() - b[k].values[m - 1]) * 1e4 - dev.mean() * 1e4,
                         "pass": bool(abs(dev.mean()) <= 3 * se + 1e-12)})
    rep["centering"] = rows

    # V2: simulated vs analytic fan width (sampling), and the fan width against the calibration target, i.e. the
    # historical dispersion of deviations from the forward stored with the calibration (v1.2.0; raw changes before)
    w = resolve_window(hist, (cal.window_start, cal.window_end))
    tg = cal.diagnostics.get("targets") or {c: deviation_targets(w, None, c) for c in ("short", "y10", "mort")}
    tgt = {c: {r["horizon_m"]: r["sd_bp"] / 1e4 for r in rows_} for c, rows_ in tg.items()}
    rows = []
    for k in ("short", "y10", "mort"):
        for m in months:
            sim = paths["dev_" + k][:, m - 1].std(ddof=1)
            if k == "mort":
                ana = float(mortgage_fan_std(cal.beta_mort, factor_std(cal, cfg, "long", m), cal.phi_spread, cal.sigma_spread, m))
            else:
                ana = float(factor_std(cal, cfg, "short" if k == "short" else "long", m))
            emp = tgt.get(k, {}).get(m, np.nan)
            row = {"rate": k, "month": m, "simulated_bp": sim * 1e4, "analytic_bp": ana * 1e4,
                   "sim_over_analytic": sim / ana, "target_bp": emp * 1e4,
                   "model_over_hist": ana / emp if emp == emp and emp > 0 else None,   # fan width vs history
                   "pass": bool(abs(sim / ana - 1) < 0.05)}
            if k == "mort":
                xl = paths["dev_y10"][:, m - 1]
                row["sample_corr_10y_spread"] = float(np.corrcoef(xl, paths["dev_mort"][:, m - 1] - cal.beta_mort * xl)[0, 1])
            rows.append(row)
    rep["dispersion"] = rows

    # V3: the 12-month deviation correlation (from today) against the calibration target
    k = CORR_HORIZON
    xs, xl, xm = paths["dev_short"][:, k - 1], paths["dev_y10"][:, k - 1], paths["dev_mort"][:, k - 1]
    sim_corr = float(np.corrcoef(xs, xl)[0, 1])
    hist_corr = cal.diagnostics.get("corr_12m_target", cal.diagnostics.get("corr_12m_changes"))
    ds = paths["dev_short"][:, k:] - paths["dev_short"][:, :-k]
    dl = paths["dev_y10"][:, k:] - paths["dev_y10"][:, :-k]
    rep["correlation"] = [{
        "sim_corr_12m": sim_corr, "hist_corr_12m": hist_corr,
        "sim_corr_12m_changes_within_path": float(np.corrcoef(ds.ravel(), dl.ravel())[0, 1]),
        "sim_inversion_freq": float(np.mean(paths["y10"] < paths["short"])),
        "baseline_inversion_freq": float(np.mean(b["y10"].values < b["short"].values)),
        "hist_inversion_freq": cal.diagnostics["hist_inversion_freq"],
        "sim_corr_mort_y10_12m": float(np.corrcoef(xm, xl)[0, 1]),
        "hist_corr_mort_y10_12m": cal.diagnostics.get("mort_corr_12m_target", cal.diagnostics.get("mort_corr_12m_changes")),
        "pass": bool(abs(sim_corr - hist_corr) <= 0.10)}]

    rep["floor"] = [{"rate": r, "share_path_months_at_floor": float(np.mean(paths[r] <= cfg.floor + 1e-12)),
                     "share_paths_touching_floor": float(np.mean((paths[r] <= cfg.floor + 1e-12).any(axis=1)))}
                    for r in ("short", "y10", "mort")]
    return rep


def _simulate_factor(rng, phi: float, mom: float, sig: float, n: int, burn: int = 240) -> tuple:
    """One factor history (x, u) of length n, started from its stationary distribution (approximately, via burn-in)."""
    e = sig * rng.standard_normal(n + burn)
    x, u = np.empty(n + burn), np.empty(n + burn)
    a = b = 0.0
    for t in range(n + burn):
        b = mom * b + e[t]
        a = phi * a + b
        x[t], u[t] = a, b
    return x[burn:], u[burn:]


def _forecast_deviation_targets(x: np.ndarray, u: np.ndarray, phi: float, mom: float,
                                horizons: Sequence[int] = CALIBRATION_HORIZONS) -> list:
    """Model analogue of the forward-deviation target: x(t+h) minus its conditional expectation at t."""
    rows = []
    for h in horizons:
        xf, uf = x.copy(), u.copy()
        for _ in range(h):
            uf = mom * uf
            xf = phi * xf + uf
        d = x[h:] - xf[:-h]
        rows.append({"horizon_m": int(h), "sd_bp": float(np.std(d, ddof=1) * 1e4), "n": int(len(d))})
    return rows


def parameter_uncertainty(cal: Calibration, n_boot: int = 200, seed: int = 11,
                          months: Sequence[int] = (12, 60)) -> list:
    """
    V7: parametric bootstrap of estimation error. Simulates n_boot histories of the calibration's length from the
    fitted factor processes (with momentum), measures each history's deviations from its own conditional expectation
    (the model analogue of deviations from the forward), re-fits with the production estimator, and reports the spread
    of the implied fan width relative to its bootstrap mean, plus the mean ratio to the generating parameters
    (about 1: the estimator is unbiased for the fan width when forwards are unbiased) and the spread of the momentum
    estimate. This is calibration sampling error, which more Monte Carlo paths cannot reduce.
    """
    rng = np.random.default_rng(seed)
    rows = []
    for k, phi, mom, sig in (("short", cal.phi_short, cal.mom_short, cal.sigma_short),
                             ("y10", cal.phi_long, cal.mom_long, cal.sigma_long)):
        ratios, moms = {m: [] for m in months}, []
        true = {m: float(factor_conditional_std(phi, mom, sig, m)) for m in months}
        for _ in range(n_boot):
            x, u = _simulate_factor(rng, phi, mom, sig, cal.n_obs)
            f = fit_factor_horizon(_forecast_deviation_targets(x, u, phi, mom))
            moms.append(f["mom"])
            for m in months:
                ratios[m].append(float(factor_conditional_std(f["phi"], f["mom"], f["sigma"], m)) / true[m])
        for m in months:
            r = np.array(ratios[m])
            rel = r / r.mean()
            rows.append({"rate": k, "month": m, "fitted_dispersion_bp": true[m] * 1e4,
                         "relative_sd": float(rel.std(ddof=1)), "p05_relative": float(np.quantile(rel, 0.05)),
                         "p95_relative": float(np.quantile(rel, 0.95)), "mean_ratio_to_generator": float(r.mean()),
                         "mom": float(mom), "mom_p05": float(np.quantile(moms, 0.05)), "mom_p95": float(np.quantile(moms, 0.95))})
    return rows


# --------------------------------------------------------------------------- #
# Synthetic history (offline testing only)
# --------------------------------------------------------------------------- #
def synthetic_history(start: str = "1976-01", end: str = "2026-10", seed: int = 1, true: Optional[dict] = None) -> pd.DataFrame:
    """Simulated history from known parameters, for unit tests and the estimator study. NOT real data."""
    p = {"phi_s": 0.985, "sig_s": 0.0022, "phi_l": 0.990, "sig_l": 0.0026, "rho": 0.55,
         "mu_s": 0.035, "mu_l": 0.050, "beta": 0.85, "spr": 0.022, "phi_m": 0.93, "sig_m": 0.0012}
    p.update(true or {})
    idx = pd.period_range(start, end, freq="M")
    rng = np.random.default_rng(seed)
    n = len(idx)
    z = rng.standard_normal((n, 3))
    xs, xl, sp = np.zeros(n), np.zeros(n), np.zeros(n)
    a = rng.standard_normal(2)
    xs[0] = a[0] * p["sig_s"] / np.sqrt(1 - p["phi_s"] ** 2)
    xl[0] = a[1] * p["sig_l"] / np.sqrt(1 - p["phi_l"] ** 2)
    for t in range(1, n):
        es = p["sig_s"] * z[t, 0]
        el = p["sig_l"] * (p["rho"] * z[t, 0] + np.sqrt(1 - p["rho"] ** 2) * z[t, 1])
        xs[t] = p["phi_s"] * xs[t - 1] + es
        xl[t] = p["phi_l"] * xl[t - 1] + el
        sp[t] = p["phi_m"] * sp[t - 1] + p["sig_m"] * z[t, 2]
    y10 = p["mu_l"] + xl
    short = p["mu_s"] + xs
    return pd.DataFrame({"short": short, "y10": y10, "mort": p["spr"] + p["beta"] * y10 + sp,
                         "y1": 0.75 * short + 0.25 * y10, "y2": 0.55 * short + 0.45 * y10}, index=idx)


def synthetic_forwards(hist: pd.DataFrame, true: Optional[dict] = None, horizons: Sequence[int] = TARGET_HORIZONS) -> pd.DataFrame:
    """Forward changes for a synthetic_history: the generator's own conditional expectation (unbiased forwards)."""
    p = {"phi_s": 0.985, "phi_l": 0.990, "mu_s": 0.035, "mu_l": 0.050}
    p.update(true or {})
    xs, xl = hist["short"] - p["mu_s"], hist["y10"] - p["mu_l"]
    cols = {}
    for h in horizons:
        es, el = (p["phi_s"] ** h - 1) * xs, (p["phi_l"] ** h - 1) * xl
        cols[("short", h)], cols[("y10", h)] = es, el
        cols[("y1", h)], cols[("y2", h)] = 0.75 * es + 0.25 * el, 0.55 * es + 0.45 * el
    out = pd.DataFrame(cols)
    out.columns = pd.MultiIndex.from_tuples(out.columns)
    return out
