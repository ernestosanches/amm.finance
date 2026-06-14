"""Multiplayer AMM game — backend package.

Stages (see ../../APP_PLAN.md §13):
  engine.py       B1  live cross-tick (band-native) AMM order-book engine
  persistence.py  B2  SQLite WAL action log (source of truth) + replay
  game.py         B3  Account / Pool / Oracle / Game state machine
  api.py          B4  FastAPI REST + WebSocket, serves the frontend
  config.py       S0  default game parameters (§10)
  contracts.py    S0  frozen REST/WS wire contract (Pydantic)
"""
