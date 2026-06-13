# Uniswap v3 Pool Data — RPC-only pipeline

Download transaction-level activity for a single Uniswap v3 pool over a date range using
**only a keyless public RPC** (no wallet, no API key, no account), then process and plot it.

Reference pool: **ETH/USDC 0.3%** `0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8`
(token0 = USDC 6dec, token1 = WETH 18dec). Example range used throughout: **Jun 12 2026** (1 day).

## Status

| Stage | What | State |
|---|---|---|
| 1 | Download raw events (Swap/Mint/Burn/Collect) via `eth_getLogs` | ✅ done, tested |
| 2 | Process → classified swaps, TVL series, liquidity-by-tick | ✅ done, tested |
| 3 | Plot → TVL, price+flow, liquidity distribution (PNGs) | ✅ done, tested |
| 4.1 | Absolute start-of-range **L2 liquidity snapshot** (`tick_snapshot.py`) + RPC usage logger (`usage.py` → `usage.csv`) | ✅ done, tested |
| 4.2 | Use the initial liquidity in Stage 3 plots (absolute depth curve) | ✅ done, tested |
| 4.3 | Interactive, shareable **order book over time** (time slider, Plotly HTML) | ✅ done, tested |
| 4.4 | Viewer `serve.py` (local default; opt-in `--tunnel` public share) | ✅ done, tested |
| 5 | One-command pipeline `run_all.py` (all CSVs/PNGs/4 HTML) | ✅ done, tested |

Tests: `python tests.py` → **62 passing** (stdlib `unittest`, `test_stage{1..5}.py` + `test_serve.py`;
tests are required for every stage/sub-stage). The Graph-subgraph variant and the original handoff
doc are archived in `old/` (out of scope).

## Quick start

```bash
pip install -r requirements.txt

# Everything in one command — all CSVs, PNGs, and the 4 HTML files (Stage 5):
python run_all.py
python serve.py        # serve out/ on 127.0.0.1:8000; view via an SSH port-forward
```

Or run the stages individually:

```bash
# Stage 1 — download (keyless RPC)
python uniswap_v3_pool_download_rpc.py \
    --pool 0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8 \
    --start 2026-06-12 --end 2026-06-13
# -> swaps.csv, mints.csv, burns.csv, collects.csv

# Stage 2 — process (pure local; --baseline-rpc for ABSOLUTE TVL)
python process.py --baseline-rpc
# -> swaps_classified.csv, tvl_series.csv, liquidity_distribution.csv

# Stage 4.1 — absolute start-of-range L2 liquidity snapshot (keyless, Multicall3) + usage log
python tick_snapshot.py --start 2026-06-12
# -> initial_liquidity.csv (self-checked vs liquidity()), usage.csv

# Stage 3 + 4.2 — plot (uses initial_liquidity.csv for an ABSOLUTE depth curve when present)
python plot.py
# -> out/tvl.png, out/price_flow.png, out/liquidity_distribution.png

# Stage 4.3 — interactive, shareable order-book-over-time (standalone HTML)
python orderbook.py --slices 24      # -> orderbook_slices.csv, depth_slices.csv
python plot_orderbook.py             # -> out/orderbook.html, out/depth_over_time.html

python tests.py     # 62 passing
```

## What we get (Jun 12 2026)

- **Swaps** 770, **Mints** 11, **Burns** 28, **Collects** 27.
- **Price** ≈ $1655–1685 / ETH (from `sqrtPriceX96`; cross-checked against tick within 0.1%).
- **TVL** ≈ **$20.7M → $20.0M** over the day (absolute basis; baseline 5.54M USDC + 9085 WETH).
- **Liquidity distribution**: net change over the day (burns > mints ⇒ net decrease).

## Files

```
uniswap_v3_pool_download_rpc.py   # Stage 1 — keyless RPC download
process.py                        # Stage 2 — derive series (no network by default)
plot.py                           # Stage 3 (+4.2) — PNG plots, optional absolute-depth overlay
usage.py                          # Stage 4.1 — RPC call counter (-> usage.csv)
tick_snapshot.py                  # Stage 4.1 — absolute L2 start snapshot (-> initial_liquidity.csv)
orderbook.py                      # Stage 4.3 — time-sliced order-book data (-> *_slices.csv)
plot_orderbook.py                 # Stage 4.3 — interactive HTML (-> out/*.html)
serve.py                          # Stage 4.4 — viewer: local default + opt-in --tunnel (public share)
run_all.py                        # Stage 5 — one-command pipeline (all CSVs/PNGs/4 HTML)
tests.py + test_stage{1..5}.py    # stdlib-unittest runner + per-stage tests
requirements.txt                  # web3, matplotlib, plotly (pinned)
PLAN.md                           # the working plan
out/                              # generated PNGs + interactive HTML (gitignored)
*.csv                             # generated data (gitignored)
old/                              # archived Graph script + original handoff plan
```

---

## How this handles concentrated liquidity (v2 vs v3)

**v2** is one constant-product pool `x·y=k`; liquidity is spread uniformly across all prices, so a
"liquidity distribution" is trivial and there is no tick / no `sqrtPriceX96`.

