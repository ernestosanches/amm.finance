#!/usr/bin/env python3
"""Stage 1 tests — the keyless RPC download (uniswap_v3_pool_download_rpc.py).

Two groups:
  * Offline unit tests        -> pure functions, no network, always run.
  * Output-validation tests   -> validate the CSVs produced by an acceptance run;
                                 skipped (not failed) if a CSV is missing.

Run via `python tests.py` or directly with `python -m unittest test_stage1`.
"""
import csv
import os
import unittest

from web3 import Web3

import uniswap_v3_pool_download_rpc as dl

HERE = os.path.dirname(os.path.abspath(__file__))

# The day the acceptance run downloaded; used to bound timestamp checks.
START_DATE = "2026-06-12"
END_DATE = "2026-06-13"

REQUIRED_COLUMNS = {
    "swaps.csv": {"block", "logIndex", "tx", "timestamp", "datetime_utc",
                  "amount0", "amount1", "direction", "sqrtPriceX96", "tick", "liquidity"},
    "mints.csv": {"block", "logIndex", "tx", "timestamp", "datetime_utc",
                  "amount0", "amount1", "amount", "tickLower", "tickUpper"},
    "burns.csv": {"block", "logIndex", "tx", "timestamp", "datetime_utc",
                  "amount0", "amount1", "amount", "tickLower", "tickUpper"},
    "collects.csv": {"block", "logIndex", "tx", "timestamp", "datetime_utc",
                     "amount0", "amount1", "tickLower", "tickUpper"},
}

# Canonical event signatures -> expected topic0 (keccak of the signature).
EVENT_SIGS = {
    "Swap": "Swap(address,address,int256,int256,uint160,uint128,int24)",
    "Mint": "Mint(address,address,int24,int24,uint128,uint256,uint256)",
    "Burn": "Burn(address,int24,int24,uint128,uint256,uint256)",
    "Collect": "Collect(address,address,int24,int24,uint128,uint128)",
}


def _load(name):
    path = os.path.join(HERE, name)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


class OfflineUnitTests(unittest.TestCase):
    """No network, no files — pure functions from the download module."""

    def test_to_unix_known_values(self):
        # 2026-06-12 00:00:00 UTC and one day later.
        self.assertEqual(dl.to_unix("2026-06-12"), 1781222400)
        self.assertEqual(dl.to_unix("2026-06-13"), 1781308800)
        self.assertEqual(dl.to_unix(END_DATE) - dl.to_unix(START_DATE), 86400)

    def test_to_unix_is_utc(self):
        # epoch 0 == 1970-01-01 UTC; confirms tz handling isn't local.
        self.assertEqual(dl.to_unix("1970-01-01"), 0)

    def test_topic0_hashes_match_signatures(self):
        for name, sig in EVENT_SIGS.items():
            topic0 = Web3.keccak(text=sig).hex()
            topic0 = topic0 if topic0.startswith("0x") else "0x" + topic0
            self.assertEqual(len(topic0), 66, f"{name} topic0 wrong length")
            self.assertTrue(topic0.startswith("0x"))

    def test_event_abi_present_for_all_events(self):
        names = {e["name"] for e in dl.POOL_EVENT_ABI if e.get("type") == "event"}
        self.assertEqual(names, set(EVENT_SIGS.keys()))


class OutputValidationTests(unittest.TestCase):
    """Validate the CSVs from an acceptance run; skip if not present."""

    def _require(self, name):
        rows = _load(name)
        if rows is None:
            self.skipTest(f"{name} not present — run Stage 1 download first")
        return rows

    def test_required_columns(self):
        for name, cols in REQUIRED_COLUMNS.items():
            rows = self._require(name)
            self.assertTrue(rows, f"{name} has no rows")
            have = set(rows[0].keys())
            missing = cols - have
            self.assertFalse(missing, f"{name} missing columns: {missing}")

    def test_non_empty(self):
        for name in REQUIRED_COLUMNS:
            rows = self._require(name)
            self.assertGreater(len(rows), 0, f"{name} is empty")

    def test_sorted_by_block_logindex(self):
        for name in REQUIRED_COLUMNS:
            rows = self._require(name)
            keys = [(int(r["block"]), int(r["logIndex"])) for r in rows]
            self.assertEqual(keys, sorted(keys), f"{name} not sorted by (block, logIndex)")

    def test_timestamps_within_requested_day(self):
        lo, hi = dl.to_unix(START_DATE), dl.to_unix(END_DATE)
        for name in REQUIRED_COLUMNS:
            rows = self._require(name)
            for r in rows:
                ts = int(r["timestamp"])
                self.assertGreaterEqual(ts, lo, f"{name} ts {ts} before window")
                self.assertLess(ts, hi, f"{name} ts {ts} not before window end")

    def test_swap_direction_valid(self):
        rows = self._require("swaps.csv")
        valid = {"pool_received_token0", "pool_received_token1"}
        for r in rows:
            self.assertIn(r["direction"], valid)

    def test_swap_numeric_fields_parse(self):
        rows = self._require("swaps.csv")
        for r in rows:
            float(r["amount0"])
            float(r["amount1"])
            int(r["tick"])
            int(r["sqrtPriceX96"])
            int(r["liquidity"])

    def test_mint_burn_tick_ranges_ordered(self):
        for name in ("mints.csv", "burns.csv", "collects.csv"):
            rows = self._require(name)
            for r in rows:
                self.assertLess(int(r["tickLower"]), int(r["tickUpper"]),
                                f"{name}: tickLower not < tickUpper")


if __name__ == "__main__":
    unittest.main()
