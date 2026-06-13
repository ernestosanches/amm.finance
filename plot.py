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


def main():
    os.makedirs(OUT, exist_ok=True)
    tvl = read_csv("tvl_series.csv")
    swaps = read_csv("swaps_classified.csv")
    dist = read_csv("liquidity_distribution.csv")
    if not (tvl and swaps and dist):
        raise SystemExit("Missing Stage 2 CSVs — run process.py first.")

    active_tick = int(swaps[-1]["tick"]) if swaps else None
    print("  ->", plot_tvl(tvl, os.path.join(OUT, "tvl.png")))
    print("  ->", plot_price_flow(swaps, os.path.join(OUT, "price_flow.png")))
    print("  ->", plot_liquidity(dist, os.path.join(OUT, "liquidity_distribution.png"), active_tick))
    print("Done. PNGs in out/.")


if __name__ == "__main__":
    main()
