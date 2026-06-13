#!/usr/bin/env python3
"""Stage 4.3 (display) — interactive, shareable order-book-over-time figures.

Reads the Stage 4.3 slice CSVs and writes two self-contained HTML files (Plotly, embedded JS,
no server, open in any browser) with a time slider + play button:

  out/depth_over_time.html  -> L2: absolute active-liquidity depth vs tick, animated over time
  out/orderbook.html        -> L3: per-position liquidity heatmap (Y=tick, X=positions), over time

Usage:
  python plot_orderbook.py
"""
import argparse
import csv
import math
import os
from collections import OrderedDict

import plotly.graph_objects as go

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")


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
        [{"type": "buttons", "showactive": False, "x": 0.0, "y": 1.18, "xanchor": "left",
          "buttons": [{"label": "▶ play", "method": "animate",
                       "args": [None, {"frame": {"duration": 600, "redraw": True},
                                       "fromcurrent": True}]},
                      {"label": "❚❚ pause", "method": "animate",
                       "args": [[None], {"frame": {"duration": 0, "redraw": True},
                                         "mode": "immediate"}]}]}],
        [{"active": 0, "x": 0.12, "len": 0.88, "xanchor": "left", "y": 1.12,
          "currentvalue": {"prefix": "time (UTC): "}, "steps": steps}],
    )


# --- L2 depth over time ------------------------------------------------------

def build_depth_figure(depth_rows, logy=True):
    groups = _by_slice(depth_rows)
    all_L = [float(r["cumulative_L"]) for r in depth_rows]
    all_t = [int(r["tick"]) for r in depth_rows]
    xrange = [min(all_t), max(all_t)]
    # log only works for strictly-positive data (the absolute/with-baseline case). The
    # relative/net-change view contains negatives & zeros -> fall back to a linear range
    # that actually INCLUDES the negatives (previously clipped at 0, hence "no bars").
    use_log = logy and all_L and min(all_L) > 0

    def trace(rows):
        rows = sorted(rows, key=lambda r: int(r["tick"]))
        return go.Scatter(x=[int(r["tick"]) for r in rows],
                          y=[float(r["cumulative_L"]) for r in rows],
                          mode="lines", fill="tozeroy", line_shape="hv",
                          line={"color": "#9467bd"}, name="active L")

    def active_shape(rows):
        at = rows[0].get("active_tick") if rows else None
        if not at or at in ("", "None"):
            return []
        at = int(at)
        # full-height line (paper coords) so it works on any y-scale
        return [{"type": "line", "x0": at, "x1": at, "xref": "x", "y0": 0, "y1": 1,
                 "yref": "paper", "line": {"color": "#ff7f0e", "dash": "dash", "width": 1}}]

    if use_log:
        pos = [v for v in all_L if v > 0]
        yaxis = {"title": "active liquidity (L) — log", "type": "log",
                 "range": [math.log10(min(pos) * 0.8), math.log10(max(pos) * 1.3)]}
        scale_note = "log Y"
    else:
        lo, hi = min(all_L + [0.0]), max(all_L + [0.0])
        pad = (hi - lo) * 0.06 or 1.0
        yaxis = {"title": "active liquidity (L)", "range": [lo - pad, hi + pad]}
        scale_note = "linear Y (data not all-positive)" if logy else "linear Y"

    idxs = list(groups)
    frames = [go.Frame(name=str(i), data=[trace(groups[i][1])],
                       layout=go.Layout(shapes=active_shape(groups[i][1]))) for i in idxs]
    first = groups[idxs[0]]
    updatemenus, sliders = _slider_and_play([(str(i), groups[i][0]) for i in idxs])
    fig = go.Figure(data=[trace(first[1])], frames=frames,
                    layout=go.Layout(shapes=active_shape(first[1])))
    fig.update_layout(
        title=f"Liquidity depth over time (L2) — orange dashed = price tick  [{scale_note}]",
        xaxis={"title": "tick (higher = more WETH per USDC)", "range": xrange},
        yaxis=yaxis, updatemenus=updatemenus, sliders=sliders, template="plotly_white")
    return fig


