"""Tests for the rate model (ratemodel): estimation on synthetic history, simulation properties, baseline, storage."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ratemodel import baseline as bl, data, store  # noqa: E402
from ratemodel import model as rm  # noqa: E402
from ratemodel.curve import bootstrap, par_yield  # noqa: E402


@pytest.fixture(scope="module")
def synth():
    return rm.synthetic_history("1976-01", "2026-09", seed=4)


@pytest.fixture(scope="module")
def sfwd(synth):
    return rm.synthetic_forwards(synth)


def _flat(n=60):
    return pd.DataFrame({"short": 0.04, "y10": 0.045, "mort": 0.065, "y2": 0.042}, index=pd.RangeIndex(1, n + 1, name="month"))


def test_calibration_recovers_known_parameters(synth, sfwd):
    """v1.2.0: with unbiased forwards the targets are deviations from the conditional expectation, so the fitted fan
    width recovers the generator's (plain AR(1): momentum near zero)."""
    c = rm.calibrate(synth, "base", fwd=sfwd)
    assert c.window_start == "1990-01" and c.n_obs == len(synth.loc["1990-01":])
    assert c.diagnostics["target"] == "forward_deviation"
    assert abs(c.beta_mort - 0.85) < 0.08
    # rho is solved so the model's 12-month deviation correlation equals history's
    ps, pl = rm.psi_weights(c.phi_short, c.mom_short, 12), rm.psi_weights(c.phi_long, c.mom_long, 12)
    model_c12 = c.rho * np.sum(ps * pl) / np.sqrt(np.sum(ps ** 2) * np.sum(pl ** 2))
    assert model_c12 == pytest.approx(c.diagnostics["corr_12m_target"], abs=1e-9)
    for k, tphi, tsig in (("short", 0.985, 0.0022), ("long", 0.990, 0.0026)):
        for m, lo, hi in ((12, 0.8, 1.25), (60, 0.5, 1.5)):   # one history: 60m has ~7 independent windows (V7)
            ratio = c.factor_std(k, m) / rm.ar1_conditional_std(tphi, tsig, m)
            assert lo < ratio < hi
    assert abs(c.mom_short) < 0.5 and abs(c.mom_long) < 0.5
    assert set(c.tenor_loadings) == {"y1", "y2"}
    assert c.tenor_loadings["y2"] == pytest.approx((0.55, 0.45), abs=0.05)


def test_momentum_process():
    """psi weights: zero momentum is the AR(1); the analytic fan width matches a direct simulation."""
    assert rm.factor_conditional_std(0.98, 0.0, 0.002, [1, 12, 60]) == pytest.approx(rm.ar1_conditional_std(0.98, 0.002, np.array([1, 12, 60])))
    c = rm.Calibration("t", rm.MODEL_VERSION, "2000-01", "2020-01", 240, 0.978, 0.0013, 0.98, 0.0025, 0.6, 0.9, 0.95, 0.001,
                       mom_short=0.78, mom_long=0.2)
    p = rm.simulate_paths(c, _flat(), rm.SimConfig(n_paths=4000, floor=-1.0))
    for m in (3, 12, 60):
        assert p["dev_short"][:, m - 1].std(ddof=1) / c.factor_std("short", m) == pytest.approx(1, abs=0.04)
    ps, pl = rm.psi_weights(0.978, 0.78, 12), rm.psi_weights(0.98, 0.2, 12)
    assert np.corrcoef(p["dev_short"][:, 11], p["dev_y10"][:, 11])[0, 1] == pytest.approx(
        0.6 * np.sum(ps * pl) / np.sqrt(np.sum(ps ** 2) * np.sum(pl ** 2)), abs=0.03)


def test_named_windows(synth):
    assert rm.calibrate(synth, "recent").n_obs == 120
    assert rm.calibrate(synth, "high_vol").window_start == "1976-01"
    with pytest.raises(ValueError):
        rm.calibrate(synth.iloc[-50:], "base")


def test_simulation_centred_and_matches_analytic_dispersion(synth, sfwd):
    c = rm.calibrate(synth, "base", fwd=sfwd)
    cfg = rm.SimConfig(n_paths=2000, n_months=60, floor=-1.0)
    p = rm.simulate_paths(c, _flat(), cfg)
    assert np.abs(p["dev_short"].mean(axis=0)).max() < 1e-12          # antithetic: exactly zero mean
    for m in (12, 60):
        sim = p["dev_y10"][:, m - 1].std(ddof=1)
        assert abs(sim / c.factor_std("long", m) - 1) < 0.05
    assert np.allclose(p["y2"], 0.042 + p["dev_y2"])                     # loaded tenor, no floor binding
    v = rm.rate_validation(c, synth, _flat(), p, cfg)
    assert all(r["pass"] for r in v["centering"]) and all(r["pass"] for r in v["dispersion"])


