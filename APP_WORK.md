# APP_WORK.md — build log for the Multiplayer AMM Game

Implementation of `APP_PLAN.md` §13, built in `app/`. One section per stage: what was done, what
worked, what didn't, and decisions taken. Checkbox state lives in `APP_PLAN.md`.

**Stack as built:** Python 3.10 · FastAPI 0.115 / uvicorn · SQLite (WAL) · **vanilla-JS frontend**
(no React/build/CDN — see the S0 note). Tests: stdlib `unittest`, run with `python app/tests.py`.

**How to run:** `pip install -r app/requirements.txt` then `python app/run.py` → http://127.0.0.1:8000.

---

## Stage S0 — Skeleton + frozen contracts ✅

**Done.** Repo layout under `app/` (`backend/`, `frontend/`, `tests/`, `data/`). Pinned the wire
contract in `backend/contracts.py` (Pydantic): REST auth/action/state/leaderboard models + a
WebSocket envelope `{type, data}` with a `ws()` helper and a closed set of frame types
(`d_tick/pool/clock/leaderboard/phase/hello`). Default game parameters (§10) in `backend/config.py`
as a `GameParams` dataclass (with mean-preserving `mu`, conservation tolerance, admin gate, db
path). Minimal FastAPI app (`backend/api.py`) serving `/health` + the static frontend; `run.py`
launcher (`--reset`, `--host/--port`). Frontend skeleton (`index.html`, `styles.css`, `app.js`)
with a tiny dependency-free DOM helper (`el`/`mount`/`api`/`fmt`) that pings `/health`.

**Decision / minor plan update — frontend is vanilla JS, not React.** The plan named React, but the
overriding constraints are *quick demo, offline replay, no build toolchain*. A no-build vanilla-ES-
module SPA (served statically by the backend, inline-SVG charts) needs only `python` to run and
retest — no node, npm, or CDN. Kept the structure component-like so it reads familiarly. Recorded
here and reflected in `APP_PLAN.md` §13 (F-stages).

**Worked:** contract round-trips and the health/static serving on the first run. **Issues:** none.

**Tests:** `test_contracts.py` — health/index/static served (200), every WS type round-trips +
unknown type rejected, REST read-models validate (and invalid pool rejected), params defaults/`mu`/
roundtrip. **10 tests green.**

---

## Stage B1 — Engine: the live swap loop ✅

**Done.** `backend/engine.py` — a *live* AMM engine that computes output + new price from order
arrival (not a replay). Key design choice: **band-native representation**. Because every position
is `tickSpacing`-aligned (§1/§6), liquidity is a per-band total `band_L[bt]` and a position is a
profile `{bt: L_i}`. This makes the cross-tick swap loop and arbitrary "curve" profiles simple and
exact, and active liquidity at spot is just `band_L[band(spot)]` — no liquidityNet/bitmap needed.

Implemented: pure tick/price math + `inventory` (§3); `quote_range` / `quote_curve` (shape →
budget → per-band L, with non-negativity validation and both-token amounts for the UI to gate on);
`add_position` / `remove_position`; the **cross-tick `swap`** (exact-input, fee skimmed off input,
halt on empty region §11, fee accrued pro-rata-by-L to in-range positions); `reserves` / `tvl_usd0`
/ `position_value_usd0`; and a `book()` L2/L3 snapshot with bid/ask/straddle sides read off spot.

**Worked:** every ORDERS §7 invariant holds against the live loop on the first green run —
path-independence at γ=0 (round-trip returns price + amount), geometric-mean fill, fee = γ·input,
marginal spread = fee, L never mutated by a swap, per-swap token conservation (reserves Δ = net-in /
−out, Σ fee_by_position = fee), cross-tick active-L change, and empty-region halt (consumed < asked).

**Issues:** none blocking. Band-boundary bookkeeping (which band you're in after landing exactly on
an edge) is the only fiddly part — handled by persisting the current band index `_band` and stepping
it ±spacing on each crossing rather than re-deriving from a float price.

**Tests:** `test_engine.py` — deposit math (above/below/straddle, reserves = Σ positions, negative
curve rejected), swap invariants (geo-mean, path-independence, no-L-mutation, conservation, fee=γ,
spread), cross-tick (active-L change, empty halt), add/remove round-trip, book sides. **15 tests; 25
total green.**

