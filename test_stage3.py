#!/usr/bin/env python3
"""Stage 3 tests — plot.py.

Agg-backend smoke test: render each curve from tiny fixture rows into a temp dir and
assert the PNG exists and is non-empty (we verify files render, not pixels).
Skips cleanly if matplotlib is not installed.
"""
import os
import tempfile
import unittest

try:
    import matplotlib  # noqa: F401
    HAVE_MPL = True
except ImportError:
    HAVE_MPL = False


@unittest.skipUnless(HAVE_MPL, "matplotlib not installed")
class PlotSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import plot
        cls.plot = plot

    def _assert_png(self, path):
        self.assertTrue(os.path.exists(path), f"{path} not created")
        self.assertGreater(os.path.getsize(path), 0, f"{path} is empty")
        with open(path, "rb") as f:
            self.assertEqual(f.read(8), b"\x89PNG\r\n\x1a\n", "not a valid PNG header")

    def test_plot_tvl(self):
        rows = [
            {"timestamp": "1781222423", "tvl_usdc": "20000000", "basis": "absolute"},
            {"timestamp": "1781226023", "tvl_usdc": "20100000", "basis": "absolute"},
        ]
        with tempfile.TemporaryDirectory() as d:
            out = self.plot.plot_tvl(rows, os.path.join(d, "tvl.png"))
            self._assert_png(out)

    def test_plot_price_flow(self):
        rows = [
            {"timestamp": "1781222423", "price_usdc_per_weth": "1674.0",
             "amount_usdc": "100.0", "side": "buy"},
            {"timestamp": "1781226023", "price_usdc_per_weth": "1675.0",
             "amount_usdc": "250.0", "side": "sell"},
        ]
        with tempfile.TemporaryDirectory() as d:
            out = self.plot.plot_price_flow(rows, os.path.join(d, "price_flow.png"))
            self._assert_png(out)

    def test_plot_liquidity(self):
        rows = [
            {"tick": "10", "net_liquidity_delta": "60", "cumulative_liquidity_delta": "60"},
            {"tick": "20", "net_liquidity_delta": "-60", "cumulative_liquidity_delta": "0"},
        ]
        with tempfile.TemporaryDirectory() as d:
            out = self.plot.plot_liquidity(rows, os.path.join(d, "liq.png"), active_tick=15)
            self._assert_png(out)


if __name__ == "__main__":
    unittest.main()
