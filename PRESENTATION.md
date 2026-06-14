<!--
PRESENTATION.md — slide deck for the AMM project.

Compile to PDF with Marp (https://marp.app):

    npx @marp-team/marp-cli@latest PRESENTATION.md --pdf --allow-local-files

or, with no Node, use the repo's bundled Chromium:

    python3 build_presentation.py        # -> out/PRESENTATION.pdf

Image paths are relative to the repo root, so keep this file at the repo root.
Slide-image directives: `![full](img)` = big image with a one-line caption,
`![bg right:NN%](img)` = right-column image. Both understood by build_presentation.py.
-->
---
marp: true
paginate: true
size: 16:9
theme: default
style: |
  section { font-size: 26px; }
  h1 { color: #1a73e8; }
  h2 { color: #1a73e8; }
  .small { font-size: 20px; color: #829ab1; }
  .big { font-size: 40px; color: #243b53; }
  table { font-size: 24px; }
footer: "amm.finance"
---

<!-- _paginate: false -->

# A Better Automated Market Maker

### See how real AMMs actually behave — then *design and try* a better one, live.

### 🔗 **[amm.finance](https://amm.finance)**

<span class="small">Ernesto Sanches · everything in this deck is real and reproducible from the repo</span>

---

## Liquidity you can't see, can't price

- **LPs** earn less than the risk they take — capital sits where it never trades.
- **Traders** pay more than they should — wider spreads, more slippage.
- An AMM curve is an **invisible, abstract object**. Nobody has good intuition for what a liquidity shape *does*.

<span class="small">You can't improve what you can't see, measure, or safely experiment with.</span>

---

![full](out/png/depth_virtual_frame23.png)

## An AMM *is* an order book

<span class="small">Bids below spot (green), asks above (red). A swap slides the line — and every level it crosses flips side.</span>

---

<!-- _footer: "Part 1 — measure a real pool, from chain data alone" -->

![full](out/tvl.png)

## One real pool, one real day — reconstructed with no account

<span class="small">ETH/USDC 0.3%, 24h of TVL — keyless public RPC only: no wallet, no API key, no Graph/Dune.</span>

---

![full](out/price_flow.png)

## Every trade, classified — buys up, sells down

<span class="small">770 swaps across the day, each priced and sided straight from the chain.</span>

---

![full](out/liquidity_distribution.png)

## Where the liquidity actually sits

<span class="small">Absolute depth across price, start vs end of day — recovered from on-chain state, not estimated.</span>

---

![full](out/png/orderbook_virtual__without_initial_frame23.png)

## …decomposed into individual LP orders

<span class="small">Each color is one liquidity provider's position, replayed across the whole day with a time slider.</span>

---

<!-- _paginate: true -->

## It checks out against reality

<div class="big">
$2.73M volume · $8.2k fees · <b>14.3% APR</b>
</div>

<span class="small">Computed by replaying every swap through our engine — and it lands squarely on Uniswap's published number for this pool. The model is validated against the real thing.</span>

---

<!-- _footer: "Part 2 — our AMM, live" -->

![full](app/out/ui_1_lobby.png)

## Trade now on our AMM!

### Compete with your peers here right now → 🔗 **[amm.finance](https://amm.finance)**

---

![full](app/out/ui_2_running.png)

## Uniswap's AMM vs. our new design — side by side

<span class="small">Left pool is classic Uniswap v3. Right pool is our model. Trade and provide liquidity on both, and watch which one serves traders and LPs better.</span>

---

![full](docs/img/game_level3_book.png)

## Our model: you draw the AMM curve yourself

<span class="small">Instead of Uniswap's fixed price range, place liquidity in *any* shape you want — it becomes a live order book (the colored hump on the grey baseline). A real way to experiment with better curves.</span>

---

![full](docs/img/game_leaderboard.png)

## Which design earns more?

<span class="small">Every participant marked against a passive-LP benchmark — a direct, live experiment in whether a hand-drawn curve can beat Uniswap's fixed-range model.</span>

---

![full](app/out/ui_5_profile.png)

## Every experiment, fully tracked

<span class="small">Balances, fees, volume, open positions, full action history — and value-over-time, so each participant can see exactly how their AMM design performed.</span>

---

![full](app/out/ui_6_admin.png)

## Runs as a real event

<span class="small">One operator configures and starts it. The ledger reads exact to float-epsilon, and a mid-game crash restarts with byte-identical state — zero loss.</span>

---

<!-- _paginate: false -->
<!-- _footer: "" -->

# Try it: 🔗 [amm.finance](https://amm.finance)

<div class="big">
Measure → <code>python run_all.py</code><br>
Try the new AMM → <code>python app/run.py</code>
</div>

<span class="small">We made the invisible AMM curve visible and measurable — then built a better one you can design and try for yourself.</span>
