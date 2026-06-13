#!/usr/bin/env python3
"""Stage 3 — display the curves.

Reads the Stage 2 CSVs and renders one PNG per curve into out/:

  out/tvl.png                    -> TVL over time
  out/price_flow.png             -> price over time + buy/sell volume flow
  out/liquidity_distribution.png -> liquidity-vs-tick curve (level 3)

Uses the non-interactive Agg backend (no display needed). matplotlib only.

Usage:
  python plot.py
"""
import argparse
import csv
import os
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")  # headless; must be set before pyplot import
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")


def read_csv(name):
    path = os.path.join(HERE, name)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _times(rows):
    return [datetime.fromtimestamp(int(r["timestamp"]), tz=timezone.utc) for r in rows]


# --- plotters (each takes rows + outpath, returns outpath) --------------------

def plot_tvl(rows, outpath):
    times = _times(rows)
    tvl = [float(r["tvl_usdc"]) for r in rows]
    basis = rows[0].get("basis", "relative") if rows else "relative"
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(times, tvl, lw=1.2, color="#1f77b4")
    ax.set_title(f"Pool TVL over time ({basis})")
    ax.set_ylabel("TVL (USDC)")
    ax.set_xlabel("time (UTC)")
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(outpath, dpi=110)
    plt.close(fig)
    return outpath


def plot_price_flow(rows, outpath):
    times = _times(rows)
    price = [float(r["price_usdc_per_weth"]) for r in rows]
    # signed volume: buys up (green), sells down (red), in USDC
    signed = [float(r["amount_usdc"]) * (1 if r["side"] == "buy" else -1) for r in rows]
    colors = ["#2ca02c" if v >= 0 else "#d62728" for v in signed]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 6), sharex=True,
                                   gridspec_kw={"height_ratios": [2, 1]})
    ax1.plot(times, price, lw=1.0, color="#1f77b4")
    ax1.set_title("Price and buy/sell flow")
    ax1.set_ylabel("USDC per WETH")
    ax1.grid(True, alpha=0.3)

    ax2.vlines(times, 0, signed, colors=colors, lw=0.8)
    ax2.axhline(0, color="black", lw=0.5)
    ax2.set_ylabel("swap size (USDC)\nbuy +  / sell −")
    ax2.set_xlabel("time (UTC)")
    ax2.grid(True, alpha=0.3)

    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(outpath, dpi=110)
    plt.close(fig)
    return outpath


def plot_liquidity(rows, outpath, active_tick=None):
    ticks = [int(r["tick"]) for r in rows]
    cum = [int(r["cumulative_liquidity_delta"]) for r in rows]
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.step(ticks, cum, where="post", color="#9467bd", lw=1.2)
    ax.fill_between(ticks, cum, step="post", alpha=0.2, color="#9467bd")
    ax.axhline(0, color="black", lw=0.5)
    if active_tick is not None:
        ax.axvline(active_tick, color="#ff7f0e", ls="--", lw=1.0,
                   label=f"active tick {active_tick}")
        ax.legend()
    ax.set_title("Liquidity distribution over price (net change over range)")
    ax.set_ylabel("cumulative active-liquidity Δ")
    ax.set_xlabel("tick (higher tick = more WETH per USDC)")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(outpath, dpi=110)
    plt.close(fig)
    return outpath


def build_absolute_curves(initial_rows, dist_rows):
    """Combine the absolute start snapshot (Stage 4.1) with the in-window net change (Stage 2)
    into absolute active-liquidity curves at the START and END of the range.

    Both inputs are liquidityNet maps in the same `L` units:
      start_net[tick] = initial_liquidity.liquidity_net  (absolute, all initialized ticks)
      delta_net[tick] = liquidity_distribution.net_liquidity_delta  (in-window change)
    end_net = start_net + delta_net per tick; cumulative sum of each = absolute active L per band.
    Returns (start_curve, end_curve), each a list of {tick, cumulative}.
    """
    start_net = {int(r["tick"]): int(r["liquidity_net"]) for r in initial_rows}
    delta_net = {int(r["tick"]): int(r["net_liquidity_delta"]) for r in dist_rows}
    end_net = dict(start_net)
    for tick, d in delta_net.items():
        end_net[tick] = end_net.get(tick, 0) + d

    def cumulate(net):
        rows, running = [], 0
        for tick in sorted(net):
            running += net[tick]
            rows.append({"tick": tick, "cumulative": running})
        return rows

    return cumulate(start_net), cumulate(end_net)


