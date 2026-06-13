#!/usr/bin/env python3
"""Stage 4.1 — absolute start-of-range L2 liquidity snapshot (keyless, no history parsing).

Reads the pool's standing liquidity-per-tick directly from contract state at the start block:
  slot0()           -> current tick
  liquidity()       -> active in-range L (anchor for the self-check)
  tickBitmap(word)  -> which ticks are initialized
  ticks(tick)       -> liquidityNet, the absolute ±L boundary delta at each initialized tick

Cumulating liquidityNet across ticks reconstructs absolute active L per band. We self-check that the
cumulative value at the current tick equals on-chain liquidity(). All tick reads are batched through
Multicall3 (~a handful of eth_calls instead of ~800). This is AGGREGATE L2 only — no individual
old-position identity (that needs full history / NFT decoding).

Output: initial_liquidity.csv  (tick, liquidity_net, cumulative_liquidity) — same shape as Stage 2's
liquidity_distribution.csv, but ABSOLUTE, so Stages 3/4 can consume it the same way.

Optional, enabled by default: pass --no-initial-liquidity to skip the on-chain reads (downstream then
falls back to the relative / 0-baseline behaviour).

Usage:
  python tick_snapshot.py --start 2026-06-12
  python tick_snapshot.py --no-initial-liquidity     # skip (no file written)
"""
import argparse
import csv
import os

from eth_abi import decode, encode
from web3 import Web3

from usage import UsageCounter
from uniswap_v3_pool_download_rpc import block_by_timestamp, to_unix

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RPC = "https://ethereum-rpc.publicnode.com"
DEFAULT_POOL = "0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8"
MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"
MIN_TICK, MAX_TICK = -887272, 887272

POOL_ABI = [
    {"name": "slot0", "inputs": [], "stateMutability": "view", "type": "function",
     "outputs": [{"type": "uint160"}, {"type": "int24"}, {"type": "uint16"}, {"type": "uint16"},
                 {"type": "uint16"}, {"type": "uint8"}, {"type": "bool"}]},
    {"name": "liquidity", "inputs": [], "outputs": [{"type": "uint128"}],
     "stateMutability": "view", "type": "function"},
    {"name": "tickSpacing", "inputs": [], "outputs": [{"type": "int24"}],
     "stateMutability": "view", "type": "function"},
]
MC_ABI = [{"name": "aggregate3", "stateMutability": "view", "type": "function",
           "inputs": [{"name": "calls", "type": "tuple[]", "components": [
               {"name": "target", "type": "address"},
               {"name": "allowFailure", "type": "bool"},
               {"name": "callData", "type": "bytes"}]}],
           "outputs": [{"name": "r", "type": "tuple[]", "components": [
               {"name": "success", "type": "bool"},
               {"name": "returnData", "type": "bytes"}]}]}]
TICKS_RET = ["uint128", "int128", "uint256", "uint256", "int56", "uint160", "uint32", "bool"]


# --- pure functions (unit-tested) --------------------------------------------

