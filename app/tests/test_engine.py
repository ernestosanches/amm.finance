"""B1 — engine invariants (ORDERS.md §7), pointed at the LIVE swap loop."""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import engine as E
from backend.engine import Engine

SPACING = 60
P0 = 3000.0


def fresh(gamma=0.003, price=P0):
    return Engine(SPACING, gamma, price)


def wide_position(eng, pid=1, owner=1, span_ticks=6000, budget=1_000_000.0):
    """A range straddling spot, wide enough that ordinary swaps stay in-range."""
    t = E.price_to_tick(eng.price())
    q = eng.quote_range(t - span_ticks, t + span_ticks, budget)
    eng.add_position(pid, owner, "v3", q)
    return q


class DepositMathTests(unittest.TestCase):
    def test_range_above_price_is_all_eth0(self):
        eng = fresh()
        t = E.price_to_tick(P0)
        q = eng.quote_range(t + 1000, t + 4000, 50_000)   # entirely above spot
        self.assertGreater(q["amount_eth0"], 0)
        self.assertAlmostEqual(q["amount_usd0"], 0.0, places=6)
        self.assertAlmostEqual(q["value_usd0"], 50_000, places=2)

    def test_range_below_price_is_all_usd0(self):
        eng = fresh()
        t = E.price_to_tick(P0)
        q = eng.quote_range(t - 4000, t - 1000, 50_000)   # entirely below spot
        self.assertAlmostEqual(q["amount_eth0"], 0.0, places=9)
        self.assertGreater(q["amount_usd0"], 0)
        self.assertAlmostEqual(q["value_usd0"], 50_000, places=2)

    def test_straddle_needs_both(self):
        eng = fresh()
        t = E.price_to_tick(P0)
        q = eng.quote_range(t - 2000, t + 2000, 80_000)
        self.assertGreater(q["amount_eth0"], 0)
        self.assertGreater(q["amount_usd0"], 0)
        self.assertAlmostEqual(q["value_usd0"], 80_000, places=2)

    def test_curve_rejects_negative(self):
        eng = fresh()
        with self.assertRaises(ValueError):
            eng.quote_curve({E.price_to_tick(P0): -1.0}, 1000)

    def test_reserves_equal_sum_of_positions(self):
        eng = fresh()
        wide_position(eng, pid=1)
        eng.quote_curve  # noqa
        q2 = eng.quote_range(E.price_to_tick(P0) - 600, E.price_to_tick(P0) + 600, 30_000)
        eng.add_position(2, 5, "v3", q2)
        rx, ry = eng.reserves()
        sx = sy = 0.0
        for pid in (1, 2):
            x, y = eng.position_amounts(pid)
            sx += x
            sy += y
        self.assertAlmostEqual(rx, sx, places=6)
        self.assertAlmostEqual(ry, sy, places=4)


