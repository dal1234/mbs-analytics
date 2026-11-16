// Rate-specific view: deterministic scenarios from ratemodel/scenarios.py run through the same income engine.
// Uses the page's horizon (HZ) and measure (MS) from index.html, so every number here follows the same toggle as EaR.
const RSD = D.rate_specific || null;
const RSC = RSD ? Object.fromEntries(RSD.catalog.map(c => [c.id, c])) : {};
const RBASE = RSD ? RSD.base_id : null;
let FOCUS = null, RFAM = 'parallel', RRAMP = false, RTEN = 't10', OVS = 0, OVM = 1, GRID_OPEN = {}, CALL = false;
const UPC = ['#A84F15', '#D9772B', '#E39455', '#F0B98C'], DNC = ['#0B3A6E', '#1F4E8C', '#4F7FBF', '#8FB0DC'];
const SHC = ['#1F4E8C', '#0B3A6E', '#2E8B7A', '#D9772B', '#A84F15', '#7A5AA6'];
const RTN = {t10: ['10Y Treasury', '10Y'], sofr: ['1M SOFR', 'SOFR'], mtg: ['30Y mortgage rate', 'Mortgage rate']};
const PARTN = RSD ? Object.fromEntries(RSD.attribution_parts) : {};
const PARTC = {prepay_coupon: '#1F4E8C', prepay_reinv: '#8FB0DC', reinv_rate: '#2E8B7A', interaction: '#9AA4AF'};

// ---------------------------------------------------------------- data helpers
const famIds = f => RSD.catalog.filter(c => c.family === f).map(c => c.id);
const sname = id => (RSC[id] || {}).name || id;
const pctl = p => p < 0.005 ? '&lt;P1' : p > 0.995 ? '&gt;P99' : 'P' + Math.round(100 * p);
const sgnbp = v => (v > 0 ? '+' : v < 0 ? MINUS : '') + Math.abs(Math.round(v));
const ovOn = () => OVS !== 0 || OVM !== 1;
const okey = (s, m) => `${s}|${m.toFixed(2)}`;
const ovRow = id => ovOn() ? (RSD.overlays.grid[okey(OVS, OVM)] || {})[id] : null;
// annual income of a scenario (overlay run when overlays are on, unless raw)
const incA = (id, raw) => { const o = raw ? null : ovRow(id); return o ? o.income : RSD.annual[id].income; };
const yrs = () => MS === 'cumulative' ? [...Array(HZ).keys()] : [HZ - 1];
const hsum = arr => yrs().reduce((a, y) => a + (arr[y] || 0), 0);
const hinc = (id, raw) => hsum(incA(id, raw));
const hdel = (id, raw) => hinc(id, raw) - hinc(RBASE, raw);
const mwin = () => MS === 'cumulative' ? [0, 12 * HZ] : [12 * (HZ - 1), 12 * HZ];
const msum = (arr, w = mwin()) => arr.slice(w[0], w[1]).reduce((a, v) => a + v, 0);
const shocks = () => RSD.catalog.filter(c => !['base', 'reference'].includes(c.family)).map(c => c.id);
const worstId = raw => shocks().reduce((a, id) => hdel(id, raw) < hdel(a, raw) ? id : a, shocks()[0]);
const bestId = raw => shocks().reduce((a, id) => hdel(id, raw) > hdel(a, raw) ? id : a, shocks()[0]);
const followId = () => FOCUS || RBASE;                    // monthly stack, balances, by-year
const cmpId = () => FOCUS && FOCUS !== RBASE ? FOCUS : worstId(true);   // attribution, cohorts, waterfall
const parFam = () => RRAMP && famIds('parallel_ramp').length ? 'parallel_ramp' : 'parallel';
const cprA = (pre, ex) => ex > 0 ? 100 * (1 - Math.pow(1 - pre / ex, 12)) : 0;
const hcpr = id => { const m = RSD.monthly[id], w = mwin(); let p = 0, e = 0;
  for (let i = w[0]; i < w[1]; i++) { p += m.prepaid[i]; e += m.prepaid[i] + m.end_upb[i]; } return cprA(p, e); };
const sline = (c, w = 2, d = '') => `<svg width="20" height="8" aria-hidden="true"><line x1="0" y1="4" x2="20" y2="4" stroke="${c}" stroke-width="${w}"${d ? ` stroke-dasharray="${d}"` : ''}/></svg>`;
const T = (x, y, s, o = {}) => `<text x="${x}" y="${y}" text-anchor="${o.a || 'start'}"${o.click ? ` style="cursor:pointer;fill:${o.f || '#5B6572'};font-weight:${o.w || 400}" onclick="setFocus('${o.click}')"` : ` style="fill:${o.f || '#5B6572'};font-weight:${o.w || 400}${o.s ? ';font-size:' + o.s + 'px' : ''}"`}>${s}</text>`;
const dheat = (v, mx) => { const a = Math.abs(v) / (mx || 1); let bg = 'transparent', c = '#5B6572';
  if (Math.abs(v) >= 5e5) { if (v > 0) { bg = a < .1 ? '#EDF6F1' : a < .3 ? '#DDEEE4' : '#CFE6D8'; c = '#1F7A4D'; }
    else { bg = a < .1 ? '#FBECEC' : a < .3 ? '#F7D9D9' : a < .6 ? '#F1BFBF' : '#E9A3A3'; c = a < .3 ? '#B23434' : '#8E2727'; } }
  return `<span class="heat" style="background:${bg};color:${c}">${fd(v, 0)}</span>`; };

function setFocus(id){ FOCUS = (!id || FOCUS === id) ? null : id; renderRate(); if (typeof renderLiab === 'function') renderLiab(); if (typeof renderWL === 'function') renderWL(); }
function setOv(k, v){ if (k === 's') OVS = v; else OVM = v; renderRate(); }
function rpick(k, v){ if (k === 'fam') RFAM = v; else if (k === 'ramp') RRAMP = v; else RTEN = v; GRID_OPEN = {}; renderRate(); }
function gridToggle(k){ GRID_OPEN[k] = !gridSecOpen(k); renderRate(); }
const gridSecOpen = k => CALL || (GRID_OPEN[k] ?? (k === 'main' || (k === 'shape' && RFAM === 'shape')));

