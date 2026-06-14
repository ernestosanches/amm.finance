// F6 — leaderboard, profile (stats + value curve + history), pool detail (price + level-3 book).
import { App, api, el, mount, fmt, sparkline, tickToPrice } from '/static/lib.js';

const colorFor = (key) => {
  const palette = ['#4f8cff', '#ff7f0e', '#2ca02c', '#e377c2', '#17becf', '#bcbd22',
                   '#9467bd', '#8c564b', '#d62728', '#1f9e89'];
  let h = 0; const s = String(key);
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return palette[h % palette.length];
};

export async function renderLeaderboard(view) {
  let lb = App.leaderboard;
  try { lb = await api('/leaderboard'); App.leaderboard = lb; } catch { /* use cached */ }
  const me = App.state.account && App.state.account.account_id;
  mount(view,
    el('div.panel',
      el('h3', {}, 'Leaderboard'),
      el('p.muted', { style: 'margin:0 0 10px' }, `Marked at current D = ${fmt.price(lb.d)} USD0/ETH0. House rows are non-winning benchmarks.`),
      el('table', {},
        el('thead', {}, el('tr', {},
          el('th', {}, '#'), el('th', {}, 'Player'), el('th', {}, 'Total value'),
          el('th', {}, App.config.quote_symbol), el('th', {}, App.config.base_symbol),
          el('th', {}, 'Fees'), el('th', {}, 'Taker vol'), el('th', {}, 'Maker vol'))),
        el('tbody', {}, ...lb.rows.map((r, i) => el('tr', {
          class: r.is_house ? 'house' : (r.account_id === me ? 'me' : '') },
          el('td', {}, r.is_house ? '·' : String(i + 1)),
          el('td', {}, r.is_house ? r.name :
            el('a', { href: '#/profile/' + encodeURIComponent(r.name), style: 'color:var(--blue);text-decoration:none' }, r.name)),
          el('td', {}, fmt.usd(r.total_value_usd0)),
          el('td', {}, fmt.usd(r.balance_usd0)),
          el('td', {}, fmt.eth(r.balance_eth0)),
          el('td', {}, fmt.usd2(r.fees_usd0)),
          el('td', {}, fmt.usd(r.taker_volume_usd0)),
          el('td', {}, fmt.usd(r.maker_volume_usd0)))))),
    ));
}

export async function renderProfile(view, name) {
  let p;
  try { p = await api('/profile/' + encodeURIComponent(name)); }
  catch (e) { return mount(view, el('p.err', {}, 'No such player: ' + name)); }
  const isMe = App.state.account && App.state.account.name === name;
  const nameInput = el('input', { value: p.name, style: 'width:160px' });
  mount(view,
    el('div.cols',
      el('div.panel',
        el('h3', {}, 'Profile — ' + p.name),
        isMe && el('div.row', { style: 'margin-bottom:10px' }, nameInput,
          el('button', { onClick: async () => {
            try { await api('/profile/name', { method: 'POST', body: { new_name: nameInput.value.trim() } });
              location.hash = '#/profile/' + encodeURIComponent(nameInput.value.trim()); }
            catch (e) { alert(e.message); } } }, 'Rename')),
        kv(App.config.quote_symbol, fmt.usd2(p.balance_usd0)),
        kv(App.config.base_symbol, fmt.eth(p.balance_eth0)),
        kv('Fees collected', fmt.usd2(p.fees_usd0)),
        kv('Taker volume', fmt.usd(p.taker_volume_usd0)),
        kv('Maker volume', fmt.usd(p.maker_volume_usd0)),
        p.name_history && p.name_history.length ?
          el('p.muted', { style: 'margin-top:8px;font-size:12px' }, 'Was: ' + p.name_history.join(', ')) : null,
      ),
      el('div.panel',
        el('h3', {}, 'Portfolio value over time (marked at D)'),
        sparkline((p.value_history || []).map((h) => ({ y: h.value })), { color: '#2ca02c' }),
        el('div', { style: 'margin-top:8px' },
          el('div.muted', { style: 'font-size:12px' }, 'Open positions'),
          (p.positions || []).length
            ? el('table', {}, el('tbody', {}, ...p.positions.map((q) => el('tr', {},
                el('td', {}, `#${q.position_id} ${q.pool}/${q.kind}`), el('td', {}, fmt.usd(q.value_usd0))))))
            : el('p.muted', { style: 'font-size:12px' }, 'none')),
      ),
    ),
    el('div.panel', { style: 'margin-top:16px' },
      el('h3', {}, 'Action history'),
      el('table', {}, el('tbody', {}, ...(p.history || []).slice(-40).reverse().map((h) =>
        el('tr', {}, el('td', {}, '#' + h.seq), el('td', {}, h.kind),
          el('td', { style: 'text-align:left;color:var(--muted);font-size:12px' }, JSON.stringify(h.payload).slice(0, 80))))))),
  );
}

