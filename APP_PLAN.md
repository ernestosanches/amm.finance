# APP_PLAN.md — Multiplayer AMM Game (build spec)

A closed-economy, multiplayer market-making game for a ~1-hour live event. Players get a
starting bag of two tokens and compete to end with the highest portfolio value, marked at a
visible-but-untradeable external price `D`. They act only through two internal pools — a
Uniswap-v3-style pool and a "draw-your-curve" pool — by buying, selling, depositing, or
withdrawing liquidity. Winner = highest USD0-marked portfolio at settlement.

**Companion specs (engine internals, not repeated here):** `ORDERS.md` (the OrderBook class —
AMM-as-orderbook model, swap math, fees, tick-indexed liquidity, the OrderBook API) and
`DETAILS.md` (level-3 orderbook data definition). This document is the application built around
that engine; wherever engine behaviour is referenced below it is defined in those two files.

---

## 0. Locked design decisions

- **Closed economy.** No real trading against `D`; `D` is only a visible scoreboard and the
  terminal mark. All token conversion happens inside the two pools, players against players.
- **`D` is exogenous and untradeable.** Because nobody can trade at `D`, a change in `D` does
  not by itself open a profitable trade against a pool. Buying or selling in a pool to push its
  price toward `D` is therefore a *directional bet* — it pays only if `D` settles on your side
  — not a risk-free correction.
- **Execution: real-time, submission-order.** No batching. Trades execute on arrival.
- **Single incentive: terminal portfolio value in USD0.** No per-account rewards, and no
  rewards for trading volume or liquidity depth. Scoring depends only on final holdings, so
  registering extra accounts yields no free advantage (see §4.4).
- **Fees auto-collected to the player's balance the moment they accrue** (collected, not
  reinvested into liquidity — see `ORDERS.md`).
- **House seed = benchmark.** Each pool is seeded with value `k·X` of full-range (constant
  product) liquidity, house-owned, doing nothing. `seed_value / k` is the passive-LP benchmark
  and appears as a non-winning reference row on the leaderboard.
- **Settlement freeze.** Trading halts one `walk_step` before game end; the final `D` used for
  scoring is a step nobody could trade on.

### Locked build decisions

- **Backend: Python (FastAPI + websockets)**, reusing the existing `OrderBook` engine and its
  test suite verbatim — *not* Go. At ≤~100 players on 5 s ticks, perf is a non-issue, so a
  second-language reimplementation of the error-prone tick/fee math buys nothing.
- **Persistence: SQLite in WAL mode, single process, action-log-as-source-of-truth** (§7). The
  determinism already required (§4.6) *is* the crash-recovery mechanism: the durable action log
  is authoritative, in-memory state is a replayable cache.
- **Execution: real-time submission-order only.** Batch / uniform-clearing mode is dropped (not
  a toggle, not deferred).
- **Buy / Sell are exact-input** ("spend N USD0" / "sell N ETH0"), the intuitive button; exact-
  output is not exposed.
- **Per-trade size cap default ON** (modest, e.g. 10 % of pool reserves) — the one cheap guard
  that blunts manual sandwiching without changing the model (§4.4). Admin-tunable, can disable.
- **Balances in `Decimal`/integers; engine internals in float.** A ledger must conserve exactly;
  the conservation invariant (§4.5) is asserted with a tiny float tolerance (§7).

---

## 1. Tokens & price convention (pin this exactly)

- **ETH0** — the volatile asset. **USD0** — the numéraire (everything is scored in USD0).
  (Display names are admin-editable; the *roles* are fixed.)
- **`D` = price of ETH0 in USD0** (USD0 per ETH0), e.g. ~3000.
- **Engine mapping:** base token = ETH0, quote token = USD0, so the engine's internal price
  `P ≡ D`. Do not invert. The engine (`ORDERS.md`) defines price as `P = quote/base = USD0/ETH0`.
- **Portfolio value (USD0):** `V = balance_USD0 + balance_ETH0 · D`.
- Each **pool has its own internal price** `P_pool` (from its reserves), which may differ from
  `D`. The UI shows both.
