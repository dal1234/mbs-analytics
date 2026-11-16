"""Funding (liability) model: interest expense on a debt book along each rate path. Separate from asset income for now.

Debt file (config funding.file), one row per instrument or bucket:
    id, type, group, face ($m), rate (%), spread_bp, maturity (YYYY-MM), first_call (YYYY-MM)
    type = discount_note  fixed yield to maturity (rate)
           fixed          fixed coupon to maturity (rate)
           callable       fixed coupon (rate); the issuer may call it at any month from first_call
           floating       index (funding.index, 1M SOFR) + spread_bp, reset monthly

Each month t = 1..H, on every path (vectorised over paths and instruments):
    interest(t)    = face x rate(t) / 1200          floating: index at the start of month t + spread
    maturities     = instruments whose maturity month is t, paid at the end of month t
    calls          = callables past first_call whose coupon exceeds the cost of replacing their remaining term
                     (Treasury curve at that term + call_rule.refi_spread_bp, by default the callable new-issue
                     spread: called debt is replaced with callable debt, so the option premium is paid again) by more than call_rule.threshold_bp
    need(t)        = target balance - debt outstanding after maturities and calls
                     target = the starting balance (balance: constant) or none (balance: runoff)
    new issues     = need split across the types to restore the target mix (funding.target_mix: the starting
                     composition by default); priced at the end of month t:
                       discount_note  index + spread         floating  index + spread (reset monthly)
                       fixed          Treasury curve at the term + spread
                       callable       Treasury curve at the term + spread, callable after lockout_months
New issues earn from month t + 1. The Treasury curve is interpolated linearly in maturity across 1M SOFR (as the
1-month point), 1/2/5/10/30Y.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .rates import SERIES
from .risk import MEASURES, sums

TYPES = ("discount_note", "floating", "fixed", "callable")
CURVE = (("SOFR_1M", 1 / 12), ("TSY_1Y", 1.0), ("TSY_2Y", 2.0), ("TSY_5Y", 5.0), ("TSY_10Y", 10.0), ("TSY_30Y", 30.0))
DEFAULT_NEW = {"discount_note": {"term_months": 3, "spread_bp": 0},
               "floating": {"term_months": 24, "spread_bp": 10},
               "fixed": {"term_months": 36, "spread_bp": 15},
               "callable": {"term_months": 48, "lockout_months": 12, "spread_bp": 35}}


class DebtFileError(ValueError):
    pass


def _month_index(s: pd.Series, as_of: pd.Timestamp) -> pd.Series:
    """'YYYY-MM' (or a date) -> months after the as-of month (Oct 2026 = 1 for a Sep 2026 as-of)."""
    p = pd.to_datetime(s.astype(str), errors="coerce").dt.to_period("M")
    return (p - as_of.to_period("M")).apply(lambda x: x.n if pd.notna(x) else np.nan)


def load_debt(path: str, as_of: pd.Timestamp) -> pd.DataFrame:
    df = pd.read_csv(path)
    need = {"id", "type", "face", "maturity"}
    if need - set(df.columns):
        raise DebtFileError(f"debt file {path} lacks columns: {', '.join(sorted(need - set(df.columns)))}")
    for c in ("group", "rate", "spread_bp", "first_call"):
        if c not in df.columns:
            df[c] = np.nan
    df["group"] = df["group"].astype(object).where(df["group"].notna(), df["type"])
    errs = []
    bad = sorted(set(df["type"]) - set(TYPES))
    if bad:
        errs.append(f"unknown type(s) {bad}; use {', '.join(TYPES)}")
    if (df["face"] <= 0).any():
        errs.append("face must be positive")
    df["mat_m"] = _month_index(df["maturity"], as_of)
    df["call_m"] = _month_index(df["first_call"], as_of)
    if df["mat_m"].isna().any() or (df["mat_m"] < 1).any():
        errs.append("maturity must be a month after the as-of date (YYYY-MM): " + ", ".join(df.loc[df["mat_m"].isna() | (df["mat_m"] < 1), "id"].astype(str)))
    fixed = df["type"] != "floating"
    if df.loc[fixed, "rate"].isna().any():
        errs.append("rate is required for discount_note, fixed and callable rows")
    if df.loc[~fixed, "spread_bp"].isna().any():
        errs.append("spread_bp is required for floating rows")
    if df.loc[df["type"] == "callable", "call_m"].isna().any():
        errs.append("first_call is required for callable rows")
    if errs:
        raise DebtFileError(f"debt file {path}: " + "; ".join(errs))
    df["call_m"] = df["call_m"].where(df["type"] == "callable").fillna(10 ** 6).clip(lower=1)
    return df.reset_index(drop=True)


def _curve(c: np.ndarray, years: np.ndarray, cols: list) -> np.ndarray:
    """Linear interpolation in maturity. c: (P, len(SERIES)) rates at one month; years: (N,) -> (P, N)."""
    xs = np.array([y for _, y in CURVE])
    pts = c[:, cols]                                              # (P, 6)
    y = np.clip(np.asarray(years, float), xs[0], xs[-1])
    j = np.clip(np.searchsorted(xs, y, side="right") - 1, 0, len(xs) - 2)
    w = (y - xs[j]) / (xs[j + 1] - xs[j])
    return pts[:, j] * (1 - w) + pts[:, j + 1] * w


def project(debt: pd.DataFrame, cube: np.ndarray, H: int, fcfg: dict) -> dict:
    """Run the debt book along every path of ``cube`` (paths x months 0..H x SERIES). Returns (P, H[, K]) arrays."""
    P, N0, K = cube.shape[0], len(debt), len(TYPES)
    new = {t: {**DEFAULT_NEW[t], **((fcfg.get("new_issues") or {}).get(t) or {})} for t in TYPES}
    ix = SERIES.index(fcfg.get("index", "SOFR_1M"))
    cols = [SERIES.index(s) for s, _ in CURVE]
    cr = fcfg.get("call_rule") or {}
    thr, refi = float(cr.get("threshold_bp", 25)) / 100, float(cr.get("refi_spread_bp", new["callable"]["spread_bp"])) / 100
    tcode = debt["type"].map({t: i for i, t in enumerate(TYPES)}).to_numpy(int)
    face0 = debt["face"].to_numpy(float) * 1e6
    w0 = np.bincount(tcode, weights=face0, minlength=K)
    tm = fcfg.get("target_mix", "current")
    share = w0 / w0.sum() if tm in (None, "current") else np.array([float(tm.get(t, 0.0)) for t in TYPES])
    if share.sum() <= 0:
        raise ValueError("funding.target_mix must give a positive share to at least one type")
    share = share / share.sum()
    runoff = fcfg.get("balance", "constant") == "runoff"

    # instrument columns: the book, then one column per month and type for new issues
    Nmax = N0 + H * K
    face = np.zeros((P, Nmax)); face[:, :N0] = face0
    rate = np.zeros((P, Nmax)); rate[:, :N0] = debt["rate"].fillna(0).to_numpy(float)
    spread = np.zeros(Nmax); spread[:N0] = debt["spread_bp"].fillna(0).to_numpy(float) / 100
    isflt = np.zeros(Nmax, bool); isflt[:N0] = tcode == TYPES.index("floating")
    mat = np.full(Nmax, 10 ** 6); mat[:N0] = debt["mat_m"].to_numpy(int)
    call0 = np.full(Nmax, 10 ** 6); call0[:N0] = debt["call_m"].to_numpy(int)
    typ = np.zeros(Nmax, int); typ[:N0] = tcode
    isnew = np.zeros(Nmax, bool)
    target = float(face0.sum())
    onehot = np.zeros((Nmax, K)); onehot[np.arange(N0), tcode] = 1.0
    out = {k: np.zeros((P, H, K)) for k in ("interest", "balance", "matured", "called", "issued")}
    out["interest_new"] = np.zeros((P, H))
    out["index_rate"] = cube[:, :H, ix].copy()
    for m in range(1, H + 1):
        t = m - 1
        r_now = np.where(isflt[None, :], cube[:, m - 1, ix][:, None] + spread[None, :], rate)
        inte = face * r_now / 1200.0
        out["interest"][:, t] = inte @ onehot
        out["balance"][:, t] = face @ onehot
        out["interest_new"][:, t] = inte[:, isnew].sum(axis=1)
        # maturities at the end of the month
        mm = (mat == m)
        if mm.any():
            out["matured"][:, t] = (face * mm[None, :]) @ onehot
            face[:, mm] = 0.0
        # calls: refinance the remaining term when that is cheaper by more than the threshold
        cand = (typ == TYPES.index("callable")) & (call0 <= m) & (mat > m)
        if cand.any():
            ci = np.where(cand)[0]
            cost = _curve(cube[:, m], (mat[ci] - m) / 12.0, cols) + refi
            hit = (face[:, ci] > 0) & (rate[:, ci] - cost > thr)
            if hit.any():
                amt = np.where(hit, face[:, ci], 0.0)
                out["called"][:, t, TYPES.index("callable")] = amt.sum(axis=1)
                face[:, ci] = np.where(hit, 0.0, face[:, ci])
        # new issues restore the target balance and mix
        bal_k = face @ onehot
        gap = np.zeros(P) if runoff else np.maximum(target - bal_k.sum(axis=1), 0.0)
        short = np.maximum(target * share[None, :] - bal_k, 0.0)
        ssum = short.sum(axis=1, keepdims=True)
        alloc = np.where(ssum > 0, short / np.where(ssum > 0, ssum, 1.0), share[None, :]) * gap[:, None]
        for k, tname in enumerate(TYPES):
            j = N0 + t * K + k
            nc = new[tname]
            face[:, j] = alloc[:, k]
            mat[j] = m + int(nc["term_months"])
            typ[j], isnew[j], onehot[j, k] = k, True, 1.0
            sp = float(nc["spread_bp"]) / 100
            if tname in ("discount_note", "floating"):
                if tname == "floating":
                    isflt[j], spread[j] = True, sp
                else:
                    rate[:, j] = cube[:, m, ix] + sp
            else:
                rate[:, j] = _curve(cube[:, m], np.array([int(nc["term_months"]) / 12.0]), cols)[:, 0] + sp
                if tname == "callable":
                    call0[j] = m + int(nc.get("lockout_months", 12))
        out["issued"][:, t] = alloc
    return out


def _annual(x: np.ndarray, H: int) -> np.ndarray:
    Y = int(np.ceil(H / 12))
    return np.stack([x[:, 12 * y:min(12 * (y + 1), H)].sum(axis=1) for y in range(Y)], axis=1)


def run(fcfg: dict, rates, scen_cube: np.ndarray, catalog: list, H: int, log=print) -> dict | None:
    """Scenarios (the rate-specific cube) and the official Monte Carlo paths through the debt book."""
    if not fcfg or not fcfg.get("enabled", True) or not fcfg.get("file"):
        return None
    debt = load_debt(fcfg["file"], rates.as_of)
    log(f"Funding    {fcfg['file']}: {len(debt)} rows, ${debt['face'].sum() / 1e3:,.1f}bn; "
        f"{len(catalog)} scenarios and {rates.n_paths} paths ...")
    S = project(debt, scen_cube, H, fcfg)
    MC = project(debt, rates.mc, H, fcfg)
    ids = [c["id"] for c in catalog]
    Y = int(np.ceil(H / 12))
    tot = S["interest"].sum(axis=2)                                   # (S, H)
    mc_tot = MC["interest"].sum(axis=2)
    bal = S["balance"].sum(axis=2)
    pct = [5, 25, 50, 75, 95]
    dist = {ms: [{"years": y, "base": float(sums(tot[ids.index("BASE")], ms, y, H)),
                  "mean": float(sums(mc_tot, ms, y, H).mean()),
                  "pct": {str(q): float(np.percentile(sums(mc_tot, ms, y, H), q)) for q in pct},
                  "hist": np.histogram(sums(mc_tot, ms, y, H), bins=30)[0].tolist(),
                  "edges": np.histogram(sums(mc_tot, ms, y, H), bins=30)[1].tolist()} for y in range(1, Y + 1)]
            for ms in MEASURES}
    mc_pct = {sid: {ms: [float(np.mean(sums(mc_tot, ms, y, H) <= sums(tot[i], ms, y, H))) for y in range(1, Y + 1)]
                    for ms in MEASURES} for i, sid in enumerate(ids)}
    r0 = lambda x: np.round(np.asarray(x, float), 0).tolist()   # noqa: E731
    r4 = lambda x: np.round(np.asarray(x, float), 4).tolist()   # noqa: E731
    with np.errstate(divide="ignore", invalid="ignore"):
        cof = np.where(bal > 0, 1200.0 * tot / bal, np.nan)            # cost of funds, % a year
    d = debt.copy()
    d["rate_now"] = np.where(d["type"] == "floating", rates.baseline[0, rates.col(fcfg.get("index", "SOFR_1M"))] + d["spread_bp"] / 100, d["rate"])
    groups = [{"group": g, "type": x["type"].iloc[0], "types": sorted(x["type"].unique(), key=TYPES.index), "face": float(x["face"].sum() * 1e6), "rows": int(len(x)),
               "rate": float((x["face"] * x["rate_now"]).sum() / x["face"].sum()),
               "first": str(x["maturity"].min()), "last": str(x["maturity"].max()),
               "callable": float(x.loc[x["type"] == "callable", "face"].sum() * 1e6)}
              for g, x in d.groupby("group", sort=False)]
    # contractual maturities and first call dates by quarter (existing book), next 5 years + later
    q = lambda mth: np.minimum((np.asarray(mth) - 1) // 3, 20)   # noqa: E731
    prof = {t: np.bincount(q(d.loc[d["type"] == t, "mat_m"].astype(int)), weights=d.loc[d["type"] == t, "face"] * 1e6, minlength=21).tolist() for t in TYPES}
    calls_q = np.bincount(q(d.loc[d["type"] == "callable", "call_m"].astype(int)), weights=d.loc[d["type"] == "callable", "face"] * 1e6, minlength=21).tolist()
    new = {t: {**DEFAULT_NEW[t], **((fcfg.get("new_issues") or {}).get(t) or {})} for t in TYPES}
    w0 = np.bincount(debt["type"].map({t: i for i, t in enumerate(TYPES)}), weights=debt["face"], minlength=4)
    tm = fcfg.get("target_mix", "current")
    share = (w0 / w0.sum()) if tm in (None, "current") else np.array([float(tm.get(t, 0)) for t in TYPES])
    return {
        "name": fcfg.get("name", ""), "source": fcfg.get("source", ""), "types": list(TYPES), "years": Y, "months": H,
        "dates": [str(x.date()) for x in rates.dates], "book": groups, "rows": int(len(debt)),
        "face": float(debt["face"].sum() * 1e6),
        "rate_now": float((d["face"] * d["rate_now"]).sum() / d["face"].sum()),
        "wam_months": float((d["mat_m"] * d["face"]).sum() / d["face"].sum()),
        "reprice_12m": float(d.loc[(d["type"].isin(["floating", "discount_note"])) | (d["mat_m"] <= 12), "face"].sum() / d["face"].sum()),
        "profile": {"by_type": prof, "first_calls": calls_q},
        "policy": {"balance": fcfg.get("balance", "constant"), "index": fcfg.get("index", "SOFR_1M"),
                   "target_mix": dict(zip(TYPES, np.round(share / share.sum(), 4).tolist())),
                   "target_mix_source": "starting composition" if tm in (None, "current") else "config",
                   "new_issues": new, "call_rule": {"threshold_bp": float((fcfg.get("call_rule") or {}).get("threshold_bp", 25)),
                                                      "refi_spread_bp": float((fcfg.get("call_rule") or {}).get("refi_spread_bp", new["callable"]["spread_bp"]))}},
        "reported": fcfg.get("reported") or {},
        "scenarios": {sid: {"interest": r0(_annual(S["interest"][i].sum(axis=1)[None], H)[0]),
                            "by_type": {t: r0(_annual(S["interest"][i, :, k][None], H)[0]) for k, t in enumerate(TYPES)},
                            "issued": {t: r0(_annual(S["issued"][i, :, k][None], H)[0]) for k, t in enumerate(TYPES)},
                            "matured": r0(_annual(S["matured"][i].sum(axis=1)[None], H)[0]),
                            "called": r0(_annual(S["called"][i].sum(axis=1)[None], H)[0]),
                            "m_interest": r0(tot[i]), "m_interest_new": r0(S["interest_new"][i]),
                            "m_balance": {t: r0(S["balance"][i, :, k]) for k, t in enumerate(TYPES)},
                            "m_cof": r4(cof[i]), "m_called": r0(S["called"][i].sum(axis=1)),
                            "m_index": r4(S["index_rate"][i])} for i, sid in enumerate(ids)},
        "mc": dist, "mc_pct": mc_pct, "n_paths": int(rates.n_paths),
        "_S": S, "_debt": debt, "_ids": ids,
    }


def frames(res: dict) -> dict:
    """CSV outputs: funding_scenarios.csv (annual) and funding_monthly.csv."""
    H, ids, S = res["months"], res["_ids"], res["_S"]
    base = res["scenarios"]["BASE"]["interest"]
    rows = [{"scenario_id": sid, "year": y + 1, "interest_expense": v["interest"][y],
             **{f"interest_{t}": v["by_type"][t][y] for t in TYPES}, "change_vs_base": v["interest"][y] - base[y],
             "matured": v["matured"][y], "called": v["called"][y], **{f"issued_{t}": v["issued"][t][y] for t in TYPES},
             "mc_percentile_cumulative": res["mc_pct"][sid]["cumulative"][y]}
            for sid, v in res["scenarios"].items() for y in range(res["years"])]
    mon = pd.concat([pd.DataFrame({"scenario_id": sid, "month": np.arange(1, H + 1), "date": res["dates"][1:H + 1],
                                   "interest_expense": S["interest"][i].sum(axis=1), "interest_new_issues": S["interest_new"][i],
                                   **{f"balance_{t}": S["balance"][i, :, k] for k, t in enumerate(TYPES)},
                                   "matured": S["matured"][i].sum(axis=1), "called": S["called"][i].sum(axis=1),
                                   **{f"issued_{t}": S["issued"][i, :, k] for k, t in enumerate(TYPES)}})
                     for i, sid in enumerate(ids)])
    return {"funding_scenarios.csv": pd.DataFrame(rows), "funding_monthly.csv": mon}


def public(res: dict | None) -> dict | None:
    return None if res is None else {k: v for k, v in res.items() if not k.startswith("_")}
