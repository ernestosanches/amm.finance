#!/usr/bin/env python3
"""Stage 4.3/4.5 (display) — interactive, shareable order-book-over-time figures.

Reads the slice CSVs and writes two self-contained HTML files (Plotly, embedded JS, no server)
with a time slider + play button:

  out/orderbook.html        -> L3 stacked-bar order book: at each PRICE, the bar height is the
                               total active liquidity, decomposed into individual LP positions
                               (one coloured segment per order). Slider = time.
  out/depth_over_time.html  -> L2 aggregate depth: total active liquidity vs price, over time.

Axes use real units (price in USDC per WETH; L in Uniswap v3 virtual-liquidity units).

Usage:
  python plot_orderbook.py            # log-Y depth when all-positive; --linear to force linear
"""
import argparse
import bisect
import csv
import math
import os
from collections import OrderedDict

import plotly.graph_objects as go
from plotly.colors import qualitative

import pool_meta

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
PALETTE = qualitative.Dark24  # one distinct colour per LP position (order)


def tick_to_price(tick, d0=6, d1=18):
    """Price as USDC per WETH at a Uniswap v3 tick (token0=USDC 6dp, token1=WETH 18dp)."""
    weth_per_usdc = (1.0001 ** tick) * (10 ** (d0 - d1))
    return 1.0 / weth_per_usdc


def read_csv(name):
    path = os.path.join(HERE, name)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _by_slice(rows):
    """Group rows by slice_idx, preserving order; returns OrderedDict idx -> (label, rows)."""
    groups = OrderedDict()
    for r in rows:
        idx = int(r["slice_idx"])
        groups.setdefault(idx, (r["slice_dt"][11:16], []))[1].append(r)  # HH:MM label
    return groups


def _slider_and_play(labels):
    steps = [{"args": [[name], {"frame": {"duration": 0, "redraw": True}, "mode": "immediate"}],
              "label": lab, "method": "animate"} for name, lab in labels]
    return (
        [{"type": "buttons", "showactive": False, "x": 0.0, "y": 1.16, "xanchor": "left",
          "buttons": [{"label": "▶ play", "method": "animate",
                       "args": [None, {"frame": {"duration": 600, "redraw": True},
                                       "fromcurrent": True}]},
                      {"label": "❚❚ pause", "method": "animate",
                       "args": [[None], {"frame": {"duration": 0, "redraw": True},
                                         "mode": "immediate"}]}]}],
        [{"active": 0, "x": 0.12, "len": 0.88, "xanchor": "left", "y": 1.10,
          "currentvalue": {"prefix": "time (UTC): "}, "steps": steps}],
    )


def _caption(text):
    return {"text": text, "xref": "paper", "yref": "paper", "x": 0.5, "y": -0.20,
            "xanchor": "center", "yanchor": "top", "showarrow": False,
            "font": {"size": 11, "color": "#555"}}


def canonical_slices(rows):
    """All (idx, label) in index order — from rows that span every slice (e.g. depth_slices)."""
    seen = OrderedDict()
    for r in rows:
        seen[int(r["slice_idx"])] = r["slice_dt"][11:16]
    return sorted(seen.items())


# --- L2 aggregate depth over time --------------------------------------------

def build_depth_figure(depth_rows, logy=False, d0=6, d1=18):
    groups = _by_slice(depth_rows)
    all_L = [float(r["cumulative_L"]) for r in depth_rows]
    prices_all = [tick_to_price(int(r["tick"]), d0, d1) for r in depth_rows]
    xrange = [min(prices_all), max(prices_all)]
    # log only works for strictly-positive data (absolute/with-baseline). The relative/net-change
    # view has negatives & zeros -> linear range that INCLUDES the negatives.
    use_log = logy and all_L and min(all_L) > 0
    ytype = "log" if use_log else "linear"

    def trace(rows):
        rows = sorted(rows, key=lambda r: int(r["tick"]))
        return go.Scatter(x=[tick_to_price(int(r["tick"]), d0, d1) for r in rows],
                          y=[float(r["cumulative_L"]) for r in rows],
                          mode="lines", fill="tozeroy", line_shape="hv",
                          line={"color": "#9467bd"}, name="active L",
                          hovertemplate="price %{x:.0f} USDC/WETH<br>L %{y:.3g}<extra></extra>")

    def active_shape(rows):
        at = rows[0].get("active_tick") if rows else None
        if not at or at in ("", "None"):
            return []
        px = tick_to_price(int(at), d0, d1)
        return [{"type": "line", "x0": px, "x1": px, "xref": "x", "y0": 0, "y1": 1,
                 "yref": "paper", "line": {"color": "#ff7f0e", "dash": "dash", "width": 1.5}}]

    # FIXED global Y across all frames -> no flicker (absolute depth is ~constant anyway).
    allvals = all_L or [0.0, 1.0]
    if use_log:
        posv = [v for v in allvals if v > 0] or [1.0]
        yrange = [math.log10(min(posv) * 0.8), math.log10(max(posv) * 1.3)]
    else:
        lo, hi = min(allvals + [0.0]), max(allvals + [0.0])
        pad = (hi - lo) * 0.06 or 1.0
        yrange = [lo - pad, hi + pad]

    idxs = list(groups)
    frames = [go.Frame(name=str(i), data=[trace(groups[i][1])],
                       layout=go.Layout(shapes=active_shape(groups[i][1]))) for i in idxs]
    first = groups[idxs[0]]
    updatemenus, sliders = _slider_and_play([(str(i), groups[i][0]) for i in idxs])
    fig = go.Figure(data=[trace(first[1])], frames=frames,
                    layout=go.Layout(shapes=active_shape(first[1])))
    basis = "absolute" if use_log else "relative / net change"
    fig.update_layout(
        title={"text": f"Aggregate liquidity depth over time (Level-2, {basis} basis)",
               "x": 0.5, "xanchor": "center"},
        xaxis={"title": "Price (USDC per WETH)", "range": xrange},
        yaxis={"title": "Active liquidity, L  (Uniswap v3 units, ∝ √(USDC·WETH))",
               "type": ytype, "range": yrange, "exponentformat": "power"},
        margin={"t": 80, "b": 110},
        annotations=[_caption(
            "Total active liquidity vs price at each timestamp (UTC). Orange dashed = current price. "
            f"Y is {'log' if use_log else 'linear'}, fixed across time. "
            "L is virtual liquidity, not a USD amount.")],
        updatemenus=updatemenus, sliders=sliders, template="plotly_white")
    return fig


