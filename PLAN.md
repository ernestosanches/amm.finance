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

- [ ] **`orderbook.py`** — replay `mints`/`burns`/`collects` in `(block, logIndex)` order, maintaining a
      **position table** keyed by `(owner, tickLower, tickUpper)` (NFT `tokenId` decoding is a later
      refinement). Bucket the range into time slices; per slice emit each **active** position
      `{mint_time(age), tickLower, tickUpper, current_L}`. Seed the per-tick aggregate from
      `initial_liquidity.csv` when available so each slice shows **absolute** depth (old baseline +
      live positions); without it, slices are in-window-only and labelled as such. Output
      `orderbook_slices.csv`/`.json`.
- [ ] **`plot_orderbook.py`** — Plotly `animation_frame` figure: per frame a heatmap `Y = tick levels`,
      `X = active positions oldest→newest`, `colour = L`, `slider = time`. Export standalone
      `out/orderbook.html`. Plus a simpler companion **L2 depth-over-time** animation
      (`X = tick`, `Y = absolute active L`, `animation_frame = time`) → `out/depth_over_time.html`.
- [ ] Add `plotly` to `requirements.txt`.
- [ ] Tests: position-replay membership (`mint⇒active`, `burn⇒absent`, per-slice `L`), baseline
      seeding, and a standalone-HTML smoke test (skip if `plotly` absent).

Acceptance: 4.1 writes `initial_liquidity.csv` (verified vs `liquidity()`) and `usage.csv`;
4.2 plots an absolute depth curve using it (degrading gracefully when absent); 4.3 writes a shareable
`out/orderbook.html` with a working time slider using all prepared data; all stage tests green.

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
tests.py + test_stage{1,2,3,4}.py # test runner + per-stage tests
requirements.txt                  # pinned deps (web3, matplotlib, +plotly at Stage 4.3)
README.md                         # overview, status, v2/v3 + level-1/2/3 notes
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
