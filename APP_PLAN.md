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

## 4. Backend (Go) — components

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
  itself exposed to `D` risk. Optional guard: a per-trade max size as a % of pool reserves
  (admin param, default off).
- *First-mover edge* on closing an obvious pool-vs-`D` gap exists but is not risk-free (profit
  depends on the final `D`). Acceptable.
- *Multi-accounting:* the register cookie is bypassable (incognito / cleared cookies). Because
  scoring rewards only final holdings — never the number of accounts, volume, or depth — an
  extra account can only help a main account by transferring value to it through a real trade,
  i.e. by actually winning an exchange against someone. The conservation invariant (§4.5) bounds
  the damage. Backstop: every action is logged; flag account pairs that repeatedly trade with
  each other.

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

## 7. Data model (PostgreSQL)

Relational chosen for transactional integrity of the ledger (atomic action application,
conservation auditing). Core tables:

- **games** — `id, status, params (jsonb: token names, D0, σ, walk_step, fees, k, X,
  gameLength, size_cap), oracle_seed, started_at, ends_at, settled_at`.
- **accounts** — `id, game_id, name, is_house, is_admin, balance_usd0, balance_eth0,
  fees_collected, taker_volume, maker_volume, register_cookie_id, created_at`.
- **name_history** — `id, account_id, old_name, new_name, changed_at`.
- **positions** — `id, game_id, account_id, pool ('v3'|'curve'), kind ('range'|'curve'),
  params (jsonb: range or per-tick profile), liquidity_snapshot, status ('open'|'withdrawn'),
  created_at, withdrawn_at`.
- **actions** — append-only audit log: `id, game_id, account_id, type ('buy'|'sell'|'deposit'
  |'withdraw'), payload (jsonb), result (jsonb: amounts, fee, price_before/after), created_at`.
- **trades** — `id, game_id, pool, taker_account_id, side, amount_in, amount_out, fee,
  price_after, created_at` (for volume/fee analytics).
- **fees_ledger** — `id, game_id, pool, position_id, account_id, token, amount, trade_id,
  created_at` (auto-collect events; supports conservation audit).
- **d_ticks** — `id, game_id, step, d_value, created_at` (the external-price series).
- **pool_snapshots** — `id, game_id, pool, step, price, reserves, level3 (jsonb), created_at`
  (level-3 data over time, format per `DETAILS.md`).

A lightweight in-memory mirror of live game state is fine for speed, with Postgres as the
durable source of truth and audit/replay store.

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
| per-trade size cap | off | optional anti-sandwich |

---

## 11. Build order (milestones)

1. **Engine wiring:** integrate `OrderBook` (per `ORDERS.md`); one pool; swap + add/remove +
   auto-collect fees; conservation invariant + assertions (§4.5).
2. **Accounts + ledger:** registration, bags, balances, atomic actions, Postgres schema.
3. **Oracle + clock:** seeded `D` walk, `walk_step` ticker, game state machine, autostop +
   freeze + settlement.
4. **Two pools:** add the curve-pool front-end (arbitrary non-negative profile + non-negativity
   validation); seed both at `k·X` house-owned; benchmark computation.
5. **WebSocket layer + React:** landing/auth, main page (portfolio, D graph, two LP interfaces,
   4 buttons, clock), withdraw-by-deposit selection.
6. **Profile + Leaderboard:** stat fields (§5), name history, balance/value curves, benchmark
   rows.
7. **Admin panel:** params, start, monitoring, password gate.
8. **LP detail view:** pool price graph + level-3 over time (`DETAILS.md`).
9. **Replay/audit + polish:** deterministic replay from seed + action log; conservation
   monitor in admin.

---

## 12. Open / future (not in demo scope)

- Optional batching / uniform-clearing mode (all actions in a `walk_step` clear together at one
  price) — would remove the first-mover edge and make sandwiching impossible, if the real-time
  version feels too reflex-driven at the event.
- Mean-reverting `D` (exponential Ornstein–Uhlenbeck) option for a calmer, more range-bound
  scenario that is friendlier to liquidity providers.
- Stronger identity (per-identity verification) if multi-accounting becomes a problem beyond a
  friendly event.
- Multi-round / regime scenarios (trending / choppy / reversal) for a fuller AMM showcase.