class SwapInvariantTests(unittest.TestCase):
    def test_blended_price_is_geometric_mean(self):
        eng = fresh(gamma=0.0)
        wide_position(eng)
        sP0 = eng.sqrtP
        r = eng.swap(False, 20_000)            # buy ETH0 with 20k USD0, price up
        sP1 = eng.sqrtP
        blended = r.amount_in / r.amount_out   # USD0 in / ETH0 out (gamma=0 -> net==gross)
        self.assertAlmostEqual(blended, sP0 * sP1, delta=blended * 1e-9)

    def test_path_independence_zero_fee(self):
        eng = fresh(gamma=0.0)
        wide_position(eng)
        p_start = eng.price()
        r1 = eng.swap(False, 25_000)           # buy ETH0
        eth_got = r1.amount_out
        r2 = eng.swap(True, eth_got)           # sell exactly what we got back
        self.assertAlmostEqual(eng.price(), p_start, delta=p_start * 1e-7)
        self.assertAlmostEqual(r2.amount_out, 25_000, delta=25_000 * 1e-7)

    def test_swap_never_mutates_position_L(self):
        eng = fresh()
        q = wide_position(eng)
        before = dict(eng.positions[1].profile)
        eng.swap(False, 50_000)
        eng.swap(True, 10)
        self.assertEqual(before, eng.positions[1].profile)

    def test_conservation_per_swap(self):
        eng = fresh(gamma=0.003)
        wide_position(eng)
        bx, by = eng.reserves()
        r = eng.swap(True, 5.0)                 # sell 5 ETH0
        ax, ay = eng.reserves()
        net_in = r.amount_in - r.fee            # ETH0 into reserves (fee skimmed to LPs)
        self.assertAlmostEqual(ax - bx, net_in, delta=abs(net_in) * 1e-7 + 1e-9)
        self.assertAlmostEqual(by - ay, r.amount_out, delta=abs(r.amount_out) * 1e-7 + 1e-6)
        self.assertAlmostEqual(sum(r.fee_by_position.values()), r.fee, delta=r.fee * 1e-9 + 1e-12)

    def test_fee_matches_gamma(self):
        eng = fresh(gamma=0.003)
        wide_position(eng)
        r = eng.swap(False, 10_000)
        self.assertAlmostEqual(r.fee, r.amount_in * 0.003, delta=r.amount_in * 1e-9)

    def test_marginal_spread_is_fee(self):
        eng = fresh(gamma=0.003)
        wide_position(eng)
        P = eng.price()
        # tiny buy -> effective price ~ P/(1-gamma); tiny sell -> ~ P*(1-gamma)
        rb = eng.swap(False, 1.0)
        ask = rb.amount_in / rb.amount_out
        self.assertAlmostEqual(ask, P / (1 - 0.003), delta=P * 1e-3)


class CrossTickTests(unittest.TestCase):
    def test_swap_crosses_bands_and_changes_active_L(self):
        eng = fresh(gamma=0.0)
        t = E.price_to_tick(P0)
        # thin band straddling spot, thicker band above -> a big buy crosses up into it
        q1 = eng.quote_range(t - 120, t + 120, 5_000)
        q2 = eng.quote_range(t + 120, t + 3000, 500_000)
        eng.add_position(1, 1, "v3", q1)
        eng.add_position(2, 2, "v3", q2)
        L_before = eng.active_L()
        eng.swap(False, 200_000)               # buy a lot of ETH0, push price up across boundary
        self.assertGreater(eng.price(), P0)
        self.assertNotAlmostEqual(eng.active_L(), L_before, delta=1.0)

    def test_empty_region_halts_swap(self):
        eng = fresh(gamma=0.0)
        t = E.price_to_tick(P0)
        # liquidity only just around spot; a huge buy exhausts it and must stop (consume < ask)
        q = eng.quote_range(t - 120, t + 120, 1_000)
        eng.add_position(1, 1, "v3", q)
        r = eng.swap(False, 10_000_000)
        self.assertLess(r.amount_in, 10_000_000)   # could not consume it all
        self.assertGreater(r.amount_in, 0)


class AddRemoveTests(unittest.TestCase):
    def test_add_remove_roundtrip(self):
        eng = fresh()
        t = E.price_to_tick(P0)
        q = eng.quote_range(t - 1200, t + 1200, 40_000)
        x_in, y_in = eng.add_position(7, 3, "v3", q)
        x_out, y_out = eng.remove_position(7)
        self.assertAlmostEqual(x_in, x_out, places=9)
        self.assertAlmostEqual(y_in, y_out, places=6)
        self.assertNotIn(7, eng.positions)
        self.assertEqual(sum(1 for v in eng.band_L.values() if abs(v) > 1e-9), 0)

    def test_book_sides_split_around_spot(self):
        eng = fresh()
        wide_position(eng)
        rows = eng.book()
        self.assertTrue(rows)
        for row in rows:
            if row["side"] == "bid":
                self.assertLessEqual(row["tick_upper"], E.price_to_tick(eng.price()) + 1)
            if row["side"] == "ask":
                self.assertGreaterEqual(row["tick_lower"], E.price_to_tick(eng.price()) - SPACING)
        self.assertTrue(any(r["side"] == "bid" for r in rows))
        self.assertTrue(any(r["side"] == "ask" for r in rows))


if __name__ == "__main__":
    unittest.main()
