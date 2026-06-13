#!/usr/bin/env python3
"""Stage 6.2.2 — render the virtual order book (from build_book.py CSVs).

Two interactive HTML, both with a time slider, fixed Y (no flicker), and a dashed spot line:

  out/depth_virtual.html      -> Level-2: aggregate depth per price band, split bid (buy WETH,
                                 green, below spot) vs ask (sell WETH, red, above spot).
  out/orderbook_virtual.html  -> Level-3: at each price band a STACK of the individual tokenId
                                 orders (one colour per position); grey base = pre-existing
                                 aggregate baseline when present. The spot line splits the book
                                 into the bid region (left/higher price) and ask region (right).

Unlike the Stage 4.3 range view, side is read off the moving spot, so levels flip bid<->ask as
price crosses them. Volume / fees / APR for the day are shown in the caption (daily_metrics.csv).

Usage:
  python plot_book.py            # linear depth (default)
  python plot_book.py --log      # log-Y for the absolute L2 depth chart
"""
import argparse
import os
from collections import OrderedDict

import plotly.graph_objects as go
from plotly.colors import qualitative

import orderbook_engine as ob
import pool_meta
from plot_orderbook import _caption, _slider_and_play, read_csv, write_html

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
PALETTE = qualitative.Dark24
SIDE_COLOR = {"bid": "#2ca02c", "ask": "#d62728", "straddle": "#7f7f7f"}


def _slices(rows):
    """OrderedDict slice_idx -> (HH:MM label, active_tick, [rows]) in slice order."""
    g = OrderedDict()
    for r in rows:
        i = int(r["slice_idx"])
        if i not in g:
            g[i] = [r["slice_dt"][11:16], r["active_tick"], []]
        g[i][2].append(r)
    return g


def _spot_shape(active_tick):
    """Dashed vertical line at the current spot (x is in tick coordinates)."""
    if active_tick in ("", "None", None):
        return []
    return [{"type": "line", "x0": int(active_tick), "x1": int(active_tick), "xref": "x",
             "y0": 0, "y1": 1, "yref": "paper",
             "line": {"color": "#ff7f0e", "dash": "dash", "width": 1.5}}]


def _xaxis(tickmids, d0, d1, title="Price (USDC per WETH)"):
    """X in tick space (even spacing), labelled with the human price at ~7 anchors.

    Price falls as tick rises, so the axis is reversed (range hi->lo) to show price ASCENDING
    left->right — the conventional book layout: bids (low price) left, asks (high price) right.
    """
    lo, hi = min(tickmids), max(tickmids)
    tvals = [round(lo + (hi - lo) * j / 6) for j in range(7)]
    return {"title": title, "tickmode": "array", "range": [hi, lo], "tickvals": tvals,
            "ticktext": [f"{ob.price_usdc_per_weth_from_tick(t, d0, d1):,.0f}" for t in tvals]}


def _metrics_line(metrics):
    if not metrics:
        return ""
    return (f"  Day: volume ${float(metrics['volume_usdc']):,.0f} · "
            f"fees ${float(metrics['fee_usd']):,.0f} · "
            f"APR(total TVL) {float(metrics['apr_total_tvl'])*100:.1f}% · "
            f"APR(active) {float(metrics['apr_active_tvl'])*100:.0f}%")


# --- Level-2 aggregate depth (bid/ask) ---------------------------------------

def build_l2_figure(l2_rows, d0, d1, logy=False, metrics=None):
    groups = _slices(l2_rows)
    midof = lambda r: (int(r["tick_lo"]) + int(r["tick_hi"])) // 2
    all_mids = [midof(r) for r in l2_rows]
    depths = [float(r["depth_weth"]) for r in l2_rows]
    use_log = logy and depths and min(depths) > 0
    if use_log:
        import math
        posv = [v for v in depths if v > 0] or [1.0]
        yrange = [math.log10(min(posv) * 0.8), math.log10(max(posv) * 1.3)]
    else:
        yrange = [0, (max(depths) if depths else 1.0) * 1.08]

    def side_trace(rows, side, name):
        rr = sorted((r for r in rows if r["side"] == side), key=midof)
        return go.Bar(x=[midof(r) for r in rr], y=[float(r["depth_weth"]) for r in rr],
                      marker_color=SIDE_COLOR[side], name=name, legendgroup=side, width=55,
                      customdata=[float(r["price"]) for r in rr],
                      hovertemplate=(f"{name}<br>price %{{customdata:.0f}} USDC/WETH"
                                     "<br>%{y:.3f} WETH<extra></extra>"))

    def frame_data(rows):
        return [side_trace(rows, "bid", "bid (buy WETH)"),
                side_trace(rows, "ask", "ask (sell WETH)"),
                side_trace(rows, "straddle", "at spot")]

    idxs = list(groups)
    frames = [go.Frame(name=str(i), data=frame_data(groups[i][2]),
                       layout=go.Layout(shapes=_spot_shape(groups[i][1]))) for i in idxs]
    updatemenus, sliders = _slider_and_play([(str(i), groups[i][0]) for i in idxs])
    fig = go.Figure(data=frame_data(groups[idxs[0]][2]), frames=frames,
                    layout=go.Layout(shapes=_spot_shape(groups[idxs[0]][1])))
    fig.update_layout(
        barmode="overlay", bargap=0,
        title={"text": "Virtual order book — Level-2 depth over time (bid vs ask)",
               "x": 0.5, "xanchor": "center"},
        xaxis=_xaxis(all_mids, d0, d1),
        yaxis={"title": "Depth at price (WETH)", "type": "log" if use_log else "linear",
               "range": yrange, "exponentformat": "power"},
        margin={"t": 80, "b": 120}, template="plotly_white",
        updatemenus=updatemenus, sliders=sliders,
        annotations=[_caption(
            "Aggregate WETH depth at each price; green = bids (pool buys WETH, below spot), "
            "red = asks (pool sells WETH, above spot). Orange dashed = spot; bands flip side as it "
            f"moves. Y fixed across time.{_metrics_line(metrics)}")])
    return fig


