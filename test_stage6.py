#!/usr/bin/env python3
"""Stage 6 tests.

6.0 here: pool-metadata + tokenId-linkage pure logic (no network) and output validation on the
linked CSVs when present. The Orderbook engine invariants (6.1) and build_book shape (6.2) are
appended to this file as those sub-stages land.
"""
import math
import os
import unittest
from collections import defaultdict

import build_book as bb
import link_positions as lp
import orderbook_engine as ob
import pool_meta

HERE = os.path.dirname(os.path.abspath(__file__))


def _rel(a, b):
    """Relative closeness, robust at the float magnitudes raw liquidity math produces."""
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def _csv(name):
    import csv
    path = os.path.join(HERE, name)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


# --- 6.0: pure join logic ----------------------------------------------------

class GammaTests(unittest.TestCase):
    def test_known_fee_tiers(self):
        self.assertEqual(lp.gamma_from_fee(3000), 0.003)   # 0.30%
        self.assertEqual(lp.gamma_from_fee(500), 0.0005)   # 0.05%
        self.assertEqual(lp.gamma_from_fee(10000), 0.01)   # 1.00%


def _inc_data(liquidity, amount0=7, amount1=9):
    """ABI-encode an IncreaseLiquidity data field: liquidity, amount0, amount1 (3x uint256 words)."""
    return "0x" + "".join(f"{v:064x}" for v in (liquidity, amount0, amount1))


def _topic_uint(v):
    return "0x" + f"{v:064x}"


class ClassifyNfpmLogTests(unittest.TestCase):
    def test_increase_parsed(self):
        res = lp.classify_nfpm_log(lp.NFPM, lp.INC_TOPIC, _topic_uint(12345), _inc_data(999))
        self.assertEqual(res, ("increase", 12345, 999))

    def test_decrease_parsed(self):
        res = lp.classify_nfpm_log(lp.NFPM, lp.DEC_TOPIC, _topic_uint(42), _inc_data(7))
        self.assertEqual(res, ("decrease", 42, 7))

    def test_wrong_contract_ignored(self):
        other = "0x0000000000000000000000000000000000000001"
        self.assertIsNone(lp.classify_nfpm_log(other, lp.INC_TOPIC, _topic_uint(1), _inc_data(1)))

    def test_unrelated_topic_ignored(self):
        transfer = "0x" + "de" * 32
        self.assertIsNone(lp.classify_nfpm_log(lp.NFPM, transfer, _topic_uint(1), _inc_data(1)))

    def test_address_case_insensitive(self):
        res = lp.classify_nfpm_log(lp.NFPM.lower(), lp.INC_TOPIC, _topic_uint(5), _inc_data(3))
        self.assertEqual(res, ("increase", 5, 3))


class MatchTokenidTests(unittest.TestCase):
    def setUp(self):
        self.evs = [{"kind": "increase", "tokenId": 100, "liquidity": 555},
                    {"kind": "decrease", "tokenId": 200, "liquidity": 80}]

    def test_exact_liquidity_match(self):
        self.assertEqual(lp.match_tokenid(self.evs, "increase", 555), 100)
        self.assertEqual(lp.match_tokenid(self.evs, "decrease", 80), 200)

    def test_no_match_returns_none(self):
        self.assertIsNone(lp.match_tokenid(self.evs, "increase", 999))

    def test_zero_liquidity_poke_matches_exactly(self):
        evs = [{"kind": "decrease", "tokenId": 7, "liquidity": 0}]
        self.assertEqual(lp.match_tokenid(evs, "decrease", 0), 7)

    def test_pure_collect_no_decrease_event_returns_none(self):
        self.assertIsNone(lp.match_tokenid([], "decrease", 0))

    def test_ambiguous_no_exact_returns_none(self):
        evs = [{"kind": "increase", "tokenId": 1, "liquidity": 10},
               {"kind": "increase", "tokenId": 2, "liquidity": 20}]
        self.assertIsNone(lp.match_tokenid(evs, "increase", 99))


