#!/usr/bin/env python3
"""
Download Uniswap v3 pool activity for a date range straight from chain logs.

NO WALLET and NO ACCOUNT required: uses a public RPC + eth_getLogs.
(You can swap in an Alchemy/Infura URL for higher limits — still no wallet, just email signup.)

Outputs: swaps.csv, mints.csv, burns.csv, collects.csv

What you get (raw on-chain truth, decimal-adjusted by token):
  swaps    -> amount0/amount1 (signed), sqrtPriceX96, tick, liquidity  [buys/sells + price]
  mints    -> tickLower/tickUpper (range), liquidity `amount`, amount0/amount1  [liquidity added]
  burns    -> tickLower/tickUpper, `amount`, amount0/amount1  [liquidity removed]
  collects -> tickLower/tickUpper, amount0/amount1  [tokens withdrawn to LP; principal+fees]

No USD here (the subgraph gives that for free; here you'd add a price feed). For exploration,
raw token amounts + price-from-tick are enough.

Direction on a swap: amount0 > 0 means the pool RECEIVED token0 (trader sent token0, got token1).
For the ETH/USDC 0.3% pool, token0 = USDC, token1 = WETH, so amount0 > 0 == trader sold USDC for WETH.

Usage:
  pip install web3
  python uniswap_v3_pool_download_rpc.py \
      --pool 0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8 \
      --start 2026-06-01 --end 2026-06-02
  # optional: --rpc https://ethereum-rpc.publicnode.com   (default public endpoint used otherwise)
  # optional: --chunk 800   (smaller if a public RPC rejects the block range)
"""

import argparse
import csv
from datetime import datetime, timedelta, timezone

from web3 import Web3

DEFAULT_RPC = "https://ethereum-rpc.publicnode.com"  # keyless; llamarpc / ankr public also work
DEFAULT_POOL = "0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8"  # ETH/USDC 0.30%
DEFAULT_DAYS = 5  # default window = the past N days (UTC)


def default_range(days: int = DEFAULT_DAYS):
    end = datetime.now(timezone.utc).date()
    return (end - timedelta(days=days)).isoformat(), end.isoformat()

# Minimal ABIs ------------------------------------------------------------------
POOL_EVENT_ABI = [
    {"anonymous": False, "name": "Swap", "type": "event", "inputs": [
        {"indexed": True, "name": "sender", "type": "address"},
        {"indexed": True, "name": "recipient", "type": "address"},
        {"indexed": False, "name": "amount0", "type": "int256"},
        {"indexed": False, "name": "amount1", "type": "int256"},
        {"indexed": False, "name": "sqrtPriceX96", "type": "uint160"},
        {"indexed": False, "name": "liquidity", "type": "uint128"},
        {"indexed": False, "name": "tick", "type": "int24"}]},
    {"anonymous": False, "name": "Mint", "type": "event", "inputs": [
        {"indexed": False, "name": "sender", "type": "address"},
        {"indexed": True, "name": "owner", "type": "address"},
        {"indexed": True, "name": "tickLower", "type": "int24"},
        {"indexed": True, "name": "tickUpper", "type": "int24"},
        {"indexed": False, "name": "amount", "type": "uint128"},
        {"indexed": False, "name": "amount0", "type": "uint256"},
        {"indexed": False, "name": "amount1", "type": "uint256"}]},
    {"anonymous": False, "name": "Burn", "type": "event", "inputs": [
        {"indexed": True, "name": "owner", "type": "address"},
        {"indexed": True, "name": "tickLower", "type": "int24"},
        {"indexed": True, "name": "tickUpper", "type": "int24"},
        {"indexed": False, "name": "amount", "type": "uint128"},
        {"indexed": False, "name": "amount0", "type": "uint256"},
        {"indexed": False, "name": "amount1", "type": "uint256"}]},
    {"anonymous": False, "name": "Collect", "type": "event", "inputs": [
        {"indexed": True, "name": "owner", "type": "address"},
        {"indexed": False, "name": "recipient", "type": "address"},
        {"indexed": True, "name": "tickLower", "type": "int24"},
        {"indexed": True, "name": "tickUpper", "type": "int24"},
        {"indexed": False, "name": "amount0", "type": "uint128"},
        {"indexed": False, "name": "amount1", "type": "uint128"}]},
]
POOL_CALL_ABI = [
    {"name": "token0", "outputs": [{"type": "address"}], "inputs": [], "stateMutability": "view", "type": "function"},
    {"name": "token1", "outputs": [{"type": "address"}], "inputs": [], "stateMutability": "view", "type": "function"},
]
ERC20_ABI = [
    {"name": "decimals", "outputs": [{"type": "uint8"}], "inputs": [], "stateMutability": "view", "type": "function"},
    {"name": "symbol", "outputs": [{"type": "string"}], "inputs": [], "stateMutability": "view", "type": "function"},
]


def to_unix(d):
    return int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def block_by_timestamp(w3, target_ts, hi):
    """Earliest block with timestamp >= target_ts (binary search)."""
    lo = 1
    while lo < hi:
        mid = (lo + hi) // 2
        if w3.eth.get_block(mid)["timestamp"] < target_ts:
            lo = mid + 1
        else:
            hi = mid
    return lo


