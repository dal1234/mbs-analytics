// Whole loans view (ILLUSTRATIVE): single-family cohorts in runoff (mbs_lite/wholeloans.py). Interest income and
// expected credit losses are separate lines. Follows the page horizon (HZ, MS) and the followed scenario (FOCUS).
const WL = D.whole_loans || null;
const WS = {current: ['Current', '#1F4E8C'], dq: ['30–89 days', '#E39455'], sdq: ['90+ days (nonaccrual)', '#B23434'], cash: ['Runoff cash (SOFR)', '#8FB0DC']};
const WB = {performing: 'Performing', reperforming: 'Reperforming', modified: 'Modified', delinquent_buyout: 'Delinquent buyout'};
const wv = id => WL.scenarios[id];
const winc = id => hsum(wv(id).interest), wcash = id => hsum(wv(id).cash_income), wloss = id => hsum(wv(id).loss);
const wnet = id => winc(id) + wcash(id) - wloss(id);
const wavg = id => yrs().reduce((a, y) => a + wv(id).avg_loans[y], 0) / yrs().length;     // average loan UPB over the horizon
const wyld = id => 100 * winc(id) / wavg(id) / yrs().length;                                // effective yield, % a year
const wlyld = id => 100 * (winc(id) - wloss(id)) / wavg(id) / yrs().length;                 // loss-adjusted yield
const wsw = c => `<span class="sw" style="background:${c}"></span>`;

function renderWLBook(){
  const b = RBASE, rep = WL.reported || {};
  $('wbTiles').innerHTML = [
    ['Loan UPB', fm(WL.upb), `${WL.cohorts.length} cohorts · single family · runoff`],
    ['Note rate', fp(WL.note_rate, 2), 'UPB-weighted, before servicing'],
    ['Accruing today', fp(100 * WL.accruing_share, 0), `90+ days delinquent loans (${fp(100 - 100 * WL.accruing_share, 0)}) accrue nothing`],
    [`Effective yield · ${hshort()}`, fp(wyld(b), 2), 'interest income / average loan UPB, base path'],
    [`Expected credit losses · ${hshort()}`, fm(wloss(b)), `${fp(100 * wloss(b) / wavg(b) / yrs().length, 2)} a year of average UPB`],
    ['Loss-adjusted yield', fp(wlyld(b), 2), 'effective yield minus expected losses'],
    [`Income after losses · ${hshort()}`, fm(wnet(b)), `incl. ${fm(wcash(b))} on runoff cash at SOFR`],
    ['Reported yield (filing)', rep.yield_pct != null ? fp(rep.yield_pct, 2) : '—', rep.period ? `${esc(rep.period)} · ${esc(rep.note || '')}` : 'not configured'],
  ].map(([l, v, s]) => `<div class="tile"><span class="l">${l}</span><span class="mono" style="font-size:15px;font-weight:600">${v}</span><span style="font-size:11px;color:#5B6572">${s}</span></div>`).join('');
  $('wbBody').innerHTML = WL.cohorts.map((c, n) => { const ci = hsum(wv(b).cohort_interest[n]), cl = hsum(wv(b).cohort_loss[n]);
    return `<tr><td>${esc(c.id)}</td><td>${WB[c.behavior] || esc(c.behavior)}</td><td>${wsw(WS[c.status][1])} ${WS[c.status][0]}</td><td>${fv(c.upb, 0)}</td><td>${fp(c.note_rate, 2)}</td><td>${c.deferred_share ? fp(100 * c.deferred_share, 0) : '—'}</td><td>${num(c.ltv, 0)}%</td><td>${c.fico == null ? '—' : num(c.fico, 0)}</td><td>${num(c.wam, 0)}</td><td>${fv(ci, 0)}</td><td>${fv(cl, 1)}</td></tr>`; }).join('')
    + `<tr class="base"><td>Total</td><td></td><td></td><td>${fv(WL.upb, 0)}</td><td>${fp(WL.note_rate, 2)}</td><td></td><td></td><td></td><td></td><td>${fv(winc(b), 0)}</td><td>${fv(wloss(b), 1)}</td></tr>`;
  $('wbIncHead').textContent = `Interest ${hshort()}`; $('wbLossHead').textContent = `Losses ${hshort()}`;
  $('wbSrc').textContent = WL.source || ''; }

