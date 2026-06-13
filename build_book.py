#!/usr/bin/env python3
"""Stage 6.2.1 — build the virtual order book over time from real data via the engine.

Replays the day's linked events (mints/burns by tokenId + swaps) through `orderbook_engine`,
snapshots the derived bid/ask ladder at N time slices, and writes:

  book_l2.csv      -> aggregate depth per price band per slice (side-labelled)
  book_l3.csv      -> per-tokenId order per price band per slice (side-labelled)  [true L3]
  daily_metrics.csv-> volume, fees, and the two APRs for the day

Unlike the Stage 4.3 range view, this is the *virtual limit-order book*: per-band order sizes are
uneven (uniform in sqrt-price), and each level's side (bid = buy WETH / ask = sell WETH) is read
off the moving spot, so levels flip as price crosses them.

A fixed price grid (every initialized tick in a window around the day's price range) is computed
once up front so the bands — and the x-axis — stay stable across slices (no flicker).

Usage:
  python build_book.py                          # with pre-existing baseline (default)
  python build_book.py --no-initial-liquidity   # in-window orders only (fully attributed L3)
  python build_book.py --slices 24 --window 3500
"""
import argparse
import csv
import os
from collections import defaultdict

import orderbook_engine as ob
import pool_meta
from orderbook import slice_times

HERE = os.path.dirname(os.path.abspath(__file__))


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


def build_events(mints, burns, swaps):
    """All events as (block, logIndex, kind, row), sorted by (block, logIndex)."""
    ev = []
    for r in mints:
        ev.append((int(r["block"]), int(r["logIndex"]), "mint", r))
    for r in burns:
        ev.append((int(r["block"]), int(r["logIndex"]), "burn", r))
    for r in swaps:
        ev.append((int(r["block"]), int(r["logIndex"]), "swap", r))
    ev.sort(key=lambda e: (e[0], e[1]))
    return ev


def apply_event(book, kind, r, d0, d1):
    """Apply one event to the book; returns the spot tick if it was a swap, else None."""
    if kind == "mint" and r["tokenId"]:
        book.apply_mint(r["tokenId"], r["tickLower"], r["tickUpper"], r["amount"])
    elif kind == "burn" and r["tokenId"] and int(r["amount"]) > 0:
        book.apply_burn(r["tokenId"], r["tickLower"], r["tickUpper"], r["amount"])
    elif kind == "swap":
        a0, a1 = abs(float(r["amount0"])), abs(float(r["amount1"]))
        side = "buy" if r["direction"] == "pool_received_token0" else "sell"
        price = ob.price_usdc_per_weth_from_tick(int(r["tick"]), d0, d1)
        book.apply_swap(side, a0, a1, price, int(r["tick"]), int(r["liquidity"]))
        return int(r["tick"])
    return None