# --- L3 per-position heatmap over time ---------------------------------------

def canonical_slices(rows):
    """All (idx, label) in index order — from rows that span every slice (e.g. depth_slices)."""
    seen = OrderedDict()
    for r in rows:
        seen[int(r["slice_idx"])] = r["slice_dt"][11:16]
    return sorted(seen.items())


def build_orderbook_figure(ob_rows, slices=None, k_levels=100):
    if not ob_rows:
        return go.Figure()
    # positions oldest -> newest by mint_time
    pos = OrderedDict()
    for r in ob_rows:
        pos.setdefault(r["pos_id"], int(r["mint_time"]))
    pos_ids = sorted(pos, key=lambda p: (pos[p], p))

    los = [int(r["tickLower"]) for r in ob_rows]
    ups = [int(r["tickUpper"]) for r in ob_rows]
    ymin, ymax = min(los), max(ups)
    levels = [ymin + (ymax - ymin) * i / (k_levels - 1) for i in range(k_levels)]

    by_idx = _by_slice(ob_rows)
    if slices is None:
        slices = [(i, by_idx[i][0]) for i in by_idx]  # only non-empty slices
    groups = {i: (lab, by_idx[i][1] if i in by_idx else []) for i, lab in slices}
    idxs = [i for i, _ in slices]
    col = {p: j for j, p in enumerate(pos_ids)}

    def zmatrix(rows):
        z = [[None] * len(pos_ids) for _ in levels]
        for r in rows:
            j = col[r["pos_id"]]
            lo, up, L = int(r["tickLower"]), int(r["tickUpper"]), int(r["L"])
            val = math.log10(L) if L > 0 else None
            for yi, lev in enumerate(levels):
                if lo <= lev < up:
                    z[yi][j] = val
        return z

    def heat(rows):
        return go.Heatmap(z=zmatrix(rows), x=pos_ids, y=levels, coloraxis="coloraxis",
                          hovertemplate="pos %{x}<br>tick %{y:.0f}<br>log10(L) %{z:.2f}<extra></extra>")

    frames = [go.Frame(name=str(i), data=[heat(groups[i][1])]) for i in idxs]
    updatemenus, sliders = _slider_and_play([(str(i), groups[i][0]) for i in idxs])
    fig = go.Figure(data=[frames[0].data[0]], frames=frames)
    fig.update_layout(
        title="Per-position liquidity over time (L3, in-window positions) — colour = log10(L)",
        xaxis={"title": "position (tickLower_tickUpper), oldest → newest", "type": "category"},
        yaxis={"title": "tick (price level)"},
        coloraxis={"colorscale": "Viridis", "colorbar": {"title": "log10(L)"}},
        updatemenus=updatemenus, sliders=sliders, template="plotly_white")
    return fig


def write_html(fig, path):
    fig.write_html(path, include_plotlyjs=True, full_html=True)  # self-contained, shareable
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--linear", dest="logy", action="store_false",
                    help="linear Y for the depth chart (default: log Y when data is all-positive)")
    ap.set_defaults(logy=True)
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    depth = read_csv("depth_slices.csv")
    ob = read_csv("orderbook_slices.csv")
    if not depth and not ob:
        raise SystemExit("Missing slice CSVs — run orderbook.py first.")
    slices = canonical_slices(depth) if depth else None  # align L3 frames to the full timeline
    if depth:
        print("  ->", write_html(build_depth_figure(depth, args.logy),
                                 os.path.join(OUT, "depth_over_time.html")))
    if ob:
        print("  ->", write_html(build_orderbook_figure(ob, slices),
                                 os.path.join(OUT, "orderbook.html")))
    print("Done. Open the HTML files in a browser (self-contained, shareable).")


if __name__ == "__main__":
    main()
