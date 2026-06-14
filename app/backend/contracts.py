"""S0 — frozen wire contract (REST + WebSocket), the single source of truth shared by the
backend and the frontend. Both sides validate against these shapes (APP_PLAN.md §8).

Kept intentionally light: action payloads are typed where it matters and otherwise free-form
dicts so the four action kinds share one endpoint.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

PoolId = Literal["v3", "curve"]
ActionType = Literal["buy", "sell", "deposit", "withdraw"]
Phase = Literal["LOBBY", "RUNNING", "FREEZE", "SETTLED"]


# ---- REST: auth ----
class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class LoginRequest(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class AuthResponse(BaseModel):
    account_id: int
    name: str
    is_admin: bool = False
    balance_usd0: float
    balance_eth0: float


# ---- REST: actions ----
class ActionRequest(BaseModel):
    type: ActionType
    pool: PoolId
    # buy/sell:    {"amount_in": float}          (exact-input; buy spends USD0, sell sells ETH0)
    # deposit v3:  {"tick_lower": int, "tick_upper": int, "budget_usd0": float}
    # deposit curve: {"profile": {tick:int -> weight:float>=0}, "budget_usd0": float}
    # withdraw:    {"position_id": int}
    payload: dict[str, Any] = Field(default_factory=dict)


class ActionResult(BaseModel):
    ok: bool
    type: ActionType
    pool: PoolId
    detail: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None


# ---- REST: state / read models ----
class PositionView(BaseModel):
    position_id: int
    pool: PoolId
    kind: Literal["range", "curve"]
    tick_lower: Optional[int] = None
    tick_upper: Optional[int] = None
    value_usd0: float
    fees_usd0: float


class PoolView(BaseModel):
    pool: PoolId
    price: float                 # internal pool price (USD0/ETH0)
    tvl_usd0: float
    your_fees_usd0: float = 0.0
    your_positions: list[PositionView] = Field(default_factory=list)


class ClockView(BaseModel):
    phase: Phase
    elapsed: float
    remaining: float
    step: int


class StateResponse(BaseModel):
    account: Optional[AuthResponse] = None
    d: float                     # external price
    pools: list[PoolView]
    clock: ClockView


class LeaderRow(BaseModel):
    account_id: Optional[int] = None
    name: str
    is_house: bool = False
    total_value_usd0: float
    balance_usd0: float
    balance_eth0: float
    fees_usd0: float
    taker_volume_usd0: float
    maker_volume_usd0: float


class LeaderboardResponse(BaseModel):
    rows: list[LeaderRow]
    d: float


# ---- WebSocket envelope ----
WSType = Literal["d_tick", "pool", "clock", "leaderboard", "phase", "hello"]


class WSMessage(BaseModel):
    type: WSType
    data: dict[str, Any] = Field(default_factory=dict)


def ws(type_: WSType, **data: Any) -> dict:
    """Build a validated WS frame as a plain dict ready for `send_json`."""
    return WSMessage(type=type_, data=data).model_dump()


# ---- Admin ----
class AdminAuth(BaseModel):
    name: str
    password: str


class AdminParamsRequest(AdminAuth):
    params: dict[str, Any] = Field(default_factory=dict)
