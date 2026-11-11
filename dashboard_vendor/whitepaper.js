// Fills the whitepaper's run-dependent parts from data.js: the section 10.2 parameter table and the
// "Results of this run" blocks (sections 6, 7, 8, 11 and 13.3). Without data.js the page shows the methodology only.
// Loaded in <head>; called by an inline script at the end of <body>, so the page is complete at first paint.
window.fillWhitepaper = function () {
  if (!window.DATA || !DATA.rate_model) return;
  const RM = DATA.rate_model, rec = RM.record, CAL = RM.calibration, V = RM.validation || {}, K = DATA.kpi;
  const cals = CAL.calibrations, off = cals.find(c => c.name === CAL.official), dg = off.diagnostics;
  const hl = phi => Math.log(0.5) / Math.log(phi) / 12, bpv = sig => sig * Math.sqrt(12) * 1e4;
  const condsd = (phi, sig, h) => Math.sqrt(sig * sig * (1 - Math.pow(phi, 2 * h)) / (1 - phi * phi));
  // v1.2.0 factor with momentum: x = phi x(-1) + u, u = mom u(-1) + sig e; st.dev. h months ahead from a zero start
  const fsd = (phi, mom, sig, h) => { let a = 1, b = 0, s2 = 0; for (let j = 0; j < h; j++) { s2 += a * a; const n = (phi + (mom || 0)) * a - phi * (mom || 0) * b; b = a; a = n; } return sig * Math.sqrt(s2); };
  const fan = (c, k, h) => k === 'short' ? fsd(c.phi_short, c.mom_short, c.sigma_short, h) : fsd(c.phi_long, c.mom_long, c.sigma_long, h);
  const tgtName = dg.target === 'forward_deviation' ? 'deviations from the forward' : 'h-month changes';
  const f = (v, d = 2) => bad(v) ? '—' : num(v, d), pc = (v, d = 2) => bad(v) ? '—' : num(100 * v, d) + '%';
  const ok = b => b ? '<span class="ok">pass</span>' : '<span class="bad">fail</span>';
  const tbl = (head, rows, text = false) => `<table><tr>${head.map((h, i) => `<th${i && !text ? ' class="n"' : ''}>${h}</th>`).join('')}</tr>`
    + rows.map(r => `<tr>${r.map((c, i) => `<td${i && !text ? ' class="n"' : ''}>${c}</td>`).join('')}</tr>`).join('') + '</table>';
  const put = (id, html) => { const el = document.getElementById(id); if (!el) return; el.querySelector('.res-b').innerHTML = html; el.hidden = false; };
  const chk = name => (DATA.checks || []).find(c => c.check.startsWith(name));

  // ---- run line under the title
  const m = document.getElementById('res-meta');
  m.innerHTML = `<b>This run:</b> portfolio and rates as of ${esc(rec.as_of)} (curve ${esc(rec.curve_date)}) · calibration <b>${esc(CAL.official)}</b> `
    + `${esc(off.window_start)} to ${esc(off.window_end)}, id <code>${esc(CAL.calibration_id)}</code> (estimated ${esc(CAL.created_utc.slice(0, 10))}) · `
    + `${rec.simulation.n_paths.toLocaleString('en-US')} paths, seed ${rec.simulation.seed} · baseline: ${esc(rec.baseline.description)} · run ${esc(DATA.meta.run_at)}.`
    + (rec.calibration.flags.length ? ` <span class="warn">Flags: ${esc(rec.calibration.flags.join('; '))}.</span>` : '')
    + (rec.warnings.length ? ` <span class="warn">${esc(rec.warnings.join('; '))}.</span>` : '')
    + (rec.volatility && rec.volatility.type === 'market' ? ` <span class="warn">This run uses market-implied volatility (${esc(rec.volatility.description)}): the results blocks below describe the calibrated model, which this run uses only for the mortgage spread, β and tenor loadings.</span>` : '');
  m.hidden = false;

  // ---- 6.1 horizon fit (v1.2.0: momentum process fitted to forward deviations) and challengers
  const fit = k => dg[k + '_fit'], sf = fit('short'), lf = fit('long'), v11 = dg.v1_1_challenger || {};
  const hz = sf.fit.map(r => r.horizon_m);
  const rows61 = hz.map((h, i) => { const s = sf.fit[i], l = lf.fit.find(x => x.horizon_m === h) || {};
    return [h + ' months', f(s.empirical_bp, 0), f(s.model_bp, 0), f(s.ratio), f(s.ar1_ratio), f(l.empirical_bp, 0), f(l.model_bp, 0), f(l.ratio), f(l.ar1_ratio)]; });
  const tg = (dg.targets || {}).short || [];
  const v11row = k => { const c = v11[k]; return c ? c.horizons.map((h, i) => f(c.fan_over_target[i])) : []; };
  put('res-s61', `<p>Official calibration <b>${esc(off.name)}</b>, ${esc(off.window_start)} to ${esc(off.window_end)} (${off.n_obs} months). Target: standard deviation of overlapping h-month ${tgtName} in history; fan: the fitted factor's standard deviation h months ahead (bp). "AR(1)" is the challenger with momentum fixed at zero, fitted to the same targets.</p>`
    + tbl(['Horizon', 'Short: target', 'Short: fan', 'Ratio', 'AR(1) ratio', '10y: target', '10y: fan', 'Ratio', 'AR(1) ratio'], rows61)
    + `<p>Short factor: φ<sub>S</sub> = ${f(off.phi_short, 4)} (half-life ${f(hl(off.phi_short), 1)} years), momentum m<sub>S</sub> = ${f(off.mom_short, 2)}${sf.phi_at_bound ? ' <span class="warn">φ at the upper bound: flag raised</span>' : ''}${sf.mom_at_bound ? ' <span class="warn">momentum at a bound: flag raised</span>' : ''}; `
    + `10-year factor: φ<sub>L</sub> = ${f(off.phi_long, 4)} (half-life ${f(hl(off.phi_long), 1)} years), m<sub>L</sub> = ${f(off.mom_long, 2)}${lf.phi_at_bound ? ' <span class="warn">φ at a bound: flag raised</span>' : ''}. `
    + `The AR(1) challenger's φ<sub>S</sub> is ${f(sf.ar1_challenger.phi, 4)}${sf.ar1_challenger.phi_at_bound ? ' (at the upper bound)' : ''}.</p>`
    + (tg.length ? `<p>The forward is a biased forecast: the mean deviation of the short rate from its forward was ${tg.map(r => `${f(r.mean_bp, 0)} bp at ${r.horizon_m}m`).join(', ')} (realised rates below forwards, the term premium and the falling-rate decades). The fan width uses the standard deviation, which removes the mean; the bias is a property of the baseline, not of the fan (section 12, A1).</p>` : '')
    + (v11.short ? `<p>v1.1.0 challenger (first-order factors fitted to raw rate changes), fan width / target at ${v11.short.horizons.join(', ')} months: short ${v11row('short').join(' · ')}; 10-year ${v11row('y10').join(' · ')}.</p>` : '')
    + (sf.fit.concat(lf.fit).some(r => Math.abs(r.ratio - 1) > 0.15) ? `<p class="warn">The fitted fan misses the target by more than 15% at some horizons. Per section 6.1 the gap is reported, not adjusted.</p>` : ''));

  // ---- 6.2 correlation (v1.2.0: rho solved so the model's 12-month deviation correlation equals history's)
  const hc = dg.hist_dev_corr_h || {};
  put('res-s62', tbl(['Statistic', 'Value'], [['Target: correlation of 12-month ' + tgtName, f(dg.corr_12m_target)],
    ['Shape factor Σψ<sub>S</sub>ψ<sub>L</sub> / √(Σψ<sub>S</sub>² Σψ<sub>L</sub>²) at 12 months', f(dg.corr_shape_factor, 3)], ['ρ (shock correlation, used) = target / shape factor', f(off.rho)],
    ...Object.entries(hc).map(([h, v]) => [`History: ${h}-month ${tgtName}`, f(v)]), ['History: 12-month raw changes (v1.1.0 target)', f(dg.corr_12m_changes)], ['History: monthly changes', f(dg.corr_monthly_changes)]]));

  // ---- 6.3 mortgage (beta from 12-month deviations, spread fitted to the mortgage fan width)
  { const mf = dg.mort_fit, bh = dg.mort_beta_by_horizon || {};
    let h63 = tbl(['Parameter', 'Value'], [['β (mortgage to 10y, 12-month ' + tgtName + ')', f(off.beta_mort, 3)], ['R² of the β regression', f(dg.mort_beta_r2, 3)],
      ['φ<sub>M</sub> (fitted to the mortgage fan)', f(off.phi_spread, 4) + (mf && mf.phi_at_bound ? ' <span class="warn">at the upper bound: flag raised</span>' : '')],
      ['Spread half-life (years)', f(hl(off.phi_spread), 1)], ['Spread shock volatility (bp, annualized)', f(bpv(off.sigma_spread), 1)],
      ['Mean of mortgage − β × 10y (bp)', f(dg.spread_mean_bp, 0)], ['v1.1.0 challenger β (12-month raw changes)', v11.mort_beta != null ? f(v11.mort_beta, 3) : '—']]);
    if (Object.keys(bh).length) h63 += `<p>Pass-through by horizon (β of mortgage-rate ${tgtName} on 10-year ${tgtName}):</p>`
      + tbl(['Horizon', ...Object.keys(bh).map(k => k + ' months')], [['β', ...Object.values(bh).map(v => f(v, 2))]]);
    if (mf) h63 += `<p>Mortgage fan width vs the historical standard deviation of h-month mortgage-rate ${tgtName} (bp):</p>`
      + tbl(['Horizon', 'Target', 'Fan', 'Fan / target'], mf.fit.map(r => [r.horizon_m + ' months', f(r.empirical_bp, 0), f(r.model_bp, 0), f(r.ratio)]));
    put('res-s63', h63); }

  // ---- 6.4 loadings
  put('res-s64', tbl(['Tenor', 'a<sub>S</sub> (short factor)', 'a<sub>L</sub> (10y factor)'],
    Object.entries(off.tenor_loadings).map(([t, a]) => [t.replace('y', '') + '-year', f(a[0], 3), f(a[1], 3)])));

  // ---- 7 simulation
  const s = rec.simulation, rp = chk('Paths reproduce');
  const v5 = RM.v58 ? RM.v58.convergence : [];
  put('res-s7', tbl(['Setting', 'This run'], [['Official paths', s.tail_paths === s.n_paths ? `${s.n_paths.toLocaleString('en-US')} (every measure: 95% and 99%, expected shortfall); V5 benchmark ${Math.max(...s.convergence_paths).toLocaleString('en-US')}` : `${s.n_paths.toLocaleString('en-US')}; tail run ${s.tail_paths.toLocaleString('en-US')} (99% measures, expected shortfall)`],
      ['Horizon', `${s.n_months} months`], ['Antithetic; moment matching; sampler', `${s.antithetic ? 'on' : 'off'}; ${s.moment_match ? 'on' : 'off'}; ${esc(s.sampler)}`],
      ['Seed; floor', `${s.seed}; ${pc(s.floor)}`], ['Baseline', `${esc(rec.baseline.description)} (id ${esc(rec.baseline.id)})`],
      ['Path fingerprint', `<code>${esc(rec.paths_sha1)}</code>${rp ? ' · ' + (rp.status === 'PASS' ? '<span class="ok">regenerates exactly from the run record</span>' : '<span class="bad">does not regenerate</span>') : ''}`]], true)
    + (v5.length ? '<p>Precision achieved (V5): half-width of the 95% bootstrap interval on the 5th percentile, % of baseline income.</p>'
      + tbl(['Paths', 'Years', 'EaR 95%', '% of baseline', 'Interval half-width'], v5.map(r => [r.n_paths.toLocaleString('en-US'), r.years, fm(r.shortfall), pc(r.shortfall_pct), pc(r.ci_halfwidth_pct_of_baseline)])) : ''));

  // ---- 8 EaR
  const rows8 = [];
  ['cumulative', 'annual'].forEach(ms => DATA.ear[ms].forEach((r, i) => { const t = DATA.ear_tail[ms][i], c = r.conf['95'];
    rows8.push([`${ms === 'cumulative' ? (r.years === 1 ? 'Year 1' : 'Years 1–' + r.years) : 'Year ' + r.years + ' alone'}`, fv(r.baseline, 0), fv(c.income, 0), fv(c.shortfall, 1), pc(c.shortfall_pct),
      `${fv(c.shortfall_ci[0], 0)}–${fv(c.shortfall_ci[1], 0)}`, pc(r.mean_minus_baseline_pct), fv(t.conf['99'].shortfall, 1), fv(t.es['95'].shortfall, 1)]); }));
  put('res-s8', `<p>Portfolio: ${K.positions.toLocaleString('en-US')} CUSIPs, ${fm(K.upb)} face. Baseline: ${esc(rec.baseline.description)}. $m; ${s.tail_paths === s.n_paths ? `all figures from the official ${s.n_paths.toLocaleString('en-US')}-path run` : `95% figures from the official run, 99% and expected shortfall from ${s.tail_paths.toLocaleString('en-US')} paths`}.</p>`
    + tbl(['Horizon', 'Baseline income', 'Income at 95%', 'Shortfall $', 'Shortfall %', '95% interval (shortfall)', 'Mean − baseline', 'Shortfall at 99%', 'Expected shortfall 95%'], rows8));

  // ---- 10.2 parameters (all calibrations)
  const pr = [['Window', c => esc(c.window_start) + ' to ' + esc(c.window_end)], ['Months', c => c.n_obs],
    ['Short factor: monthly shock σ<sub>S</sub> (bp)', c => f(c.sigma_short * 1e4, 1)], ['Short factor: half-life (years)', c => f(hl(c.phi_short), 2)], ['Short factor: momentum m<sub>S</sub>', c => f(c.mom_short || 0, 2)],
    ['Short factor: fan st.dev. at 1 / 5 years (bp)', c => `${f(fan(c, 'short', 12) * 1e4, 0)} / ${f(fan(c, 'short', 60) * 1e4, 0)}`],
    ['Long factor: monthly shock σ<sub>L</sub> (bp)', c => f(c.sigma_long * 1e4, 1)], ['Long factor: half-life (years)', c => f(hl(c.phi_long), 2)], ['Long factor: momentum m<sub>L</sub>', c => f(c.mom_long || 0, 2)],
    ['Long factor: fan st.dev. at 1 / 5 years (bp)', c => `${f(fan(c, 'long', 12) * 1e4, 0)} / ${f(fan(c, 'long', 60) * 1e4, 0)}`],
    ['Short/long shock correlation', c => f(c.rho)], ['Mortgage beta to 10-year', c => f(c.beta_mort)],
    ['Spread: annualized shock volatility (bp)', c => f(bpv(c.sigma_spread), 1)], ['Spread: half-life (years)', c => f(hl(c.phi_spread), 2)],
    ['Parameters at a bound', c => [...['short', 'long'].flatMap(k => [c.diagnostics[k + '_fit'].phi_at_bound ? k + ' φ' : null, c.diagnostics[k + '_fit'].mom_at_bound ? k + ' momentum' : null]).filter(Boolean), ...(c.diagnostics.mort_fit && c.diagnostics.mort_fit.phi_at_bound ? ['spread φ'] : [])].join(', ') || 'none']];
  const t102 = document.getElementById('wp-calibration');
  if (t102) t102.innerHTML = `<table><tr><th>Parameter</th>${cals.map(c => `<th class="n">${esc(c.name)}${c.name === CAL.official ? ' (official)' : ''}</th>`).join('')}</tr>`
    + pr.map(([l, fn]) => `<tr><td>${l}</td>${cals.map(c => `<td class="n">${fn(c)}</td>`).join('')}</tr>`).join('') + '</table>'
    + `<p class="note">Model version ${esc(CAL.model_version)} · calibration id <code>${esc(CAL.calibration_id)}</code>, data through ${esc(CAL.data_through)}, estimated ${esc(CAL.created_utc.slice(0, 16).replace('T', ' '))} UTC · file <code>calibration/calibration.json</code>. Calibration target: ${CAL.target === 'forward_deviation' ? 'historical deviations from the forward baseline' : esc(CAL.target || 'raw changes')}. Spread volatility is the monthly shock standard deviation scaled by &radic;12; the factors are reported by fan width, since with momentum a monthly shock keeps moving the rate for several months.</p>`;

  // ---- 11 validation
  let h = '';
  const c1 = V.centering || [];
  h += `<p><b>V1 Centering</b> ${ok(c1.every(r => r.pass))}: ${c1.filter(r => r.pass).length} of ${c1.length} rate-months within 3 Monte Carlo standard errors of zero. Floor bias in the mean (bp):</p>`
    + tbl(['Rate', ...[...new Set(c1.map(r => r.month))].map(x => 'Month ' + x)], ['short', 'y10', 'mort'].map(k => [k, ...c1.filter(r => r.rate === k).map(r => f(r.floor_bias_bp, 1))]));
  const c2 = V.dispersion || [];
  const outside = c2.filter(r => r.model_over_hist != null && (r.model_over_hist < 0.85 || r.model_over_hist > 1.15));
  h += `<p><b>V2 Dispersion</b> ${ok(c2.every(r => r.pass))}: simulated vs analytic within 5% in ${c2.filter(r => r.pass).length} of ${c2.length}.</p>`
    + tbl(['Rate, month', 'Simulated (bp)', 'Analytic fan width (bp)', 'Sim / analytic', `Target: history, ${tgtName} (bp)`, 'Fan width / target'],
      c2.map(r => [`${r.rate}, ${r.month}`, f(r.simulated_bp, 0), f(r.analytic_bp, 0), f(r.sim_over_analytic, 3), f(r.target_bp != null ? r.target_bp : r.hist_change_std_bp, 0), f(r.model_over_hist)]))
    + (() => { const bad = c2.filter(r => r.rate === 'mort' && !r.pass);
        return bad.length ? `<p class="warn">Mortgage rows outside 5% of the analytic width (${bad.map(r => `${r.month}m: ${f(r.sim_over_analytic, 3)}`).join(', ')}): sampling error in the official path set. The 10-year and spread deviations are independent by design, but their sample correlation in these ${s.n_paths.toLocaleString('en-US')} paths is ${bad.map(r => f(r.sample_corr_10y_spread, 2)).join(' and ')} at those horizons, which narrows the combined mortgage fan. Moment matching (section 7) fixes each shock's variance, not the correlation between shocks; at ${s.tail_paths.toLocaleString('en-US')} paths the gap is within 1%. See V5.</p>` : ''; })()
    + (outside.length ? `<p class="warn">Fan-width-to-history ratios outside 0.85–1.15: ${outside.map(r => `${r.rate} ${r.month}m (${f(r.model_over_hist)})`).join(', ')}. To be explained by the model owner (criterion V2); the fits in sections 6.1 and 6.3 show where the processes depart from history.</p>` : `<p>Fan width within 0.85–1.15 of the historical target at every horizon.</p>`);
  const c3 = (V.correlation || [])[0];
  if (c3) h += `<p><b>V3 Co-movement</b> ${ok(c3.pass)}: correlation of the short-rate and 10-year deviations 12 months ahead ${f(c3.sim_corr_12m != null ? c3.sim_corr_12m : c3.sim_corr_12m_changes)} simulated vs ${f(c3.hist_corr_12m != null ? c3.hist_corr_12m : c3.hist_corr_12m_changes)} history (${tgtName}; within 0.10 required). Curve inversion frequency: ${pc(c3.sim_inversion_freq, 1)} simulated, ${pc(c3.baseline_inversion_freq, 1)} baseline, ${pc(c3.hist_inversion_freq, 1)} history. Mortgage-to-10y correlation at 12 months ${f(c3.sim_corr_mort_y10_12m)} simulated vs ${f(c3.hist_corr_mort_y10_12m)} history.</p>`;
  const v4 = RM.v4;
  h += `<p><b>V4 Floor incidence</b> ${v4.triggered ? '<span class="warn">trigger breached: rerun disclosed</span>' : '<span class="ok">below the trigger</span>'}:</p>`
    + tbl(['Rate', 'Path-months at floor', 'Paths touching floor'], (V.floor || []).map(r => [r.rate, pc(r.share_path_months_at_floor, 2), pc(r.share_paths_touching_floor, 1)]))
    + (v4.triggered ? tbl(['Horizon', `EaR 95% at ${pc(v4.floor)}`, `EaR 95% at ${pc(v4.alt_floor)}`], v4.rows.filter(r => r.measure === 'cumulative').map(r => [r.years + 'y', fm(r.official_shortfall), fm(r.shortfall)])) : '');
  if (RM.v58) {
    h += `<p><b>V5 Convergence</b> ${ok(RM.v58.convergence_pass)}: criterion: the ${Math.max(...RM.v58.convergence.map(r => r.n_paths)).toLocaleString('en-US')}-path 5th percentile lies inside the official ${s.n_paths.toLocaleString('en-US')}-path 95% interval (computed ${esc(RM.v58.computed_utc.slice(0, 10))} for this calibration, portfolio of ${esc(RM.v58.portfolio.as_of)}).</p>`
      + tbl(['Paths', 'Years', 'EaR 95%', '% of baseline', 'P5 interval low', 'P5 interval high'], RM.v58.convergence.map(r => [r.n_paths.toLocaleString('en-US'), r.years, fm(r.shortfall), pc(r.shortfall_pct), fm(r.ci_low), fm(r.ci_high)]));
  }
  const v6 = RM.v6;
  h += `<p><b>V6 Benchmark shocks</b> ${v6.pass ? '<span class="ok">direction consistent</span>' : '<span class="bad">direction inconsistent</span>'}: instantaneous parallel shifts of every rate, located in the simulated distribution of cumulative income.</p>`
    + tbl(['Shock', 'Years', 'Income change', 'Percentile in distribution'], v6.rows.map(r => [(r.shock_bp > 0 ? '+' : '') + r.shock_bp + ' bp', r.years, pc(r.income_change_pct), f(r.percentile_in_distribution, 1)]));
  const v7 = CAL.parameter_uncertainty || [];
  h += `<p><b>V7 Estimation error</b> (200 refits of simulated histories of the window's length, official calibration; each history's deviations are measured from its own conditional expectation, the model analogue of the forward): re-estimated fan width relative to its bootstrap mean, the mean ratio to the generating parameters (1 = unbiased; a few % below 1 at 5 years is the small-sample bias of long-horizon standard deviations) and the 5–95% range of the re-estimated momentum.</p>`
    + tbl(['Rate, month', 'Fitted (bp)', 'Relative std. dev.', 'P5 relative', 'P95 relative', 'Mean ratio to generator', 'Momentum: fitted (P5–P95 of refits)'], v7.map(r => [`${r.rate}, ${r.month}`, f(r.fitted_dispersion_bp, 0), pc(r.relative_sd, 1), f(r.p05_relative), f(r.p95_relative), f(r.mean_ratio_to_generator), r.mom != null ? `${f(r.mom)} (${f(r.mom_p05)}–${f(r.mom_p95)})` : '—']));
  if (RM.v58) {
    const sens = RM.v58.sensitivity.filter(r => r.measure === 'cumulative'), names = [...new Set(sens.map(r => r.calibration))], ys = [...new Set(sens.map(r => r.years))];
    h += `<p><b>V8 Calibration sensitivity</b>: cumulative EaR 95% as % of baseline under each named calibration (official in bold).</p>`
      + tbl(['Years', ...names.map(n => n === CAL.official ? `<b>${esc(n)}</b>` : esc(n))], ys.map(y => [y, ...names.map(n => { const r = sens.find(x => x.calibration === n && x.years === y); return n === CAL.official ? `<b>${pc(r.shortfall_pct)}</b>` : pc(r.shortfall_pct); })]));
  }
  put('res-s11', h);

  // ---- 13.3 run record
  const sm = [['Short σ (bp/month)', off.sigma_short * 1e4, 1], ['Short half-life (yrs)', hl(off.phi_short), 2], ['Short momentum', off.mom_short || 0, 2], ['Long σ (bp/month)', off.sigma_long * 1e4, 1], ['Long half-life (yrs)', hl(off.phi_long), 2], ['Long momentum', off.mom_long || 0, 2],
    ['ρ', off.rho, 2], ['β', off.beta_mort, 3], ['Spread vol (bp)', bpv(off.sigma_spread), 1], ['Spread half-life (yrs)', hl(off.phi_spread), 2]];
  put('res-s133', tbl(['Item', 'Value'], [['Model version', esc(rec.model_version)], ['Calibration name; window', `${esc(CAL.official)}; ${esc(off.window_start)} to ${esc(off.window_end)}`],
    ['Calibration id; estimated', `<code>${esc(CAL.calibration_id)}</code>; ${esc(CAL.created_utc.slice(0, 16).replace('T', ' '))} UTC`],
    ['Parameter values', sm.map(([l, v, d]) => `${l} ${f(v, d)}`).join(' · ')], ['Seed; paths', `${s.seed}; ${s.n_paths.toLocaleString('en-US')} official${s.tail_paths !== s.n_paths ? ', ' + s.tail_paths.toLocaleString('en-US') + ' tail' : ''}`],
    ['Baseline identifier', `${esc(rec.baseline.type)} · id ${esc(rec.baseline.id)}`], ['Rate run', `${esc(rec.created_utc.slice(0, 16).replace('T', ' '))} UTC · paths <code>${esc(rec.paths_sha1)}</code>`],
    ['Income run', `${esc(DATA.meta.run_at)} · ${esc(DATA.meta.portfolio_file)}`]], true));
  if (location.hash) { const t = document.querySelector(location.hash); if (t) t.scrollIntoView(); }
};