function renderWLGrid(){
  const secs = gridSections(), bn = wnet(RBASE), mx = Math.max(1, ...RSD.catalog.map(c => Math.abs(wnet(c.id) - bn)));
  const row = id => { const b = id === RBASE, d = wnet(id) - bn;
    return `<tr class="click${b ? ' base' : ''}${id === FOCUS ? ' on' : ''}" onclick="setFocus('${id}')" title="${esc(RSC[id].desc)} Click to follow.">`
      + `<td>${esc(sname(id))}</td><td>${fv(winc(id), 0)}</td><td>${fv(wcash(id), 0)}</td><td>${fd(-wloss(id), 0)}</td><td><b>${fv(wnet(id), 0)}</b></td>`
      + `<td>${b ? '—' : dheat(d, mx)}</td><td>${fp(wyld(id), 2)}</td><td>${fp(wlyld(id), 2)}</td><td>${fp(yrs().reduce((a, y) => a + wv(id).cpr[y], 0) / yrs().length, 1)}</td></tr>`; };
  let h = '';
  secs.forEach(s => { const open = s.key !== 'alt' || LALT;
    h += `<tr class="sec${s.key === 'alt' ? ' click' : ''}"${s.key === 'alt' ? ' onclick="LALT=!LALT;renderLiab();renderWL()"' : ''}><td colspan="9">${s.key === 'alt' ? `<span class="car">${open ? '▾' : '▸'}</span>` : ''}${s.label}${open ? '' : `<span class="cnt">${s.rows.length} scenarios</span>`}</td></tr>`;
    if (open) h += s.rows.map(row).join(''); });
  $('wgBody').innerHTML = h;
  $('wgSub').textContent = `$m over the ${hlab()} horizon · interest income and expected credit losses as separate lines · losses are the same in every rate scenario because house prices and unemployment stay on the baseline · click a row to follow it`; }

// monthly income above the axis, losses below
function renderWLFollow(){
  const id = FOCUS || RBASE, v = wv(id), bse = wv(RBASE), n = WL.months, isB = id === RBASE, w = mwin();
  const W = 1100, H = 260, L = 56, R = 14, Tp = 12, B = 232, bw = (W - L - R) / n;
  const up = [...Array(n).keys()].map(i => (v.m_interest[i] + v.m_cash_income[i]) / 1e6), dn = v.m_loss.map(x => x / 1e6);
  const [lo, hi, step] = niceTicks(-Math.max(...dn, 1), Math.max(...up), 6), Yv = x => Tp + (hi - x) / (hi - lo) * (B - Tp);
  let g = `<rect x="${(L + w[0] * bw).toFixed(1)}" y="${Tp}" width="${((w[1] - w[0]) * bw).toFixed(1)}" height="${B - Tp}" fill="#B23434" fill-opacity="0.04"/>`;
  for (let x = lo; x <= hi + 1e-9; x += step) g += `<line x1="${L}" x2="${W - R}" y1="${Yv(x).toFixed(1)}" y2="${Yv(x).toFixed(1)}" stroke="${Math.abs(x) < 1e-9 ? '#9AA4AF' : '#E3E7EC'}"/>` + T(L - 6, (Yv(x) + 3).toFixed(1), (x < 0 ? MINUS : '') + num(Math.abs(x), step < 1 ? 1 : 0), {a: 'end'});
  for (let i = 0; i < n; i++) { const x = (L + i * bw + 0.5).toFixed(1), ww = Math.max(1, bw - 1).toFixed(1), a = v.m_interest[i] / 1e6, c = v.m_cash_income[i] / 1e6, l = dn[i];
    g += `<rect x="${x}" y="${Yv(a).toFixed(1)}" width="${ww}" height="${(Yv(0) - Yv(a)).toFixed(1)}" fill="#1F4E8C"/><rect x="${x}" y="${Yv(a + c).toFixed(1)}" width="${ww}" height="${(Yv(a) - Yv(a + c)).toFixed(1)}" fill="#8FB0DC"/>`
      + `<rect x="${x}" y="${Yv(0).toFixed(1)}" width="${ww}" height="${(Yv(-l) - Yv(0)).toFixed(1)}" fill="#B23434"/>`
      + `<rect x="${(L + i * bw).toFixed(1)}" y="${Tp}" width="${bw.toFixed(1)}" height="${B - Tp}" fill="transparent"><title>${mlab(i + 1)}: interest $${num(a, 1)}m · cash at SOFR $${num(c, 1)}m · expected losses $${num(l, 1)}m · after losses $${num(a + c - l, 1)}m</title></rect>`; }
  const ln = (f, col, wd, d) => `<polyline points="${[...Array(n).keys()].map(i => (L + i * bw + bw / 2).toFixed(1) + ',' + Yv(f(i)).toFixed(1)).join(' ')}" fill="none" stroke="${col}" stroke-width="${wd}"${d ? ` stroke-dasharray="${d}"` : ''} pointer-events="none"/>`;
  if (!isB) g += ln(i => (bse.m_interest[i] + bse.m_cash_income[i] - bse.m_loss[i]) / 1e6, PURPLE, 1.5, '4 3');
  g += ln(i => up[i] - dn[i], '#15202B', 2);
  for (let i = 0; i < n; i += 12) g += T((L + i * bw + bw / 2).toFixed(1), B + 14, mlab(i + 1), {a: 'middle'});
  g += T(L, H - 2, '$m per month');
  $('wmChart').innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Monthly whole-loan income and expected losses, ${esc(sname(id))}">${g}</svg>`;
  $('wmTitle').firstChild.textContent = `Monthly income and expected losses — ${sname(id)}`;
  $('wmSub').textContent = `$m per month · interest income and SOFR on runoff cash above the axis, expected credit losses below · line = income after losses · ${hlab()} window shaded · ${isB ? 'follow a scenario to compare it with the base' : 'base dashed'}`;
  $('wmLegend').innerHTML = `<span>${wsw('#1F4E8C')}Interest income</span><span>${wsw('#8FB0DC')}Runoff cash at SOFR</span><span>${wsw('#B23434')}Expected credit losses</span><span>${sline('#15202B')}After losses</span>` + (isB ? '' : `<span>${sline(PURPLE, 1.5, '4 3')}Base after losses</span>`);
  $('wmTiles').innerHTML = [[`Interest income, ${hlab()}`, fm(winc(id))], ['Runoff cash at SOFR', fm(wcash(id))], ['Expected credit losses', fm(wloss(id))],
    ['After losses', fm(wnet(id))], ['vs base', isB ? '—' : fms(wnet(id) - wnet(RBASE))], ['Effective / loss-adjusted yield', `${fp(wyld(id), 2)} / ${fp(wlyld(id), 2)}`]]
    .map(([a, x]) => `<div class="tile"><span class="l">${a}</span><span class="mono">${x}</span></div>`).join('');
  // balances by status
  $('wbalChart').innerHTML = lchartStack(['current', 'dq', 'sdq', 'cash'].map(k => ({y: v.m_bal[k].map(x => x / 1e9), c: WS[k][1], l: WS[k][0] + ' $bn'})), n, x => num(x, 0), null, 560);
  $('wbalTitle').firstChild.textContent = `Balances by status — ${sname(id)}`;
  $('wbalLegend').innerHTML = ['current', 'dq', 'sdq', 'cash'].map(k => `<span>${wsw(WS[k][1])}${WS[k][0]}</span>`).join('');
  // by year
  $('wyBody').innerHTML = v.interest.map((x, y) => `<tr${yrs().includes(y) ? ' class="hzr"' : ''}><td>Y${y + 1}</td><td>${fv(v.avg_loans[y], 0)}</td><td>${fv(x, 0)}</td><td>${fv(v.cash_income[y], 0)}</td><td>${fd(-v.loss[y], 1)}</td><td><b>${fv(x + v.cash_income[y] - v.loss[y], 0)}</b></td><td>${fp(100 * x / v.avg_loans[y], 2)}</td><td>${fp(100 * (x - v.loss[y]) / v.avg_loans[y], 2)}</td><td>${fv(v.sched[y], 0)}</td><td>${fv(v.prepaid[y], 0)}</td><td>${fv(v.liquidated[y], 0)}</td><td>${fp(v.cpr[y], 1)}</td></tr>`).join('');
  $('wyTitle').firstChild.textContent = `By year — ${sname(id)}`; }

