#!/usr/bin/env python3
"""
Download Uniswap v3 pool activity for a date range from The Graph subgraph.

Outputs five CSVs in the current directory:
  swaps.csv, mints.csv, burns.csv, collects.csv, pool_hour_data.csv

What you get:
  - swaps        -> buys/sells (signed amounts, price via sqrtPriceX96/tick, amountUSD)
  - mints        -> liquidity ADDED, with tickLower/tickUpper (the range) and liquidity `amount`
  - burns        -> liquidity REMOVED, with tickLower/tickUpper and `amount`
  - collects     -> tokens actually withdrawn to LPs (principal + fees together; see note below)
  - pool_hour_data -> per-hour TVL (totalValueLockedUSD), volume, fees, prices -> draw your TVL graph

Notes:
  * Uniswap v3 has NO per-swap fee event. Fee on a swap ~= |amount_in| * feeTier
    (0.3% pool -> 0.003). Fees accrue to LPs; `collect` is when an LP withdraws.
  * For this pool (ETH/USDC 0.3%): token0 = USDC (6 dec), token1 = WETH (18 dec).
    The subgraph already decimal-normalizes amount0/amount1, so values are human-readable.
  * To reconstruct the full liquidity-vs-price curve over time ("level 3"), replay
    mints/burns in (timestamp, logIndex) order: net[tickLower]+=amount, net[tickUpper]-=amount
    (reverse for burns). Running cumulative sum to the active tick = active liquidity.

Usage:
  export GRAPH_API_KEY=your_key_here
  python uniswap_v3_pool_download.py \
      --pool 0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8 \
      --start 2026-06-01 --end 2026-06-02
"""

import argparse
import csv
import os
import sys
import time
from datetime import datetime, timezone

import requests  # pip install requests

SUBGRAPH_ID = "5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV"  # Uniswap v3, Ethereum mainnet
GATEWAY = "https://gateway.thegraph.com/api/{key}/subgraphs/id/" + SUBGRAPH_ID
PAGE = 1000  # subgraph hard cap per query


def to_unix(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def run_query(endpoint: str, query: str, variables: dict) -> dict:
    for attempt in range(5):
        resp = requests.post(endpoint, json={"query": query, "variables": variables}, timeout=60)
        if resp.status_code == 200:
            body = resp.json()
            if "errors" in body:
                raise RuntimeError(f"GraphQL errors: {body['errors']}")
            return body["data"]
        if resp.status_code in (429, 502, 503, 504):
            time.sleep(2 ** attempt)
            continue
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:500]}")
    raise RuntimeError("Too many retries")


def paginate(endpoint, entity, fields, pool, start_ts, end_ts):
    """Cursor-paginate by timestamp to avoid the skip<=5000 limit."""
    query = """
    query($pool: String!, $start: Int!, $end: Int!, $first: Int!) {
      %s(
        first: $first
        orderBy: timestamp
        orderDirection: asc
        where: { pool: $pool, timestamp_gte: $start, timestamp_lt: $end }
      ) { %s }
    }
    """ % (entity, fields)

    rows, cursor = [], start_ts
    while True:
        data = run_query(endpoint, query, {"pool": pool, "start": cursor, "end": end_ts, "first": PAGE})
        batch = data[entity]
        if not batch:
            break
        rows.extend(batch)
        last_ts = int(batch[-1]["timestamp"])
        if len(batch) < PAGE:
            break
        # advance cursor; +1 risks dropping same-second rows, so re-query from last_ts and dedupe by id
        cursor = last_ts
        seen = {r["id"] for r in rows}
        # crude same-second guard: if everything in batch shares last_ts, bump to avoid infinite loop
        if all(int(r["timestamp"]) == last_ts for r in batch):
            cursor = last_ts + 1
        rows = list({r["id"]: r for r in rows}.values())  # dedupe
        _ = seen
    # final dedupe + sort
    rows = list({r["id"]: r for r in rows}.values())
    rows.sort(key=lambda r: (int(r["timestamp"]), int(r.get("logIndex", 0))))
    return rows


def write_csv(path, rows):
    if not rows:
        print(f"  (no rows) {path}")
        open(path, "w").close()
        return
    keys = sorted({k for r in rows for k in r.keys()})
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {len(rows):>6} rows -> {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True, help="pool address (lowercase)")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD (UTC, inclusive)")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD (UTC, exclusive)")
    ap.add_argument("--key", default=os.environ.get("GRAPH_API_KEY"))
    args = ap.parse_args()

    if not args.key:
        sys.exit("Set GRAPH_API_KEY env var or pass --key (get one free at thegraph.com/studio)")

    pool = args.pool.lower()
    start_ts, end_ts = to_unix(args.start), to_unix(args.end)
    endpoint = GATEWAY.format(key=args.key)
    print(f"Pool {pool}  [{args.start} .. {args.end})  ts [{start_ts}, {end_ts})")

    entities = {
        "swaps":    "id timestamp transaction { id } origin sender recipient amount0 amount1 amountUSD sqrtPriceX96 tick logIndex",
        "mints":    "id timestamp transaction { id } owner sender origin amount amount0 amount1 amountUSD tickLower tickUpper logIndex",
        "burns":    "id timestamp transaction { id } owner origin amount amount0 amount1 amountUSD tickLower tickUpper logIndex",
        "collects": "id timestamp transaction { id } owner amount0 amount1 amountUSD tickLower tickUpper logIndex",
    }
    for entity, fields in entities.items():
        print(f"Fetching {entity} ...")
        try:
            rows = paginate(endpoint, entity, fields, pool, start_ts, end_ts)
        except RuntimeError as e:
            # `collects` may not exist on every schema version; don't abort the whole run
            print(f"  skipped {entity}: {e}")
            rows = []
        # flatten nested transaction.id
        for r in rows:
            if isinstance(r.get("transaction"), dict):
                r["transaction"] = r["transaction"].get("id")
        write_csv(f"{entity}.csv", rows)

    # Hourly TVL series for the TVL graph
    print("Fetching pool_hour_data (TVL series) ...")
    hour_q = """
    query($pool: String!, $start: Int!, $end: Int!) {
      poolHourDatas(
        first: 1000
        orderBy: periodStartUnix
        orderDirection: asc
        where: { pool: $pool, periodStartUnix_gte: $start, periodStartUnix_lt: $end }
      ) {
        periodStartUnix totalValueLockedUSD volumeUSD feesUSD
        token0Price token1Price liquidity sqrtPrice tick high low open close
      }
    }
    """
    hours = run_query(endpoint, hour_q, {"pool": pool, "start": start_ts, "end": end_ts}).get("poolHourDatas", [])
    for h in hours:
        h["datetime_utc"] = datetime.fromtimestamp(int(h["periodStartUnix"]), tz=timezone.utc).isoformat()
    write_csv("pool_hour_data.csv", hours)

    print("Done.")


if __name__ == "__main__":
    main()