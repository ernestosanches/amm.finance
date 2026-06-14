// F7 — admin panel: parameters, start control, live monitoring (gated by name + password).
import { App, api, el, mount, fmt } from '/static/lib.js';

let creds = null;        // {name, password} held for the admin's session only
let pollTimer = null;

const PARAM_FIELDS = [
  ['d0', 'Initial price D₀'], ['sigma', 'σ per step'], ['walk_step', 'walk_step (s)'],
  ['fee', 'pool fee (γ)'], ['k', 'seed depth k'], ['x', 'player bag X'],
  ['game_length', 'game length (s)'], ['size_cap_frac', 'size cap (frac)'],
  ['range_factor', 'range factor'], ['base_symbol', 'base name'], ['quote_symbol', 'quote name'],
];

export function renderAdmin(view) {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  if (!creds) return renderLogin(view);
  renderPanel(view);
  pollTimer = setInterval(() => {
    if (!location.hash.startsWith('#/admin')) { clearInterval(pollTimer); pollTimer = null; return; }
    refreshMonitor().catch(() => {});
  }, 2000);
}

function renderLogin(view) {
  const name = el('input', { value: 'admin', placeholder: 'admin name' });
  const pw = el('input', { type: 'password', placeholder: 'admin password' });
  const err = el('p.err', { style: 'margin-top:8px' });
  mount(view, el('div.panel', { style: 'max-width:420px;margin:32px auto' },
    el('h3', {}, 'Admin login'),
    el('p.muted', {}, 'Name + password gate (the one real privilege boundary). The demo launcher prints the password.'),
    el('div.row', { style: 'margin-top:10px' }, name),
    el('div.row', { style: 'margin-top:8px' }, pw),
    el('div.row', { style: 'margin-top:10px' },
      el('button.primary', { onClick: async () => {
        const c = { name: name.value.trim(), password: pw.value };
        try { await api('/admin/monitor', { method: 'POST', body: c }); creds = c; renderAdmin(view); }
        catch (e) { err.textContent = 'Rejected: ' + e.message; } } }, 'Enter')),
    err));
}

let lastMonitor = null;

async function refreshMonitor() {
  lastMonitor = await api('/admin/monitor', { method: 'POST', body: creds });
  const box = document.getElementById('admin-monitor');
  if (box) mount(box, monitorBody(lastMonitor));
}

function renderPanel(view) {
  mount(view,
    el('div.cols',
      paramsPanel(),
      el('div.panel', { id: 'admin-monitor' }, el('p.muted', {}, 'loading monitor…'))),
  );
  refreshMonitor().catch((e) => { const b = document.getElementById('admin-monitor'); if (b) mount(b, el('p.err', {}, e.message)); });
}

function paramsPanel() {
  const phase = App.state.clock.phase;
  const locked = phase !== 'LOBBY';
  const cur = App.config;
  const inputs = {};
  const rows = PARAM_FIELDS.map(([k, label]) => {
    const i = el('input', { value: cur[k] != null ? cur[k] : '', disabled: locked, style: 'width:120px' });
    inputs[k] = i;
    return el('div.kv', {}, el('span', {}, label), i);
  });
  return el('div.panel',
    el('h3', {}, 'Parameters'),
    locked ? el('p.muted', {}, `Locked — game is ${phase.toLowerCase()}.`)
           : el('p.muted', {}, 'Editable before start. Defaults from §10.'),
    ...rows,
    el('div.row', { style: 'margin-top:12px' },
      el('button', { disabled: locked, onClick: async () => {
        const params = {};
        for (const [k] of PARAM_FIELDS) {
          const v = inputs[k].value;
          params[k] = (k === 'base_symbol' || k === 'quote_symbol') ? v : Number(v);
        }
        try { await api('/admin/params', { method: 'POST', body: { ...creds, params } });
          App.config = await api('/config'); renderAdmin(document.getElementById('view')); }
        catch (e) { alert('params: ' + e.message); } } }, 'Save params'),
      el('button.primary', { disabled: locked, onClick: async () => {
        try { await api('/admin/start', { method: 'POST', body: creds }); }
        catch (e) { alert('start: ' + e.message); } } }, 'Start game')),
  );
}

function monitorBody(m) {
  const cons = m.conservation;
  return el('div',
    el('h3', {}, 'Monitor'),
    el('div.kv', {}, el('span', {}, 'Phase'), el('span', { class: 'pill' }, m.phase)),
    el('div.kv', {}, el('span', {}, 'D'), el('span', {}, fmt.price(m.d))),
    el('div.kv', {}, el('span', {}, 'Time left'), el('span', {}, fmt.clock(m.clock.remaining))),
    el('div.kv', {}, el('span', {}, 'Conservation'),
      el('span', { class: cons.ok ? 'ok' : 'err' },
        cons.ok ? `OK (Δ ${cons.d_eth0.toExponential(1)} / ${cons.d_usd0.toExponential(1)})` : 'DRIFT')),
    m.alerts && m.alerts.length ? el('div', {},
      el('div.err', { style: 'margin-top:6px;font-size:12px' }, `${m.alerts.length} alert(s):`),
      ...m.alerts.slice(-5).map((a) => el('div.muted', { style: 'font-size:11px' }, a))) : null,
    el('div.muted', { style: 'margin-top:10px;font-size:12px' }, `${m.players.length} accounts`),
    el('table', {}, el('thead', {}, el('tr', {}, el('th', {}, 'Account'),
      el('th', {}, App.config.quote_symbol), el('th', {}, App.config.base_symbol), el('th', {}, 'Pos'))),
      el('tbody', {}, ...m.players.map((p) => el('tr', { class: p.is_house ? 'house' : '' },
        el('td', {}, p.name), el('td', {}, fmt.usd(p.balance_usd0)),
        el('td', {}, fmt.eth(p.balance_eth0)), el('td', {}, String(p.positions)))))),
  );
}
