// F5 — landing/auth + the main play page (portfolio, D chart, two LP interfaces with
// Buy / Sell / Deposit / Withdraw). Curve deposits are drawn on a tickSpacing-bucketed grid.
import { App, api, el, mount, fmt, sparkline, priceToTick, tickToPrice, snapTick,
         refreshState, refreshLeaderboard } from '/static/lib.js';

let notice = null;
function toast(msg, ok = true) { notice = { msg, ok }; setTimeout(() => { notice = null; App.emit(); }, 4000); App.emit(); }

async function act(body) {
  try {
    const r = await api('/action', { method: 'POST', body });
    await refreshState(); await refreshLeaderboard();
    toast(`${body.type} ${body.pool}: ok`, true);
    return r;
  } catch (e) {
    toast(`${body.type} ${body.pool}: ${e.message}`, false);
    throw e;
  }
}

async function doRegister(name) {
  name = (name || '').trim();
  if (!name) { toast('Enter a name to register.', false); return; }
  try { await api('/register', { method: 'POST', body: { name } }); await afterAuth(); }
  catch (e) {
    const taken = /taken|exist|registered/i.test(e.message || '');
    toast(taken ? `"${name}" is already taken — use Log in instead.` : e.message, false);
  }
}

async function doLogin(name) {
  name = (name || '').trim();
  if (!name) { toast('Enter your name to log in.', false); return; }
  try { await api('/login', { method: 'POST', body: { name } }); await afterAuth(); }
  catch (e) {
    toast(e.status === 404 ? `No player named "${name}" — register first.` : e.message, false);
  }
}

export function renderLanding(view) {
  const registered = document.cookie.includes('registered=1');
  const log = el('input', { placeholder: 'your name', id: 'log-name' });
  const loginRow = el('div.row', { style: 'margin-top:8px' }, log,
    el('button.primary', { onClick: () => doLogin(log.value) }, 'Log in'));
  const reg = el('input', { placeholder: 'choose a name', id: 'reg-name' });
  const registerRow = el('div.row', { style: 'margin-top:8px' }, reg,
    el('button.primary', { onClick: () => doRegister(reg.value) }, 'Register'));

  const blurb = el('p.muted', {}, `You'll get a balanced bag (${App.config.quote_symbol} + ` +
    `${App.config.base_symbol}). Trade and provide liquidity across two pools; highest portfolio ` +
    `value at settlement wins.`);

  const body = registered
    ? el('div', {},
        el('div', { style: 'margin-top:12px;padding:10px;border:1px solid var(--accent);border-radius:8px' },
          el('b', { class: 'accent' }, 'You’re already registered in this browser.'),
          el('div.muted', { style: 'margin-top:4px;font-size:13px' },
            'Use Log in with your player name below. To register a different player, open a ' +
            'private / incognito window.')),
        el('p.muted', { style: 'margin:14px 0 0;font-size:12px' }, 'Log in'),
        loginRow,
        el('details', { style: 'margin-top:14px' },
          el('summary', { class: 'muted', style: 'cursor:pointer' }, 'register another player anyway'),
          el('p.muted', { style: 'margin:6px 0;font-size:12px' },
            'A browser is meant to hold one player; this reuses your current session.'),
          registerRow))
    : el('div', {},
        registerRow,
        el('p.muted', { style: 'margin:14px 0 0;font-size:12px' }, 'Already joined? Log in:'),
        loginRow);

  mount(view, el('div.panel', { style: 'max-width:480px;margin:32px auto' },
    el('h3', {}, 'Join the market-making game'), blurb, body, noticeNode()));
  view.dataset.page = 'landing';
}

async function afterAuth() { await refreshState(); location.hash = '#/'; App.emit(); }

function noticeNode() {
  if (!notice) return null;
  return el('p', { class: notice.ok ? 'ok' : 'err', style: 'margin-top:10px' }, notice.msg);
}

// The main page is built ONCE per route/phase; on each tick `updateLiveMain()` patches only the
// dynamic numbers (price, TVL, fees, portfolio, chart, positions) in place — so trade inputs, the
// open deposit form, and a curve you're drawing are NEVER torn down mid-interaction.
export function renderMain(view) {
  const s = App.state;
  if (!s.account) return renderLanding(view);
  const totalPos = posTotal(s);
  const total = s.account.balance_usd0 + s.account.balance_eth0 * s.d + totalPos;
  mount(view,
    el('div.cols',
      el('div.panel',
        el('h3', {}, 'Portfolio'),
        kvId(App.config.quote_symbol, 'pf-usd0', fmt.usd2(s.account.balance_usd0)),
        kvId(App.config.base_symbol, 'pf-eth0', ethLine(s)),
        kvId('In LP positions', 'pf-pos', fmt.usd(totalPos)),
        el('div.kv', { style: 'border-top:1px solid var(--line);margin-top:6px;padding-top:6px' },
          el('span', {}, 'Total value'), el('span', { id: 'pf-total', class: 'big' }, fmt.usd(total))),
      ),
      el('div.panel',
        el('h3', {}, `External price (D, ${App.config.quote_symbol}/${App.config.base_symbol})`),
        el('div', { id: 'dchart' }, sparkline(App.dseries.map((p) => ({ y: p.d })))),
        el('p.muted', { style: 'margin:8px 0 0' }, 'Untradeable mark. Pushing a pool toward D is a directional bet.'),
      ),
    ),
    noticeNode(),
    el('div.cols', { style: 'margin-top:16px' },
      poolCard('v3'), poolCard('curve')),
  );
  view.dataset.page = 'main';
  view.dataset.phase = s.clock.phase;
}

