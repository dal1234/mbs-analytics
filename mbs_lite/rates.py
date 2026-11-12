"""Rate paths for the income engine, from a run_rates.py output folder and the stored calibration.

Files read (rates_output/)
    run_record.json      as-of, calibration id and file, simulation settings, baseline identifier
    baseline.csv         baseline path, months 0..H, percent
    mc_paths.csv         the official paths (checked: they must reproduce from the calibration, baseline and seed)
    history_monthly.csv  month-end actuals (prepayment lag, burnout estimate, fan-chart history)
    rate_validation.json V1-V4 from the rate run
Any other path set (the V4 floor rerun, V5 convergence, V8 calibrations) is regenerated with
ratemodel from the same calibration, baseline and seed, so every number traces back to the run record.

Cube layout used by the engine: (paths, months 0..H, series) in percent; month 0 = today's observed rates on every
path; month m = the rate at the end of month m. The engine reinvests in month m at the rate at the start of month m.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

from ratemodel import scenarios as scen
from ratemodel import store
from ratemodel.model import Calibration, SimConfig, simulate_paths

SERIES = ["SOFR_1M", "TSY_1Y", "TSY_2Y", "TSY_5Y", "TSY_10Y", "TSY_30Y", "MTG_30Y"]
KEYS = ["short", "y1", "y2", "y5", "y10", "y30", "mort"]


@dataclass
class RateSet:
    as_of: pd.Timestamp
    dates: pd.DatetimeIndex          # month 0..H
    mc: np.ndarray                   # official paths (P, H+1, len(SERIES)), percent
    baseline: np.ndarray             # (H+1, len(SERIES)), percent
    mtg_observed: np.ndarray         # actual 30y mortgage rate at months -2, -1, 0 (prepayment lag)
    history: pd.DataFrame            # month-end actuals, index month_end, columns SERIES, percent
    record: dict                     # run_record.json
    calibration_meta: dict           # stored calibration payload (all named calibrations, V7)
    calibrations: dict               # name -> Calibration
    rate_validation: dict
    sim: SimConfig                   # official simulation settings (n_months = the full rate horizon)
    reproduced: bool                 # official paths regenerate exactly from the run record
    full_baseline: np.ndarray        # baseline over the full rate horizon (paths are always simulated in full)

    @property
    def horizon(self) -> int:
        return len(self.dates) - 1

    @property
    def n_paths(self) -> int:
        return self.mc.shape[0]

    @property
    def official(self) -> Calibration:
        return self.calibrations[self.calibration_meta["official"]]

    def col(self, name: str) -> int:
        return SERIES.index(name)

    def baseline_frame(self) -> pd.DataFrame:
        """Months 1..H in decimals, as ratemodel.simulate_paths expects."""
        b = pd.DataFrame(self.full_baseline[1:] / 100.0, columns=KEYS)
        b.index = pd.RangeIndex(1, len(self.full_baseline), name="month")
        return b

    def simulate(self, n_paths: int | None = None, calibration: str | None = None, floor: float | None = None,
                 with_dev: bool = False):
        """Paths in cube layout for any path count, named calibration or floor (same seed and settings)."""
        cfg = replace(self.sim, n_paths=int(n_paths or self.sim.n_paths),
                      floor=self.sim.floor if floor is None else float(floor))
        cal = self.calibrations[calibration or self.calibration_meta["official"]]
        p = simulate_paths(cal, self.baseline_frame(), cfg)
        H = self.horizon
        cube = np.empty((cfg.n_paths, H + 1, len(SERIES)))
        cube[:, 0, :] = self.full_baseline[0]
        for j, k in enumerate(KEYS):
            cube[:, 1:, j] = p[k][:, :H] * 100.0
        return (cube, p) if with_dev else cube

    def baseline_cube(self) -> np.ndarray:
        """The baseline as one path."""
        return self.baseline.copy()[None, :, :]

    def parallel_cube(self, shift_bp: float) -> np.ndarray:
        """The baseline with an immediate parallel shock as one path (months 1..H): every Treasury tenor and 1M SOFR
        move ``shift_bp``, the mortgage rate beta x ``shift_bp`` (ratemodel.scenarios), floored like the paths."""
        d = scen.deviation(self.official, "parallel", shift_bp, shift_bp)
        b = self.baseline.copy()
        b[1:] = np.maximum(b[1:] + 100.0 * np.array([d[k] for k in KEYS]), self.sim.floor * 100.0)
        return b[None, :, :]

    def scenario_cube(self, spec: dict | None = None) -> tuple[list, np.ndarray]:
        """Deterministic scenarios (ratemodel.scenarios) in cube layout, one path per scenario: (catalog, cube).
        Built from the same baseline, calibration and floor as the Monte Carlo; the base row is the baseline exactly."""
        spot = dict(zip(KEYS, self.full_baseline[0] / 100.0))
        cat, paths = scen.build(self.baseline_frame(), spot, self.official, spec, self.sim.floor)
        H = self.horizon
        cube = np.empty((len(cat), H + 1, len(SERIES)))
        cube[:, 0, :] = self.full_baseline[0]
        for i, row in enumerate(cat):
            cube[i, 1:, :] = paths[row["id"]][list(KEYS)].to_numpy(float)[:H] * 100.0
            if row["family"] == "base":
                cube[i] = self.baseline
        return cat, cube

    def with_mortgage_spread(self, cube: np.ndarray, spread_bp: float, ramp_months: int = 3) -> np.ndarray:
        """Overlay: the 30Y mortgage rate + ``spread_bp`` in months 1..H, phased in over ``ramp_months``; Treasuries
        and SOFR unchanged. Moves prepayment speeds only (the engine reinvests at the designated rate)."""
        if not spread_bp:
            return cube
        out = cube.copy()
        out[:, 1:, self.col("MTG_30Y")] += spread_bp / 100.0 * scen.timing(cube.shape[1] - 1, ramp_months)
        return out


def load_rates(rates_dir: str, horizon: int) -> RateSet:
    d = Path(rates_dir)
    if not (d / "run_record.json").exists():
        raise FileNotFoundError(f"no run_record.json in {d}: run `python run_rates.py --as-of <date>` first")
    rec = json.loads((d / "run_record.json").read_text())
    H = int(rec["simulation"]["n_months"])
    if horizon > H:
        raise ValueError(f"horizon_months {horizon} is longer than the rate paths ({H} months)")
    cal_file = rec["calibration"]["file"]
    if not Path(cal_file).exists():
        raise FileNotFoundError(f"calibration file {cal_file} named in the run record is missing")
    meta, cals = store.load(cal_file)
    if meta["calibration_id"] != rec["calibration"]["id"]:
        raise ValueError(f"calibration file {cal_file} now holds {meta['calibration_id']}, but the rate run used "
                         f"{rec['calibration']['id']}: rerun run_rates.py")
    s = rec["simulation"]
    sim = SimConfig(n_paths=s["n_paths"], n_months=H, antithetic=s["antithetic"], moment_match=s["moment_match"],
                    sampler=s["sampler"], seed=s["seed"], floor=s["floor"], vol=s.get("vol"))
    b = pd.read_csv(d / "baseline.csv")
    dates = pd.DatetimeIndex(pd.to_datetime(b["date"]))
    base = b[SERIES].to_numpy(float)
    m = pd.read_csv(d / "mc_paths.csv")
    P = int(m["path"].max())
    stored = m[SERIES].to_numpy(float).reshape(P, H + 1, len(SERIES))

    hist = pd.read_csv(d / "history_monthly.csv")
    hist["month_end"] = pd.to_datetime(hist["month_end"])
    hist = hist.set_index("month_end")[[c for c in SERIES if c in hist.columns]]
    as_of = pd.Timestamp(rec["as_of"])
    prior = hist.loc[hist.index < as_of.replace(day=1), "MTG_30Y"].dropna()
    obs = np.array([prior.iloc[-2], prior.iloc[-1], base[0, SERIES.index("MTG_30Y")]]) if len(prior) >= 2 \
        else np.full(3, base[0, SERIES.index("MTG_30Y")])
    val = json.loads((d / "rate_validation.json").read_text()) if (d / "rate_validation.json").exists() else {}
    rs = RateSet(as_of, dates, stored, base, obs, hist, rec, meta, cals, val, sim, False, base)
    regen = rs.simulate()
    rs.reproduced = bool(np.abs(regen - stored).max() < 1e-5)
    if horizon < H:                                   # the income model may use a shorter horizon
        rs = replace(rs, dates=dates[: horizon + 1], mc=stored[:, : horizon + 1], baseline=base[: horizon + 1])
    return rs
