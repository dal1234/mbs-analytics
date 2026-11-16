// Target state: the components of a full portfolio analytics suite and how far this package has got.
// Static content (no model run needed). Status: done = complete in this package · wip = in progress (partial,
// illustrative, separate, or a prototype elsewhere) · todo = not started.
const RM_S = {done: ['Complete', '#1F7A4D', '#E7F3EC'], wip: ['In progress', '#A84F15', '#FFF1E3'], todo: ['Not started', '#6B7684', '#F1F3F5'], na: ['n/a', '#9AA4AF', 'transparent']};

// net interest income from portfolios: [item, runoff status, new-business status, note, link]
const RM_NII = {
  assets: [
    {group: 'Mortgage securities', note: 'Agency MBS (Fannie, Freddie Mac, Ginnie Mae) held for investment or lender liquidity, plus small non-agency and reverse-mortgage securities', rows: [
      ['Coupon interest', 'done', 'wip', 'Runoff: the income engine. New purchases: prototype in the NII engine (purchase policy, TBA pricing)', '#ear'],
      ['Other GAAP requirements', 'wip', 'wip', 'Premium/discount amortization at effective yield with catch-up (Freddie Mac, Ginnie Mae); consolidation and extinguishment gains or losses on Fannie MBS bought: prototype in the NII engine'],
    ]},
    {group: 'Whole loans', status: ['wip', 'todo'], note: 'All mortgage loans held: loss mitigation, cash-window loans, multifamily, legacy and reverse mortgage loans. Single family: illustrative cohorts (runoff); the rest not started', link: '#wl'},
    {group: 'Corporate liquidity and other', note: 'Cash, repo and Treasuries, advances to lenders, and principal waiting to be reinvested', rows: [
      ['Runoff cash until reinvested', 'done', 'na', 'Principal from securities and loans earns 1M SOFR until it is reinvested or repays debt', '#ear'],
      ['Other', 'todo', 'todo', 'Cash, repo and Treasuries held for liquidity; advances to lenders'],
    ]},
  ],
  funding: [
    ['Coupon interest', 'wip', 'wip', 'Fixed coupons, floating resets and discount-note yields. Runoff: the debt book on the Liabilities tab; new business: rule-based issuance that replaces maturities and calls (calls move debt from runoff to new). Separate from income for now', '#liab'],
    ['Amortization of discounts, premiums and issuance costs', 'todo', 'todo', 'Including unamortized discount written off when debt is called. Hedge-related basis adjustments are reported in hedge accounting, not here'],
  ],
  result: {status: 'todo', note: 'Integration: one balance sheet, assets less funding, run on the same paths', reported: 'Reported Q3 2026: $1,666m'},
};

const RM_OTHER = [
  ['Hedge accounting', '±', 'todo', 'Swap accruals and hedge-related basis adjustment amortization on debt and loans; inside total NII, outside portfolio NII'],
  ['Fair value gains (losses)', '±', 'todo', 'Risk-management derivatives, loan commitments, trading securities, fair-value debt'],
  ['Investment gains (losses)', '±', 'todo', 'Extinguishment of Fannie MBS bought, AFS and loan sales, held-for-sale valuation'],
  ['Fee and other income', '+', 'todo', 'Structuring and resecuritization fees, prepayment fees'],
  ['Provision for credit losses', '−', 'wip', 'Expected losses on retained loans (illustrative, Whole loans tab); CECL allowance not started', '#wl'],
  ['Allocated expenses and taxes', '−', 'todo', 'Administrative cost allocation, effective tax rate'],
  ['Other comprehensive income', '±', 'todo', 'Unrealized gains and losses on AFS securities (equity, not net income)'],
];

const RM_MODELS = [
  ['Rate scenario generator', 'done', 'Two-factor Monte Carlo (5,000 paths) and deterministic shocks, governed and validated (V1–V8)', '#rate'],
  ['Mortgage rate model', 'done', 'Mortgage = β × 10-year + mean-reverting spread; spread overlays', '#rate'],
  ['Prepayment model', 'wip', 'Pool S-curve refit on factor-file history (runs ~7% fast); loan-level hazard model in development'],
  ['Accounting engine', 'wip', 'Effective yield, catch-up, extinguishment: prototype in the NII engine'],
  ['New purchase / reinvestment policy', 'wip', 'Target balance, pacing, mix and coupon rule: prototype in the NII engine'],
  ['MBS pricing (OAS / current coupon)', 'todo', 'Purchase prices, fair value and premium for new business'],
  ['Credit transitions and loss severity', 'wip', 'Constant cohort averages (illustrative); needs a fitted, macro-driven model', '#wl'],
  ['Delinquent loan buyout model', 'todo', 'Flow of loans bought out of MBS trusts, from delinquency'],
  ['Macro scenarios (house prices, unemployment)', 'wip', 'Fixed baseline only; no stress or simulated paths', '#wl'],
  ['Funding and issuance policy', 'wip', 'Constant balance, starting mix; not yet tied to the assets', '#liab'],
  ['Callable debt exercise', 'wip', 'Single threshold rule', '#liab'],
  ['Hedging and derivatives', 'todo', 'Swaps, swaptions, futures; hedge accounting'],
  ['Capital model (ERCF)', 'todo', 'Risk-based capital by asset for allocated equity'],
];

