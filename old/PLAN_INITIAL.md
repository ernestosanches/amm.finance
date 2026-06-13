# Uniswap v3 Pool Data — Project Plan

Handoff doc for continuing in Claude Code. Self-contained: all addresses, IDs,
event details, and gotchas needed are below.

## Goal

Download transaction-level activity for a **single Uniswap v3 pool** over a chosen
date range, and from it derive:

- **TVL over time** (to draw a TVL graph)
- **Buys / sells** (swaps, with direction + price)
- **Liquidity added / removed**, each with its **tick range** (v3-specific)
- A **level-3-style liquidity distribution over time** (full liquidity-vs-price curve,
  reconstructable from mint/burn events)

Example target: **ETH/USDC 0.3%**, 1-day range.

## Key facts (verified)

- **Pool (ETH/USDC 0.3%, mainnet):** `0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8`
- **Token ordering:** `token0 = USDC (6 decimals)`, `token1 = WETH (18 decimals)`.
  Price is quoted as token1-per-token0 (WETH per USDC). The lower token address is token0.
- To find any pool address: call `UniswapV3Factory.getPool(tokenA, tokenB, fee)`
  (fee = 500 / 3000 / 10000 for 0.05% / 0.3% / 1%).

## Data model — the pool emits everything we need

All target data lives in five events on the pool contract. Topic0 hashes are
derivable via `keccak(signature)`; the RPC script computes them at runtime.

| Event | Meaning | Key fields |
|---|---|---|
| `Swap`    | buy/sell | `amount0`,`amount1` (signed), `sqrtPriceX96`, `liquidity`, `tick` |
| `Mint`    | liquidity ADDED | `tickLower`,`tickUpper` (range), `amount` (liquidity), `amount0`,`amount1` |
| `Burn`    | liquidity REMOVED | `tickLower`,`tickUpper`, `amount`, `amount0`,`amount1` |
| `Collect` | LP withdrawal | `tickLower`,`tickUpper`, `amount0`,`amount1` |
| `Flash`   | flash loan | `amount0`,`amount1`,`paid0`,`paid1` (affects balances; usually minor) |

Canonical signatures (for topic0 / ABI):
```
Swap(address,address,int256,int256,uint160,uint128,int24)
Mint(address,address,int24,int24,uint128,uint256,uint256)
Burn(address,int24,int24,uint128,uint256,uint256)
Collect(address,address,int24,int24,uint128,uint128)
```

**Swap direction:** `amount0 > 0` ⇒ pool RECEIVED token0 (trader sent token0, got token1).
For this pool that means the trader sold USDC for WETH.

## CRITICAL gotchas (don't skip)

1. **No per-swap fee event.** Fee on a swap ≈ `|amount_in| × feeTier` (0.3% ⇒ 0.003).
   Fees accrue to in-range LPs; they are not logged per swap.
2. **`Burn` moves NO tokens.** It only decrements liquidity and credits `tokensOwed`.
   Tokens physically leave the pool at **`Collect`**.
3. **`Collect` mixes principal + fees.** Don't treat Collect as "fees earned"; isolate fees
   by diffing Collect against the matching Burn principal, or compute from `feeGrowthInside`.
4. **TVL reconstruction from events** uses the balance-affecting set:
   `+Mint`, `±Swap`, `−Collect`, `+Flash.paid` — NOT Burn. Simpler/safer alternative:
   call `token0.balanceOf(pool)` and `token1.balanceOf(pool)` at each target block
   (needs an archive node), then multiply by USD price.
5. **Liquidity distribution ("level 3"):** replay mint/burn in `(block, logIndex)` order with
   `net[tickLower] += amount; net[tickUpper] -= amount` (reverse on burns). Cumulative sum up
   to the active `tick` = active liquidity. This is pure local computation once events are saved.

## Data source options (decided)

| Source | Wallet? | Account? | USD/TVL free? | Effort | Notes |
|---|---|---|---|---|---|
| **The Graph** (Uniswap v3 subgraph) | **Yes** (Studio API key) | wallet sign-in | **Yes** (`poolHourData`) | lowest | 100k queries/mo free, then ~$2–4/100k |
| **Dune** | No | email | Yes (decoded tables) | low (SQL) | 2,500 credits/mo free; CSV export + API |
| **Public RPC** (eth_getLogs) | No | **none** | No (add price feed) | medium | keyless; range-limited, paginate |
| **Free RPC key** (Alchemy/Infura) | No | email | No | medium | 30M CU/mo free; bigger ranges |
| **Etherscan API** | No | email | No | medium | raw logs, decode yourself |
| **BigQuery** `crypto_ethereum.logs` | No | Google | No | low–med SQL | cheap bulk backfill |

The Graph subgraph id: `5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV`
Gateway: `https://gateway.thegraph.com/api/<API_KEY>/subgraphs/id/5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV`

**Chosen default:** wallet-free path. Use the RPC script for raw event truth; use Dune
when dollar-denominated TVL is wanted with zero setup. The Graph only if a wallet is acceptable.

## Starter scripts (already written)

- `uniswap_v3_pool_download.py` — pulls swaps/mints/burns/collects + hourly TVL from
  **The Graph** subgraph (needs `GRAPH_API_KEY`; gives free `amountUSD` + TVL series).
- `uniswap_v3_pool_download_rpc.py` — pulls the same events via **public RPC + eth_getLogs**,
  no wallet/key/account; decimal-adjusts amounts; writes 4 CSVs. USD not included.

Both paginate, dedupe, sort by `(block/timestamp, logIndex)`, and output CSV.

## TODO (next in Claude Code)

- [ ] Pick the source (RPC for no-account; Dune for free USD TVL; Graph if wallet OK).
- [ ] Parameterize pool + fee tier + date range; resolve pool via factory `getPool`.
- [ ] Persist events to a small store (SQLite/Parquet) keyed by `(block, logIndex)` for replay.
- [ ] Build **TVL series**: either `balanceOf(pool)` per block × price, or event-based balances
      (Mint/Swap/Collect/Flash) — then attach USD via a price source (Chainlink ETH/USD,
      or token0/token1Price from the subgraph if using Graph).
- [ ] Compute **fees** per swap (`|amount_in| × feeTier`) and reconcile vs. Collect.
- [ ] Build **liquidity-by-tick over time** (net-liquidity map replay) → depth curve at any t.
- [ ] Classify buys vs sells from swap `amount0` sign; add price = f(`sqrtPriceX96`).
- [ ] Plot: TVL(t), volume, buy/sell flow, liquidity distribution snapshots.
- [ ] (Optional) Generalize to multiple pools / multiple fee tiers.

## References

- Uniswap subgraph schema & endpoints: https://docs.uniswap.org/api/subgraph/overview
- v3-subgraph code: https://github.com/Uniswap/v3-subgraph
- v3 deployments / factory: https://docs.uniswap.org/contracts/v3/reference/deployments/ethereum-deployments