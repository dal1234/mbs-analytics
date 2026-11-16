"""Whole loans (illustrative), mbs_lite/wholeloans.py: run with `python -m pytest tests`."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mbs_lite import wholeloans as wl            # noqa: E402
from mbs_lite.prepay import SCurvePrepay         # noqa: E402
from mbs_lite.rates import SERIES, RateSet       # noqa: E402
from ratemodel.model import SimConfig            # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BEH = {"performing": {"roll_cd": 0.01, "cure_dc": 0.5, "roll_ds": 0.1, "cure_sc": 0.1, "liq_s": 0.05, "prepay_mult": 1.0}}
CFG = {"behavior": BEH, "severity": {"liquidation_cost": 0.25, "min_severity": 0.05},
       "macro": {"hpi_growth_pct": 0.0, "unemployment_pct": 4.0}}


def _rates(H=24, sofr=4.0, mtg=6.5, shift=0.0):
    base = np.zeros((H + 1, len(SERIES)))
    base[:, SERIES.index("SOFR_1M")], base[:, SERIES.index("MTG_30Y")] = sofr, mtg
    cube = base[None].copy()
    cube[:, 1:, :] += shift
    dates = pd.date_range("2026-09-30", periods=H + 1, freq="ME")
    return RateSet(dates[0], dates, cube, base, np.full(3, mtg), pd.DataFrame(), {}, {}, {}, {}, SimConfig(n_months=H), True, base), cube


def _df(**kw):
    row = {"id": "A", "behavior": "performing", "status": "current", "upb": 100.0, "note_rate": 6.0, "servicing_bp": 25,
           "deferred_share": 0.0, "product": "30Y", "wala": 12, "wam": 348, "ltv": 80, "fico": 700, "avg_loan": 300000}
    row.update(kw)
    return pd.DataFrame([row])


def _prepay():
    return SCurvePrepay.from_file(ROOT / "config" / "prepay_params.yaml")


def test_conservation_and_first_month():
    r, cube = _rates()
    o = wl.project(_df(deferred_share=0.1), cube, r, _prepay(), {}, 24, CFG)
    assert o["interest"][0, 0, 0] == pytest.approx(100e6 * 0.9 * (6.0 - 0.25) / 1200)   # deferred part earns nothing
    loans = (o["bal_c"] + o["bal_d"] + o["bal_s"]).sum(axis=2)
    cum = np.concatenate([[[0.0]], np.cumsum(o["loss"].sum(axis=2), axis=1)[:, :-1]], axis=1)
    assert np.allclose(loans + o["cash_bal"] + cum, 100e6)
    assert o["cash_income"][0, 1] == pytest.approx(o["cash_bal"][0, 1] * 4.0 / 1200)    # runoff cash earns SOFR


def test_nonaccrual_and_severity():
    r, cube = _rates()
    o = wl.project(_df(status="sdq", ltv=100), cube, r, _prepay(), {}, 24, CFG)
    assert o["interest"][0, 0, 0] == 0.0                                                  # 90+ accrues nothing
    liq = o["liquidated"][0, 0, 0]
    assert liq == pytest.approx(100e6 * 0.05)
    assert o["loss"][0, 0, 0] == pytest.approx(liq * (1 - 0.75 / 1.0))                     # 25% severity at LTV 100
    low = wl.project(_df(status="sdq", ltv=40), cube, r, _prepay(), {}, 24, CFG)
    assert low["loss"][0, 0, 0] == pytest.approx(low["liquidated"][0, 0, 0] * 0.05)       # floor at low LTV


def test_losses_do_not_depend_on_rates_but_prepayments_do():
    r, cube = _rates()
    _, dn = _rates(shift=-2.0)
    a = wl.project(_df(), cube, r, _prepay(), {}, 24, CFG)
    b = wl.project(_df(), dn, r, _prepay(), {}, 24, CFG)
    assert b["prepaid"].sum() > a["prepaid"].sum()                                         # rally: faster prepayment
    rate_of_loss = lambda o: o["loss"].sum() / (o["bal_c"] + o["bal_d"] + o["bal_s"]).sum()  # noqa: E731
    assert rate_of_loss(a) == pytest.approx(rate_of_loss(b), rel=0.2)                      # same credit baseline


def test_higher_unemployment_raises_losses_and_house_prices_lower_them():
    r, cube = _rates()
    base = wl.project(_df(ltv=95), cube, r, _prepay(), {}, 24, CFG)["loss"].sum()
    hi_u = wl.project(_df(ltv=95), cube, r, _prepay(), {}, 24, {**CFG, "macro": {"hpi_growth_pct": 0.0, "unemployment_pct": [4.0, 6.0]}})["loss"].sum()
    hpi = wl.project(_df(ltv=95), cube, r, _prepay(), {}, 24, {**CFG, "macro": {"hpi_growth_pct": 10.0, "unemployment_pct": 4.0}})["loss"].sum()
    assert hi_u > base > hpi


def test_sample_file_loads():
    beh = {k: {} for k in ("performing", "reperforming", "modified", "delinquent_buyout")}
    df = wl.load_cohorts(ROOT / "data" / "whole_loans" / "sample_loans.csv", beh)
    assert set(df["status"]) <= set(wl.STATES) and df["upb"].sum() == pytest.approx(56300)
    with pytest.raises(wl.LoanFileError):
        wl.load_cohorts(ROOT / "data" / "whole_loans" / "sample_loans.csv", {"performing": {}})