const RM_TOOLS = [
  {group: 'Earnings', items: [
    ['Interest rate shocks', 'done', 'Parallel, ramped and curve-shape scenarios with overlays', '#rate'],
    ['Earnings at risk', 'done', 'Monte Carlo EaR 95/99, expected shortfall, calibration range, history', '#ear'],
    ['Rate-scenario attribution', 'done', 'MBS income change split into prepayment, reinvestment rate and interaction', '#rate'],
    ['Funding cost at risk', 'wip', 'Debt interest on the same paths; separate from income', '#liab'],
    ['Full NII attribution', 'todo', 'Volume, rate, prepayment, mix, funding and accounting effects on reported NII'],
    ['Prepayment model risk', 'wip', 'Prepayment multiplier overlays on the grid; model-vs-model comparison not started', '#rate'],
    ['Spread and basis risk', 'wip', 'Income: mortgage-spread overlay. Value-based spread (OAS) and SOFR/Treasury basis risk not started', '#rate'],
  ]},
  {group: 'Value and hedging', items: [
    ['Economic value of equity and duration gap', 'todo', 'Needs MBS pricing and the funding book together'],
    ['Key-rate duration and convexity profile', 'todo', 'Assets, debt and hedges by key rate; needs MBS pricing (OAS)'],
    ['Duration and convexity matching', 'todo', 'Funding and hedge mix that closes key-rate and convexity gaps: bullets vs callables, swaps, swaptions, futures'],
  ]},
  {group: 'Liquidity, capital and credit', items: [
    ['Repricing gap and maturity ladder', 'wip', 'Debt maturity profile and 12-month repricing share; asset ladder and net gap not started', '#liab'],
    ['Liquidity risk', 'todo', 'Stressed cash-flow gap, liquidity requirements, debt rollover'],
    ['Capital planning and adequacy', 'todo', 'ERCF requirement projected by scenario'],
    ['Credit stress testing', 'todo', 'House-price and unemployment shocks on whole loans'],
  ]},
  {group: 'Decision support', items: [
    ['Custom rate paths', 'wip', 'Drawing preview; not calculated', '#rate'],
    ['Strategy and what-if analysis', 'wip', 'Purchase policy inputs exist in the NII engine; funding mix and hedge what-ifs not started'],
    ['Relative value for purchases', 'todo', 'OAS, spreads and breakevens by coupon; feeds strategy and optimization'],
    ['Return on equity', 'todo', 'Portfolio P&L after tax over ERCF-allocated capital'],
    ['Optimization engine', 'todo', 'Choose purchases, funding and hedges for return per unit of capital and risk'],
  ]},
  {group: 'Controls', items: [
    ['Limit and risk-appetite monitoring', 'wip', 'EaR history exists; limits, breaches and escalation not started', '#ear'],
    ['Reconciliation to filings', 'wip', 'Debt, loan yield and reported NII references on each tab'],
    ['Model validation and backtesting', 'wip', 'Rate model V1–V8 complete; prepayment backtests in progress'],
  ]},
];

const RM_DATA = [
  ['MBS positions (CUSIP file)', 'done'], ['Rate history (FRED)', 'done'], ['Debt book', 'wip'], ['Whole-loan cohorts', 'wip'],
  ['Pool and loan performance history', 'wip'], ['Derivative positions', 'todo'], ['Market prices and OAS', 'todo'], ['General ledger tie-out', 'todo'],
];

const rmPill = (s, small) => `<span class="rmp${small ? ' sm' : ''}" style="color:${RM_S[s][1]};background:${RM_S[s][2]};border-color:${RM_S[s][1]}33">${s === 'done' ? '✓ ' : s === 'wip' ? '◐ ' : s === 'todo' ? '○ ' : ''}${RM_S[s][0]}</span>`;
const rmLink = (l, txt) => l ? `<a class="rml" href="${l}" title="Open this tab" onclick="setView('${l.slice(1)}');return false">${txt ? txt + ' ' : ''}→</a>` : '';
function rmCount(){
  const all = [];
  RM_NII.assets.forEach(g => g.rows ? g.rows.forEach(r => { all.push(r[1], r[2]); }) : all.push(...g.status));
  RM_NII.funding.forEach(r => { all.push(r[1], r[2]); }); all.push(RM_NII.result.status);
  RM_OTHER.forEach(r => all.push(r[2])); RM_MODELS.forEach(r => all.push(r[1])); RM_TOOLS.forEach(g => g.items.forEach(r => all.push(r[1]))); RM_DATA.forEach(r => all.push(r[1]));
  const c = {done: 0, wip: 0, todo: 0}; all.forEach(s => { if (c[s] != null) c[s]++; }); return c; }

