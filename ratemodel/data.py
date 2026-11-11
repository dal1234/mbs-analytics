"""Public rate history (FRED) from the local cache in data/fred, for calibration and for the as-of curve.

Each series is one CSV named <FRED id>.csv with header `observation_date,<FRED id>`, rates in percent ('.' or blank =
missing). With refresh=True a missing or stale file is downloaded from FRED first; otherwise the cache is used as is,
so the model runs offline and can be fed from internal data in the same layout.

Calibration history (whitepaper section 5): monthly, decimals, columns short, y10, mort, y1, y2, y5, y30.
    short  overnight SOFR from 2018-04, effective fed funds (DFF) before; monthly AVERAGE
    others last observation of the month (Treasury CMT, Freddie Mac PMMS)
As-of curve: the Treasury CMT curve (1M-30Y, plus 20Y as a bootstrap knot), 1-month SOFR (30-day average SOFR as the
1M proxy; 30-day average EFFR before 2018) and the 30y mortgage rate, all on the last date at or before the as-of
date with a complete Treasury curve. Nothing after the as-of date is read.
"""
from __future__ import annotations

import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
HISTORY_SERIES = {"sofr": "SOFR", "effr": "DFF", "y1": "DGS1", "y2": "DGS2", "y5": "DGS5", "y10": "DGS10",
                  "y30": "DGS30", "mort": "MORTGAGE30US"}
CURVE_SERIES = {"1M": "DGS1MO", "3M": "DGS3MO", "6M": "DGS6MO", "1Y": "DGS1", "2Y": "DGS2", "3Y": "DGS3",
                "5Y": "DGS5", "7Y": "DGS7", "10Y": "DGS10", "20Y": "DGS20", "30Y": "DGS30"}
SOFR_1M_PROXY, EFFR_DAILY = "SOFR30DAYAVG", "EFFR"
SOFR_START = "2018-04"


def read_series(sid: str, data_dir: str, refresh: bool = False, retries: int = 3) -> pd.Series:
    path = Path(data_dir) / f"{sid}.csv"
    if refresh or not path.exists():
        err = None
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(FRED_URL.format(sid=sid), timeout=60) as r:
                    text = r.read().decode("utf-8")
                if not text.startswith("observation_date"):
                    raise ValueError(f"unexpected FRED payload for {sid}")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
                break
            except Exception as e:  # noqa: BLE001
                err = e
                time.sleep(2 * (attempt + 1))
        else:
            if not path.exists():
                raise RuntimeError(f"could not download {sid} and no cached file {path}: {err}")
            print(f"  warning: download of {sid} failed ({err}); using the cached file")
    df = pd.read_csv(path, na_values=".")
    s = pd.Series(pd.to_numeric(df.iloc[:, 1], errors="coerce").values, index=pd.to_datetime(df.iloc[:, 0]), name=sid)
    return s.dropna()


def load_history(data_dir: str, as_of, refresh: bool = False) -> pd.DataFrame:
    """Monthly calibration history in decimals through the last complete month at or before ``as_of``."""
    as_of = pd.Timestamp(as_of)
    raw = {k: read_series(sid, data_dir, refresh).loc[:as_of] for k, sid in HISTORY_SERIES.items()}
    m_avg = lambda s: s.resample("ME").mean()   # noqa: E731
    m_last = lambda s: s.resample("ME").last()  # noqa: E731
    short = m_avg(raw["effr"])
    sofr = m_avg(raw["sofr"])
    sofr = sofr[sofr.index >= pd.Timestamp(SOFR_START)]
    short.loc[sofr.index.intersection(short.index)] = sofr
    short = short.combine_first(sofr)
    out = {"short": short, "y10": m_last(raw["y10"]), "mort": m_last(raw["mort"])}
    for k in ("y1", "y2", "y5", "y30"):
        out[k] = m_last(raw[k])
    hist = pd.DataFrame(out) / 100.0
    last_complete = as_of if as_of == as_of + pd.offsets.MonthEnd(0) else as_of - pd.offsets.MonthEnd(1)
    hist = hist[hist.index <= last_complete]
    hist.index = hist.index.to_period("M")
    return hist.dropna(subset=["short", "y10", "mort"])


@dataclass
class Spot:
    date: pd.Timestamp            # actual curve date used (last complete curve at or before the as-of date)
    curve: dict                   # Treasury CMT par yields, percent, by tenor (1M..30Y, 20Y)
    sofr_1m: float                # percent
    mort: float                   # percent
    sofr_source: str


def load_spot(data_dir: str, as_of, refresh: bool = False) -> Spot:
    as_of = pd.Timestamp(as_of)
    tsy = pd.DataFrame({t: read_series(sid, data_dir, refresh).loc[:as_of] for t, sid in CURVE_SERIES.items()})
    core = [t for t in CURVE_SERIES if t != "20Y"]
    complete = tsy[core].dropna()
    if complete.empty:
        raise ValueError(f"no complete Treasury curve at or before {as_of.date()}")
    d = complete.index[-1]
    curve = {t: float(tsy.loc[d, t]) for t in core}
    v20 = tsy["20Y"].loc[:d].dropna()
    if len(v20) and (d - v20.index[-1]).days <= 7:
        curve["20Y"] = float(v20.iloc[-1])
    s1m = read_series(SOFR_1M_PROXY, data_dir, refresh).loc[:d]
    if len(s1m) and (d - s1m.index[-1]).days <= 7:
        sofr, src = float(s1m.iloc[-1]), "30-day average SOFR"
    else:
        effr = read_series(EFFR_DAILY, data_dir, refresh).loc[:d]
        sofr, src = float(effr.loc[d - pd.Timedelta(days=30):].mean()), "30-day average EFFR (pre-SOFR proxy)"
    mort = float(read_series(HISTORY_SERIES["mort"], data_dir, refresh).loc[:d].iloc[-1])
    return Spot(d, curve, sofr, mort, src)
