// Shared frontend library: DOM helper, API client, formatters, app state store, WS client,
// inline-SVG charts, and tick<->price math. No dependencies, no build step.

export function el(spec, attrs, ...children) {
  // Heuristic: if arg 2 isn't a plain attrs object (it's a Node, array, string, number, or null),
  // treat it as the first child. Lets you write el('div.cols', childA, childB) safely.
  if (attrs == null || typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs)) {
    if (attrs !== undefined) children.unshift(attrs);
    attrs = {};
  }
  const [tag, ...classes] = spec.split('.');
  const node = document.createElement(tag || 'div');
  if (classes.length) node.className = classes.join(' ');
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'html') node.innerHTML = v;
    else if (k === 'value') node.value = v;
    else if (k in node && k !== 'list' && k !== 'type') node[k] = v;
    else node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.appendChild(typeof c === 'string' || typeof c === 'number' ? document.createTextNode(String(c)) : c);
  }
  return node;
}

export function mount(target, ...nodes) {
  target.replaceChildren(...nodes.flat(Infinity).filter((n) => n != null && n !== false));
}

export async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* empty */ }
  if (!res.ok) throw Object.assign(new Error((data && (data.detail || data.error)) || res.statusText),
    { status: res.status, data });
  return data;
}

export const fmt = {
  usd: (v) => '$' + Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 0 }),
  usd2: (v) => '$' + Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 2 }),
  eth: (v) => Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 4 }),
  price: (v) => Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 2 }),
  num: (v, d = 2) => Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: d }),
  clock: (s) => { s = Math.max(0, Math.floor(s)); const m = Math.floor(s / 60); return `${m}:${String(s % 60).padStart(2, '0')}`; },
};

// --- tick <-> price (must match backend engine.py) ---
const LN = Math.log(1.0001);
export const priceToTick = (p) => Math.floor(Math.log(p) / LN);
export const tickToPrice = (t) => Math.pow(1.0001, t);
export const snapTick = (t, s) => Math.floor(t / s) * s;

// --- app state store ---
export const App = {
  state: { account: null, d: 0, pools: [], clock: { phase: 'LOBBY', elapsed: 0, remaining: 0, step: 0 } },
  config: { tick_spacing: 60, base_symbol: 'ETH0', quote_symbol: 'USD0', size_cap_frac: 0.1 },
  leaderboard: { rows: [], d: 0 },
  dseries: [],
  listeners: new Set(),
  set(partial) { Object.assign(this.state, partial); this.emit(); },
  emit() { for (const fn of this.listeners) fn(); },
  on(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); },
  pool(label) { return this.state.pools.find((p) => p.pool === label); },
};

export async function refreshState() {
  const s = await api('/state');
  App.state.account = s.account;
  App.state.d = s.d;
  App.state.pools = s.pools;
  App.state.clock = s.clock;
  App.dseries.push({ step: s.clock.step, d: s.d });
  if (App.dseries.length > 2000) App.dseries.shift();
  App.emit();
}

export async function refreshLeaderboard() {
  App.leaderboard = await api('/leaderboard');
  App.emit();
}

export function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  let ws;
  const open = () => {
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      const d = m.data || {};
      if (m.type === 'hello' && d.state) {
        Object.assign(App.state, d.state);
        App.emit();
      } else if (m.type === 'd_tick') {
        App.state.d = d.d;
        App.dseries.push({ step: d.step, d: d.d });
        if (App.dseries.length > 2000) App.dseries.shift();
        App.emit();
      } else if (m.type === 'clock') {
        App.state.clock = d; App.emit();
      } else if (m.type === 'pool') {
        for (const pu of d.pools || []) { const p = App.pool(pu.pool); if (p) { p.price = pu.price; p.tvl_usd0 = pu.tvl_usd0; } }
        // refresh personal positions/balances after market moves
        refreshState().catch(() => {});
      } else if (m.type === 'leaderboard') {
        App.leaderboard = { rows: d.rows, d: d.d }; App.emit();
      }
    };
    ws.onclose = () => setTimeout(open, 1500); // auto-reconnect
    ws.onerror = () => { try { ws.close(); } catch {} };
  };
  open();
}

// --- inline-SVG charts ---
export function sparkline(points, { w = 320, h = 120, color = '#4f8cff' } = {}) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'chart');
  svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
  svg.setAttribute('preserveAspectRatio', 'none');
  if (!points || points.length < 2) {
    const t = document.createElementNS(svg.namespaceURI, 'text');
    t.setAttribute('x', 8); t.setAttribute('y', 20); t.setAttribute('fill', '#8a94a3');
    t.setAttribute('font-size', '11'); t.textContent = 'waiting for data…';
    svg.appendChild(t); return svg;
  }
  const ys = points.map((p) => p.y ?? p);
  const lo = Math.min(...ys), hi = Math.max(...ys), span = hi - lo || 1;
  const path = points.map((p, i) => {
    const x = (i / (points.length - 1)) * (w - 8) + 4;
    const y = h - 6 - ((p.y ?? p) - lo) / span * (h - 12);
    return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const pl = document.createElementNS(svg.namespaceURI, 'path');
  pl.setAttribute('d', path); pl.setAttribute('fill', 'none');
  pl.setAttribute('stroke', color); pl.setAttribute('stroke-width', '1.6');
  svg.appendChild(pl);
  const last = document.createElementNS(svg.namespaceURI, 'text');
  last.setAttribute('x', w - 4); last.setAttribute('y', 14); last.setAttribute('text-anchor', 'end');
  last.setAttribute('fill', color); last.setAttribute('font-size', '12');
  last.textContent = fmt.price(ys[ys.length - 1]);
  svg.appendChild(last);
  return svg;
}