---

## Stage B2 — Persistence spine ✅

**Done.** `backend/persistence.py` — a `Store` over SQLite in **WAL + `synchronous=FULL`** (fsync on
every commit). The append-only `actions` table is the source of truth; `append()` durably commits
before returning the seq, so the API only acks a durable action. `read_log()` + `replay(store,
apply_fn)` rebuild exact state from the log. Plus the `accounts` and `oracle_ticks` projection
tables (rebuilt on boot, handy for SQL inspection / the D-graph), a `meta` key/value table (params,
seed, phase, timestamps), and `backup_to(dest)` using SQLite's online backup API for the §7 off-box
copy.

**Worked:** restart-survives test (write 50, drop handle, reopen → all 50 in order) and the
replay-rebuilds-exact-state reducer both pass first try; backup produces a consistent copy.

**Issues:** none. Note `isolation_level=None` (autocommit) + `synchronous=FULL` is what gives the
fsync-before-ack guarantee; verified the pragmas via `PRAGMA journal_mode/synchronous`. The true
`kill -9` durability test is exercised for real in S8 (here it's simulated by dropping the handle).

**Tests:** `test_persistence.py` — append/read order, durable pragmas, restart preserves log, replay
rebuilds state, meta roundtrip, projections upsert, consistent backup. **7 tests; 32 total green.**

---

## Stage B3 — Game core ✅

**Done.** `backend/game.py` — `Account` (Decimal balances, atomic debit/credit, portfolio mark),
`Oracle` (seeded lognormal walk, per-step σ/μ), `Pool` (wraps an Engine + fee auto-collect to LP
owner balances + maker-volume attribution + house-seed benchmark), and the `Game` state machine
(`LOBBY→RUNNING→FREEZE→SETTLED`, autostop by step count). Single mutator `_apply` shared by the live
path and replay; `Game.load(store)` rebuilds exact state from the log. House seeds both pools with a
wide (v2-like) boxcar worth `k·X`, funded with exactly the quoted amounts so seeding conserves.

**Design notes worth recording.** (1) **Validate → append → apply**: clean rejections (wrong phase,
insufficient funds, size cap, bad ownership/profile) run in a pure `_validate` *before* anything is
written, so a rejected action leaves no log entry (verified by `test_insufficient_funds_appends_
nothing`). Deposits validate fully because `quote_*` is pure. (2) **Conservation is a detector, not a
gate**: after each action the invariant is checked with a generous tolerance; on drift it appends an
`alert` and records it but **never raises** — degrade gracefully (§7). In practice it never fires
because credits/debits mirror the engine's exact deltas. (3) **Auto-collect**: fees credit LP owner
balances immediately in the input token (USD0-equiv added to `fees_usd0`); position fee counters stay
cumulative for the benchmark/LP-detail views.

**Issues & fixes.** First run: house seed under-funded — I'd funded a balanced 50/50 bag for one
pool, but two boxcars over a wide range need a non-50/50, per-pool split. Fixed by quoting both pools
first and funding the house with exactly `Σ need_eth0 / Σ need_usd0`. After that, all green including
the **replay-reproduces-exact-state** test (balances/fees/volumes/price/step/phase identical to the
string, zero alerts).

**Tests:** `test_game.py` — balanced bag, duplicate-name reject, start-seeds-and-conserves, trading
blocked pre-start, phase→SETTLED, buy+conservation, deposit/withdraw/fees/maker-volume, size cap,
curve deposit, clean-reject-appends-nothing, oracle reproducibility, **full replay determinism**,
leaderboard house benchmarks. **13 tests; 45 total green.**

---

## Stage B4 — API surface (REST + WebSocket) ✅

**Done.** `backend/api.py` — a `GameServer` holds the Store + Game behind an `asyncio.Lock` (Game is
sync/not thread-safe) and a set of WS clients. REST (§8): `/register` + `/login` (cookie `aid`,
server-authoritative), `/action` (buy/sell/deposit/withdraw → `game.act`), `/state`, `/leaderboard`,
`/pool/{pool}/detail` (price history + D series + live `book()` for the LP-detail view), `/profile/
{name}` + `/profile/name`, and admin endpoints `/admin/{params,start,monitor}` gated by name+password
(`hmac.compare_digest`). A background **ticker** advances the seeded oracle every `walk_step`s while
RUNNING/FREEZE and broadcasts; it swallows its own exceptions so it can never kill the event.
WebSocket `/ws` sends `hello` (full state) + `leaderboard` on connect, then `d_tick/clock/pool/
leaderboard` frames on each tick and after every action.

**Worked:** the whole flow (register → admin start → login → buy/deposit → leaderboard → monitor)
passes, conservation stays clean through the API, and the WS handshake delivers `hello`+`leaderboard`
under the lifespan-aware `with TestClient(app)` context. Admin password gate rejects wrong creds (403).

**Issues:** `@app.on_event` is deprecated in favour of lifespan handlers in this FastAPI version
(works fine; left as-is for the demo). Autotick is gated by `AMM_AUTOTICK`/the `autotick` arg so the
test suite runs deterministically without the background oracle firing.

**Tests:** `test_api.py` — register bag+cookie, duplicate reject, state-from-cookie, trade-blocked-
pre-start, admin gate, full start+trade+leaderboard+monitor, deposit→pool-detail-book, profile +
name change, WS hello/leaderboard. **9 tests; 54 total green.**

---

## Stage F5 — Frontend: core play ✅

**Done.** Vanilla-JS SPA. `frontend/lib.js` — DOM helper `el`, `mount`, `api`, formatters, the
`App` state store (with listeners), `connectWS` (auto-reconnecting WS client that patches state and
re-renders), inline-SVG `sparkline`, and tick↔price math mirroring the engine. `frontend/app.js` —
hash router + topbar (phase pill, live D, clock, nav). `frontend/pages_play.js` — **landing/auth**
(register/login, cookie hides Register) and the **main play page**: portfolio (USD0/ETH0/in-LP/total
marked at D), D sparkline, and two pool cards each with **Buy/Sell** (exact-input, disabled off-
RUNNING), **Deposit** (v3 = price-range + budget; curve = a click/drag **draw-your-curve grid**
bucketed to tickSpacing), and a **Withdraw** list. Added a `/config` endpoint (tickSpacing, symbols).

**Verification — headless browser (`render_check.py`).** Launches a real server and drives Chromium
through register → admin-start → buy, screenshots each state, and **fails on any console error /
page exception** (how a broken ES-module import would surface). Confirmed visually: alice's bag
debits 500 USD0 on a buy, gets ETH0, the v3 price moves 3000→3138, TVL grows, "buy v3: ok" — all
correct, no JS errors.

**Issues & fixes (caught only by the render loop, not by serving).** A real bug in `el()`: calling
`el('div.cols', nodeA, nodeB)` mis-parsed the **first child node as the attrs object**, silently
dropping the Portfolio panel and emitting a stray "null". Pure data/HTTP checks miss this entirely —
the headless render exposed it. Fixed with the standard hyperscript heuristic (treat arg 2 as a
child unless it's a plain attrs object) and hardened `mount()` to filter nullish children. This is
the §4.5/Stage-4.5 "actually render it" lesson paying off again.

**Tests:** unit suite unchanged at **54 green** (frontend verified by `render_check.py`, kept out of
the offline unittest run as it needs a browser — same split as the data pipeline's render step).

---

## Stage F6 — Frontend: profile + leaderboard + LP detail ✅

**Done.** `frontend/pages_read.js`. **Leaderboard** — all §5 fields (total value at D, both balances,
fees, taker/maker volume), me-row highlighted, the two house benchmark rows styled as non-winning.
**Profile** — stats, editable name + history, **portfolio-value-over-time sparkline**, open
positions, and an action-history table. **LP detail** — pool-price-over-time chart + a **live
level-3 order book** drawn as an inline-SVG stacked-bar chart (one coloured segment per LP order per
tickSpacing band, grey = house seed, dashed orange spot line splitting bids/asks). Backend: cheap
per-tick `snapshot_values()` feeds the profile value curve; `/pool/{pool}/detail` already serves the
live `book()`.

**Verification (render check extended).** Drove deposit → leaderboard → pool-detail → profile,
screenshotting each, no JS errors. The level-3 render is the money shot: the grey house seed shows
the smooth `q0 ∝ 1/(√Pₐ·√P_b)` density decaying as price rises, alice's deposited range sits as a
coloured hump straddling the spot line — the virtual book behaving exactly as ORDERS.md predicts.
Leaderboard shows house-v3 at $10,004 (it earned alice's fee) vs house-curve $10,000 (untouched).

**Issues:** none. **Tests:** unit suite **54 green**; F6 verified by `render_check.py` (5 screenshots).

---

## Stage F7 — Admin panel + monitoring ✅

**Done.** `frontend/pages_admin.js` — name+password login (creds held only for the admin's browser
session), a **parameters form** (all §10 fields, editable in LOBBY, auto-locked once RUNNING) wired
to `/admin/params`, a **Start game** button (`/admin/start`), and a **live monitor** (2s poll):
phase, D, time left, **conservation status**, recent alerts, and a per-account table (balances +
position count). Extended `/config` to expose `σ/k/X` so the params form pre-fills.

**Verification (render check extended).** Logged in as admin and viewed the monitor: parameters
correctly locked (game RUNNING), and **conservation reads Δ 6.0e-17 / 4.5e-12** — float-epsilon,
i.e. the ledger is exact end-to-end. No JS errors across all six screens.

**Issues:** none. **Tests:** unit suite **54 green**; F7 verified by `render_check.py` (6 screenshots).

---

## Stage S8 — Event hardening + dry run ✅

**Done.** (1) **Off-box backup** — a background task (`run_backup`, alongside the ticker) copies the
SQLite db via the online backup API every `AMM_BACKUP_SECS` (default 60) to `data/backup/` (§7
mechanism 3). (2) **Demo/deploy scripts** — `demo_seed.py` writes a deterministic, populated game to
the db (N bot players, seeded trades/deposits/withdraws) and exits; `run.py` then loads and continues
it live. `run_demo.sh` does both in one command and prints the admin creds. `README.md` documents
run / replay / retest. (3) **Chaos rehearsal** — `chaos_check.py`: plays a game over the API,
**SIGKILLs the server mid-game**, restarts on the same db, and asserts the recovered leaderboard is
byte-identical; plus a 200-request load burst and a 40-connection WS reconnect storm.

**Results.** `demo_seed.py` (6 players, 8 rounds, seed 7): conservation Δ ≈ (−2e-15, −3e-12), 0
alerts, players differentiated ($9,975–$10,009), house benchmarks present. `chaos_check.py`: **load
burst all-200; SIGKILL → restart → ZERO DATA LOSS (leaderboard identical); WS storm healthy; PASS.**
The durability unit tests also confirm a forced conservation breach raises an alert + persists it but
**never halts** the game, and an abrupt handle-drop mid-sequence reloads to identical state.

**Issues:** none. **Tests:** `test_durability.py` (3) — breach-alerts-not-halts, alert-persisted,
abrupt-drop-loses-nothing — **57 unit total green**; `chaos_check.py` + `render_check.py` both PASS.

---

## Final status

- **Unit suite: 57 green** (`python app/tests.py`) — engine invariants, persistence/replay, game core
  + determinism, REST/WS API, durability.
- **Headless UI: PASS** (`python app/render_check.py`) — 6 screens, no JS errors.
- **Chaos/durability: PASS** (`python app/chaos_check.py`) — kill -9 zero-loss, load, WS storm.
- All 9 stages (S0, B1–B4, F5–F7, S8) complete and committed. The conservation invariant reads at
  float-epsilon (≈1e-15…1e-12) throughout — the ledger is exact end-to-end.

## Add-on — public sharing via cloudflared (`app_serve.py`)

Added `app/app_serve.py`: runs the live FastAPI server (via `run.py`) and, with `--tunnel`, fronts
it with a Cloudflare quick tunnel for remote players — reusing `serve.py`'s `find_cloudflared` /
`find_tunnel_url` / `terminate_process_group` (process-group teardown on every exit path).
`--seed` seeds a populated game first; default is local-only. Verified end-to-end: captured a
`https://…trycloudflare.com` URL and fetched `/health` 200 through it; the launcher's own tunnel tore
down cleanly on Ctrl+C. Pure helpers covered by `test_app_serve.py` (**60 unit total green**).
