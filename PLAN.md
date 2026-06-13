# Uniswap v3 Pool — Minimal RPC-Only Plan

Goal: a minimal, self-contained pipeline that uses **only the keyless public RPC**
(no wallet, no API key, no account) to download one Uniswap v3 pool's activity for a
date range, process it, and draw the required curves.

Reference pool: **ETH/USDC 0.3%** `0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8`
(token0 = USDC 6dec, token1 = WETH 18dec). Example range: 1 day (Jun 12 2026).

The Graph version and the original handoff doc are archived in `old/` and are out of scope.

## What "the curves" means (from the original plan)

1. **TVL over time** — pool value over the range.
2. **Buys / sells** — swaps with direction + price.
3. **Liquidity added / removed** — mints/burns with their tick ranges.
4. **Liquidity distribution over price ("level-3")** — full liquidity-vs-tick curve,
   reconstructed by replaying mint/burn in `(block, logIndex)` order.

---

## Stage 1 — Get the data (RPC download)

Fix and harden the existing `uniswap_v3_pool_download_rpc.py` so it actually writes output.

- [ ] **Fix the blocking bug** at line ~195: `{...}.keys()` on a set → use `sorted({...})`
      (the CSV header construction). This is what currently crashes every run.
- [ ] Keep the keyless flow: connect → token metadata → resolve date range to blocks
      (binary search by timestamp) → chunked `eth_getLogs` with auto-shrink on range errors.
- [ ] Output **raw, decimal-adjusted** events to CSV: `swaps.csv`, `mints.csv`, `burns.csv`,
      `collects.csv`, sorted by `(block, logIndex)`.
- [ ] Keep fields needed downstream: swaps → `amount0/1, sqrtPriceX96, tick, liquidity, direction`;
      mints/burns → `tickLower, tickUpper, amount (liquidity), amount0/1`; collects → ranges + amounts.
- [ ] Sanity print: per-event row counts (expected for Jun 12: Swap 770, Mint 11, Burn 28, Collect 27).

Acceptance: `python uniswap_v3_pool_download_rpc.py --pool <addr> --start 2026-06-12 --end 2026-06-13`
writes four non-empty CSVs and exits 0.

## Stage 2 — Process the data (derive series, pure local compute)

New script `process.py` — reads the Stage 1 CSVs, writes derived CSVs. No network.

- [x] **Price from tick/sqrtPriceX96**: `price = (sqrtPriceX96**2 / 2**192)` then adjust by
      `10**(d0-d1)` to get token1-per-token0 in human units (USDC per WETH → invert for WETH price).
- [x] **Buys vs sells**: from swap `amount0` sign (`amount0>0` = pool received USDC = trader sold
      USDC for WETH). Emit `swaps_classified.csv` with side + price + size.
- [x] **TVL over time (RPC-only, no price API):** reconstruct pool token balances by replaying
      balance-affecting events from a starting balance:
      start from `balanceOf(pool)` at `start_block` for token0/token1 (one RPC call each — still
      keyless, current-state read), then apply `+Mint`, `±Swap`, `−Collect`, `+Flash.paid` per event
      in order. Express TVL in token units and in WETH-terms using the swap-derived price
      (USD optional/out-of-scope for the minimal version). Emit `tvl_series.csv`.
      - Fallback if start-block `balanceOf` isn't served by the public node: start at 0 and report
        TVL as *net flow* (relative), noting the offset. Decide at implementation time.
- [x] **Liquidity-by-tick over time ("level 3"):** replay mints/burns in `(block, logIndex)` order:
      `net[tickLower] += amount; net[tickUpper] -= amount` (reverse sign on burns). Cumulative sum
      gives active liquidity vs tick. Snapshot the curve at the start and end of the range (and the
      active `tick` from the latest swap). Emit `liquidity_distribution.csv`.

- [x] **`test_stage2.py`** (same pattern as Stage 1): offline unit tests for the pure math
      (price-from-`sqrtPriceX96`, buy/sell classification, tick-replay cumulative sum on a tiny
      hand-built fixture with a known answer) + output-validation tests on the derived CSVs
      (`swaps_classified.csv`, `tvl_series.csv`, `liquidity_distribution.csv`): required columns,
      monotonic time ordering, `side ∈ {buy,sell}`, tick-sorted integer deltas (a *net-change* curve,
      so values may be negative — it is NOT asserted non-negative).

