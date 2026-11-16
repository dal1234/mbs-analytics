// Liabilities view: interest expense on the debt book (mbs_lite/funding.py) along the same rate scenarios and Monte
// Carlo paths. Separate from asset income for now (not netted). Follows the page horizon (HZ, MS) and FOCUS.
const FD = D.funding || null;
const LT = {discount_note: ['Discount notes', '#8FB0DC'], floating: ['Floating (SOFR)', '#4F7FBF'], fixed: ['Fixed bullets', '#1F4E8C'], callable: ['Callable', '#D9772B']};
let LALT = false;
const lsc = id => FD.scenarios[id];
const lh = id => hsum(lsc(id).interest);                         // interest expense over the horizon
const ldel = id => lh(id) - lh(RBASE);
const lfollow = () => FOCUS || RBASE;
const lsw = t => `<span class="sw" style="background:${LT[t][1]}"></span>`;
const mlabQ = q => { const d = new Date(AS0); d.setUTCDate(1); d.setUTCMonth(d.getUTCMonth() + 3 * q + 1); return `Q${Math.floor(d.getUTCMonth() / 3) + 1} '${String(d.getUTCFullYear()).slice(2)}`; };

// ---------------------------------------------------------------- debt book + maturity profile
function renderLiabBook(){
  const e = FD.mc[MS][HZ - 1], far = e.pct['95'] - e.base, rep = FD.reported || {};
  $('lbTiles').innerHTML = [
    ['Debt outstanding', fm(FD.face), `${FD.rows} rows · ${esc(FD.name)}`],
    ['Average rate today', fp(FD.rate_now, 2), 'coupon or yield; floaters at 1M SOFR + spread'],
    ['Reprices within 12 months', fp(100 * FD.reprice_12m, 0), 'floating, discount notes and debt maturing within a year'],
    ['Callable', fm(FD.book.reduce((a, g) => a + g.callable, 0)), `${fp(100 * FD.book.reduce((a, g) => a + g.callable, 0) / FD.face, 0)} of the book`],
    ['Weighted-average maturity', num(FD.wam_months, 0) + ' months', 'to contractual maturity'],
    [`Interest expense · ${hshort()}`, fm(lh(RBASE)), `on the baseline rate path · ${fp(100 * lh(RBASE) / FD.face / yrs().length, 2)} a year`],
    ['Funding cost at risk 95%', fms(far), `P95 of ${FD.n_paths.toLocaleString('en-US')} paths minus the baseline, ${hlab()}`],
    ['Reported cost (filing)', rep.cost_pct != null ? fp(rep.cost_pct, 2) : '—', rep.period ? `${esc(rep.period)} · incl. discount amortization and hedges` : 'not configured'],
  ].map(([l, v, s]) => `<div class="tile"><span class="l">${l}</span><span class="mono" style="font-size:15px;font-weight:600">${v}</span><span style="font-size:11px;color:#5B6572">${s}</span></div>`).join('');
  $('lbBody').innerHTML = FD.book.map(g => `<tr><td>${lsw(g.type)} ${esc(g.group)}</td><td>${(g.types || [g.type]).map(t => LT[t][0].split(' (')[0].replace(' bullets', '')).join(' + ')}</td><td>${fv(g.face, 0)}</td><td>${fp(100 * g.face / FD.face, 1)}</td><td>${fp(g.rate, 2)}</td><td>${String(g.first).slice(0, 4) === String(g.last).slice(0, 4) ? esc(String(g.first).slice(0, 4)) : esc(String(g.first).slice(0, 4)) + '–' + esc(String(g.last).slice(0, 4))}</td><td>${g.callable ? fv(g.callable, 0) : '—'}</td></tr>`).join('')
    + `<tr class="base"><td>Total</td><td></td><td>${fv(FD.face, 0)}</td><td>100.0%</td><td>${fp(FD.rate_now, 2)}</td><td></td><td>${fv(FD.book.reduce((a, g) => a + g.callable, 0), 0)}</td></tr>`;
  $('lbSrc').textContent = FD.source || '';
  // maturity profile: contractual maturities by quarter and type; first call dates of callables
  const P = FD.profile, n = P.first_calls.length, W = 720, H = 230, L = 50, R = 14, Tp = 14, B = 196, bw = (W - L - R) / n;
  const tot = i => FD.types.reduce((a, t) => a + P.by_type[t][i], 0), mx = Math.max(...[...Array(n).keys()].map(i => Math.max(tot(i), P.first_calls[i]))) / 1e9;
  const [, hi, step] = niceTicks(0, mx, 4), Yv = v => B - v / 1e9 / hi * (B - Tp);
  let g = '';
  for (let v = 0; v <= hi + 1e-9; v += step) g += `<line x1="${L}" x2="${W - R}" y1="${Yv(v * 1e9).toFixed(1)}" y2="${Yv(v * 1e9).toFixed(1)}" stroke="#E3E7EC"/>` + T(L - 6, (Yv(v * 1e9) + 3).toFixed(1), num(v, 0), {a: 'end'});
  for (let i = 0; i < n; i++) { let a = 0; const x = L + i * bw;
    FD.types.forEach(t => { const v = P.by_type[t][i]; if (!v) return;
      g += `<rect x="${(x + 2).toFixed(1)}" y="${Yv(a + v).toFixed(1)}" width="${(bw - 4).toFixed(1)}" height="${(Yv(a) - Yv(a + v)).toFixed(1)}" fill="${LT[t][1]}"><title>${i === n - 1 ? 'Later' : mlabQ(i)}: ${LT[t][0]} maturing $${fv(v, 0)}m</title></rect>`; a += v; });
    if (P.first_calls[i]) g += `<line x1="${(x + 3).toFixed(1)}" x2="${(x + bw - 3).toFixed(1)}" y1="${Yv(P.first_calls[i]).toFixed(1)}" y2="${Yv(P.first_calls[i]).toFixed(1)}" stroke="#A84F15" stroke-width="3"><title>${mlabQ(i)}: $${fv(P.first_calls[i], 0)}m of callable debt becomes callable</title></line>`;
    if (i % 2 === 0 || i === n - 1) g += T((x + bw / 2).toFixed(1), B + 14, i === n - 1 ? 'Later' : mlabQ(i), {a: 'middle'}); }
  g += T(L, H - 4, '$bn by quarter');
  $('lbProfile').innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Contractual maturities of the debt book by quarter and type, with first call dates">${g}</svg>`;
  $('lbLegend').innerHTML = FD.types.map(t => `<span>${lsw(t)}${LT[t][0]} maturing</span>`).join('') + `<span>${sline('#A84F15', 3)}Callable from this quarter</span>`; }

