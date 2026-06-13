#!/usr/bin/env python3
"""Stage 4.3 (data) — build time-sliced order-book data for the interactive figures.

Replays in-window Mint/Burn events into per-position liquidity over time, and (seeded by the
Stage 4.1 absolute baseline when present) the absolute L2 depth curve per time slice.

Positions are keyed by (tickLower, tickUpper) — `owner` is the NonfungiblePositionManager for
every row, so it carries no per-LP identity (a known L3 limitation). The L3 view is therefore
scoped to positions **minted during the window** (they start from a known 0); pre-existing
burn-only ranges are excluded from the L3 columns but DO affect the L2 aggregate correctly.

Outputs:
  orderbook_slices.csv  -> long form: one row per active position per time slice (the L3 heatmap)
  depth_slices.csv      -> long form: absolute active L per tick per slice (the L2 depth animation)

Usage:
  python orderbook.py --slices 24
  python orderbook.py --no-initial-liquidity   # L2 depth becomes in-window net change only
"""
import argparse
import csv
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))


# --- pure functions (unit-tested) --------------------------------------------

def merge_position_events(mints, burns):
    """Signed liquidity events keyed by (tickLower, tickUpper), sorted by (block, logIndex)."""
    evs = []
    for r in mints:
        evs.append({"block": int(r["block"]), "logIndex": int(r["logIndex"]),
                    "ts": int(r["timestamp"]), "lo": int(r["tickLower"]),
                    "up": int(r["tickUpper"]), "dL": int(r["amount"])})
    for r in burns:
        evs.append({"block": int(r["block"]), "logIndex": int(r["logIndex"]),
                    "ts": int(r["timestamp"]), "lo": int(r["tickLower"]),
                    "up": int(r["tickUpper"]), "dL": -int(r["amount"])})
    evs.sort(key=lambda e: (e["block"], e["logIndex"]))
    return evs


def first_mint_times(mints):
    out = {}
    for r in mints:
        key = (int(r["tickLower"]), int(r["tickUpper"]))
        ts = int(r["timestamp"])
        out[key] = min(ts, out[key]) if key in out else ts
    return out


def slice_times(start_ts, end_ts, n):
    if n < 2:
        return [end_ts]
    return [start_ts + (end_ts - start_ts) * i // (n - 1) for i in range(n)]


def liquidity_by_key_at(events, t):
    """Cumulative L per (lo, up) position from all events with ts <= t."""
    L = defaultdict(int)
    for e in events:
        if e["ts"] <= t:
            L[(e["lo"], e["up"])] += e["dL"]
    return L


def active_positions_at(events, t, first_mint):
    """Positions with L > 0 at time t that were minted in-window (have a first-mint time)."""
    L = liquidity_by_key_at(events, t)
    rows = []
    for (lo, up), liq in L.items():
        if liq > 0 and (lo, up) in first_mint:
            rows.append({"tickLower": lo, "tickUpper": up, "L": liq,
                         "mint_time": first_mint[(lo, up)]})
    rows.sort(key=lambda r: (r["mint_time"], r["tickLower"], r["tickUpper"]))
    return rows


def net_delta_at(events, t):
    """In-window liquidityNet delta map (+L at lo, -L at up) for events with ts <= t."""
    net = defaultdict(int)
    for e in events:
        if e["ts"] <= t:
            net[e["lo"]] += e["dL"]
            net[e["up"]] -= e["dL"]
    return net


def absolute_net(baseline_net, delta_net):
    out = dict(baseline_net)
    for tick, v in delta_net.items():
        out[tick] = out.get(tick, 0) + v
    return out


def cumulative(net):
    """Ascending [(tick, cumulative_L)] from a liquidityNet map."""
    rows, running = [], 0
    for tick in sorted(net):
        running += net[tick]
        rows.append((tick, running))
    return rows


def active_tick_at(swaps, t):
    """Last swap tick at or before t (current price tick for that slice)."""
    tick = None
    for r in swaps:
        if int(r["timestamp"]) <= t:
            tick = int(r["tick"])
        else:
            break
    return tick


# --- io ----------------------------------------------------------------------

def read_csv(name):
    path = os.path.join(HERE, name)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write_csv(name, rows, fieldnames):
    with open(os.path.join(HERE, name), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {len(rows):>5} rows -> {name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slices", type=int, default=24, help="number of time slices (frames)")
    ap.add_argument("--window", type=int, default=3000, help="± ticks around price for depth view")
    ap.add_argument("--no-initial-liquidity", dest="use_initial", action="store_false",
                    help="L2 depth uses in-window net change only (no absolute baseline)")
    ap.set_defaults(use_initial=True)
    args = ap.parse_args()

    mints, burns = read_csv("mints.csv"), read_csv("burns.csv")
    swaps = read_csv("swaps_classified.csv")
    if not mints and not burns:
        raise SystemExit("No mints/burns — run Stage 1 first.")
    initial = read_csv("initial_liquidity.csv") if args.use_initial else []
    baseline_net = {int(r["tick"]): int(r["liquidity_net"]) for r in initial}
    basis = "absolute" if baseline_net else "relative"

    events = merge_position_events(mints, burns)
    first_mint = first_mint_times(mints)
    all_ts = [e["ts"] for e in events] + [int(r["timestamp"]) for r in swaps]
    start_ts, end_ts = min(all_ts), max(all_ts)
    times = slice_times(start_ts, end_ts, args.slices)

    # depth window around the price range
    swap_ticks = [int(r["tick"]) for r in swaps] or [0]
    lo_win, hi_win = min(swap_ticks) - args.window, max(swap_ticks) + args.window

    ob_rows, depth_rows = [], []
    for idx, t in enumerate(times):
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(t, tz=timezone.utc).isoformat()
        a_tick = active_tick_at(swaps, t)

        for p in active_positions_at(events, t, first_mint):
            ob_rows.append({"slice_idx": idx, "slice_time": t, "slice_dt": dt,
                            "pos_id": f"{p['tickLower']}_{p['tickUpper']}",
                            "mint_time": p["mint_time"], "tickLower": p["tickLower"],
                            "tickUpper": p["tickUpper"], "L": p["L"]})

        net = absolute_net(baseline_net, net_delta_at(events, t)) if baseline_net \
            else net_delta_at(events, t)
        for tick, cumL in cumulative(net):
            if lo_win <= tick <= hi_win:
                depth_rows.append({"slice_idx": idx, "slice_time": t, "slice_dt": dt,
                                   "active_tick": a_tick, "tick": tick, "cumulative_L": cumL})

    write_csv("orderbook_slices.csv", ob_rows,
              ["slice_idx", "slice_time", "slice_dt", "pos_id", "mint_time",
               "tickLower", "tickUpper", "L"])
    write_csv("depth_slices.csv", depth_rows,
              ["slice_idx", "slice_time", "slice_dt", "active_tick", "tick", "cumulative_L"])
    print(f"slices={len(times)}  positions(in-window)={len(first_mint)}  depth basis={basis}. Done.")


if __name__ == "__main__":
    main()