def fixed_grid(mints, burns, baseline_net, swaps, window):
    """Stable band boundaries = every initialized tick within ±window of the day's price range."""
    swap_ticks = [int(r["tick"]) for r in swaps] or [0]
    lo_win, hi_win = min(swap_ticks) - window, max(swap_ticks) + window
    ticks = set(baseline_net)
    for r in mints + burns:
        ticks.add(int(r["tickLower"]))
        ticks.add(int(r["tickUpper"]))
    return sorted(t for t in ticks if lo_win <= t <= hi_win), (lo_win, hi_win)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slices", type=int, default=24, help="number of time slices (frames)")
    ap.add_argument("--window", type=int, default=3500, help="± ticks around price for the grid")
    ap.add_argument("--no-initial-liquidity", dest="use_initial", action="store_false",
                    help="omit the pre-existing aggregate baseline (in-window orders only)")
    ap.set_defaults(use_initial=True)
    args = ap.parse_args()

    meta = pool_meta.load()
    if not meta:
        raise SystemExit("pool_metadata.csv absent — run link_positions.py (Stage 6.0) first.")
    d0, d1, gamma = meta["decimals0"], meta["decimals1"], meta["gamma"]

    mints, burns = read_csv("mints_linked.csv"), read_csv("burns_linked.csv")
    swaps = read_csv("swaps.csv")
    if not swaps:
        raise SystemExit("No swaps.csv — run Stage 1 first.")
    if not mints and not burns:
        raise SystemExit("No linked mints/burns — run link_positions.py (Stage 6.0) first.")
    initial = read_csv("initial_liquidity.csv") if args.use_initial else []
    baseline_net = {int(r["tick"]): int(r["liquidity_net"]) for r in initial}
    basis = "with-initial" if baseline_net else "without-initial"

    events = build_events(mints, burns, swaps)
    times_all = [int(r["timestamp"]) for *_, r in events]
    start_ts, end_ts = min(times_all), max(times_all)
    times = slice_times(start_ts, end_ts, args.slices)
    grid, (lo_win, hi_win) = fixed_grid(mints, burns, baseline_net, swaps, args.window)

    book = ob.Orderbook(gamma=gamma, d0=d0, d1=d1)
    if baseline_net:
        book.set_baseline(baseline_net)

    from datetime import datetime, timezone

    def dt(t):
        return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()

    l2_rows, l3_rows = [], []

    def snapshot(idx, t):
        spot = book.tick
        bands = book.book_at(grid, tick=spot, include_baseline=bool(baseline_net))
        for b in bands:
            price = b["price"]
            l2_rows.append({
                "slice_idx": idx, "slice_time": t, "slice_dt": dt(t), "active_tick": spot,
                "tick_lo": b["tick_lo"], "tick_hi": b["tick_hi"], "price": f"{price:.4f}",
                "side": b["side"],
                "depth_usdc": f"{ob.human0(b['agg_q0'], d0):.6f}",
                "depth_weth": f"{ob.human1(b['agg_q1'], d1):.10f}",
            })
            for tid, q in b["positions"].items():
                l3_rows.append({
                    "slice_idx": idx, "slice_time": t, "slice_dt": dt(t), "active_tick": spot,
                    "tokenId": tid, "tick_lo": b["tick_lo"], "tick_hi": b["tick_hi"],
                    "price": f"{price:.4f}", "side": b["side"],
                    "q_usdc": f"{ob.human0(q['q0'], d0):.6f}",
                    "q_weth": f"{ob.human1(q['q1'], d1):.10f}",
                })

    # single ordered replay; snapshot each slice once all events up to its time are applied
    si = 0
    for blk, li, kind, r in events:
        ts = int(r["timestamp"])
        while si < len(times) and ts > times[si]:
            snapshot(si, times[si])
            si += 1
        apply_event(book, kind, r, d0, d1)
    while si < len(times):
        snapshot(si, times[si])
        si += 1

    write_csv("book_l2.csv", l2_rows,
              ["slice_idx", "slice_time", "slice_dt", "active_tick", "tick_lo", "tick_hi",
               "price", "side", "depth_usdc", "depth_weth"])
    write_csv("book_l3.csv", l3_rows,
              ["slice_idx", "slice_time", "slice_dt", "active_tick", "tokenId", "tick_lo",
               "tick_hi", "price", "side", "q_usdc", "q_weth"])

    print(f"basis={basis}  slices={len(times)}  bands/slice≈{len(l2_rows)//max(len(times),1)}  "
          f"tokenId positions={sum(1 for p in book.positions.values() if p['L'] > 0)}")

    # daily metrics: a POOL-level artifact (volume/fees/APR). Active TVL needs the full book, so it
    # is written only on the with-initial run; the without-initial run is just the L3 order figure.
    if baseline_net:
        tvl_rows = read_csv("tvl_series.csv")
        tvl_vals = [float(r["tvl_usdc"]) for r in tvl_rows] or [0.0]
        tvl_usd = sum(tvl_vals) / len(tvl_vals)    # mean TVL over the day (capital base for APR)
        tvl_basis = tvl_rows[0].get("basis", "relative") if tvl_rows else "relative"
        end_price = ob.price_usdc_per_weth_from_tick(book.tick, d0, d1) if book.tick is not None else 0.0
        active_tvl = book.active_band_value(grid, end_price)
        days = max((end_ts - start_ts) / 86400.0, 1e-9)
        s = book.daily_stats(tvl_usd=tvl_usd, active_tvl_usd=active_tvl, days=days)
        s.update({"date_start": dt(start_ts), "date_end": dt(end_ts), "days": f"{days:.4f}",
                  "gamma": gamma, "tvl_basis": tvl_basis,
                  "end_price_usdc_per_weth": f"{end_price:.2f}"})
        metric_cols = ["date_start", "date_end", "days", "gamma", "swap_count",
                       "volume_usdc", "volume_weth", "fee_usdc", "fee_weth", "fee_usd",
                       "tvl_basis", "tvl_usd", "active_tvl_usd", "apr_total_tvl", "apr_active_tvl",
                       "end_price_usdc_per_weth"]
        write_csv("daily_metrics.csv", [{k: s.get(k, "") for k in metric_cols}], metric_cols)
        print(f"volume≈${s['volume_usdc']:,.0f}  fees≈${s['fee_usd']:,.0f}  "
              f"APR(total)={s['apr_total_tvl']*100:.2f}%  APR(active)={s['apr_active_tvl']*100:.1f}%")
    else:
        print("  (daily_metrics.csv written only on the with-initial run — pool-level numbers)")
    print("Done.")


if __name__ == "__main__":
    main()