export async function renderPoolDetail(view, pool) {
  let d;
  try { d = await api('/pool/' + pool + '/detail'); }
  catch (e) { return mount(view, el('p.err', {}, 'No such pool: ' + pool)); }
  mount(view,
    el('div.panel',
      el('div.row', {}, el('h3', { style: 'flex:1' }, `${pool} pool — detail`),
        el('a', { href: '#/', style: 'color:var(--muted);font-size:12px' }, '← back')),
      kv('Pool price', fmt.price(d.price) + ` ${App.config.quote_symbol}/${App.config.base_symbol}`),
      kv('TVL', fmt.usd(d.tvl_usd0)),
      el('h3', { style: 'margin-top:16px' }, 'Pool price over time'),
      sparkline((d.price_history || []).map((h) => ({ y: h.price }))),
    ),
    el('div.panel', { style: 'margin-top:16px' },
      el('h3', {}, 'Level-3 order book (live)'),
      el('p.muted', { style: 'margin:0 0 8px' },
        'Each price band is a stack of individual LP orders. ',
        el('span', { style: 'color:var(--green)' }, 'bids'), ' (below spot) · ',
        el('span', { style: 'color:var(--red)' }, 'asks'), ' (above spot). Updates each tick.'),
      bookChart(d.book || [], d.price)),
  );
}

function kv(k, v) { return el('div.kv', {}, el('span', {}, k), el('span', {}, v)); }

// Stacked per-position level-3 book as inline SVG. x = price band (ascending), height = depth (ETH0),
// each band a stack of segments coloured per owner; a dashed line marks spot.
function bookChart(book, spot) {
  const W = 900, H = 280, padB = 28, padL = 4;
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'chart'); svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.style.height = '300px';
  if (!book.length) { svg.appendChild(text(svg, 'no liquidity yet', 8, 20)); return svg; }
  const bands = book.slice().sort((a, b) => a.tick_lower - b.tick_lower);
  const maxDepth = Math.max(...bands.map((b) => b.depth_eth0)) || 1;
  const bw = (W - 2 * padL) / bands.length;
  bands.forEach((band, i) => {
    const x = padL + i * bw;
    let y = H - padB;
    const orders = (band.orders && band.orders.length) ? band.orders : [{ owner: 'agg', q_eth0: band.depth_eth0 }];
    for (const o of orders) {
      const h = (o.q_eth0 / maxDepth) * (H - padB - 10);
      if (h <= 0) continue;
      const rect = document.createElementNS(svg.namespaceURI, 'rect');
      rect.setAttribute('x', x + 0.5); rect.setAttribute('width', Math.max(1, bw - 1));
      rect.setAttribute('y', y - h); rect.setAttribute('height', h);
      rect.setAttribute('fill', o.owner === 0 ? '#666' : colorFor(o.owner));
      rect.setAttribute('opacity', band.side === 'bid' ? '0.95' : band.side === 'ask' ? '0.95' : '0.8');
      const t = document.createElementNS(svg.namespaceURI, 'title');
      t.textContent = `band ${fmt.price(tickToPrice(band.tick_lower))}–${fmt.price(tickToPrice(band.tick_upper))} · ${band.side} · order ${o.owner} · ${fmt.eth(o.q_eth0)} ETH0`;
      rect.appendChild(t); svg.appendChild(rect); y -= h;
    }
  });
  // spot line
  const spotIdx = bands.findIndex((b) => tickToPrice(b.tick_upper) > spot);
  if (spotIdx >= 0) {
    const sx = padL + spotIdx * bw;
    const line = document.createElementNS(svg.namespaceURI, 'line');
    line.setAttribute('x1', sx); line.setAttribute('x2', sx);
    line.setAttribute('y1', 4); line.setAttribute('y2', H - padB);
    line.setAttribute('stroke', '#ff7f0e'); line.setAttribute('stroke-dasharray', '4 3');
    svg.appendChild(line);
    svg.appendChild(text(svg, `spot ${fmt.price(spot)}`, Math.min(sx + 4, W - 80), 14, '#ff7f0e'));
  }
  svg.appendChild(text(svg, `${fmt.price(tickToPrice(bands[0].tick_lower))}`, padL, H - 8, '#8a94a3'));
  svg.appendChild(text(svg, `${fmt.price(tickToPrice(bands[bands.length - 1].tick_upper))}`, W - 60, H - 8, '#8a94a3'));
  return svg;
}

function text(svg, s, x, y, fill = '#8a94a3') {
  const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  t.setAttribute('x', x); t.setAttribute('y', y); t.setAttribute('fill', fill);
  t.setAttribute('font-size', '11'); t.textContent = s; return t;
}