# --- Level-3 per-tokenId order book ------------------------------------------

def build_l3_figure(l3_rows, d0, d1, metrics=None):
    if not l3_rows:
        return go.Figure()
    bands = sorted({(int(r["tick_lo"]), int(r["tick_hi"])) for r in l3_rows})
    mids = [(lo + hi) // 2 for lo, hi in bands]
    band_ix = {b: k for k, b in enumerate(bands)}

    # token order: baseline first (bottom of the stack), then by first appearance
    order, seen = [], set()
    for r in l3_rows:
        t = r["tokenId"]
        if t not in seen:
            seen.add(t)
            order.append(t)
    tokens = (["baseline"] if "baseline" in seen else []) + \
             [t for t in order if t != "baseline"]

    groups = _slices(l3_rows)
    idxs = list(groups)

    def frame_traces(rows):
        # per token: y across every band (0 where it doesn't contribute) -> stacked
        ymap = {t: [0.0] * len(bands) for t in tokens}
        for r in rows:
            b = (int(r["tick_lo"]), int(r["tick_hi"]))
            ymap[r["tokenId"]][band_ix[b]] += float(r["q_weth"])
        traces = []
        for k, t in enumerate(tokens):
            grey = t == "baseline"
            traces.append(go.Bar(
                x=mids, y=ymap[t], width=55,
                name="pre-existing (baseline)" if grey else t,
                marker_color="#cccccc" if grey else PALETTE[(k - 1) % len(PALETTE)],
                legendgroup=t,
                hovertemplate=(("baseline" if grey else f"order {t}") +
                               "<br>%{y:.4f} WETH<extra></extra>")))
        return traces

    def stacked_max(rows):
        col = [0.0] * len(bands)
        for r in rows:
            col[band_ix[(int(r["tick_lo"]), int(r["tick_hi"]))]] += float(r["q_weth"])
        return max(col) if col else 0.0

    ytop = (max((stacked_max(groups[i][2]) for i in idxs), default=0.0) or 1.0) * 1.05
    start = next((p for p, i in enumerate(idxs) if groups[i][2]), 0)
    frames = [go.Frame(name=str(i), data=frame_traces(groups[i][2]),
                       layout=go.Layout(shapes=_spot_shape(groups[i][1]))) for i in idxs]
    updatemenus, sliders = _slider_and_play([(str(i), groups[i][0]) for i in idxs])
    sliders[0]["active"] = start
    init = idxs[start]
    fig = go.Figure(data=frame_traces(groups[init][2]), frames=frames,
                    layout=go.Layout(shapes=_spot_shape(groups[init][1])))
    has_base = "baseline" in seen
    title = ("Virtual order book — Level-3 (per-position orders, "
             + ("baseline + day's orders)" if has_base else "day's orders only)"))
    fig.update_layout(
        barmode="stack", bargap=0,
        title={"text": title, "x": 0.5, "xanchor": "center"},
        xaxis=_xaxis(mids, d0, d1),
        yaxis={"title": "Order size at price (WETH)", "range": [0, ytop],
               "exponentformat": "power"},
        legend={"title": {"text": "order (tokenId)"}, "font": {"size": 9}},
        margin={"t": 80, "b": 120}, template="plotly_white",
        updatemenus=updatemenus, sliders=sliders,
        annotations=[_caption(
            "Each price band is a stack of the individual LP positions (orders) active there — "
            "the Level-3 detail. Orange dashed = spot: bands right of it (higher price) are asks "
            "(sell WETH), left (lower price) are bids. Y fixed across time."
            + (" Grey base = pre-existing aggregate liquidity." if has_base else "")
            + _metrics_line(metrics))])
    return fig


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", dest="logy", action="store_true",
                    help="log-Y for the absolute L2 depth chart (default linear)")
    ap.set_defaults(logy=False)
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    meta = pool_meta.load()
    d0, d1 = pool_meta.decimals(meta)
    l2, l3 = read_csv("book_l2.csv"), read_csv("book_l3.csv")
    metrics_rows = read_csv("daily_metrics.csv")
    metrics = metrics_rows[0] if metrics_rows else None
    if not l2 and not l3:
        raise SystemExit("Missing book_l2.csv / book_l3.csv — run build_book.py first.")
    if l2:
        print("  ->", write_html(build_l2_figure(l2, d0, d1, args.logy, metrics),
                                 os.path.join(OUT, "depth_virtual.html")))
    if l3:
        print("  ->", write_html(build_l3_figure(l3, d0, d1, metrics),
                                 os.path.join(OUT, "orderbook_virtual.html")))
    if metrics:
        print(_metrics_line(metrics).strip())
    print("Done.")


if __name__ == "__main__":
    main()