- **No decimal scaling.** Tokens are virtual, so use `P ≡ D` directly — drop the historical
  pipeline's `10^(d1−d0)` gymnastics. Pin a **finite tick range** for the bitmap (e.g. `D ∈
  [0.1·D₀, 10·D₀]`) so liquidity ops and the curve-draw UI have bounded support.

---

## 2. Starting allocation, seeding, and the benchmark

- **Player bag:** on registration each player receives a balanced bag of total value `X` USD0
  at the initial price `D₀`: `X/2` USD0 and `(X/2)/D₀` ETH0. "Balanced" = ready to LP into a
  full-range position with no leftover.
- **Seed per pool:** value `k·X`, full-range (v2) liquidity, balanced at `D₀`, **house-owned**.
  Both pools seeded identically at start. `k` and `X` are admin parameters.
- **Benchmark (per pool):**
  `benchmark_pool(t) = [ seed_USD0(t) + seed_ETH0(t)·D_t + seed_fees_collected(t) ] / k`
  i.e. the live value of the house seed normalized to one starting bag. This is what a player
  who deposited their whole bag full-range at `t=0` and did nothing would be worth now. Shown
  as `House (seed, v3-pool)` and `House (seed, curve-pool)` reference rows, excluded from
  winning.
- **Turnout note:** with fixed `k`, the depth ratio `k/N` shifts with the number of players
  `N` — calm/LP-diluted at small `N`, whippy at large `N`. `k` is admin-tunable; default below.

---

## 3. The two pools = one engine

Both pools are the **same OrderBook engine** (tick-indexed liquidity, swap loop, fee accrual —
defined in `ORDERS.md`). They differ only in the **liquidity-add front-end**:

- **v3 pool:** deposit takes a `range` parameter `[tickLower, tickUpper]`; liquidity is uniform
  `L` over that range (a boxcar).
- **Curve pool:** deposit takes a `curve` parameter — an arbitrary **non-negative** liquidity
  profile `L_i` per tick. Validation: `L_i ≥ 0` everywhere (a profile is only a valid AMM curve
  if its depth is non-negative — negative depth has no meaning). The required USD0/ETH0 to fund
  the profile at the current pool price is computed per-tick by the engine (`ORDERS.md`); the
  player must hold enough of each token.

Everything downstream (swaps, fees, level-3 snapshots) is identical across both pools.

---

## 4. Backend (Python — see §0 locked decisions) — components

### 4.1 Classes / modules

- **`OrderBook`** (defined in `ORDERS.md`) — per pool. Holds tick-indexed liquidity, executes
  swaps, tracks fee growth. Game needs from it: `swap(side, amountIn) -> (amountOut, fee,
  newPrice)`; `addLiquidity(profile, owner) -> positionId` (uniform for v3, arbitrary for
  curve); `removeLiquidity(positionId) -> (amount0, amount1, feesOwed)`; `positionValue(
  positionId, P) -> (amount0, amount1)`; `priceNow()`; level-3 snapshot per `DETAILS.md`. Treat
  the exact method names/signatures in `ORDERS.md` as authoritative if they differ from these.
- **`Account`** — one per player and one for the house. Fields: `name`, `is_house`,
  `balance_USD0`, `balance_ETH0`, list of `positionId`s per pool, cumulative `fees_collected`,
  `taker_volume_USD0`, `maker_volume_USD0`. Methods: debit/credit (atomic, never negative),
  `portfolioValue(D)`.
- **`Pool`** — wraps an `OrderBook` + fee rate + the auto-collect logic + maker-volume
  attribution. On each swap, splits the fee across in-range positions pro-rata by their share
  of in-range `L` and **credits each owner's Account immediately** (auto-collect). Records
  maker volume against each LP whose liquidity was traded through.
- **`Oracle`** — the `D` random walk (§4.3). Seeded RNG → reproducible.
- **`Game`** — state machine + clock + settlement (§4.2). Owns the two Pools, the Oracle, the
  Accounts, the parameters.

### 4.2 Game lifecycle (state machine)

```
LOBBY      -> registration open; players get bags on register; pools not yet seeded
RUNNING    -> admin starts: seed pools at k·X (house), start Oracle ticker + game clock,
              trading enabled, fees auto-collect, snapshots every walk_step