# --- L3 stacked-bar order book over time -------------------------------------

def _baseline_by_level(baseline_curve, levels):
    """Step-lookup the absolute pre-existing liquidity at each level tick (or None if no baseline)."""
    if not baseline_curve:
        return None
    bc = sorted(baseline_curve)
    bt = [t for t, _ in bc]
    bl = [L for _, L in bc]
    out = []
    for t in levels:
        i = bisect.bisect_right(bt, t) - 1
        out.append(bl[i] if i >= 0 else 0)
    return out


def build_orderbook_figure(ob_rows, slices=None, k_levels=140, d0=6, d1=18,
                           xtick_range=None, baseline_curve=None):
    """L3 order book: at each price, a stacked bar of the individual LP positions (orders).

    With `baseline_curve` (start-of-range absolute liquidity per tick), a grey base layer of the
    pre-existing aggregate liquidity is stacked UNDER the day's orders -> the absolute standing book
    (the orders are a small sliver on the much larger baseline). Without it, just the day's orders.
    Fixed linear Y (no flicker; a stacked bar cannot be log-scaled); X windowed via `xtick_range`;
    opens on the first non-empty frame.
    """
    if not ob_rows:
        return go.Figure()
    pos = OrderedDict()
    for r in ob_rows:
        pos.setdefault(r["pos_id"], int(r["mint_time"]))
    pos_ids = sorted(pos, key=lambda p: (pos[p], p))  # oldest -> newest

    los = [int(r["tickLower"]) for r in ob_rows]
    ups = [int(r["tickUpper"]) for r in ob_rows]
    tmin, tmax = min(los), max(ups)
    levels = [round(tmin + (tmax - tmin) * i / (k_levels - 1)) for i in range(k_levels)]
    prices = [tick_to_price(t, d0, d1) for t in levels]
    base_y = _baseline_by_level(baseline_curve, levels)

    by_idx = _by_slice(ob_rows)
    if slices is None:
        slices = [(i, by_idx[i][0]) for i in by_idx]
    groups = {i: (lab, by_idx[i][1] if i in by_idx else []) for i, lab in slices}
    idxs = [i for i, _ in slices]

    def label(pid):
        lo, up = pid.split("_")
        return f"[{lo}, {up}]"

    def frame_traces(rows):
        traces = []
        if base_y is not None:  # grey base layer = pre-existing aggregate, stacked first (bottom)
            traces.append(go.Bar(
                x=levels, y=base_y, customdata=prices, name="pre-existing (baseline)",
                marker_color="#cccccc", legendgroup="baseline",
                hovertemplate=("pre-existing baseline<br>price %{customdata:.0f} USDC/WETH"
                               "<br>L %{y:.3g}<extra></extra>")))
        active = {r["pos_id"]: (int(r["tickLower"]), int(r["tickUpper"]), int(r["L"])) for r in rows}
        for k, p in enumerate(pos_ids):
            lo, up, L = active.get(p, (0, 0, 0))
            y = [L if (L > 0 and lo <= t < up) else 0 for t in levels]
            traces.append(go.Bar(
                x=levels, y=y, customdata=prices, name=label(p),
                marker_color=PALETTE[k % len(PALETTE)], legendgroup=p,
                hovertemplate=(f"order {label(p)}<br>price %{{customdata:.0f}} USDC/WETH"
                               "<br>L %{y:.3g}<extra></extra>")))
        return traces

    def frame_total(rows):  # tallest stacked bar this frame (incl. baseline)
        live = [(int(r["tickLower"]), int(r["tickUpper"]), int(r["L"])) for r in rows
                if int(r["L"]) > 0]
        best = 0.0
        for i, t in enumerate(levels):
            s = (base_y[i] if base_y is not None else 0) + sum(L for lo, up, L in live if lo <= t < up)
            best = max(best, s)
        return float(best)

    # ONE fixed Y for every frame -> zero flicker (a stacked bar can't be log; single global max).
    ytop = (max((frame_total(groups[i][1]) for i in idxs), default=0.0) or 1.0) * 1.05

    start_pos = next((p for p, i in enumerate(idxs) if groups[i][1]), 0)  # first non-empty frame
    frames = [go.Frame(name=str(i), data=frame_traces(groups[i][1])) for i in idxs]  # no per-frame Y
    updatemenus, sliders = _slider_and_play([(str(i), groups[i][0]) for i in idxs])
    sliders[0]["active"] = start_pos
    init_i = idxs[start_pos]
    fig = go.Figure(data=frame_traces(groups[init_i][1]), frames=frames)

    if xtick_range:  # window the price axis (x data is tick; labels are price) with labels inside it
        lo_t, hi_t = xtick_range
        tvals = [round(lo_t + (hi_t - lo_t) * j / 6) for j in range(7)]
        xaxis = {"title": "Price (USDC per WETH)", "tickmode": "array", "range": xtick_range,
                 "tickvals": tvals, "ticktext": [f"{tick_to_price(t, d0, d1):,.0f}" for t in tvals]}
    else:
        sel = sorted(set(int(round(j * (k_levels - 1) / 7)) for j in range(8)))
        xaxis = {"title": "Price (USDC per WETH)", "tickmode": "array",
                 "tickvals": [levels[i] for i in sel], "ticktext": [f"{prices[i]:,.0f}" for i in sel]}

    if base_y is not None:
        title = "Level-3 order book over time — absolute (pre-existing baseline + day's orders)"
        cap = ("Grey base = pre-existing aggregate liquidity at start of range; coloured segments = "
               "the day's individual orders stacked on top (a thin sliver vs the baseline). "
               "Slider = time (UTC); Y fixed & linear. L is virtual liquidity, not USD.")
    else:
        title = "Level-3 order book over time — the day's in-window orders"
        cap = ("Each bar = total active liquidity at that price; coloured segments = individual LP "
               "positions (orders). Slider = time (UTC); Y fixed & linear. L is virtual liquidity, "
               "not USD.")

    fig.update_layout(
        barmode="stack", bargap=0,
        title={"text": title, "x": 0.5, "xanchor": "center"},
        xaxis=xaxis,
        yaxis={"title": "Active liquidity, L  (Uniswap v3 units, ∝ √(USDC·WETH))",
               "range": [0, ytop], "exponentformat": "power"},
        legend={"title": {"text": "LP position [tickLower, tickUpper]"}, "font": {"size": 9}},
        margin={"t": 80, "b": 110},
        annotations=[_caption(cap)],
        template="plotly_white", updatemenus=updatemenus, sliders=sliders)
    return fig


