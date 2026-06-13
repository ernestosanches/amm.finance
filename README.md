# A Better Automated Market Maker

**Reducing the inefficiencies of DeFi market makers by improving AMM formulas — higher returns for
liquidity providers, competitive prices for end users.**

## The idea

Automated Market Makers (AMMs) are the engine of decentralized exchanges, but today's formulas leave
value on the table — liquidity sits where it isn't used, LPs earn less than the risk they take, and
traders pay more than they should. We're designing AMM mechanics that close that gap:

- **Higher returns for liquidity providers** — capital works where it matters.
- **Competitive prices for end users** — tighter effective spreads and less slippage.
- **Less wasted liquidity** — efficiency by design, not by incentives alone.

## What's in this repo

The work starts with **measurement**: to improve on existing AMMs we first reconstruct exactly how
today's concentrated-liquidity pools (Uniswap v3) behave — TVL, trade flow, fees, and the full
liquidity-vs-price book over time — straight from on-chain data, with no third-party accounts.

That analysis pipeline (download → process → visualize, including an interactive order-book-over-time
view) is built and tested here. See **[DETAILS.md](DETAILS.md)** for the technical overview, how to
run it, current status, and notes on Uniswap v2 vs v3 and order-book depth (L1/L2/L3).
