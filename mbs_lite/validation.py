"""Income-dependent validation of the rate model (whitepaper section 11). Rate-only tests V1-V4 and V7 come from
run_rates.py / the stored calibration.

    V4  floor incidence trigger: when more than floor_trigger of paths touch the floor, EaR is rerun with the
        alternative floor and both are disclosed                                          (every run, if triggered)
    V5  convergence: EaR at each count in convergence_paths (default 1,000 / 5,000 / 20,000); the largest run's 5th
        percentile must lie inside the official run's 95% bootstrap interval    (when the calibration changes)
    V6  benchmark shocks: instantaneous parallel shifts of +/-100 and +/-200 bp located as a percentile of the
        simulated distribution; direction consistent with the simulated paths                 (every run: cheap)
    V8  calibration sensitivity: EaR under every named calibration                (when the calibration changes)
V5 and V8 are stored in calibration/income_validation.json keyed by calibration id and simulation settings, and reused
until either changes (or run_model.py run --revalidate).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .engine import project
from .risk import ear, sums


def _ear_rows(e: dict, conf: str = "95") -> list:
    return [{"measure": m, "years": r["years"], "baseline": r["baseline"], "income": r["conf"][conf]["income"],
             "shortfall": r["conf"][conf]["shortfall"], "shortfall_pct": r["conf"][conf]["shortfall_pct"],
             "ci_low": r["conf"][conf]["ci_low"], "ci_high": r["conf"][conf]["ci_high"]}
            for m, rows in e.items() for r in rows]


def floor_rerun(df, rates, prepay, inc, H, base_proj, official_ear: dict) -> dict:
    s = rates.record["simulation"]
    touch = {r["rate"]: r["share_paths_touching_floor"] for r in rates.rate_validation.get("floor", [])}
    worst = max(touch.values()) if touch else 0.0
    res = {"trigger": s["floor_trigger"], "floor": s["floor"], "alt_floor": s["alt_floor"], "share_touching": touch,
           "triggered": bool(worst > s["floor_trigger"]), "rows": []}
    if res["triggered"]:
        cube = rates.simulate(floor=s["alt_floor"])
        alt = ear(project(df, cube, rates, prepay, inc, H), base_proj, detail=False,
                  antithetic=rates.sim.antithetic and rates.sim.sampler == "pseudo")
        off = {(r["measure"], r["years"]): r for r in _ear_rows(official_ear)}
        for r in _ear_rows(alt):
            o = off[(r["measure"], r["years"])]
            res["rows"].append({**r, "official_shortfall": o["shortfall"], "official_shortfall_pct": o["shortfall_pct"]})
    return res


def shocks(df, rates, prepay, inc, H, proj, base_proj, shocks_bp=(-200, -100, 100, 200)) -> dict:
    base_inc = base_proj.agg["income"][0]
    out = []
    years = sorted({1, H // 12})
    for bp in shocks_bp:
        si = project(df, rates.baseline_cube(bp), rates, prepay, inc, H).agg["income"][0]
        for y in years:
            sim = sums(proj.agg["income"], "cumulative", y, H)
            sh, b = si[: 12 * y].sum(), base_inc[: 12 * y].sum()
            out.append({"shock_bp": bp, "years": y, "income_change_pct": float(sh / b - 1),
                        "percentile_in_distribution": float(np.mean(sim <= sh) * 100)})
    # direction: does simulated year-1 income rise or fall with the 10y deviation, and do the shocks agree?
    j = rates.col("TSY_10Y")
    dev10 = rates.mc[:, 1:13, j].mean(axis=1) - rates.baseline[1:13, j].mean()
    slope = float(np.polyfit(dev10, sums(proj.agg["income"], "cumulative", 1, H), 1)[0])
    up = [r for r in out if r["years"] == 1 and r["shock_bp"] > 0]
    dn = [r for r in out if r["years"] == 1 and r["shock_bp"] < 0]
    shock_dir = np.sign(np.mean([r["income_change_pct"] for r in up]) - np.mean([r["income_change_pct"] for r in dn]))
    return {"rows": out, "sim_slope_per_pct_10y": slope, "pass": bool(np.sign(slope) == shock_dir or abs(slope) < 1e-9)}


def _settings(rates) -> dict:
    """Simulation settings the stored V5/V8 results depend on: a change reruns them even for the same calibration."""
    s = rates.record["simulation"]
    out = {"n_paths": int(s["n_paths"]), "convergence_paths": [int(x) for x in s["convergence_paths"]],
           "seed": int(s["seed"]), "sampler": s["sampler"], "antithetic": bool(s["antithetic"])}
    if s.get("vol"):                                  # market-implied width: stored separately from historical results
        out["vol"] = s["vol"]
    return out


def _convergence_pass(conv: list, tested: int, big: int, H: int) -> bool:
    """V5 criterion (revised 2026-11-11): the benchmark (largest run) 5th percentile lies inside the 95% bootstrap
    interval of the tested (official) run, at year 1 and at the full horizon. The earlier rule (tested point inside the
    benchmark's interval) fails about a third of the time on converged results, because it ignores the tested run's
    own sampling error."""
    ok = []
    for y in sorted({1, H // 12}):
        b = next(r for r in conv if r["n_paths"] == big and r["years"] == y)
        o = next(r for r in conv if r["n_paths"] == tested and r["years"] == y)
        ok.append(bool(o["ci_low"] <= b["income"] <= o["ci_high"]))
    return all(ok)


def cached_or_run(path: str, rates, df, prepay, inc, H, base_proj, official_ear, tail_ear, portfolio_meta: dict,
                  force: bool = False, log=print) -> dict:
    """V5 and V8 for the calibration in use: stored by calibration id, recomputed only when it changes."""
    p = Path(path)
    cid = rates.calibration_meta["calibration_id"]
    if p.exists() and not force:
        old = json.loads(p.read_text())
        if old.get("calibration_id") == cid and old.get("settings") == _settings(rates):
            old["reused"] = True
            passed = _convergence_pass(old["convergence"], old["tested_paths"], old["benchmark_paths"], H)
            if passed != old.get("convergence_pass"):          # criterion revised since the results were stored
                old["convergence_pass"] = passed
                p.write_text(json.dumps({k: v for k, v in old.items() if k != "reused"}, indent=2, default=float))
            return old
    log(f"Calibration {cid} has no stored V5/V8 results: running them ...")
    anti = rates.sim.antithetic and rates.sim.sampler == "pseudo"
    # V5 convergence: the official (and tail) run is reused; other counts are run here
    conv = []
    have = {rates.sim.n_paths: official_ear, int(rates.record["simulation"]["tail_paths"]): tail_ear}
    for n in rates.record["simulation"]["convergence_paths"]:
        e = have.get(int(n))
        if e is None:
            e = ear(project(df, rates.simulate(n_paths=int(n)), rates, prepay, inc, H), base_proj, detail=False, antithetic=anti)
        for r in e["cumulative"]:
            if r["years"] in (1, H // 12):
                c = r["conf"]["95"]
                conv.append({"n_paths": int(n), "years": r["years"], "income": c["income"], "shortfall": c["shortfall"],
                             "shortfall_pct": c["shortfall_pct"], "ci_low": c["ci_low"], "ci_high": c["ci_high"],
                             "ci_halfwidth_pct_of_baseline": (c["ci_high"] - c["ci_low"]) / 2 / r["baseline"]})
    counts = sorted(int(x) for x in rates.record["simulation"]["convergence_paths"])
    big = counts[-1]
    tested = rates.sim.n_paths if rates.sim.n_paths < big else counts[-2]   # the official run, unless it is the largest
    ok = [_convergence_pass(conv, tested, big, H)]
    # V8 calibration sensitivity (historical volatility only: under the market-implied width the named calibrations
    # would change only the mortgage spread and tenor loadings, so the range would be misleading)
    sens = []
    names = [rates.calibration_meta["official"]] if rates.sim.vol else list(rates.calibrations)
    for name in names:
        if name == rates.calibration_meta["official"]:
            e = official_ear
        else:
            e = ear(project(df, rates.simulate(calibration=name), rates, prepay, inc, H), base_proj, detail=False, antithetic=anti)
        for r in _ear_rows(e):
            sens.append({"calibration": name, "official": name == rates.calibration_meta["official"], **r})
    res = {"calibration_id": cid, "settings": _settings(rates), "computed_utc": datetime.now(timezone.utc).isoformat(), "reused": False,
           "portfolio": portfolio_meta, "convergence": conv, "convergence_pass": all(ok), "tested_paths": tested,
           "benchmark_paths": big, "sensitivity": sens}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(res, indent=2, default=float))
    return res
