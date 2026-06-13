#!/usr/bin/env python3
"""Stage 5 tests — run_all.py orchestration plan (pure; no network/subprocess)."""
import unittest

import run_all


class PlanStepsTests(unittest.TestCase):
    def setUp(self):
        self.steps = run_all.plan_steps("0xPOOL", "2026-06-12", "2026-06-13", 24)

    def _scripts(self):
        return [st["run"][1] for st in self.steps if "run" in st]

    def _copies(self):
        return [st["copy"] for st in self.steps if "copy" in st]

    def test_runs_all_stages_in_order(self):
        scripts = self._scripts()
        # Stage 1 first; process before plot; tick_snapshot before plot
        self.assertEqual(scripts[0], "uniswap_v3_pool_download_rpc.py")
        for s in ("tick_snapshot.py", "process.py", "plot.py", "orderbook.py", "plot_orderbook.py"):
            self.assertIn(s, scripts)
        self.assertLess(scripts.index("tick_snapshot.py"), scripts.index("plot.py"))
        self.assertLess(scripts.index("process.py"), scripts.index("plot.py"))

    def test_orderbook_runs_both_variants(self):
        ob_cmds = [st["run"] for st in self.steps if "run" in st and st["run"][1] == "orderbook.py"]
        self.assertEqual(len(ob_cmds), 2)
        with_flag = ["--no-initial-liquidity" in c for c in ob_cmds]
        self.assertIn(True, with_flag)   # one without initial
        self.assertIn(False, with_flag)  # one with initial

    def test_passes_pool_and_dates(self):
        dl = next(st["run"] for st in self.steps if "run" in st
                  and st["run"][1] == "uniswap_v3_pool_download_rpc.py")
        self.assertIn("0xPOOL", dl)
        self.assertIn("2026-06-12", dl)
        self.assertIn("2026-06-13", dl)

    def test_produces_four_html_copies(self):
        targets = [dst for _, dst in self._copies()]
        for h in run_all.EXPECTED_HTML:
            self.assertIn(h, targets)
        self.assertEqual(len(targets), 4)

    def test_with_initial_copied_last(self):
        # canonical out/*.html should end up = the WITH-initial run -> its copies come last
        copies = self._copies()
        last_two_dst = [dst for _, dst in copies[-2:]]
        self.assertTrue(all("__with_initial" in d for d in last_two_dst))

    def test_no_baseline_rpc_flag(self):
        steps = run_all.plan_steps("0xP", "2026-06-12", "2026-06-13", 24, baseline_rpc=False)
        proc = next(st["run"] for st in steps if "run" in st and st["run"][1] == "process.py")
        self.assertNotIn("--baseline-rpc", proc)


if __name__ == "__main__":
    unittest.main()
