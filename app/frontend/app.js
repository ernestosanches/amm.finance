// SPA entry: hash router + topbar + pages. F5 implements landing + main (core play);
// leaderboard/profile/pool-detail (F6) and admin (F7) are wired in their own modules.
import { App, api, el, mount, fmt, refreshState, refreshLeaderboard, connectWS } from '/static/lib.js';
import { renderLanding, renderMain } from '/static/pages_play.js';
import { renderLeaderboard, renderProfile, renderPoolDetail } from '/static/pages_read.js';
import { renderAdmin } from '/static/pages_admin.js';

const view = () => document.getElementById('view');

function topbar() {
  const bar = document.getElementById('topbar');
  const s = App.state, c = s.clock;
  const phaseColor = { RUNNING: 'green', FREEZE: 'accent', SETTLED: 'red', LOBBY: 'muted' }[c.phase] || 'muted';
  mount(bar,
    el('span.brand', {}, 'AMM Game'),
    el('span.pill', { class: 'pill ' + phaseColor }, c.phase),
    el('span.muted', {}, 'D = ', el('b', { class: 'accent' }, fmt.price(s.d)), ` ${App.config.quote_symbol}/${App.config.base_symbol}`),
    el('span.muted', {}, '⏱ ', fmt.clock(c.remaining), ' left'),
    el('span.spacer'),
    navlink('#/', 'Play'),
    navlink('#/leaderboard', 'Leaderboard'),
    s.account && navlink('#/profile/' + encodeURIComponent(s.account.name), 'Profile'),
    navlink('#/admin', 'Admin'),
    s.account
      ? el('span.muted', {}, 'as ', el('b', {}, s.account.name))
      : el('span.muted', {}, 'not logged in'),
  );
}

function navlink(href, label) {
  const on = (location.hash || '#/') === href || (href === '#/' && location.hash === '');
  return el('a', { href, style: `margin-right:12px;color:${on ? 'var(--blue)' : 'var(--muted)'};text-decoration:none` }, label);
}

async function route() {
  const h = location.hash || '#/';
  const v = view();
  try {
    if (h.startsWith('#/leaderboard')) return renderLeaderboard(v);
    if (h.startsWith('#/profile/')) return renderProfile(v, decodeURIComponent(h.split('/')[2] || ''));
    if (h.startsWith('#/pool/')) return renderPoolDetail(v, h.split('/')[2]);
    if (h.startsWith('#/admin')) return renderAdmin(v);
    // default: landing if not logged in, else main
    if (!App.state.account) return renderLanding(v);
    return renderMain(v);
  } catch (e) {
    mount(v, el('p.err', {}, 'Error: ' + e.message));
  }
}

function rerender() {
  topbar();
  // re-render the live pages on state changes (read-only pages refresh themselves)
  const h = location.hash || '#/';
  if (h === '#/' || h === '') {
    if (App.state.account) renderMain(view()); else renderLanding(view());
  } else if (h.startsWith('#/leaderboard')) {
    renderLeaderboard(view());
  }
}

async function boot() {
  const status = document.getElementById('statusbar');
  try {
    App.config = await api('/config');
    await refreshState();
    await refreshLeaderboard();
  } catch (e) {
    mount(view(), el('p.err', {}, 'Backend unreachable: ' + e.message));
    status.textContent = 'disconnected';
    return;
  }
  status.innerHTML = 'connected · server-authoritative · vanilla JS, no build step';
  App.on(rerender);
  window.addEventListener('hashchange', route);
  connectWS();
  topbar();
  route();
}

boot();
