"""B3 — game core: Account, Pool, Oracle, and the Game state machine (APP_PLAN.md §4).

State is rebuilt by replaying the action log (B2): every mutation goes through `_apply`, which is
the single function used by both the live path (validate → append → apply → ack) and replay
(read log → apply). Determinism: the oracle is seeded and re-draws identically on replay.

Balances are `Decimal` (exact ledger); the engine works in float. Conservation (§4.5) is a
*detector*: cheap pre-checks reject bad actions before mutation; the invariant is asserted after
and, on drift beyond a generous tolerance, raises an ALERT but never halts the game (§7).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from decimal import Decimal, getcontext
from typing import Optional

from . import config
from .engine import Engine
from .persistence import Store, LogEntry

getcontext().prec = 40
D = Decimal


def dec(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(repr(x)) if isinstance(x, float) else Decimal(str(x))


class GameError(Exception):
    """A clean, expected rejection (insufficient funds, wrong phase, …). Never an alert."""


# ---------------------------------------------------------------------------

@dataclass
class Account:
    id: int
    name: str
    is_house: bool = False
    is_admin: bool = False
    balance_usd0: Decimal = field(default_factory=lambda: D(0))
    balance_eth0: Decimal = field(default_factory=lambda: D(0))
    fees_usd0: Decimal = field(default_factory=lambda: D(0))      # cumulative, USD0-equiv
    taker_volume: Decimal = field(default_factory=lambda: D(0))
    maker_volume: Decimal = field(default_factory=lambda: D(0))

    def credit(self, usd0: Decimal = D(0), eth0: Decimal = D(0)) -> None:
        self.balance_usd0 += usd0
        self.balance_eth0 += eth0

    def debit(self, usd0: Decimal = D(0), eth0: Decimal = D(0)) -> None:
        if usd0 > self.balance_usd0 + D("1e-9") or eth0 > self.balance_eth0 + D("1e-9"):
            raise GameError("insufficient balance")
        self.balance_usd0 -= usd0
        self.balance_eth0 -= eth0

    def portfolio_value(self, d: float, position_value: float = 0.0) -> float:
        return float(self.balance_usd0) + float(self.balance_eth0) * d + position_value


class Oracle:
    """Lognormal random walk, seeded for replay (§4.3). sigma/mu are per step."""

    def __init__(self, seed: int, sigma: float, mu: float):
        self.rng = random.Random(seed)
        self.sigma = sigma
        self.mu = mu

    def step(self, d_prev: float) -> float:
        z = self.rng.gauss(0.0, 1.0)
        return d_prev * math.exp(self.mu + self.sigma * z)


class Pool:
    """Wraps one Engine + fee auto-collect + maker-volume attribution."""

    def __init__(self, label: str, params: config.GameParams):
        self.label = label
        self.gamma = params.fee
        self.engine = Engine(params.tick_spacing, params.fee, params.d0)
        self.size_cap_frac = params.size_cap_frac
        self.seed_pid: Optional[int] = None

    def price(self) -> float:
        return self.engine.price()

    def tvl_usd0(self) -> float:
        return self.engine.tvl_usd0()

    def position_value_at_d(self, pid: int, d: float) -> float:
        x, y = self.engine.position_amounts(pid)
        return x * d + y

    # --- actions ---
    def swap(self, accounts: dict[int, Account], trader: Account, side: str,
             amount_in: float) -> dict:
        zero_for_one = side == "sell"            # sell ETH0 (token0 in)
        rx, ry = self.engine.reserves()
        reserve_in = rx if zero_for_one else ry
        if self.size_cap_frac > 0 and amount_in > reserve_in * self.size_cap_frac:
            raise GameError(f"trade exceeds size cap ({self.size_cap_frac:.0%} of pool reserves)")
        # balance pre-check (input token)
        if zero_for_one and dec(amount_in) > trader.balance_eth0 + D("1e-9"):
            raise GameError("insufficient ETH0")
        if not zero_for_one and dec(amount_in) > trader.balance_usd0 + D("1e-9"):
            raise GameError("insufficient USD0")

        r = self.engine.swap(zero_for_one, amount_in)
        price = self.engine.price()
        if zero_for_one:
            trader.debit(eth0=dec(r.amount_in))
            trader.credit(usd0=dec(r.amount_out))
            notional = r.amount_out                 # USD0 leg
        else:
            trader.debit(usd0=dec(r.amount_in))
            trader.credit(eth0=dec(r.amount_out))
            notional = r.amount_in
        trader.taker_volume += dec(notional)

        # auto-collect fees to LP owners (fee is in the input token) + maker volume
        total_fee = r.fee or 0.0
        for pid, fee_share in r.fee_by_position.items():
            pos = self.engine.positions.get(pid)
            owner = accounts.get(pos.owner) if pos else None
            if owner is None:
                continue
            if zero_for_one:
                owner.credit(eth0=dec(fee_share))
                owner.fees_usd0 += dec(fee_share * price)
            else:
                owner.credit(usd0=dec(fee_share))
                owner.fees_usd0 += dec(fee_share)
            if total_fee > 0:
                owner.maker_volume += dec(notional * (fee_share / total_fee))
        return {"side": side, "amount_in": r.amount_in, "amount_out": r.amount_out,
                "fee": r.fee, "price": price, "consumed_all": r.amount_in >= amount_in - 1e-9}

    def deposit(self, game: "Game", owner: Account, kind: str, payload: dict) -> dict:
        budget = float(payload.get("budget_usd0", 0))
        if budget <= 0:
            raise GameError("budget must be positive")
        if kind == "range":
            q = self.engine.quote_range(int(payload["tick_lower"]), int(payload["tick_upper"]), budget)
        elif kind == "curve":
            shape = {int(k): float(v) for k, v in (payload.get("profile") or {}).items()}
            q = self.engine.quote_curve(shape, budget)
        else:
            raise GameError(f"unknown deposit kind {kind}")
        need_eth0, need_usd0 = dec(q["amount_eth0"]), dec(q["amount_usd0"])
        if need_eth0 > owner.balance_eth0 + D("1e-9") or need_usd0 > owner.balance_usd0 + D("1e-9"):
            raise GameError("insufficient balance for this deposit")
        pid = game.next_pid()
        x, y = self.engine.add_position(pid, owner.id, self.label, q)
        owner.debit(eth0=dec(x), usd0=dec(y))
        return {"position_id": pid, "amount_eth0": x, "amount_usd0": y,
                "tick_lower": q["tick_lower"], "tick_upper": q["tick_upper"], "kind": kind}

    def withdraw(self, owner: Account, pid: int) -> dict:
        pos = self.engine.positions.get(pid)
        if pos is None or pos.owner != owner.id:
            raise GameError("no such position")
        x, y = self.engine.remove_position(pid)
        owner.credit(eth0=dec(x), usd0=dec(y))
        return {"position_id": pid, "amount_eth0": x, "amount_usd0": y}

    def benchmark(self, d: float) -> float:
        """Live value of the house seed normalized to one starting bag (§2)."""
        if self.seed_pid is None or self.seed_pid not in self.engine.positions:
            return 0.0
        pos = self.engine.positions[self.seed_pid]
        val = self.position_value_at_d(self.seed_pid, d)
        fees = float(pos.fees_usd0) + float(pos.fees_eth0) * d
        return val + fees  # caller divides by k


# ---------------------------------------------------------------------------

class Game:
    HOUSE_ID = 0

    def __init__(self, store: Store, params: config.GameParams):
        self.store = store
        self.params = params
        self.accounts: dict[int, Account] = {}
        self.pools = {"v3": Pool("v3", params), "curve": Pool("curve", params)}
        self.oracle = Oracle(params.oracle_seed, params.sigma, params.mu)
        self.d = params.d0
        self.step = 0
        self.phase = "LOBBY"
        self._next_account_id = 1
        self._next_pid_counter = 1
        self._expected_eth0 = D(0)
        self._expected_usd0 = D(0)
        self.alerts: list[str] = []
        self.total_steps = max(1, round(params.game_length / params.walk_step))

    # --- ids ---
    def next_pid(self) -> int:
        pid = self._next_pid_counter
        self._next_pid_counter += 1
        return pid

    # --- conservation (detector, never halts) ---
    def conservation_drift(self) -> tuple[float, float]:
        eth0 = sum((a.balance_eth0 for a in self.accounts.values()), D(0))
        usd0 = sum((a.balance_usd0 for a in self.accounts.values()), D(0))
        for p in self.pools.values():
            rx, ry = p.engine.reserves()
            eth0 += dec(rx)
            usd0 += dec(ry)
        return float(eth0 - self._expected_eth0), float(usd0 - self._expected_usd0)

    def _check_conservation(self, where: str) -> None:
        de, du = self.conservation_drift()
        tol_e = config.CONSERVATION_EPS + 1e-6 * (abs(float(self._expected_eth0)) + 1)
        tol_u = config.CONSERVATION_EPS + 1e-6 * (abs(float(self._expected_usd0)) + 1)
        if abs(de) > tol_e or abs(du) > tol_u:
            msg = f"CONSERVATION drift at {where}: dETH0={de:.6g} dUSD0={du:.6g}"
            self.alerts.append(msg)
            try:
                self.store.append("alert", None, {"where": where, "d_eth0": de, "d_usd0": du}, ts=0.0)
            except Exception:
                pass
            # never raise: degrade gracefully (§7)

    # --- the single mutator (live + replay) ---
    def _apply(self, kind: str, account_id: Optional[int], payload: dict) -> dict:
        if kind == "register":
            return self._do_register(account_id, payload)
        if kind == "start":
            return self._do_start(payload)
        if kind == "oracle":
            return self._do_oracle(payload)
        if kind in ("buy", "sell"):
            return self._do_trade(account_id, kind, payload)
        if kind == "deposit":
            return self._do_deposit(account_id, payload)
        if kind == "withdraw":
            return self._do_withdraw(account_id, payload)
        if kind == "name":
            acc = self.accounts[account_id]
            acc.name = payload["new_name"]
            return {"name": acc.name}
        if kind == "alert":
            return {}
        raise GameError(f"unknown action {kind}")

    # --- effects ---
    def _do_register(self, account_id: Optional[int], payload: dict) -> dict:
        aid = account_id or self._next_account_id
        self._next_account_id = max(self._next_account_id, aid + 1)
        x = self.params.x
        usd0 = D(repr(x / 2.0))
        eth0 = D(repr((x / 2.0) / self.params.d0))
        acc = Account(id=aid, name=payload["name"], is_admin=bool(payload.get("is_admin")),
                      balance_usd0=usd0, balance_eth0=eth0)
        self.accounts[aid] = acc
        self._expected_usd0 += usd0
        self._expected_eth0 += eth0
        return {"account_id": aid, "balance_usd0": float(usd0), "balance_eth0": float(eth0)}

    def _do_start(self, payload: dict) -> dict:
        if self.phase != "LOBBY":
            return {"phase": self.phase}
        house = Account(id=self.HOUSE_ID, name="House", is_house=True)
        self.accounts[self.HOUSE_ID] = house
        seed_value = self.params.k * self.params.x
        # quote each pool's wide (v2-like) seed first, then fund the house with EXACTLY what the
        # two boxcars need (the ETH0/USD0 split is not 50/50 and differs per pool).
        rf = self.params.range_factor
        quotes = {}
        need_x = need_y = 0.0
        for label, pool in self.pools.items():
            t_lo = self._tick(self.params.d0 / rf)
            t_hi = self._tick(self.params.d0 * rf)
            q = pool.engine.quote_range(t_lo, t_hi, seed_value)
            quotes[label] = q
            need_x += q["amount_eth0"]
            need_y += q["amount_usd0"]
        house.balance_eth0 = dec(need_x)
        house.balance_usd0 = dec(need_y)
        self._expected_eth0 += house.balance_eth0
        self._expected_usd0 += house.balance_usd0
        for label, pool in self.pools.items():
            pid = self.next_pid()
            x, y = pool.engine.add_position(pid, self.HOUSE_ID, label, quotes[label])
            house.debit(eth0=dec(x), usd0=dec(y))
            pool.seed_pid = pid
        self.phase = "RUNNING"
        return {"phase": "RUNNING", "seed_value": seed_value}

    def _do_oracle(self, payload: dict) -> dict:
        self.d = self.oracle.step(self.d)        # deterministic from the seed (re-draws on replay)
        self.step += 1
        if self.step >= self.total_steps:
            self.phase = "SETTLED"
        elif self.step >= self.total_steps - self.params.settlement_freeze_steps:
            self.phase = "FREEZE"
        return {"step": self.step, "d": self.d, "phase": self.phase}

    def _require_running(self):
        if self.phase != "RUNNING":
            raise GameError(f"trading is {self.phase.lower()}, not open")

    def _do_trade(self, account_id, kind, payload) -> dict:
        self._require_running()
        acc = self.accounts[account_id]
        pool = self.pools[payload["pool"]]
        amount_in = float(payload["amount_in"])
        if amount_in <= 0:
            raise GameError("amount must be positive")
        return pool.swap(self.accounts, acc, kind, amount_in)

    def _do_deposit(self, account_id, payload) -> dict:
        self._require_running()
        acc = self.accounts[account_id]
        pool = self.pools[payload["pool"]]
        return pool.deposit(self, acc, payload.get("kind", "range"), payload)

    def _do_withdraw(self, account_id, payload) -> dict:
        # withdraw allowed in RUNNING and FREEZE? Keep to RUNNING for simplicity of settlement.
        self._require_running()
        acc = self.accounts[account_id]
        pool = self.pools[payload["pool"]]
        return pool.withdraw(acc, int(payload["position_id"]))

    def _tick(self, price: float) -> int:
        from .engine import price_to_tick
        return price_to_tick(price)

    # --- live entry points (validate -> append -> apply -> ack) ---
    def register(self, name: str, is_admin: bool = False, ts: float = 0.0) -> dict:
        if self.phase not in ("LOBBY", "RUNNING"):
            raise GameError("registration is closed")
        if any(a.name == name for a in self.accounts.values()):
            raise GameError("name taken")
        payload = {"name": name, "is_admin": is_admin}
        aid = self._next_account_id
        payload_with_id = dict(payload)
        self.store.append("register", aid, payload_with_id, ts=ts)
        res = self._apply("register", aid, payload_with_id)
        self._sync_projection(aid)
        return res

    def start(self, ts: float = 0.0) -> dict:
        self.store.append("start", None, {}, ts=ts)
        res = self._apply("start", None, {})
        self.store.set_meta("phase", self.phase)
        self._check_conservation("start")
        return res

    def tick(self, ts: float = 0.0) -> dict:
        self.store.append("oracle", None, {"step": self.step + 1}, ts=ts)
        res = self._apply("oracle", None, {})
        self.store.record_oracle_tick(self.step, ts, self.d)
        self.store.set_meta("phase", self.phase)
        return res

    def act(self, account_id: int, kind: str, payload: dict, ts: float = 0.0) -> dict:
        if account_id not in self.accounts:
            raise GameError("unknown account")
        full = dict(payload)
        # cheap pre-validation FIRST (no mutation): a clean rejection appends nothing (§7).
        self._validate(kind, account_id, full)
        self.store.append(kind, account_id, full, ts=ts)
        res = self._apply(kind, account_id, full)
        self._sync_projection(account_id)
        self._check_conservation(kind)
        return res

    def _validate(self, kind: str, account_id: int, payload: dict) -> None:
        """Pure pre-checks; raise GameError on a clean rejection without touching state."""
        acc = self.accounts[account_id]
        pool = self.pools.get(payload.get("pool"))
        if pool is None:
            raise GameError("unknown pool")
        if self.phase != "RUNNING":
            raise GameError(f"trading is {self.phase.lower()}, not open")
        if kind in ("buy", "sell"):
            amt = float(payload.get("amount_in", 0))
            if amt <= 0:
                raise GameError("amount must be positive")
            rx, ry = pool.engine.reserves()
            if kind == "sell":
                if dec(amt) > acc.balance_eth0 + D("1e-9"):
                    raise GameError("insufficient ETH0")
                if pool.size_cap_frac > 0 and amt > rx * pool.size_cap_frac:
                    raise GameError(f"trade exceeds size cap ({pool.size_cap_frac:.0%} of reserves)")
            else:
                if dec(amt) > acc.balance_usd0 + D("1e-9"):
                    raise GameError("insufficient USD0")
                if pool.size_cap_frac > 0 and amt > ry * pool.size_cap_frac:
                    raise GameError(f"trade exceeds size cap ({pool.size_cap_frac:.0%} of reserves)")
        elif kind == "deposit":
            budget = float(payload.get("budget_usd0", 0))
            if budget <= 0:
                raise GameError("budget must be positive")
            knd = payload.get("kind", "range")
            if knd == "range":
                q = pool.engine.quote_range(int(payload["tick_lower"]), int(payload["tick_upper"]), budget)
            elif knd == "curve":
                shape = {int(k): float(v) for k, v in (payload.get("profile") or {}).items()}
                q = pool.engine.quote_curve(shape, budget)
            else:
                raise GameError(f"unknown deposit kind {knd}")
            if dec(q["amount_eth0"]) > acc.balance_eth0 + D("1e-9") or \
               dec(q["amount_usd0"]) > acc.balance_usd0 + D("1e-9"):
                raise GameError("insufficient balance for this deposit")
        elif kind == "withdraw":
            pid = int(payload.get("position_id", -1))
            pos = pool.engine.positions.get(pid)
            if pos is None or pos.owner != account_id:
                raise GameError("no such position")

    def name_change(self, account_id: int, new_name: str, ts: float = 0.0) -> dict:
        if any(a.name == new_name for a in self.accounts.values()):
            raise GameError("name taken")
        self.store.append("name", account_id, {"new_name": new_name}, ts=ts)
        return self._apply("name", account_id, {"new_name": new_name})

    # --- projections ---
    def _sync_projection(self, account_id: int) -> None:
        a = self.accounts.get(account_id)
        if not a:
            return
        try:
            self.store.upsert_account(
                id=a.id, name=a.name, is_house=int(a.is_house), is_admin=int(a.is_admin),
                balance_usd0=a.balance_usd0, balance_eth0=a.balance_eth0, fees_usd0=a.fees_usd0,
                taker_volume=a.taker_volume, maker_volume=a.maker_volume)
        except Exception:
            pass

    # --- read models ---
    def positions_of(self, account_id: int, pool_label: str) -> list:
        eng = self.pools[pool_label].engine
        return [p for p in eng.positions.values() if p.owner == account_id]

    def leaderboard(self) -> list[dict]:
        rows = []
        for a in self.accounts.values():
            if a.is_house or a.is_admin:
                continue
            pv = 0.0
            for label, pool in self.pools.items():
                for p in self.positions_of(a.id, label):
                    pv += pool.position_value_at_d(p.id, self.d)
            rows.append({
                "account_id": a.id, "name": a.name, "is_house": False,
                "total_value_usd0": a.portfolio_value(self.d, pv),
                "balance_usd0": float(a.balance_usd0), "balance_eth0": float(a.balance_eth0),
                "fees_usd0": float(a.fees_usd0), "taker_volume_usd0": float(a.taker_volume),
                "maker_volume_usd0": float(a.maker_volume)})
        rows.sort(key=lambda r: r["total_value_usd0"], reverse=True)
        # house benchmark rows (non-winning)
        for label, pool in self.pools.items():
            bench = pool.benchmark(self.d) / self.params.k if self.params.k else 0.0
            rows.append({"account_id": None, "name": f"House (seed, {label})", "is_house": True,
                         "total_value_usd0": bench, "balance_usd0": 0.0, "balance_eth0": 0.0,
                         "fees_usd0": 0.0, "taker_volume_usd0": 0.0, "maker_volume_usd0": 0.0})
        return rows

    def clock(self) -> dict:
        elapsed = self.step * self.params.walk_step
        remaining = max(0.0, self.params.game_length - elapsed)
        return {"phase": self.phase, "elapsed": elapsed, "remaining": remaining, "step": self.step}

    # --- replay / load ---
    @classmethod
    def load(cls, store: Store, params: config.GameParams) -> "Game":
        g = cls(store, params)
        store.reset_projections()

        def apply(entry: LogEntry):
            try:
                g._apply(entry.kind, entry.account_id, entry.payload)
                if entry.kind == "oracle":
                    store.record_oracle_tick(g.step, entry.ts, g.d)
            except GameError:
                pass  # tolerate (shouldn't happen for logged actions)

        from .persistence import replay
        replay(store, apply)
        for aid in g.accounts:
            g._sync_projection(aid)
        store.set_meta("phase", g.phase)
        g._check_conservation("load")
        return g
