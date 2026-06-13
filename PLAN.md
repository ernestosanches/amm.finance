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

- [ ] **Price from tick/sqrtPriceX96**: `price = (sqrtPriceX96**2 / 2**192)` then adjust by
      `10**(d0-d1)` to get token1-per-token0 in human units (USDC per WETH → invert for WETH price).
- [ ] **Buys vs sells**: from swap `amount0` sign (`amount0>0` = pool received USDC = trader sold
      USDC for WETH). Emit `swaps_classified.csv` with side + price + size.
- [ ] **TVL over time (RPC-only, no price API):** reconstruct pool token balances by replaying
      balance-affecting events from a starting balance:
      start from `balanceOf(pool)` at `start_block` for token0/token1 (one RPC call each — still
      keyless, current-state read), then apply `+Mint`, `±Swap`, `−Collect`, `+Flash.paid` per event
      in order. Express TVL in token units and in WETH-terms using the swap-derived price
      (USD optional/out-of-scope for the minimal version). Emit `tvl_series.csv`.
      - Fallback if start-block `balanceOf` isn't served by the public node: start at 0 and report
        TVL as *net flow* (relative), noting the offset. Decide at implementation time.
- [ ] **Liquidity-by-tick over time ("level 3"):** replay mints/burns in `(block, logIndex)` order:
      `net[tickLower] += amount; net[tickUpper] -= amount` (reverse sign on burns). Cumulative sum
      gives active liquidity vs tick. Snapshot the curve at the start and end of the range (and the
      active `tick` from the latest swap). Emit `liquidity_distribution.csv`.

Acceptance: derived CSVs produced from Stage 1 output with no network calls (except the two optional
`balanceOf` reads for the TVL baseline).

## Stage 3 — Display the curves

New script `plot.py` — reads Stage 2 CSVs, renders PNGs with matplotlib.

- [ ] **TVL(t)** line chart from `tvl_series.csv`.
- [ ] **Price(t)** + **buy/sell flow**: price line with buy/sell volume bars from `swaps_classified.csv`.
- [ ] **Liquidity distribution**: bar/step chart of active liquidity vs price (tick), with the active
      tick marked — start vs end snapshot.
- [ ] Save to `out/*.png`; one figure per curve. Keep it minimal (matplotlib only).

Acceptance: running `plot.py` produces the PNGs from Stage 2 CSVs.

---

## Scope / non-goals (keep it minimal)

- RPC-only. No Graph, no Dune, no API keys, no wallet.
- USD denomination is **optional**; default output is token-unit / WETH-denominated TVL.
- No database — flat CSVs between stages are enough at this scale (~hundreds–thousands of rows/day).
- Fees: derivable as `|amount_in| × feeTier` if wanted, but not required for the four curves; defer.
- One pool, one date range per run.

## Deliverables

```
uniswap_v3_pool_download_rpc.py   # Stage 1 (fix + reuse)
process.py                        # Stage 2 (new)
plot.py                           # Stage 3 (new)
out/                              # PNGs
*.csv                             # intermediate data
old/                              # archived Graph script + original plan
```

## Dependencies

`web3` (installed, 7.16.0) for Stage 1; `matplotlib` for Stage 3 (install at Stage 3).