def test_simulation_is_reproducible_and_floored(synth):
    c = rm.calibrate(synth, "base")
    a = rm.simulate_paths(c, _flat(), rm.SimConfig(n_paths=100))
    b = rm.simulate_paths(c, _flat(), rm.SimConfig(n_paths=100))
    assert np.array_equal(a["mort"], b["mort"])
    assert a["short"].min() >= 0.0


def test_store_roundtrip(synth, sfwd, tmp_path):
    payload = store.estimate(synth, sfwd, log=lambda *x: None)
    store.save(payload, tmp_path / "cal.json", tmp_path / "archive")
    meta, cals = store.load(tmp_path / "cal.json")
    assert meta["calibration_id"] == payload["calibration_id"] and set(cals) == {"base", "recent", "high_vol"}
    assert len(list((tmp_path / "archive").iterdir())) == 1
    assert cals["base"].phi_short == pytest.approx(payload["calibrations"][0]["phi_short"])
    assert len(meta["parameter_uncertainty"]) == 4 and meta["target"] == "forward_deviation"
    for r in meta["parameter_uncertainty"]:          # unbiased forwards: the estimator recovers the generator's fan width
        assert 0.85 < r["mean_ratio_to_generator"] < 1.15 and r["relative_sd"] < 0.4


def test_curve_bootstrap_roundtrip():
    par = {"1M": 4.0, "3M": 4.1, "6M": 4.2, "1Y": 4.3, "2Y": 4.4, "3Y": 4.5, "5Y": 4.6, "7Y": 4.7, "10Y": 4.8, "30Y": 5.0}
    dc = bootstrap(par)
    for t, T in (("2Y", 2.0), ("10Y", 10.0), ("30Y", 30.0)):
        assert abs(par_yield(dc, 0.0, T) - par[t]) < 1e-8


@pytest.mark.skipif(not (ROOT / "data" / "fred" / "DGS10.csv").exists(), reason="no cached FRED data")
def test_history_and_baseline_from_cache():
    h = data.load_history(ROOT / "data" / "fred", "2026-09-30")
    assert str(h.index[-1]) == "2026-09" and {"short", "y10", "mort", "y2"} <= set(h.columns)
    assert h.loc["2015-06", "short"] < 0.005 and h.loc["2023-06", "short"] > 0.04     # EFFR splice and SOFR era
    s = data.load_spot(ROOT / "data" / "fred", "2026-09-30")
    for kind in ("forward", "flat"):
        b = bl.build_baseline(kind, s, 60)
        assert len(b) == 61 and b.loc[0, "y10"] == pytest.approx(s.curve["10Y"] / 100)
        assert b.loc[0, "short"] == pytest.approx(s.sofr_1m / 100)
    assert data.load_history(ROOT / "data" / "fred", "2015-12-31").index[-1] == pd.Period("2015-12", "M")


def test_mortgage_fan_matches_history(synth, sfwd):
    """beta from 12-month deviations; the spread is fitted so the mortgage fan matches mortgage deviation dispersion."""
    c = rm.calibrate(synth, "base", fwd=sfwd)
    w = synth.loc["1990-01":]
    l, m = w["y10"], w["mort"]
    dl = (l.shift(-12) - l - sfwd[("y10", 12)].reindex(w.index)).dropna()
    dm = (m.shift(-12) - m - sfwd[("y10", 12)].reindex(w.index)).dropna()
    assert c.beta_mort == pytest.approx(float(np.cov(dl, dm)[0, 1] / np.var(dl, ddof=1)))
    fit = c.diagnostics["mort_fit"]["fit"]
    assert [r["horizon_m"] for r in fit] == [3, 12, 24, 36, 60]
    assert all(0.85 < r["ratio"] < 1.15 for r in fit)
    cfg = rm.SimConfig(n_paths=2000, n_months=60, floor=-1.0)
    p = rm.simulate_paths(c, _flat(), cfg)
    v = rm.rate_validation(c, synth, _flat(), p, cfg)
    mort = [r for r in v["dispersion"] if r["rate"] == "mort"]
    assert len(mort) == 5 and all(r["pass"] for r in mort)
    assert v["correlation"][0]["pass"] and v["correlation"][0]["hist_corr_mort_y10_12m"] is not None


