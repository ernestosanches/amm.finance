#!/usr/bin/env python3
"""Stage 4 tests.

Stage 4.1 (this file): offline unit tests for the tick-snapshot math + the usage counter,
plus output-validation of initial_liquidity.csv / usage.csv when present.
(4.2 / 4.3 tests are added with their sub-stages.)
"""
import csv
import os
import tempfile
import unittest

import tick_snapshot as ts
from usage import UsageCounter

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    path = os.path.join(HERE, name)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


class TickMathTests(unittest.TestCase):
    def test_ticks_in_word(self):
        # bits 0 and 2 set, spacing 60 -> ticks 0 and 120
        self.assertEqual(ts.ticks_in_word(0, 0b101, 60), [0, 120])
        # word 1, bit 0 -> compressed 256 -> tick 256*60
        self.assertEqual(ts.ticks_in_word(1, 0b1, 60), [256 * 60])
        self.assertEqual(ts.ticks_in_word(0, 0, 60), [])

    def test_word_range_covers_space(self):
        lo, hi = ts.word_range(60)
        self.assertLessEqual(lo * 256 * 60, ts.MIN_TICK)
        self.assertGreaterEqual((hi * 256 + 255) * 60, ts.MAX_TICK)

    def test_build_distribution_cumsum(self):
        rows = ts.build_distribution({10: 60, 20: -60})
        self.assertEqual(rows, [
            {"tick": 10, "liquidity_net": 60, "cumulative_liquidity": 60},
            {"tick": 20, "liquidity_net": -60, "cumulative_liquidity": 0},
        ])

    def test_full_range_nets_to_zero(self):
        # any set of positions sums liquidityNet to 0 across the whole range
        rows = ts.build_distribution({10: 60, 20: -60, 5: 100, 30: -100})
        self.assertEqual(rows[-1]["cumulative_liquidity"], 0)

    def test_active_liquidity_at(self):
        rows = ts.build_distribution({10: 60, 20: -60})
        self.assertEqual(ts.active_liquidity_at(rows, 15), 60)   # in range
        self.assertEqual(ts.active_liquidity_at(rows, 5), 0)     # below range
        self.assertEqual(ts.active_liquidity_at(rows, 25), 0)    # above range

    def test_reconciliation_identity(self):
        # two overlapping positions; active L at a tick == sum of nets at ticks <= cur
        net = {0: 100, 50: 30, 80: -30, 120: -100}  # [0,120) carries 100, [50,80) extra 30
        rows = ts.build_distribution(net)
        self.assertEqual(ts.active_liquidity_at(rows, 60), 130)
        self.assertEqual(ts.active_liquidity_at(rows, 10), 100)
        self.assertEqual(ts.active_liquidity_at(rows, 200), 0)


class FakeProvider:
    """Minimal stand-in so we can test the counter without a network."""
    def make_request(self, method, params):
        return {"jsonrpc": "2.0", "id": 1, "result": "0x0"}


class FakeW3:
    def __init__(self):
        self.provider = FakeProvider()


class UsageCounterTests(unittest.TestCase):
    def test_counts_by_method(self):
        w3 = FakeW3()
        uc = UsageCounter().attach(w3)
        w3.provider.make_request("eth_call", [])
        w3.provider.make_request("eth_call", [])
        w3.provider.make_request("eth_getBlockByNumber", [])
        self.assertEqual(uc.total(), 3)
        self.assertEqual(uc.counts["eth_call"], 2)
        self.assertEqual(uc.counts["eth_getBlockByNumber"], 1)

    def test_dump_wide_union(self):
        w3 = FakeW3()
        uc = UsageCounter().attach(w3)
        w3.provider.make_request("eth_call", [])
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "usage.csv")
            uc.dump(label="run1", path=path)
            # second run with a different method -> columns become a union
            uc2 = UsageCounter().attach(FakeW3())
            uc2.counts["eth_chainId"] = 5
            uc2.dump(label="run2", path=path)
            with open(path, newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 2)
            self.assertIn("eth_call", rows[0])
            self.assertIn("eth_chainId", rows[0])
            self.assertEqual(rows[0]["eth_chainId"], "0")   # filled for run1
            self.assertEqual(rows[1]["total"], "5")


try:
    import matplotlib  # noqa: F401
    HAVE_MPL = True
except ImportError:
    HAVE_MPL = False