Acceptance: derived CSVs produced from Stage 1 output with no network calls (except the two optional
`balanceOf` reads for the TVL baseline); `test_stage2.py` green.

## Stage 3 — Display the curves

New script `plot.py` — reads Stage 2 CSVs, renders PNGs with matplotlib.

- [x] **TVL(t)** line chart from `tvl_series.csv` (`out/tvl.png`).
- [x] **Price(t)** + **buy/sell flow**: price line over a stem plot of signed swap size — buys up
      (green), sells down (red) — from `swaps_classified.csv` (`out/price_flow.png`).
- [x] **Liquidity distribution**: step/filled chart of cumulative liquidity Δ vs tick, active tick
      marked, from `liquidity_distribution.csv` (`out/liquidity_distribution.png`).
- [x] Save to `out/*.png`; one figure per curve. matplotlib only, `Agg` backend (headless).
- [x] **`test_stage3.py`** (same pattern): `Agg`-backend smoke test driving each plotter against tiny
      fixture rows; asserts the PNG exists, is non-empty, and has a valid PNG header. Skips if
      `matplotlib` isn't installed.

Acceptance: running `plot.py` produces the PNGs from Stage 2 CSVs; `test_stage3.py` green.

## Stage 4 — Level-3 order book over time (interactive)

Goal: visualize the **liquidity book evolving over time** with a **time slider**, anchored on the
pool's *actual* starting liquidity (not 0). Conceptually a 3D object (level × order × time); we
collapse time to a slider so each frame is a 2D slice. Target slice: **Y = price levels (ticks)**,
**X = active positions, oldest→newest**, **colour = that position's liquidity at that level**;
**slider = time**. Columns appear on mint and disappear on burn as time advances.

Split into three sub-stages so each lands independently with its own tests.

### Why a starting snapshot is needed (context)

Our in-window Mint/Burn events only describe the *change* in liquidity over the range — they start
from 0. The pool already holds liquidity at `start_date`. We **cannot** recover individual *old*
positions (L3) without parsing full history, BUT we **can** read the absolute **aggregate (L2)**
liquidity-per-tick directly from pool contract state at the start block — keyless, no event parsing:
- `slot0()` → current tick; `liquidity()` → active L anchor; `tickBitmap(word)` → which ticks are
  initialized; `ticks(t).liquidityNet` → the absolute `±L` boundary delta at each initialized tick.
- Cumulating `liquidityNet` outward from the current tick reconstructs absolute active L at every
  band. **Verified:** for this pool that reconstruction equals on-chain `liquidity()` exactly.
- **Scale is small:** ~688 initialized ticks here; via **Multicall3** (`0xcA11…CA11`, works at
  historical blocks) the whole snapshot is **~5 RPC calls**, not ~800. Comfortably under any limit.
- This baseline is **aggregate L2 only** — it represents "old orders" as a per-tick depth layer with
  no individual identity. In-window events (L3) layer on top.

### App vs library — decision (for 4.3)

**No custom server app.** Use **Plotly `animation_frame`** → a **single standalone HTML** file:
built-in time slider + play button, client-side, shareable. Precompute all slices once (cheap here).
Not building an app because: matplotlib `Slider` needs a live process (bad for sharing); a bespoke
HTML/JS app is only worth it for **server-side on-demand** slicing over data too big to precompute
(not our case); Bokeh standalone HTML is a fine alt but Plotly is least-effort. `FuncAnimation` →
GIF/MP4 is an easy non-interactive teaser. Keep X-axis = **union of all positions/ticks seen**
(stable axis); mask inactive per frame so the animation stays clean.

---

### Stage 4.1 — Initial (start-of-range) L2 liquidity snapshot + API usage logger

- [x] **`usage.py`** — a small Web3 request counter (provider middleware or a wrapped provider)
      that tallies RPC calls **by method** (`eth_call`, `eth_getLogs`, `eth_getBlockByNumber`, …) for
      the current run, prints a summary, and appends a cumulative tally to **`usage.csv`** (keep all
      info in CSV; one row per run with per-method counts + total). Importable so Stages 1, 2, 4.1 can
      all use it. `usage.csv` is a generated artifact, already covered by the `*.csv` gitignore rule.
