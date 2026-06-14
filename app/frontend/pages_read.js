// F6 — leaderboard, profile, pool-detail. (Placeholder until Stage F6.)
import { el, mount } from '/static/lib.js';

export function renderLeaderboard(view) { mount(view, el('p.muted', {}, 'Leaderboard — coming in F6.')); }
export function renderProfile(view, name) { mount(view, el('p.muted', {}, `Profile ${name} — coming in F6.`)); }
export function renderPoolDetail(view, pool) { mount(view, el('p.muted', {}, `Pool ${pool} detail — coming in F6.`)); }