@unittest.skipUnless(HAVE_MPL, "matplotlib not installed")
class AbsoluteCurveTests(unittest.TestCase):
    """Stage 4.2 — baseline (4.1) + in-window change (Stage 2) = absolute curve."""

    @classmethod
    def setUpClass(cls):
        import plot
        cls.plot = plot

    def test_build_absolute_curves(self):
        initial = [
            {"tick": "10", "liquidity_net": "100", "cumulative_liquidity": "100"},
            {"tick": "20", "liquidity_net": "-100", "cumulative_liquidity": "0"},
        ]
        dist = [
            {"tick": "10", "net_liquidity_delta": "50", "cumulative_liquidity_delta": "50"},
            {"tick": "30", "net_liquidity_delta": "-50", "cumulative_liquidity_delta": "0"},
        ]
        start_curve, end_curve = self.plot.build_absolute_curves(initial, dist)
        # start = baseline cumulative
        self.assertEqual(start_curve, [{"tick": 10, "cumulative": 100},
                                       {"tick": 20, "cumulative": 0}])
        # end = (baseline + delta) cumulative: 10->150, 20->50, 30->0
        self.assertEqual(end_curve, [{"tick": 10, "cumulative": 150},
                                     {"tick": 20, "cumulative": 50},
                                     {"tick": 30, "cumulative": 0}])

    def test_plot_absolute_renders(self):
        start_curve = [{"tick": 10, "cumulative": 100}, {"tick": 20, "cumulative": 0}]
        end_curve = [{"tick": 10, "cumulative": 150}, {"tick": 30, "cumulative": 0}]
        with tempfile.TemporaryDirectory() as d:
            out = self.plot.plot_liquidity_absolute(start_curve, end_curve,
                                                    os.path.join(d, "liq.png"), 15, 25)
            self.assertTrue(os.path.exists(out) and os.path.getsize(out) > 0)
            with open(out, "rb") as f:
                self.assertEqual(f.read(8), b"\x89PNG\r\n\x1a\n")


import orderbook as ob

try:
    import plotly  # noqa: F401
    HAVE_PLOTLY = True
except ImportError:
    HAVE_PLOTLY = False


class OrderbookReplayTests(unittest.TestCase):
    """Stage 4.3 — position replay & slicing (pure)."""

    MINTS = [{"block": "1", "logIndex": "0", "timestamp": "100",
              "tickLower": "10", "tickUpper": "20", "amount": "100"}]
    BURNS = [{"block": "3", "logIndex": "0", "timestamp": "300",
              "tickLower": "10", "tickUpper": "20", "amount": "100"}]

    def test_first_mint_times(self):
        self.assertEqual(ob.first_mint_times(self.MINTS), {(10, 20): 100})

    def test_slice_times(self):
        self.assertEqual(ob.slice_times(0, 300, 4), [0, 100, 200, 300])
        self.assertEqual(ob.slice_times(0, 300, 1), [300])

    def test_mint_active_then_burn_absent(self):
        evs = ob.merge_position_events(self.MINTS, self.BURNS)
        fm = ob.first_mint_times(self.MINTS)
        self.assertEqual(ob.active_positions_at(evs, 50, fm), [])          # before mint
        active = ob.active_positions_at(evs, 200, fm)                      # after mint
        self.assertEqual(len(active), 1)
        self.assertEqual((active[0]["tickLower"], active[0]["tickUpper"], active[0]["L"]),
                         (10, 20, 100))
        self.assertEqual(ob.active_positions_at(evs, 400, fm), [])         # after burn

    def test_burn_only_position_excluded(self):
        # a position with only a burn in-window (pre-existing) has no first-mint -> not shown
        evs = ob.merge_position_events([], self.BURNS)
        self.assertEqual(ob.active_positions_at(evs, 400, {}), [])

    def test_depth_cumulation(self):
        evs = ob.merge_position_events(self.MINTS, [])
        net = ob.net_delta_at(evs, 200)
        self.assertEqual(dict(net), {10: 100, 20: -100})
        absn = ob.absolute_net({5: 7}, net)
        self.assertEqual(absn, {5: 7, 10: 100, 20: -100})
        self.assertEqual(ob.cumulative(net), [(10, 100), (20, 0)])

    def test_active_tick_at(self):
        swaps = [{"timestamp": "100", "tick": "5"}, {"timestamp": "200", "tick": "9"}]
        self.assertEqual(ob.active_tick_at(swaps, 150), 5)
        self.assertEqual(ob.active_tick_at(swaps, 250), 9)
        self.assertIsNone(ob.active_tick_at(swaps, 50))


