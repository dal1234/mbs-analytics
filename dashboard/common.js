// Shared by every page: formatters, the header with the as-of popover, the banner and the (i) tooltips.
const D = window.DATA;
const $ = id => document.getElementById(id);
const esc = s => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const num = (v, d) => Number(v).toLocaleString('en-US', {minimumFractionDigits: d, maximumFractionDigits: d});
const bad = v => v == null || isNaN(v);
const MINUS = '−';
// headline money: $bn at or above $1bn, otherwise $m
const fm = v => bad(v) ? '—' : (v < 0 ? MINUS : '') + '$' + (Math.abs(v) >= 1e9 ? num(Math.abs(v) / 1e9, 2) + 'bn' : num(Math.abs(v) / 1e6, 1) + 'm');
const fms = v => bad(v) ? '—' : (v > 0 ? '+' : '') + fm(v);
// table money in $m
const fv = (v, d = 1) => bad(v) ? '—' : (v < 0 ? MINUS : '') + num(Math.abs(v) / 1e6, d);
const fd = (v, d = 1) => bad(v) ? '—' : (Math.abs(v) < 0.5 * Math.pow(10, 6 - d) ? '' : v > 0 ? '+' : MINUS) + num(Math.abs(v) / 1e6, d);
const fp = (v, d = 1) => bad(v) ? '—' : num(v, d) + '%';
const fpc = (v, d = 1) => bad(v) ? '—' : (v > 0 ? '+' : v < 0 ? MINUS : '') + num(Math.abs(v), d) + '%';
const fbp = v => bad(v) ? '—' : (Math.abs(v) < 0.5 ? '' : v > 0 ? '+' : MINUS) + num(Math.abs(v), 0);
const tip = t => `<i class="i" tabindex="0" data-tip="${esc(t)}">i</i>`;
const heat = v => { const a = Math.abs(v) / 1e6; if (a < 0.05) return ['transparent', '#5B6572'];
  if (v > 0) return [a < 5 ? '#EDF6F1' : a < 15 ? '#DDEEE4' : '#CFE6D8', '#1F7A4D'];
  return [a < 5 ? '#FBECEC' : a < 15 ? '#F7D9D9' : a < 40 ? '#F1BFBF' : '#E9A3A3', a < 15 ? '#B23434' : '#8E2727']; };
const cell = (v, d = 1) => { const h = heat(v); return `<span class="heat" style="background:${h[0]};color:${h[1]}">${fd(v, d)}</span>`; };
const SERIES_NAME = {SOFR_1M: '1M SOFR', TSY_1M: '1M Treasury', TSY_3M: '3M Treasury', TSY_6M: '6M Treasury', TSY_1Y: '1Y Treasury',
  TSY_2Y: '2Y Treasury', TSY_3Y: '3Y Treasury', TSY_5Y: '5Y Treasury', TSY_7Y: '7Y Treasury', TSY_10Y: '10Y Treasury',
  TSY_30Y: '30Y Treasury', MTG_30Y: '30Y mortgage rate'};
const reinvLabel = () => { const m = D.model, sp = Number(m.reinvest_spread_bp || 0);
  return SERIES_NAME[m.reinvest_series] + (sp ? ` ${sp > 0 ? '+' : MINUS} ${Math.abs(sp)} bp` : ''); };

// Which approach this dashboard shows (official = historical volatility) and a link to the other one, if configured
function setupChip(){ const s = (D.meta || {}).setup; if (!s) return '';
  const c = s.compare, page = location.pathname.split('/').pop() || 'index.html';
  const link = c && c.built ? ` <a class="swap" href="${esc(c.href.replace(/index\.html$/, page))}" title="Same page in the ${esc(c.label)} dashboard">${esc(c.label)} →</a>` : '';
  return ` <span class="setup ${s.validated ? 'off' : 'cmp'}" title="${esc(s.baseline_description + ' · ' + s.vol_description)}">${esc(s.label)}</span>${link}`; }

