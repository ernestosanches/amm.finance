"""S0 — default game parameters (APP_PLAN.md §10) and fixed conventions (§1).

Tokens are virtual: base = ETH0, quote = USD0, and the engine price is the external price
directly (`P == D`, USD0 per ETH0). No decimal scaling. Balances are kept as Decimal at the
ledger layer; the engine works in float internally.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, asdict, field

# Conservation invariant tolerance (§7): generous — well above float-rounding noise so minor
# drift never trips it. A breach beyond this rejects ONE action; it never halts the game.
CONSERVATION_EPS = 1e-3

TICK_SPACING_DEFAULT = 60  # 0.30% fee tier (fee tier fixes spacing: 1/10/60/200)


@dataclass
class GameParams:
    # token display names (roles fixed: base=ETH0 volatile, quote=USD0 numeraire)
    base_symbol: str = "ETH0"
    quote_symbol: str = "USD0"

    # oracle (lognormal random walk, §4.3)
    d0: float = 3000.0           # initial external price USD0/ETH0 (admin-entered)
    sigma: float = 0.0045        # per-step stdev of log-return (~12%/hr at 5s steps)
    walk_step: float = 5.0       # seconds per oracle step
    oracle_seed: int = 1         # seeded for replay

    # pool / liquidity
    fee: float = 0.003           # gamma, per pool (0.30%)
    tick_spacing: int = TICK_SPACING_DEFAULT
    # finite tick range for the band grid (§1): D in [d0/range_factor, d0*range_factor]
    range_factor: float = 10.0

    # economy
    k: float = 3.0               # house seed = k * X per pool (benchmark depth)
    x: float = 10000.0           # player starting bag total value (USD0)

    # lifecycle
    game_length: float = 3600.0  # seconds; autostop RUNNING->FREEZE->SETTLED
    settlement_freeze_steps: int = 1

    # guards
    size_cap_frac: float = 0.10  # max trade as fraction of pool reserves; <=0 disables (default ON)

    @property
    def mu(self) -> float:
        # mean-preserving drift so E[D_t] = D0
        return -0.5 * self.sigma * self.sigma

    def to_dict(self) -> dict:
        d = asdict(self)
        d["mu"] = self.mu
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "GameParams":
        fields = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (d or {}).items() if k in fields})


# Admin gate (§9). The one real privilege boundary. The password is taken from AMM_ADMIN_PASSWORD
# if set, otherwise a fresh random one is GENERATED per server run and printed at startup — no
# hardcoded default, so a public tunnel never ships a known password.
ADMIN_NAME = os.environ.get("AMM_ADMIN_NAME", "admin")
ADMIN_PASSWORD = os.environ.get("AMM_ADMIN_PASSWORD")  # None -> server generates one


def generate_admin_password() -> str:
    return secrets.token_urlsafe(9)

DB_PATH = os.environ.get("AMM_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "data", "game.db"))
