#!/usr/bin/env python3
"""Stage 6.1 — the virtual-order-book engine (pure, no network).

Models a Uniswap-v3 concentrated-liquidity pool as the regenerative grid of paired limit orders
described in ORDERS.md, and replays a day of events to produce the L2/L3 book over time plus the
fee / volume / APR by-products.

Native units. Everything here is in the pool's *raw* token0/token1 space, exactly as ORDERS.md
defines it, so the invariants hold with no decimal fudging:
    P  = token1/token0 (raw)      sP = sqrt(P) = 1.0001**(tick/2)      L  = raw liquidity
The display layer converts to human terms with the token decimals (helpers at the bottom). For the
ETH/USDC 0.3% pool token0 = USDC (6dec), token1 = WETH (18dec); human price = USDC per WETH =
10**(d1-d0) / P.  A trade that BUYS WETH sends USDC in (token0 in) -> ORDERS "sell token0" -> sP
*down* -> human USDC/WETH *up*; selling WETH is the mirror. So in human price coordinates a band
ABOVE spot is an ASK (pool sells WETH there) and a band BELOW spot is a BID (pool buys WETH).

Fees are skimmed off the taker's input into a separate per-position counter and NEVER reinvested
(v3), so order sizes are constant as price moves — only each level's bid/ask side flips.
"""
import math
from collections import defaultdict


# --- math layer (pure; the ORDERS.md contract) -------------------------------

def tick_to_sqrt_price(tick):
    """sP(tick) = 1.0001**(tick/2) in raw token1/token0 space."""
    return math.sqrt(1.0001 ** tick)


def position_inventory(L, sPa, sPb, sP):
    """(x, y) = (token0, token1) held by liquidity L over [sPa, sPb] at spot sP.

    Depends only on the current price, never on path. Out of range -> single-sided.
    """
    if sP <= sPa:
        return L * (1 / sPa - 1 / sPb), 0.0
    if sP >= sPb:
        return 0.0, L * (sPb - sPa)
    return L * (1 / sP - 1 / sPb), L * (sP - sPa)


def virtual_reserves(L, sPa, sPb, sP):
    """(xv, yv) virtual reserves; satisfy xv*yv == L**2 and yv/xv == P within constant-L."""
    x, y = position_inventory(L, sPa, sPb, sP)
    return x + L / sPb, y + L * sPa


def band_order(L, sP_i, sP_j):
    """One tick-band limit order over [sP_i, sP_j] (sP_i < sP_j): (q0, q1, pbar).

    q0 = token0 size, q1 = token1 size, pbar = q1/q0 = sP_i*sP_j = geometric-mean fill price.
    """
    q0 = L * (1 / sP_i - 1 / sP_j)
    q1 = L * (sP_j - sP_i)
    return q0, q1, q1 / q0


def liquidity_for_amounts(sP, sPa, sPb, x, y):
    """L obtainable from depositing (x, y) at spot sP into range [sPa, sPb] (binding side wins)."""
    if sP <= sPa:
        return x * (sPa * sPb) / (sPb - sPa)
    if sP >= sPb:
        return y / (sPb - sPa)
    return min(x * sP * sPb / (sPb - sP), y / (sP - sPa))


def swap_step(sP, L, amount_in_net, zero_for_one):
    """Single constant-L swap step. `amount_in_net` already has the fee removed.

    zero_for_one=True : token0 in, price DOWN -> returns (sP', token1_out).
    zero_for_one=False: token1 in, price UP   -> returns (sP', token0_out).
    """
    if zero_for_one:
        sP2 = L * sP / (L + amount_in_net * sP)
        return sP2, L * (sP - sP2)
    sP2 = sP + amount_in_net / L
    return sP2, L * (1 / sP - 1 / sP2)


def marginal_prices(P, gamma):
    """(bid, ask) marginal execution prices around mid P: bid=P*(1-gamma), ask=P/(1-gamma).

    The multiplicative half-spread is exactly the fee per side, independent of P (no gap).
    """
    return P * (1 - gamma), P / (1 - gamma)


def annualized_apr(daily_fee_usd, tvl_usd, days=1.0):
    """Simple (non-compounding) APR = fees/TVL annualized; 0 if TVL is non-positive."""
    if tvl_usd <= 0 or days <= 0:
        return 0.0
    return (daily_fee_usd / tvl_usd) * (365.0 / days)


# --- the stateful book -------------------------------------------------------

