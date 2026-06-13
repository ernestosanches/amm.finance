#!/usr/bin/env python3
"""Stage 6.2.0 — shared pool-metadata loader (stdlib only).

`pool_metadata.csv` (written by `link_positions.py` from on-chain `fee()` / `tickSpacing()` /
token decimals & symbols) is the single source of truth for the pipeline's pool-specific
constants. Each consumer reads it via `load()`; when it is absent — e.g. a stage run standalone
before Stage 6.0 — callers fall back to their own `--d0/--d1` / hardcoded defaults via the
`decimals()` / `symbols()` helpers, so nothing breaks and the pipeline stays pool-agnostic.
"""
import csv
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def load(here=HERE, name="pool_metadata.csv"):
    """Return the pool metadata dict (typed), or None if the CSV is absent/empty."""
    path = os.path.join(here, name)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return None
    r = rows[0]
    return {
        "pool": r["pool"], "token0": r["token0"], "token1": r["token1"],
        "symbol0": r["symbol0"], "symbol1": r["symbol1"],
        "decimals0": int(r["decimals0"]), "decimals1": int(r["decimals1"]),
        "fee": int(r["fee"]), "gamma": float(r["gamma"]),
        "tickSpacing": int(r["tickSpacing"]),
    }


def decimals(meta, d0_default=6, d1_default=18):
    """(decimals0, decimals1) from metadata, or the supplied fallbacks when metadata is absent."""
    return (meta["decimals0"], meta["decimals1"]) if meta else (d0_default, d1_default)


def symbols(meta, s0_default="USDC", s1_default="WETH"):
    """(symbol0, symbol1) from metadata, or the supplied fallbacks when metadata is absent."""
    return (meta["symbol0"], meta["symbol1"]) if meta else (s0_default, s1_default)