// ---------------------------------------------------------------- rate paths: parallel family over time
function rateTime(){
  const k = RTEN, H = RSD.months, hz = 12 * HZ, hz0 = MS === 'annual' ? hz - 12 : 0, hv = RSD.history[k] || [], nh = hv.length;
  const ids = famIds(parFam()), ups = ids.filter(id => RSC[id].short_bp > 0), dns = ids.filter(id => RSC[id].short_bp < 0);
  const ser = [...ups.map((id, i) => ({id, c: UPC[i % 4], w: 1.5})), {id: RBASE, c: '#15202B', w: 2.5, lab: 'Base'},
    ...(RSC.FLAT ? [{id: 'FLAT', c: PURPLE, w: 1.5, d: '5 3', lab: 'Flat'}] : []), ...dns.map((id, i) => ({id, c: DNC[Math.max(0, 3 - i)], w: 1.5}))]
    .map(s => ({...s, y: RSD.rates[s.id][k]}));
  const W = 720, Ht = 260, L = 50, R = 96, Tp = 30, B = 230, X = m => L + (m + nh) * (W - L - R) / (nh + H);
  const all = [...hv, ...ser.flatMap(s => s.y)].filter(v => v != null), [lo, hi, step] = niceTicks(Math.min(...all), Math.max(...all), 6);
  const Yv = v => Tp + (hi - v) / (hi - lo) * (B - Tp), pt = (m, v) => X(m).toFixed(1) + ',' + Yv(v).toFixed(1), dec = step < 0.5 ? 2 : step < 1 ? 1 : 0;
  let g = `<rect x="${L}" y="${Tp}" width="${(X(0) - L).toFixed(1)}" height="${B - Tp}" fill="#15202B" fill-opacity="0.04"/>`
    + `<rect x="${X(hz0).toFixed(1)}" y="${Tp}" width="${(X(hz) - X(hz0)).toFixed(1)}" height="${B - Tp}" fill="#B23434" fill-opacity="0.04"/>`;
  for (let v = hi; v >= lo - 1e-9; v -= step) g += `<line x1="${L}" x2="${W - R + 6}" y1="${Yv(v).toFixed(1)}" y2="${Yv(v).toFixed(1)}" stroke="#E3E7EC"/>` + T(L - 6, (Yv(v) + 3).toFixed(1), v.toFixed(dec) + '%', {a: 'end'});
  if (nh) g += T(L + 6, Tp + 14, 'ACTUAL', {w: 600});
  g += T(X(0) + 6, Tp + 14, 'SCENARIOS', {w: 600}) + `<line x1="${X(0)}" x2="${X(0)}" y1="${Tp}" y2="${B}" stroke="#5B6572"/>`
    + `<line x1="${X(hz).toFixed(1)}" x2="${X(hz).toFixed(1)}" y1="${Tp}" y2="${B}" stroke="#B23434" stroke-dasharray="3 3"/>`;
  for (let m = -nh; m <= H; m += 12) g += T(X(m).toFixed(1), B + 16, m === 0 ? 'Today' : (m > 0 ? '+' : MINUS) + Math.abs(m) + 'm', {a: 'middle', f: m === 0 ? '#15202B' : null, w: m === 0 ? 600 : 400});
  if (nh) g += `<polyline points="${[...hv.map((v, i) => v == null ? null : pt(i - nh, v)), pt(0, ser[0].y[0])].filter(Boolean).join(' ')}" fill="none" stroke="#15202B" stroke-width="2"/>`;
  const foc = FOCUS && ser.some(s => s.id === FOCUS);
  [...ser.filter(s => s.id !== FOCUS), ...ser.filter(s => s.id === FOCUS)].forEach(s => {
    g += `<polyline points="${s.y.map((v, m) => pt(m, v)).join(' ')}" fill="none" stroke="${s.c}" stroke-width="${s.id === FOCUS ? 3.5 : s.w}" stroke-opacity="${foc && s.id !== FOCUS ? 0.35 : 1}"${s.d ? ` stroke-dasharray="${s.d}"` : ''} stroke-linejoin="round" style="cursor:pointer" onclick="setFocus('${s.id}')"><title>${esc(sname(s.id))}</title></polyline>`; });
  const by = RSD.rates[RBASE][k][hz];
  spread(ser.map(s => ({t: s.lab || sgnbp(100 * (s.y[hz] - by)), c: s.c, id: s.id, y: Yv(s.y[hz]) + 3})), 12, Tp, B)
    .forEach(l => { g += T(W - R + 10, l.y.toFixed(1), l.t, {f: l.c, w: l.id === FOCUS || l.t === 'Base' ? 700 : 500, click: l.id}); });
  return `<svg viewBox="0 0 ${W} ${Ht}" width="100%" role="img" aria-label="${RTN[k][0]}: actuals, then the base and the parallel scenarios over ${H} months">${g}</svg>`; }

// ---------------------------------------------------------------- rate paths: curve shapes at +12m
function rateShape(){
  const ids = famIds('shape'), ti = RSD.curve_tenors, today = RSD.curve_today, b12 = RSD.curve_12m[RBASE], m12 = Math.min(12, RSD.months);
  const iS = 0, i10 = ti.indexOf('10Y');
  let all = today.concat(b12); ids.forEach(id => { all = all.concat(RSD.curve_12m[id]); });
  const lo = Math.floor(Math.min(...all) / 0.5) * 0.5, hi = Math.ceil(Math.max(...all) / 0.5) * 0.5, TX = ti.map((_, i) => 30 + i * 32);
  const CY = v => 12 + (hi - v) / (hi - lo) * 92, line = a => a.map((v, i) => TX[i] + ',' + CY(v).toFixed(1)).join(' ');
  const dd = id => [100 * (RSD.curve_12m[id][iS] - b12[iS]), 100 * (RSD.curve_12m[id][i10] - b12[i10])];
  const cards = ids.map((id, i) => { const [ds, dl] = dd(id), c = SHC[i % SHC.length], mt = RSD.rates[id].mtg[m12] - RSD.rates[RBASE].mtg[m12];
    return `<div class="mcard${id === FOCUS ? ' foc' : ''}" onclick="setFocus('${id}')" title="${esc(RSC[id].desc)} Click to follow."><div style="display:flex;align-items:center;gap:6px"><span class="sw" style="background:${c}"></span><span style="font-size:12px;font-weight:600">${esc(sname(id))}</span></div>
      <svg viewBox="0 0 200 128" width="100%" role="img" aria-label="${esc(sname(id))}: curve at +12 months">
      <line x1="30" y1="104" x2="192" y2="104" stroke="#C9D1DA"/>${T(26, 15, hi.toFixed(1) + '%', {a: 'end', s: 9})}${T(26, 107, lo.toFixed(1) + '%', {a: 'end', s: 9})}
      <polyline points="${line(today)}" fill="none" stroke="#9AA4AF" stroke-width="1.5" stroke-dasharray="3 2"/>
      <polyline points="${line(b12)}" fill="none" stroke="#15202B" stroke-width="1" stroke-opacity="0.5"/>
      <polyline points="${line(RSD.curve_12m[id])}" fill="none" stroke="${c}" stroke-width="2.5" stroke-linejoin="round"/>
      ${RSD.curve_12m[id].map((v, j) => `<circle cx="${TX[j]}" cy="${CY(v).toFixed(1)}" r="2.5" fill="${c}"/>`).join('')}
      ${ti.map((l, j) => T(TX[j], 121, j === 0 ? 'SOFR' : l, {a: 'middle', s: 9})).join('')}</svg>
      <div class="mono" style="font-size:11px">ΔSOFR ${sgnbp(ds)} · Δ10Y ${sgnbp(dl)} · Δmtg ${sgnbp(100 * mt)}</div></div>`; }).join('');
  const sh = ids.map(dd), pars = famIds('parallel').map(dd), Rg = Math.max(150, Math.ceil(Math.max(...sh.flat().map(Math.abs)) * 1.4 / 50) * 50);
  const QX = v => 200 + v / Rg * 130, QY = v => 150 - v / Rg * 130;
  let q = `<rect x="70" y="20" width="260" height="260" fill="#FFFFFF" stroke="#E3E7EC"/><polygon points="70,280 70,20 330,20" fill="#2E8B7A" fill-opacity="0.06"/><polygon points="70,280 330,280 330,20" fill="#7A5AA6" fill-opacity="0.06"/>
    <line x1="70" y1="150" x2="330" y2="150" stroke="#C9D1DA"/><line x1="200" y1="20" x2="200" y2="280" stroke="#C9D1DA"/><line x1="70" y1="280" x2="330" y2="20" stroke="#9AA4AF" stroke-dasharray="4 3"/>`
    + T(318, 40, 'Parallel', {a: 'end'}) + T(80, 36, 'STEEPENING', {f: '#2E8B7A', w: 600}) + T(320, 270, 'FLATTENING', {a: 'end', f: '#7A5AA6', w: 600})
    + T(320, 166, 'Selloff ↗', {a: 'end'}) + T(80, 140, '↙ Rally') + T(200, 296, 'Δ 1M SOFR (bp)', {a: 'middle'}) + T(64, 14, 'Δ 10Y (bp)')
    + T(70, 294, MINUS + Rg, {a: 'middle'}) + T(330, 294, '+' + Rg, {a: 'middle'}) + T(64, 284, MINUS + Rg, {a: 'end'}) + T(64, 24, '+' + Rg, {a: 'end'});
  pars.filter(([a, b]) => Math.abs(a) <= Rg && Math.abs(b) <= Rg).forEach(([a, b]) => { q += `<circle cx="${QX(a).toFixed(1)}" cy="${QY(b).toFixed(1)}" r="3" fill="#C9D1DA"/>`; });
  sh.forEach(([a, b], i) => { const x = QX(a), y = QY(b), r = x > 255;
    q += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${ids[i] === FOCUS ? 7 : 5.5}" fill="${SHC[i % SHC.length]}" stroke="#FFFFFF" stroke-width="1.5" style="cursor:pointer" onclick="setFocus('${ids[i]}')"/>`
      + T((r ? x - 9 : x + 9).toFixed(1), (y + 4).toFixed(1), esc(sname(ids[i]).split(' (')[0]), {a: r ? 'end' : 'start', f: '#15202B', w: 500}); });
  return `<div style="display:flex;flex-direction:column;gap:12px"><div class="mcards">${cards}</div>
    <div style="display:flex;flex-wrap:wrap;gap:16px;align-items:center"><svg viewBox="0 0 440 300" width="100%" role="img" aria-label="Change in 1M SOFR against the change in the 10-year at +12 months for each curve scenario" style="max-width:480px;flex:1 1 300px">${q}</svg>
    <div style="flex:1 1 200px;font-size:12px;display:flex;flex-direction:column;gap:6px"><div style="font-weight:600">How the shapes are built</div>
    <div style="color:#5B6572">Each shape moves the rate model's two factors: the short rate (1M SOFR) and the 10-year. The 1/2/5/30-year yields follow their calibrated loadings on the two factors, and the mortgage rate moves β = ${num(RSD.beta_mort, 3)} × the 10-year move, as on every Monte Carlo path.</div>
    <div style="color:#5B6572">Cards: today's curve (dashed), the base at +12m (thin) and the scenario at +12m. Δ figures are against the base at +12m. Gray dots in the quadrant are the parallel scenarios.</div></div></div></div>`; }