class Orderbook:
    """Event-replay virtual order book for one pool.

    Feed it mints/burns/swaps in (block, logIndex) order. Positions are keyed by tokenId; each
    swap moves spot and skims its fee into the in-range positions' separate counters (never into
    L). The book shape is derived on demand via book_at(); side (bid/ask) is read off current spot,
    so levels flip automatically as price moves.
    """

    def __init__(self, gamma, d0, d1):
        self.gamma = gamma
        self.d0, self.d1 = d0, d1
        self.positions = {}                 # tokenId -> {"L", "lo", "up"}
        self.net_by_tick = defaultdict(int)  # aggregate liquidityNet (baseline + tracked)
        self.baseline_net = defaultdict(int)  # the pre-existing aggregate (untracked) book
        self.tick = None
        self.active_L = None
        self.fees0 = defaultdict(float)     # tokensOwed0 per tokenId (token0 = USDC)
        self.fees1 = defaultdict(float)     # tokensOwed1 per tokenId (token1 = WETH)
        # daily aggregates
        self.volume0 = self.volume1 = 0.0
        self.fee0_total = self.fee1_total = 0.0
        self.fee_usd_total = 0.0
        self.swap_count = 0

    # --- liquidity events ---------------------------------------------------
    def set_baseline(self, net_by_tick):
        """Install the pre-existing aggregate book (one synthetic position; not per-LP)."""
        for tick, net in net_by_tick.items():
            self.baseline_net[int(tick)] += int(net)
            self.net_by_tick[int(tick)] += int(net)

    def apply_mint(self, token_id, lo, up, L):
        p = self.positions.setdefault(token_id, {"L": 0, "lo": int(lo), "up": int(up)})
        p["L"] += int(L)
        self.net_by_tick[int(lo)] += int(L)
        self.net_by_tick[int(up)] -= int(L)

    def apply_burn(self, token_id, lo, up, L):
        p = self.positions.setdefault(token_id, {"L": 0, "lo": int(lo), "up": int(up)})
        p["L"] -= int(L)
        self.net_by_tick[int(lo)] -= int(L)
        self.net_by_tick[int(up)] += int(L)

    # --- swap ---------------------------------------------------------------
    def apply_swap(self, side, amount0, amount1, price, tick, active_L):
        """Record a swap: move spot to `tick`, skim its fee into in-range positions' counters.

        side: "buy" = trader buys WETH (USDC/token0 in)  -> fee in token0
              "sell"= trader sells WETH (WETH/token1 in)  -> fee in token1
        amount0/amount1: absolute human token amounts (USDC, WETH); price: USDC per WETH at the swap.
        active_L: the pool's real in-range liquidity for this swap (swaps.csv `liquidity`); tracked
        positions take L_pos/active_L of the fee, the untracked baseline absorbs the rest.
        Single swaps that cross several initialized ticks are attributed at their end tick.
        """
        gross = amount0 if side == "buy" else amount1
        fee = gross * self.gamma
        self.volume0 += amount0
        self.volume1 += amount1
        self.swap_count += 1
        if side == "buy":
            self.fee0_total += fee
            self.fee_usd_total += fee                     # token0 = USDC ~ USD
        else:
            self.fee1_total += fee
            self.fee_usd_total += fee * price             # WETH fee -> USD at swap price

        if active_L and active_L > 0:
            for tid, p in self.positions.items():
                if p["L"] > 0 and p["lo"] <= tick < p["up"]:
                    share = p["L"] / active_L
                    if side == "buy":
                        self.fees0[tid] += fee * share
                    else:
                        self.fees1[tid] += fee * share
        self.tick = int(tick)
        self.active_L = active_L

    # --- derived views ------------------------------------------------------
    def book_at(self, boundaries, tick=None, include_baseline=True):
        """The virtual ladder at `tick` (default current spot) over the given boundary ticks.

        `boundaries` = ascending list of grid ticks (e.g. every initialized tick). Returns one band
        per [t_i, t_j] pair with its geometric-mean human price, side (bid/ask/straddle vs spot), the
        aggregate L2 sizes, and the per-tokenId L3 contributions (plus a "baseline" entry when the
        pre-existing book is included).
        """
        cur = self.tick if tick is None else int(tick)
        bands = []
        for t_i, t_j in zip(boundaries, boundaries[1:]):
            sP_i, sP_j = tick_to_sqrt_price(t_i), tick_to_sqrt_price(t_j)
            per_pos = {}
            for tid, p in self.positions.items():
                if p["L"] > 0 and p["lo"] <= t_i and p["up"] >= t_j:
                    q0, q1, _ = band_order(p["L"], sP_i, sP_j)
                    per_pos[tid] = {"q0": q0, "q1": q1}
            if include_baseline:
                bL = self._baseline_L_in_band(t_i)
                if bL > 0:
                    q0, q1, _ = band_order(bL, sP_i, sP_j)
                    per_pos["baseline"] = {"q0": q0, "q1": q1}
            if not per_pos:
                continue
            agg0 = sum(v["q0"] for v in per_pos.values())
            agg1 = sum(v["q1"] for v in per_pos.values())
            bands.append({
                "tick_lo": t_i, "tick_hi": t_j,
                "price": price_usdc_per_weth_from_tick((t_i + t_j) // 2, self.d0, self.d1),
                "side": self._side(t_i, t_j, cur),
                "agg_q0": agg0, "agg_q1": agg1, "positions": per_pos,
            })
        return bands

    def _baseline_L_in_band(self, tick):
        """Cumulative baseline liquidity active in the band starting at `tick` (step lookup)."""
        run = 0
        for t in sorted(self.baseline_net):
            if t <= tick:
                run += self.baseline_net[t]
            else:
                break
        return run

    @staticmethod
    def _side(t_i, t_j, cur):
        """Human-price side vs spot: lower ticks = higher USDC/WETH = ASK; higher ticks = BID."""
        if cur is None:
            return "na"
        if t_j <= cur:
            return "ask"      # whole band below current tick -> above spot price -> sell WETH
        if t_i >= cur:
            return "bid"      # whole band above current tick -> below spot price -> buy WETH
        return "straddle"

    def daily_stats(self, tvl_usd=None, active_tvl_usd=None, days=1.0):
        """Volume / fees / (optional) APRs after a full replay.

        APRs are computed only when the matching TVL is supplied (build_book passes them from
        tvl_series + the active-liquidity value); volume/fees are always available.
        """
        out = {
            "swap_count": self.swap_count,
            "volume_usdc": self.volume0, "volume_weth": self.volume1,
            "fee_usdc": self.fee0_total, "fee_weth": self.fee1_total,
            "fee_usd": self.fee_usd_total,
        }
        if tvl_usd is not None:
            out["tvl_usd"] = tvl_usd
            out["apr_total_tvl"] = annualized_apr(self.fee_usd_total, tvl_usd, days)
        if active_tvl_usd is not None:
            out["active_tvl_usd"] = active_tvl_usd
            out["apr_active_tvl"] = annualized_apr(self.fee_usd_total, active_tvl_usd, days)
        return out

    def active_band_value(self, boundaries, price, tick=None):
        """USD value of the in-range (at-the-money) liquidity: the reserves of the active L in the
        band straddling spot, valued at `price` (USDC per WETH). This is the denominator for
        apr_active_tvl — the capital actually earning fees right now, hence a much smaller base and
        a higher APR than total pool TVL."""
        cur = self.tick if tick is None else int(tick)
        if cur is None:
            return 0.0
        lo = max((t for t in boundaries if t <= cur), default=None)
        hi = min((t for t in boundaries if t > cur), default=None)
        if lo is None or hi is None:
            return 0.0
        L = self._active_L_at(lo)
        sP = tick_to_sqrt_price(cur)
        x, y = position_inventory(L, tick_to_sqrt_price(lo), tick_to_sqrt_price(hi), sP)
        return human0(x, self.d0) + human1(y, self.d1) * price   # USDC + WETH*price

    def _active_L_at(self, tick):
        run = 0
        for t in sorted(self.net_by_tick):
            if t <= tick:
                run += self.net_by_tick[t]
            else:
                break
        return run


# --- display helpers (native raw -> human) -----------------------------------

def price_usdc_per_weth_from_tick(tick, d0, d1):
    """Human USDC per WETH from a tick: 10**(d1-d0) / (1.0001**tick)."""
    return (10 ** (d1 - d0)) / (1.0001 ** tick)


def human0(raw_x, d0):
    """Raw token0 amount -> human (USDC)."""
    return raw_x / (10 ** d0)


def human1(raw_y, d1):
    """Raw token1 amount -> human (WETH)."""
    return raw_y / (10 ** d1)