- [x] **`tick_snapshot.py`** — read the absolute aggregate (L2) liquidity-per-tick at the pre-range
      block via Multicall3 (`slot0` + `liquidity` + `tickBitmap` walk + batched `ticks()`), cumulate
      `liquidityNet` into absolute active L per tick band, and **self-check** the active-tick total
      against on-chain `liquidity()`. Output **`initial_liquidity.csv`** — same spirit/shape as Stage 2's
      `liquidity_distribution.csv` (columns: `tick`, plus absolute `liquidity_net` and
      `cumulative_liquidity`), so Stage 3 and 4 can consume it the same way.
      - **Optional, enabled by default:** flag `--no-initial-liquidity` (or `--initial-liquidity/--no-...`)
        to skip the on-chain reads; default ON. When off, downstream falls back to the 0-baseline
        (relative) behaviour. Wire the call counter from `usage.py` through these reads.
- [x] Tests: `test_stage4.py` covers the cumulation math (fixture `liquidityNet` map → known absolute
      curve; active-tick total reconciles) and `initial_liquidity.csv` output validation.

### Stage 4.2 — Use the initial liquidity in Stage 3 plots

- [x] Extend `plot.py` so the **liquidity distribution** plot overlays the absolute baseline from
      `initial_liquidity.csv` with the in-window net change → an **absolute** standing depth curve
      (start vs end), instead of only the net-change delta. `build_absolute_curves()` combines the two
      liquidityNet maps; cumulative L is float-cast (real pools exceed int64) and the x-axis is
      windowed around the active price (huge far-OTM positions otherwise dwarf the chart; full curve
      stays in the CSV).
- [ ] _(optional, deferred)_ use the baseline to render extra TVL/price context — not needed for the
      curve; skip for now.
- [x] **Optional, enabled by default:** `--no-initial-liquidity` flag on `plot.py`; when the baseline
      CSV is present it is used by default, otherwise plots degrade gracefully to the Stage-3
      net-change behaviour (no breakage).
- [x] Tests (in `test_stage4.py`): `build_absolute_curves` = baseline + net change on a fixture;
      absolute plot renders; baseline-absent path still covered by Stage-3 tests.

### Stage 4.3 — Interactive time-axis figure (shareable)

- [x] **`orderbook.py`** — replay `mints`/`burns`/`collects` in `(block, logIndex)` order, maintaining a
      **position table** keyed by `(owner, tickLower, tickUpper)` (NFT `tokenId` decoding is a later
      refinement). Bucket the range into time slices; per slice emit each **active** position
      `{mint_time(age), tickLower, tickUpper, current_L}`. Seed the per-tick aggregate from
      `initial_liquidity.csv` when available so each slice shows **absolute** depth (old baseline +
      live positions); without it, slices are in-window-only and labelled as such. Output
      `orderbook_slices.csv`/`.json`.
- [x] **`plot_orderbook.py`** — Plotly `animation_frame` figure: per frame a heatmap `Y = tick levels`,
      `X = active positions oldest→newest`, `colour = L`, `slider = time`. Export standalone
      `out/orderbook.html`. Plus a simpler companion **L2 depth-over-time** animation
      (`X = tick`, `Y = absolute active L`, `animation_frame = time`) → `out/depth_over_time.html`.
- [x] Add `plotly` to `requirements.txt`.
- [x] Tests: position-replay membership (`mint⇒active`, `burn⇒absent`, per-slice `L`), baseline
      seeding, and a standalone-HTML smoke test (skip if `plotly` absent).
