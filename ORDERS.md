# ORDERBOOK.md — AMM as a Virtual Limit-Order Book

Reference model for an AMM backtester. Treats a Uniswap-v3-style concentrated-liquidity
position (and the v2 full-range pool as its special case) as an equivalent **regenerative
grid of paired limit orders**. This document is the conceptual + numerical contract the
backtester must satisfy.

**Companion file:** `ORDERBOOK.html` — a standalone, dependency-free interactive that
animates the five-stage walkthrough (LP add → Taker 1 buy → Maker repost → Taker 2 sell →
Maker repost) with a fee slider. Open it in any browser. It demonstrates the key v3 fact
(see §5A): the fee pot grows while every rung size stays fixed. Its hardcoded `L = 1000`
and toy `sqrt(P)` grid are illustration only — feed real `L` / ticks / `gamma` when wiring
it to the engine.

---

## 0. TL;DR mental model

An LP position is **not** a one-shot order book. It is a **grid bot with
infinite-density paired orders**:

- Every price level holds *both* a sell-token0 order and a buy-token0 order at the same price.
- The current spot price is just a **pointer** into a static book. Levels above spot are
  live asks; levels below spot are live bids.
- A swap **slides the pointer**; it does not redraw the book.
- Crossing a level **toggles its side** (a filled ask instantly becomes a resting bid at
  the same level, funded by the taker's own input). No order is ever re-priced or re-sized.
- The book *shape* `rho0(P) = L / (2 * P^1.5)` (token0 per unit price) is invariant; only
  the bid/ask boundary (spot) and per-level side change.

The "gap" a hand-run traditional book would show after a fill is closed automatically by
this toggle. Liquidity is **never spatially redistributed** and **never re-centered** on
the new price.

---

## 1. Conventions

| Symbol | Meaning |
|---|---|
| token0 | base asset (on-chain: the lower-address token) |
| token1 | quote asset |
| `P`    | price of token0 in token1 = `y / x` = token1 per token0 |
| `sP`   | `sqrt(P)` (Uniswap stores `sqrtPriceX96`; here use float `sqrt(P)`) |
| `L`    | liquidity (constant within a single tick interval) |
| `x`    | token0 amount, `y` token1 amount |
| `gamma`| pool fee rate (e.g. 0.0005, 0.003) |

Tick <-> price:

```
P(i)  = 1.0001 ^ i
sP(i) = 1.0001 ^ (i/2)
```

A position is defined by `(L, tickLower, tickUpper, owner)`, giving
`sPa = sP(tickLower)`, `sPb = sP(tickUpper)`, with `sPa < sPb`.

Direction convention: **sP increasing = price up = token0 appreciating**. Buying token0
(token1 in, token0 out) moves price **up**; selling token0 moves price **down**.

---

## 2. Liquidity from deposited amounts

Given spot `sP` and desired deposit `(x, y)`:

```
if sP <= sPa:        # entirely token0
    L = x * (sPa * sPb) / (sPb - sPa)
elif sP >= sPb:      # entirely token1
    L = y / (sPb - sPa)
else:                # in range; binding side wins
    L = min( x * sP * sPb / (sPb - sP),
             y / (sP - sPa) )
```

## 3. Position inventory at a given spot

```
if sP <= sPa:   x = L * (1/sPa - 1/sPb);   y = 0
elif sP >= sPb: x = 0;                      y = L * (sPb - sPa)
else:           x = L * (1/sP  - 1/sPb);    y = L * (sP - sPa)
```

Holdings depend **only on current price**, never on path (see §7).

### Virtual-reserve invariant (use as a runtime assertion)

Within a constant-`L` region, define virtual reserves:

```
xv = x + L/sPb        # = L/sP   when measured against this position's own range
yv = y + L*sPa        # = L*sP
xv * yv = L^2         # the v3 constant-product invariant
price  = yv / xv = sP^2 = P
```

v2 is the special case `tickLower -> -inf`, `tickUpper -> +inf`, so `sPa -> 0`,
`sPb -> inf`, `xv = x`, `yv = y`, `x*y = L^2 = k`.

---

## 4. The virtual order ladder (the "book")

Discretize the range into tick bands `[i, i+1]`. Each band is **one limit order**:

```
q0_i  = L * (1/sP_i - 1/sP_{i+1})      # token0 size of the order
q1_i  = L * (sP_{i+1} - sP_i)          # token1 size of the order
Pbar_i = q1_i / q0_i = sP_i * sP_{i+1} = sqrt(P_i * P_{i+1})   # geometric-mean fill price
```

Side relative to spot `sP`:

```
sP_i     >= sP  ->  ASK  (sell token0, filled as price rises)
sP_{i+1} <= sP  ->  BID  (buy token0,  filled as price falls)
band straddling sP -> partially filled (ask above sP, bid below sP)
```

Telescoping identity (must hold in tests): summing bands over `[lo, hi]`

```
sum q0_i = L * (1/sP_lo - 1/sP_hi)
sum q1_i = L * (sP_hi - sP_lo)
```

### Continuous limit (density)

```
dx = (L / P) * d(sP)     ->  token0 sell density  = L / (2 * P^1.5)  per unit P
dy =  L       * d(sP)     ->  token1 density       = L / (2 * sP)     per unit P
dy/dx = P                 ->  each infinitesimal slice fills exactly at its own price P
```

`q1` per band is uniform in `sP` (`= L * dSP`); `q0` per band grows as price falls
(`~ 1/(sP_i * sP_{i+1})`). The ladder is **uniform in sqrt-price space, not in price space.**

---

## 5. Swap dynamics (single constant-L step)

A swap moves `sP -> sP'`. The swept band executes; everything outside is untouched.

### Exact-input, fee taken on input

```
amount_in_net = amount_in_gross * (1 - gamma)
fee           = amount_in_gross * gamma          # stays in pool, credited to in-range LPs
```

Buy token0 (token1 in, price UP):
```
sP'         = sP + amount1_in_net / L
amount0_out = L * (1/sP - 1/sP')
```

Sell token0 (token0 in, price DOWN):
```
sP'         = 1 / (1/sP + amount0_in_net / L)     # = L*sP / (L + amount0_in_net*sP)
amount1_out = L * (sP - sP')
```

### Exact-output

Buy exact token0_out (price UP):
```
sP'        = 1 / (1/sP - amount0_out / L)
amount1_in_net = L * (sP' - sP)
```

Sell for exact token1_out (price DOWN):
```
sP'        = sP - amount1_out / L
amount0_in_net = L * (1/sP' - 1/sP)
```

### Blended execution price of the step

```
avg fill = amount1 / amount0 = sP * sP' = sqrt(P * P')   # geometric mean of endpoints
```

This equals the sum of the individual band fills — i.e. a swap **is** the execution of the
virtual orders in `[sP, sP']`, in order, independently.

---

## 5A. Fees — v2 reinvest vs v3 separate accrual (READ THIS)

The single most important fee distinction, and it changes the book.

**v3 (and the ladder in this doc): fees are NOT reinvested.** The fee is skimmed off the
taker's input and booked to a *separate* per-position accumulator. Active `L` is unchanged
by a swap (it changes only on mint/burn or tick-cross). Since every rung size is a pure
function of `L` (`q0_i = L(1/sP_i - 1/sP_{i+1})`), **order sizes are exactly constant when
price moves.** The fee is a toll parked on the side; it never enlarges the resting orders.
The spread (§7.5) is a haircut on execution price, not a resize. Skim != reinvest.

Accounting (mirrors Uniswap v3):

```
# on every swap step, charged on the IN-RANGE liquidity:
feeStep                  = amount_in_gross * gamma        # denominated in the INPUT token
feeGrowthGlobal{0,1}    += feeStep / L                    # per unit of liquidity

# per position, computed lazily on mint / burn / collect:
feeGrowthInside{0,1}     = feeGrowthGlobal - feeGrowthBelow - feeGrowthAbove
tokensOwed{0,1}         += L_position * (feeGrowthInside - feeGrowthInsideLast)
feeGrowthInsideLast{0,1} = feeGrowthInside
# collect() pays out tokensOwed and zeroes it; it does NOT change L
```

`tokensOwed` is a claimable side-balance, never part of `L`. Auto-compounding (some vaults
do it) is a *discrete* `mint` that bumps `L` at a chosen cadence — model it as an explicit
liquidity-add event, never as a side effect of a swap.

**v2: fees ARE reinvested.** The taker's full input (fee included) stays in the reserves,
so `k = x*y` grows every trade, `L = sqrt(k)` grows, and the depth ladder thickens over
time. There is no separate accumulator; LP value is simply the share of the (growing)
reserves. Here order sizes are *not* constant — they compound upward. If you model v2, make
`L` (or `k`) a function of cumulative fees and derive depth from the current `k`.

| | v2 | v3 |
|---|---|---|
| fee destination | back into reserves | separate `tokensOwed` |
| effect on `L` | grows each trade | unchanged by swaps |
| rung sizes as price moves | thicken (compound) | constant |
| how LP realizes fees | burn LP tokens for a larger share | `collect()` |

`ORDERBOOK.html` shows the v3 case live: crank `gamma`, watch the fee pot grow while every
`q0` stays fixed.

---

## 6. State transition = "execute then flip" (the core update)

For each swap, the book update is:

1. **Execute**: orders in the swept interval `(sP, sP')` fill at their preset `Pbar_i`.
2. **Survivors untouched**: orders outside `(sP, sP')` keep identical price and size.
3. **Flip**: each swept level is re-posted on the *opposite side*, same price, same `q0`,
   funded by the asset the taker just delivered. (An ask that filled selling token0 becomes
   a bid buying token0 with the token1 just received.)

A hand-run **traditional** book would stop at step 1 and leave a gap. To emulate the AMM you
must apply step 3 (the AMM does it automatically). Net new capital for the flip = **zero**;
it is a redenomination of the taker's payment at the same levels. **In v3 the reposted level
comes back the SAME size** — the fee is skimmed into `tokensOwed` (see §5A), not into the
order, so `L` and every `q0` are unchanged. (In v2 the fee re-enters reserves, so the level
returns marginally thicker; that is the v2 LP's income. Do not apply the v2 behavior to a
v3 model.)

If a swap fully crosses `tickUpper`, all asks are consumed and the position is 100% token1
(every level now a bid). Symmetrically at `tickLower`.

---

## 7. Invariants to assert in tests

1. **Telescoping** (§4): per-band sums equal closed forms. Tolerance: float eps.
2. **Virtual reserves** (§3): `xv * yv == L^2` and `yv/xv == P` within constant-L region.
3. **Path independence (gamma = 0)**: any round trip `sP -> sP' -> sP` returns `(x, y)`
   exactly (up to float tol). Inventory is a pure function of current price.
4. **Geometric-mean fill**: each band's realized `q1/q0 == sqrt(P_i * P_{i+1})`.
5. **Constant multiplicative spread**: marginal ask price `= P/(1-gamma)`, marginal bid
   price `= P*(1-gamma)`; half-spread `= gamma` per side, **independent of P**. No gap ever.
   The spread comes from the fee skim, not from resizing — in v3, `L` and all rung sizes are
   unchanged by it (§5A). Assert that a swap never mutates any position's `L`.
6. **Fee monotonicity**: `feeGrowthGlobal` is non-decreasing; total fees == sum of per-step
   `amount_in_gross * gamma`.
7. **Conservation**: token0/token1 in == out + fee retained, per step.

---

## 8. Multi-LP and tick boundaries (do NOT assume global constant L)

The single-step formulas in §5 hold only **within one tick interval where L is constant**.
Across the whole pool, active `L` changes at every initialized tick because other LPs'
ranges start/end there.

- Maintain a tick-indexed map `tick -> liquidityNet`.
- Crossing a tick **upward** (price up): `L += liquidityNet[tick]`.
- Crossing a tick **downward** (price down): `L -= liquidityNet[tick]`.
- A position earns fees **only** for the portion of a swap traversed while spot is inside
  its `[tickLower, tickUpper]` (track via `feeGrowthInside = global - below - above`).

Your own position's orders behave exactly as §4–§6 describe. But the *pool-wide* price
impact of a taker depends on aggregate L, which jumps at boundaries.

---

## 9. Recommended representation (don't materialize the book)

The order book is a **derived view**, not a storage format. Store:

```
PoolState:
    sP            : float        # current sqrt price
    tick          : int          # current tick
    L             : float        # active liquidity at spot
    ticks         : map<int, TickInfo>   # liquidityNet, feeGrowthOutside0/1, initialized
    feeGrowthGlobal0, feeGrowthGlobal1 : float
    gamma         : float

Position:
    L, tickLower, tickUpper, owner
    feeGrowthInsideLast0/1, tokensOwed0/1
```

Derive the ladder on demand from `(L-profile across ticks, sP)` only when you need the
order-book view for validation, plotting, or strategy logic. Never iterate per-order in the
hot path — iterate per *initialized tick*.

---

## 10. Swap loop (cross-tick, the backtester hot path)

```
function swap(zeroForOne, amount_in_gross):
    # zeroForOne = true  -> selling token0, price DOWN
    # zeroForOne = false -> buying token0,  price UP
    remaining = amount_in_gross
    out = 0
    while remaining > 0:
        tickNext  = next_initialized_tick(tick, zeroForOne)
        sPNext    = sP(tickNext)
        # how far can we go in this constant-L interval?
        (sPNew, inStep, outStep, feeStep) =
            compute_swap_step(sP, sPNext, L, remaining, gamma, zeroForOne)
        sP = sPNew
        remaining -= (inStep + feeStep)
        out += outStep
        feeGrowthGlobal += feeStep / L          # per-unit-liquidity accrual
        if sP == sPNext:                         # crossed the tick exactly
            net = ticks[tickNext].liquidityNet
            L  += zeroForOne ? -net : +net
            tick = zeroForOne ? tickNext - 1 : tickNext
        else:                                    # ran out of input mid-interval
            tick = tick_at(sP)
            break
        if L == 0 and remaining > 0:
            # liquidity gap: price cannot advance without crossing empty space
            handle_empty_region()                # see §11
    return out
```

`compute_swap_step` is §5 applied to `min(target sPNext, price reachable with remaining)`,
with the fee skimmed from input first.

---

## 11. Edge cases / gotchas

- **Empty-liquidity regions**: if `L == 0` between initialized ticks, spot cannot traverse
  it via trading (no counterparty). Decide policy: halt the swap (realistic) vs jump
  (only if external liquidity assumed). Real pools halt — input is left unfilled.
- **Rounding direction**: on-chain integer math rounds outputs down / inputs up for
  solvency. A float backtester drifts from on-chain values; if you need exact parity,
  port the `X96` fixed-point rounding. For PnL/strategy backtests, float + the invariants
  in §7 is usually sufficient — but document the choice.
- **Token ordering**: `P` and the entire ask/bid labeling depend on which token is token0.
  Fix this once (lower address = token0) and assert it.
- **Fee on input vs output**: this doc takes fee on input (Uniswap v3 convention). Keep it
  consistent everywhere or the spread invariant (§7.5) breaks.
- **Straddle band**: the band containing spot is partially filled — split into an ask part
  above `sP` and a bid part below. Handle fractional fills here, not just whole bands.
- **Crossing your own range bound**: when spot exits `[tickLower, tickUpper]`, the position
  stops trading and stops earning fees; it sits 100% in one token until spot re-enters.

---

## 12. v2 as the special case (sanity baseline)

Set one full-range position: `sPa -> 0`, `sPb -> inf`, `L = sqrt(k)`. Then `x = L/sP`,
`y = L*sP`, `x*y = k`, density `L/(2 P^1.5)` for all P, spot pointer never hits a range
bound, and active L is globally constant. Implement and validate v2 first; v3 is the same
machinery with a non-trivial tick-indexed L-profile.

---

## 13. Build order (suggested)

1. v2 single-position engine: §2, §3, §5, §7 invariants 2–5, 7.
2. Order-ladder derived view + §4 telescoping/geo-mean assertions.
3. Fees: §5 fee handling, §5A accrual model (v3: separate `tokensOwed`, `L` untouched),
   §7.6, spread invariant §7.5.
4. v3 ticks: §8 liquidityNet map, §10 cross-tick loop.
5. Multi-LP fee attribution (`feeGrowthInside`).
6. Edge cases §11; then strategy/PnL layer on top.