def active_tick_window(depth_rows, margin=3500):
    """[min_active_tick - margin, max_active_tick + margin] for windowing the order book x-axis."""
    ticks = [int(r["active_tick"]) for r in depth_rows
             if str(r.get("active_tick")) not in ("", "None", "")]
    if not ticks:
        return None
    return [min(ticks) - margin, max(ticks) + margin]


def write_html(fig, path):
    fig.write_html(path, include_plotlyjs=True, full_html=True)  # self-contained, shareable
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", dest="logy", action="store_true",
                    help="log Y for the ABSOLUTE depth chart (default OFF: linear everywhere). "
                         "Has no effect on the relative depth or the stacked order book.")
    ap.set_defaults(logy=False)
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    meta = pool_meta.load()           # decimals from the single source of truth (else 6/18)
    d0, d1 = pool_meta.decimals(meta)
    depth = read_csv("depth_slices.csv")
    ob = read_csv("orderbook_slices.csv")
    base = read_csv("orderbook_baseline.csv")  # populated only on a with-initial run
    baseline_curve = [(int(r["tick"]), int(r["baseline_L"])) for r in base]
    if not depth and not ob:
        raise SystemExit("Missing slice CSVs — run orderbook.py first.")
    slices = canonical_slices(depth) if depth else None  # align L3 frames to the full timeline
    xwin = active_tick_window(depth) if depth else None   # window L3 x-axis to the active price band
    if depth:
        print("  ->", write_html(build_depth_figure(depth, args.logy, d0, d1),
                                 os.path.join(OUT, "depth_over_time.html")))
    if ob:
        print("  ->", write_html(
            build_orderbook_figure(ob, slices, d0=d0, d1=d1, xtick_range=xwin,
                                   baseline_curve=baseline_curve),
            os.path.join(OUT, "orderbook.html")))
    print("Done. Open the HTML files in a browser (self-contained, shareable).")


if __name__ == "__main__":
    main()