- [x] **Scale review (follow-up):** depth chart Y defaults to **log** when data is all-positive
      (absolute view); falls back to **linear including negatives** for the relative/net-change view
      (log can't show ≤0, and the old `[0, max]` range was clipping the negative bars). `--linear`
      overrides. Heatmap colour is `log10(L)`.

### Stage 4.4 — Viewing helper (`serve.py`)

Goal: serve the generated `out/*.html` for viewing. **Default is local-only** (binds `127.0.0.1`);
remote viewing is via an **SSH / Cursor port-forward** (authenticated). An **opt-in `--tunnel`**
exposes a public link for a deliberate quick share — never on by default.

- [x] **`serve.py`** (default = local) — serve `out/` on `127.0.0.1:<port>`; list every `.html`
      with its localhost URL; highlight one page with a positional arg. Loopback only.
- [x] **`--tunnel` (opt-in)** — also start a cloudflared **quick tunnel** and print the PUBLIC,
      no-auth, ephemeral URL (cloudflared auto-discovered). Clearly flagged as public; used only when
      asked for. Works on Vast, where every published port is occupied by Vast's own services so the
      direct/forward paths aren't viable.
- [x] **Robust teardown (tunnel mode)** — cleaned up on **every** exit path (Ctrl+C / `kill` /
      SIGTERM / normal exit / cloudflared dying): cloudflared runs in its own session, killed by
      process group via `signal` handlers + `atexit` — no orphaned tunnel left running.
- [x] Tests (`test_serve.py`): URL listing + link formatting (pure), a real bind-and-GET asserting
      HTTP 200 / 404 on a `127.0.0.1` socket, tunnel-URL parsing, and process-group teardown.

Acceptance: 4.1 writes `initial_liquidity.csv` (verified vs `liquidity()`) and `usage.csv`;
4.2 plots an absolute depth curve using it (degrading gracefully when absent); 4.3 writes a shareable
`out/orderbook.html` with a working time slider using all prepared data; 4.4 serves the HTML for
viewing; all stage tests green.

### Stage 4.5 — Redesign the order-book figure + scientific axes

Problem: `out/orderbook.html` showed **no data** — the original per-position **heatmap** loaded on an
empty first frame (positions mint partway through the day) with an unpinned colour axis, and was hard
to read regardless. Units were missing ("10e15" with no labels). Redesigned into a clear stacked-bar
order book with real units.

- [x] **Stacked-bar L3 order book** (`build_orderbook_figure`) — at each **price** the bar height is
      the total active liquidity, **decomposed into one coloured segment per LP position (order)**:
      `go.Bar` per position, `barmode="stack"`. So "price 1,883 USDC/WETH = 7 stacked orders" reads
      directly (verified on real data). Replaces the heatmap.
- [x] **Real units & axis labels (NeurIPS-style)** — X = **Price (USDC per WETH)** (tick→price via
      `tick_to_price`, axis labelled with $ prices); Y = **Active liquidity, L (Uniswap v3 units,
      ∝ √(USDC·WETH))** with `exponentformat="power"`; legend = LP position `[tickLower, tickUpper]`;
      a caption annotation explains the figure; hover shows order, price, and L.
- [x] **Open on first non-empty frame** — the view opens on the first populated slice, not the blank
      slice 0 (positions mint partway through the day).
- [x] **`serve.py` key links** — numbered **Key links (ctrl+click)** block before the full listing.

#### Follow-up — verified by actually rendering the HTML (closed the blind loop)

Set up `tools.sh` + `render_figs.py` (open-source headless Chromium via Playwright) so the figures can
be screenshotted to PNG and **looked at**. Rendering exposed real problems the data checks missed; all
fixed and re-verified against renders:
- [x] **Flicker killed → fixed global Y.** Per-frame Y autoscale swung the axis ~500× per frame
      (1e13 ↔ 5e15); a "largest-so-far" running max still stepped up ~5× mid-play. Final: **one fixed
      Y for all frames** on both depth and order book (a stacked bar can't be log-scaled, so the order
      book is fixed *linear*). Zero rescales → no flicker.
- [x] **Order book made legible.** Wide far-from-price positions squished the action into a sliver →
      **window the X-axis to the active price band** (`active_tick_window`, ±3500 ticks) with
      window-aware price labels; centred the title so the play button no longer hides it.
- [x] **Two versions per level (4 files, with-initial = default/canonical, without-initial = suffix):**
      - **L2 depth:** with-initial = **absolute** (baseline + in-window), without-initial = **relative**
        (net change, has negatives → linear). Titles carry the basis.
      - **L3 order book:** with-initial **stacks the absolute baseline UNDER the day's orders**
        (`orderbook.py` → `orderbook_baseline.csv`; `build_orderbook_figure(baseline_curve=...)` adds a
        grey base layer). The baseline (~1.5e18) dwarfs the day's orders (~5e15, ~300×), so in the
        with-initial view the orders are a thin sliver on the standing book — the *without-initial*
        view is where the per-order detail is readable. (Confirmed by render.)
- [x] **`--log` (off by default → linear everywhere).** Opt-in log Y for the **absolute depth only**
      (L3 stacked bars can't be log). On `plot_orderbook.py`, propagated by `run_all.py --log`, and
      `serve.py --log` (rebuilds figures via `run_all.py --figures-only --log`, no network, then serves).
- [x] **serve links de-duplicated.** One "Links (ctrl+click)" block of the 4 key files; an "Other
      pages" list only appears for non-key HTML.

Acceptance: 4 figures (2 per level); with-initial canonical, without-initial suffixed; no flicker
(fixed Y), windowed to price, units on axes, `--log` off by default; all confirmed by rendered PNGs;
tests green.

## Stage 5 — One-command pipeline runner (`run_all.py`)

Goal: a single entry point that runs every stage in dependency order and produces **all** artifacts
(CSVs, PNGs, and the **4 HTML** files), so there's no need to remember the per-stage sequence.

- [x] **`run_all.py`** — orchestrates the stages via subprocess (the per-stage scripts stay the source
      of truth). Shared `--pool/--start/--end/--slices`; `--no-baseline-rpc` to skip absolute reads.
      Order: download → tick_snapshot (4.1) → process (2) → plot (3+4.2) → orderbook+figures (4.3).
      Runs Stage 4.3 **twice** — without then with initial liquidity — and copies the outputs to the
      four `out/{depth_over_time,orderbook}__{with,without}_initial.html`; the with-initial run goes
      last so the canonical `out/*.html` stay the absolute versions. Prints a produced-files summary.
- [x] **`test_stage5.py`** — pure tests on `plan_steps()`: all stages present & ordered, both
      order-book variants emitted, the four HTML copy targets, dates/pool threaded through,
      `--no-baseline-rpc` drops `--baseline-rpc`. (No network/subprocess in tests.)

Acceptance: `python run_all.py` produces all CSVs, the three PNGs, and the four HTML files (verified
end-to-end); `test_stage5.py` green.

---

## Stage 6 — A real virtual order book (the `Orderbook` engine)

**Why.** Everything before Stage 6 renders a *liquidity-range* view: a Mint adds a constant `L`
across its whole `[tickLower, tickUpper]`, and we cumulate `liquidityNet` into a static depth
curve. That is **not** how the AMM behaves as a book. In reality (see `ORDERS.md`):

- A concentrated-liquidity position is a dense grid of **paired limit orders**. Within a range the
  per-price order sizes are **uneven** — uniform in √price, not in price — so size grows as price
  falls (`q0 = L·(1/√P_i − 1/√P_{i+1})` per tick band).
- Current spot is just a **pointer** into a static book. Levels above spot are live **asks** (sell
  WETH), levels below are live **bids** (buy WETH). **A swap slides the pointer and each crossed
  level flips side** — a filled ask instantly becomes a resting bid at the same price and size,
  funded by the taker's own input. No level is re-priced or re-sized.
- The pool fee (0.30% here) is **skimmed off the taker's input into a separate per-position
  counter — never reinvested** — so order sizes stay constant as price moves; only the side flips.

Stage 6 builds an `Orderbook` class that models this correctly and makes it the single source of
truth for the L2/L3 views, then derives **daily volume, daily fees, and daily APR** as a
cross-checkable by-product.

### Identity / data realities (decided)

- Pool Mint/Burn events carry `owner = NonfungiblePositionManager` for every NFT-routed LP, so the
  *pool* keys positions by `(owner, tickLower, tickUpper)` and cannot tell two LPs apart — pool
  events alone are **not** true L3.
- **True per-position identity is recovered via `tokenId`** (Stage 6.0): the NFPM emits
  `IncreaseLiquidity`/`DecreaseLiquidity` with an indexed `tokenId` in the **same transaction** as
  each pool Mint/Burn. Joining by transaction recovers the tokenId, which **links every Add to its
  later Removes**. tokenId is the stable position id (better than wallet — the NFT can be transferred).
- "Individual order" granularity = **per-tokenId position × per-initialized-tick band**. There is no
  atomic order finer than `(position, tick band)`; that is as deep as on-chain reality goes.
- The pre-existing baseline (`initial_liquidity.csv`) is an **aggregate** `liquidityNet` profile and
  **cannot be attributed per-LP**. It is modelled as **one synthetic "pre-existing" position** whose
  aggregate L-profile is still expanded into per-tick-band virtual orders and flipped by spot — it
  just isn't colored per-LP. In-window mints get full per-tokenId attribution. This maps onto the
  existing split: **without-initial = fully-attributed true L3; with-initial = attributed in-window
  orders over the aggregate backdrop.**

### Stage 6.0 — `tokenId` linkage + pool metadata (data)

New script `link_positions.py` (keyless RPC).

- [x] **Pool metadata → `pool_metadata.csv`.** Read the pool contract once (keyless): `fee()`
      (hundredths of a bip → `gamma = fee/1e6`, e.g. `3000`→`0.003`), `tickSpacing()`, `token0()`,
      `token1()`, and each token's `decimals()`/`symbol()`. Store one row:
      `pool, token0, token1, symbol0, symbol1, decimals0, decimals1, fee, gamma, tickSpacing`. The
      engine takes `gamma` from here instead of a hardcoded `0.003`, so it works for any pool/fee tier.
- [x] For each mint/burn transaction (39 in the sample day), fetch the **transaction receipt** and
      find the NFPM `IncreaseLiquidity`/`DecreaseLiquidity` log in it; read its `tokenId`. The log's
      `liquidity` field must equal the pool event's `amount` (cross-check the join).
- [x] Write `tokenId` onto each row → `mints_linked.csv`, `burns_linked.csv` (originals untouched).
      Fallback: a direct-to-pool mint (owner ≠ NFPM) has no tokenId → identity = `owner` address.
- [x] Emit `positions.csv`: one row per tokenId — `tokenId, tickLower, tickUpper, first_mint_ts,
      net_L_in_window, add_count, remove_count` — the Add↔Remove linkage made explicit.
- [x] Tests: receipt-parse join is exercised by the acceptance run; unit-test the pure join logic
      (match by tx + `amount==liquidity`, no-tokenId fallback) against a hand-built fixture.

> **Acceptance run (Jun 12 2026):** `pool_metadata.csv` = USDC/WETH, `fee=3000` → `gamma=0.003`,
> `tickSpacing=60`, decimals `(6,18)`; **11/11 mints and 16/16 liquidity-removing burns linked to a
> tokenId** (the other 12 burns are 0-amount fee pokes — no `DecreaseLiquidity`, correctly skipped);
> `positions.csv` = **23 distinct tokenId positions** (19 still open + 4 opened-and-closed intraday).
> 63 keyless RPC calls. `test_stage6.py` green (22 tests); full suite 94 green.

### Stage 6.1 — the `Orderbook` engine (`orderbook_engine.py`, pure, no network)

A stateful event-replay book. Holds positions per tokenId, expands each into per-initialized-tick
virtual orders, walks the day's swaps in order, and accrues fees separately.

- [ ] **Math layer** (pure functions, the contract in `ORDERS.md`): `L` from deposited amounts;
      position inventory `(x, y)` at a given spot; the per-tick-band ladder
      (`q0_i`, `q1_i`, geometric-mean fill price `√(P_i·P_{i+1})`); single-tick swap step
      (price move + amounts) with the fee skimmed off input first.
- [ ] **`Orderbook` state**: current √price / tick / active `L`; tick-indexed `liquidityNet` map;
      positions `{tokenId → (L, tickLower, tickUpper)}`; a per-tokenId **fee counter** (`tokensOwed0/1`,
      never folded back into `L`); global fee growth accumulators.
- [ ] **`apply_mint` / `apply_burn`**: add/remove a position's `L`, update the tick map. **`apply_swap`**:
      move spot, cross initialized ticks (active `L += liquidityNet` up / `−=` down), and **flip each
      crossed level between bid and ask** (same price, same size). Skim the swap's fee
      (`gross_in × 0.003`) into the in-range positions' counters pro-rata by `L` (per-unit-liquidity
      growth). We have each swap's tick and active `L` in `swaps.csv`, so attribution is data-driven;
      exact sub-segmentation of a single swap that crosses several initialized ticks is approximated
      by its end-state tick (documented; the vast majority of swaps cross none).
- [ ] **Derived views**: `book_at()` → the side-labeled (bid/ask) per-tick-band ladder at current
      spot, as an aggregate (L2) and per-tokenId (L3); `daily_stats()` → **volume** (USDC & WETH),
      **total fees** (both tokens), and **two APRs**: `apr_total_tvl` (24h fees ÷ total pool TVL × 365 —
      the headline number directly comparable to Uniswap's pool page) and `apr_active_tvl`
      (÷ value of in-range liquidity only — what active LPs actually earn). Both stored; the total-TVL
      one is the external cross-check.
- [ ] **Invariant tests** (`test_stage6.py`): telescoping (band sums equal the closed form);
      virtual-reserve product holds within a constant-`L` region; **path independence** with zero fee
      (any spot round-trip returns inventory exactly); geometric-mean fill per band; **constant
      multiplicative spread** = fee per side, independent of price; a swap never mutates any
      position's `L`; fee monotonicity + total fee = Σ(`gross_in × 0.003`); token conservation per step.
- [ ] **APR cross-check**: assert the engine's daily volume/fees/APR are in the right ballpark vs the
      pool's published figures (sanity bounds, not exact parity) — this is the headline external check.

### Stage 6.2 — wire the engine through the pipeline

Each integration point is its own checkpoint. The new virtual figures are added **alongside** the
existing range-view figures (both sets served), so the two can be compared.

- [ ] **6.2.0 Metadata threading (pipeline becomes pool-agnostic).** Make `pool_metadata.csv` the
      single source of truth: `process.py`, `plot.py`, `orderbook.py`, `tick_snapshot.py` read
      `decimals0/1`, `symbol0/1`, `gamma`, `tickSpacing` from it instead of hardcoding USDC/WETH /
      `--d0=6/--d1=18` (flags remain as a fallback when the CSV is absent, so each script still runs
      standalone). `pool_metadata.csv` is produced early — right after Stage 1 download, before
      Stage 2 — so every downstream stage can consume it. Re-run the **Stage 1–5 tests** after the
      refactor to confirm no regression.
- [ ] **6.2.1 `build_book.py`** — replay the linked data through `Orderbook` to emit the
      book-over-time CSVs: `book_l2.csv` (aggregate bid/ask depth per tick band per slice) and
      `book_l3.csv` (per-tokenId order per tick band per slice, side-labeled), plus
      `daily_metrics.csv` (volume, fees, APR). Honours with/without-initial (synthetic baseline
      position on/off).
- [ ] **6.2.2 `plot_book.py`** — render the virtual book: x = price (USDC per WETH), bars split into
      **bid (buy WETH) vs ask (sell WETH)** by spot, L3 stacked per tokenId, L2 aggregated; the
      existing fixed-Y / no-flicker, axis-label, and `--log` conventions carry over. Outputs
      `out/orderbook_virtual.html` and `out/orderbook_virtual__without_initial.html` (+ the L2
      `depth_virtual` pair). A small `daily_metrics` panel/printout shows volume/fees/APR.
- [ ] **6.2.3 `run_all.py`** — add Stage 6.0 (link) and 6.2 (build + plot) to `plan_steps`; the new
      HTML join `EXPECTED_HTML`. **6.2.4 `serve.py`** — list the new virtual figures alongside the old.
- [ ] Tests extended in `test_stage6.py`: linkage join, engine invariants, `build_book` output shape,
      `plan_steps` includes the new steps, the new HTML are listed.

**Acceptance:** `python link_positions.py` annotates the in-window positions with tokenIds and links
Adds↔Removes; `python build_book.py` + `plot_book.py` produce the virtual L2/L3 figures whose levels
visibly **flip bid↔ask as spot moves across the day**, plus a `daily_metrics.csv` with volume, fees,
and an APR that lands in a sane range vs Uniswap's published number; `test_stage6.py` green and
`python tests.py` stays green.

---

## Testing

> **Tests are required for every stage and sub-stage** (1, 2, 3, 4.1, 4.2, 4.3 — and any future
> work), following the pattern below. This is a standing rule; individual stage entries don't need to
> restate it. A stage isn't "done" until its tests are added and `python tests.py` is green.

Dependency-free, using the standard library `unittest`. One runner + one file per stage.

- **`tests.py`** — runner. Discovers and runs every `test_*.py` in the directory and exits
  non-zero on any failure. Usage: `python tests.py` (add `-v` for verbose).
- **`test_stage1.py`** — Stage 1 tests, two groups:
  - **Offline unit tests** (no network, no files): import pure functions from
    `uniswap_v3_pool_download_rpc` and check them — `to_unix` date→epoch conversion, and the
    event topic0 keccak hashes match the known canonical signatures.
  - **Output-validation tests** (validate the produced CSVs; skipped if a CSV is absent):
    required columns present; >0 rows; rows sorted by `(block, logIndex)`; numeric fields parse;
    swap `direction` ∈ {`pool_received_token0`,`pool_received_token1`}; mint/burn `tickLower <
    tickUpper`; all timestamps fall within the requested UTC day `[start, end)`.
- **`test_stage2.py`** — added with Stage 2. Offline unit tests for the pure math against tiny
  hand-built fixtures with known answers (price-from-`sqrtPriceX96`, buy/sell classification,
  tick-replay cumulative sum) + output-validation on the derived CSVs.
- **`test_stage3.py`** — added with Stage 3. `Agg`-backend smoke test: render from fixture CSVs and
  assert each PNG exists and is non-empty; skips if `matplotlib` is absent.
- **`test_stage4.py`** — spans 4.1–4.3: `liquidityNet` cumulation + active-tick reconciliation and
  `initial_liquidity.csv` validation (4.1); absolute curve = baseline + net change (4.2); position-replay
  membership (mint⇒active, burn⇒absent, per-slice `L`) + standalone-HTML smoke test (4.3, skips if
  `plotly` absent). Network reads (Multicall3 snapshot) are exercised by running 4.1, not in unit tests.
- **`test_serve.py`** — Stage 4.4: URL listing + link formatting (pure), a real bind-and-GET
  asserting HTTP 200 / 404 on a loopback-bound port, tunnel-URL parsing, and process-group teardown.
- **`test_stage5.py`** — Stage 5: `plan_steps()` ordering, both order-book variants, 4 HTML targets.
- **`test_stage6.py`** — added with Stage 6. Pure tests for the `tokenId` join (match by tx +
  `amount==liquidity`, no-tokenId fallback); the `Orderbook` engine invariants (telescoping,
  virtual-reserve product, zero-fee path independence, geometric-mean fill, constant spread,
  `L` never mutated by a swap, fee monotonicity, conservation); `build_book.py` output shape;
  daily volume/fees/APR sanity bounds vs the pool's published figures; new HTML in `plan_steps`.

Every stage ships its tests in the same commit as its code, and `python tests.py` must stay green.
Tests do not hit the network — Stage 1 network behavior is verified by actually running the
download (the acceptance run), tests then assert the artifact is well-formed.

---

## Scope / non-goals (keep it minimal)

- RPC-only. No Graph, no Dune, no API keys, no wallet.
- USD denomination is **optional**; default output is token-unit / WETH-denominated TVL.
- No database — flat CSVs between stages are enough at this scale (~hundreds–thousands of rows/day).
- Fees: derivable as `|amount_in| × feeTier` if wanted, but not required for the four curves; defer.
- One pool, one date range per run.

## Deliverables

```
uniswap_v3_pool_download_rpc.py   # Stage 1 — download
process.py                        # Stage 2 — derive series
plot.py                           # Stage 3 (+ 4.2) — PNG plots, optional initial-liquidity overlay
usage.py                          # Stage 4.1 — RPC call counter (-> usage.csv)
tick_snapshot.py                  # Stage 4.1 — absolute L2 start snapshot (-> initial_liquidity.csv)
orderbook.py + plot_orderbook.py  # Stage 4.3 — L3 book over time (-> out/orderbook.html)
serve.py                          # Stage 4.4 — viewer: local default + opt-in --tunnel (public share)
run_all.py                        # Stage 5 — one-command pipeline (all CSVs/PNGs/4 HTML)
tests.py + test_stage{1..5}.py + test_serve.py   # test runner + per-stage tests
requirements.txt                  # pinned deps (web3, matplotlib, +plotly at Stage 4.3)
README.md                         # public-facing project pitch
DETAILS.md                        # technical overview, status, v2/v3 + level-1/2/3 notes
out/                              # PNGs + interactive HTML
*.csv                             # intermediate data (incl. initial_liquidity.csv, usage.csv)
old/                              # archived Graph script + original plan
```

## Dependencies

Pinned in **`requirements.txt`** — install with `pip install -r requirements.txt`:

- `web3==7.16.0` — Stage 1 download (`eth_getLogs`) + optional Stage 2 TVL baseline (`balanceOf`).
- `matplotlib==3.10.9` — Stage 3 plotting (`Agg` backend, no display needed).
- `plotly` — Stage 4.3 interactive order-book-over-time (standalone HTML, `animation_frame` slider).
  (Stage 4.1 reads use `web3` + Multicall3; no new dep.)

Stages 1–2 core math is stdlib-only; tests use the stdlib `unittest` (no extra deps). The archived
Graph script in `old/` needs `requests` and is out of scope.