@unittest.skipUnless(HAVE_PLOTLY, "plotly not installed")
class OrderbookFigureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import plot_orderbook
        cls.po = plot_orderbook

    def _depth(self, cumL_pair):
        return [{"slice_idx": "0", "slice_dt": "2026-06-12T00:00:00+00:00", "active_tick": "12",
                 "tick": "10", "cumulative_L": str(cumL_pair[0])},
                {"slice_idx": "0", "slice_dt": "2026-06-12T00:00:00+00:00", "active_tick": "12",
                 "tick": "20", "cumulative_L": str(cumL_pair[1])}]

    def test_depth_html(self):
        fig = self.po.build_depth_figure(self._depth((100, 50)))
        self.assertEqual(len(fig.frames), 1)
        with tempfile.TemporaryDirectory() as d:
            p = self.po.write_html(fig, os.path.join(d, "depth.html"))
            self.assertGreater(os.path.getsize(p), 0)
            with open(p) as f:
                self.assertIn("plotly", f.read().lower())

    def test_depth_logy_when_positive(self):
        fig = self.po.build_depth_figure(self._depth((100, 50)), logy=True)
        self.assertEqual(fig.layout.yaxis.type, "log")

    def test_depth_linear_when_nonpositive(self):
        # negatives present (relative/net-change case) -> log impossible -> linear, range includes <0
        fig = self.po.build_depth_figure(self._depth((-30, 50)), logy=True)
        self.assertNotEqual(fig.layout.yaxis.type, "log")
        self.assertLess(fig.layout.yaxis.range[0], 0)

    def test_depth_linear_flag(self):
        fig = self.po.build_depth_figure(self._depth((100, 50)), logy=False)
        self.assertNotEqual(fig.layout.yaxis.type, "log")

    def test_tick_to_price(self):
        # tick 0 -> raw price 1; decimal-adjust by 10^(6-18) -> USDC/WETH = 1e12 ... so a sane
        # ETH price lands at a large positive tick. Just check monotonic + positivity here.
        self.assertGreater(self.po.tick_to_price(202089), 1000)   # ~ETH price band
        self.assertLess(self.po.tick_to_price(202089), 3000)
        # higher tick => more WETH per USDC => CHEAPER USDC-per-WETH
        self.assertLess(self.po.tick_to_price(203000), self.po.tick_to_price(201000))

    def test_orderbook_html(self):
        rows = [{"slice_idx": "0", "slice_time": "100", "slice_dt": "2026-06-12T06:00:00+00:00",
                 "pos_id": "10_20", "mint_time": "100", "tickLower": "10", "tickUpper": "20",
                 "L": "100"}]
        fig = self.po.build_orderbook_figure(rows)
        self.assertEqual(fig.layout.barmode, "stack")
        self.assertEqual(len(fig.data), 1)  # one trace per position (one order here)
        with tempfile.TemporaryDirectory() as d:
            p = self.po.write_html(fig, os.path.join(d, "ob.html"))
            self.assertGreater(os.path.getsize(p), 0)

    def test_orderbook_stacks_positions_and_has_units(self):
        # two positions that overlap on a tick band -> both contribute (stack) there
        rows = [
            {"slice_idx": "1", "slice_time": "100", "slice_dt": "2026-06-12T06:00:00+00:00",
             "pos_id": "0_300", "mint_time": "90", "tickLower": "0", "tickUpper": "300", "L": "5"},
            {"slice_idx": "1", "slice_time": "100", "slice_dt": "2026-06-12T06:00:00+00:00",
             "pos_id": "100_200", "mint_time": "100", "tickLower": "100", "tickUpper": "200",
             "L": "7"},
        ]
        fig = self.po.build_orderbook_figure(rows, [(0, "00:00"), (1, "06:00")])
        # opens on first non-empty frame (index 1), not the blank slice 0
        self.assertEqual(fig.layout.sliders[0].active, 1)
        self.assertEqual(len(fig.data), 2)  # two position traces (stacked)
        # at a tick inside BOTH ranges (e.g. 150) both traces are > 0 => they stack
        levels = list(fig.data[0].x)
        mid = min(range(len(levels)), key=lambda i: abs(levels[i] - 150))
        self.assertGreater(fig.data[0].y[mid], 0)
        self.assertGreater(fig.data[1].y[mid], 0)
        # scientific axes: titles carry units
        self.assertIn("Price (USDC per WETH)", fig.layout.xaxis.title.text)
        self.assertIn("Active liquidity", fig.layout.yaxis.title.text)

    def test_depth_fixed_global_yrange(self):
        # depth Y is fixed across frames (no flicker): layout has a range, frames carry none
        depth = [{"slice_idx": "0", "slice_dt": "2026-06-12T00:00:00+00:00", "active_tick": "12",
                  "tick": "10", "cumulative_L": "100"},
                 {"slice_idx": "0", "slice_dt": "2026-06-12T00:00:00+00:00", "active_tick": "12",
                  "tick": "20", "cumulative_L": "50"}]
        fig = self.po.build_depth_figure(depth, logy=True)
        self.assertIsNotNone(fig.layout.yaxis.range)
        self.assertIsNone(fig.frames[0].layout.yaxis.range)

    def test_orderbook_y_is_fixed_no_flicker(self):
        # Y is ONE fixed range for all frames (zero rescales -> no flicker): layout has the range,
        # frames carry none. Range top = global max total (here the 1,000,000 order) + 5% pad.
        rows = []
        for idx, (L, dt) in enumerate([("100", "06:00"), ("1000000", "07:00"), ("50", "08:00")], 1):
            rows.append({"slice_idx": str(idx), "slice_time": "1", "slice_dt": f"2026-06-12T{dt}:00",
                         "pos_id": "10_20", "mint_time": "1", "tickLower": "10", "tickUpper": "20",
                         "L": L})
        slices = [(1, "06:00"), (2, "07:00"), (3, "08:00")]
        fig = self.po.build_orderbook_figure(rows, slices)
        self.assertTrue(all(f.layout.yaxis.range is None for f in fig.frames))  # no per-frame Y
        self.assertAlmostEqual(fig.layout.yaxis.range[1], 1_000_000 * 1.05)     # fixed global top

    def test_orderbook_xtick_window_applied(self):
        rows = [{"slice_idx": "1", "slice_time": "1", "slice_dt": "2026-06-12T06:00:00",
                 "pos_id": "0_300", "mint_time": "1", "tickLower": "0", "tickUpper": "300", "L": "5"}]
        fig = self.po.build_orderbook_figure(rows, [(1, "06:00")], xtick_range=[50, 250])
        # axis reversed (hi->lo tick) so price ascends left->right; window endpoints preserved
        self.assertEqual(sorted(fig.layout.xaxis.range), [50, 250])
        self.assertEqual(list(fig.layout.xaxis.range), [250, 50])

    def test_orderbook_baseline_stacked_under_orders(self):
        # with baseline_curve: a grey base layer is the FIRST stacked trace; Y top includes it
        rows = [{"slice_idx": "1", "slice_time": "1", "slice_dt": "2026-06-12T06:00:00",
                 "pos_id": "100_200", "mint_time": "1", "tickLower": "100", "tickUpper": "200",
                 "L": "7"}]
        baseline = [(0, 1000), (150, 1000)]  # absolute baseline ~1000 across the range
        fig = self.po.build_orderbook_figure(rows, [(1, "06:00")], baseline_curve=baseline)
        self.assertEqual(fig.data[0].name, "pre-existing (baseline)")   # base layer first (bottom)
        self.assertEqual(len(fig.data), 2)                              # baseline + 1 order
        # Y top accommodates baseline(1000) + order(7) at the shared band
        self.assertGreaterEqual(fig.layout.yaxis.range[1], 1007)
        # without baseline -> no base layer
        fig2 = self.po.build_orderbook_figure(rows, [(1, "06:00")])
        self.assertNotEqual(fig2.data[0].name, "pre-existing (baseline)")

    def test_depth_default_linear(self):
        # log is OFF by default now: default build is linear even when all-positive
        fig = self.po.build_depth_figure(self._depth((100, 50)))   # logy default False
        self.assertNotEqual(fig.layout.yaxis.type, "log")


class OutputValidationTests(unittest.TestCase):
    def test_initial_liquidity_csv(self):
        rows = _load("initial_liquidity.csv")
        if rows is None:
            self.skipTest("initial_liquidity.csv not present — run tick_snapshot.py first")
        ticks = [int(r["tick"]) for r in rows]
        self.assertEqual(ticks, sorted(ticks))
        for r in rows:
            int(r["liquidity_net"]); int(r["cumulative_liquidity"])
        # full-range cumulative nets to zero
        self.assertEqual(int(rows[-1]["cumulative_liquidity"]), 0)

    def test_usage_csv(self):
        rows = _load("usage.csv")
        if rows is None:
            self.skipTest("usage.csv not present — run tick_snapshot.py first")
        for r in rows:
            self.assertIn("total", r)
            self.assertGreaterEqual(int(r["total"]), 0)


if __name__ == "__main__":
    unittest.main()