@pytest.mark.skipif(not (ROOT / "data" / "fred" / "DGS10.csv").exists(), reason="no cached FRED data")
def test_forward_deviation_calibration_from_cache():
    """Real data: monthly forward curves build for the whole history, and the official fit matches the targets."""
    from ratemodel import forwards
    fwd = forwards.forward_changes(forwards.monthly_curves(ROOT / "data" / "fred", "2026-09-30"))
    assert str(fwd.index[-1]) == "2026-09" and fwd.loc["1990-01":].notna().all().all()
    h = data.load_history(ROOT / "data" / "fred", "2026-09-30")
    c = rm.calibrate(h, "base", fwd=fwd)
    for k in ("short_fit", "long_fit", "mort_fit"):
        assert all(0.8 < r["ratio"] < 1.2 for r in c.diagnostics[k]["fit"])
    assert 0 < c.mom_short < 0.95 and not c.diagnostics["short_fit"]["phi_at_bound"]


def test_market_volatility_width_and_correlation(synth, sfwd):
    """Comparison width: random walk with yearly normal vols; analytic st.dev. matches simulation; V2/V3 pass."""
    c = rm.calibrate(synth, "base", fwd=sfwd)
    vol = {"type": "market", "sofr_bp": [61, 90, 90, 90, 90], "y10_bp": [90] * 5, "corr": None}
    cfg = rm.SimConfig(n_paths=4000, floor=-1.0, vol=vol)
    p = rm.simulate_paths(c, _flat(), cfg)
    assert rm.market_std(vol, "short", 12) == pytest.approx(0.0061)
    assert rm.market_std(vol, "short", 60) == pytest.approx(np.sqrt(0.0061 ** 2 + 4 * 0.009 ** 2))
    for m in (12, 60):
        assert p["dev_short"][:, m - 1].std(ddof=1) / rm.market_std(vol, "short", m) == pytest.approx(1, abs=0.03)
    assert np.corrcoef(p["dev_short"][:, 11], p["dev_y10"][:, 11])[0, 1] == pytest.approx(c.diagnostics["corr_12m_target"], abs=0.03)
    v = rm.rate_validation(c, synth, _flat(), p, cfg)
    assert all(r["pass"] for r in v["dispersion"]) and v["correlation"][0]["pass"]
    hist = rm.simulate_paths(c, _flat(), rm.SimConfig(n_paths=4000, floor=-1.0))
    assert not np.allclose(hist["dev_short"], p["dev_short"]) and np.allclose(hist["dev_mort"] - c.beta_mort * hist["dev_y10"],
                                                                                 p["dev_mort"] - c.beta_mort * p["dev_y10"])


@pytest.mark.skipif(not (ROOT / "data" / "fred" / "DGS10.csv").exists(), reason="no cached FRED data")
def test_swap_baseline_estimated_and_from_file(tmp_path):
    s = data.load_spot(ROOT / "data" / "fred", "2026-07-31")
    est = bl.build_baseline("swap", s, 60)
    fwd = bl.build_baseline("forward", s, 60)
    assert est.loc[0, "short"] == pytest.approx(s.sofr_1m / 100) and est.loc[0, "mort"] == pytest.approx(s.mort / 100)
    assert np.allclose(est[["y1", "y2", "y5", "y10", "y30"]], fwd[["y1", "y2", "y5", "y10", "y30"]])   # Treasuries stay
    assert est.loc[60, "short"] < fwd.loc[60, "short"] and est.loc[60, "mort"] < fwd.loc[60, "mort"]   # negative spreads flatten
    # every spread = today's SOFR-bill basis: a parallel shift of the Treasury curve, so SOFR and the mortgage rate
    # follow the Treasury forward baseline exactly (that baseline applies the same basis to SOFR)
    b = 100 * (s.sofr_1m - s.curve["1M"])
    par = bl.build_baseline("swap", s, 60, swap={"spreads_bp": {k: b for k in bl.DEFAULT_SWAP_SPREADS_BP}})
    assert par["short"].sub(fwd["short"]).abs().max() < 1e-4 and par["mort"].sub(fwd["mort"]).abs().max() < 1e-4   # within 1 bp (compounding)
    f = tmp_path / "swap.csv"
    sc, _ = bl.swap_curve(s)
    pd.DataFrame({"tenor": list(sc), "rate": list(sc.values())}).to_csv(f, index=False)
    fromfile = bl.build_baseline("swap", s, 60, swap={"curve_file": str(f)})
    assert np.allclose(fromfile, est)
    with pytest.raises(ValueError):
        pd.DataFrame({"tenor": ["2Y"], "rate": [4.0]}).to_csv(f, index=False)
        bl.read_swap_curve(str(f))