// ---------------------------------------------------------------- funding cost by scenario + distribution
function renderLiabGrid(){
  const Yn = FD.years, secs = gridSections(), bh = lh(RBASE), mx = Math.max(1, ...RSD.catalog.map(c => Math.abs(ldel(c.id))));
  $('lgHead').innerHTML = `<tr><th>Scenario</th>${[...Array(Yn).keys()].map(y => `<th${yrs().includes(y) ? ' class="hz"' : ''}>Y${y + 1}</th>`).join('')}<th>${hshort()}</th><th>Δ vs base</th><th>Δ %</th><th>Called</th><th>MC${tip('Share of the Monte Carlo paths whose funding cost over the same horizon is at or below this scenario.')}</th></tr>`;
  const row = id => { const v = lsc(id), b = id === RBASE, d = ldel(id);
    return `<tr class="click${b ? ' base' : ''}${id === FOCUS ? ' on' : ''}" onclick="setFocus('${id}')" title="${esc(RSC[id].desc)} Click to follow.">`
      + `<td>${esc(sname(id))}</td>${v.interest.map((x, y) => `<td${yrs().includes(y) ? ' class="hz"' : ''}>${fv(x, 0)}</td>`).join('')}<td><b>${fv(hsum(v.interest), 0)}</b></td>`
      + `<td>${b ? '—' : dheat(-d, mx).replace(/>([^<]*)<\/span>$/, '>' + fd(d, 0) + '</span>')}</td><td>${b ? '—' : fpc(100 * d / bh)}</td><td>${hsum(v.called) ? fv(hsum(v.called), 0) : '—'}</td><td>${pctl(FD.mc_pct[id][MS][HZ - 1])}</td></tr>`; };
  let h = '';
  secs.forEach(s => { const open = s.key !== 'alt' || LALT;
    h += `<tr class="sec${s.key === 'alt' ? ' click' : ''}"${s.key === 'alt' ? ' onclick="LALT=!LALT;renderLiab()"' : ''}><td colspan="${Yn + 6}">${s.key === 'alt' ? `<span class="car">${open ? '▾' : '▸'}</span>` : ''}${s.label}${open ? '' : `<span class="cnt">${s.rows.length} scenarios</span>`}</td></tr>`;
    if (open) h += s.rows.map(row).join(''); });
  $('lgBody').innerHTML = h;
  $('lgSub').textContent = `$m · interest expense by year, the ${hlab()} total and its change vs the base (red = costs more) · called = callable debt redeemed over the horizon · click a row to follow it`;
  // Monte Carlo distribution of funding cost
  const e = FD.mc[MS][HZ - 1], hs = e.hist, ed = e.edges, hm = Math.max(...hs), q = e.pct, W = 520, Hh = 190, L = 10, Rr = 10, Tp = 26, Bm = 34;
  const lo = Math.min(ed[0], e.base), hi = Math.max(ed[ed.length - 1], e.base), X = v => L + (W - L - Rr) * (v - lo) / (hi - lo), bw = X(ed[1]) - X(ed[0]);
  let g = hs.map((c, i) => { const m = (ed[i] + ed[i + 1]) / 2; return `<rect x="${(X(ed[i]) + 1).toFixed(1)}" y="${(Tp + (Hh - Tp - Bm) * (1 - c / hm)).toFixed(1)}" width="${Math.max(1, bw - 2).toFixed(1)}" height="${((Hh - Tp - Bm) * c / hm).toFixed(1)}" fill="${m > q['95'] ? '#B23434' : m < q['5'] ? '#C9D1DA' : (m >= q['25'] && m <= q['75']) ? '#4F7FBF' : '#8FB0DC'}"><title>$${fv(ed[i], 0)}–${fv(ed[i + 1], 0)}m: ${c} paths</title></rect>`; }).join('');
  [[q['95'], 'P95', '#B23434', '3 3'], [q['50'], 'Median', '#15202B', ''], [e.base, 'Base', PURPLE, '5 3']].forEach(([v, l, c, d]) => { g += `<line x1="${X(v).toFixed(1)}" x2="${X(v).toFixed(1)}" y1="${Tp}" y2="${Hh - Bm}" stroke="${c}" stroke-width="1.5" stroke-dasharray="${d}"/>`; });
  spread([[q['95'], 'P95', '#B23434'], [q['50'], 'Median', '#15202B'], [e.base, 'Base', PURPLE]].map(([v, l, c]) => ({t: `${l} ${fv(v, 0)}`, c, y: X(v)})), 64, L, W - Rr)
    .forEach(l => { g += `<text x="${l.y.toFixed(1)}" y="${Hh - Bm + 12}" text-anchor="middle" style="fill:${l.c}">${l.t}</text>`; });
  const xb = X(e.base), x95 = X(q['95']);
  g += `<line x1="${xb.toFixed(1)}" x2="${x95.toFixed(1)}" y1="${Tp - 8}" y2="${Tp - 8}" stroke="#B23434"/>` + `<text x="${((xb + x95) / 2).toFixed(1)}" y="${Tp - 13}" text-anchor="middle" style="fill:#B23434;font-weight:600">Funding cost at risk 95%: ${fms(q['95'] - e.base)}</text>`
    + `<text x="${L}" y="${Hh - 3}">${hlab()} interest expense, $m · ${FD.n_paths.toLocaleString('en-US')} paths · red = above P95</text>`;
  $('ldHist').innerHTML = `<svg viewBox="0 0 ${W} ${Hh}" width="100%" role="img" aria-label="Distribution of ${hlab()} interest expense across the Monte Carlo paths">${g}</svg>`;
  $('ldTable').innerHTML = [['Base / mean', `${fm(e.base)} / ${fm(e.mean)}`], ['P5 / median / P95', `${fm(q['5'])} / ${fm(q['50'])} / ${fm(q['95'])}`],
    ['Funding cost at risk 95% (P95 − base)', fms(q['95'] - e.base)], ['Same paths as EaR', 'yes: a path with high funding cost is a high-rate path, where asset income is usually higher too (netting comes later)']]
    .map(([a, b]) => `<tr><td>${a}</td><td>${b}</td></tr>`).join(''); }