// ---------------------------------------------------------------- custom path: PREVIEW (drawing only, nothing is calculated)
// Drag across the forecast area to draw one rate, one point every 3 months; Run explains what a live version would do.
const CPM = (() => { const a = [], H = RSD ? RSD.months : 60; for (let m = 0; m <= H; m += 3) a.push(m); return a; })();
let CP = {}, CPY = null, CP_NAME = 'Custom 1', CP_LAST = null;
const cpBase = k => RSD.rates[RBASE][k];
const cpGet = k => CP[k] || (CP[k] = CPM.map(m => cpBase(k)[m]));
const cpDrawn = () => Object.keys(RTN).filter(k => CP[k] && CP[k].some((v, i) => Math.abs(v - cpBase(k)[CPM[i]]) > 1e-9));
const cpGeo = () => { const hv = RSD.history[RTEN] || [], nh = hv.length, H = CPM[CPM.length - 1], L = 50, R = 96, W = 720;
  return {hv, nh, H, L, W, R, X: m => L + (m + nh) * (W - L - R) / (nh + H), M: x => (x - L) * (nh + H) / (W - L - R) - nh}; };
function rateCustom(){
  const k = RTEN, g0 = cpGeo(), {hv, nh, H, L, W, R, X} = g0, Tp = 30, B = 230, b = cpBase(k).slice(0, H + 1);
  const all = hv.filter(v => v != null).concat(b), [lo, hi, step] = niceTicks(Math.max(0, Math.min(...all) - 3), Math.max(...all) + 3, 6);
  CPY = {lo, hi, Tp, B};
  const Yv = v => Tp + (hi - v) / (hi - lo) * (B - Tp), pt = (m, v) => X(m).toFixed(1) + ',' + Yv(v).toFixed(1), dec = step < 0.5 ? 2 : step < 1 ? 1 : 0;
  let g = `<rect x="${L}" y="${Tp}" width="${(X(0) - L).toFixed(1)}" height="${B - Tp}" fill="#15202B" fill-opacity="0.04"/><rect x="${X(0).toFixed(1)}" y="${Tp}" width="${(X(H) - X(0)).toFixed(1)}" height="${B - Tp}" fill="#1F4E8C" fill-opacity="0.04"/>`;
  for (let v = hi; v >= lo - 1e-9; v -= step) g += `<line x1="${L}" x2="${W - R + 6}" y1="${Yv(v).toFixed(1)}" y2="${Yv(v).toFixed(1)}" stroke="#E3E7EC"/>` + T(L - 6, (Yv(v) + 3).toFixed(1), v.toFixed(dec) + '%', {a: 'end'});
  if (nh) g += T(L + 6, Tp + 14, 'ACTUAL', {w: 600});
  g += T(X(0) + 6, Tp + 14, 'DRAW HERE · DRAG ACROSS THE MONTHS', {w: 600, f: '#1F4E8C'}) + `<line x1="${X(0)}" x2="${X(0)}" y1="${Tp}" y2="${B}" stroke="#5B6572"/>`;
  for (let m = -nh; m <= H; m += 12) g += T(X(m).toFixed(1), B + 16, m === 0 ? 'Today' : (m > 0 ? '+' : MINUS) + Math.abs(m) + 'm', {a: 'middle', f: m === 0 ? '#15202B' : null, w: m === 0 ? 600 : 400});
  if (nh) g += `<polyline points="${[...hv.map((v, i) => v == null ? null : pt(i - nh, v)), pt(0, b[0])].filter(Boolean).join(' ')}" fill="none" stroke="#15202B" stroke-width="2"/>`;
  g += `<polyline points="${b.map((v, m) => pt(m, v)).join(' ')}" fill="none" stroke="#9AA4AF" stroke-width="1.5" stroke-dasharray="4 3"/>` + T(W - R + 10, (Yv(b[H]) + 12).toFixed(1), 'Base');
  g += `<polyline id="cpLine" fill="none" stroke="#D9772B" stroke-width="2.5" stroke-linejoin="round"/><text id="cpEnd" x="${W - R + 10}" style="fill:#A84F15;font-weight:600">${esc(CP_NAME)}</text>`
    + CPM.map((m, i) => `<circle id="cpH${i}" cx="${X(m).toFixed(1)}" r="${i ? 4 : 3.5}" fill="${i ? '#FFFFFF' : '#15202B'}" stroke="#D9772B" stroke-width="2"/>`).join('');
  return `<svg id="cpSvg" viewBox="0 0 ${W} 260" width="100%" role="img" aria-label="Draw a custom path for the ${RTN[k][0]}" style="touch-action:none;cursor:crosshair">${g}</svg>
  <div class="cpbar"><label class="lbl" for="cpName">Name</label><input id="cpName" maxlength="40" value="${esc(CP_NAME)}">
   <button type="button" class="dl" onclick="cpReset()">Reset to base</button><button type="button" class="tb on" onclick="cpRun()">Run scenario</button></div>
  <div class="note mono" id="cpSum" style="margin-top:0"></div>
  <div class="mocknote" id="cpMsg" role="status" hidden></div>`; }