class BuildPositionsTests(unittest.TestCase):
    def _mint(self, tid, lo, up, amount, ts):
        return {"tokenId": str(tid), "identity": str(tid), "tickLower": lo, "tickUpper": up,
                "amount": amount, "timestamp": ts, "owner": lp.NFPM}

    def _burn(self, tid, lo, up, amount, ts):
        r = self._mint(tid, lo, up, amount, ts)
        return r

    def test_netting_counts_and_first_mint(self):
        mints = [self._mint(1, 100, 200, 50, 1000), self._mint(1, 100, 200, 30, 1500)]
        burns = [self._burn(1, 100, 200, 20, 2000)]
        pos = lp.build_positions(mints, burns)
        self.assertEqual(len(pos), 1)
        p = pos[0]
        self.assertEqual(p["net_L_in_window"], 60)      # 50 + 30 - 20
        self.assertEqual(p["add_count"], 2)
        self.assertEqual(p["remove_count"], 1)
        self.assertEqual(p["first_mint_ts"], 1000)      # earliest mint

    def test_distinct_tokenids_kept_apart(self):
        mints = [self._mint(1, 100, 200, 50, 1000), self._mint(2, 100, 200, 70, 1200)]
        pos = lp.build_positions(mints, [])
        self.assertEqual({p["identity"] for p in pos}, {"1", "2"})
        self.assertEqual(pos[0]["identity"], "1")        # ordered by first_mint_ts

    def test_burn_only_position_has_no_first_mint(self):
        burns = [self._burn(9, 100, 200, 40, 3000)]
        pos = lp.build_positions([], burns)
        self.assertEqual(pos[0]["net_L_in_window"], -40)
        self.assertIsNone(pos[0]["first_mint_ts"])

    def test_zero_amount_poke_burns_skipped(self):
        # a 0-liquidity poke (no tokenId, owner-range identity) must not form a phantom position
        poke = {"tokenId": "", "identity": "0xNFPM:100:200", "tickLower": 100, "tickUpper": 200,
                "amount": 0, "timestamp": 3000, "owner": "0xNFPM"}
        pos = lp.build_positions([self._mint(1, 100, 200, 50, 1000)], [poke])
        self.assertEqual([p["identity"] for p in pos], ["1"])


class IdentityOfTests(unittest.TestCase):
    def test_tokenid_when_linked(self):
        self.assertEqual(lp.identity_of(123, "0xowner", 10, 20), "123")

    def test_owner_range_fallback_when_unlinked(self):
        self.assertEqual(lp.identity_of(None, "0xowner", 10, 20), "0xowner:10:20")


# --- 6.0: output validation (skipped if the acceptance run hasn't produced them) ---

class PoolMetadataCsvTests(unittest.TestCase):
    def setUp(self):
        self.rows = _csv("pool_metadata.csv")
        if self.rows is None:
            self.skipTest("pool_metadata.csv absent (run link_positions.py)")

    def test_single_row_with_required_columns(self):
        self.assertEqual(len(self.rows), 1)
        for col in ("pool", "token0", "token1", "symbol0", "symbol1",
                    "decimals0", "decimals1", "fee", "gamma", "tickSpacing"):
            self.assertIn(col, self.rows[0])

    def test_gamma_matches_fee(self):
        r = self.rows[0]
        self.assertAlmostEqual(float(r["gamma"]), int(r["fee"]) / 1_000_000)

    def test_tickspacing_positive(self):
        self.assertGreater(int(self.rows[0]["tickSpacing"]), 0)


class PositionsCsvTests(unittest.TestCase):
    def setUp(self):
        self.rows = _csv("positions.csv")
        if self.rows is None:
            self.skipTest("positions.csv absent (run link_positions.py)")

    def test_columns_and_ranges(self):
        for r in self.rows:
            self.assertLess(int(r["tickLower"]), int(r["tickUpper"]))
            self.assertGreaterEqual(int(r["add_count"]) + int(r["remove_count"]), 1)

    def test_linked_mints_carry_a_tokenid(self):
        mints = _csv("mints_linked.csv")
        if mints is None:
            self.skipTest("mints_linked.csv absent")
        # in this pool every in-window mint is NFPM-routed -> all linked
        self.assertTrue(all(r["tokenId"] for r in mints))


# --- 6.1: engine math invariants (the ORDERS.md contract) --------------------

