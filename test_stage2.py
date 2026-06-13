#!/usr/bin/env python3
"""Stage 2 tests — process.py.

Offline unit tests on the pure math against hand-built fixtures with known answers,
plus output-validation on the derived CSVs (skipped if not present).
"""
import csv
import os
import unittest

import process as p

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    path = os.path.join(HERE, name)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


class PriceMathTests(unittest.TestCase):
    def test_price_unit_case(self):
        # sqrtPriceX96 = 2**96 -> raw token1/token0 = 1; equal decimals -> USDC/WETH = 1.
        self.assertAlmostEqual(p.price_usdc_per_weth(2 ** 96, 0, 0), 1.0, places=9)

    def test_price_decimal_adjustment(self):
        # raw price 1, but token0 has 12 fewer decimals -> USDC per WETH = 1e-12 inverted = 1e12.
        self.assertAlmostEqual(p.price_usdc_per_weth(2 ** 96, 6, 18), 1e12, places=0)

    def test_sqrt_and_tick_agree(self):
        # The real sample: tick 202089 and its sqrtPriceX96 should give the same price.
        sqrt = 1936304128467088717556142137565739
        tick = 202089
        ps = p.price_usdc_per_weth(sqrt, 6, 18)
        pt = p.price_from_tick(tick, 6, 18)
        self.assertAlmostEqual(ps, pt, delta=ps * 0.001)  # within 0.1%
        self.assertTrue(1000 < ps < 3000, f"ETH price out of sane range: {ps}")

    def test_price_monotonic(self):
        # higher sqrtPriceX96 -> more token1 per token0 -> CHEAPER USDC-per-WETH.
        lo = p.price_usdc_per_weth(2 ** 96, 6, 18)
        hi = p.price_usdc_per_weth(2 ** 97, 6, 18)
        self.assertLess(hi, lo)


class ClassifyTests(unittest.TestCase):
    def test_buy_sell_mapping(self):
        self.assertEqual(p.classify_side("pool_received_token0"), "buy")
        self.assertEqual(p.classify_side("pool_received_token1"), "sell")

    def test_unknown_raises(self):
        with self.assertRaises(ValueError):
            p.classify_side("nonsense")


class LiquidityReplayTests(unittest.TestCase):
    def test_known_distribution(self):
        mints = [{"amount": "100", "tickLower": "10", "tickUpper": "20"}]
        burns = [{"amount": "40", "tickLower": "10", "tickUpper": "20"}]
        rows = p.build_liquidity_distribution(mints, burns)
        by_tick = {r["tick"]: r for r in rows}
        # net +60 enters at 10, -60 leaves at 20
        self.assertEqual(by_tick[10]["net_liquidity_delta"], 60)
        self.assertEqual(by_tick[20]["net_liquidity_delta"], -60)
        # cumulative: in-range [10,20) carries 60, back to 0 at 20
        self.assertEqual(by_tick[10]["cumulative_liquidity_delta"], 60)
        self.assertEqual(by_tick[20]["cumulative_liquidity_delta"], 0)

    def test_ticks_sorted(self):
        mints = [{"amount": "5", "tickLower": "30", "tickUpper": "5"},
                 {"amount": "5", "tickLower": "1", "tickUpper": "99"}]
        rows = p.build_liquidity_distribution(mints, [])
        ticks = [r["tick"] for r in rows]
        self.assertEqual(ticks, sorted(ticks))


class TvlReplayTests(unittest.TestCase):
    def test_balance_replay(self):
        events = [
            {"block": 1, "logIndex": 0, "timestamp": 1, "datetime_utc": "", "event": "mint",
             "d0": 100.0, "d1": 1.0, "price": 2000.0},   # +100 USDC, +1 WETH
            {"block": 2, "logIndex": 0, "timestamp": 2, "datetime_utc": "", "event": "swap",
             "d0": 50.0, "d1": -0.025, "price": 2000.0},  # pool gains 50 USDC, loses .025 WETH
            {"block": 3, "logIndex": 0, "timestamp": 3, "datetime_utc": "", "event": "collect",
             "d0": -20.0, "d1": 0.0, "price": 2000.0},    # 20 USDC leaves
        ]
        out = p.build_tvl_series(events, 0.0, 0.0)
        self.assertAlmostEqual(out[-1]["balance0_usdc"], 130.0)
        self.assertAlmostEqual(out[-1]["balance1_weth"], 0.975)
        # TVL in USDC = 130 + 0.975*2000
        self.assertAlmostEqual(out[-1]["tvl_usdc"], 130.0 + 0.975 * 2000.0)

    def test_baseline_offset(self):
        events = [{"block": 1, "logIndex": 0, "timestamp": 1, "datetime_utc": "", "event": "swap",
                   "d0": 10.0, "d1": 0.0, "price": 2000.0}]
        rel = p.build_tvl_series(events, 0.0, 0.0)[-1]["balance0_usdc"]
        absolute = p.build_tvl_series(events, 1000.0, 0.0)[-1]["balance0_usdc"]
        self.assertEqual(absolute - rel, 1000.0)


class OutputValidationTests(unittest.TestCase):
    def _require(self, name):
        rows = _load(name)
        if rows is None:
            self.skipTest(f"{name} not present — run Stage 2 (process.py) first")
        return rows

    def test_swaps_classified(self):
        rows = self._require("swaps_classified.csv")
        self.assertTrue(rows)
        for r in rows:
            self.assertIn(r["side"], {"buy", "sell"})
            self.assertGreater(float(r["price_usdc_per_weth"]), 0)
            self.assertGreaterEqual(float(r["amount_usdc"]), 0)

    def test_tvl_series_time_ordered(self):
        rows = self._require("tvl_series.csv")
        ts = [int(r["timestamp"]) for r in rows]
        self.assertEqual(ts, sorted(ts))
        for r in rows:
            self.assertIn(r["basis"], {"relative", "absolute"})
            float(r["tvl_usdc"]); float(r["tvl_weth"])

    def test_liquidity_distribution(self):
        rows = self._require("liquidity_distribution.csv")
        ticks = [int(r["tick"]) for r in rows]
        self.assertEqual(ticks, sorted(ticks))
        for r in rows:
            int(r["net_liquidity_delta"])
            int(r["cumulative_liquidity_delta"])


if __name__ == "__main__":
    unittest.main()
