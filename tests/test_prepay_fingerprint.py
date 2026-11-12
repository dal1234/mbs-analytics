"""Stored V5/V8 results are tied to the prepayment model: a parameter or multiplier change gives a new fingerprint."""
import copy
from pathlib import Path

from mbs_lite.prepay import SCurvePrepay
from mbs_lite.validation import prepay_fingerprint

PARAMS = Path(__file__).resolve().parents[1] / "config" / "prepay_params.yaml"


def test_fingerprint_follows_parameters_and_multiplier():
    a = SCurvePrepay.from_file(str(PARAMS))
    b = SCurvePrepay.from_file(str(PARAMS))
    assert prepay_fingerprint(a) == prepay_fingerprint(b)
    c = SCurvePrepay(copy.deepcopy(a.p)); c.p["refi_mid"] += 0.01
    assert prepay_fingerprint(c) != prepay_fingerprint(a)
    assert prepay_fingerprint(SCurvePrepay(a.p, multiplier=1.25)) != prepay_fingerprint(a)