class TickSqrtPriceTests(unittest.TestCase):
    def test_zero_tick_is_one(self):
        self.assertTrue(_rel(ob.tick_to_sqrt_price(0), 1.0))

    def test_monotonic_in_tick(self):
        self.assertLess(ob.tick_to_sqrt_price(100), ob.tick_to_sqrt_price(200))

    def test_matches_price_definition(self):
        # sP**2 == P == 1.0001**tick
        self.assertTrue(_rel(ob.tick_to_sqrt_price(500) ** 2, 1.0001 ** 500))


class TelescopingTests(unittest.TestCase):
    """Summing band orders over sub-bands equals the single closed-form order. ORDERS.md §4."""
    def test_q0_q1_sum_over_subbands(self):
        L = 10 ** 18
        ticks = [0, 60, 120, 180, 240]
        sPs = [ob.tick_to_sqrt_price(t) for t in ticks]
        q0_sum = sum(ob.band_order(L, sPs[i], sPs[i + 1])[0] for i in range(len(ticks) - 1))
        q1_sum = sum(ob.band_order(L, sPs[i], sPs[i + 1])[1] for i in range(len(ticks) - 1))
        self.assertTrue(_rel(q0_sum, L * (1 / sPs[0] - 1 / sPs[-1])))
        self.assertTrue(_rel(q1_sum, L * (sPs[-1] - sPs[0])))


class VirtualReserveTests(unittest.TestCase):
    def test_product_is_L_squared_and_ratio_is_price(self):
        L, sPa, sPb = 5 * 10 ** 17, ob.tick_to_sqrt_price(-300), ob.tick_to_sqrt_price(300)
        sP = ob.tick_to_sqrt_price(50)
        xv, yv = ob.virtual_reserves(L, sPa, sPb, sP)
        self.assertTrue(_rel(xv * yv, float(L) ** 2))
        self.assertTrue(_rel(yv / xv, sP ** 2))            # == P


class PathIndependenceTests(unittest.TestCase):
    """Zero-fee round trip returns spot and inventory exactly. ORDERS.md §7.3."""
    def test_sell_then_buy_back_returns_spot(self):
        L, sP = 10 ** 18, ob.tick_to_sqrt_price(100)
        amount0_in = 10 ** 15
        sP2, out1 = ob.swap_step(sP, L, amount0_in, zero_for_one=True)    # token0 in, price down
        sP3, out0 = ob.swap_step(sP2, L, out1, zero_for_one=False)        # token1 back in, up
        self.assertTrue(_rel(sP3, sP))
        self.assertTrue(_rel(out0, amount0_in))


class GeometricMeanFillTests(unittest.TestCase):
    def test_band_fill_is_geomean(self):
        L, sP_i, sP_j = 10 ** 18, ob.tick_to_sqrt_price(0), ob.tick_to_sqrt_price(120)
        q0, q1, pbar = ob.band_order(L, sP_i, sP_j)
        self.assertTrue(_rel(pbar, q1 / q0))
        self.assertTrue(_rel(pbar, sP_i * sP_j))           # geometric mean of P_i, P_j


class SwapStepConservationTests(unittest.TestCase):
    def test_avg_fill_is_geomean_of_endpoints(self):
        L, sP = 10 ** 18, ob.tick_to_sqrt_price(100)
        a_in = 3 * 10 ** 15
        sP2, out = ob.swap_step(sP, L, a_in, zero_for_one=True)
        self.assertTrue(_rel(out / a_in, sP * sP2))        # blended price = sqrt(P*P')


class SpreadTests(unittest.TestCase):
    """Multiplicative half-spread equals the fee per side, independent of price. ORDERS.md §7.5."""
    def test_constant_spread(self):
        gamma = 0.003
        for P in (0.5, 1.0, 1700.0, 1e6):
            bid, ask = ob.marginal_prices(P, gamma)
            self.assertTrue(_rel(ask / P, 1 / (1 - gamma)))
            self.assertTrue(_rel(bid / P, 1 - gamma))


class AprTests(unittest.TestCase):
    def test_annualized(self):
        self.assertTrue(_rel(ob.annualized_apr(100.0, 365000.0), 0.1))   # 100/365000*365
        self.assertEqual(ob.annualized_apr(100.0, 0.0), 0.0)             # guard div-by-zero