function cpDraw(){
  const svg = $('cpSvg'); if (!svg || !CPY) return;
  const p = cpGet(RTEN), {lo, hi, Tp, B} = CPY, {X} = cpGeo(), Yv = v => Tp + (hi - v) / (hi - lo) * (B - Tp), n = CPM.length - 1;
  $('cpLine').setAttribute('points', CPM.map((m, i) => X(m).toFixed(1) + ',' + Yv(p[i]).toFixed(1)).join(' '));
  CPM.forEach((m, i) => $('cpH' + i).setAttribute('cy', Yv(p[i]).toFixed(1)));
  $('cpEnd').setAttribute('y', (Yv(p[n]) - 8).toFixed(1));
  const at = m => CPM.indexOf(m), bp = i => sgnbp(100 * (p[i] - cpBase(RTEN)[CPM[i]])) + ' bp vs base';
  $('cpSum').textContent = `${RTN[RTEN][0]}: today ${p[0].toFixed(2)}%` + [12, 36, CPM[n]].filter((m, j, a) => at(m) > 0 && a.indexOf(m) === j)
    .map(m => ` · +${m}m ${p[at(m)].toFixed(2)}% (${bp(at(m))})`).join(''); }
function cpWire(){
  const svg = $('cpSvg'); if (!svg) return; let down = false;
  const set = e => { const q = svg.createSVGPoint(); q.x = e.clientX; q.y = e.clientY; const r = q.matrixTransform(svg.getScreenCTM().inverse());
    const {M} = cpGeo(), H = CPM[CPM.length - 1], i = Math.round(Math.max(0, Math.min(H, M(r.x))) / 3); if (!i) return;
    const v = Math.max(0, Math.min(CPY.hi, Math.round(20 * (CPY.hi - (r.y - CPY.Tp) / (CPY.B - CPY.Tp) * (CPY.hi - CPY.lo))) / 20)), p = cpGet(RTEN);
    if (CP_LAST && CP_LAST[0] !== i) { const [j, w] = CP_LAST, s = i > j ? 1 : -1; for (let k = j + s; k !== i; k += s) p[k] = w + (v - w) * (k - j) / (i - j); }
    p[i] = v; CP_LAST = [i, v]; $('cpMsg').hidden = true; cpDraw(); };
  svg.addEventListener('pointerdown', e => { down = true; CP_LAST = null; try { svg.setPointerCapture(e.pointerId); } catch (x) {} set(e); e.preventDefault(); });
  svg.addEventListener('pointermove', e => { if (down) set(e); });
  ['pointerup', 'pointercancel', 'lostpointercapture'].forEach(ev => svg.addEventListener(ev, () => { down = false; CP_LAST = null; }));
  $('cpName').addEventListener('input', e => { CP_NAME = e.target.value.trim() || 'Custom 1'; $('cpEnd').textContent = CP_NAME; }); }
function cpReset(){ delete CP[RTEN]; $('cpMsg').hidden = true; cpDraw(); }
function cpRun(){
  const drawn = cpDrawn().map(k => RTN[k][0]), m = $('cpMsg');
  m.innerHTML = `<b>Preview: this scenario is not calculated in this version.</b> In a live version, <b>Run scenario</b> would build “${esc(CP_NAME)}” from the rates you drew and run it through the same engine as every other scenario. `
    + (drawn.length ? `The rates you drew (${drawn.join(', ')}) would be used as given, between the points every 3 months; the rates you did not draw would follow the rate model: the mortgage rate β = ${num(RSD.beta_mort, 3)} × the 10-year change, the other Treasury tenors on their factor loadings. ` : 'You have not changed any rate from the base yet: drag across the shaded area to draw a path. ')
    + `The scenario would then appear everywhere on this view: a row in the income sensitivity grid with its Monte Carlo percentile, the monthly income, drivers, attribution, cash flows and cohorts.`;
  m.hidden = false; }

function renderRateCard(){
  $('rateFams').innerHTML = [['parallel', 'Parallel'], ['shape', 'Curve shape'], ['custom', 'Custom']].map(([k, l]) => `<button type="button" class="tb${RFAM === k ? ' on' : ''}" aria-pressed="${RFAM === k}" onclick="rpick('fam','${k}')">${l}${k === 'custom' ? '<span class="mocktag">preview</span>' : ''}</button>`).join('');
  const shape = RFAM === 'shape', custom = RFAM === 'custom', rm = (RSC[famIds('parallel_ramp')[0]] || {}).ramp;
  // timing is a sub-option of the parallel family only
  $('rateTiming').hidden = RFAM !== 'parallel' || !rm;
  $('rateTiming').innerHTML = RFAM !== 'parallel' || !rm ? '' : `<span class="tsep" aria-hidden="true"></span><span class="lbl">Timing</span>`
    + [[false, 'Immediate'], [true, `Ramped ${rm}m`]].map(([v, l]) => `<button type="button" class="tb sm${RRAMP === v ? ' on' : ''}" aria-pressed="${RRAMP === v}" onclick="rpick('ramp',${v})">${l}</button>`).join('');
  $('rateTenLbl').hidden = shape;
  $('rateTenTxt').textContent = custom ? 'Rate to draw' : 'Rate shown';
  $('rateTenors').innerHTML = shape ? '' : Object.entries(RTN).map(([k, [, l]]) => `<button type="button" class="tb${RTEN === k ? ' on' : ''}" aria-pressed="${RTEN === k}" onclick="rpick('ten','${k}')">${l}</button>`).join('');
  $('rateChart').innerHTML = shape ? rateShape() : custom ? rateCustom() : rateTime();
  if (custom) { cpWire(); cpDraw(); }
  $('rateTitle').firstChild.textContent = shape ? 'Curve shape scenarios' : custom ? `Custom rate path — ${RTN[RTEN][0]}` : `Rate paths — ${RTN[RTEN][0]}`;
  $('rateSub').textContent = shape ? `Immediate shocks to the two model factors · curves at +12 months · on top of the ${RSD.baseline_type} baseline`
    : custom ? `Draw your own path: drag across the shaded forecast area · one point every 3 months to +${CPM[CPM.length - 1]}m · pick the rate to draw below · base dashed`
    : `${(RSD.history.dates || []).length} months of actuals, then the ${RSD.baseline_type} baseline and ${famIds(parFam()).length} parallel shocks ${RRAMP ? `phased in over ${rm} months, then held` : 'from month 1'} · end labels: change vs base at the ${hlab()} horizon, bp`;
  $('rateNote').innerHTML = shape ? '' : custom ? '<b>Preview of a future capability.</b> Drawing and Run work here, but no custom scenario is calculated in this version; the grid and the sections below show the configured scenarios only.'
    : `Every Treasury tenor and 1M SOFR move the full shock; the mortgage rate moves <b>β = ${num(RSD.beta_mort, 3)}</b> × the shock, the calibrated pass-through of the 10-year to the mortgage rate (whitepaper 13.4). Rates are floored at ${num(100 * (D.rate_model.record.simulation.floor || 0), 1)}%, as on the Monte Carlo paths. Click a line to follow it.`; }

