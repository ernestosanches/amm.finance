#!/usr/bin/env python3
"""Stage 2 — process the raw RPC CSVs into the series needed for the curves.

Reads Stage 1 output (swaps.csv, mints.csv, burns.csv, collects.csv) and writes:

  swaps_classified.csv      -> buy/sell side + price (USDC per WETH) + sizes
  tvl_series.csv            -> pool token balances + TVL over time (event-by-event)
  liquidity_distribution.csv-> net liquidity-vs-tick curve over the period ("level 3")

Pure local computation. No network by default. The only optional network is a single
historical `balanceOf(pool)` read per token to anchor TVL in ABSOLUTE terms (--baseline-rpc);
without it, TVL is reported RELATIVE to the start of the range (net flow from 0).

Conventions (ETH/USDC 0.3% pool): token0 = USDC (6 dec), token1 = WETH (18 dec).
  * "price" we report = USDC per WETH (the intuitive ETH price).
  * "side" is from the trader's view of the volatile asset (token1 = WETH):
      pool_received_token0  (USDC in, WETH out) -> trader BUYS WETH  -> side = "buy"
      pool_received_token1  (WETH in, USDC out) -> trader SELLS WETH -> side = "sell"
Override decimals with --d0/--d1 for other pools.

Usage:
  python process.py                     # relative TVL (offline)
  python process.py --baseline-rpc      # absolute TVL (one balanceOf per token)
"""
import argparse
import csv
import os

HERE = os.path.dirname(os.path.abspath(__file__))
Q96 = 2 ** 96
Q192 = 2 ** 192


# --- pure functions (unit-tested) --------------------------------------------

def price_usdc_per_weth(sqrt_price_x96, d0, d1):
    """USDC per WETH (token0 per token1) from a pool sqrtPriceX96.

    sqrtPriceX96 encodes sqrt(token1/token0) in raw units. Squaring and shifting
    gives token1-per-token0 (raw); * 10**(d0-d1) makes it human (WETH per USDC);
    invert for USDC per WETH.
    """
    weth_per_usdc = (int(sqrt_price_x96) ** 2 / Q192) * (10 ** (d0 - d1))
    return 1.0 / weth_per_usdc


def price_from_tick(tick, d0, d1):
    """Same price (USDC per WETH) derived from the tick instead of sqrtPriceX96."""
    weth_per_usdc = (1.0001 ** int(tick)) * (10 ** (d0 - d1))
    return 1.0 / weth_per_usdc


def classify_side(direction):
    """Map a swap `direction` string to buy/sell of the volatile asset (token1)."""
    if direction == "pool_received_token0":
        return "buy"
    if direction == "pool_received_token1":
        return "sell"
    raise ValueError(f"unknown direction: {direction!r}")


def build_liquidity_distribution(mints, burns):
    """Net liquidity-vs-tick curve over the period (the "level 3" replay).

    Each position [tickLower, tickUpper) contributes +amount at tickLower and
    -amount at tickUpper (signs reversed for burns). Returns rows sorted by tick:
      {tick, net_liquidity_delta, cumulative_liquidity_delta}
    The cumulative sum at a tick = active in-range liquidity CHANGE contributed by
    this period's events (start-of-range baseline is 0; absolute curve needs full history).
    """
    deltas = {}
    for r in mints:
        amt = int(r["amount"])
        deltas[int(r["tickLower"])] = deltas.get(int(r["tickLower"]), 0) + amt
        deltas[int(r["tickUpper"])] = deltas.get(int(r["tickUpper"]), 0) - amt
    for r in burns:
        amt = int(r["amount"])
        deltas[int(r["tickLower"])] = deltas.get(int(r["tickLower"]), 0) - amt
        deltas[int(r["tickUpper"])] = deltas.get(int(r["tickUpper"]), 0) + amt

    rows, running = [], 0
    for tick in sorted(deltas):
        running += deltas[tick]
        rows.append({
            "tick": tick,
            "net_liquidity_delta": deltas[tick],
            "cumulative_liquidity_delta": running,
        })
    return rows


def build_tvl_series(events, b0_start, b1_start):
    """Replay balance-affecting events to get pool balances + TVL over time.

    `events` are dicts pre-sorted by (block, logIndex) with keys:
      block, logIndex, timestamp, datetime_utc, event, d0, d1, price
    where d0/d1 are signed deltas to the pool's token0/token1 balance, and `price`
    is USDC per WETH (forward-filled from swaps). b0_start/b1_start seed the balances
    (0 for relative / net-flow; actual balanceOf for absolute).
    """
    b0, b1 = b0_start, b1_start
    out = []
    for e in events:
        b0 += e["d0"]
        b1 += e["d1"]
        price = e["price"]
        out.append({
            "block": e["block"],
            "logIndex": e["logIndex"],
            "timestamp": e["timestamp"],
            "datetime_utc": e["datetime_utc"],
            "event": e["event"],
            "balance0_usdc": b0,
            "balance1_weth": b1,
            "price_usdc_per_weth": price,
            "tvl_usdc": b0 + b1 * price,
            "tvl_weth": (b0 / price if price else 0.0) + b1,
        })
    return out


# --- io helpers --------------------------------------------------------------

def read_csv(name):
    path = os.path.join(HERE, name)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write_csv(name, rows, fieldnames):
    path = os.path.join(HERE, name)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {len(rows):>5} rows -> {name}")


