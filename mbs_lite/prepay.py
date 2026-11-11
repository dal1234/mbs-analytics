"""Simplified prepayment model: turnover + refinancing S-curve, in CPR terms (see config/prepay_params.yaml).

    CPR = (turnover + refi + involuntary) x tuning_multiplier x multiplier,   SMM = 1 - (1 - CPR)^(1/12)

All functions are vectorised: position arrays have shape (N,), rate arrays (P, N) or broadcastable,
so every Monte Carlo path is projected in one pass.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import yaml

from .portfolio import PRODUCTS, STORIES


def cpr_to_smm(cpr_pct):
    return 1.0 - (1.0 - np.asarray(cpr_pct, dtype=float) / 100.0) ** (1.0 / 12.0)


def smm_to_cpr(smm):
    return 100.0 * (1.0 - (1.0 - np.asarray(smm, dtype=float)) ** 12)


class SCurvePrepay:
    def __init__(self, params: dict, multiplier: float = 1.0):
        self.p = params
        self.multiplier = float(multiplier)
        s = np.asarray(params["seasonality"], dtype=float)
        self.season = s / s.mean()

    @classmethod
    def from_file(cls, path: str, multiplier: float = 1.0) -> "SCurvePrepay":
        return cls(yaml.safe_load(Path(path).read_text()), multiplier)

    def static(self, product: np.ndarray, story: np.ndarray, loan_size: np.ndarray) -> dict:
        """Per-position constants: product and story multipliers, rate offset, involuntary CPR, size factor."""
        p = self.p
        prod = lambda k: np.array([p["product"].get(x, p["product"]["30Y"])[k] for x in product], dtype=float)  # noqa: E731
        st_t = np.array([p["story_turnover"].get(x, 1.0) for x in story], dtype=float)
        st_r = np.array([p["story_refi"].get(x, 1.0) for x in story], dtype=float)
        size = np.clip(np.asarray(loan_size, dtype=float) / p["loan_size_ref"], 0.2, 5.0)
        return {"offset": prod("rate_offset"), "turn_mult": prod("turnover") * st_t * size ** p["turnover_size_elast"],
                "refi_mult": prod("refi") * st_r * size ** p["refi_size_elast"], "invol": prod("involuntary")}

    def incentive(self, wac, offset, mtg_prior, mtg_prior2):
        """WAC minus the rate borrowers could refinance into (two-month average: application-to-closing lag)."""
        return wac - (0.5 * (mtg_prior + mtg_prior2) + offset)

    def cpr(self, st: dict, wac, age, burnout, mtg_prior, mtg_prior2, month_of_year: int) -> np.ndarray:
        p = self.p
        inc = self.incentive(wac, st["offset"], mtg_prior, mtg_prior2)
        lock = np.where(inc < 0, np.maximum(p["lockin_floor"], 1.0 + p["lockin_slope"] * inc), 1.0)
        ramp_t = np.minimum(1.0, (age + 1.0) / p["turnover_ramp_months"])
        turnover = p["turnover_base"] * lock * ramp_t * self.season[month_of_year - 1] * st["turn_mult"]
        z = np.clip(-(inc - p["refi_mid"]) / p["refi_scale"], -50, 50)
        refi = (p["refi_max"] / (1.0 + np.exp(z)) * np.minimum(1.0, (age + 1.0) / p["refi_ramp_months"])
                * np.exp(-p["burnout_k"] * burnout) * st["refi_mult"])
        return np.minimum((turnover + refi + st["invol"]) * p["tuning_multiplier"] * self.multiplier, 99.0)

    def describe(self) -> str:
        p = self.p
        mult = f", stress multiplier {self.multiplier:.2f}x" if self.multiplier != 1.0 else ""
        return (f"Refi S-curve: turnover {p['turnover_base']:.1f}% with lock-in, refi up to {p['refi_max']:.0f}% "
                f"centred at {p['refi_mid']:.2f}% incentive, burnout {p['burnout_k']:.2f}/yr, "
                f"tuning {p['tuning_multiplier']:.2f}x{mult}")


def estimate_burnout(wac: np.ndarray, wala: np.ndarray, as_of, mtg_history) -> np.ndarray:
    """Years each position has spent more than 50 bp in the money since issue, from month-end mortgage rates.

    ``mtg_history`` is a Series of month-end 30y mortgage rates (percent) indexed by date. Uses the same 30y rate for
    every product (the model's product offset is ignored here), so it is a simple estimate.
    """
    s = mtg_history.dropna()
    s = s[s.index <= as_of]
    if s.empty:
        return np.zeros(len(wac))
    rates = s.to_numpy()[::-1]                     # most recent first
    n = np.minimum(np.asarray(wala, dtype=int), len(rates))
    out = np.zeros(len(wac))
    for i, (w, k) in enumerate(zip(np.asarray(wac, dtype=float), n)):
        if k > 0:
            out[i] = np.count_nonzero(w - rates[:k] > 0.5) / 12.0
    return out


__all__ = ["SCurvePrepay", "cpr_to_smm", "smm_to_cpr", "estimate_burnout", "PRODUCTS", "STORIES"]