function renderTarget(){
  const el = $('view-tgt'); if (!el || el.dataset.done) return; el.dataset.done = '1';
  const c = rmCount(), n = c.done + c.wip + c.todo;
  const seg = s => `<div style="width:${(100 * c[s] / n).toFixed(1)}%;background:${RM_S[s][1]}" title="${RM_S[s][0]}: ${c[s]}"></div>`;
  const head = `<div class="card rmhead"><div><h2>Target state: a full portfolio analytics suite</h2><div class="sub">What a complete suite for the retained portfolio needs, and how far this package has got. Hover any item for detail; → opens its tab.</div></div>
    <div class="rmbar">${seg('done')}${seg('wip')}${seg('todo')}</div>
    <div class="rmleg">${['done', 'wip', 'todo'].map(s => `<span>${rmPill(s)} <b>${c[s]}</b> of ${n}</span>`).join('')}</div></div>`;

  // NII from portfolios: assets − funding = NII, each line with runoff | new business
  const rowH = r => `<div class="rmrow" title="${esc(r[3] || '')}"><div class="rmi">${r[0]}${r[4] ? ' ' + rmLink(r[4], '') : ''}</div><div class="rmc">${rmPill(r[1], 1)}</div><div class="rmc">${rmPill(r[2], 1)}</div></div>`;
  const colH = `<div class="rmrow rmch"><div></div><div class="rmc">Runoff</div><div class="rmc">New business</div></div>`;
  const assets = RM_NII.assets.map(g => g.rows ? `<div class="rmg"><div class="rmgh" title="${esc(g.note || '')}">${g.group}</div><div class="rmsub">${g.rows.map(rowH).join('')}</div></div>`
    : `<div class="rmg"><div class="rmrow rmgrow" title="${esc(g.note || '')}"><div class="rmgh" style="margin:0">${g.group}${g.link ? ' ' + rmLink(g.link, '') : ''}</div><div class="rmc">${rmPill(g.status[0], 1)}</div><div class="rmc">${rmPill(g.status[1], 1)}</div></div></div>`).join('');
  const nii = `<div class="card stackc"><div><h2>Net interest income from portfolios</h2><div class="sub">Interest income on every asset in the retained and liquidity portfolios, less interest on the debt that funds them, split by what is on the books today (runoff) and what is bought or issued later (new business)</div></div>
    <div class="rmeq">
     <div class="rmblk"><div class="rmbt" style="border-color:#1F4E8C">Interest income · assets</div>${colH}${assets}</div>
     <div class="rmop">−</div>
     <div class="rmblk"><div class="rmbt" style="border-color:#D9772B">Interest expense · funding debt</div>${colH}<div class="rmg">${RM_NII.funding.map(rowH).join('')}</div></div>
     <div class="rmop">=</div>
     <div class="rmblk rmres"><div class="rmbt" style="border-color:#15202B">NII from portfolios</div><div class="rmbig">Assets − funding</div>${rmPill(RM_NII.result.status)}<div class="rmrep" title="${esc(RM_NII.result.note)}">${RM_NII.result.reported}</div></div>
    </div></div>`;

  const card = (t, s, d, l, extra) => `<div class="rmcard" style="border-left-color:${RM_S[s][1]}" title="${esc(d)}"><div class="rmct"><span>${extra ? `<span class="rmsign">${extra}</span>` : ''}${t}${l ? ' ' + rmLink(l, '') : ''}</span>${rmPill(s, 1)}</div></div>`;
  // a category as a vertical list, items grouped under Complete / In progress / Not started
  const slist = items => ['done', 'wip', 'todo'].map(s => { const it = items.filter(x => x.s === s);
    return it.length ? `<div class="rsg"><div class="rsh">${rmPill(s, 1)}<span class="rsn">${it.length}</span></div><ul class="rsl" style="border-left-color:${RM_S[s][1]}">`
      + it.map(x => `<li title="${esc(x.d || '')}">${x.sign ? `<span class="rmsign">${x.sign}</span>` : ''}${x.t}${x.l ? ' ' + rmLink(x.l, '') : ''}</li>`).join('') + '</ul></div>' : ''; }).join('');
  const col = (title, items) => `<div class="rcol"><div class="rct">${title}</div>${slist(items)}</div>`;
  const tools = `<div class="card stackc"><div><h2>Analytics this enables</h2><div class="sub">Tools that sit on top of the income, funding and behavioral models · hover an item for detail</div></div>
    <div class="rcols">${RM_TOOLS.map(g => col(g.group, g.items.map(r => ({t: r[0], s: r[1], d: r[2], l: r[3]})))).join('')}</div></div>`;
  const parts = `<div class="card stackc"><div><h2>What it is built from</h2><div class="sub">Income statement lines outside portfolio NII, the behavioral and market models, and the data underneath</div></div>
    <div class="rcols">${col('Other income statement lines', RM_OTHER.map(r => ({t: r[0], sign: r[1], s: r[2], d: r[3], l: r[4]})))}
    ${col('Behavioral and market models', RM_MODELS.map(r => ({t: r[0], s: r[1], d: r[2], l: r[3]})))}
    ${col('Data foundations', RM_DATA.map(([t, s]) => ({t, s})))}</div></div>`;
  el.innerHTML = head + nii + tools + parts; }