def word_range(spacing):
    """Inclusive [min_word, max_word] of tickBitmap words covering the full tick space."""
    return (MIN_TICK // spacing) >> 8, (MAX_TICK // spacing) >> 8


def ticks_in_word(word, bitmap, spacing):
    """Ticks initialized in one bitmap word: bit i set -> tick (word*256 + i) * spacing."""
    return [(word * 256 + i) * spacing for i in range(256) if (bitmap >> i) & 1]


def build_distribution(net_by_tick):
    """Ascending rows with cumulative sum of liquidityNet (= absolute active L per band)."""
    rows, running = [], 0
    for tick in sorted(net_by_tick):
        running += net_by_tick[tick]
        rows.append({"tick": tick, "liquidity_net": net_by_tick[tick],
                     "cumulative_liquidity": running})
    return rows


def active_liquidity_at(rows, cur_tick):
    """Active in-range L at cur_tick = cumulative at the greatest initialized tick <= cur_tick."""
    active = 0
    for r in rows:
        if r["tick"] <= cur_tick:
            active = r["cumulative_liquidity"]
        else:
            break
    return active


# --- network --------------------------------------------------------------------

def _multicall(w3, calls, block):
    mc = w3.eth.contract(address=Web3.to_checksum_address(MULTICALL3), abi=MC_ABI)
    batch = [(t, False, d) for t, d in calls]
    return mc.functions.aggregate3(batch).call(block_identifier=block)


def fetch_net_by_tick(w3, pool, block, spacing):
    """Read every initialized tick's liquidityNet at `block`, batched via Multicall3."""
    bitmap_sel = Web3.keccak(text="tickBitmap(int16)")[:4]
    ticks_sel = Web3.keccak(text="ticks(int24)")[:4]
    lo, hi = word_range(spacing)

    # one Multicall for all bitmap words
    bm_calls = [(pool, bitmap_sel + encode(["int16"], [w])) for w in range(lo, hi + 1)]
    initialized = []
    for w, (ok, data) in zip(range(lo, hi + 1), _multicall(w3, bm_calls, block)):
        bm = decode(["uint256"], data)[0]
        if bm:
            initialized.extend(ticks_in_word(w, bm, spacing))
    initialized.sort()

    # batched Multicalls for ticks(); chunk to keep response sizes sane
    net = {}
    CHUNK = 250
    for j in range(0, len(initialized), CHUNK):
        chunk = initialized[j:j + CHUNK]
        calls = [(pool, ticks_sel + encode(["int24"], [t])) for t in chunk]
        for t, (ok, data) in zip(chunk, _multicall(w3, calls, block)):
            net[t] = decode(TICKS_RET, data)[1]  # liquidityNet
    return net


def write_csv(rows, path):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["tick", "liquidity_net", "cumulative_liquidity"])
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {len(rows)} rows -> {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default=DEFAULT_POOL)
    ap.add_argument("--start", default="2026-06-12", help="YYYY-MM-DD UTC; snapshot at its start block")
    ap.add_argument("--rpc", default=DEFAULT_RPC)
    ap.add_argument("--block", type=int, default=None, help="override: snapshot at this block")
    ap.add_argument("--no-initial-liquidity", dest="enabled", action="store_false",
                    help="skip the on-chain reads (downstream falls back to 0-baseline)")
    ap.set_defaults(enabled=True)
    args = ap.parse_args()

    if not args.enabled:
        print("initial-liquidity gathering disabled (--no-initial-liquidity); nothing written.")
        return

    w3 = Web3(Web3.HTTPProvider(args.rpc))
    if not w3.is_connected():
        raise SystemExit(f"Could not reach RPC {args.rpc}")
    uc = UsageCounter().attach(w3)
    pool = Web3.to_checksum_address(args.pool)
    pc = w3.eth.contract(address=pool, abi=POOL_ABI)

    block = args.block if args.block is not None else \
        block_by_timestamp(w3, to_unix(args.start), w3.eth.block_number)
    spacing = pc.functions.tickSpacing().call()
    cur_tick = pc.functions.slot0().call(block_identifier=block)[1]
    on_chain_L = pc.functions.liquidity().call(block_identifier=block)
    print(f"block={block}  tickSpacing={spacing}  current tick={cur_tick}")

    net = fetch_net_by_tick(w3, pool, block, spacing)
    rows = build_distribution(net)

    recon = active_liquidity_at(rows, cur_tick)
    full_sum = rows[-1]["cumulative_liquidity"] if rows else 0
    match = recon == on_chain_L
    print(f"initialized ticks={len(rows)}  reconstructed active L={recon}")
    print(f"on-chain liquidity()={on_chain_L}  MATCH={match}  (full-range cumsum={full_sum}, want 0)")
    if not match:
        print("  WARNING: reconstruction != liquidity(); snapshot may be incomplete.")

    write_csv(rows, os.path.join(HERE, "initial_liquidity.csv"))
    print(uc.summary())
    uc.dump(label=f"tick_snapshot start={args.start} block={block}")
    print("Done.")


if __name__ == "__main__":
    main()