def fetch_baseline_balances(rpc, swaps):
    """Optional: absolute pool balances just before the first event of the range."""
    from web3 import Web3
    w3 = Web3(Web3.HTTPProvider(rpc))
    pool = Web3.to_checksum_address("0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8")
    pool_abi = [{"name": n, "inputs": [], "outputs": [{"type": "address"}],
                 "stateMutability": "view", "type": "function"} for n in ("token0", "token1")]
    erc20 = [{"name": "balanceOf", "inputs": [{"type": "address"}], "outputs": [{"type": "uint256"}],
              "stateMutability": "view", "type": "function"},
             {"name": "decimals", "inputs": [], "outputs": [{"type": "uint8"}],
              "stateMutability": "view", "type": "function"}]
    pc = w3.eth.contract(address=pool, abi=pool_abi)
    t0 = w3.eth.contract(address=pc.functions.token0().call(), abi=erc20)
    t1 = w3.eth.contract(address=pc.functions.token1().call(), abi=erc20)
    pre_block = min(int(r["block"]) for r in swaps) - 1
    b0 = t0.functions.balanceOf(pool).call(block_identifier=pre_block) / 10 ** t0.functions.decimals().call()
    b1 = t1.functions.balanceOf(pool).call(block_identifier=pre_block) / 10 ** t1.functions.decimals().call()
    print(f"  baseline @ block {pre_block}: {b0:.2f} USDC, {b1:.4f} WETH")
    return b0, b1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--d0", type=int, default=6, help="token0 decimals (USDC=6)")
    ap.add_argument("--d1", type=int, default=18, help="token1 decimals (WETH=18)")
    ap.add_argument("--baseline-rpc", nargs="?", const="https://ethereum-rpc.publicnode.com",
                    default=None, help="fetch absolute TVL baseline via balanceOf (optional)")
    args = ap.parse_args()

    swaps = read_csv("swaps.csv")
    mints = read_csv("mints.csv")
    burns = read_csv("burns.csv")
    collects = read_csv("collects.csv")
    if not swaps:
        raise SystemExit("No swaps.csv — run Stage 1 (uniswap_v3_pool_download_rpc.py) first.")

    # 1) classify swaps + price -----------------------------------------------
    classified = []
    for r in swaps:
        price = price_usdc_per_weth(r["sqrtPriceX96"], args.d0, args.d1)
        classified.append({
            "block": int(r["block"]),
            "logIndex": int(r["logIndex"]),
            "timestamp": int(r["timestamp"]),
            "datetime_utc": r["datetime_utc"],
            "side": classify_side(r["direction"]),
            "price_usdc_per_weth": price,
            "amount_usdc": abs(float(r["amount0"])),
            "amount_weth": abs(float(r["amount1"])),
            "tick": int(r["tick"]),
        })
    write_csv("swaps_classified.csv", classified,
              ["block", "logIndex", "timestamp", "datetime_utc", "side",
               "price_usdc_per_weth", "amount_usdc", "amount_weth", "tick"])

    # 2) TVL series ------------------------------------------------------------
    b0_start, b1_start, basis = 0.0, 0.0, "relative"
    if args.baseline_rpc:
        b0_start, b1_start = fetch_baseline_balances(args.baseline_rpc, swaps)
        basis = "absolute"

    events = []
    for r in swaps:  # swap amounts are signed from the pool's perspective
        events.append({"block": int(r["block"]), "logIndex": int(r["logIndex"]),
                       "timestamp": int(r["timestamp"]), "datetime_utc": r["datetime_utc"],
                       "event": "swap", "d0": float(r["amount0"]), "d1": float(r["amount1"]),
                       "price": price_usdc_per_weth(r["sqrtPriceX96"], args.d0, args.d1)})
    for r in mints:   # pool receives tokens
        events.append({"block": int(r["block"]), "logIndex": int(r["logIndex"]),
                       "timestamp": int(r["timestamp"]), "datetime_utc": r["datetime_utc"],
                       "event": "mint", "d0": float(r["amount0"]), "d1": float(r["amount1"]),
                       "price": None})
    for r in collects:  # tokens physically leave the pool
        events.append({"block": int(r["block"]), "logIndex": int(r["logIndex"]),
                       "timestamp": int(r["timestamp"]), "datetime_utc": r["datetime_utc"],
                       "event": "collect", "d0": -float(r["amount0"]), "d1": -float(r["amount1"]),
                       "price": None})
    # (Burn moves no tokens; excluded. Flash not downloaded; negligible here.)
    events.sort(key=lambda e: (e["block"], e["logIndex"]))

    # forward-fill price from swaps; back-fill the leading non-swap events
    first_price = next((e["price"] for e in events if e["price"] is not None), None)
    last = first_price
    for e in events:
        if e["price"] is None:
            e["price"] = last
        else:
            last = e["price"]

    tvl = build_tvl_series(events, b0_start, b1_start)
    for row in tvl:
        row["basis"] = basis
    write_csv("tvl_series.csv", tvl,
              ["block", "logIndex", "timestamp", "datetime_utc", "event",
               "balance0_usdc", "balance1_weth", "price_usdc_per_weth",
               "tvl_usdc", "tvl_weth", "basis"])

    # 3) liquidity distribution (level 3) -------------------------------------
    dist = build_liquidity_distribution(mints, burns)
    write_csv("liquidity_distribution.csv", dist,
              ["tick", "net_liquidity_delta", "cumulative_liquidity_delta"])

    # reference scalars from swaps: active liquidity/tick at range start & end
    print(f"  active liquidity start: tick={swaps[0]['tick']} L={swaps[0]['liquidity']}")
    print(f"  active liquidity end:   tick={swaps[-1]['tick']} L={swaps[-1]['liquidity']}")
    print(f"TVL basis: {basis}. Done.")


if __name__ == "__main__":
    main()