// ---------------------------------------------------------------- sensitivity grid + overlays
function gridSections(){
  const par = famIds(parFam()), alt = famIds(parFam() === 'parallel' ? 'parallel_ramp' : 'parallel'), rm = (RSC[famIds('parallel_ramp')[0]] || {}).ramp;
  const lab = f => f === 'parallel_ramp' ? `Parallel · ramped over ${rm} months` : 'Parallel · immediate';
  const s = [{key: 'main', label: lab(parFam()), rows: [...par.filter(id => RSC[id].short_bp > 0), RBASE, ...par.filter(id => RSC[id].short_bp < 0)]}];
  if (alt.length) s.push({key: 'alt', label: lab(parFam() === 'parallel' ? 'parallel_ramp' : 'parallel'), rows: alt});
  if (famIds('shape').length) s.push({key: 'shape', label: 'Curve shape', rows: famIds('shape')});
  if (famIds('reference').length) s.push({key: 'ref', label: 'Reference', rows: famIds('reference')});
  return s; }

function renderGrid(){
  const Yn = RSD.years, base = incA(RBASE), bh = hinc(RBASE), secs = gridSections(), mx = Math.max(1, ...RSD.catalog.map(c => Math.abs(hdel(c.id))));
  $('gridHead').innerHTML = `<tr><th>Scenario</th>${[...Array(Yn).keys()].map(y => `<th${yrs().includes(y) ? ' class="hz"' : ''}>Y${y + 1}</th>`).join('')}<th>${hshort()}</th><th>Δ vs base</th><th>Δ %</th><th>MC${tip('Share of the ' + K.mc_paths.toLocaleString('en-US') + ' Monte Carlo paths whose ' + 'income over the same horizon is at or below this scenario. Not shown with overlays on: the Monte Carlo has none.')}</th></tr>`;
  const row = id => { const a = incA(id), b = id === RBASE, d = hdel(id), p = RSD.mc_pct[id][MS][HZ - 1];
    return `<tr class="click${b ? ' base' : ''}${id === FOCUS ? ' on' : ''}" onclick="setFocus('${id}')" title="${esc(RSC[id].desc)} Click to follow this scenario.">`
      + `<td>${esc(sname(id))}</td>${a.map((v, y) => `<td${yrs().includes(y) ? ' class="hz"' : ''}>${fv(v, 0)}</td>`).join('')}<td><b>${fv(hsum(a), 0)}</b></td>`
      + `<td>${b ? '—' : dheat(d, mx)}</td><td>${b ? '—' : fpc(100 * d / bh)}</td><td>${ovOn() ? '—' : pctl(p)}</td></tr>`; };
  let h = '';
  secs.forEach(s => { const open = gridSecOpen(s.key), fin = !open && s.rows.includes(FOCUS);
    h += `<tr class="sec click" onclick="gridToggle('${s.key}')"><td colspan="${Yn + 5}"><span class="car">${open ? '▾' : '▸'}</span>${s.label}${open ? '' : `<span class="cnt">${s.rows.length} scenarios${fin ? ' · following ' + esc(sname(FOCUS)) : ''}</span>`}</td></tr>`;
    if (open) h += s.rows.map(row).join(''); });
  $('grid').innerHTML = h;
  $('gridSub').textContent = `$m · income by year, the ${hlab()} total and its change vs the base (the EaR baseline) · ${ovOn() ? `overlays: spread ${OVS ? sgnbp(OVS) + ' bp' : 'held'}, prepayment ${OVM.toFixed(2)}×` : 'no overlays'} · click a row to follow it`;
  $('gridAll').textContent = CALL ? 'Fold to the selected family' : 'Open all sections';
  const ob = (on, l, fn) => `<button type="button" class="tb${on ? ' on' : ''}" aria-pressed="${on}" onclick="${fn}">${l}</button>`, O = RSD.overlays;
  $('overlays').innerHTML = `<div class="ov"><div class="ovh"><b>Mortgage spread</b><span class="mono">${OVS ? sgnbp(OVS) + ' bp' : 'held'}</span></div><div class="grp">${O.spread_bp.map(s => ob(s === OVS, s === 0 ? 'Held' : sgnbp(s), `setOv('s',${s})`)).join('')}</div>`
    + `<div class="note" style="margin-top:0">The 30Y mortgage rate only, phased in over ${O.spread_ramp_months} months; Treasuries and SOFR unchanged. Moves prepayment speeds.</div></div>`
    + `<div class="ov"><div class="ovh"><b>Prepayment multiplier</b><span class="mono">${OVM.toFixed(2)}×</span></div><div class="grp">${O.prepay_mult.map(m => ob(m === OVM, m.toFixed(2) + '×', `setOv('m',${m})`)).join('')}</div>`
    + `<div class="note" style="margin-top:0">Scales every projected speed, in every scenario and month (on top of the configured ${num(D.model.prepay_multiplier, 2)}×).</div></div>`;
  $('ovBase').innerHTML = `Base ${hshort()} with overlays <b>${fm(bh)}</b>${ovOn() ? ` (${fms(bh - hinc(RBASE, true))} vs none)` : ''}`; }

// ---------------------------------------------------------------- following line + selector
function renderFollow(){
  const opts = gridSections().map(s => `<optgroup label="${s.label}">` + s.rows.map(id => `<option value="${id}"${id === FOCUS ? ' selected' : ''}>${esc(sname(id))}</option>`).join('') + '</optgroup>').join('');
  $('followSel').innerHTML = `<option value="">None (base)</option>` + opts; $('followSel').value = FOCUS || '';
  if (!FOCUS) { $('focusLine').innerHTML = `<b>Following:</b> none. Click a scenario in the grid, the rate chart or the stack, or pick one in the bar above, to follow it through the cash flows below. Attribution and cohorts show the worst scenario (${esc(sname(worstId(true)))}) until you do.`; return; }
  const d = hdel(FOCUS, true), at = RSD.attribution[FOCUS];
  let why = '';
  if (at) { const ps = Object.keys(at).filter(k => k !== 'interaction').map(k => [k, hsum(at[k])]), lo = ps.reduce((a, b) => b[1] < a[1] ? b : a), hi = ps.reduce((a, b) => b[1] > a[1] ? b : a);
    why = (lo[1] < 0 ? ` Biggest drag: ${PARTN[lo[0]].toLowerCase()} (${fms(lo[1])})` : '') + (hi[1] > 0 ? `${lo[1] < 0 ? ', offset by' : ' Biggest lift:'} ${PARTN[hi[0]].toLowerCase()} (${fms(hi[1])})` : '') + '.'; }
  $('focusLine').innerHTML = `<b>Following ${esc(sname(FOCUS))}:</b> ${hlab()} income ${fm(hinc(FOCUS, true))}` + (FOCUS === RBASE ? ' (the base).' : ` (${fms(d)} vs base, ${fpc(100 * d / hinc(RBASE, true))}), ${pctl(RSD.mc_pct[FOCUS][MS][HZ - 1])} of the Monte Carlo. CPR ${fp(hcpr(FOCUS))} vs ${fp(hcpr(RBASE))} in the base.${why}`)
    + ` <button type="button" class="dl" onclick="setFocus(null)">Clear</button>`; }

