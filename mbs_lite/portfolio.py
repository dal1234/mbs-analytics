"""Portfolio file: one row per CUSIP held on one as-of date.

Required columns
    cusip          security identifier (text)
    as_of_date     the date the balances are as of (YYYY-MM-DD); one date for the whole file
    product        30Y | 20Y | 15Y | 10Y  (original term of the loans)
    coupon         pass-through (net) coupon, percent, e.g. 5.5
    wac            weighted-average gross loan rate, percent, e.g. 6.47
    wala           weighted-average loan age, months
    wam            weighted-average remaining term, months
    current_upb    current face held (original face x pool factor), dollars

Optional columns (default when blank or missing)
    avg_loan_size  current average loan balance in the pool, dollars (300,000: no size effect)
    story_group    Generic | Spec | Geo | LLB (Generic)
    burnout_years  years the pool has spent more than 50 bp in the money (estimated from rate history)
    vintage        issue year (as-of year minus WALA)
    market_price   price per 100 face, used only for the market value shown on the Portfolio page
    pool_number    shown on the Portfolio page
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

REQUIRED = ["cusip", "as_of_date", "product", "coupon", "wac", "wala", "wam", "current_upb"]
OPTIONAL = {"avg_loan_size": 300000.0, "story_group": "Generic", "burnout_years": np.nan, "vintage": np.nan,
            "market_price": np.nan, "pool_number": ""}
PRODUCTS = ("30Y", "20Y", "15Y", "10Y")
STORIES = ("Generic", "Spec", "Geo", "LLB")
PRODUCT_TERM = {"30Y": 360, "20Y": 240, "15Y": 180, "10Y": 120}


@dataclass
class Portfolio:
    df: pd.DataFrame
    as_of: pd.Timestamp
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.df)


def _num(df: pd.DataFrame, col: str, errors: list) -> None:
    v = pd.to_numeric(df[col], errors="coerce")
    bad = v.isna() & df[col].notna() & (df[col].astype(str).str.strip() != "")
    if bad.any():
        errors.append(f"{col}: {int(bad.sum())} value(s) are not numbers, e.g. CUSIP "
                      f"{df.loc[bad, 'cusip'].iloc[0]} has {df.loc[bad, col].iloc[0]!r}")
    df[col] = v


def load_portfolio(path: str) -> Portfolio:
    raw = pd.read_csv(path, dtype={"cusip": str, "pool_number": str})
    raw.columns = [c.strip().lower() for c in raw.columns]
    errors, warnings = [], []

    missing = [c for c in REQUIRED if c not in raw.columns]
    if missing:
        errors.append(f"missing required column(s): {', '.join(missing)}")
        return Portfolio(raw, pd.NaT, errors, warnings)
    df = raw.copy()
    df["cusip"] = df["cusip"].astype(str).str.strip()
    for c in ("coupon", "wac", "wala", "wam", "current_upb"):
        _num(df, c, errors)
    for c, default in OPTIONAL.items():
        if c not in df.columns:
            df[c] = default
    for c in ("avg_loan_size", "burnout_years", "vintage", "market_price"):
        _num(df, c, errors)

    # blanks in required fields
    for c in REQUIRED:
        blank = df[c].isna() | (df[c].astype(str).str.strip() == "")
        if blank.any():
            errors.append(f"{c}: {int(blank.sum())} blank value(s), e.g. row {int(np.flatnonzero(blank)[0]) + 2}")

    # one as-of date
    dates = pd.to_datetime(df["as_of_date"], errors="coerce")
    if dates.isna().any():
        errors.append(f"as_of_date: {int(dates.isna().sum())} value(s) are not dates (use YYYY-MM-DD)")
    uniq = dates.dropna().unique()
    if len(uniq) > 1:
        errors.append(f"as_of_date: the file has {len(uniq)} different dates; a portfolio is one date "
                      f"({', '.join(str(pd.Timestamp(d).date()) for d in uniq[:4])} ...)")
    as_of = pd.Timestamp(uniq[0]) if len(uniq) else pd.NaT

    # product and story codes
    df["product"] = df["product"].astype(str).str.strip().str.upper()
    badp = ~df["product"].isin(PRODUCTS)
    if badp.any():
        errors.append(f"product: {int(badp.sum())} value(s) not in {', '.join(PRODUCTS)}, "
                      f"e.g. {df.loc[badp, 'product'].iloc[0]!r}")
    df["story_group"] = df["story_group"].fillna("Generic").astype(str).str.strip().replace({"": "Generic"})
    bads = ~df["story_group"].isin(STORIES)
    if bads.any():
        warnings.append(f"story_group: {int(bads.sum())} value(s) not in {', '.join(STORIES)} treated as Generic")
        df.loc[bads, "story_group"] = "Generic"

    # ranges
    def chk(mask, msg, err=True):
        if mask.any():
            (errors if err else warnings).append(f"{msg}: {int(mask.sum())} CUSIP(s), e.g. {df.loc[mask, 'cusip'].iloc[0]}")

    chk((df["coupon"] < 0) | (df["coupon"] > 15), "coupon outside 0-15%")
    chk((df["wac"] < 0) | (df["wac"] > 20), "wac outside 0-20%")
    chk(df["wac"] < df["coupon"], "wac below the coupon (servicing and guarantee fees are usually 0.25-1.5%)", err=False)
    chk(df["current_upb"] < 0, "negative current_upb")
    chk(df["current_upb"] == 0, "zero current_upb (paid off; contributes nothing)", err=False)
    chk((df["wam"] < 0) | (df["wam"] > 480), "wam outside 0-480 months")
    chk(df["wala"] < 0, "negative wala")
    if not badp.any():
        term = df["product"].map(PRODUCT_TERM)
        chk(df["wala"] + df["wam"] > term + 24, "wala + wam is more than the product term + 24 months", err=False)
    dup = df["cusip"].duplicated(keep=False)
    if dup.any():
        warnings.append(f"{int(dup.sum())} rows share a CUSIP; each row is projected as its own position "
                        f"(e.g. {df.loc[dup, 'cusip'].iloc[0]})")

    # defaults for optional fields
    df["avg_loan_size"] = df["avg_loan_size"].where(df["avg_loan_size"] > 0, OPTIONAL["avg_loan_size"])
    if pd.notna(as_of):
        derived = (as_of.year - np.floor(df["wala"].fillna(0) / 12)).astype(int)
        df["vintage"] = df["vintage"].fillna(derived)
    df["pool_number"] = df["pool_number"].fillna("").astype(str)
    df = df.reset_index(drop=True)
    return Portfolio(df, as_of, errors, warnings)