const posTotal = (s) => s.pools.reduce((a, p) => a + (p.your_positions || []).reduce((b, q) => b + q.value_usd0, 0), 0);
const ethLine = (s) => fmt.eth(s.account.balance_eth0) + ` (${fmt.usd(s.account.balance_eth0 * s.d)})`;

export function updateLiveMain() {
  const s = App.state;
  if (!s.account) return;
  const set = (id, txt) => { const e = document.getElementById(id); if (e) e.textContent = txt; };
  const totalPos = posTotal(s);
  set('pf-usd0', fmt.usd2(s.account.balance_usd0));
  set('pf-eth0', ethLine(s));
  set('pf-pos', fmt.usd(totalPos));
  set('pf-total', fmt.usd(s.account.balance_usd0 + s.account.balance_eth0 * s.d + totalPos));
  const dc = document.getElementById('dchart');
  if (dc) mount(dc, sparkline(App.dseries.map((p) => ({ y: p.d }))));
  const running = s.clock.phase === 'RUNNING';
  for (const label of ['v3', 'curve']) {
    const p = App.pool(label);
    if (!p) continue;
    set(`pp-${label}-price`, fmt.price(p.price) + ` ${App.config.quote_symbol}/${App.config.base_symbol}`);
    set(`pp-${label}-tvl`, fmt.usd(p.tvl_usd0));
    set(`pp-${label}-fees`, fmt.usd2(p.your_fees_usd0 || 0));
    const pc = document.getElementById(`pp-${label}-pos`);
    if (pc) mount(pc, positionsList(label, p, running));  // no inputs inside — safe to rebuild
  }
}

function kvId(k, id, v) { return el('div.kv', {}, el('span', {}, k), el('span', { id }, v)); }

function poolCard(label) {
  const s = App.state;
  const p = App.pool(label) || { price: 0, tvl_usd0: 0, your_positions: [], your_fees_usd0: 0 };
  const running = s.clock.phase === 'RUNNING';
  const title = label === 'v3' ? 'v3 pool — range liquidity' : 'curve pool — draw your curve';
  return el('div.panel',
    el('div.row', {}, el('h3', { style: 'flex:1' }, title),
      el('a', { href: `#/pool/${label}`, style: 'color:var(--muted);font-size:12px' }, 'detail →')),
    kvId('Pool price', `pp-${label}-price`, fmt.price(p.price) + ` ${App.config.quote_symbol}/${App.config.base_symbol}`),
    kvId('TVL', `pp-${label}-tvl`, fmt.usd(p.tvl_usd0)),
    kvId('Your fees', `pp-${label}-fees`, fmt.usd2(p.your_fees_usd0 || 0)),
    !running && el('p.muted', { style: 'margin:8px 0 0' }, `Trading is ${s.clock.phase.toLowerCase()}.`),
    el('div', { style: 'margin-top:10px' }, tradeRow(label, running)),
    el('details', { style: 'margin-top:10px' }, el('summary', {}, 'Deposit liquidity'),
      label === 'v3' ? rangeDeposit(label, running) : curveDeposit(label, running)),
    el('div', { id: `pp-${label}-pos` }, positionsList(label, p, running)),
  );
}

function tradeRow(label, running) {
  const s = App.state;
  const buyAmt = el('input', { type: 'number', placeholder: `${App.config.quote_symbol} in`, style: 'width:110px' });
  const sellAmt = el('input', { type: 'number', placeholder: `${App.config.base_symbol} in`, style: 'width:110px' });
  const enoughUsd = (v) => running && v > 0 && v <= s.account.balance_usd0 + 1e-9;
  const enoughEth = (v) => running && v > 0 && v <= s.account.balance_eth0 + 1e-9;
  const buyBtn = el('button.buy', { disabled: !running, onClick: () =>
    act({ type: 'buy', pool: label, payload: { amount_in: Number(buyAmt.value) } }) }, `Buy ${App.config.base_symbol}`);
  const sellBtn = el('button.sell', { disabled: !running, onClick: () =>
    act({ type: 'sell', pool: label, payload: { amount_in: Number(sellAmt.value) } }) }, `Sell ${App.config.base_symbol}`);
  return el('div',
    el('div.row', {}, buyAmt, buyBtn),
    el('div.row', { style: 'margin-top:6px' }, sellAmt, sellBtn));
}

