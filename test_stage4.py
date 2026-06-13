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