// ---------------------------------------------------------------- monthly income stack (followed scenario)
function renderMonthly(){
  const id = followId(), M = RSD.monthly[id], BM = RSD.monthly[RBASE], n = RSD.months, isB = id === RBASE, w = mwin();
  const tot = i => M.interest[i] + M.reinv[i], btot = i => BM.interest[i] + BM.reinv[i];
  const W = 1100, H = 260, L = 56, Rr = 14, Tp = 12, Bt = 232, bw = (W - L - Rr) / n;
  const vmax = Math.max(...[...Array(n).keys()].map(i => Math.max(tot(i), btot(i)))), [lo, hi, step] = niceTicks(0, vmax / 1e6, 5);
  const Yv = v => Bt - (v / 1e6 - lo) / (hi - lo) * (Bt - Tp), X = i => L + i * bw;
  let g = `<rect x="${X(w[0]).toFixed(1)}" y="${Tp}" width="${(X(w[1]) - X(w[0])).toFixed(1)}" height="${Bt - Tp}" fill="#B23434" fill-opacity="0.04"/>`;
  for (let v = lo; v <= hi + 1e-9; v += step) g += `<line x1="${L}" x2="${W - Rr}" y1="${Yv(v * 1e6).toFixed(1)}" y2="${Yv(v * 1e6).toFixed(1)}" stroke="#E3E7EC"/>` + T(L - 6, (Yv(v * 1e6) + 3).toFixed(1), num(v, step < 1 ? 1 : 0), {a: 'end'});
  for (let i = 0; i < n; i++) { const c = M.interest[i], r = M.reinv[i], x = (X(i) + 0.5).toFixed(1), bwd = Math.max(1, bw - 1).toFixed(1), op = i >= w[0] && i < w[1] ? 1 : 0.45;
    g += `<rect x="${x}" y="${Yv(c).toFixed(1)}" width="${bwd}" height="${(Bt - Yv(c)).toFixed(1)}" fill="#1F4E8C" fill-opacity="${op}"/><rect x="${x}" y="${Yv(c + r).toFixed(1)}" width="${bwd}" height="${(Yv(c) - Yv(c + r)).toFixed(1)}" fill="#8FB0DC" fill-opacity="${op}"/>`
      + `<rect x="${X(i).toFixed(1)}" y="${Tp}" width="${bw.toFixed(1)}" height="${Bt - Tp}" fill="transparent"><title>${mlab(i + 1)}: coupon $${fv(c)}m · reinvestment $${fv(r)}m · total $${fv(c + r)}m${isB ? '' : ` · base $${fv(btot(i))}m (Δ ${fd(c + r - btot(i), 2)}m)`} · CPR ${fp(M.cpr[i])} · reinvestment rate ${fp(M.reinv_rate[i], 2)}</title></rect>`; }
  const ln = (f, c, wd, d) => `<polyline points="${[...Array(n).keys()].map(i => (X(i) + bw / 2).toFixed(1) + ',' + Yv(f(i)).toFixed(1)).join(' ')}" fill="none" stroke="${c}" stroke-width="${wd}"${d ? ` stroke-dasharray="${d}"` : ''} pointer-events="none"/>`;
  if (!isB) g += ln(btot, PURPLE, 1.5, '4 3');
  g += ln(tot, '#15202B', 2);
  for (let i = 0; i < n; i += 12) g += T((X(i) + bw / 2).toFixed(1), Bt + 14, mlab(i + 1), {a: 'middle'});
  g += T(L, H - 2, '$m per month');
  $('tsChart').innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Monthly coupon and reinvestment income, ${esc(sname(id))}">${g}</svg>`;
  $('tsTitle').firstChild.textContent = `Monthly income — ${sname(id)}`;
  $('tsSub').textContent = `$m per month · coupon on the held CUSIPs and income on reinvested prepaid principal at ${reinvLabel()} · ${hlab()} window shaded · ${isB ? 'follow a scenario to compare it with the base' : 'base total dashed'} · hover a month for the numbers · no overlays`;
  $('tsLegend').innerHTML = `<span><span class="sw" style="background:#1F4E8C"></span>Coupon</span><span><span class="sw" style="background:#8FB0DC"></span>Reinvestment</span><span>${sline('#15202B')}Total income</span>` + (isB ? '' : `<span>${sline(PURPLE, 1.5, '4 3')}Base total</span>`);
  const sI = msum(M.interest), sR = msum(M.reinv), bI = msum(BM.interest) + msum(BM.reinv);
  $('tsTiles').innerHTML = [[`Income, ${hlab()}`, fm(sI + sR)], ['vs base', isB ? '—' : fms(sI + sR - bI)], ['Reinvestment share', fp(100 * sR / (sI + sR))],
    ['CPR over the horizon', fp(hcpr(id))], ['MBS balance at the end', fm(M.end_upb[w[1] - 1])], ['Reinvested balance', fm(M.reinv_bal[w[1] - 1])]]
    .map(([a, b]) => `<div class="tile"><span class="l">${a}</span><span class="mono">${b}</span></div>`).join(''); }

// ---------------------------------------------------------------- income stack by scenario
function renderStack(){
  const ids = [...gridSections()[0].rows, ...famIds('shape')], w = mwin(), comp = id => [msum(RSD.monthly[id].interest), msum(RSD.monthly[id].reinv)];
  const mx = Math.max(...ids.map(id => comp(id)[0] + comp(id)[1]));
  $('stack').innerHTML = ids.map(id => { const [c, r] = comp(id), b = id === RBASE;
    return `<div class="srow click${id === FOCUS ? ' on' : ''}" onclick="setFocus('${id}')" title="${esc(sname(id))}: coupon $${fv(c, 0)}m + reinvestment $${fv(r, 0)}m · click to follow"><div style="${b ? 'font-weight:600;' : ''}overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(sname(id))}</div>`
      + `<div style="display:flex;height:16px"><div style="width:${(100 * c / mx).toFixed(2)}%;background:#1F4E8C"></div><div style="width:${(100 * r / mx).toFixed(2)}%;background:#8FB0DC"></div></div>`
      + `<div class="mono" style="text-align:right${b ? ';font-weight:600' : ''}">${fv(c + r, 0)}</div><div class="mono" style="text-align:right;color:${b ? '#5B6572' : (c + r - msum(RSD.monthly[RBASE].interest) - msum(RSD.monthly[RBASE].reinv)) < 0 ? '#B23434' : '#1F7A4D'}">${b ? '' : fd(c + r - msum(RSD.monthly[RBASE].interest) - msum(RSD.monthly[RBASE].reinv), 0)}</div></div>`; }).join('');
  $('stackSub').textContent = `$m · ${hlab()} income by component · the parallel family picked in the rate card, then the curve shapes · last column: change vs base · no overlays`; }

