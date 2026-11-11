"""The stored calibration (whitepaper section 13.3): estimated on demand, reused by every run until the next one.

One JSON file holds all named calibrations (the official one plus the sensitivities), the V7 parameter-uncertainty
bootstrap and an id. Each new calibration is also archived as calibration_<data_through>_<timestamp>.json so earlier
runs can be reproduced. The layout extends the one the whitepaper page reads (model_version, official, calibrations).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .model import CALIBRATION_WINDOWS, MODEL_VERSION, OFFICIAL_CALIBRATION, Calibration, calibrate, parameter_uncertainty


def estimate(hist, fwd=None, log=print) -> dict:
    """Fit every named calibration on ``hist`` and run the V7 bootstrap on the official one.

    fwd: forward changes for the same months (forwards.forward_changes); production calibrations always pass it, so
    every target is a deviation from the forward baseline (v1.2.0). None = raw changes (synthetic tests only)."""
    cals = {}
    for name in CALIBRATION_WINDOWS:
        cals[name] = calibrate(hist, name, fwd=fwd)
        log(f"      {name:9s} {cals[name].window_start}..{cals[name].window_end} ({cals[name].n_obs} months)")
    log("      V7 parameter-uncertainty bootstrap (200 refits) ...")
    unc = parameter_uncertainty(cals[OFFICIAL_CALIBRATION])
    now = datetime.now(timezone.utc)
    through = str(hist.index[-1])
    return {"model_version": MODEL_VERSION, "official": OFFICIAL_CALIBRATION,
            "calibration_id": f"{through}_{now.strftime('%Y%m%dT%H%M%SZ')}", "created_utc": now.isoformat(),
            "data_through": through, "target": "forward_deviation" if fwd is not None else "raw_change",
            "calibrations": [c.to_dict() for c in cals.values()],
            "parameter_uncertainty": unc}


def save(payload: dict, path: str, archive_dir: str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, default=float)
    p.write_text(text)
    a = Path(archive_dir)
    a.mkdir(parents=True, exist_ok=True)
    (a / f"calibration_{payload['calibration_id']}.json").write_text(text)
    return p


def load(path: str) -> tuple[dict, dict]:
    payload = json.loads(Path(path).read_text())
    cals = {c["name"]: Calibration.from_dict(c) for c in payload["calibrations"]}
    return payload, cals
