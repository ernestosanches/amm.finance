#!/usr/bin/env python3
"""Stage 5 — one-command pipeline runner.

Runs every stage in dependency order and produces all artifacts:
  CSVs   : swaps/mints/burns/collects, pool_metadata, mints/burns_linked, positions,
           initial_liquidity, swaps_classified, tvl_series, liquidity_distribution,
           orderbook/depth_slices, book_l2/l3, daily_metrics, usage.csv
  PNGs   : out/tvl.png, out/price_flow.png, out/liquidity_distribution.png
  HTML x8: range view  out/{depth_over_time,orderbook}[__without_initial].html
           virtual book out/{depth_virtual,orderbook_virtual}[__without_initial].html
           (canonical out/*.html = the with-initial versions; daily_metrics from the full book)

Each stage is its own script; this just orchestrates them (subprocess) so the manual
sequence stays the source of truth.

Usage:
  python run_all.py                                   # defaults: ETH/USDC 0.3%, the past 5 days
  python run_all.py --start 2026-06-12 --end 2026-06-13 --slices 24
  python run_all.py --no-baseline-rpc                 # skip absolute-TVL/snapshot network reads
"""
import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
DEFAULT_POOL = "0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8"
DEFAULT_DAYS = 5  # default download window = the past N days (UTC)


def default_range(days: int = DEFAULT_DAYS) -> tuple[str, str]:
    """The past `days` days as (start, end) YYYY-MM-DD UTC (end exclusive = today)."""
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()

# Two versions per level (L2 depth, L3 order book): with-initial = canonical (no copy), without-initial
# = suffixed copy. The with-initial run is LAST so the canonical out/*.html are the absolute
# (baseline-included) views. Both the Stage 4.3 range view AND the Stage 6.2 virtual book are produced.
EXPECTED_HTML = [
    # Stage 6.2 — virtual order book (the real bid/ask book from the engine)
    "out/orderbook_virtual.html",                 # L3 with initial (baseline + per-position orders)
    "out/orderbook_virtual__without_initial.html",  # L3 without initial (day's orders only)
    "out/depth_virtual.html",                     # L2 with initial (bid/ask depth)
    "out/depth_virtual__without_initial.html",    # L2 without initial
    # Stage 4.3 — range view (kept alongside for comparison)
    "out/orderbook.html",                         # L3 with initial (baseline + orders) — canonical
    "out/orderbook__without_initial.html",        # L3 without initial (day's orders only)
    "out/depth_over_time.html",                   # L2 with initial (absolute) — canonical
    "out/depth_over_time__without_initial.html",  # L2 without initial (relative)
]


def plan_steps(pool, start, end, slices, baseline_rpc=True, log=False, figures_only=False):
    """Ordered list of actions: {"label","run":[cmd...]} or {"copy":(src,dst)}. Pure (unit-tested).

    figures_only: skip download/snapshot/process/PNG — just rebuild the 4 HTML from existing CSVs
    (used by `serve.py --log`). log: pass --log to the figure step (absolute depth -> log Y).
    """
    s = str(slices)
    plot = [PY, "plot_orderbook.py"] + (["--log"] if log else [])
    plot_book = [PY, "plot_book.py"] + (["--log"] if log else [])
    # 4.3 range view — both levels get with/without. WITHOUT first -> copy depth+orderbook to
    # __without_initial; WITH last -> leaves canonical out/*.html as the absolute versions.
    figure_steps = [
        {"label": "Stage 4.3: data (without initial liquidity)",
         "run": [PY, "orderbook.py", "--slices", s, "--no-initial-liquidity"]},
        {"label": "Stage 4.3: figures (without initial liquidity)", "run": plot},
        {"copy": ("out/depth_over_time.html", "out/depth_over_time__without_initial.html")},
        {"copy": ("out/orderbook.html", "out/orderbook__without_initial.html")},
        {"label": "Stage 4.3: data (with initial liquidity)",
         "run": [PY, "orderbook.py", "--slices", s]},
        {"label": "Stage 4.3: figures (with initial liquidity)", "run": plot},
    ]
    # 6.2 virtual book — same with/without pattern; WITH last so daily_metrics.csv is the full pool.
    virtual_steps = [
        {"label": "Stage 6.2: virtual book data (without initial liquidity)",
         "run": [PY, "build_book.py", "--slices", s, "--no-initial-liquidity"]},
        {"label": "Stage 6.2: virtual book figures (without initial liquidity)", "run": plot_book},
        {"copy": ("out/depth_virtual.html", "out/depth_virtual__without_initial.html")},
        {"copy": ("out/orderbook_virtual.html", "out/orderbook_virtual__without_initial.html")},
        {"label": "Stage 6.2: virtual book data (with initial liquidity)",
         "run": [PY, "build_book.py", "--slices", s]},
        {"label": "Stage 6.2: virtual book figures (with initial liquidity)", "run": plot_book},
    ]
    if figures_only:
        return figure_steps + virtual_steps
    return [
        {"label": "Stage 1: download events",
         "run": [PY, "uniswap_v3_pool_download_rpc.py", "--pool", pool, "--start", start, "--end", end]},
        {"label": "Stage 6.0: link positions (tokenId) + pool metadata",
         "run": [PY, "link_positions.py", "--pool", pool]},
        {"label": "Stage 4.1: absolute L2 start snapshot + usage log",
         "run": [PY, "tick_snapshot.py", "--start", start]},
        {"label": "Stage 2: derive series",
         "run": [PY, "process.py"] + (["--baseline-rpc"] if baseline_rpc else [])},
        {"label": "Stage 3 + 4.2: PNG plots", "run": [PY, "plot.py"]},
    ] + figure_steps + virtual_steps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default=DEFAULT_POOL)
    ap.add_argument("--start", default=None, help="YYYY-MM-DD UTC inclusive (default: 5 days ago)")
    ap.add_argument("--end", default=None, help="YYYY-MM-DD UTC exclusive (default: today)")
    ap.add_argument("--slices", type=int, default=24)
    ap.add_argument("--no-baseline-rpc", dest="baseline_rpc", action="store_false",
                    help="skip absolute-TVL/snapshot on-chain reads")
    ap.add_argument("--log", action="store_true", help="log Y for the absolute depth chart")
    ap.add_argument("--figures-only", action="store_true",
                    help="rebuild only the HTML figures from existing CSVs (no network)")
    ap.set_defaults(baseline_rpc=True)
    args = ap.parse_args()

    if args.start is None or args.end is None:
        ds, de = default_range()
        args.start = args.start or ds
        args.end = args.end or de
        print(f"date range (default = past {DEFAULT_DAYS} days): {args.start} -> {args.end}")

    steps = plan_steps(args.pool, args.start, args.end, args.slices, args.baseline_rpc,
                       log=args.log, figures_only=args.figures_only)
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
    print("  HTML (8 — virtual book + range view):")
    for h in EXPECTED_HTML:
        ok = "ok" if os.path.exists(os.path.join(HERE, h)) else "MISSING"
        print(f"    [{ok}] {h}")
    print("  View with:  python serve.py")


if __name__ == "__main__":
    main()