def plot_liquidity_absolute(start_curve, end_curve, outpath, start_tick=None, end_tick=None,
                            window=6000):
    # cast cumulative L to float: real pools have positions whose cumulative L exceeds int64,
    # which would make numpy use object dtype and break fill_between.
    st = [r["tick"] for r in start_curve]
    sc = [float(r["cumulative"]) for r in start_curve]
    et = [r["tick"] for r in end_curve]
    ec = [float(r["cumulative"]) for r in end_curve]
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.step(st, sc, where="post", color="#7f7f7f", lw=1.0, label="start of range")
    ax.step(et, ec, where="post", color="#9467bd", lw=1.4, label="end of range")
    ax.fill_between(et, ec, step="post", alpha=0.18, color="#9467bd")
    if start_tick is not None:
        ax.axvline(start_tick, color="#7f7f7f", ls=":", lw=1.0, label=f"start tick {start_tick}")
    if end_tick is not None:
        ax.axvline(end_tick, color="#ff7f0e", ls="--", lw=1.0, label=f"end tick {end_tick}")
    # focus on the near-price depth: huge far-OTM positions otherwise dwarf the chart.
    anchors = [t for t in (start_tick, end_tick) if t is not None]
    if anchors and window:
        lo, hi = min(anchors) - window, max(anchors) + window
        ax.set_xlim(lo, hi)
        vis = [c for t, c in zip(et, ec) if lo <= t <= hi]
        if vis:
            ax.set_ylim(0, max(vis) * 1.1)
        title_sfx = f"  [±{window} ticks around price; full curve in CSV]"
    else:
        title_sfx = ""
    ax.set_title("Absolute liquidity distribution over price (start baseline + in-window change)"
                 + title_sfx)
    ax.set_ylabel("active liquidity (L)")
    ax.set_xlabel("tick (higher tick = more WETH per USDC)")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(outpath, dpi=110)
    plt.close(fig)
    return outpath


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-initial-liquidity", dest="use_initial", action="store_false",
                    help="ignore initial_liquidity.csv; plot only the in-window net change")
    ap.set_defaults(use_initial=True)
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    tvl = read_csv("tvl_series.csv")
    swaps = read_csv("swaps_classified.csv")
    dist = read_csv("liquidity_distribution.csv")
    if not (tvl and swaps and dist):
        raise SystemExit("Missing Stage 2 CSVs — run process.py first.")
    initial = read_csv("initial_liquidity.csv")  # Stage 4.1 baseline (optional)

    start_tick = int(swaps[0]["tick"]) if swaps else None
    end_tick = int(swaps[-1]["tick"]) if swaps else None
    print("  ->", plot_tvl(tvl, os.path.join(OUT, "tvl.png")))
    print("  ->", plot_price_flow(swaps, os.path.join(OUT, "price_flow.png")))

    liq_png = os.path.join(OUT, "liquidity_distribution.png")
    if args.use_initial and initial:
        # Stage 4.2: absolute standing curve = start baseline + in-window change
        start_curve, end_curve = build_absolute_curves(initial, dist)
        print("  ->", plot_liquidity_absolute(start_curve, end_curve, liq_png, start_tick, end_tick),
              "(absolute, using initial_liquidity.csv)")
    else:
        # Stage 3 fallback: in-window net change only
        why = "disabled" if not args.use_initial else "initial_liquidity.csv absent"
        print("  ->", plot_liquidity(dist, liq_png, end_tick), f"(net change — {why})")
    print("Done. PNGs in out/.")


if __name__ == "__main__":
    main()
