#!/usr/bin/env python3
"""Stage 5 — one-command pipeline runner.

Runs every stage in dependency order and produces all artifacts:
  CSVs   : swaps/mints/burns/collects, initial_liquidity, swaps_classified, tvl_series,
           liquidity_distribution, orderbook_slices, depth_slices, usage.csv
  PNGs   : out/tvl.png, out/price_flow.png, out/liquidity_distribution.png
  HTML x4: out/{depth_over_time,orderbook}__{with,without}_initial.html
           (and the canonical out/{depth_over_time,orderbook}.html = the with-initial versions)

Each stage is its own script; this just orchestrates them (subprocess) so the manual
sequence stays the source of truth.

Usage:
  python run_all.py                                   # defaults: ETH/USDC 0.3%, Jun 12 2026
  python run_all.py --start 2026-06-12 --end 2026-06-13 --slices 24
  python run_all.py --no-baseline-rpc                 # skip absolute-TVL/snapshot network reads
"""
import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
DEFAULT_POOL = "0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8"

EXPECTED_HTML = [
    "out/depth_over_time__with_initial.html",
    "out/depth_over_time__without_initial.html",
    "out/orderbook__with_initial.html",
    "out/orderbook__without_initial.html",
]


def plan_steps(pool, start, end, slices, baseline_rpc=True):
    """Ordered list of actions: {"label","run":[cmd...]} or {"copy":(src,dst)}. Pure (unit-tested)."""
    s = str(slices)
    steps = [
        {"label": "Stage 1: download events",
         "run": [PY, "uniswap_v3_pool_download_rpc.py", "--pool", pool, "--start", start, "--end", end]},
        {"label": "Stage 4.1: absolute L2 start snapshot + usage log",
         "run": [PY, "tick_snapshot.py", "--start", start]},
        {"label": "Stage 2: derive series",
         "run": [PY, "process.py"] + (["--baseline-rpc"] if baseline_rpc else [])},
        {"label": "Stage 3 + 4.2: PNG plots",
         "run": [PY, "plot.py"]},
        # 4.3 — run twice; WITHOUT initial first, WITH initial last so canonical files = absolute
        {"label": "Stage 4.3: order-book data (without initial liquidity)",
         "run": [PY, "orderbook.py", "--slices", s, "--no-initial-liquidity"]},
        {"label": "Stage 4.3: figures (without initial liquidity)",
         "run": [PY, "plot_orderbook.py"]},
        {"copy": ("out/depth_over_time.html", "out/depth_over_time__without_initial.html")},
        {"copy": ("out/orderbook.html", "out/orderbook__without_initial.html")},
        {"label": "Stage 4.3: order-book data (with initial liquidity)",
         "run": [PY, "orderbook.py", "--slices", s]},
        {"label": "Stage 4.3: figures (with initial liquidity)",
         "run": [PY, "plot_orderbook.py"]},
        {"copy": ("out/depth_over_time.html", "out/depth_over_time__with_initial.html")},
        {"copy": ("out/orderbook.html", "out/orderbook__with_initial.html")},
    ]
    return steps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default=DEFAULT_POOL)
    ap.add_argument("--start", default="2026-06-12", help="YYYY-MM-DD UTC inclusive")
    ap.add_argument("--end", default="2026-06-13", help="YYYY-MM-DD UTC exclusive")
    ap.add_argument("--slices", type=int, default=24)
    ap.add_argument("--no-baseline-rpc", dest="baseline_rpc", action="store_false",
                    help="skip absolute-TVL/snapshot on-chain reads")
    ap.set_defaults(baseline_rpc=True)
    args = ap.parse_args()

    steps = plan_steps(args.pool, args.start, args.end, args.slices, args.baseline_rpc)
    total = sum(1 for st in steps if "run" in st)
    n = 0
    for st in steps:
        if "run" in st:
            n += 1
            print(f"\n===> [{n}/{total}] {st['label']}")
            subprocess.run(st["run"], cwd=HERE, check=True)
        else:
            src, dst = st["copy"]
            shutil.copy(os.path.join(HERE, src), os.path.join(HERE, dst))
            print(f"     copied -> {dst}")

    print("\nAll stages done. Produced:")
    print("  PNGs: out/tvl.png, out/price_flow.png, out/liquidity_distribution.png")
    print("  HTML (4):")
    for h in EXPECTED_HTML:
        ok = "ok" if os.path.exists(os.path.join(HERE, h)) else "MISSING"
        print(f"    [{ok}] {h}")
    print("  View with:  python serve.py")


if __name__ == "__main__":
    main()