def fetch_logs(w3, event, address, topic0, from_block, to_block, chunk):
    """eth_getLogs in block chunks; auto-shrinks the window if the RPC complains."""
    out, start = [], from_block
    while start <= to_block:
        end = min(start + chunk - 1, to_block)
        try:
            raw = w3.eth.get_logs({
                "address": Web3.to_checksum_address(address),
                "topics": [topic0],
                "fromBlock": start, "toBlock": end,
            })
        except Exception as e:
            if chunk > 50:  # range too big for this public RPC: split and retry
                chunk //= 2
                continue
            raise e
        for lg in raw:
            out.append(event.process_log(lg))
        start = end + 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default=DEFAULT_POOL)
    ap.add_argument("--start", default=None, help="YYYY-MM-DD UTC inclusive (default: 5 days ago)")
    ap.add_argument("--end", default=None, help="YYYY-MM-DD UTC exclusive (default: today)")
    ap.add_argument("--rpc", default=DEFAULT_RPC)
    ap.add_argument("--chunk", type=int, default=800, help="blocks per getLogs call")
    args = ap.parse_args()

    if args.start is None or args.end is None:
        ds, de = default_range()
        args.start = args.start or ds
        args.end = args.end or de
        print(f"date range (default = past {DEFAULT_DAYS} days): {args.start} -> {args.end}")

    w3 = Web3(Web3.HTTPProvider(args.rpc))
    if not w3.is_connected():
        raise SystemExit(f"Could not reach RPC {args.rpc}")
    pool = Web3.to_checksum_address(args.pool)

    # token metadata so amounts come out human-readable
    pc = w3.eth.contract(address=pool, abi=POOL_CALL_ABI)
    t0 = w3.eth.contract(address=pc.functions.token0().call(), abi=ERC20_ABI)
    t1 = w3.eth.contract(address=pc.functions.token1().call(), abi=ERC20_ABI)
    d0, d1 = t0.functions.decimals().call(), t1.functions.decimals().call()
    s0, s1 = t0.functions.symbol().call(), t1.functions.symbol().call()
    print(f"token0={s0} ({d0} dec)  token1={s1} ({d1} dec)")

    head = w3.eth.block_number
    start_block = block_by_timestamp(w3, to_unix(args.start), head)
    end_block = block_by_timestamp(w3, to_unix(args.end), head) - 1
    print(f"blocks [{start_block}, {end_block}]  ({end_block - start_block + 1} blocks)")

    pool_evt = w3.eth.contract(address=pool, abi=POOL_EVENT_ABI)
    events = {
        "Swap": "Swap(address,address,int256,int256,uint160,uint128,int24)",
        "Mint": "Mint(address,address,int24,int24,uint128,uint256,uint256)",
        "Burn": "Burn(address,int24,int24,uint128,uint256,uint256)",
        "Collect": "Collect(address,address,int24,int24,uint128,uint128)",
    }

    ts_cache = {}

    def ts_of(bn):
        if bn not in ts_cache:
            ts_cache[bn] = w3.eth.get_block(bn)["timestamp"]
        return ts_cache[bn]

    for name, sig in events.items():
        topic0 = Web3.keccak(text=sig).hex()
        if not topic0.startswith("0x"):
            topic0 = "0x" + topic0
        ev = getattr(pool_evt.events, name)()
        print(f"Fetching {name} ...")
        logs = fetch_logs(w3, ev, pool, topic0, start_block, end_block, args.chunk)

        rows = []
        for lg in logs:
            a = dict(lg["args"])
            ts = ts_of(lg["blockNumber"])
            row = {
                "block": lg["blockNumber"],
                "logIndex": lg["logIndex"],
                "tx": lg["transactionHash"].hex(),
                "timestamp": ts,
                "datetime_utc": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            }
            if "amount0" in a:
                row["amount0"] = a["amount0"] / 10**d0
                row["amount1"] = a["amount1"] / 10**d1
            for k in ("amount", "tickLower", "tickUpper", "tick", "sqrtPriceX96",
                      "liquidity", "owner", "sender", "recipient"):
                if k in a:
                    row[k] = a[k]
            if name == "Swap":
                row["direction"] = "pool_received_token0" if a["amount0"] > 0 else "pool_received_token1"
            rows.append(row)

        rows.sort(key=lambda r: (r["block"], r["logIndex"]))
        path = f"{name.lower()}s.csv"
        if rows:
            keys = sorted({k for r in rows for k in r})
            keys = ["block", "logIndex", "tx", "timestamp", "datetime_utc"] + \
                   [k for k in keys if k not in ("block", "logIndex", "tx", "timestamp", "datetime_utc")]
            with open(path, "w", newline="") as f:
                wr = csv.DictWriter(f, fieldnames=keys)
                wr.writeheader()
                wr.writerows(rows)
            print(f"  wrote {len(rows)} rows -> {path}")
        else:
            open(path, "w").close()
            print(f"  (no rows) -> {path}")

    print("Done. No wallet, no API key, no account.")


if __name__ == "__main__":
    main()