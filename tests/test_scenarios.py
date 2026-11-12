"""Rate-specific scenarios (ratemodel/scenarios.py, mbs_lite/scenarios.py): run with `python -m pytest tests`."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mbs_lite import scenarios as scen               # noqa: E402
from mbs_lite.engine import project                  # noqa: E402
from mbs_lite.prepay import SCurvePrepay             # noqa: E402
from mbs_lite.rates import SERIES, RateSet           # noqa: E402
from ratemodel import scenarios as rsc               # noqa: E402
from ratemodel.model import SimConfig                # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BETA = 0.9
LOAD = {"y1": (0.5, 0.6), "y2": (0.3, 0.85), "y5": (0.1, 1.0), "y30": (0.0, 0.8)}
SPOT = {"SOFR_1M": 4.0, "TSY_1Y": 3.9, "TSY_2Y": 3.8, "TSY_5Y": 4.0, "TSY_10Y": 4.3, "TSY_30Y": 4.8, "MTG_30Y": 6.3}
SPEC = {"parallel_bp": [100, 200, 400], "ramp_months": 12, "include_flat": True,
        "shapes": [{"id": "BEAR_STEEP", "name": "Bear steepener", "short_bp": 50, "long_bp": 200}],
        "overlays": {"spread_bp": [0, 25], "prepay_mult": [1.0, 1.25], "spread_ramp_months": 3}}


def _rates(H=36, sofr_drift=0.0):
    base = np.tile([SPOT[s] for s in SERIES], (H + 1, 1)).astype(float)
    base[:, SERIES.index("SOFR_1M")] += sofr_drift * np.arange(H + 1)
    dates = pd.date_range("2026-09-30", periods=H + 1, freq="ME")
    cal = SimpleNamespace(beta_mort=BETA, tenor_loadings=LOAD)
    return RateSet(dates[0], dates, base[None].copy(), base, np.full(3, SPOT["MTG_30Y"]), pd.DataFrame(), {},
                   {"official": "t"}, {"t": cal}, {}, SimConfig(n_months=H, floor=0.0), True, base)


def _book():
    return pd.DataFrame({"cusip": ["A", "B", "C"], "product": ["30Y", "15Y", "30Y"], "coupon": [5.5, 4.5, 6.0],
                         "wac": [6.4, 5.3, 6.9], "wala": [12, 30, 6], "wam": [348, 150, 354],
                         "current_upb": [1e8, 5e7, 8e7], "avg_loan_size": [3e5, 3e5, 3e5],
                         "story_group": ["Generic"] * 3, "burnout_years": [0.0] * 3, "vintage": [2024, 2022, 2025]})


def _prepay(mult=1.0):
    return SCurvePrepay.from_file(ROOT / "config" / "prepay_params.yaml", mult)


def _path(cat, cube, sid):
    return cube[[c["id"] for c in cat].index(sid)]


def test_parallel_moves_treasuries_and_sofr_fully_and_mortgage_by_beta():
    r = _rates()
    cat, cube = r.scenario_cube(SPEC)
    up, base = _path(cat, cube, "PAR_UP100"), _path(cat, cube, "BASE")
    d = up[1:] - base[1:]
    for s in SERIES[:-1]:
        assert np.allclose(d[:, SERIES.index(s)], 1.0)
    assert np.allclose(d[:, SERIES.index("MTG_30Y")], BETA)
    assert np.allclose(up[0], base[0])                       # month 0 = today's observed rates on every path
    assert np.allclose(r.parallel_cube(100)[0], up)          # V6 uses the same shock


def test_shape_follows_factor_loadings_and_ramp_and_floor():
    r = _rates()
    cat, cube = r.scenario_cube(SPEC)
    d = _path(cat, cube, "BEAR_STEEP")[1:] - _path(cat, cube, "BASE")[1:]
    assert np.allclose(d[:, SERIES.index("SOFR_1M")], 0.5) and np.allclose(d[:, SERIES.index("TSY_10Y")], 2.0)
    assert np.allclose(d[:, SERIES.index("TSY_2Y")], 0.3 * 0.5 + 0.85 * 2.0)
    assert np.allclose(d[:, SERIES.index("MTG_30Y")], BETA * 2.0)
    ramp = _path(cat, cube, "PAR_UP200_R12")[1:] - _path(cat, cube, "BASE")[1:]
    assert np.isclose(ramp[5, SERIES.index("TSY_10Y")], 2.0 * 6 / 12) and np.allclose(ramp[11:, SERIES.index("TSY_10Y")], 2.0)
    dn = _path(cat, cube, "PAR_DN400")
    assert dn[1:, SERIES.index("SOFR_1M")].min() == 0.0      # 4.0% - 4.00 floored at 0
    flat = _path(cat, cube, "FLAT")
    assert np.allclose(flat, flat[0])


def test_base_scenario_is_the_baseline_and_attribution_adds_up():
    r = _rates(sofr_drift=-0.02)
    df = _book()
    codes = np.array([0, 1, 0])
    res = scen.run(df, r, _prepay(), {"reinvest_rate_series": "SOFR_1M"}, 36, SPEC, codes, ["30Y", "15Y"],
                   np.zeros((4, 36)), log=lambda *a: None)
    b = project(df, r.baseline_cube(), r, _prepay(), {"reinvest_rate_series": "SOFR_1M"}, 36)
    ids = [c["id"] for c in res["catalog"]]
    assert np.allclose(res["_proj"].agg["income"][ids.index("BASE")], b.agg["income"][0])
    base = np.array(res["annual"]["BASE"]["income"])
    for sid, p in res["attribution"].items():
        tot = np.array(res["annual"][sid]["income"]) - base
        assert np.allclose(sum(np.array(v) for v in p.values()), tot, atol=3.0)   # rounding to whole dollars
    up, dn = (np.sum(res["annual"][s]["income"]) - base.sum() for s in ("PAR_UP100", "PAR_DN100"))
    assert up > 0 > dn                                       # premium book, reinvesting at SOFR
    # cohorts add up to the portfolio
    assert np.allclose(np.sum(res["cohorts"]["income"]["PAR_DN200"], axis=0), res["annual"]["PAR_DN200"]["income"], atol=3.0)


def test_overlays_match_direct_runs():
    r = _rates()
    df, inc = _book(), {"reinvest_rate_series": "SOFR_1M"}
    res = scen.run(df, r, _prepay(), inc, 36, SPEC, np.zeros(3, int), ["All"], np.zeros((4, 36)), log=lambda *a: None)
    cat, cube = r.scenario_cube(SPEC)
    direct = project(df, cube, r, _prepay(1.25), inc, 36).agg["income"]
    got = res["overlays"]["grid"][scen.okey(0, 1.25)]
    for i, c in enumerate(cat):
        assert np.allclose(got[c["id"]]["income"], scen._annual(direct[i], 36), atol=1.0)
    sp = project(df, r.with_mortgage_spread(cube, 25, 3), r, _prepay(), inc, 36).agg["income"]
    assert np.allclose(res["overlays"]["grid"][scen.okey(25, 1.0)]["BASE"]["income"], scen._annual(sp[0], 36), atol=1.0)
    assert scen.okey(0, 1.0) not in res["overlays"]["grid"]  # no-overlay grid is the main run


def test_catalog_order_and_ids():
    ids = [c["id"] for c in rsc.catalog(SPEC)]
    assert ids[:2] == ["BASE", "FLAT"] and "PAR_UP400" in ids and "PAR_DN100_R12" in ids and ids[-1] == "BEAR_STEEP"