# --- 6.1: stateful book behaviour --------------------------------------------

class OrderbookSwapTests(unittest.TestCase):
    def setUp(self):
        self.book = ob.Orderbook(gamma=0.003, d0=6, d1=18)
        self.book.apply_mint("A", lo=200000, up=204000, L=10 ** 18)

    def test_swap_never_mutates_position_L(self):
        before = self.book.positions["A"]["L"]
        self.book.apply_swap("buy", amount0=1000.0, amount1=0.6, price=1700.0,
                             tick=202000, active_L=10 ** 18)
        self.assertEqual(self.book.positions["A"]["L"], before)

    def test_fee_skim_into_separate_counter(self):
        self.book.apply_swap("buy", amount0=1000.0, amount1=0.6, price=1700.0,
                             tick=202000, active_L=10 ** 18)   # USDC in -> fee in token0
        self.assertTrue(_rel(self.book.fees0["A"], 1000.0 * 0.003))
        self.assertEqual(self.book.fees1["A"], 0.0)

    def test_out_of_range_position_earns_no_fee(self):
        self.book.apply_swap("buy", amount0=1000.0, amount1=0.6, price=1700.0,
                             tick=210000, active_L=10 ** 18)   # spot outside [200000,204000)
        self.assertEqual(self.book.fees0["A"], 0.0)

    def test_total_fee_equals_sum_gross_times_gamma(self):
        swaps = [("buy", 1000.0, 0.6, 1700.0), ("sell", 0.0, 0.5, 1710.0),
                 ("buy", 2000.0, 1.2, 1705.0)]
        for side, a0, a1, px in swaps:
            self.book.apply_swap(side, a0, a1, px, tick=202000, active_L=10 ** 18)
        exp0 = (1000.0 + 2000.0) * 0.003
        exp1 = 0.5 * 0.003
        self.assertTrue(_rel(self.book.fee0_total, exp0))
        self.assertTrue(_rel(self.book.fee1_total, exp1))


class BookSideTests(unittest.TestCase):
    def setUp(self):
        self.book = ob.Orderbook(gamma=0.003, d0=6, d1=18)
        self.book.apply_mint("A", lo=201000, up=203400, L=10 ** 18)
        self.book.tick = 202200   # spot in the middle

    def test_sides_flip_around_spot(self):
        bands = self.book.book_at([201000, 201600, 202200, 202800, 203400])
        sides = {(b["tick_lo"], b["tick_hi"]): b["side"] for b in bands}
        # band entirely below current tick 202200 -> higher USDC/WETH -> ASK
        self.assertEqual(sides[(201000, 201600)], "ask")
        # band entirely above current tick -> lower USDC/WETH -> BID
        self.assertEqual(sides[(202800, 203400)], "bid")

    def test_side_recomputes_when_spot_moves(self):
        # the SAME band flips side as spot crosses it (the core ORDERS.md behaviour)
        hi = [b["side"] for b in self.book.book_at([201600, 202200], tick=203000)][0]
        lo = [b["side"] for b in self.book.book_at([201600, 202200], tick=201000)][0]
        self.assertEqual(hi, "ask")   # spot above band -> band below spot -> ask
        self.assertEqual(lo, "bid")   # spot below band -> band above spot -> bid

    def test_aggregate_is_sum_of_positions(self):
        self.book.apply_mint("B", lo=201000, up=203400, L=3 * 10 ** 17)
        band = self.book.book_at([201000, 201600])[0]
        self.assertTrue(_rel(band["agg_q1"],
                             sum(v["q1"] for v in band["positions"].values())))


class HumanPriceTests(unittest.TestCase):
    def test_usdc_per_weth_from_tick(self):
        # the live pool sat near tick 202089 ~ 1674 USDC/WETH
        px = ob.price_usdc_per_weth_from_tick(202089, 6, 18)
        self.assertTrue(1500 < px < 1850)


# --- 6.1: end-to-end replay on the real data (skips if CSVs absent) ----------

