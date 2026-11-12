# MBS Income Risk (lite)

Projects **interest income** for a portfolio of agency MBS CUSIPs on a **baseline** rate path and on **Monte Carlo
rate paths** around it, and reports **Earnings at Risk (EaR)**: baseline income minus the 5th percentile of simulated
income, for years 1 to 5, cumulative and annual. It also runs **deterministic rate scenarios** (parallel shocks and
curve shapes built from the same baseline and calibration) through the same engine, with attribution, overlays and
cohorts, so a named rate move and the Monte Carlo distribution share one baseline. The rate model is a real-world,
baseline-centred, two-factor Gaussian model calibrated to public history. Its methodology, decisions, validation and
governance are in the whitepaper, which the dashboard shows as the **Rate model** page, filled with the results of each
run.

For each CUSIP, each path (and the baseline) and each month the model computes:

```
scheduled(t)   = level-payment principal on balance(t-1) at the WAC over the remaining term
prepaid(t)     = (balance(t-1) - scheduled(t)) x SMM(t)                 SMM from the prepayment model
balance(t)     = balance(t-1) - prepaid(t) - scheduled(t)
interest(t)    = balance(t-1) x coupon / 12                              interest on the held security
reinvested(t)  = reinvested(t-1) + prepaid(t)                            prepaid UPB is reinvested ...
reinv_inc(t)   = reinvested(t-1) x 1M SOFR(start of month t) / 12        ... at 1M SOFR (configurable)
income(t)      = interest(t) + reinv_inc(t)
EaR 95%        = baseline income - 5th percentile of simulated income, per horizon
```