**v3** is *concentrated*: each LP position is liquidity `L` in a range `[tickLower, tickUpper)`,
active only while the price is inside it. The active liquidity at a price = cumulative sum of
per-position deltas (`+L` at lower tick, `−L` at upper tick). This non-uniform curve is the whole
point of v3 and is what makes an AMM resemble an order book.

**Data side (correct & v3-aware):** Stage 1 captures the v3 primitives — `tickLower/tickUpper/amount(=L)`
on mints/burns (L stored un-decimal-adjusted, correct) and `liquidity`+`tick` on swaps. Stage 2's
`build_liquidity_distribution` implements the standard `+L`/`−L` tick map + ascending cumsum exactly.

**Key limitation — daily *net change*, not the standing curve.** We download **one day** of events,
but the *absolute* liquidity curve needs the cumulative sum over the pool's entire history. So our
curve is the **net change** over the range (it can go negative — on Jun 12 burns outweighed mints).
The plot is labelled accordingly. The absolute standing curve is recoverable cheaply via a direct
on-chain tick-state read (`ticks()` / `tickBitmap`) at the start block — keyless, ~5 calls, verified
(see "Absolute liquidity snapshot" below) — and is exactly what **Stage 4.1** adds.

Other caveats:
- Pool-level `owner` on mint/burn is the **NonfungiblePositionManager** (`0xC364…FE88`), not the end
  LP — true per-user identity needs decoding the NFT `tokenId` one layer up.
- The liquidity plot uses **raw `L`**, not token/USD depth (converting needs per-band price width).
- TVL (`balanceOf` + event replay) and price (`sqrtPriceX96`) are exact and version-correct.

## Level-1/2/3 and our status

Order-book "levels", mapped onto an AMM by analogy (a v3 range position ≈ a resting range limit order):

- **L1** — top of book (best bid/ask, last price).
- **L2** — **aggregated depth per price level** (the depth ladder). Aggregating v3 positions per tick
  gives this.
- **L3** — **order-by-order** book: every individual maker order/position shown separately.

Where we stand:
- **L3-grade source data: yes.** Each `Mint`/`Burn` row is a single position-level action
  (`owner`, range, `L`) — order-by-order granularity.
- **Processed output: L2 (a daily delta).** `liquidity_distribution.csv` aggregates positions into a
  net per-tick map, discarding per-position identity.

| Goal | Have? | Needs |
|---|---|---|
| L3 **event log** (per-position actions) | ✅ (for the window) | already in `mints.csv`/`burns.csv` |
| L2 depth, **net change over the day** | ✅ | current `liquidity_distribution.csv` |
| L2 **absolute standing depth** at a time | ✅ Stage 4.1 | on-chain tick-state read — **cheap & verified**: ~5 RPC calls via Multicall3 (below) |
| L3 **standing book** (live positions w/ owner+range+size) | ❌ | per-position state tracking (decode NFT `tokenId`s) |
| Depth in **tokens/USD** (not raw `L`) | ❌ | convert `L` per tick band → token amounts |

Concentrated liquidity is the bridge that makes the AMM order-book-shaped at all: keep each position
separate → L3; aggregate per tick → L2. **Stage 4** (done) adds the absolute L2 baseline (4.1), folds
it into the Stage-3 plots (4.2), and builds the interactive book-over-time figures with a time slider
(4.3): `out/depth_over_time.html` (absolute L2 depth) and `out/orderbook.html` (per-position L3, scoped
to in-window-minted positions).

### Absolute liquidity snapshot — feasibility (verified)

The standing L2 depth at any block is readable from pool state, keyless, **without parsing history**:
`slot0()` (current tick) + `liquidity()` (active-L anchor) + `tickBitmap()` (which ticks are
initialized) + `ticks(t).liquidityNet` (the absolute `±L` at each tick). Cumulating `liquidityNet`
outward from the current tick rebuilds absolute active L per band.

- This pool has **688 initialized ticks** at the start block (not "thousands").
- Via **Multicall3** (`0xcA11…CA11`, valid at historical blocks) the whole snapshot is **~5 `eth_call`s**
  instead of ~800.
- The reconstruction was checked to **equal on-chain `liquidity()` exactly** — so the method is correct
  and complete. It yields **aggregate L2 only** (no individual old-order identity).

### RPC endpoint, limits & usage

Default endpoint `https://ethereum-rpc.publicnode.com` is **keyless** (no account). It's
Cloudflare-fronted with an Envoy proxy and exposes **no `x-ratelimit-*` / `retry-after` headers**, so
there's no machine-readable quota; PublicNode markets it as free/fair-use with no published hard limit,
and throttling (when it happens) surfaces as HTTP 429/503. Posture: **batch** (Multicall3 cuts calls
~100×), retry-with-backoff on 429/503, modest concurrency. No usage is tracked today — **Stage 4.1**
adds `usage.py`, a per-method RPC counter that logs to **`usage.csv`** so total endpoint usage is
visible across runs.
