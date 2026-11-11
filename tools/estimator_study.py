#!/usr/bin/env python
"""Reproduces the estimator comparison in whitepaper section 6.1: simulated histories with known AR(1) parameters
(300 replications, 1990-01 to 2026-10, three parameter sets). It studies the v1.0/1.1 first-order estimator on raw
changes (model.fit_ar1_horizon, kept as a challenger); the v1.2.0 production estimator on forward deviations is checked
by the V7 bootstrap at every calibration. For the 12- and 60-month fan width: horizon matching on raw changes
against its target, the true dispersion of h-month changes (hz_vs_change) and against the true conditional
dispersion (hz); bias-corrected least squares (challenger, olsbc) and uncorrected least squares (ols) against the true
conditional dispersion.

    python tools/estimator_study.py        (about a minute)
"""
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ratemodel import model as erm  # noqa: E402

warnings.filterwarnings("ignore")
res = []
for true_phi, true_sig in [(0.985, 0.0022), (0.990, 0.0026), (0.97, 0.0025)]:
    for seed in range(300):
        h = erm.synthetic_history("1990-01", "2026-10", seed=seed, true={"phi_s": true_phi, "sig_s": true_sig})
        y = h["short"].values
        f, o = erm.fit_ar1_horizon(y), erm.fit_ar1_ols(y)
        row = {"phi": true_phi}
        for m in (12, 60):
            t = erm.ar1_conditional_std(true_phi, true_sig, m)
            row[f"hz_{m}"] = erm.ar1_conditional_std(f["phi"], f["sigma"], m) / t
            row[f"hz_vs_change_{m}"] = erm.ar1_conditional_std(f["phi"], f["sigma"], m) / erm.ar1_change_std(true_phi, true_sig, m)
            row[f"olsbc_{m}"] = erm.ar1_conditional_std(o["phi_bias_corrected"], o["sigma"], m) / t
            row[f"ols_{m}"] = erm.ar1_conditional_std(o["phi_ols"], o["sigma"], m) / t
        k = 12
        s, l = h["short"].values, h["y10"].values
        row["rho12"] = np.corrcoef(s[k:] - s[:-k], l[k:] - l[:-k])[0, 1]
        row["rho1"] = np.corrcoef(np.diff(s), np.diff(l))[0, 1]
        res.append(row)
df = pd.DataFrame(res)
print(df.groupby("phi").agg(["mean", "std"]).T.round(3).to_string())
