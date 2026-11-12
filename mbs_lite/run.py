"""End-to-end run: validate inputs -> baseline + Monte Carlo income -> EaR, validation -> outputs/, history, dashboard/."""
from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import __version__
from . import history as hist
from . import scenarios as scen
from . import validation as val
from .engine import cpr_from, project
from .portfolio import load_portfolio
from .prepay import SCurvePrepay, estimate_burnout
from .rates import SERIES, load_rates
from .risk import MEASURES, band, cohort_codes, ear, fan, outcomes, path_table, sums, tail_attribution

TEMPLATES = Path(__file__).parent / "templates"


class InputError(Exception):
    pass


def prepare(cfg: dict, log=print):
    """Load and validate the portfolio and the rate run. Raises InputError with every problem found."""
    pf = load_portfolio(cfg["portfolio"]["file"])
    log(f"Portfolio  {cfg['portfolio']['file']}")
    log(f"           {pf.n} rows, as of {pf.as_of.date() if pd.notna(pf.as_of) else '?'}"
        + (f", face ${pf.df['current_upb'].sum() / 1e9:,.2f}bn" if not pf.errors else ""))
    for w in pf.warnings:
        log(f"  WARN  {w}")
    for e in pf.errors:
        log(f"  ERROR {e}")
    rc = cfg["rates"]
    rates = load_rates(rc["dir"], cfg["horizon_months"])
    r = rates.record
    log(f"Rates      {rc['dir']}")
    log(f"           as of {rates.as_of.date()}, calibration {r['calibration']['official']} "
        f"{r['calibration']['window'][0]}..{r['calibration']['window'][1]} (id {r['calibration']['id']}), "
        f"{rates.n_paths} paths, baseline {r['baseline']['type']}")
    if not rates.reproduced:
        log("  WARN  the stored paths do not regenerate from the run record; rerun run_rates.py")
    errors = list(pf.errors)
    if cfg["income"].get("reinvest_rate_series", "SOFR_1M") not in SERIES:
        errors.append(f"income.reinvest_rate_series must be one of {', '.join(SERIES)}")
    if pd.notna(pf.as_of) and pf.as_of.to_period("M") != rates.as_of.to_period("M"):
        msg = (f"portfolio date {pf.as_of.date()} and rate as-of {rates.as_of.date()} are in different months; "
               f"run `python run_rates.py --as-of {pf.as_of.date()}`")
        if rc.get("require_matching_as_of", True):
            errors.append(msg)
        else:
            pf.warnings.append(msg)
            log(f"  WARN  {msg}")
    if errors:
        raise InputError("\n".join(errors))

    df = pf.df
    on = cfg["prepay"].get("estimate_burnout", True)
    est = estimate_burnout(df["wac"].to_numpy(float), df["wala"].to_numpy(float), rates.as_of, rates.history["MTG_30Y"]) \
        if on else np.zeros(len(df))
    df["burnout_source"] = np.where(df["burnout_years"].notna(), "file", "estimated" if on else "zero")
    df["burnout_years"] = df["burnout_years"].fillna(pd.Series(est, index=df.index))
    prepay = SCurvePrepay.from_file(cfg["prepay"]["params_file"], cfg["prepay"].get("multiplier", 1.0))
    return pf, rates, prepay


