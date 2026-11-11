"""ratemodel - real-world, baseline-centred two-factor Gaussian rate path generator for EaR (model version 1.0.0).

    data.py       FRED history and as-of curve from the local cache (data/fred)
    model.py      calibration (horizon matching), simulation, rate-only validation (V1-V4, V7)
    curve.py      Treasury curve bootstrap and implied forwards
    baseline.py   forward / flat / house-view baselines
    store.py      the stored, versioned calibration file
The whitepaper is mbs_lite/templates/whitepaper.html: the dashboard's "Rate model" page, which fills its results
blocks and the section 10.2 parameter table from each run (mbs_lite/templates/whitepaper.js).
"""
from .model import MODEL_VERSION  # noqa: F401
