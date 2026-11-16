"""Funding (liability) model, mbs_lite/funding.py: run with `python -m pytest tests`."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mbs_lite import funding as fund      # noqa: E402
from mbs_lite.rates import SERIES         # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
AS_OF = pd.Timestamp("2026-09-30")
CURVE = {"SOFR_1M": 4.0, "TSY_1Y": 4.0, "TSY_2Y": 4.0, "TSY_5Y": 4.0, "TSY_10Y": 4.0, "TSY_30Y": 4.0, "MTG_30Y": 6.0}


def _cube(H=24, shift=0.0, paths=1):
    c = np.tile([CURVE[s] for s in SERIES], (paths, H + 1, 1)).astype(float)
    c[:, 1:, :] += shift
    return c


def _debt(tmp_path, rows):
    p = tmp_path / "debt.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    return fund.load_debt(p, AS_OF)


ROWS = [{"id": "DN", "type": "discount_note", "face": 100, "rate": 4.0, "spread_bp": None, "maturity": "2026-12", "first_call": None},
        {"id": "FX", "type": "fixed", "face": 200, "rate": 3.0, "spread_bp": None, "maturity": "2028-09", "first_call": None},
        {"id": "CL", "type": "callable", "face": 300, "rate": 5.0, "spread_bp": None, "maturity": "2030-09", "first_call": "2027-03"},
        {"id": "FL", "type": "floating", "face": 400, "rate": None, "spread_bp": 10, "maturity": "2027-06", "first_call": None}]
CFG = {"balance": "constant", "call_rule": {"threshold_bp": 25, "refi_spread_bp": 35}}


def test_first_month_interest_is_contractual(tmp_path):
    d = _debt(tmp_path, ROWS)
    out = fund.project(d, _cube(), 24, CFG)
    by = dict(zip(fund.TYPES, out["interest"][0, 0]))
    assert by["discount_note"] == pytest.approx(100e6 * 4.0 / 1200)
    assert by["fixed"] == pytest.approx(200e6 * 3.0 / 1200)
    assert by["callable"] == pytest.approx(300e6 * 5.0 / 1200)
    assert by["floating"] == pytest.approx(400e6 * 4.10 / 1200)        # SOFR at the start of the month + 10 bp


def test_constant_balance_and_roll_forward(tmp_path):
    d = _debt(tmp_path, ROWS)
    out = fund.project(d, _cube(shift=-1.0, paths=3), 24, CFG)
    bal = out["balance"].sum(axis=2)
    assert np.allclose(bal, 1000e6)
    flow = bal[:, :-1] - out["matured"].sum(axis=2)[:, :-1] - out["called"].sum(axis=2)[:, :-1] + out["issued"].sum(axis=2)[:, :-1]
    assert np.allclose(flow, bal[:, 1:])
    # mix is restored: each type keeps its starting share
    assert np.allclose(out["balance"][0, -1] / 1000e6, [0.1, 0.4, 0.2, 0.3])


def test_calls_only_when_refinancing_is_cheaper(tmp_path):
    d = _debt(tmp_path, ROWS)
    up = fund.project(d, _cube(shift=+1.0), 24, CFG)
    dn = fund.project(d, _cube(shift=-1.0), 24, CFG)
    # 5% coupon vs 4% + 1% + 35 bp: not called; vs 3% + 35 bp: called at the first call month (month 6)
    assert up["called"].sum() == 0
    m = np.nonzero(dn["called"][0].sum(axis=1))[0]
    assert m[0] == 5 and dn["called"][0, 5].sum() == pytest.approx(300e6)


def test_runoff_and_parallel_sensitivity(tmp_path):
    d = _debt(tmp_path, ROWS)
    ro = fund.project(d, _cube(), 24, {**CFG, "balance": "runoff"})
    assert ro["issued"].sum() == 0 and ro["balance"][0, -1].sum() < 1000e6
    base = fund.project(d, _cube(), 24, CFG)["interest"].sum()
    hi = fund.project(d, _cube(shift=+1.0), 24, CFG)["interest"].sum()
    assert hi > base                                                 # higher rates cost more on repricing debt


def test_sample_book_matches_the_filing():
    d = fund.load_debt(ROOT / "data" / "liabilities" / "sample_debt.csv", AS_OF)
    g = d.groupby("group")["face"].sum()
    assert d["face"].sum() == pytest.approx(168731)                 # $m, Form 10-Q Q3 2026 Note 8 (carrying amounts)
    assert g["Short-term debt"] == pytest.approx(31120)
    assert d.loc[d["type"] == "callable", "face"].sum() == pytest.approx(60300)   # 61.8bn incl. 1.5bn short-term


def test_bad_rows_are_reported(tmp_path):
    with pytest.raises(fund.DebtFileError):
        _debt(tmp_path, [{"id": "X", "type": "callable", "face": 1, "rate": 4.0, "spread_bp": None, "maturity": "2028-01", "first_call": None}])
