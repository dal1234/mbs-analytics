"""Rate-specific (deterministic) scenarios: income on each scenario path, attribution, overlays and cohorts.

The scenario paths come from ratemodel.scenarios through RateSet.scenario_cube: the same baseline, calibration and
floor as the Monte Carlo, so the base scenario's income is the EaR baseline income exactly. Income runs through the
same engine (engine.project), one path per scenario.

Attribution of each scenario's income change against the base (exact: the parts add up to the total)
    prepay_coupon   coupon lost (or kept) because speeds change: the scenario mortgage rate with base SOFR,
                    interest line
    prepay_reinv    the same run, reinvestment line: more (or less) prepaid principal reinvested at the base rate
    reinv_rate      the scenario reinvestment rate with the base mortgage rate: base balances, new rate
    interaction     total minus the three (faster prepayments reinvested at the changed rate)

Overlays (applied to every scenario): a mortgage spread (30Y mortgage rate only, phased in) and a multiplier on every
prepayment speed. Each combination is a separate run of all scenarios; the dashboard swaps the grid to it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .engine import cpr_from, project
from .prepay import SCurvePrepay
from .rates import SERIES
from .risk import MEASURES, sums

CURVE = [("SOFR_1M", "1M SOFR"), ("TSY_1Y", "1Y"), ("TSY_2Y", "2Y"), ("TSY_5Y", "5Y"), ("TSY_10Y", "10Y"), ("TSY_30Y", "30Y")]
PARTS = (("prepay_coupon", "Coupon lost to prepayment"), ("prepay_reinv", "Prepaid principal reinvested"),
         ("reinv_rate", "Reinvestment rate"), ("interaction", "Interaction"))


def _annual(monthly: np.ndarray, H: int) -> np.ndarray:
    """(..., H) monthly -> (..., Y) sums by year."""
    Y = int(np.ceil(H / 12))
    return np.stack([monthly[..., 12 * y:min(12 * (y + 1), H)].sum(axis=-1) for y in range(Y)], axis=-1)


def okey(spread_bp: float, mult: float) -> str:
    return f"{int(round(spread_bp))}|{mult:.2f}"


def run(df, rates, prepay: SCurvePrepay, inc: dict, H: int, cfg: dict, codes: np.ndarray, labels: list,
        mc_income: np.ndarray, log=print) -> dict:
    """All rate-specific results. ``cfg`` = the model.yaml ``scenarios`` block; ``mc_income`` = (P, H) official
    Monte Carlo income, used to place each scenario in the simulated distribution."""
    cfg = cfg or {}
    cat, cube = rates.scenario_cube(cfg)
    S, Y = len(cat), int(np.ceil(H / 12))
    ids = [c["id"] for c in cat]
    b = ids.index("BASE")
    log(f"Projecting {S} rate-specific scenarios ...")
    proj = project(df, cube, rates, prepay, inc, H, groups=codes)
    a = proj.agg
    imtg, irr = rates.col("MTG_30Y"), rates.col(inc.get("reinvest_rate_series", "SOFR_1M"))
    spread = float(inc.get("reinvest_spread_bp", 0.0)) / 100.0

    # attribution: one driver at a time from the base path (the engine reads only these two rate columns)
    others = [i for i in range(S) if i != b]
    hp = np.repeat(cube[b:b + 1], len(others), axis=0)
    hp[:, :, imtg] = cube[others][:, :, imtg]
    hr = np.repeat(cube[b:b + 1], len(others), axis=0)
    hr[:, :, irr] = cube[others][:, :, irr]
    att = {}
    if others:
        ap = project(df, hp, rates, prepay, inc, H).agg
        ar = project(df, hr, rates, prepay, inc, H).agg
        for j, i in enumerate(others):
            parts = {"prepay_coupon": ap["interest"][j] - a["interest"][b], "prepay_reinv": ap["reinv_income"][j] - a["reinv_income"][b],
                     "reinv_rate": ar["income"][j] - a["income"][b]}
            parts["interaction"] = a["income"][i] - a["income"][b] - sum(parts.values())
            att[ids[i]] = {k: _annual(v, H) for k, v in parts.items()}

    # overlays: every scenario under each mortgage spread x prepayment multiplier
    spreads = [float(x) for x in (cfg.get("overlays") or {}).get("spread_bp", [-50, -25, 0, 25, 50])]
    mults = [float(x) for x in (cfg.get("overlays") or {}).get("prepay_mult", [0.75, 1.0, 1.25])]
    ramp = int((cfg.get("overlays") or {}).get("spread_ramp_months", 3))
    grid = {}
    combos = [(sp, m) for m in mults for sp in spreads if not (sp == 0 and m == 1.0)]
    if combos:
        log(f"Overlays: {len(combos)} spread x prepayment combinations x {S} scenarios ...")
    for m in mults:
        sps = [sp for sp in spreads if not (sp == 0 and m == 1.0)]
        if not sps:
            continue
        stacked = np.concatenate([rates.with_mortgage_spread(cube, sp, ramp) for sp in sps], axis=0)
        pr = SCurvePrepay(prepay.p, prepay.multiplier * m)
        oa = project(df, stacked, rates, pr, inc, H).agg
        inc_y = _annual(oa["income"], H)
        cpr_y = cpr_from(_annual(oa["prepaid"], H), _annual(oa["prepay_base"], H))
        for k, sp in enumerate(sps):
            grid[okey(sp, m)] = {sid: {"income": inc_y[k * S + i].tolist(), "cpr": cpr_y[k * S + i].tolist()}
                                 for i, sid in enumerate(ids)}

    # where each scenario falls in the Monte Carlo distribution of the same horizon
    mc_pct = {sid: {ms: [float(np.mean(sums(mc_income, ms, y, H) <= sums(a["income"][i], ms, y, H)))
                         for y in range(1, Y + 1)] for ms in MEASURES} for i, sid in enumerate(ids)}

    ann = {k: _annual(a[k], H) for k in ("income", "interest", "reinv_income", "prepaid", "sched", "prepay_base")}
    cpr_y = cpr_from(ann["prepaid"], ann["prepay_base"])
    begin_y = a["begin_upb"][:, ::12][:, :Y]
    end_y = a["end_upb"][:, [min(12 * (y + 1), H) - 1 for y in range(Y)]]
    cpr_m = cpr_from(a["prepaid"], a["prepay_base"])
    reinv_rate = cube[:, :H, irr] + spread
    dates = [str(x.date()) for x in rates.dates]

    # cohorts: income by scenario, cohort and year (the drill-down follows any scenario)
    g = proj.group
    upb = df["current_upb"].to_numpy(float)
    G = len(labels)
    cupb = np.bincount(codes, weights=upb, minlength=G)
    ccpn = np.bincount(codes, weights=upb * df["coupon"].to_numpy(float), minlength=G) / np.where(cupb > 0, cupb, 1)
    cwac = np.bincount(codes, weights=upb * df["wac"].to_numpy(float), minlength=G) / np.where(cupb > 0, cupb, 1)

    hist = rates.history
    h = hist.loc[hist.index < rates.as_of.replace(day=1)].tail(24) if len(hist) else hist
    curve_cols = [rates.col(s) for s, _ in CURVE]
    r2 = lambda x: np.round(np.asarray(x, float), 4).tolist()   # noqa: E731
    r0 = lambda x: np.round(np.asarray(x, float), 0).tolist()   # noqa: E731
    cal = rates.official
    return {
        "base_id": "BASE", "years": Y, "months": H, "dates": dates, "catalog": cat,
        "reinvest_series": inc.get("reinvest_rate_series", "SOFR_1M"),
        "beta_mort": float(cal.beta_mort), "loadings": {k: list(v) for k, v in cal.tenor_loadings.items()},
        "baseline_type": rates.record.get("baseline", {}).get("type", ""),
        "rates": {sid: {"sofr": r2(cube[i, :, rates.col("SOFR_1M")]), "t10": r2(cube[i, :, rates.col("TSY_10Y")]),
                        "mtg": r2(cube[i, :, imtg])} for i, sid in enumerate(ids)},
        "curve_tenors": [l for _, l in CURVE], "curve_today": r2(cube[b, 0, curve_cols]),
        "curve_12m": {sid: r2(cube[i, min(12, H), curve_cols]) for i, sid in enumerate(ids)},
        "history": {"dates": [str(x.date()) for x in h.index],
                    **{k: r2(h[c].to_numpy(float)) if c in h else [] for k, c in (("sofr", "SOFR_1M"), ("t10", "TSY_10Y"), ("mtg", "MTG_30Y"))}},
        "annual": {sid: {"income": r0(ann["income"][i]), "interest": r0(ann["interest"][i]), "reinv": r0(ann["reinv_income"][i]),
                         "prepaid": r0(ann["prepaid"][i]), "sched": r0(ann["sched"][i]), "begin_upb": r0(begin_y[i]),
                         "end_upb": r0(end_y[i]), "cpr": r2(cpr_y[i])} for i, sid in enumerate(ids)},
        "monthly": {sid: {"interest": r0(a["interest"][i]), "reinv": r0(a["reinv_income"][i]), "end_upb": r0(a["end_upb"][i]),
                          "reinv_bal": r0(a["reinv_end"][i]), "prepaid": r0(a["prepaid"][i]), "sched": r0(a["sched"][i]),
                          "cpr": r2(cpr_m[i]), "reinv_rate": r2(reinv_rate[i])} for i, sid in enumerate(ids)},
        "attribution": {sid: {k: r0(v) for k, v in p.items()} for sid, p in att.items()},
        "attribution_parts": [list(p) for p in PARTS],
        "overlays": {"spread_bp": spreads, "prepay_mult": mults, "spread_ramp_months": ramp, "grid": grid},
        "mc_pct": mc_pct,
        "cohorts": {"labels": list(labels), "by": None, "positions": np.bincount(codes, minlength=G).tolist(),
                    "upb": r0(cupb), "coupon": r2(ccpn), "wac": r2(cwac),
                    "income": {sid: r0(g["income"][i]) for i, sid in enumerate(ids)},
                    "prepaid": {sid: r0(g["prepaid"][i]) for i, sid in enumerate(ids)},
                    "prepay_base": {sid: r0(g["prepay_base"][i]) for i, sid in enumerate(ids)}},
        "_cube": cube, "_proj": proj,
    }


def frames(res: dict, rates) -> dict:
    """CSV outputs: name -> DataFrame."""
    cat = {c["id"]: c for c in res["catalog"]}
    ids = list(cat)
    cube, H, Y = res["_cube"], res["months"], res["years"]
    base = res["annual"][res["base_id"]]
    out = {}
    out["scenario_rates.csv"] = pd.concat([pd.DataFrame({"scenario_id": sid, "scenario": cat[sid]["name"], "month": np.arange(H + 1),
                                                         "date": res["dates"][:H + 1], **{s: cube[i, :, rates.col(s)] for s in SERIES}})
                                           for i, sid in enumerate(ids)])
    rows = []
    for sid in ids:
        an = res["annual"][sid]
        cum = np.cumsum(an["income"])
        bcum = np.cumsum(base["income"])
        for y in range(Y):
            rows.append({"scenario_id": sid, "scenario": cat[sid]["name"], "family": cat[sid]["family"], "year": y + 1,
                         "income": an["income"][y], "interest_income": an["interest"][y], "reinvest_income": an["reinv"][y],
                         "prepaid_upb": an["prepaid"][y], "scheduled_principal": an["sched"][y], "begin_upb": an["begin_upb"][y],
                         "end_upb": an["end_upb"][y], "cpr": an["cpr"][y], "income_change_vs_base": an["income"][y] - base["income"][y],
                         "cumulative_income": cum[y], "cumulative_change_vs_base": cum[y] - bcum[y],
                         "mc_percentile_cumulative": res["mc_pct"][sid]["cumulative"][y],
                         "mc_percentile_annual": res["mc_pct"][sid]["annual"][y]})
    out["scenario_income.csv"] = pd.DataFrame(rows)
    out["scenario_monthly.csv"] = pd.concat([pd.DataFrame({"scenario_id": sid, "month": np.arange(1, H + 1), "date": res["dates"][1:H + 1],
                                                           "interest_income": m["interest"], "reinvest_income": m["reinv"],
                                                           "total_income": np.add(m["interest"], m["reinv"]), "prepaid_upb": m["prepaid"],
                                                           "scheduled_principal": m["sched"], "end_upb": m["end_upb"],
                                                           "reinvest_balance_end": m["reinv_bal"], "cpr": m["cpr"], "reinvest_rate": m["reinv_rate"]})
                                             for sid, m in res["monthly"].items()])
    out["scenario_attribution.csv"] = pd.DataFrame([{"scenario_id": sid, "scenario": cat[sid]["name"], "year": y + 1,
                                                     **{k: p[k][y] for k, _ in PARTS}, "total": sum(p[k][y] for k, _ in PARTS)}
                                                    for sid, p in res["attribution"].items() for y in range(Y)])
    out["scenario_overlays.csv"] = pd.DataFrame([{"spread_bp": int(k.split("|")[0]), "prepay_multiplier": float(k.split("|")[1]),
                                                  "scenario_id": sid, "year": y + 1, "income": v["income"][y], "cpr": v["cpr"][y]}
                                                 for k, g in res["overlays"]["grid"].items() for sid, v in g.items() for y in range(Y)])
    return out


def public(res: dict) -> dict:
    """The dashboard payload (no arrays kept for the CSVs)."""
    return {k: v for k, v in res.items() if not k.startswith("_")}