// ---------------------------------------------------------------- drivers: CPR and the reinvestment rate by month
function mini(id, key, title, fmt, pick){ const W = 340, H = 200, L = 36, Rr = 262, Tp = 20, Bt = 170, n = RSD.months;
  const rest = RSD.catalog.map(c => c.id).filter(s => !pick.some(p => p[0] === s)).map(s => RSD.monthly[s][key]);
  const sel = pick.map(([s, l, c]) => ({y: RSD.monthly[s][key], l, c, w: s === FOCUS ? 3 : l === 'Base' ? 2 : 1.5}));
  const all = [...rest.flat(), ...sel.flatMap(s => s.y)], [lo, hi, step] = niceTicks(Math.min(...all), Math.max(...all), 4);
  const X = i => L + (Rr - L) * i / (n - 1), Yv = v => Tp + (Bt - Tp) * (hi - v) / (hi - lo), pl = (y, c, w) => `<polyline points="${y.map((v, i) => X(i).toFixed(1) + ',' + Yv(v).toFixed(1)).join(' ')}" fill="none" stroke="${c}" stroke-width="${w}" stroke-linejoin="round"/>`;
  let g = T(L, 12, title, {f: '#15202B', w: 600});
  for (let v = lo; v <= hi + 1e-9; v += step) g += `<line x1="${L}" y1="${Yv(v).toFixed(1)}" x2="${Rr}" y2="${Yv(v).toFixed(1)}" stroke="#E3E7EC"/>` + T(L - 4, (Yv(v) + 3).toFixed(1), fmt(v), {a: 'end'});
  g += T(L, 190, '1m') + T(Rr, 190, n + 'm', {a: 'end'}) + rest.map(y => pl(y, '#E3E7EC', 1)).join('') + sel.slice().reverse().map(s => pl(s.y, s.c, s.w)).join('');
  spread(sel.map(s => ({t: s.l, c: s.c, y: Yv(s.y[n - 1]) + 3})), 11, Tp, Bt + 6).forEach(l => { g += T(Rr + 4, l.y.toFixed(1), esc(l.t), {f: l.c}); });
  $(id).innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="${title} by month">${g}</svg>`; }
function renderDrivers(){
  const pid = bp => famIds(parFam()).find(id => RSC[id].short_bp === bp), wid = worstId(true);
  const pick = [[RBASE, 'Base', '#15202B'], [pid(-200), sname(pid(-200) || ''), '#1F4E8C'], [pid(200), sname(pid(200) || ''), '#D9772B'], [wid, sname(wid), '#B23434']]
    .filter(([s], i, a) => s && a.findIndex(x => x[0] === s) === i);
  if (FOCUS && !pick.some(p => p[0] === FOCUS)) pick.push([FOCUS, sname(FOCUS), PURPLE]);
  mini('chartCpr', 'cpr', 'CPR, %', v => num(v, 0), pick);
  mini('chartReinv', 'reinv_rate', `Reinvestment rate (${reinvLabel()}), %`, v => num(v, 1), pick);
  $('driversSub').textContent = `By month over ${RSD.months} months · highlighted: ${pick.map(p => p[1]).join(', ')}; every other scenario in gray`; }

// ---------------------------------------------------------------- attribution
function renderAttribution(){
  const id = cmpId(), at = RSD.attribution[id], keys = Object.keys(PARTN);
  if (!at) { $('atBars').innerHTML = '<div class="note">No attribution for the base.</div>'; return; }
  const v = Object.fromEntries(keys.map(k => [k, hsum(at[k])])), tot = keys.reduce((a, k) => a + v[k], 0), mx = Math.max(1, ...keys.map(k => Math.abs(v[k])));
  $('atTitle').firstChild.textContent = `Why income moves — ${sname(id)}, ${hlab()}`;
  $('atBars').innerHTML = `<table class="g"><tbody><tr class="base"><td>Base income</td><td></td><td>${fv(hinc(RBASE, true), 0)}</td></tr>`
    + keys.map(k => `<tr><td><span class="sw" style="background:${PARTC[k]};margin-right:6px"></span>${PARTN[k]}</td><td style="text-align:left;width:45%"><span class="bar" style="width:${Math.max(1, 200 * Math.abs(v[k]) / mx).toFixed(0)}px;background:${v[k] < 0 ? '#B23434' : '#1F7A4D'}"></span></td><td>${fd(v[k])}</td></tr>`).join('')
    + `<tr class="base"><td>${esc(sname(id))} income</td><td></td><td>${fv(hinc(id, true), 0)} <span style="color:#5B6572;font-weight:400">(${fd(tot)})</span></td></tr></tbody></table>`;
  const ids = shocks();
  $('atHead').innerHTML = `<tr><th>Scenario</th>${keys.map(k => `<th>${PARTN[k]}</th>`).join('')}<th>Total</th></tr>`;
  $('atTable').innerHTML = ids.map(s => { const a = RSD.attribution[s]; return `<tr class="click${s === id ? ' on' : ''}" onclick="setFocus('${s}')"><td>${esc(sname(s))}</td>${keys.map(k => `<td>${fd(hsum(a[k]))}</td>`).join('')}<td><b>${fd(keys.reduce((t, k) => t + hsum(a[k]), 0))}</b></td></tr>`; }).join(''); }

// ---------------------------------------------------------------- balances and rates, by year, waterfall
function lchart(series, yfmt){ const W = 560, H = 210, L = 56, R = 16, Tp = 12, Bm = 26, n = series[0].y.length;
  const all = series.flatMap(s => s.y), [lo, hi, step] = niceTicks(Math.min(...all), Math.max(...all), 4);
  const X = i => L + (W - L - R) * i / (n - 1), Yv = v => Tp + (H - Tp - Bm) * (hi - v) / (hi - lo);
  let g = '';
  for (let v = lo; v <= hi + 1e-9; v += step) g += `<line x1="${L}" x2="${W - R}" y1="${Yv(v).toFixed(1)}" y2="${Yv(v).toFixed(1)}" stroke="#E3E7EC"/>` + T(L - 6, (Yv(v) + 3).toFixed(1), yfmt(v), {a: 'end'});
  for (let i = 0; i < n; i += 12) g += T(X(i).toFixed(1), H - 8, i ? '+' + i + 'm' : 'Today', {a: 'middle'});
  series.forEach(s => { g += `<polyline fill="none" stroke="${s.c}" stroke-width="${s.w || 1.5}"${s.d ? ` stroke-dasharray="${s.d}"` : ''} points="${s.y.map((v, i) => X(i).toFixed(1) + ',' + Yv(v).toFixed(1)).join(' ')}"/>`; });
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img">${g}</svg>`; }
function renderCash(){
  const id = followId(), isB = id === RBASE, M = RSD.monthly[id], BMm = RSD.monthly[RBASE], up0 = K.upb;
  const bal = m => [up0, ...m.end_upb].map(v => v / 1e9), rbal = m => [0, ...m.reinv_bal].map(v => v / 1e9), rr = s => RSD.rates[s];
  const ref = (y, c) => isB ? [] : [{y, c, w: 1.2, d: '4 3'}];
  $('chartUpb').innerHTML = lchart([...ref(bal(BMm), '#9AA4AF'), ...ref(rbal(BMm), '#C9D1DA'), {y: bal(M), c: '#15202B', w: 2}, {y: rbal(M), c: '#8FB0DC', w: 2}], v => num(v, 1));
  $('chartRates').innerHTML = lchart([...ref(rr(RBASE).mtg, '#E39455'), ...ref(rr(RBASE).sofr, '#8FB0DC'), {y: rr(id).mtg, c: '#A84F15', w: 1.8}, {y: rr(id).sofr, c: '#1F4E8C', w: 1.8}], v => num(v, 1) + '%');
  $('cashTitle').firstChild.textContent = `Balances and rates — ${sname(id)}`;
  $('cashSub').textContent = `${sname(id)} month by month` + (isB ? ' · follow a scenario to compare it with the base' : ' · base dashed');
  $('cashLegend').innerHTML = `<span>${sline('#15202B')}MBS balance $bn</span><span>${sline('#8FB0DC')}Reinvested balance $bn</span><span>${sline('#A84F15')}30Y mortgage rate</span><span>${sline('#1F4E8C')}1M SOFR</span>` + (isB ? '' : `<span>${sline('#9AA4AF', 1.2, '4 3')}Base</span>`);
  const A = RSD.annual[id], BA = RSD.annual[RBASE];
  let cum = 0;
  $('byyearTitle').firstChild.textContent = `${isB ? 'Base' : sname(id)} by year`;
  $('byyear').innerHTML = A.income.map((v, y) => { cum += v; return `<tr${yrs().includes(y) ? ' class="hzr"' : ''}><td>Y${y + 1}</td><td>${fv(A.begin_upb[y], 0)}</td><td>${fv(A.end_upb[y], 0)}</td><td>${fv(A.sched[y], 0)}</td><td>${fv(A.prepaid[y], 0)}</td><td>${fp(A.cpr[y])}</td><td>${fv(A.interest[y])}</td><td>${fv(A.reinv[y])}</td><td><b>${fv(v)}</b></td><td>${fv(cum, 0)}</td><td>${isB ? '—' : fd(v - BA.income[y])}</td></tr>`; }).join('');
  const c = cmpId(), CM = RSD.monthly[c], w = mwin(), endv = (m, k) => m[k][w[1] - 1];
  const lines = [['Coupon income', m => msum(m.interest)], ['Reinvestment income', m => msum(m.reinv)], ['Total income', m => msum(m.interest) + msum(m.reinv), 1],
    ['Scheduled principal (leaves the book)', m => msum(m.sched)], ['Prepaid principal (reinvested)', m => msum(m.prepaid)],
    ['MBS balance at the end', m => endv(m, 'end_upb'), 1], ['Reinvested balance at the end', m => endv(m, 'reinv_bal')]];
  $('wfTitle').firstChild.textContent = `Cash flows — base vs ${sname(c)}, ${hlab()}`; $('wfS').textContent = sname(c);
  $('waterfall').innerHTML = lines.map(([l, f, b]) => { const x = f(BMm), y = f(CM); return `<tr${b ? ' class="base"' : ''}><td>${l}</td><td>${fv(x, 0)}</td><td>${fv(y, 0)}</td><td>${fd(y - x, 0)}</td></tr>`; }).join(''); }