function rangeDeposit(label, running) {
  const p = App.pool(label);
  const lo = el('input', { type: 'number', placeholder: 'price low', value: Math.round(p.price * 0.8) });
  const hi = el('input', { type: 'number', placeholder: 'price high', value: Math.round(p.price * 1.2) });
  const budget = el('input', { type: 'number', placeholder: `budget ${App.config.quote_symbol}`, value: 1000 });
  return el('div', { style: 'margin-top:8px' },
    el('p.muted', { style: 'margin:0 0 6px' }, 'Uniform liquidity over a price range.'),
    el('div.row', {}, el('span.muted', {}, 'from'), lo, el('span.muted', {}, 'to'), hi),
    el('div.row', { style: 'margin-top:6px' }, el('span.muted', {}, 'budget'), budget,
      el('button.primary', { disabled: !running, onClick: () => {
        const tl = snapTick(priceToTick(Number(lo.value)), App.config.tick_spacing);
        const th = snapTick(priceToTick(Number(hi.value)), App.config.tick_spacing) + App.config.tick_spacing;
        act({ type: 'deposit', pool: label, payload: { kind: 'range',
          tick_lower: Math.min(tl, th - App.config.tick_spacing), tick_upper: Math.max(th, tl + App.config.tick_spacing),
          budget_usd0: Number(budget.value) } });
      } }, 'Deposit')));
}

function curveDeposit(label, running) {
  const p = App.pool(label);
  const s = App.config.tick_spacing;
  const center = snapTick(priceToTick(p.price), s);
  const N = 11; // bands either side of centre
  const ticks = [];
  for (let i = -N; i <= N; i++) ticks.push(center + i * s);
  const weights = ticks.map((_, i) => Math.max(0.05, 1 - Math.abs(i - N) / (N + 1))); // default: a hump
  const grid = el('div.curvegrid', { style: `grid-template-columns:repeat(${ticks.length},1fr)` });
  const bars = ticks.map((t, i) => el('div.curvebar', { style: `height:${weights[i] * 100}%`, title: fmt.price(tickToPrice(t)) }));
  bars.forEach((b, i) => grid.appendChild(b));
  let dragging = false;
  const setFromEvent = (ev) => {
    const r = grid.getBoundingClientRect();
    const idx = Math.min(ticks.length - 1, Math.max(0, Math.floor((ev.clientX - r.left) / (r.width / ticks.length))));
    const frac = Math.min(1, Math.max(0, 1 - (ev.clientY - r.top) / r.height));
    weights[idx] = frac; bars[idx].style.height = `${frac * 100}%`;
  };
  grid.addEventListener('mousedown', (e) => { dragging = true; setFromEvent(e); });
  grid.addEventListener('mousemove', (e) => { if (dragging) setFromEvent(e); });
  window.addEventListener('mouseup', () => { dragging = false; });
  const budget = el('input', { type: 'number', placeholder: `budget ${App.config.quote_symbol}`, value: 1000 });
  return el('div', { style: 'margin-top:8px' },
    el('p.muted', { style: 'margin:0 0 6px' }, 'Click/drag to draw a non-negative liquidity profile (bucketed to tickSpacing).'),
    grid,
    el('div.row', { style: 'margin-top:8px' }, el('span.muted', {}, 'budget'), budget,
      el('button.primary', { disabled: !running, onClick: () => {
        const profile = {};
        ticks.forEach((t, i) => { if (weights[i] > 0.001) profile[t] = weights[i]; });
        act({ type: 'deposit', pool: label, payload: { kind: 'curve', profile, budget_usd0: Number(budget.value) } });
      } }, 'Deposit curve')));
}

function positionsList(label, p, running) {
  const pos = p.your_positions || [];
  if (!pos.length) return el('p.muted', { style: 'margin-top:8px;font-size:12px' }, 'No positions in this pool.');
  return el('div', { style: 'margin-top:10px' },
    el('div.muted', { style: 'font-size:12px' }, 'Your positions'),
    el('table', {}, el('tbody', {},
      ...pos.map((q) => el('tr', {},
        el('td', {}, `#${q.position_id} ${q.kind}`),
        el('td', {}, fmt.usd(q.value_usd0)),
        el('td', {}, fmt.usd2(q.fees_usd0) + ' fees'),
        el('td', {}, el('button', { disabled: !running, onClick: () =>
          act({ type: 'withdraw', pool: label, payload: { position_id: q.position_id } }) }, 'Withdraw')))))));
}