| Piece | What it does | Entry point |
|---|---|---|
| Rate model (`ratemodel/`) | Calibration (stored, refitted annually or on demand), baseline (forward / flat / house view), simulation, rate validation V1–V4 and V7 | `run_rates.py` |
| Income model (`mbs_lite/`) | Portfolio validation, prepayment model, cash flows, EaR with bootstrap intervals, deterministic rate scenarios (section 5a), validation V4–V6 and V8, run history | `run_model.py` |
| Dashboard (`dashboard/`) | **Income risk**: one KPI row (portfolio, baseline income, EaR 95%, P5, worst and best rate scenario, ±100 bp, calibration range) for the horizon chosen in the bar, then two views. **Rate specific** (`index.html#rate`): rate paths and curve shapes, the income sensitivity grid with spread and prepayment overlays, why income moves (monthly income, income by scenario, drivers, attribution), cash flows (balances and rates, by year, waterfall) and cohorts, all following the scenario you pick. **Earnings at risk** (`index.html#ear`): rate fan and income distribution (with the ±100/±200 bp shocks marked), then tabs: cash flows by outcome, rates and speeds, baseline by month, EaR by horizon, EaR over time, tail attribution (a tab can be linked as `index.html#bl` etc.). Also **Portfolio**, **Models & assumptions**, **Rate model** (the whitepaper with this run's results) | open `dashboard/index.html` |
| Two approaches (section 7a) | **Official**: Treasury forward baseline + historical volatility (the validated model). **Vendor emulation**: SOFR swap forward baseline + market-implied volatility, our reading of a risk-neutral vendor model, for comparison. Each has its own config, outputs and dashboard; the dashboards link to each other | `config/*_vendor.yaml` |
| Tools (`tools/`) | `mock_history.py` (mock-up of 12 months of history), `estimator_study.py` (reproduces whitepaper table 6.1), `vols_from_percentiles.py` (market volatility inputs from another model's percentiles or quoted implied vols), `compare_runs.py` (official vs vendor emulation, side by side) | |

---

## 1. Set up

Python 3.10 or later.

```bash
cd mbs_risk_lite
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt      # numpy, pandas, scipy, pyyaml
python -m pytest tests               # optional: pip install pytest first
```

What ships: the code, a **sample portfolio** (548 CUSIPs, $100bn, as of 2026-09-30), the rate history cache
(`data/fred/`), the stored official calibration and its V5/V8 results (`calibration/`), a mock-up run history, and the
dashboard as last built (open `dashboard/index.html` to look before running anything). The generated folders
`rates_output/` and `outputs/` are **not** shipped; the first run creates them. Run the sample once to confirm the
install:

```bash
python run_rates.py --as-of 2026-09-30     # ~3 s, offline, uses the stored calibration -> rates_output/
python run_model.py run                    # ~40 s: baseline + 5,000 paths (+ V4 floor rerun) -> outputs/, dashboard/
```

The run should print only PASS, INFO and these expected results: WATCH on the calibration flags (parameters at a
bound) and on V4 floor incidence (the rerun at a −0.5% floor is disclosed); section 6 explains both. Then open `dashboard/index.html` in a browser; no web server is needed.

---

## 2. Plug in a new portfolio

### Step 1: build the portfolio file

One CSV, **one row per CUSIP, all on one as-of date**. Column names are case-insensitive. Extra columns are ignored.

**Required columns**

| Column | Type / units | Meaning | Typical source |
|---|---|---|---|
| `cusip` | text | Security identifier, used as the row key in every output | accounting / position system |
| `as_of_date` | date `YYYY-MM-DD` | Date the balances are as of. **The same date on every row.** | position system |
| `product` | `30Y`, `20Y`, `15Y` or `10Y` | Original loan term of the pool. Drives the refinance rate offset and product speed multipliers. | pool disclosure (original term 360/240/180/120) |
| `coupon` | percent, e.g. `5.5` | Pass-through (net) coupon paid to the investor | security master |
| `wac` | percent, e.g. `6.473` | Weighted-average gross loan rate; sets the refinance incentive and the amortization schedule | pool factor / disclosure file |
| `wala` | months | Weighted-average loan age | pool factor / disclosure file |
| `wam` | months | Weighted-average remaining term | pool factor / disclosure file |
| `current_upb` | dollars | Face **you hold** today = original face held × current pool factor | position system × factor file |

**Optional columns** (leave out or blank to use the default)

| Column | Default | Meaning |
|---|---|---|
| `avg_loan_size` | 300,000 (neutral) | Current average loan balance **in the pool** = pool current UPB / loan count. Large loans refinance faster. |
| `story_group` | `Generic` | `Generic` (TBA-deliverable), `Spec` (specified pool), `Geo` (geographic story), `LLB` (low loan balance). |
| `burnout_years` | estimated | Years the pool has spent more than 50 bp in the money; if blank, estimated from the 30y mortgage-rate history. |
| `vintage` | as-of year − WALA/12 | Issue year; used for cohorts and the Portfolio page. |
| `market_price` | none | Price per 100 face. **Display only** (market value on the Portfolio page); income does not use it. |
| `pool_number` | blank | Shown on the Portfolio page. |

Example (`data/portfolio/sample_portfolio.csv`):

```csv
cusip,as_of_date,product,coupon,wac,wala,wam,current_upb,avg_loan_size,story_group,vintage,market_price,pool_number
31418D4B5,2026-09-30,15Y,1.5,2.106,62,113,32683500.0,208619.0,Generic,2021,85.9501,MA4417
31418FUY1,2026-09-30,30Y,5.5,6.473,8,347,1000000000.0,552579.0,Generic,2026,97.4443,MA5998
```

Mapping notes:
- Several lots of one CUSIP can be **combined into one row** (sum `current_upb`). Duplicate CUSIPs are allowed but get a warning.
- `coupon` is the net coupon, not the WAC. `wac` minus `coupon` is normally 0.25–1.5%; `wac` below `coupon` triggers a warning.
- Use the factor that matches `as_of_date`. A stale factor overstates the balance.
- Only fixed-rate, level-payment agency pass-throughs fit this model. ARMs, CMOs, IOs/POs and balloons do not.

### Step 2: run the rate model for the portfolio's date

```bash
python run_rates.py --as-of 2026-12-31                    # stored calibration, forward baseline
python run_rates.py --as-of 2026-12-31 --recalibrate      # annually, or when a monitoring trigger fires
python run_rates.py --as-of 2026-12-31 --baseline flat    # or: --baseline file --baseline-file house_view.csv
```

This writes `rates_output/` (run record, baseline, official paths, monthly history, V1–V4 results).

- **Calibration.** On the first run, or with `--recalibrate`, the three named calibrations are estimated on history up
  to the as-of date, and the V7 parameter-uncertainty bootstrap is run. Calibration first bootstraps every month-end
  Treasury curve since 1962 into historical forward baselines (`ratemodel/forwards.py`, about 2 s), because the fan is
  fitted to how far rates ended up from those forwards (model 1.2.0). The result is stored in
  `calibration/calibration.json` and archived in `calibration/archive/`. Later runs reuse it, so month-to-month EaR
  changes come from the book and the market, not from re-estimation (whitepaper 13.3). The run warns when the
  calibration is more than 12 months old.
- **Baseline.** `forward` (default) uses implied forwards from the as-of Treasury curve, with 1M SOFR = forward
  1-month rate + today's SOFR–bill basis and mortgage = forward 10-year + today's mortgage spread, both held constant.
  `flat` holds today's rates. `file` reads a house view: a CSV with `month` (1–60) and `short`, `y10`, `mort` (and
  optionally `y1`, `y2`, `y5`, `y30`) in percent.
- **Rate history** comes from `data/fred/` (FRED files through **2026-10-22**). For a later date, set
  `data.refresh: true` in `config/rates.yaml` (downloads from FRED), or replace the files with your own in the same
  layout: header `observation_date,<SERIES_ID>`, one row per date, rates in percent.

  | Files | Series | Used for |
  |---|---|---|
  | `SOFR`, `DFF` | overnight SOFR (2018-04 on), effective fed funds (before) | short-rate history (monthly average) |
  | `DGS1`, `DGS2`, `DGS5`, `DGS10`, `DGS30` | Treasury CMT | factor and tenor history (month-end) |
  | `MORTGAGE30US` | Freddie Mac PMMS 30-year fixed | mortgage history (month-end) |
  | `DGS1MO`, `DGS3MO`, `DGS6MO`, `DGS3`, `DGS7`, `DGS20` | Treasury CMT | as-of curve for the forward baseline; with the files above, every month-end curve for the calibration targets |
  | `SOFR30DAYAVG`, `EFFR` | 30-day average SOFR; EFFR before 2018 | today's 1M SOFR |

  History must start by 1976-01 for the `high_vol` calibration.

### Step 3: validate the portfolio

```bash
python run_model.py validate --portfolio /path/to/my_portfolio.csv
```

Every problem is listed at once (missing columns, non-numbers, blanks, mixed dates, out-of-range values, unknown
products), with an example CUSIP or row for each. Warnings do not stop the run; errors do. `validate` also checks
that the rate run from Step 2 is for the same month and that its paths regenerate from the stored calibration.

### Step 4: run the income model and open the dashboard

```bash
python run_model.py run --portfolio /path/to/my_portfolio.csv
```

The run projects the baseline and the official 5,000 paths (every measure: 95% and 99%, expected shortfall, cash
flows, attribution), runs V4/V6 (and V5/V8 the first time a calibration or the simulation settings are used), prints
its checks, rewrites `outputs/`, appends to `history/ear_history.csv` and rebuilds `dashboard/`. The dashboard folder is
self-contained (HTML, CSS, JS and `data.js`) and can be copied or shared. Set `portfolio.name`, `dashboard.title` and
`portfolio.banner` (`""` removes the sample-data warning) in `config/model.yaml`.

### Run time and memory

The work grows with positions × paths × months; every run projects 1 + 5,000 paths over 60 months, plus another
5,000 when the V4 floor trigger fires (it does on the sample at 2026-09: 6.7% of paths touch 0%). Measured on 8 cores:

| Positions | Regular run | First run with a new calibration (adds V5: 1,000 + 20,000 paths; V8: 2 × 5,000) | Peak memory |
|---|---|---|---|
| 548 | 40 s (21 s without the V4 rerun) | 2.2 min | 1.3 GB |
| 6,000 | about 6 min (3.2 min without the V4 rerun) | about 17 min (estimated from the sample's ratio) | 1.4 GB |

Paths are processed in blocks (`max_cells` in `mbs_lite/engine.py`), so memory stays bounded. 4–8 GB of RAM is
plenty.

---

## 3. Configuration

`config/model.yaml` (income model and reporting):

| Key | Default | What it does |
|---|---|---|
| `portfolio.file` | sample | Portfolio CSV (relative to `config/`) |
| `rates.dir` | `../rates_output` | Output folder of `run_rates.py` |
| `horizon_months` | 60 | Whole years, up to the rate horizon |
| `income.reinvest_rate_series` | `SOFR_1M` | The reinvestment rate: `SOFR_1M`, `TSY_1Y`, `TSY_2Y`, `TSY_5Y`, `TSY_10Y`, `TSY_30Y` or `MTG_30Y` |
| `income.reinvest_spread_bp` | 0 | Added to the reinvestment rate |
| `income.reinvest_scheduled_principal` | `false` | `true` also reinvests scheduled paydown (as the whitepaper's reference engine does) |
| `prepay.multiplier` | 1.0 | Scales every speed; use 0.75 / 1.25 to test prepayment-model error |
| `risk.outcomes` | `[5, 10, 25, 50, 75, 90, 95]` | Percentiles shown as representative monthly cash flows |
| `risk.band_pct` | 5 | Each outcome averages the paths ranked within this % of all paths around its percentile |
| `risk.tail_pct` | 5 | Tail used for the cohort attribution |
| `risk.cohort_by` | `[product, coupon, vintage]` | Any portfolio columns |
| `validation.file` | `../calibration/income_validation.json` | Stored V5/V8 results, keyed by calibration id |
| `history.file` | `../history/ear_history.csv` | Run history appended by every run |
| `risk.decision_paths` | 500 | Paths for the business-decision sensitivities on the Models & assumptions page (0 = skip) |
| `scenarios.parallel_bp`, `.ramp_months` | `[100, 200, 300, 400]`, 12 | Parallel shocks, each up and down, immediate and ramped (0 = immediate only) |
| `scenarios.shapes` | 6 shapes | `{id, name, short_bp, long_bp}`: 1M SOFR and 10-year moves of each curve shape |
| `scenarios.include_flat` | `true` | Reference row with today's rates held |
| `scenarios.overlays` | spread −50…+50 bp, prepay 0.75/1.00/1.25× | Combinations re-run on every scenario for the grid's overlay buttons |

`config/rates.yaml` (rate model): data cache, calibration file, baseline (`baseline.type`: forward, swap, flat or file,
plus `baseline.swap` for the swap curve) and volatility (`volatility.type`: historical or market, plus
`volatility.market`); section 7a explains the choices. The simulation settings (5,000 official
paths, V5 convergence counts 1,000 / 5,000 / 20,000, antithetic, moment matching, seed 20261110, 0% floor, V4 trigger) and the calibration windows are
**fixed by design** (whitepaper 10.1 and 13): changing them is a model change that requires revalidation.

`config/prepay_params.yaml`: prepayment model parameters.

---

## 4. Outputs

`outputs/` (rebuilt on every run):

| File | Contents |
|---|---|
| `ear_by_horizon.csv` | Per measure (cumulative / annual) and year: baseline, mean, mean − baseline, percentiles, income at 95%, shortfall $ and %, bootstrap interval, shortfall at 99%, expected shortfall 95/99 |
| `mc_path_income.csv` | Official paths: income, coupon and reinvestment per measure and year |
| `outcome_monthly.csv` | Representative outcomes and the baseline, month by month, per measure and year |
| `cusip_monthly_baseline.csv`, `_mean.csv`, `_P50.csv`, `_P05.csv` | CUSIP × month: balance, scheduled paydown, prepaid UPB, interest, reinvestment, income, CPR (baseline path, all-path average, median and P5 outcomes) |
| `cohort_tail.csv` | Per measure, year and cohort: baseline vs tail income and CPR, contribution to the tail shortfall |
| `holdings_projected.csv` | The validated portfolio plus burnout, projected WAL, baseline and mean year-1 income and CPR |
| `scenario_income.csv` | Rate scenarios: per scenario and year, income, coupon, reinvestment, principal, balances, CPR, change vs base (annual and cumulative), percentile in the Monte Carlo distribution |
| `scenario_monthly.csv`, `scenario_rates.csv` | Rate scenarios month by month: income lines, balances, CPR, reinvestment rate; the seven rate series of each scenario path |
| `scenario_attribution.csv`, `scenario_overlays.csv` | Change vs base split into prepayment (coupon, reinvested principal), reinvestment rate and interaction; income and CPR under each overlay |
| `validation_v5_convergence.csv`, `_v6_shocks.csv`, `_v8_calibrations.csv`, (`_v4_floor_rerun.csv`) | Income-dependent validation |
| `checks.csv` | The run's checks |

`rates_output/` (rebuilt by every `run_rates.py`): `run_record.json`, `baseline.csv`, `mc_paths.csv` (official
paths, percent), `history_monthly.csv`, `rate_validation.json`. `calibration/` (kept; changes only on
`--recalibrate`): `calibration.json` (in use), `archive/` (every calibration ever used), `income_validation.json`.

---

## 5. Risk measures (whitepaper section 8)

- **EaR 95%** = baseline income − 5th percentile of simulated income, in $ and % of baseline, for years 1–5,
  **cumulative** (months 1 to 12k) and **annual** (year k alone). Each percentile is taken separately, because the
  worst paths in year 1 are not the worst over five years. A 95% bootstrap interval (antithetic pairs resampled
  together) accompanies each figure.
- **EaR 99% and expected shortfall** come from the same official 5,000-path run; a 1st percentile needs about 5,000
  paths (whitepaper section 7).
- **Mean − baseline** is reported as the convexity of income, not as an error.
- **Calibration range (V8)**: the same EaR under the `recent` and `high_vol` calibrations, beside the official figure.
- **Representative outcomes**: the paths ranked around a percentile of income over the horizon are averaged, so
  coupon + reinvestment = income month by month. They are shown with the baseline path and their difference from it.
- **Tail attribution**: each cohort's average income in the worst 5% of paths minus its baseline income. These add up
  to the tail's shortfall against the baseline.

## 5a. Rate scenarios (whitepaper section 13.4)

Deterministic scenarios are built by `ratemodel/scenarios.py` the way a Monte Carlo path is (baseline + deviation),
with a fixed deviation, the official calibration and the floor, and run through the same engine
(`mbs_lite/scenarios.py`, about one second):

```
parallel ±X    every Treasury tenor and 1M SOFR + X; 30Y mortgage + β X (β = 0.91, the calibrated pass-through)
               immediate (month 1 on) or ramped linearly over 12 months, then held
shape (S, L)   1M SOFR + S, 10Y + L, 1/2/5/30Y on their factor loadings, mortgage + β L    (immediate)
base           the baseline itself = the EaR baseline; flat = today's rates held (reference)
```

- The base row is the EaR baseline exactly (a run check), so the grid, the KPI row and EaR share one reference. Each
  scenario's percentile in the Monte Carlo distribution of the same horizon is shown beside it.
- **Attribution** (exact): the scenario mortgage rate alone (coupon lost to prepayment, prepaid principal reinvested
  at the base rate), the scenario reinvestment rate alone, and the interaction.
- **Overlays**: a mortgage spread (mortgage rate only, phased in over 3 months) and a prepayment multiplier re-run every
  scenario; they change the grid only, never EaR.
- V6 uses the immediate ±100/±200 bp scenarios. Up to income model 1.0.0 it moved every rate, the mortgage rate
  included, by the full shock.
- Scenarios are views, not model settings: change them in `config/model.yaml` (`scenarios`) without revalidation.
  Not covered: historical replays, named combined scenarios, realized-volatility and user-drawn paths.

---

## 6. Rate model, calibration and validation

Model (version 1.2.0): each rate = baseline + zero-mean deviation. The short rate and the 10-year deviation are
mean-reverting with **momentum** (an AR(1) whose shock is itself AR(1): a move continues for a few months before
reversion takes over) and correlated shocks; mortgage = β × 10-year deviation + an AR(1) spread; 1/2/5/30-year yields
are loadings on the two factors; every rate is floored at 0%. φ, momentum and σ are fitted so the **fan width** h months
ahead equals the historical dispersion of h-month **deviations from the forward** (the rate that occurred minus what
that month's Treasury curve implied), for h = 3, 12, 24, 36 and 60: the same quantity the fan represents around a
forward baseline (whitepaper 6.1). ρ is solved so the model's 12-month deviation correlation equals history's. β and
the spread are fitted the same way to mortgage-rate deviations, and the tenor loadings to 3-month deviations.
Earlier versions fitted first-order factors to raw rate changes: 1.0.0 also took β from 3-month changes (mortgage fan
20–24% narrower than history at 3–5 years); 1.1.0 fixed the mortgage rate but its short-rate φ sat at the bound and its
fan missed the short-rate history by up to 31% at 2 years, which one momentum parameter fixes.

**Calibrations** (`ratemodel/model.py`, `CALIBRATION_WINDOWS`): `base` = 1990-01 to latest (official), `recent` =
trailing 120 months, `high_vol` = 1976-01 to latest. Users do not set calibration parameters (whitepaper 13.2). Fan
width changes only through a governed recalibration. The two alternatives show the model-risk range on every run.

**Validation** (whitepaper section 11; results on the Rate model page, section 11):

| Test | Where it runs | When |
|---|---|---|
| V1 centring, V2 dispersion (short rate, 10-year and mortgage fans vs the stored forward-deviation targets), V3 co-movement, V4 floor incidence | `run_rates.py` | every run |
| V4 rerun at a −0.50% floor (if more than 5% of paths touch the floor) | `run_model.py` | every run, when triggered |
| V5 convergence (1,000 / 5,000 / 20,000 paths; passes when the 20,000-path result lies inside the official run's interval), V8 calibration sensitivity | `run_model.py` | once per calibration and simulation settings (stored; `--revalidate` to rerun) |
| V6 benchmark parallel shocks (the ±100/±200 bp rate scenarios, mortgage rate β × the shock) | `run_model.py` | every run |
| V7 parameter uncertainty (200 refits) | `run_rates.py --recalibrate` | once per calibration |

**Current results** (sample portfolio, model 1.2.0, 5,000 official paths, calibration through 2026-09), shown in full
on the Rate model page:
- EaR 95%: $77m (1.5% of baseline) over 1 year, $1.54bn (10.3%) over 3 years, $4.67bn (19.2%) cumulative over 5 years.
  EaR 99%: $130m and $7.5bn. Calibration range (V8): 1.5% / 1.5% / 3.0% at 1 year and 19.2% / 32.1% / 32.3% at 5 years
  (base / recent / high_vol). Under model 1.1.0 the official figures were $57m (1.1%) and $3.82bn (15.7%): the
  short-rate fan is now wider over years 1–3, where it had been 19–31% too narrow.
- Short rate: half-life 2.6 years, momentum 0.78 (V7 range 0.69–0.89, well identified); fan 141 / 263 bp at 1 / 5
  years, 0.92–1.10× the forward-deviation target at every horizon. 10-year: momentum 0.18 (poorly identified, V7
  −0.38 to 0.49), fan 1.00–1.01× its target. ρ = 0.67. Mortgage: β = 0.91; fan 0.87–1.05× its target; the spread's φ sits
  at its upper bound (flagged).
- Forwards were biased forecasts: since 1990 the short rate ended on average 65 bp below its 12-month forward and
  2.35 points below its 5-year forward. The fan width excludes that bias (it is the baseline's, assumption A1).
- Rate scenarios (income model 1.1.0): immediate −100 / +100 bp change 1-year income by −$72m / +$29m (−1.4% /
  +0.6%; P6 and P85 of the simulated distribution) and 5-year cumulative income by −$1.37bn / +$0.64bn. Worst at
  1 year: −400 bp, −$1.03bn (−20.4%). Income falls with rates: faster prepayment of a premium book, reinvested at a
  lower SOFR.
- V1–V6 pass. V4 triggers (6.7% of paths touch the 0% floor); the −0.5% floor rerun changes EaR by under 1%. V5: the
  20,000-path benchmark (5-year EaR $4.59bn) lies inside the official run's interval. V3 notes that the simulated
  curve inverts in 31% of path-months against 18% historically (disclosed).

The whitepaper text is at `mbs_lite/templates/whitepaper.html`. Its results blocks and the section 10.2 parameter
table are filled by `whitepaper.js` from each run's `data.js`.

---

## 7. Run history and the "EaR over time" chart

Every `run_model.py run` appends one row per measure and year to `history/ear_history.csv`: as-of, source,
portfolio, UPB, path counts, **calibration id**, baseline type, baseline income, mean, percentiles, EaR 95% ($, %,
interval), EaR 99%, expected shortfall, CPR, starting SOFR and mortgage rate. A rerun for the same as-of date replaces
its rows. The dashboard plots the last 12 as-of dates.

**What ships is a mock-up.** `tools/mock_history.py` made the 11 earlier points from real rates and baselines for each
past month-end, the current calibration, and **today's CUSIPs** with their ages rolled back. They are tagged `mock`.

**In the new environment:**
1. Delete `history/ear_history.csv` to drop the mock-up (the first real run starts a new file).
2. Once per month-end, after positions and factors are final:
   ```bash
   python run_rates.py --as-of <month-end>
   python run_model.py run --portfolio <that month's portfolio CSV>
   ```
   Schedule this with the environment's job scheduler. Keep `history/` and `calibration/` on durable storage.
3. **Recalibrate** once a year (`run_rates.py --as-of <date> --recalibrate`), or when a monitoring trigger in
   whitepaper section 11 fires. The next income run then reruns V5/V8 automatically, and the history shows the new
   calibration id.
4. **To backfill**, loop over past month-ends with each month's own portfolio, oldest first and the latest last:
   ```bash
   python run_rates.py --as-of <month-end> --output rates_history/<month-end>
   python run_model.py run --portfolio <that month's CSV> --rates-dir rates_history/<month-end>
   ```
   For a back-dated run, use the calibration in force at the time with
   `--calibration calibration/archive/<file>`. A later calibration triggers a warning, because it uses data after
   the as-of date. The shipped calibration uses data through 2026-09.

## 7a. Two approaches: official and vendor emulation

The package runs the rate model two ways. Both use the same portfolio, prepayment model, income engine, calibration,
random numbers (5,000 paths, seed 20261110) and 0% floor. They differ in two choices only.

| | **Official** (the validated model) | **Vendor emulation** (comparison) |
|---|---|---|
| Centre of the fan (zero-vol path) | Implied forwards from the **Treasury** curve | Implied forwards from a **SOFR swap** curve: from a file, or estimated |
| Width of the fan (volatility) | **Historical**: fitted to how far rates have ended up from the forward since 1990 (whitepaper 6) | **Market-implied**: normal vols you enter, per year; random walk, no mean reversion, like a risk-neutral model calibrated to option prices |
| What EaR means | A real-world probability: income falls below this level 1 year in 20 | Income at the 5th percentile of the dispersion the market prices today |
| Config | `config/rates.yaml` + `config/model.yaml` | `config/rates_vendor.yaml` + `config/model_vendor.yaml` (each extends the official file) |
| Folders written | `rates_output/`, `outputs/`, `dashboard/`, `history/ear_history.csv`, `calibration/income_validation.json` | `rates_output_vendor/`, `outputs_vendor/`, `dashboard_vendor/`, `history/ear_history_vendor.csv`, `calibration/income_validation_vendor.json` |
| Dashboard header | Green **OFFICIAL** chip, link to the emulation | Orange **VENDOR EMULATION** chip, a comparison banner, link back to the official dashboard |

Use the official set-up for reported EaR. Use the emulation to explain differences from the vendor tool. Neither run
overwrites the other's results or history.

Why there are two, and how the vendor model was read: the other model's 1M SOFR median tracks its zero-vol path, its
fan is narrow at 1 year (P5–P95 3.0–5.0% as of July 2026) and much wider at 5 years (about 0.75–7.0%). A fan that
widens faster than the square root of time is typical of option-implied volatility. A centre that tracks the
zero-vol path is typical of a risk-neutral model built on a swap curve. On the July 2026 sample, the emulation gave
1-year EaR of about $62m against $110m official. Almost all of the gap came from the SOFR width; the centre mattered
little.

### Run both

```bash
# official
python run_rates.py --as-of 2026-09-30
python run_model.py run

# vendor emulation (same as-of date and portfolio)
python run_rates.py --config config/rates_vendor.yaml --as-of 2026-09-30
python run_model.py run --config config/model_vendor.yaml

# side by side: EaR by year and rate percentiles at 12 and 60 months
python tools/compare_runs.py [--csv comparison.csv]
```

Each dashboard links to the other once both exist. The link is written at build time, so on a fresh install run the
official `run_model.py run` once more after the first vendor-emulation run. A new portfolio is passed the same way to
both: `--portfolio my_portfolio.csv`. The first vendor-emulation run with
a given set of volatilities also runs its own V5 convergence test, which takes a few minutes; later runs reuse it.
V8 (calibration range) is not run for the emulation, because its width does not come from the calibrations.

### Option 1: the centre (baseline)

Set in `config/rates.yaml` under `baseline`. The vendor file inherits it and changes only `type`. Any baseline can be
combined with either volatility: the baseline is an input, not part of the model (whitepaper 3.2).

| `baseline.type` | What it builds | Data needed | When to use |
|---|---|---|---|
| `forward` | Implied forwards from the Treasury curve. 1M SOFR = forward 1-month + today's SOFR–bill basis. Mortgage = forward 10-year + today's spread | `data/fred` (public) | Official default |
| `swap` | Implied forwards from a SOFR swap curve. 1M SOFR = forward 1-month swap rate + (today's SOFR − 1M swap). Mortgage = today's rate + the change in the forward 10-year swap rate. The Treasury tenors stay Treasury forwards | A swap curve file, or none (estimated) | Matching a swap-curve vendor model |
| `flat` | Today's rates held | none | Policy NII sensitivity convention, simple comparison |
| `file` | Any monthly path you supply | A CSV | A house view or budget path, or **the other model's zero-vol path** |

**The SOFR swap curve.** Best is the actual curve on the as-of date, from Bloomberg (for example the SOFR OIS curve),
Refinitiv or the vendor tool. Save it as a CSV with columns `tenor,rate`, rates in percent, tenors from
`1M 3M 6M 1Y 2Y 3Y 5Y 7Y 10Y 20Y 30Y`. At least 1Y, 5Y and 10Y are required; a missing 1M/3M/6M is filled from
Treasury bills plus today's SOFR–bill basis:

```
tenor,rate
1M,3.64
3M,3.69
6M,3.80
1Y,3.90
2Y,4.08
5Y,4.13
10Y,4.29
30Y,4.46
```

Then either set `baseline.swap.curve_file: ../data/swap_curve_2026-09-30.csv` in `config/rates.yaml`, or pass it on
the command line:

```bash
python run_rates.py --config config/rates_vendor.yaml --as-of 2026-09-30 --swap-curve data/swap_curve_2026-09-30.csv
```

The swap curve is bootstrapped like the Treasury curve (bills as zero rates, longer tenors as semiannual par). Swaps
pay annually, but the convention difference moves forwards by well under 1 bp.

**Without a swap curve file** the curve is **estimated** as Treasury par yields plus the spreads in
`baseline.swap.spreads_bp` (swap minus Treasury, bp; tenors in between are interpolated; tenors of 1 year and under use
today's SOFR–bill basis). The defaults are typical 2024–25 SOFR swap spreads (2Y −18, 3Y −22, 5Y −30, 7Y −37, 10Y −45,
20Y −75, 30Y −80). They are an assumption, not data. Replace them with current spreads, or better with the curve
itself, as soon as you have them. The dashboard's baseline description says "ESTIMATED" whenever spreads were used.
On the July 2026 sample the estimate put 1M SOFR at 4.17% at 1 year and 4.29% at 5 years, against about 4.0% and
3.9% in the other model, so it brings the centres closer without matching them exactly.

**Using the other model's zero-vol path** (exact centre match). Export its base, zero-volatility scenario by month and
save it as a CSV, rates in percent:

```
month,short,y10,mort
1,3.70,4.31,6.69
2,3.78,4.33,6.71
...
60,3.91,4.40,6.79
```

`month` runs 1 to 60 (month 0 is today's observed rates and is filled automatically). `short` is 1M SOFR, `y10` is
the 10-year rate, and `mort` is the 30-year mortgage rate. Optional columns `y1`, `y2`, `y5` and `y30` are filled from the Treasury
forwards when absent. If the vendor reports quarterly or annual points, interpolate to months first. Then:

```bash
python run_rates.py --config config/rates_vendor.yaml --as-of 2026-09-30 --baseline file --baseline-file data/vendor_zero_vol_2026-09-30.csv
python run_model.py run --config config/model_vendor.yaml
```

Run the same file with the official volatility (`--config config/rates.yaml --baseline file ...`, then
`run_model.py run`) to see the centre's effect on its own. That writes to the official folders, so rerun the official
command afterwards to restore the official forward-baseline results.

### Option 2: the width (volatility)

Set in `config/rates.yaml` under `volatility`. The vendor file switches `type` to `market`, and the numbers are shared:

```yaml
volatility:
  type: historical            # historical (official) | market
  market:
    sofr_bp: [61, 90, 90, 90, 90]   # 1M SOFR normal vol, bp per year, for year 1, 2, 3, 4, 5
    y10_bp:  [90, 90, 90, 90, 90]   # 10-year
    corr: null                      # SOFR/10-year shock correlation; null = historical (about 0.65)
    source: "where the numbers came from"
```

Each value is the volatility applied within that year, a forward vol. The fan's standard deviation at the end of year
k is the square root of the sum of the squares up to k: with the defaults, 61 bp at 1 year and 190 bp at 5 years.
The mortgage spread, β and the other tenors keep their calibrated values, since there is no market data for them.
The 0% floor and the V4 rerun apply as usual.

**Getting the numbers.** Use `tools/vols_from_percentiles.py` in one of two ways:

```bash
# from the other model's percentiles of 1M SOFR: horizon_months:P5:P50:P95 (percent)
python tools/vols_from_percentiles.py --pct 12:3.0:4.0:5.0 --pct 60:0.75:3.9:7.0
#   -> volatility.market list (bp per year, years 1..5): [61, 90, 90, 90, 90]

# from quoted normal implied vols by option expiry (caplets for SOFR, swaptions on 10-year swaps): expiry_years:bp
python tools/vols_from_percentiles.py --implied 1:61 --implied 5:85
```

Do the same with the 10-year's percentiles or swaption vols for `y10_bp`. The 10-year volatility is the largest
remaining unknown in the emulation. On the July sample, 70 bp or 110 bp instead of 90 bp moved 1-year EaR from
$62m to $52m or $74m. The tool also flags percentiles that are skewed (a lognormal model, or a floor binding), where a
normal width is only an approximation.

**Not validated.** The market setting is not part of the whitepaper model. Runs that use it carry a "Rate set-up"
WATCH in the checks, a banner on the risk page, and a note on the Models and Rate model pages. The historical
calibration still feeds the mortgage spread and β, so the whitepaper's results blocks remain useful for those.

### Keeping the emulation current

When the vendor reports a new month, update `volatility.market` (and the swap curve or spreads), then run the two
vendor commands. The official set-up needs no change. The emulation's run history (`history/ear_history_vendor.csv`)
builds up one real point per run; `tools/mock_history.py` mocks only the official history.

---

## 8. Limitations

- Interest income only, not NII: no funding, hedges, dollar rolls or fees.
- Cash coupon, not GAAP income: no premium amortization or discount accretion.
- Prepaid principal is reinvested at 1M SOFR (scheduled principal leaves the book). This departs from the
  whitepaper's reference engine, which reinvests all returned principal.
- The rate model's assumptions A1–A13 (whitepaper section 12) apply: forward baseline includes a term premium,
  constant and level-independent volatility, Gaussian shocks, one correlation, independent mortgage spread, zero floor.
- Five-year fan width carries roughly 20% estimation error (V7), and the calibrations differ materially (V8).
- The prepayment model is a simple S-curve fitted to Aug–Sep 2026 Fannie Mae speeds; it is governed separately and
  should be refit to your own speeds.
- Rate scenarios cover parallel and curve-shape moves only (no historical replays, named combinations,
  realized-volatility or user-drawn paths); curve shapes are immediate and at one size. A scenario says nothing about
  likelihood: read it with its percentile in the Monte Carlo distribution.

## 9. Troubleshooting

| Message | Fix |
|---|---|
| `portfolio date … and rate as-of … are in different months` | `python run_rates.py --as-of <portfolio date>` |
| `no run_record.json in …` | Run `run_rates.py` first, or point `rates.dir` / `--rates-dir` at its output |
| `calibration file … now holds …, but the rate run used …` | You recalibrated after the rate run: rerun `run_rates.py` |
| `annual recalibration is due` | `python run_rates.py --as-of <date> --recalibrate` |
| `could not download …` | No internet: keep `data.refresh: false` and supply `data/fred/` files |
| `Calibration window 'recent' has N months` | History in `data/fred/` is too short (needs 96 months in every window) |
| Dashboard shows the old run | Re-run `run_model.py run`; hard-refresh the browser |

## Folder map

```
README.md
requirements.txt
run_rates.py              rate model run: calibration (stored), baseline, official paths, V1-V4 -> rates_output/
run_model.py              validate | run: income, EaR, V4-V6/V8, history, dashboard -> outputs/, history/, dashboard/
config/
  rates.yaml              official rate run: data, calibration, simulation (fixed by design), baseline, volatility
  rates_vendor.yaml       vendor emulation: extends rates.yaml (swap baseline, market volatility, own output folder)
  model.yaml              income model, risk settings, validation cache, history (official)
  model_vendor.yaml       vendor emulation: extends model.yaml (own rates/outputs/validation/history/dashboard folders)
  prepay_params.yaml      prepayment model parameters
ratemodel/                rate model: data.py, forwards.py, model.py, curve.py, baseline.py, scenarios.py, store.py
mbs_lite/                 portfolio, prepayment, engine, risk, scenarios, validation (V4-V6, V8), history, dashboard writer
  templates/              dashboard pages, incl. scenarios.js (Rate specific view), whitepaper.html + whitepaper.js
calibration/              calibration.json (in use), archive/, income_validation.json (V5/V8)
data/fred/                rate history cache (FRED format)
data/portfolio/           sample_portfolio.csv
tools/                    mock_history.py, estimator_study.py, vols_from_percentiles.py, compare_runs.py
history/ear_history.csv   run history (11 mock + 1 real as-of date)
dashboard/                generated: rebuilt by every official run (shipped as last built)
dashboard_vendor/         generated: rebuilt by every vendor-emulation run (shipped as last built)
rates_output/  outputs/   generated: created by the runs (not shipped); *_vendor/ likewise for the emulation
tests/                    python -m pytest tests
```