class EngineReplayTests(unittest.TestCase):
    def setUp(self):
        meta = _csv("pool_metadata.csv")
        swaps = _csv("swaps.csv")
        if meta is None or swaps is None:
            self.skipTest("pool_metadata.csv / swaps.csv absent (run Stage 1 + link_positions.py)")
        self.meta, self.swaps = meta[0], swaps

    def _replay(self):
        m = self.meta
        book = ob.Orderbook(gamma=float(m["gamma"]), d0=int(m["decimals0"]), d1=int(m["decimals1"]))
        mints = _csv("mints_linked.csv") or []
        burns = _csv("burns_linked.csv") or []
        events = []
        for r in mints:
            events.append((int(r["block"]), int(r["logIndex"]), "mint", r))
        for r in burns:
            events.append((int(r["block"]), int(r["logIndex"]), "burn", r))
        for r in self.swaps:
            events.append((int(r["block"]), int(r["logIndex"]), "swap", r))
        events.sort(key=lambda e: (e[0], e[1]))
        d0, d1 = int(m["decimals0"]), int(m["decimals1"])
        for _, _, kind, r in events:
            if kind == "mint" and r["tokenId"]:
                book.apply_mint(r["tokenId"], r["tickLower"], r["tickUpper"], r["amount"])
            elif kind == "burn" and r["tokenId"] and int(r["amount"]) > 0:
                book.apply_burn(r["tokenId"], r["tickLower"], r["tickUpper"], r["amount"])
            elif kind == "swap":
                a0, a1 = abs(float(r["amount0"])), abs(float(r["amount1"]))
                side = "buy" if r["direction"] == "pool_received_token0" else "sell"
                price = ob.price_usdc_per_weth_from_tick(int(r["tick"]), d0, d1)
                book.apply_swap(side, a0, a1, price, int(r["tick"]), int(r["liquidity"]))
        return book

    def test_volume_and_fee_relationship(self):
        book = self._replay()
        s = book.daily_stats()
        self.assertGreater(s["swap_count"], 0)
        self.assertGreaterEqual(s["volume_usdc"], 0)
        # fee_usdc must equal gamma * (USDC volume of buy-side swaps) <= gamma * total USDC volume
        self.assertLessEqual(s["fee_usdc"], float(self.meta["gamma"]) * s["volume_usdc"] + 1e-6)
        self.assertGreaterEqual(s["fee_usd"], 0)

    def test_apr_is_finite_and_nonnegative(self):
        book = self._replay()
        # use a nominal TVL to exercise the APR path (real TVL comes from tvl_series in 6.2)
        s = book.daily_stats(tvl_usd=1e7, active_tvl_usd=1e5)
        self.assertGreaterEqual(s["apr_total_tvl"], 0)
        self.assertGreater(s["apr_active_tvl"], s["apr_total_tvl"])   # smaller base -> higher APR
        self.assertTrue(math.isfinite(s["apr_total_tvl"]))


# --- 6.2.0: shared metadata loader -------------------------------------------

class PoolMetaFallbackTests(unittest.TestCase):
    def test_decimals_fallback_when_absent(self):
        self.assertEqual(pool_meta.decimals(None, 6, 18), (6, 18))
        self.assertEqual(pool_meta.decimals(None, 8, 9), (8, 9))

    def test_symbols_fallback_when_absent(self):
        self.assertEqual(pool_meta.symbols(None), ("USDC", "WETH"))
        self.assertEqual(pool_meta.symbols(None, "A", "B"), ("A", "B"))

    def test_meta_overrides_fallback(self):
        meta = {"decimals0": 9, "decimals1": 8, "symbol0": "FOO", "symbol1": "BAR"}
        self.assertEqual(pool_meta.decimals(meta, 6, 18), (9, 8))
        self.assertEqual(pool_meta.symbols(meta), ("FOO", "BAR"))


class PoolMetaLoadTests(unittest.TestCase):
    def setUp(self):
        self.meta = pool_meta.load()
        if self.meta is None:
            self.skipTest("pool_metadata.csv absent (run link_positions.py)")

    def test_typed_fields(self):
        self.assertIsInstance(self.meta["decimals0"], int)
        self.assertIsInstance(self.meta["gamma"], float)
        self.assertIsInstance(self.meta["tickSpacing"], int)
        self.assertAlmostEqual(self.meta["gamma"], self.meta["fee"] / 1_000_000)


# --- 6.2.1: build_book replay helpers + output validation --------------------