def run(cfg: dict, log=print, write_outputs: bool = True, write_dashboard: bool = True, source: str = "run",
        record_history: bool = True, income_validation: bool = True, revalidate: bool = False) -> dict:
    t0 = time.time()
    pf, rates, prepay = prepare(cfg, log)
    df, H, inc, rk = pf.df, cfg["horizon_months"], cfg["income"], cfg["risk"]
    upb0 = float(df["current_upb"].sum())
    codes, labels = cohort_codes(df, list(rk.get("cohort_by", ["product", "coupon", "vintage"])))
    anti = rates.sim.antithetic and rates.sim.sampler == "pseudo"
    bins = int(rk.get("histogram_bins", 30))
    tail_n = int(rates.record["simulation"]["tail_paths"])

    log(f"Projecting the baseline and {rates.n_paths} official paths x {pf.n} CUSIPs x {H} months ...")
    base = project(df, rates.baseline_cube(), rates, prepay, inc, H, groups=codes, mean_detail=True)
    off = project(df, rates.mc, rates, prepay, inc, H, groups=codes, mean_detail=True)
    if tail_n == rates.n_paths:                      # official run carries the 99% measures too (one path set)
        off_ear = ear(off, base, confs=(0.95, 0.99), es_confs=(0.95, 0.99), antithetic=anti, bins=bins)
        tail_ear = off_ear
    else:
        off_ear = ear(off, base, confs=(0.95,), es_confs=(0.95,), antithetic=anti, bins=bins)
        log(f"Projecting {tail_n:,} paths for the 99% measures and expected shortfall ...")
        tail = project(df, rates.simulate(n_paths=tail_n), rates, prepay, inc, H)
        tail_ear = ear(tail, base, confs=(0.95, 0.99), es_confs=(0.95, 0.99), antithetic=anti, detail=False)
    outs = outcomes(off, base, rates, list(rk.get("outcomes", [5, 10, 25, 50, 75, 90, 95])), float(rk.get("band_pct", 5)))
    tails = tail_attribution(df, codes, labels, off, base, float(rk.get("tail_pct", 5)))
    decisions = _decisions(df, rates, prepay, inc, H, int(rk.get("decision_paths", 500)), anti, log)

    rs = scen.run(df, rates, prepay, inc, H, cfg.get("scenarios") or {}, codes, labels, off.agg["income"], log)
    rs["cohorts"]["by"] = list(rk.get("cohort_by", ["product", "coupon", "vintage"]))

    v4 = val.floor_rerun(df, rates, prepay, inc, H, base, off_ear)
    v6 = val.shocks(df, rates, prepay, inc, H, off, base)
    v58 = None
    if income_validation:
        v58 = val.cached_or_run(cfg["validation"]["file"], rates, df, prepay, inc, H, base, off_ear, tail_ear,
                                {"as_of": str(pf.as_of.date()), "file": Path(cfg["portfolio"]["file"]).name,
                                 "positions": pf.n, "upb": upb0}, force=revalidate, log=log)

    # projected average life with rates held at today's levels, to full term
    L = int(max(df["wam"].max(), 12))
    flat = np.repeat(rates.baseline_cube()[:, :1], L + 1, axis=1)
    lp = project(df, flat, rates, prepay, inc, L, mean_detail=True).detail
    prin = lp["sched"] + lp["prepaid"]
    t = np.arange(1, L + 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        df["proj_wal_years"] = (prin * t).sum(axis=1) / prin.sum(axis=1) / 12.0
    port_wal = float((prin * t).sum() / prin.sum() / 12.0)

    run_at = datetime.now(timezone.utc)
    spot = rates.baseline[0]
    rec = rates.record
    hmeta = {"as_of": str(pf.as_of.date()), "run_at": run_at.strftime("%Y-%m-%d %H:%M"), "source": source,
             "portfolio": cfg["portfolio"].get("name", ""), "positions": pf.n, "upb": upb0, "n_paths": rates.n_paths,
             "tail_paths": tail_n, "calibration_id": rec["calibration"]["id"], "baseline_type": rec["baseline"]["type"],
             "sofr_1m": float(spot[rates.col("SOFR_1M")]), "mtg_30y": float(spot[rates.col("MTG_30Y")])}
    if record_history:
        hist.append(cfg["history"]["file"], hist.rows(hmeta, off_ear, tail_ear))

    details = {}
    if write_outputs or write_dashboard:
        details = {"baseline": base.detail, "mean": off.detail}
        ry = int(cfg["outputs"].get("cusip_detail_rank_years", 1))
        v = sums(off.agg["income"], "cumulative", ry, H)
        for q in cfg["outputs"].get("cusip_detail_outcomes", [50, 5]):
            idx = band(v, float(q), float(rk.get("band_pct", 5)))
            details[f"P{int(q):02d}"] = project(df, rates.mc[idx], rates, prepay, inc, H, mean_detail=True).detail
    checks = _checks(pf, rates, off, details, outs, tails, inc, v4, v6, v58)
    checks[-1:-1] = _scenario_checks(rs, base)
    if write_outputs:
        out = Path(cfg["outputs"]["dir"])
        out.mkdir(parents=True, exist_ok=True)
        files = _write_outputs(out, df, rates, off, details, off_ear, tail_ear, outs, tails, checks, v4, v6, v58)
        for name, frame in scen.frames(rs, rates).items():
            frame.to_csv(out / name, index=False, float_format="%.6f")
            files.append(name)
        log(f"Wrote {len(files)} files to {out}")
    data = None
    if write_dashboard:
        data = _dashboard_data(cfg, pf, rates, prepay, off_ear, tail_ear, outs, tails, checks, port_wal, run_at,
                               base, v4, v6, v58)
        data["decisions"] = decisions
        data["rate_specific"] = scen.public(rs)
        dash = _write_dashboard(cfg, data)
        log(f"Dashboard  {dash / 'index.html'}  ({time.time() - t0:.1f}s)")
    for c in checks:
        log(f"  {c['status']:5s} {c['check']}: {c['detail']}")
    return {"ear": off_ear, "tail": tail_ear, "checks": checks, "data": data}


# --------------------------------------------------------------------------- business decisions
def _decisions(df, rates, prepay, inc, H, n, anti, log) -> dict:
    """EaR 95% with each switchable income decision flipped, on the same n paths (common random numbers), so the
    differences come from the decision alone. Reported as % change against the current settings on those paths."""
    if n <= 0:
        return {"paths": 0, "rows": []}
    log(f"Business-decision sensitivities on {n} paths ...")
    cube = rates.simulate(n_paths=n)
    years = sorted({1, H // 12})

    def ear_of(i):
        b = project(df, rates.baseline_cube(), rates, prepay, i, H)
        e = ear(project(df, cube, rates, prepay, i, H), b, detail=False, n_boot=100, antithetic=anti)["cumulative"]
        return {y: e[y - 1]["conf"]["95"]["shortfall"] for y in years}

    cur = ear_of(inc)
    sched = bool(inc.get("reinvest_scheduled_principal", False))
    series, spread = inc.get("reinvest_rate_series", "SOFR_1M"), float(inc.get("reinvest_spread_bp", 0))
    variants = [("reinvest_scheduled", "Scheduled principal " + ("not reinvested" if sched else "reinvested too"),
                 {**inc, "reinvest_scheduled_principal": not sched}),
                ("reinvest_spread", f"Reinvestment rate + 100 bp ({spread + 100:g} bp spread)", {**inc, "reinvest_spread_bp": spread + 100}),
                ("reinvest_term", "Reinvest at the 2Y Treasury instead of " + series.replace("_", " "),
                 {**inc, "reinvest_rate_series": "TSY_2Y" if series != "TSY_2Y" else "SOFR_1M"})]
    rows = []
    for key, label, i in variants:
        e = ear_of(i)
        rows.append({"key": key, "alternative": label, **{f"ear_y{y}": e[y] for y in years},
                     **{f"change_y{y}": (e[y] / cur[y] - 1) if cur[y] else None for y in years}})
    return {"paths": n, "years": years, "current": {f"ear_y{y}": cur[y] for y in years}, "rows": rows}


# --------------------------------------------------------------------------- checks
def _checks(pf, rates, off, details, outs, tails, inc, v4, v6, v58) -> list:
    out = []

    def add(name, status, detail):
        out.append({"check": name, "status": status, "detail": detail})

    a = off.agg
    e1 = float(np.abs(a["end_upb"] - (a["begin_upb"] - a["sched"] - a["prepaid"])).max())
    e2 = float(np.abs(a["begin_upb"][:, 1:] - a["end_upb"][:, :-1]).max())
    add("Balance identity, every path", "PASS" if max(e1, e2) < 0.01 else "FAIL",
        f"portfolio balance(t) = balance(t-1) - prepaid - scheduled on all {a['income'].shape[0]} paths: max error ${max(e1, e2):.2e}")
    if details:
        err = max(max(float(np.abs(d["end_upb"] - (d["begin_upb"] - d["sched"] - d["prepaid"])).max()),
                      float(np.abs(d["begin_upb"][:, 1:] - d["end_upb"][:, :-1]).max())) for d in details.values())
        add("Balance identity by CUSIP", "PASS" if err < 0.01 else "FAIL",
            f"same identity per CUSIP and month in the CUSIP detail ({', '.join(details)}): max error ${err:.2e}")
    added = a["prepaid"] + (a["sched"] if inc.get("reinvest_scheduled_principal", False) else 0.0)
    rerr = float(np.abs(a["reinv_end"] - a["reinv_bal"] - added).max())
    add("Reinvestment balance roll-forward", "PASS" if rerr < 0.01 else "FAIL",
        f"reinvested balance grows by the principal reinvested each month (max difference ${rerr:,.2f})")
    oerr = max(abs(sum(r["interest"]) + sum(r["reinv"]) - sum(r["income"])) for m in MEASURES for h in outs[m]
               for r in [*h["rows"].values(), h["baseline"]])
    add("Outcome cash flows add up", "PASS" if oerr < 1.0 else "FAIL",
        f"coupon + reinvestment = income for every outcome, the baseline and every horizon (max difference ${oerr:,.2f})")
    terr = max(abs(sum(r["delta"] for r in t["rows"]) + t["shortfall"]) for m in MEASURES for t in tails[m])
    add("Tail attribution adds up", "PASS" if terr < 1.0 else "FAIL",
        f"cohort differences sum to the tail's shortfall against the baseline (max difference ${terr:,.2f})")
    add("Paths reproduce from the run record", "PASS" if rates.reproduced else "FAIL",
        f"official paths regenerate from calibration {rates.record['calibration']['id']}, the baseline and seed {rates.sim.seed}")
    add("Portfolio and rate dates", "PASS" if pf.as_of.to_period("M") == rates.as_of.to_period("M") else "WATCH",
        f"portfolio {pf.as_of.date()}, rates {rates.as_of.date()}")
    add("Portfolio file", "PASS" if not pf.warnings else "WATCH",
        "no warnings" if not pf.warnings else f"{len(pf.warnings)} warning(s): " + "; ".join(pf.warnings))
    vr = rates.record.get("volatility") or {"type": "historical"}
    add("Rate set-up", "PASS" if vr["type"] == "historical" else "WATCH",
        f"baseline {rates.record['baseline']['type']}: {rates.record['baseline']['description']}; volatility: "
        + vr.get("description", vr["type"]))
    rc = rates.record["calibration"]
    add("Calibration in use", "WATCH" if rates.record.get("warnings") else "PASS",
        f"{rc['official']} {rc['window'][0]}..{rc['window'][1]}, id {rc['id']}"
        + ("; " + "; ".join(rates.record["warnings"]) if rates.record.get("warnings") else ""))
    if rc.get("flags"):
        add("Calibration flags", "WATCH", "; ".join(rc["flags"]) + " (whitepaper 6.1: a bound estimate is reported, not adjusted)")
    rv = rates.rate_validation
    for key, name in (("centering", "V1 centering"), ("dispersion", "V2 dispersion"), ("correlation", "V3 co-movement")):
        rows = rv.get(key, [])
        n_ok = sum(bool(r.get("pass")) for r in rows)
        add(name, "PASS" if rows and n_ok == len(rows) else "FAIL", f"{n_ok} of {len(rows)} tests within the acceptance criterion")
    worst = max(v4["share_touching"].values()) if v4["share_touching"] else 0.0
    add("V4 floor incidence", "WATCH" if v4["triggered"] else "PASS",
        f"up to {worst:.1%} of paths touch the {v4['floor']:.2%} floor (trigger {v4['trigger']:.0%})"
        + (f"; EaR rerun with a {v4['alt_floor']:.2%} floor and disclosed" if v4["triggered"] else ""))
    if v58:
        add("V5 convergence", "PASS" if v58["convergence_pass"] else "FAIL",
            f"{v58.get('benchmark_paths', 0):,}-path 5th percentile inside the official {v58.get('tested_paths', rates.n_paths):,}-path interval ({'stored for' if v58.get('reused') else 'run for'} "
            f"calibration {v58['calibration_id']})")
        rng = [r["shortfall_pct"] for r in v58["sensitivity"] if r["measure"] == "cumulative" and r["years"] == 1]
        if len(rng) > 1:
            add("V8 calibration sensitivity", "INFO", f"year-1 shortfall across calibrations {min(rng):.2%} to {max(rng):.2%} of baseline")
        else:
            add("V8 calibration sensitivity", "INFO", "not run: the market-implied volatility does not come from the calibrations")
    add("V6 benchmark shocks", "PASS" if v6["pass"] else "WATCH",
        "direction of the parallel-shock results agrees with the simulated paths" if v6["pass"]
        else "parallel shocks and simulated paths disagree on the direction of income")
    return out


def _scenario_checks(rs, base) -> list:
    """Rate-specific scenarios: the base row is the EaR baseline, and the attribution adds up."""
    out = []
    b = rs["_proj"].agg["income"][[c["id"] for c in rs["catalog"]].index(rs["base_id"])]
    err = float(np.abs(b - base.agg["income"][0]).max())
    out.append({"check": "Rate-specific base = EaR baseline", "status": "PASS" if err < 0.01 else "FAIL",
                "detail": f"income on the base scenario equals baseline income month by month (max difference ${err:,.2f})"})
    an = rs["annual"]
    aerr = max([abs(sum(p[k][y] for k in p) - (an[sid]["income"][y] - an[rs["base_id"]]["income"][y]))
                for sid, p in rs["attribution"].items() for y in range(rs["years"])] or [0.0])
    out.append({"check": "Scenario attribution adds up", "status": "PASS" if aerr < 5.0 else "FAIL",
                "detail": f"prepayment, reinvestment-rate and interaction parts sum to each scenario's change vs base "
                          f"in every year (max difference ${aerr:,.0f}, rounding)"})
    return out


# --------------------------------------------------------------------------- outputs
def _ear_frame(off_ear, tail_ear) -> pd.DataFrame:
    rows = []
    for m in MEASURES:
        for r, t in zip(off_ear[m], tail_ear[m]):
            c = r["conf"]["95"]
            rows.append({"measure": m, "years": r["years"], "baseline_income": r["baseline"], "mean_income": r["mean"],
                         "mean_minus_baseline_pct": r["mean_minus_baseline_pct"], **{f"p{k}": v for k, v in r["pct"].items()},
                         "income_at_95": c["income"], "shortfall95_usd": c["shortfall"], "shortfall95_pct": c["shortfall_pct"],
                         "ci_low_95": c["ci_low"], "ci_high_95": c["ci_high"],
                         "shortfall95_usd_5k": t["conf"]["95"]["shortfall"],
                         "income_at_99_5k": t["conf"]["99"]["income"], "shortfall99_usd_5k": t["conf"]["99"]["shortfall"],
                         "shortfall99_pct_5k": t["conf"]["99"]["shortfall_pct"], "ci_low_99_5k": t["conf"]["99"]["ci_low"],
                         "ci_high_99_5k": t["conf"]["99"]["ci_high"], "es95_usd_5k": t["es"]["95"]["shortfall"],
                         "es95_pct_5k": t["es"]["95"]["shortfall_pct"], "es99_usd_5k": t["es"]["99"]["shortfall"],
                         "cpr_mean": r["cpr"]["mean"], "cpr_baseline": r["cpr"]["baseline"]})
    return pd.DataFrame(rows)


def _write_outputs(out: Path, df, rates, off, details, off_ear, tail_ear, outs, tails, checks, v4, v6, v58) -> list:
    files = []

    def save(frame, name):
        frame.to_csv(out / name, index=False, float_format="%.6f")
        files.append(name)

    for old in out.glob("*.csv"):           # outputs are rebuilt on every run
        old.unlink()
    H = off.months
    dates = [d.date() for d in rates.dates[1:H + 1]]
    save(_ear_frame(off_ear, tail_ear), "ear_by_horizon.csv")
    save(path_table(off), "mc_path_income.csv")
    rows = []
    for m in MEASURES:
        for h in outs[m]:
            for q, r in [*h["rows"].items(), ("baseline", h["baseline"])]:
                n = h["months"]
                rows.append(pd.DataFrame({"measure": m, "years": h["years"], "outcome": q if q == "baseline" else f"P{int(q):02d}",
                                          "month": np.arange(h["start"] + 1, h["start"] + n + 1), "date": dates[h["start"]:h["start"] + n],
                                          "interest_income": r["interest"], "reinvest_income": r["reinv"], "total_income": r["income"],
                                          "scheduled_principal": r["sched"], "prepaid_upb": r["prepaid"], "end_upb": r["end_upb"],
                                          "reinvest_balance_end": r["reinv_bal"], "cpr": r["cpr"], "reinvest_rate": r["sofr"],
                                          "mortgage_rate_30y": r["mtg"]}))
    save(pd.concat(rows), "outcome_monthly.csv")
    N = len(df)
    for name, d in details.items():
        save(pd.DataFrame({
            "outcome": name, "cusip": np.repeat(df["cusip"].to_numpy(), H), "month": np.tile(np.arange(1, H + 1), N),
            "date": np.tile(dates, N), "begin_upb": d["begin_upb"].ravel(), "scheduled_principal": d["sched"].ravel(),
            "prepaid_upb": d["prepaid"].ravel(), "end_upb": d["end_upb"].ravel(), "interest_income": d["interest"].ravel(),
            "reinvest_balance_begin": d["reinv_bal"].ravel(), "reinvest_rate": d["reinv_rate"].ravel(),
            "reinvest_income": d["reinv_income"].ravel(), "total_income": d["income"].ravel(), "cpr": d["cpr"].ravel(),
            "mortgage_rate_prior_month": d["mtg_rate"].ravel()}), f"cusip_monthly_{name}.csv")
    save(pd.concat([pd.DataFrame(t["rows"]).assign(measure=m, years=t["years"], tail_paths=t["tail_paths"])
                    for m in MEASURES for t in tails[m]]), "cohort_tail.csv")
    h = df.copy()
    for tag, d in (("baseline", details["baseline"]), ("mean", details["mean"])):
        h[f"{tag}_income_y1"] = d["income"][:, :12].sum(axis=1)
        h[f"{tag}_interest_y1"] = d["interest"][:, :12].sum(axis=1)
        h[f"{tag}_reinv_income_y1"] = d["reinv_income"][:, :12].sum(axis=1)
        h[f"{tag}_cpr_y1"] = cpr_from(d["prepaid"][:, :12].sum(axis=1), (d["begin_upb"] - d["sched"])[:, :12].sum(axis=1))
    save(h, "holdings_projected.csv")
    save(pd.DataFrame(checks), "checks.csv")
    save(pd.DataFrame(v6["rows"]), "validation_v6_shocks.csv")
    if v4["triggered"]:
        save(pd.DataFrame(v4["rows"]), "validation_v4_floor_rerun.csv")
    if v58:
        save(pd.DataFrame(v58["convergence"]), "validation_v5_convergence.csv")
        save(pd.DataFrame(v58["sensitivity"]), "validation_v8_calibrations.csv")
    return files


# --------------------------------------------------------------------------- dashboard
def _dashboard_data(cfg, pf, rates, prepay, off_ear, tail_ear, outs, tails, checks, port_wal, run_at, base, v4, v6, v58) -> dict:
    df = pf.df
    w = df["current_upb"].to_numpy(float)
    W = w.sum()
    wa = lambda c: float((df[c].to_numpy(float) * w).sum() / W) if W > 0 else float("nan")  # noqa: E731
    d = base.detail
    spot = rates.baseline[0]
    st = prepay.static(df["product"].to_numpy(), df["story_group"].to_numpy(), df["avg_loan_size"].to_numpy(float))
    incentive = 100 * (df["wac"].to_numpy(float) - (spot[rates.col("MTG_30Y")] + st["offset"]))
    px = df["market_price"].to_numpy(float)
    has_px = np.isfinite(px)
    y1 = slice(0, 12)
    holdings = pd.DataFrame({
        "cusip": df["cusip"], "pool": df["pool_number"], "product": df["product"], "coupon": df["coupon"], "wac": df["wac"],
        "wala": df["wala"], "wam": df["wam"], "upb": df["current_upb"], "loan_size": df["avg_loan_size"],
        "story": df["story_group"], "vintage": df["vintage"].astype(int), "price": np.where(has_px, px, None),
        "burnout": df["burnout_years"].round(2), "incentive_bp": incentive.round(0), "wal": df["proj_wal_years"].round(2),
        "cpr_y1": cpr_from(d["prepaid"][:, y1].sum(axis=1), (d["begin_upb"] - d["sched"])[:, y1].sum(axis=1)).round(2),
        "income_y1": d["income"][:, y1].sum(axis=1).round(0), "interest_y1": d["interest"][:, y1].sum(axis=1).round(0),
        "reinv_y1": d["reinv_income"][:, y1].sum(axis=1).round(0)})
    rec = rates.record
    kpi = {"upb": W, "positions": int(pf.n), "coupon": wa("coupon"), "wac": wa("wac"), "wala": wa("wala"), "wam": wa("wam"),
           "wal": port_wal, "loan_size": wa("avg_loan_size"),
           "market_value": float((np.where(has_px, px, 0) * w).sum() / 100) if has_px.any() else None,
           "price": float((np.where(has_px, px, 0) * w).sum() / w[has_px].sum()) if has_px.any() else None,
           "price_coverage": float(w[has_px].sum() / W) if W > 0 else 0.0,
           "mc_paths": rates.n_paths, "tail_paths": int(rec["simulation"]["tail_paths"]), "seed": rates.sim.seed,
           "spot_mtg": float(spot[rates.col("MTG_30Y")]), "spot_sofr": float(spot[rates.col("SOFR_1M")])}
    # baseline rate path by month, portfolio totals (dashboard "Baseline by month" tab)
    bu = d["begin_upb"]
    bus = bu.sum(axis=0)
    wavg = lambda x: np.divide((x[:, None] * bu).sum(axis=0), bus, out=np.full(bus.shape, np.nan), where=bus > 0)  # noqa: E731
    baseline_monthly = {
        "date": [str(x.date()) for x in rates.dates[1:rates.horizon + 1]],
        "upb": bus.round(2).tolist(), "wac": wavg(df["wac"].to_numpy(float)).round(5).tolist(),
        "coupon": wavg(df["coupon"].to_numpy(float)).round(5).tolist(),
        "reinv_bal": d["reinv_bal"].sum(axis=0).round(2).tolist(), "sofr": d["reinv_rate"][0].round(5).tolist(),
        "interest": d["interest"].sum(axis=0).round(2).tolist(), "reinv": d["reinv_income"].sum(axis=0).round(2).tolist(),
        "sched": d["sched"].sum(axis=0).round(2).tolist(), "prepaid": d["prepaid"].sum(axis=0).round(2).tolist()}
    p = prepay.p
    hw = hist.window(cfg["history"]["file"], pf.as_of, int(cfg["history"].get("months_shown", 12)))
    return {
        "meta": {"title": cfg["dashboard"].get("title", "MBS Income Risk"), "portfolio_name": cfg["portfolio"].get("name", ""),
                 "banner": (cfg["portfolio"].get("banner") or "").strip(), "as_of": str(pf.as_of.date()),
                 "rates_as_of": str(rates.as_of.date()), "run_at": run_at.strftime("%Y-%m-%d %H:%M UTC"),
                 "version": __version__, "horizon": rates.horizon, "years": rates.horizon // 12,
                 "portfolio_file": Path(cfg["portfolio"]["file"]).name, "cohort_by": list(cfg["risk"].get("cohort_by", [])),
                 "tail_pct": float(cfg["risk"].get("tail_pct", 5)), "history_file": Path(cfg["history"]["file"]).name,
                 "months_shown": int(cfg["history"].get("months_shown", 12)),
                 "setup": _setup(cfg, rates)},
        "kpi": kpi, "ear": off_ear, "ear_tail": tail_ear, "outcomes": outs, "tails": tails, "fan": fan(rates, rates.horizon),
        "holdings": holdings.to_dict("records"), "checks": checks, "baseline_monthly": baseline_monthly,
        "history": hw.where(pd.notna(hw), None).to_dict("records"),
        "rate_model": {"record": _portable(rec, Path(cfg["_root"]).parent), "calibration": rates.calibration_meta,
                       "validation": rates.rate_validation,
                       "v4": v4, "v6": v6, "v58": v58,
                       "baseline": {"dates": [str(x.date()) for x in rates.dates],
                                    "rates": {s: rates.baseline[:, rates.col(s)].round(4).tolist() for s in SERIES}}},
        "model": {
            "prepay": {k: p[k] for k in ("turnover_base", "lockin_slope", "lockin_floor", "turnover_ramp_months", "refi_max",
                                         "refi_mid", "refi_scale", "refi_ramp_months", "burnout_k", "tuning_multiplier",
                                         "turnover_size_elast", "refi_size_elast", "loan_size_ref")},
            "prepay_product": p["product"], "prepay_story_refi": p["story_refi"],
            "prepay_multiplier": prepay.multiplier, "prepay_desc": prepay.describe(), "prepay_source": p.get("source"),
            "burnout_source": df["burnout_source"].value_counts().to_dict(),
            "reinvest_series": cfg["income"].get("reinvest_rate_series", "SOFR_1M"),
            "reinvest_spread_bp": cfg["income"].get("reinvest_spread_bp", 0),
            "reinvest_scheduled": bool(cfg["income"].get("reinvest_scheduled_principal", False)),
        },
    }


def _portable(rec: dict, root: Path) -> dict:
    """The run record for the dashboard with file paths relative to the package folder (no machine-specific paths)."""
    out = json.loads(json.dumps(rec, default=str))

    def rel(x):
        if not x:
            return x
        try:
            return os.path.relpath(x, root).replace(os.sep, "/")
        except ValueError:                             # another drive (Windows)
            return Path(x).name
    out["calibration"]["file"] = rel(out["calibration"].get("file"))
    out["baseline"]["file"] = rel(out["baseline"].get("file"))
    if out["baseline"].get("swap"):
        out["baseline"]["swap"]["curve_file"] = rel(out["baseline"]["swap"].get("curve_file"))
    return out


def _setup(cfg, rates) -> dict:
    """Which approach this dashboard shows, and the link to the other one (README, "Two approaches")."""
    rec, d = rates.record, cfg["dashboard"]
    vr = rec.get("volatility") or {"type": "historical", "description": "Historical (official)"}
    out = {"label": d.get("label") or ("Official" if vr["type"] == "historical" else "Comparison"),
           "validated": vr["type"] == "historical", "vol_type": vr["type"], "vol_description": vr.get("description", ""),
           "baseline_type": rec["baseline"]["type"], "baseline_description": rec["baseline"]["description"], "compare": None}
    c = d.get("compare") or {}
    if c.get("dir"):
        other = Path(c["dir"]) if Path(c["dir"]).is_absolute() else (Path(cfg["_root"]) / c["dir"]).resolve()
        out["compare"] = {"label": c.get("label", "Other approach"),
                          "href": os.path.relpath(other / "index.html", Path(d["dir"])).replace(os.sep, "/"),
                          "built": (other / "data.js").exists()}
    return out


def _write_dashboard(cfg, data) -> Path:
    d = Path(cfg["dashboard"]["dir"])
    d.mkdir(parents=True, exist_ok=True)
    for f in TEMPLATES.iterdir():
        if f.is_file():
            shutil.copy(f, d / f.name)

    def clean(o):
        if isinstance(o, float) and not np.isfinite(o):
            return None
        if isinstance(o, dict):
            return {str(k): clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [clean(v) for v in o]
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            return clean(float(o))
        if isinstance(o, np.bool_):
            return bool(o)
        return o

    js = json.dumps(clean(data), separators=(",", ":"), allow_nan=False)
    (d / "data.js").write_text("window.DATA = " + js + ";\n")
    return d