function header(page){
  const k = D.kpi, m = D.meta, c = D.checks || [];
  const n = s => c.filter(x => x.status === s).length, worst = n('FAIL') ? 'r' : n('WATCH') ? 'a' : 'g';
  const pages = [['index.html', 'Income risk'], ['portfolio.html', 'Portfolio'], ['models.html', 'Models &amp; assumptions'], ['whitepaper.html', 'Rate model']];
  const sub = `${esc(m.portfolio_name)} · ${k.positions.toLocaleString('en-US')} CUSIPs · rate scenarios and ${k.mc_paths.toLocaleString('en-US')} Monte Carlo paths · vs baseline, 1–${m.years} years`;
  document.title = `${m.title} — ${pages.find(p => p[0] === page)[1].replace('&amp;', '&')}`;
  $('top').innerHTML = (m.banner ? `<div class="banner" role="note"><span class="ic" aria-hidden="true">!</span><div>${esc(m.banner)}</div></div>` : '')
    + `<div class="hdr"><div class="hl"><h1>${esc(m.title)}${setupChip()}</h1><div class="sub" title="${sub}">${sub}</div></div>`
    + `<nav class="nav">${pages.map(([h, l]) => `<a href="${h}"${h === page ? ' class="on" aria-current="page"' : ''}>${l}</a>`).join('')}</nav>`
    + `<div class="hr"><div class="asof"><button type="button" id="asofBtn" aria-haspopup="true" aria-expanded="false" aria-controls="asofPop">As of ${m.as_of} ▾</button>`
    + `<div class="pop" id="asofPop" role="dialog" aria-label="Data dates, run and checks" hidden>`
    + `<h3>Data dates</h3><table><tbody>`
    + `<tr><td>Portfolio</td><td><b>${m.as_of}</b> · ${esc(m.portfolio_file)}</td></tr>`
    + `<tr><td>Rates</td><td><b>${m.rates_as_of}</b> · 30y mortgage ${fp(k.spot_mtg, 2)}, 1M SOFR ${fp(k.spot_sofr, 2)}</td></tr>`
    + `<tr><td>Run</td><td>${m.run_at} · model v${m.version}</td></tr></tbody></table>`
    + `<h3>This run</h3><table><tbody>`
    + `<tr><td>Horizons</td><td>1 to ${m.years} years (${m.horizon} months projected)</td></tr>`
    + `<tr><td>Monte Carlo</td><td>${k.mc_paths === k.tail_paths ? k.mc_paths.toLocaleString('en-US') + ' paths (every measure)' : k.mc_paths.toLocaleString('en-US') + ' paths (95%) and ' + k.tail_paths.toLocaleString('en-US') + ' (99%, expected shortfall)'}, seed ${k.seed ?? '—'}</td></tr>`
    + `<tr><td>Rate model</td><td>${esc(D.rate_model.record.calibration.official)} calibration ${esc(D.rate_model.record.calibration.window.join(' to '))} · <a href="whitepaper.html">whitepaper</a></td></tr>`
    + `<tr><td>Baseline</td><td>${esc(D.rate_model.record.baseline.description)}</td></tr>`
    + `<tr><td>Volatility</td><td>${esc((m.setup || {}).vol_description || 'Historical (official)')}</td></tr>`
    + `<tr><td>Prepayment</td><td>${esc(D.model.prepay_desc)}</td></tr>`
    + `<tr><td>Reinvestment</td><td>${D.model.reinvest_scheduled ? 'Prepaid and scheduled principal' : 'Prepaid principal'} earns ${reinvLabel()}</td></tr></tbody></table>`
    + `<h3>Checks</h3><table><tbody><tr><td><span class="d ${worst}"></span>This run</td><td><a href="models.html#checks">${n('PASS')} pass · ${n('WATCH')} watch · ${n('FAIL')} fail · ${n('INFO')} info</a></td></tr></tbody></table>`
    + `<h3>Known limits</h3><div>Asset side only: interest on the securities plus reinvestment at ${reinvLabel()}; no funding, hedges, premium or discount amortization. EaR depends on the calibration (see the range on the risk page). <a href="models.html#limits">All limits</a></div>`
    + `</div></div></div></div>`;
  wirePopover(); wireTips();
}

function wirePopover(){ const b = $('asofBtn'), p = $('asofPop'); if (!b || !p) return;
  const set = o => { p.hidden = !o; b.setAttribute('aria-expanded', String(o)); if (!o) return;
    const r = b.getBoundingClientRect(), w = Math.min(460, window.innerWidth - 32);
    p.style.width = w + 'px'; p.style.left = Math.min(Math.max(16, r.right - w), window.innerWidth - w - 16) + 'px'; p.style.top = (r.bottom + 6) + 'px';
    p.style.maxHeight = Math.max(200, window.innerHeight - r.bottom - 22) + 'px'; };
  b.addEventListener('click', e => { e.stopPropagation(); set(p.hidden); });
  document.addEventListener('click', e => { if (!p.hidden && !p.contains(e.target)) set(false); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !p.hidden) { set(false); b.focus(); } });
  window.addEventListener('scroll', () => { if (!p.hidden) set(false); }, {passive: true}); }

function wireTips(){ if (window._tips) return; window._tips = true;
  const tt = document.createElement('div'); tt.className = 'tt'; tt.hidden = true; tt.setAttribute('role', 'tooltip'); document.body.appendChild(tt);
  const show = el => { tt.textContent = el.dataset.tip; tt.hidden = false; const r = el.getBoundingClientRect(), w = tt.offsetWidth, h = tt.offsetHeight;
    tt.style.left = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), window.innerWidth - w - 8) + 'px';
    tt.style.top = (r.top - h - 8 < 8 ? r.bottom + 8 : r.top - h - 8) + 'px'; };
  const hide = () => { tt.hidden = true; };
  document.addEventListener('mouseover', e => { const el = e.target.closest && e.target.closest('[data-tip]'); if (el) show(el); });
  document.addEventListener('mouseout', e => { if (e.target.closest && e.target.closest('[data-tip]')) hide(); });
  document.addEventListener('focusin', e => { const el = e.target.closest && e.target.closest('[data-tip]'); if (el) show(el); });
  document.addEventListener('focusout', hide);
  window.addEventListener('scroll', hide, {passive: true});
  document.addEventListener('keydown', e => { if (e.key === 'Escape') hide(); }); }

// face-weighted summary of holdings grouped by a key function
function groupBy(rows, key){ const g = new Map();
  rows.forEach(r => { const k = key(r); if (!g.has(k)) g.set(k, []); g.get(k).push(r); });
  return [...g.entries()].map(([k, rs]) => { const u = rs.reduce((a, r) => a + r.upb, 0), wa = f => rs.reduce((a, r) => a + (r[f] ?? 0) * r.upb, 0) / (u || 1);
    const pr = rs.filter(r => r.price != null), pu = pr.reduce((a, r) => a + r.upb, 0);
    return {key: k, n: rs.length, upb: u, coupon: wa('coupon'), wac: wa('wac'), wala: wa('wala'), wam: wa('wam'), wal: wa('wal'),
      cpr: wa('cpr_y1'), income: rs.reduce((a, r) => a + r.income_y1, 0), price: pu ? pr.reduce((a, r) => a + r.price * r.upb, 0) / pu : null}; }); }