// ---------------------------------------------------------------- cohorts
let RCALL = false;
function renderCohorts(){
  const id = cmpId(), C = RSD.cohorts, ys = yrs(), n = C.labels.length;
  const s = (arr, i) => ys.reduce((a, y) => a + arr[i][y], 0), cpr = (sid, i) => { const p = s(C.prepaid[sid], i), e = s(C.prepay_base[sid], i); return e > 0 ? 100 * (1 - Math.pow(1 - p / e, 12)) : 0; };
  const rows = [...Array(n).keys()].map(i => ({i, base: s(C.income[RBASE], i), scen: s(C.income[id], i)})).map(r => ({...r, d: r.scen - r.base})).sort((a, b) => a.d - b.d);
  const tot = rows.reduce((a, r) => a + r.d, 0), show = RCALL ? rows : rows.slice(0, 25), U = C.upb.reduce((a, v) => a + v, 0);
  $('cohortTitle').firstChild.textContent = `Cohorts — ${sname(id)} vs base, ${hlab()}`;
  $('cohortSub').textContent = `$m · grouped by ${C.by.join(', ')} · largest losses first · ${n} cohorts · the differences add up to the scenario's change vs base (${fms(tot)})`;
  $('cohorts').innerHTML = show.map(r => { const i = r.i; return `<tr><td>${esc(C.labels[i])}</td><td>${C.positions[i]}</td><td>${fv(C.upb[i], 0)}</td><td>${num(C.coupon[i], 2)}</td><td>${num(C.wac[i], 2)}</td><td>${fp(cpr(RBASE, i))}</td><td>${fp(cpr(id, i))}</td><td>${fv(r.base)}</td><td>${fv(r.scen)}</td><td>${dheat(r.d, Math.abs(rows[0].d))}</td><td>${tot ? fp(100 * r.d / tot) : '—'}</td><td>${fbp(1e4 * r.d / C.upb[i] / ys.length)}</td></tr>`; }).join('')
    + `<tr class="base"><td>Total</td><td>${C.positions.reduce((a, v) => a + v, 0)}</td><td>${fv(U, 0)}</td><td></td><td></td><td></td><td></td><td>${fv(rows.reduce((a, r) => a + r.base, 0))}</td><td>${fv(rows.reduce((a, r) => a + r.scen, 0))}</td><td>${dheat(tot, Math.abs(tot))}</td><td>100.0%</td><td>${fbp(1e4 * tot / U / ys.length)}</td></tr>`;
  $('cohortAll').textContent = RCALL ? 'Show top 25' : `Show all ${n}`; $('cohortAll').hidden = n <= 25; }

// ---------------------------------------------------------------- page
function renderRate(){
  if (!RSD) { $('view-rate').innerHTML = '<div class="card">No rate-specific scenarios in this run\'s data (rerun run_model.py).</div>'; return; }
  renderFollow(); renderRateCard(); renderGrid(); renderMonthly(); renderStack(); renderDrivers(); renderAttribution(); renderCash(); renderCohorts(); }

// CSV of the grid as shown (overlays included)
function gridCsv(){
  const Yn = RSD.years, head = ['scenario_id', 'scenario', 'family', ...[...Array(Yn).keys()].map(y => 'income_y' + (y + 1)), 'horizon_income', 'change_vs_base', 'change_pct', 'mc_percentile', 'mortgage_spread_bp', 'prepay_multiplier'];
  const rows = RSD.catalog.map(c => { const a = incA(c.id), d = hdel(c.id); return [c.id, '"' + c.name + '"', c.family, ...a.map(v => v.toFixed(0)), hsum(a).toFixed(0), d.toFixed(0), (100 * d / hinc(RBASE)).toFixed(3), ovOn() ? '' : RSD.mc_pct[c.id][MS][HZ - 1].toFixed(4), OVS, OVM].join(','); });
  const el = document.createElement('a'); el.href = URL.createObjectURL(new Blob([[head.join(','), ...rows].join('\n')], {type: 'text/csv'}));
  el.download = `rate_scenarios_${MS}_${HZ}y_${D.meta.as_of}.csv`; document.body.appendChild(el); el.click(); el.remove(); }
