"""B1 — the live AMM order-book engine (ORDERS.md, applied to the game).

A *live* engine: unlike a backtest replay it COMPUTES output + new price from order arrival,
crossing ticks as needed (ORDERS.md §10), skimming the fee off the input (§5/§5A), and never
mutating any position's L (the book is a derived view, §9).

Representation — band-native. Because every position in the game is aligned to the pool's
`tickSpacing` (§1/§6 locked decisions), liquidity is stored as a per-band total `band_L[bt]`
(bt = the band's lower tick, a multiple of `spacing`). A position is a profile `{bt: L_i}`.
This makes arbitrary "curve" profiles and the cross-tick swap loop simple and exact, and the
active liquidity at spot is just `band_L[band(spot)]` — no liquidityNet/bitmap bookkeeping.

Token convention (§1): token0 = base = ETH0, token1 = quote = USD0, price P = USD0/ETH0 = D.
  buy  ETH0  = USD0 in  (token1 in)  -> price UP   -> zero_for_one = False
  sell ETH0  = ETH0 in  (token0 in)  -> price DOWN  -> zero_for_one = True
Fee is taken on the input token.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

LOG_1_0001 = math.log(1.0001)
_TINY = 1e-12


# ---- pure tick / price math ------------------------------------------------

def tick_to_sqrt_price(tick: float) -> float:
    return 1.0001 ** (tick / 2.0)


def price_to_tick(price: float) -> int:
    return math.floor(math.log(price) / LOG_1_0001)


def band_lower(tick: int, spacing: int) -> int:
    return (math.floor(tick / spacing)) * spacing


def band_edges_sqrt(bt: int, spacing: int) -> tuple[float, float]:
    return tick_to_sqrt_price(bt), tick_to_sqrt_price(bt + spacing)


def inventory(L: float, sPa: float, sPb: float, sP: float) -> tuple[float, float]:
    """Token holdings (x = ETH0, y = USD0) of liquidity L over [sPa, sPb] at spot sP (§3)."""
    if L <= 0:
        return 0.0, 0.0
    if sP <= sPa:
        return L * (1.0 / sPa - 1.0 / sPb), 0.0
    if sP >= sPb:
        return 0.0, L * (sPb - sPa)
    return L * (1.0 / sP - 1.0 / sPb), L * (sP - sPa)


def band_value_usd0(L: float, sPa: float, sPb: float, sP: float) -> float:
    x, y = inventory(L, sPa, sPb, sP)
    return x * (sP * sP) + y


# ---- results ----------------------------------------------------------------

@dataclass
class SwapResult:
    amount_out: float          # token received by the taker (USD0 if selling ETH0, else ETH0)
    amount_in: float           # input actually CONSUMED (gross, fee included); may be < requested
    fee: float                 # fee in the input token
    price: float               # new pool price
    fee_token0: bool           # True if the fee is denominated in ETH0 (a sell), else USD0
    fee_by_position: dict[int, float] = field(default_factory=dict)  # pid -> fee in input token


@dataclass
class Position:
    id: int
    owner: int
    pool: str
    kind: str                  # 'range' | 'curve'
    profile: dict[int, float]  # band lower tick -> L_i
    tick_lower: int
    tick_upper: int
    fees_eth0: float = 0.0     # cumulative fees attributed (for display; auto-collected by Pool)
    fees_usd0: float = 0.0


# ---- engine -----------------------------------------------------------------

class Engine:
    def __init__(self, spacing: int, gamma: float, init_price: float):
        self.spacing = spacing
        self.gamma = gamma
        self.sqrtP = math.sqrt(init_price)
        self._band = band_lower(price_to_tick(init_price), spacing)
        self.band_L: dict[int, float] = {}
        self.positions: dict[int, Position] = {}

    # --- price views ---
    def price(self) -> float:
        return self.sqrtP * self.sqrtP

    def active_L(self) -> float:
        return self.band_L.get(self._band, 0.0)

    def reserves(self) -> tuple[float, float]:
        x = y = 0.0
        for bt, L in self.band_L.items():
            if L <= 0:
                continue
            sPa, sPb = band_edges_sqrt(bt, self.spacing)
            dx, dy = inventory(L, sPa, sPb, self.sqrtP)
            x += dx
            y += dy
        return x, y

    def tvl_usd0(self) -> float:
        x, y = self.reserves()
        return x * self.price() + y

    def position_value_usd0(self, pid: int) -> float:
        p = self.positions[pid]
        v = 0.0
        for bt, L in p.profile.items():
            sPa, sPb = band_edges_sqrt(bt, self.spacing)
            v += band_value_usd0(L, sPa, sPb, self.sqrtP)
        return v

    def position_amounts(self, pid: int) -> tuple[float, float]:
        p = self.positions[pid]
        x = y = 0.0
        for bt, L in p.profile.items():
            sPa, sPb = band_edges_sqrt(bt, self.spacing)
            dx, dy = inventory(L, sPa, sPb, self.sqrtP)
            x += dx
            y += dy
        return x, y

    # --- deposit quoting (pure; no mutation) ---
    def _snap_range(self, tick_lower: int, tick_upper: int) -> tuple[int, int]:
        lo = band_lower(tick_lower, self.spacing)
        hi = band_lower(tick_upper - 1, self.spacing) + self.spacing
        if hi <= lo:
            hi = lo + self.spacing
        return lo, hi

    def _profile_amounts(self, profile_L: dict[int, float]) -> tuple[float, float, float]:
        x = y = 0.0
        for bt, L in profile_L.items():
            sPa, sPb = band_edges_sqrt(bt, self.spacing)
            dx, dy = inventory(L, sPa, sPb, self.sqrtP)
            x += dx
            y += dy
        return x, y, x * self.price() + y

    def quote_range(self, tick_lower: int, tick_upper: int, budget_usd0: float) -> dict:
        lo, hi = self._snap_range(tick_lower, tick_upper)
        bands = list(range(lo, hi, self.spacing))
        shape = {bt: 1.0 for bt in bands}
        return self._quote_shape(shape, budget_usd0, "range", lo, hi)

    def quote_curve(self, shape: dict[int, float], budget_usd0: float) -> dict:
        bands = {}
        for t, w in shape.items():
            if w is None:
                continue
            w = float(w)
            if w < 0:
                raise ValueError("curve profile must be non-negative")
            bt = band_lower(int(t), self.spacing)
            if w > 0:
                bands[bt] = bands.get(bt, 0.0) + w
        if not bands:
            raise ValueError("empty curve profile")
        lo, hi = min(bands), max(bands) + self.spacing
        return self._quote_shape(bands, budget_usd0, "curve", lo, hi)

    def _quote_shape(self, shape: dict[int, float], budget_usd0: float,
                     kind: str, lo: int, hi: int) -> dict:
        _, _, unit_value = self._profile_amounts(shape)
        if unit_value <= 0:
            raise ValueError("profile has no value at the current price")
        alpha = budget_usd0 / unit_value
        profile_L = {bt: w * alpha for bt, w in shape.items() if w > 0}
        x, y, value = self._profile_amounts(profile_L)
        return {"kind": kind, "profile": profile_L, "tick_lower": lo, "tick_upper": hi,
                "amount_eth0": x, "amount_usd0": y, "value_usd0": value}

    # --- mutations ---
    def add_position(self, pid: int, owner: int, pool: str, quote: dict) -> tuple[float, float]:
        profile_L = quote["profile"]
        for bt, L in profile_L.items():
            self.band_L[bt] = self.band_L.get(bt, 0.0) + L
        pos = Position(id=pid, owner=owner, pool=pool, kind=quote["kind"],
                       profile=dict(profile_L), tick_lower=quote["tick_lower"],
                       tick_upper=quote["tick_upper"])
        self.positions[pid] = pos
        x, y = self.position_amounts(pid)
        return x, y

    def remove_position(self, pid: int) -> tuple[float, float]:
        p = self.positions[pid]
        x, y = self.position_amounts(pid)
        for bt, L in p.profile.items():
            self.band_L[bt] = self.band_L.get(bt, 0.0) - L
            if abs(self.band_L[bt]) < _TINY:
                self.band_L.pop(bt, None)
        del self.positions[pid]
        return x, y

    # --- swap (the live cross-tick loop, ORDERS.md §10) ---
    def swap(self, zero_for_one: bool, amount_in_gross: float) -> SwapResult:
        gamma = self.gamma
        remaining = amount_in_gross
        out = 0.0
        fee_total = 0.0
        fee_by_pos: dict[int, float] = {}
        sP = self.sqrtP
        band = self._band
        guard = 0
        max_iter = 1_000_000
        while remaining > _TINY and guard < max_iter:
            guard += 1
            L = self.band_L.get(band, 0.0)
            if L <= 0:
                break  # empty region: no counterparty, price can't advance (§11). Halt.
            sPa, sPb = band_edges_sqrt(band, self.spacing)
            if zero_for_one:                      # price down toward sPa
                net_to_reach = L * (1.0 / sPa - 1.0 / sP)
            else:                                  # price up toward sPb
                net_to_reach = L * (sPb - sP)
            net_to_reach = max(net_to_reach, 0.0)
            gross_to_reach = net_to_reach / (1.0 - gamma) if net_to_reach > 0 else 0.0

            crossed = gross_to_reach > 0 and gross_to_reach <= remaining + _TINY
            if crossed:
                gross = gross_to_reach
                net = net_to_reach
                new_sP = sPa if zero_for_one else sPb
            else:
                gross = remaining
                net = gross * (1.0 - gamma)
                if zero_for_one:
                    new_sP = 1.0 / (1.0 / sP + net / L)
                else:
                    new_sP = sP + net / L

            if zero_for_one:
                step_out = L * (sP - new_sP)       # USD0 out
            else:
                step_out = L * (1.0 / sP - 1.0 / new_sP)  # ETH0 out
            step_out = max(step_out, 0.0)
            fee = gross - net

            self._accrue_fee(band, L, fee, zero_for_one, fee_by_pos)
            out += step_out
            fee_total += fee
            remaining -= gross
            sP = new_sP

            if crossed:
                band = band - self.spacing if zero_for_one else band + self.spacing
            else:
                break

        self.sqrtP = sP
        self._band = band
        consumed = amount_in_gross - remaining
        return SwapResult(amount_out=out, amount_in=consumed, fee=fee_total, price=self.price(),
                          fee_token0=zero_for_one, fee_by_position=fee_by_pos)

    def _accrue_fee(self, band: int, L: float, fee: float, token0: bool,
                    sink: dict[int, float]) -> None:
        if fee <= 0 or L <= 0:
            return
        for p in self.positions.values():
            Li = p.profile.get(band, 0.0)
            if Li <= 0:
                continue
            share = fee * (Li / L)
            if token0:
                p.fees_eth0 += share
            else:
                p.fees_usd0 += share
            sink[p.id] = sink.get(p.id, 0.0) + share

    # --- level-3 / level-2 snapshot for the LP-detail view ---
    def book(self) -> list[dict]:
        """Per active band: edges, mid price, side vs spot, and per-position order sizes.

        side: 'bid' (band below spot — pool buys ETH0), 'ask' (above), 'straddle' (contains spot).
        """
        rows = []
        P = self.price()
        for bt in sorted(self.band_L):
            L = self.band_L[bt]
            if L <= 0:
                continue
            sPa, sPb = band_edges_sqrt(bt, self.spacing)
            mid = (sPa * sPb)            # geometric-mean fill price (sqrt(P_i*P_j))
            if sPb <= self.sqrtP:
                side = "bid"
            elif sPa >= self.sqrtP:
                side = "ask"
            else:
                side = "straddle"
            orders = []
            for p in self.positions.values():
                Li = p.profile.get(bt, 0.0)
                if Li <= 0:
                    continue
                q0 = Li * (1.0 / sPa - 1.0 / sPb)   # ETH0 size of the band order
                q1 = Li * (sPb - sPa)               # USD0 size
                orders.append({"position_id": p.id, "owner": p.owner, "L": Li,
                               "q_eth0": q0, "q_usd0": q1})
            rows.append({"tick_lower": bt, "tick_upper": bt + self.spacing,
                         "price": mid, "side": side, "L": L,
                         "depth_eth0": L * (1.0 / sPa - 1.0 / sPb),
                         "orders": orders})
        return rows
