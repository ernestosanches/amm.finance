// S0 — frontend skeleton + tiny dependency-free helpers. Expanded into the full SPA in F5–F7.
// No framework, no build step, no CDN: vanilla ES modules + inline SVG, served statically by the
// backend, so the demo runs offline with just `python app/run.py`.

// --- tiny DOM helper: el('div.cls', {attrs}, ...children) ---
export function el(spec, attrs = {}, ...children) {
  const [tag, ...classes] = spec.split('.');
  const node = document.createElement(tag || 'div');
  if (classes.length) node.className = classes.join(' ');
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'html') node.innerHTML = v;
    else if (k in node && k !== 'list') node[k] = v;
    else node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.appendChild(typeof c === 'string' || typeof c === 'number' ? document.createTextNode(String(c)) : c);
  }
  return node;
}

export function mount(target, ...nodes) {
  target.replaceChildren(...nodes);
}

export async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* no body */ }
  if (!res.ok) throw Object.assign(new Error((data && data.detail) || res.statusText), { status: res.status, data });
  return data;
}

export const fmt = {
  usd: (v) => '$' + Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 0 }),
  usd2: (v) => '$' + Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 2 }),
  eth: (v) => Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 4 }),
  price: (v) => Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 2 }),
  pct: (v) => (Number(v || 0) * 100).toFixed(1) + '%',
  clock: (s) => { s = Math.max(0, Math.floor(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; },
};

async function boot() {
  const view = document.getElementById('view');
  const status = document.getElementById('statusbar');
  try {
    const h = await api('/health');
    mount(view, el('p.ok', {}, 'Backend healthy. The full app loads here in Stage F5.'));
    status.textContent = `connected · ${h.service}`;
  } catch (e) {
    mount(view, el('p.err', {}, 'Backend unreachable: ' + e.message));
    status.textContent = 'disconnected';
  }
}

boot();