// ---------------------------------------------------------------- followed scenario: monthly cost, balances, flows
function lchartStack(series, n, yfmt, total, W = 1100){ const H = W < 800 ? 280 : 240, L = 56, R = 14, Tp = 12, B = H - 28, bw = (W - L - R) / n;
  const top = [...Array(n).keys()].map(i => series.reduce((a, s) => a + s.y[i], 0)), mx = Math.max(...top, ...(total ? total.flatMap(t => t.y) : []));
  const [lo, hi, step] = niceTicks(0, mx, 5), Yv = v => B - (v - lo) / (hi - lo) * (B - Tp), w = mwin();
  let g = `<rect x="${(L + w[0] * bw).toFixed(1)}" y="${Tp}" width="${((w[1] - w[0]) * bw).toFixed(1)}" height="${B - Tp}" fill="#B23434" fill-opacity="0.04"/>`;
  for (let v = lo; v <= hi + 1e-9; v += step) g += `<line x1="${L}" x2="${W - R}" y1="${Yv(v).toFixed(1)}" y2="${Yv(v).toFixed(1)}" stroke="#E3E7EC"/>` + T(L - 6, (Yv(v) + 3).toFixed(1), yfmt(v), {a: 'end'});
  for (let i = 0; i < n; i++) { let a = 0; const x = (L + i * bw + 0.5).toFixed(1);
    series.forEach(s => { const v = s.y[i]; if (v <= 0) return; g += `<rect x="${x}" y="${Yv(a + v).toFixed(1)}" width="${Math.max(1, bw - 1).toFixed(1)}" height="${(Yv(a) - Yv(a + v)).toFixed(1)}" fill="${s.c}"><title>${mlab(i + 1)}: ${s.l} ${yfmt(v)}</title></rect>`; a += v; }); }
  (total || []).forEach(t => { g += `<polyline points="${t.y.map((v, i) => (L + i * bw + bw / 2).toFixed(1) + ',' + Yv(v).toFixed(1)).join(' ')}" fill="none" stroke="${t.c}" stroke-width="${t.w || 1.5}"${t.d ? ` stroke-dasharray="${t.d}"` : ''} pointer-events="none"/>`; });
  for (let i = 0; i < n; i += 12) g += T((L + i * bw + bw / 2).toFixed(1), B + 14, mlab(i + 1), {a: 'middle'});
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img">${g}</svg>`; }
function renderLiabFollow(){
  const id = lfollow(), v = lsc(id), b = lsc(RBASE), n = FD.months, isB = id === RBASE, w = mwin();
  const exist = v.m_interest.map((x, i) => x - v.m_interest_new[i]);
  $('lmChart').innerHTML = lchartStack([{y: exist.map(x => x / 1e6), c: '#1F4E8C', l: 'existing debt $m'}, {y: v.m_interest_new.map(x => x / 1e6), c: '#D9772B', l: 'new issues $m'}], n, x => num(x, 0),
    [...(isB ? [] : [{y: b.m_interest.map(x => x / 1e6), c: PURPLE, d: '4 3'}]), {y: v.m_interest.map(x => x / 1e6), c: '#15202B', w: 2}]);
  $('lmTitle').firstChild.textContent = `Monthly interest expense — ${sname(id)}`;
  $('lmSub').textContent = `$m per month · debt on the books today vs debt issued to replace maturities and calls · ${hlab()} window shaded · ${isB ? 'follow a scenario to compare it with the base' : 'base total dashed'}`;
  $('lmLegend').innerHTML = `<span><span class="sw" style="background:#1F4E8C"></span>Debt outstanding today</span><span><span class="sw" style="background:#D9772B"></span>New issues</span><span>${sline('#15202B')}Total</span>` + (isB ? '' : `<span>${sline(PURPLE, 1.5, '4 3')}Base total</span>`);
  const cofH = (() => { let i = 0, bsum = 0; for (let m = w[0]; m < w[1]; m++) { i += v.m_interest[m]; bsum += FD.types.reduce((a, t) => a + v.m_balance[t][m], 0); } return 1200 * i / bsum; })();
  $('lmTiles').innerHTML = [[`Interest expense, ${hlab()}`, fm(hsum(v.interest))], ['vs base', isB ? '—' : fms(ldel(id))], ['Cost of funds over the horizon', fp(cofH, 2)],
    ['Called over the horizon', fm(hsum(v.called))], ['New issues over the horizon', fm(FD.types.reduce((a, t) => a + hsum(v.issued[t]), 0))], ['MC percentile', pctl(FD.mc_pct[id][MS][HZ - 1])]]
    .map(([a, x]) => `<div class="tile"><span class="l">${a}</span><span class="mono">${x}</span></div>`).join('');
  // balance by type
  $('lbalChart').innerHTML = lchartStack(FD.types.map(t => ({y: v.m_balance[t].map(x => x / 1e9), c: LT[t][1], l: LT[t][0] + ' $bn'})), n, x => num(x, 0), null, 560);
  $('lbalTitle').firstChild.textContent = `Debt by type — ${sname(id)}`;
  $('lbalLegend').innerHTML = FD.types.map(t => `<span>${lsw(t)}${LT[t][0]}</span>`).join('');
  // flows by year
  $('lflowBody').innerHTML = v.interest.map((x, y) => `<tr${yrs().includes(y) ? ' class="hzr"' : ''}><td>Y${y + 1}</td><td><b>${fv(x, 0)}</b></td>${FD.types.map(t => `<td>${fv(v.by_type[t][y], 0)}</td>`).join('')}<td>${fv(v.matured[y], 0)}</td><td>${v.called[y] ? fv(v.called[y], 0) : '—'}</td>${FD.types.map(t => `<td>${fv(v.issued[t][y], 0)}</td>`).join('')}<td>${isB ? '—' : fd(x - b.interest[y], 0)}</td></tr>`).join('');
  $('lflowTitle').firstChild.textContent = `Interest, maturities, calls and new issues by year — ${sname(id)}`; }

// ---------------------------------------------------------------- policy, reconciliation
function renderLiabPolicy(){
  const p = FD.policy, ni = p.new_issues, rep = FD.reported || {}, base3 = lsc(RBASE).m_interest.slice(0, 3).reduce((a, v) => a + v, 0);
  $('lpBody').innerHTML = FD.types.map(t => `<tr><td>${lsw(t)} ${LT[t][0]}</td><td>${fp(100 * p.target_mix[t], 1)}</td><td>${ni[t].term_months} months${t === 'callable' ? `, callable after ${ni[t].lockout_months}` : ''}</td><td>${t === 'discount_note' || t === 'floating' ? '1M SOFR' : 'Treasury curve at the term'} ${ni[t].spread_bp >= 0 ? '+' : MINUS} ${Math.abs(ni[t].spread_bp)} bp</td></tr>`).join('');
  $('lpText').innerHTML = `<p><b>Balance:</b> ${p.balance === 'constant' ? 'held at today\'s total: every maturity and call is replaced' : 'run off: nothing is replaced'}. <b>Mix:</b> new issues restore the ${p.target_mix_source === 'config' ? 'configured' : 'starting'} mix by type. `
    + `<b>Calls:</b> a callable is called once past its first call date when its coupon exceeds the cost of replacing its remaining term (Treasury curve at that term + ${p.call_rule.refi_spread_bp} bp) by more than ${p.call_rule.threshold_bp} bp. With today's curve this also calls high-coupon debt in its last months, when the short end is far below the coupon.</p>`
    + `<p><b>Reconciliation:</b> the model's first three months on the base path cost ${fm(base3)} (${fp(1200 * base3 / 3 / FD.face, 2)} a year on ${fm(FD.face)}). `
    + (rep.interest_expense_musd ? `The filing reports ${fm(rep.interest_expense_musd * 1e6)} for the ${esc(rep.period)} on ${fm(rep.average_debt_musd * 1e6)} of average debt (${fp(rep.cost_pct, 2)}); ${esc(rep.note || '')}.` : '')
    + ` Interest on debt is not yet netted against asset income: the Rate specific and EaR views are unchanged.</p>`; }

function renderLiab(){
  if (!FD) { $('view-liab').innerHTML = '<div class="card">No debt book in this run (set funding.file in config/model.yaml and rerun run_model.py).</div>'; return; }
  renderLiabBook(); renderLiabGrid(); renderLiabFollow(); renderLiabPolicy(); }
