#!/usr/bin/env python3
"""Stage 6 tests.

6.0 here: pool-metadata + tokenId-linkage pure logic (no network) and output validation on the
linked CSVs when present. The Orderbook engine invariants (6.1) and build_book shape (6.2) are
appended to this file as those sub-stages land.
"""
import os
import unittest

import link_positions as lp

HERE = os.path.dirname(os.path.abspath(__file__))


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


if __name__ == "__main__":
    unittest.main()