FREEZE     -> at (gameLength − walk_step): trading disabled; Oracle advances one final step
SETTLED    -> mark every account at the final (frozen-step) D; freeze leaderboard; replay data
              retained
```

Admin sets `gameLength`; game **autostops** (RUNNING→FREEZE→SETTLED) on the clock. Elapsed and
remaining time are broadcast to all clients continuously.

### 4.3 Oracle — `D` as a lognormal random walk

Multiplicative (log-returns normal), one step every `walk_step` seconds, seeded for replay:

```
D_{t+1} = D_t · exp( μ·Δt + σ·√Δt · z_t ),   z_t ~ N(0,1),   Δt = walk_step
μ = −σ²/2     # mean-preserving drift so E[D_t] = D₀ (centered on the initial price)
```

This is a free (non-reverting) walk — it trends and its spread widens over time, which is
acceptable for a bounded 1-hour game (and is a "trending market" scenario, good for showcasing
that curve shape is a regime bet). `D` is broadcast and graphed live as the **external price**.

**Suggested defaults (admin-tunable):**
- `walk_step` = **5 s** (~720 steps/hour — smooth graph, lively but not frenetic).
- `σ` per step = **0.0045** → ~**12% hourly stdev** (gamified; higher than real ETH ~1%/hr so
  price visibly moves and creates opportunities; dial down toward 0.001–0.002 for realism).
- `D₀` = current ETH/USD at game start. Closed game has no live oracle, so admin **types it**
  in the panel (optional one-time external fetch at start; default ~3000 placeholder).

### 4.4 Execution model (real-time, submission-order)

Trades and liquidity ops execute immediately on arrival, server-side, each in a DB
transaction. There is no per-tick batching; `D` updates independently every `walk_step`.

**Accepted residual risks of real-time submission-order execution:**
- *Manual sandwiching* is theoretically possible (one trade pushes the price, a victim trades at
  the worsened price, a second trade reverts) but is hard to land at human click-speed and is
  itself exposed to `D` risk. Guard: a per-trade max size as a % of pool reserves (admin param,
  **default ON** at ~10 %; can be disabled).
- *First-mover edge* on closing an obvious pool-vs-`D` gap exists but is not risk-free (profit
  depends on the final `D`). Acceptable.
- *Multi-accounting:* the register cookie is bypassable (incognito / cleared cookies). **The
  real lever is the free starting bag**, not the trade mechanism: each registration *mints*
  value `X` from outside the ledger, so `N` alts = `N·X` of capital that can be funnelled into a
  main account via deliberately bad trades **in a thin pool the player controls**, where the only
  leakage (slippage + fees) can be made small. The conservation invariant (§4.5) stops *minting*
  inside the ledger but not the per-registration injection. For a friendly event this is
  accepted; backstop: every action is logged — flag account pairs that repeatedly trade with each
  other, and judge the winner as a person. Stronger identity (§12) only if it becomes a problem.

### 4.5 Conservation invariant (master anti-cheat)

After **every** action, assert for each token independently:
```
Σ player balances + Σ house balance + Σ pool reserves + Σ uncollected fees  =  constant
```
With auto-collect, uncollected fees ≈ 0 between actions. Any violation = reject + log. This is
the single most important integrity check.

### 4.6 Determinism

All state mutations server-side and atomic. Oracle uses a stored seed. Action log + per-step
snapshots make any game fully replayable.

---

## 5. Scoring & leaderboard

Sorted by **total portfolio value in USD0 at the current `D`** (the goal). Per-row fields, shown
on the **Leaderboard page** and individually on each **Profile**:

- **Total value (USD0) at current `D`** — primary sort key.
- **Token balances** — USD0 and ETH0.
- **Total fees collected** (USD0-equivalent).
- **Current LP positions** in each pool (TVL per pool, and per-position list).
- **Total taker volume** (USD0 notional you swapped).
- **Total maker volume** (USD0 notional swapped *through your* liquidity).

House appears as two non-winning **benchmark rows** (`seed_value/k` per pool, §2). No APR
anywhere — absolute figures only.

---

## 6. Frontend (React) — pages

### 6.1 Landing / auth
- Register and Login buttons. Register: choose an account name → receive the fixed starting bag
  at `D₀`. Login: by account name only (no password for players).
- **Cookie anti-double-register:** if the register cookie is present, hide Register, show Login
  only. (Bypassable — accepted, §4.4.)

### 6.2 Main page
- **Portfolio** (USD0, ETH0, total value at current `D`).
- **External price graph** button → shows the `D` walk so far.
- **Game clock:** elapsed and remaining (broadcast).
- **Two LP interfaces**, side by side, each showing: **TVL in the pool**, **current pool price**,
  **fees earned (yours)**, and four action buttons — **Buy / Sell / Deposit / Withdraw** —
  enabled only when the player holds enough resources for that action.
  - Deposit (v3): pick `[tickLower, tickUpper]`. Deposit (curve): draw the non-negative `L_i`
    profile. Both: UI computes required USD0/ETH0 and disables if insufficient.
  - **Withdraw:** enabled only if the player has a deposit in that pool; player **selects which
    deposit** to withdraw (positions are individually tracked). Returns tokens + residual fees.
  - **Repositioning is manual:** withdraw → buy/sell to rebalance → deposit again, as separate
    actions (there is no one-click "move liquidity").
- Click a pool → **LP detail view:** pool price graph, and **level-3 orderbook data over time**
  (format defined in `DETAILS.md` — referenced, not redefined here).

### 6.3 Profile page
- Name (editable) with **name-change history** stored.
- Own balances and **action history**.
- **Balance curve** over time: token amounts and **total portfolio value (USD0) marked at the
  `D` of each timestamp**.
- The same leaderboard stat fields for self (§5).

### 6.4 Leaderboard page
- All players sorted by total value at current `D`, with the §5 fields and the two house
  benchmark rows.

### 6.5 Admin page (gated)
- Access by a specific **admin account name + password** (hardcoded long hash, §8). Admin does
  **not** play.
- **Parameters:** token display names (default USD0 / ETH0); initial price `D₀` (default current
  ETH/USD); `σ` (lognormal variance); `walk_step`; per-pool fee % (default **0.3%**); `k`
  (default **3** — see turnout note, consider 1 for small fields); `X` (player bag value);
  `gameLength`; optional per-trade size cap.
- **Controls:** Start game (distributes bags, seeds both pools at `k·X` house-owned, starts
  Oracle + clock). Autostop on `gameLength`.
- **Monitoring:** player list, each player's portfolio, each player's LP positions per pool,
  live `D`, conservation-invariant status, action feed.

---

## 7. Persistence & durability (SQLite, log-as-source-of-truth)

The requirement is **do not crash mid-game and lose state** — with minimal build effort. The
determinism already required (§4.6) *is* the crash-recovery mechanism, so durability is cheap if
the action log is made authoritative rather than a side audit.

**Engine:** **SQLite in WAL mode**, single file, single process. Chosen *over* Postgres precisely
because there is no second server to independently crash; it is ACID, embedded, and trivial to
deploy. A second process is a liability here, not robustness.

**Model: the durable action log is the source of truth; in-memory state is a replayable cache.**

- Every state-mutating event — `register`, `buy`, `sell`, `deposit`, `withdraw`, each oracle
  tick, `freeze`, `settle` — is **appended to the log and committed (`fsync`'d) BEFORE the action
  is acknowledged** to the client. Nothing is ack'd that isn't durable.
- The conservation invariant (§4.5) is asserted **before commit** with a **generous tolerance**
  (an absolute ε well above float-rounding noise — minor float drift is expected and explicitly
  fine). On a *real* breach the **single offending action is rejected and never written**; the
  running game is **never halted** — the event must not derail mid-play. Balances are held in
  `Decimal`/integers; engine internals stay float.
- **On crash/restart: replay the log from row 0** to rebuild exact state — sub-second for a
  ~1-hour game (a few thousand rows). Clients hold no authoritative state (server-authoritative,
  §9), so a refresh or WS reconnect just calls `GET /state` and rehydrates.

**Collapsed schema — 3 core tables (not 8).** Everything else is *derivable on read* from the
log; materialize a derived table only if a query is ever too slow (it won't be at this scale):

- **accounts** — `id, name, is_house, is_admin, balance_usd0, balance_eth0, fees_collected,
  taker_volume, maker_volume, register_cookie_id, created_at`. (A convenience projection of the
  log; the log is still authoritative.)
- **actions** — append-only log, the source of truth: `id, seq, account_id, type
  ('register'|'buy'|'sell'|'deposit'|'withdraw'|'oracle'|'freeze'|'settle'), payload (json),
  result (json: amounts, fee, price_before/after, positionId), created_at`.
- **oracle_ticks** — `seq, step, d_value, created_at` (the external-price series; part of state,
  so logged like any action).

*Derived on read (no table needed unless slow):* trades & volume/fee analytics, per-position
fee ledger, leaderboard, level-3 pool snapshots (`DETAILS.md`), name history → fold name changes
into `actions`. Positions live in the in-memory engine state and are reconstructed by replay.

**Suggested safety mechanisms (all cheap):**

1. **WAL + fsync-before-ack** — the core guarantee above. Acknowledged ⇒ durable.
2. **Conservation check before every commit** (§4.5) — corrupt state never reaches disk.
3. **Off-box copy every 60 s** — `rsync`/file-copy the SQLite file to a second location. Residual
   risk on total host loss is ≤60 s of trades. (~5 lines; the only guard against disk/host death,
   since it's a single box.)
4. **Replay-on-boot check** — on startup, replay the log, then assert conservation. On a
   tolerance mismatch, **warn loudly + flag the admin but still come up** (a stuck-down game is
   worse than a tiny drift at a live event). Only a corrupt/truncated *log* blocks boot.
5. **Fast restart + client auto-reconnect** — WS clients reconnect and `GET /state`; a process
   restart is invisible to players beyond a blip.

**Guiding principle: degrade gracefully, never derail.** A minor numeric drift or a single bad
action must never take the event down — reject the action, alert the admin, keep playing.
Single-process / single-box is the accepted topology for a 1-hour event; the above bounds every
failure mode that actually loses the game (process crash fully covered; host loss ≤60 s).

---

## 8. API (REST + WebSocket)

**REST (all game-state mutations server-validated; clients can never modify data directly):**
- `POST /register {name}` → bag + sets register cookie.
- `POST /login {name}` → session.
- `POST /action {type, pool, payload}` → buy/sell/deposit/withdraw; validated, applied
  atomically, conservation checked.
- `GET /state` → portfolio, pools (TVL, price, your fees), clock.
- `GET /pool/{pool}/detail` → price series + level-3 over time.
- `GET /profile/{name}` , `POST /profile/name {newName}` (records history).
- `GET /leaderboard`.
- **Admin (gated by name + password hash):** `POST /admin/params`, `POST /admin/start`,
  `GET /admin/monitor`.

**WebSocket (push):** `D` tick updates, pool price/TVL updates, game clock (elapsed/remaining),
leaderboard deltas, freeze/settle events. Recommended over polling for the live clock and graphs.

---

## 9. Security / auth

- Players: name-only login, no password (demo). Cookie hides Register on repeat visits
  (bypassable — accepted).
- **Admin:** account name kept private + a hardcoded **long random password hash** (e.g.
  bcrypt/argon2 of a generated secret). The panel checks name + password before exposing any
  admin endpoint. This is the one real privilege gate.
- Server is authoritative for all state. No client-trusted balances, prices, or positions.

---

## 10. Suggested defaults (one place)

| Param | Default | Note |
|---|---|---|
| `walk_step` | 5 s | ~720 steps/hour |
| `σ` (per step) | 0.0045 | ≈12% hourly stdev; lower for realism |
| `μ` | −σ²/2 | mean-preserving |
| `D₀` | current ETH/USD | admin-entered |
| pool fee | 0.3% | each pool, tunable |
| `k` | 3 | consider 1 for small turnout |
| `X` | admin-set (e.g. 10,000 USD0) | player bag total value |
| `gameLength` | 60 min | autostop |
| settlement freeze | 1 `walk_step` | final `D` untradeable |
| per-trade size cap | **on, ~10%** | anti-sandwich; can disable |

---

## 11. Build order (milestones)

*Quick dependency order; the dev-facing detail (Goal / tasks / acceptance / tests per stage,
split backend ⟂ frontend) is in §13.*

1. **Live swap loop (the real engine risk):** extend the existing `OrderBook` from *replay*
   (handed price/tick/L) to a **live cross-tick swap loop** that *computes* output + new price
   from order arrival — ORDERS.md §10 loop + straddle band §11 + multi-LP fee attribution §8.
   Exact-input only. This is new code the backtester never had and where correctness bugs hide;
   point the §7-invariant tests at it. Balances in `Decimal`, engine internals float.
2. **Engine wiring:** one pool; swap + add/remove + auto-collect fees; conservation invariant +
   assertions (§4.5).
3. **Persistence spine (build early, not last):** SQLite WAL, action-log-as-source-of-truth,
   fsync-before-ack, replay-on-boot self-test (§7). Everything downstream writes through it.
4. **Accounts + ledger:** registration, bags, balances, atomic actions over the log spine.
5. **Oracle + clock:** seeded `D` walk, `walk_step` ticker, game state machine, autostop +
   freeze + settlement.
6. **Two pools:** add the curve-pool front-end (arbitrary non-negative profile + non-negativity
   validation, bucketed to `tickSpacing`; shape → magnitude knob → engine funds at pool price);
   seed both at `k·X` house-owned; benchmark computation.
7. **WebSocket layer + React:** landing/auth, main page (portfolio, D graph, two LP interfaces,
   4 buttons, clock), withdraw-by-deposit selection.
8. **Profile + Leaderboard:** stat fields (§5), name history, balance/value curves, benchmark
   rows.
9. **Admin panel:** params, start, monitoring, password gate.
10. **LP detail view:** pool price graph + level-3 over time (`DETAILS.md`).
11. **Replay/audit + polish:** deterministic replay from seed + action log; conservation
    monitor in admin.

---

## 12. Open / future (not in demo scope)

- Mean-reverting `D` (exponential Ornstein–Uhlenbeck) option for a calmer, more range-bound
  scenario that is friendlier to liquidity providers.
- Stronger identity (per-identity verification) if multi-accounting becomes a problem beyond a
  friendly event.
- Multi-round / regime scenarios (trending / choppy / reversal) for a fuller AMM showcase.

---

## 13. Staged build plan (dev-facing)

Each stage lands independently with its own tests (standing rule — a stage isn't done until
`tests` are green). Tags: **[B]** backend (Python / FastAPI / SQLite), **[F]** frontend (React),
**[S]** shared. The dependency spine is **S0 → B1 → B2 → B3 → B4**; the frontend (**F5–F7**) can
start right after **S0** against the frozen contract + a mock server and join the real backend at
**B4**. **S8** is the event rehearsal. Detail is deliberately light where the steps are obvious —
the *non-obvious* decisions are spelled out; routine wiring is left to the developer.

### Stage S0 — Skeleton + frozen contracts [S]

Goal: backend and frontend can be built in parallel, so the **wire contract is pinned first**.

- [x] Repo layout: `backend/` (FastAPI app, engine package, SQLite) and `frontend/` (**vanilla-JS
      SPA** — no React/build/CDN, chosen for offline replay + zero toolchain; see APP_WORK.md S0).
- [x] **Freeze the API contract (§8) as the single source of truth** — REST request/response
      shapes and the WS message envelope (type + payload) for: `D` tick, pool price/TVL, clock,
      leaderboard delta, freeze/settle. Captured as Pydantic models in `backend/contracts.py`.
- [x] **Mock server** — minimal FastAPI app (`/health` + static) so **[F]** can start immediately.
- [x] Pin conventions from §1 once, in code: `P ≡ D`, no decimal scaling, `Decimal` balances
      (`backend/config.py`).
- Acceptance: `GET /health` is green; the React dev server renders against the mock; contract
  types shared. Tests: contract schema validates; a sample message of each WS type round-trips.

### Stage B1 — Engine: the live swap loop [B] *(the real risk — do it first, test it hardest)*

Goal: turn the existing replay `OrderBook` into a **live** engine that *computes* output + new
price from order arrival (ORDERS.md §10 loop + straddle §11 + multi-LP fee attribution §8).

- [x] `swap(zero_for_one, amountIn) -> (amountOut, in_consumed, fee, newPrice, fee_by_position)`
      — **exact-input**, cross-tick loop, fee skimmed off input (pro-rata to in-range positions),
      `L` never mutated. Halt-on-empty-region (§11). *(Band-native: liquidity stored per
      tickSpacing band, so the loop steps band-by-band — see APP_WORK.md B1.)*
- [x] `add_position` — uniform `L` over a range (v3) **and** an arbitrary non-negative per-tick
      profile (curve) via `quote_range`/`quote_curve`; validate `L_i ≥ 0`; compute the USD0/ETH0
      the profile needs at the current price. `remove_position`, `position_value_usd0`, `price`,
      `book()` level-3 snapshot.
- [x] Inventory/engine internals float; conservation asserted at the ledger (Game) layer with the
      generous tolerance (§7) — engine returns exact per-swap deltas for that check.
- Acceptance: swap/add/remove against a seeded pool conserve each token (within ε); v2 full-range
  special case sanity-checks. Tests: the ORDERS §7 invariants pointed at the **live** loop
  (telescoping, geo-mean fill, constant spread, `L`-never-mutated, fee monotonicity, round-trip
  path-independence at γ=0), plus curve funding + non-negativity rejection.

### Stage B2 — Persistence spine [B] *(build early — everything writes through it)*

Goal: crash-safety per §7, before there's much state to lose.

- [x] SQLite WAL; the 3 tables (`accounts`, `actions`, `oracle_ticks`) + a small `meta` kv table.
- [x] **Append-and-fsync the action to the log before ack** (`synchronous=FULL`, autocommit);
      apply to in-memory state only after. `read_log()` + `replay()` rebuild exact state.
- [x] **Replay-on-boot** rebuilds exact state (Store side; wired into Game at B3); conservation
      tripwire **rejects a bad action but never halts** lands at B3; off-box `backup_to()` hook (S8).
- Acceptance: `kill -9` mid-session → restart → state identical; a forced invariant breach
  rejects one action and the game keeps serving. Tests: replay determinism, ack-only-after-fsync,
  reject-not-halt on a synthetic breach.

### Stage B3 — Game core: accounts, pools, oracle, clock, settlement [B]

Goal: a full game can run headless, deterministically, with no API or UI yet.

- [x] `Account` (atomic debit/credit, never negative), `Pool` (wraps engine + fee auto-collect to
      owner balances + maker-volume attribution), `Oracle` (seeded lognormal walk, §4.3), `Game`
      state machine `LOBBY→RUNNING→FREEZE→SETTLED` (§4.2) with clock + autostop + settlement freeze.
- [x] Register → balanced bag at `D₀`; seed both pools at `k·X` house-owned (funded with exactly
      the quoted split so it conserves); benchmark row (§2); per-trade size cap (default on, §4.4).
      Validate→append→apply so clean rejections leave no log entry; conservation is a never-halt
      detector (§7).
- Acceptance: a scripted game (register N, random actions, advance oracle) runs start→settle and
  is **bit-reproducible from the seed + action log**. Tests: lifecycle transitions, oracle
  reproducibility, benchmark formula, auto-collect correctness, size-cap enforcement.

### Stage B4 — API surface: REST + WebSocket [B]

Goal: the game is fully playable over the wire (no UI), matching the S0 contract exactly.

- [x] REST endpoints (§8), all **server-authoritative** — never trust client balances/prices;
      every mutation runs through the Game (validate → log → apply → conserve). Admin endpoints
      behind the name + password gate (`hmac.compare_digest`, §9).
- [x] WS push for the live streams (§8). Broadcast on each oracle tick and each accepted action;
      background ticker drives the seeded oracle and swallows its own errors (never kills the event).
- Acceptance: a headless script plays a whole game through HTTP/WS only. Tests: per-endpoint
  validation + rejection paths, admin gate, conservation holds after each action endpoint, WS
  broadcasts the expected message on tick/action.

### Stage F5 — Frontend: core play [F] *(starts after S0 on the mock; wires to B4 when ready)*

Goal: a player can play a complete game in the browser.

- [x] Landing/auth (register → bag, login by name, cookie hides Register on repeat, §6.1).
- [x] Main page (§6.2): portfolio, external-`D` graph, game clock, and the **two LP interfaces**
      side by side with **Buy / Sell / Deposit / Withdraw**, disabled off-RUNNING. Withdraw lists
      individual positions.
- [x] Deposit UIs: v3 = price-range + budget; curve = **draw a non-negative profile bucketed to
      `tickSpacing`** + budget → engine funding math gates on balances (B1).
- [x] Live updates over WS; on reconnect, `GET /state` rehydrates (client holds no truth).
- Acceptance: full game playable in-browser against B4. Tests: action buttons enable/disable on
  balances, withdraw-by-deposit selection, reconnect rehydrate, curve non-negativity in the UI.

### Stage F6 — Frontend: profile + leaderboard + LP detail [F]

Goal: the read/analytics surface.

- [x] Profile (§6.3): editable name + history, action history, value-over-time sparkline (per-tick
      snapshots marked at `D`), the §5 stat fields for self.
- [x] Leaderboard (§6.4): all players sorted by total value at current `D`, §5 fields, the two
      house benchmark rows (non-winning).
- [x] LP detail (§6.2): pool price graph + **live level-3 order book** (inline-SVG stacked per-
      position bars, spot line). Live current book rather than a historical slider — see APP_WORK F6.
- Acceptance: stats match the backend; benchmark rows present; level-3 view renders over time.

### Stage F7 — Admin panel + monitoring [F] (+ small [B] as needed)

Goal: run the event.

- [ ] Params form (§6.5 / §10 defaults), Start control (distributes bags, seeds pools, starts
      oracle + clock), autostop.
- [ ] Monitoring: player list + portfolios + positions, live `D`, **conservation-invariant
      status**, action feed. Behind the password gate (§9).
- Acceptance: admin can configure, start, and monitor a live game end to end.

### Stage S8 — Event hardening + dry run [S]

Goal: prove it won't lose the game or derail on the day.

- [ ] Off-box SQLite copy every 60 s (§7); deterministic replay/audit from seed + log.
- [ ] **Load + chaos rehearsal:** simulate the expected player count, then **`kill -9` the process
      mid-game** → assert zero data loss on restart; reconnect-storm the WS; inject a forced
      invariant violation → assert the game **keeps running** and the admin is alerted (never
      derails, §7).
- Acceptance: a rehearsal game under realistic load survives a mid-game kill with no data loss and
  no derail; settlement + leaderboard freeze correctly.

> **Note.** No batching, no exact-output, no Postgres, no Go — all out of scope by the locked
> decisions (§0). Keep stages thin; let the developer fill in obvious wiring.