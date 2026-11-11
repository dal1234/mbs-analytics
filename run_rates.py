#!/usr/bin/env python
"""Rate model run: baseline + official Monte Carlo paths for one as-of date (ratemodel, version in ratemodel/model.py).

    python run_rates.py --as-of YYYY-MM-DD [--config FILE] [--recalibrate] [--calibration FILE]
                        [--baseline forward|swap|flat|file] [--baseline-file CSV] [--swap-curve CSV]
                        [--vol historical|market] [--output DIR] [--refresh]

Two approaches are configured (README, "Two approaches"):
    config/rates.yaml         official: Treasury forward baseline + historical (calibrated) volatility
    config/rates_vendor.yaml  comparison: SOFR swap forward baseline + market-implied volatility (emulates a
                              risk-neutral vendor model; not the validated model)

Steps
  1. history and the as-of curve from data/fred (nothing after the as-of date)
  2. calibration: the stored one (config calibration.file), or a new one with --recalibrate / when none exists
     (all named calibrations + the V7 bootstrap; recalibrate annually or when a monitoring trigger fires)
  3. baseline (forward | flat | file)
  4. official paths (5,000, antithetic, moment-matched, fixed seed) and the rate-only validation V1-V4
  5. writes the output folder: run_record.json, baseline.csv, mc_paths.csv, history_monthly.csv, rate_validation.json
run_model.py reads these and regenerates any other path set (V4 floor rerun, V5 convergence, V8 calibrations) from the
stored calibration, baseline and seed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from ratemodel import baseline as bl, data, forwards, store  # noqa: E402
from ratemodel.model import MODEL_VERSION, SimConfig, rate_validation, simulate_paths  # noqa: E402

KEYS = ["short", "y1", "y2", "y5", "y10", "y30", "mort"]
NAMES = ["SOFR_1M", "TSY_1Y", "TSY_2Y", "TSY_5Y", "TSY_10Y", "TSY_30Y", "MTG_30Y"]


def _read(p: Path) -> dict:
    """YAML with optional ``extends: other.yaml`` (relative to this file): this file's keys override the other's."""
    cfg = yaml.safe_load(p.read_text()) or {}
    parent = cfg.pop("extends", None)
    if not parent:
        return cfg
    base = _read((p.parent / parent).resolve())

    def merge(b, o):
        out = dict(b)
        for k, v in o.items():
            out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
        return out
    return merge(base, cfg)


def load_cfg(path: str) -> dict:
    p = Path(path).resolve()
    cfg = _read(p)
    rel = lambda x: None if x is None else str((p.parent / x).resolve()) if not Path(x).is_absolute() else x  # noqa: E731
    cfg["data"]["dir"] = rel(cfg["data"]["dir"])
    cfg["calibration"]["file"] = rel(cfg["calibration"]["file"])
    cfg["calibration"]["archive_dir"] = rel(cfg["calibration"]["archive_dir"])
    cfg["output"]["dir"] = rel(cfg["output"]["dir"])
    cfg["baseline"]["file"] = rel(cfg["baseline"].get("file"))
    sw = cfg["baseline"].setdefault("swap", {}) or {}
    sw["curve_file"] = rel(sw.get("curve_file"))
    cfg["baseline"]["swap"] = sw
    cfg.setdefault("volatility", {"type": "historical"})
    cfg["_path"] = str(p)
    return cfg


def vol_spec(cfg: dict) -> dict | None:
    """None = historical (the calibrated, validated model); a dict = the market-implied comparison width."""
    v = cfg.get("volatility") or {}
    if str(v.get("type", "historical")).lower() != "market":
        return None
    m = v.get("market") or {}
    return {"type": "market", "sofr_bp": [float(x) for x in m["sofr_bp"]], "y10_bp": [float(x) for x in m["y10_bp"]],
            "corr": None if m.get("corr") is None else float(m["corr"]), "source": m.get("source", "")}


def vol_describe(vol: dict | None) -> str:
    if not vol:
        return "Historical: calibrated to deviations of rates from the forward since the calibration window start (official)"
    f = lambda v: "/".join(f"{x:g}" for x in v)  # noqa: E731
    return (f"Market-implied (comparison, not the validated model): random walk, normal vol by year SOFR {f(vol['sofr_bp'])} bp, "
            f"10-year {f(vol['y10_bp'])} bp" + (f", correlation {vol['corr']:.2f}" if vol.get("corr") is not None else ", historical correlation")
            + (f"; source: {vol['source']}" if vol.get("source") else ""))


def sim_config(cfg: dict, n_paths: int | None = None, floor: float | None = None) -> SimConfig:
    s = cfg["simulation"]
    return SimConfig(n_paths=int(n_paths or s["n_paths"]), n_months=int(s["horizon_months"]), antithetic=bool(s["antithetic"]),
                     moment_match=bool(s["moment_match"]), sampler=s.get("sampler", "pseudo"), seed=int(s["seed"]),
                     floor=float(s["floor"] if floor is None else floor), vol=vol_spec(cfg))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "config" / "rates.yaml"))
    ap.add_argument("--as-of", dest="as_of", required=True)
    ap.add_argument("--recalibrate", action="store_true", help="estimate a new calibration on history to the as-of date")
    ap.add_argument("--calibration", help="use this calibration file instead of the configured one (e.g. an archived one)")
    ap.add_argument("--baseline", choices=["forward", "swap", "flat", "file"])
    ap.add_argument("--baseline-file", help="for --baseline file: monthly path (house view or another model's zero-vol path)")
    ap.add_argument("--swap-curve", help="for --baseline swap: SOFR swap par curve on the as-of date (CSV tenor,rate)")
    ap.add_argument("--vol", choices=["historical", "market"], help="volatility: historical (official) or market-implied")
    ap.add_argument("--output")
    ap.add_argument("--refresh", action="store_true", help="download from FRED first")
    a = ap.parse_args(argv)
    cfg = load_cfg(a.config)
    if a.baseline:
        cfg["baseline"]["type"] = a.baseline
    if a.baseline_file:
        cfg["baseline"]["file"] = str(Path(a.baseline_file).resolve())
    if a.swap_curve:
        cfg["baseline"]["swap"]["curve_file"] = str(Path(a.swap_curve).resolve())
    if a.vol:
        cfg["volatility"]["type"] = a.vol
    if a.output:
        cfg["output"]["dir"] = str(Path(a.output).resolve())
    refresh = a.refresh or bool(cfg["data"].get("refresh"))
    H = int(cfg["simulation"]["horizon_months"])
    as_of = pd.Timestamp(a.as_of)

    print(f"[1/5] History and as-of curve (refresh={refresh}) ...")
    hist = data.load_history(cfg["data"]["dir"], as_of, refresh)
    spot = data.load_spot(cfg["data"]["dir"], as_of, refresh)
    print(f"      history {hist.index[0]}..{hist.index[-1]} ({len(hist)} months); curve date {spot.date.date()}; "
          f"1M SOFR {spot.sofr_1m:.2f}% ({spot.sofr_source}), 10Y {spot.curve['10Y']:.2f}%, mortgage {spot.mort:.2f}%")

    print("[2/5] Calibration ...")
    cal_file = a.calibration or cfg["calibration"]["file"]
    warnings = []
    if a.recalibrate or not Path(cal_file).exists():
        if not a.recalibrate:
            print(f"      no calibration at {cal_file}: estimating one")
        print("      forward curves for every month of history (calibration target: deviations from the forward) ...")
        fwd = forwards.forward_changes(forwards.monthly_curves(cfg["data"]["dir"], as_of, refresh))
        payload = store.estimate(hist, fwd)
        store.save(payload, cal_file, cfg["calibration"]["archive_dir"])
        print(f"      saved {cal_file} (id {payload['calibration_id']}, archived)")
    payload, cals = store.load(cal_file)
    cal = cals[payload["official"]]
    through = pd.Period(payload["data_through"], "M")
    age = (as_of.to_period("M") - through).n
    if age > int(cfg["calibration"].get("max_age_months", 12)):
        warnings.append(f"calibration data ends {through}, {age} months before the as-of date: annual recalibration is due "
                        f"(run_rates.py --recalibrate)")
    if age < 0:
        warnings.append(f"calibration uses data through {through}, after the as-of date {as_of.date()} (back-dated run "
                        f"with a later calibration); use --calibration with the archived calibration in force at the time")
    flags = [f"{c.name}: {f} {par} at its bound" for c in cals.values()
             for f, d in (("short factor", c.diagnostics["short_fit"]), ("long factor", c.diagnostics["long_fit"]),
                          ("mortgage spread", c.diagnostics.get("mort_fit", {"phi_at_bound": False})))
             for par, key in (("phi", "phi_at_bound"), ("momentum", "mom_at_bound")) if d.get(key)]
    if payload.get("target") != "forward_deviation":
        warnings.append("calibration was not fitted to forward deviations (model 1.2.0 target): run_rates.py --recalibrate")
    print(f"      in use: {payload['official']} {cal.window_start}..{cal.window_end}, id {payload['calibration_id']}")
    print("      " + cal.summary().round(3).to_string().replace("\n", "\n      "))
    for f in flags:
        print(f"      FLAG  {f}")
    for w in warnings:
        print(f"      WARN  {w}")

    bdesc = bl.describe(cfg["baseline"]["type"], cfg["baseline"].get("file"), spot, cfg["baseline"]["swap"])
    print(f"[3/5] Baseline: {cfg['baseline']['type']} ({bdesc}) ...")
    base = bl.build_baseline(cfg["baseline"]["type"], spot, H, cfg["baseline"].get("file"), cfg["baseline"]["swap"])
    print("      " + (base.iloc[[0, 12, 60]] * 100).round(2).to_string().replace("\n", "\n      "))

    sc = sim_config(cfg)
    if sc.vol and not all(sc.vol[k] for k in ("sofr_bp", "y10_bp")):
        raise ValueError("volatility.market needs sofr_bp and y10_bp (bp per year, one value per year)")
    validated = sc.vol is None          # the baseline is an input (whitepaper 3.2); the volatility is the model
    if not validated:
        warnings.append("market-implied volatility: a comparison set-up that emulates a risk-neutral vendor model, "
                        "not the validated rate model (whitepaper section 13)")
    print(f"[4/5] Paths: {sc.n_paths} x {sc.n_months} months, seed {sc.seed}; volatility: {vol_describe(sc.vol)} ...")
    b1 = base.iloc[1:].copy()
    b1.index = pd.RangeIndex(1, H + 1, name="month")
    paths = simulate_paths(cal, b1, sc)
    val = rate_validation(cal, hist, b1, paths, sc)
    for name, rows in val.items():
        ok = all(r.get("pass", True) for r in rows)
        print(f"      {'PASS' if ok else 'FAIL'}  {name}")
    touch = max(r["share_paths_touching_floor"] for r in val["floor"])
    print(f"      floor: up to {touch:.1%} of paths touch {sc.floor:.2%}"
          + (" -> run_model.py will rerun EaR with the alternative floor (V4)" if touch > cfg['simulation']['floor_trigger'] else ""))

    print("[5/5] Writing outputs ...")
    out = Path(cfg["output"]["dir"])
    out.mkdir(parents=True, exist_ok=True)
    for f in list(out.iterdir()):                       # the folder holds one run: clear earlier files
        if f.is_file() and f.suffix in (".csv", ".json", ".md", ".xlsx"):
            f.unlink()
    dates = [str((as_of + pd.offsets.MonthEnd(0) + pd.offsets.MonthEnd(m)).date()) if m else str(as_of.date())
             for m in range(H + 1)]
    bdf = (base * 100).rename(columns=dict(zip(KEYS, NAMES)))
    bdf.insert(0, "date", dates)
    bdf.to_csv(out / "baseline.csv", float_format="%.6f")
    cube = np.stack([np.concatenate([np.full((sc.n_paths, 1), base[k].iloc[0]), paths[k]], axis=1) for k in KEYS], axis=2) * 100
    N = sc.n_paths
    mp = pd.DataFrame(cube.reshape(N * (H + 1), len(KEYS)), columns=NAMES)
    mp.insert(0, "date", np.tile(dates, N))
    mp.insert(0, "month", np.tile(np.arange(H + 1), N))
    mp.insert(0, "path", np.repeat(np.arange(1, N + 1), H + 1))
    mp.to_csv(out / "mc_paths.csv", index=False, float_format="%.6f")
    hm = (hist * 100).rename(columns=dict(zip(KEYS, NAMES)))
    hm.index = hm.index.to_timestamp("M")
    hm.index.name = "month_end"
    hm.to_csv(out / "history_monthly.csv", float_format="%.6f")
    (out / "rate_validation.json").write_text(json.dumps(val, indent=2, default=float))
    record = {
        "model_version": MODEL_VERSION, "created_utc": datetime.now(timezone.utc).isoformat(),
        "as_of": str(as_of.date()), "curve_date": str(spot.date.date()),
        "spot": {"curve": spot.curve, "sofr_1m": spot.sofr_1m, "sofr_source": spot.sofr_source, "mort": spot.mort},
        "history": {"first": str(hist.index[0]), "last": str(hist.index[-1]), "months": len(hist)},
        "calibration": {"file": str(Path(cal_file).resolve()), "id": payload["calibration_id"], "official": payload["official"],
                        "window": [cal.window_start, cal.window_end], "data_through": payload["data_through"],
                        "created_utc": payload["created_utc"], "flags": flags},
        "simulation": {**asdict(sc), "tail_paths": int(cfg["simulation"]["tail_paths"]),
                       "convergence_paths": list(cfg["simulation"]["convergence_paths"]),
                       "alt_floor": float(cfg["simulation"]["alt_floor"]), "floor_trigger": float(cfg["simulation"]["floor_trigger"])},
        "volatility": {"type": "market" if sc.vol else "historical", "description": vol_describe(sc.vol), "spec": sc.vol},
        "validated_model": validated,
        "baseline": {"type": cfg["baseline"]["type"], "file": cfg["baseline"].get("file"),
                     "swap": cfg["baseline"]["swap"] if cfg["baseline"]["type"] == "swap" else None,
                     "description": bdesc,
                     "id": hashlib.sha1(base.round(10).to_csv().encode()).hexdigest()[:12]},
        "paths_sha1": hashlib.sha1(np.round(cube, 6).tobytes()).hexdigest()[:12],
        "warnings": warnings,
    }
    (out / "run_record.json").write_text(json.dumps(record, indent=2, default=float))
    print(f"      wrote {out} (run_record.json, baseline.csv, mc_paths.csv, history_monthly.csv, rate_validation.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