class BuildEventsTests(unittest.TestCase):
    def test_sorted_by_block_then_logindex_with_kinds(self):
        mints = [{"block": "5", "logIndex": "2"}]
        burns = [{"block": "5", "logIndex": "1"}]
        swaps = [{"block": "4", "logIndex": "9"}]
        ev = bb.build_events(mints, burns, swaps)
        self.assertEqual([(b, l, k) for b, l, k, _ in ev],
                         [(4, 9, "swap"), (5, 1, "burn"), (5, 2, "mint")])


class FixedGridTests(unittest.TestCase):
    def test_windows_and_includes_position_boundaries(self):
        mints = [{"tickLower": "201000", "tickUpper": "203000"}]
        burns = []
        baseline = {200000: 1, 202000: 1, 500000: 1}   # 500000 is far outside the window
        swaps = [{"tick": "202000"}]
        grid, (lo, hi) = bb.fixed_grid(mints, burns, baseline, swaps, window=3000)
        self.assertEqual((lo, hi), (199000, 205000))
        self.assertIn(201000, grid)       # position boundary inside window
        self.assertIn(203000, grid)
        self.assertNotIn(500000, grid)    # outside window dropped
        self.assertEqual(grid, sorted(grid))


class ApplyEventTests(unittest.TestCase):
    def test_mint_then_swap(self):
        book = ob.Orderbook(gamma=0.003, d0=6, d1=18)
        bb.apply_event(book, "mint", {"tokenId": "1", "tickLower": "200000",
                                      "tickUpper": "204000", "amount": "1000"}, 6, 18)
        self.assertEqual(book.positions["1"]["L"], 1000)
        tick = bb.apply_event(book, "swap", {"direction": "pool_received_token0",
                              "amount0": "1000", "amount1": "0.6", "tick": "202000",
                              "liquidity": "1000"}, 6, 18)
        self.assertEqual(tick, 202000)
        self.assertGreater(book.fees0["1"], 0)   # in-range -> earned token0 fee


class BookCsvTests(unittest.TestCase):
    def setUp(self):
        self.l2 = _csv("book_l2.csv")
        self.l3 = _csv("book_l3.csv")
        if self.l2 is None:
            self.skipTest("book_l2.csv absent (run build_book.py)")

    def test_side_consistent_with_active_tick(self):
        for r in self.l2:
            at, lo, hi = int(r["active_tick"]), int(r["tick_lo"]), int(r["tick_hi"])
            if r["side"] == "ask":
                self.assertLessEqual(hi, at)         # band below current tick = above spot price
            elif r["side"] == "bid":
                self.assertGreaterEqual(lo, at)      # band above current tick = below spot price
            elif r["side"] == "straddle":
                self.assertTrue(lo < at < hi)

    def test_l3_aggregates_to_l2(self):
        if self.l3 is None:
            self.skipTest("book_l3.csv absent")
        agg = defaultdict(float)
        for r in self.l3:
            agg[(r["slice_idx"], r["tick_lo"], r["tick_hi"])] += float(r["q_weth"])
        for r in self.l2:
            key = (r["slice_idx"], r["tick_lo"], r["tick_hi"])
            self.assertTrue(_rel(agg[key], float(r["depth_weth"])))


class DailyMetricsCsvTests(unittest.TestCase):
    def setUp(self):
        rows = _csv("daily_metrics.csv")
        if rows is None:
            self.skipTest("daily_metrics.csv absent (run build_book.py)")
        self.m = rows[0]

    def test_fee_is_gamma_times_volume_ish(self):
        # fee_usd should be on the order of gamma * volume (both legs valued in USD)
        vol, fee, g = float(self.m["volume_usdc"]), float(self.m["fee_usd"]), float(self.m["gamma"])
        self.assertGreater(fee, 0)
        self.assertLess(fee, vol)                       # fee is a small fraction of volume
        self.assertLess(abs(fee - g * vol) / (g * vol), 0.5)   # within 2x of one-leg estimate

    def test_active_apr_exceeds_total_apr(self):
        self.assertGreater(float(self.m["apr_active_tvl"]), float(self.m["apr_total_tvl"]))


if __name__ == "__main__":
    unittest.main()
