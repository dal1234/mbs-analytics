"""Unit tests for the income engine and the risk measures: run with `python -m pytest tests`."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mbs_lite import history as hist             # noqa: E402
from mbs_lite.engine import project               # noqa: E402
from mbs_lite.portfolio import load_portfolio     # noqa: E402
from mbs_lite.prepay import SCurvePrepay          # noqa: E402
from mbs_lite.rates import SERIES, RateSet        # noqa: E402
from mbs_lite.risk import band, ear, outcomes, sums, tail_attribution  # noqa: E402
from ratemodel.model import SimConfig             # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _rates(H=36, sofr=4.0, mtg=7.0, paths=1, noise=0.0, seed=0):
    cube = np.zeros((paths, H + 1, len(SERIES)))
    rng = np.random.default_rng(seed)
    shift = np.concatenate([np.zeros((paths, 1)), np.cumsum(rng.normal(0, noise, (paths, H)), axis=1)], axis=1) if noise else 0.0
    cube[..., SERIES.index("SOFR_1M")] = np.maximum(sofr + shift, 0)
    cube[..., SERIES.index("MTG_30Y")] = mtg + shift
    base = cube[:1].mean(axis=0) * 0 + cube[0, 0]          # flat baseline at the starting rates
    dates = pd.date_range("2026-09-30", periods=H + 1, freq="ME")
    return RateSet(dates[0], dates, cube, base, np.full(3, mtg), pd.DataFrame(), {}, {}, {}, {}, SimConfig(n_months=H),
                   True, base)


def _book():
    return pd.DataFrame({"cusip": ["A", "B", "C"], "product": ["30Y", "15Y", "30Y"], "coupon": [5.5, 4.5, 6.0],
                         "wac": [6.4, 5.3, 6.9], "wala": [12, 30, 6], "wam": [348, 150, 354],
                         "current_upb": [1e8, 5e7, 8e7], "avg_loan_size": [3e5, 3e5, 3e5],
                         "story_group": ["Generic"] * 3, "burnout_years": [0.0] * 3})


def _prepay(mult=1.0):
    return SCurvePrepay.from_file(ROOT / "config" / "prepay_params.yaml", mult)


def test_balance_identity_and_reinvestment():
    r = _rates()
    d = project(_book(), r.mc, r, _prepay(), {"reinvest_rate_series": "SOFR_1M"}, 36, mean_detail=True).detail
    assert np.allclose(d["end_upb"], d["begin_upb"] - d["sched"] - d["prepaid"])
    assert np.allclose(d["begin_upb"][:, 1:], d["end_upb"][:, :-1])
    cum = np.concatenate([np.zeros((3, 1)), np.cumsum(d["prepaid"], axis=1)[:, :-1]], axis=1)
    assert np.allclose(d["reinv_bal"], cum)                       # reinvested = prepaid UPB to date
    assert np.allclose(d["reinv_income"], cum * 4.0 / 1200)       # earning 1M SOFR
    assert np.allclose(d["income"], d["interest"] + d["reinv_income"])
    assert np.allclose(d["interest"], d["begin_upb"] * np.array([[5.5], [4.5], [6.0]]) / 1200)


def test_no_prepay_amortizes_to_zero_at_maturity():
    book = _book().assign(wam=[24, 12, 36])
    r = _rates(H=36)
    d = project(book, r.mc, r, _prepay(mult=0.0), {}, 36, mean_detail=True).detail
    assert np.allclose(d["prepaid"], 0)
    assert abs(d["end_upb"][0, 23]) < 1e-6 and abs(d["end_upb"][1, 11]) < 1e-6 and abs(d["end_upb"][2, 35]) < 1e-6
    assert np.allclose(d["sched"].sum(axis=1), [1e8, 5e7, 8e7])


def test_faster_prepay_when_rates_fall():
    hi, lo = _rates(mtg=7.0), _rates(mtg=4.5)
    ph = project(_book(), hi.mc, hi, _prepay(), {}, 12)
    pl = project(_book(), lo.mc, lo, _prepay(), {}, 12)
    assert pl.agg["prepaid"].sum() > 3 * ph.agg["prepaid"].sum()


def test_paths_groups_and_chunks_agree():
    r = _rates(H=24, paths=40, noise=0.15, seed=3)
    codes = np.array([0, 1, 0])
    a = project(_book(), r.mc, r, _prepay(), {}, 24, groups=codes)
    b = project(_book(), r.mc, r, _prepay(), {}, 24, groups=codes, max_cells=7)   # 2 paths per block
    assert np.allclose(a.agg["income"], b.agg["income"]) and np.allclose(a.group["income"], b.group["income"])
    for y in (1, 2):                                       # cohorts add up to the portfolio, per path and year
        assert np.allclose(a.group["income"][:, :, y - 1].sum(axis=1), sums(a.agg["income"], "annual", y, 24))
    m = project(_book(), r.mc, r, _prepay(), {}, 24, mean_detail=True)
    assert np.allclose(m.detail["income"].sum(axis=0), a.agg["income"].mean(axis=0))


def test_ear_against_baseline_adds_up():
    r = _rates(H=24, paths=60, noise=0.2, seed=5)
    codes = np.array([0, 1, 2])
    p = project(_book(), r.mc, r, _prepay(), {}, 24, groups=codes)
    b = project(_book(), r.baseline_cube(), r, _prepay(), {}, 24, groups=codes)
    e = ear(p, b, confs=(0.95, 0.99), es_confs=(0.95,), n_boot=200)
    for m in ("cumulative", "annual"):
        for row in e[m]:
            c = row["conf"]["95"]
            assert np.isclose(c["shortfall"], row["baseline"] - c["income"])
            assert c["ci_low"] <= c["income"] <= c["ci_high"]
            assert row["conf"]["99"]["income"] <= c["income"]
            assert row["es"]["95"]["shortfall"] >= c["shortfall"] - 1e-6
    assert e["cumulative"][0]["baseline"] == e["annual"][0]["baseline"]
    o = outcomes(p, b, r, [5, 50, 95], 10)
    for m in ("cumulative", "annual"):
        for h in o[m]:
            for row in [*h["rows"].values(), h["baseline"]]:
                assert np.allclose(np.add(row["interest"], row["reinv"]), row["income"], atol=0.02)
            assert h["rows"]["5"]["total"] < h["rows"]["50"]["total"] < h["rows"]["95"]["total"]
    t = tail_attribution(_book(), codes, ["A", "B", "C"], p, b, 5)
    for m in ("cumulative", "annual"):
        for x in t[m]:
            assert abs(sum(rw["delta"] for rw in x["rows"]) + x["shortfall"]) < 1e-3
    assert len(band(sums(p.agg["income"], "cumulative", 1, 24), 50, 10)) == 6


def test_history_replaces_same_date(tmp_path):
    f = tmp_path / "h.csv"
    r = {"years": 1, "baseline": 1.0, "mean": 1.0, "mean_minus_baseline_pct": 0.0, "cpr": {"mean": 5.0, "baseline": 5.0},
         "pct": {str(k): 1.0 for k in (1, 5, 10, 25, 50, 75, 90, 95, 99)},
         "conf": {"95": {"income": 0.9, "shortfall": 0.1, "shortfall_pct": 0.1, "ci_low": 0.8, "ci_high": 0.95}}}
    t = {"conf": {"99": {"shortfall": 0.2, "shortfall_pct": 0.2}}, "es": {"95": {"shortfall": 0.15, "shortfall_pct": 0.15}}}
    off, tail = {"cumulative": [r], "annual": [r]}, {"cumulative": [t], "annual": [t]}
    meta = lambda d, s: {"as_of": d, "source": s}  # noqa: E731
    hist.append(f, hist.rows(meta("2026-08-31", "mock"), off, tail))
    hist.append(f, hist.rows(meta("2026-09-30", "run"), off, tail))
    hist.append(f, hist.rows(meta("2026-08-31", "run"), off, tail))
    h = pd.read_csv(f)
    assert len(h) == 4 and set(h["source"]) == {"run"}
    assert len(hist.window(f, "2026-09-30", 1)) == 2


def test_sample_portfolio_is_valid():
    pf = load_portfolio(ROOT / "data" / "portfolio" / "sample_portfolio.csv")
    assert not pf.errors, pf.errors
    assert pf.n > 0


def test_validation_reports_bad_rows(tmp_path):
    f = tmp_path / "bad.csv"
    f.write_text("cusip,as_of_date,product,coupon,wac,wala,wam,current_upb\n"
                 "X1,2026-09-30,30Y,5.5,6.4,12,348,1000000\n"
                 "X2,2026-09-30,40Y,abc,6.4,12,348,1000000\n"
                 "X3,2026-10-31,30Y,5.5,6.4,12,,1000000\n")
    errs = " | ".join(load_portfolio(f).errors)
    assert "coupon" in errs and "product" in errs and "wam" in errs and "different dates" in errs


def test_config_extends_and_vendor_preset():
    """config/model_vendor.yaml inherits model.yaml and only redirects folders; rates_vendor.yaml likewise."""
    from mbs_lite.config import load_config
    a, b = load_config(ROOT / "config" / "model.yaml"), load_config(ROOT / "config" / "model_vendor.yaml")
    assert b["income"] == a["income"] and b["prepay"] == a["prepay"] and b["risk"] == a["risk"]
    for k in ("rates", "outputs", "dashboard"):
        assert b[k]["dir"] != a[k]["dir"]
    assert b["history"]["file"] != a["history"]["file"] and b["validation"]["file"] != a["validation"]["file"]
    sys.path.insert(0, str(ROOT))
    import run_rates
    ra, rb = run_rates.load_cfg(ROOT / "config" / "rates.yaml"), run_rates.load_cfg(ROOT / "config" / "rates_vendor.yaml")
    assert run_rates.vol_spec(ra) is None and run_rates.vol_spec(rb)["sofr_bp"][0] > 0
    assert rb["baseline"]["type"] == "swap" and rb["output"]["dir"] != ra["output"]["dir"]
    assert rb["calibration"] == ra["calibration"] and rb["simulation"] == ra["simulation"]