function renderWLAssump(){
  const B = WL.behavior, ks = ['roll_cd', 'cure_dc', 'roll_ds', 'cure_sc', 'liq_s', 'prepay_mult'], used = [...new Set(WL.cohorts.map(c => c.behavior))];
  $('waBody').innerHTML = used.map(b => `<tr><td>${WB[b] || esc(b)}</td>${ks.map(k => `<td>${k === 'prepay_mult' ? num(B[b][k], 2) + '×' : fp(100 * B[b][k], 1)}</td>`).join('')}</tr>`).join('');
  const M = WL.macro, s = WL.severity, arr = x => Array.isArray(x) ? x.map(v => num(v, 1)).join(' / ') : num(x, 1);
  $('waText').innerHTML = `<p><b>Transitions</b> are monthly shares of the balance in each state, constant over time (cohort-level averages, not a fitted model). Loans 90+ days delinquent accrue no interest; loans that cure go back to current and resume paying. Modified loans carry a deferred, non-interest-bearing balance.</p>`
    + `<p><b>Severity</b> = max(${fp(100 * s.min_severity, 0)}, 1 − (1 − ${fp(100 * s.liquidation_cost, 0)}) / LTV(t)), with LTV(t) = starting LTV / house price index. <b>Macro baseline</b> (held on every rate path): house prices ${arr(M.hpi_growth_pct)}% a year (index ${num(M.hpi_index[M.hpi_index.length - 1], 3)} at month ${WL.months}); unemployment ${arr(M.unemployment_pct)}%, roll rates scaled by (unemployment / today)^${num(M.unemployment_elasticity, 1)}. So credit losses do not change with the rate scenario; only prepayments (mortgage rate) and runoff cash (SOFR) do.</p>`
    + `<p><b>Not modelled:</b> GAAP discount accretion (cash coupon only), CECL allowances, foreclosure timelines by state, modification redefault curves, loan sales, multifamily. Set the cohorts, rates and baselines in <code>whole_loans</code> (config/model.yaml).</p>`; }

function renderWL(){
  if (!WL) { $('view-wl').innerHTML = '<div class="card">No whole-loan cohorts in this run (set whole_loans.file in config/model.yaml and rerun run_model.py).</div>'; return; }
  renderWLBook(); renderWLGrid(); renderWLFollow(); renderWLAssump(); }
