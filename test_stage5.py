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

    def test_two_versions_per_level_no_with_initial_dup(self):
        targets = [dst for _, dst in self._copies()]
        # both levels get a __without_initial copy; the with-initial views are the canonicals
        # (out/orderbook.html, out/depth_over_time.html) -> no __with_initial duplicates.
        self.assertEqual(sorted(targets), ["out/depth_over_time__without_initial.html",
                                           "out/orderbook__without_initial.html"])
        self.assertFalse(any("__with_initial" in t for t in targets))
        for h in ("out/orderbook.html", "out/orderbook__without_initial.html",
                  "out/depth_over_time.html", "out/depth_over_time__without_initial.html"):
            self.assertIn(h, run_all.EXPECTED_HTML)

    def test_with_initial_runs_last(self):
        # canonical out/*.html must end as the absolute (with-initial) view -> the LAST orderbook.py
        # run has no --no-initial-liquidity.
        runs = [st["run"] for st in self.steps if "run" in st]
        last_ob = max(i for i, c in enumerate(runs) if c[1] == "orderbook.py")
        self.assertNotIn("--no-initial-liquidity", runs[last_ob])

    def test_log_flag_propagates_to_figures(self):
        steps = run_all.plan_steps("0xP", "2026-06-12", "2026-06-13", 24, log=True)
        plots = [st["run"] for st in steps if "run" in st and st["run"][1] == "plot_orderbook.py"]
        self.assertTrue(plots and all("--log" in c for c in plots))
        # off by default
        plots0 = [st["run"] for st in self.steps if "run" in st and st["run"][1] == "plot_orderbook.py"]
        self.assertTrue(all("--log" not in c for c in plots0))

    def test_figures_only_skips_pipeline(self):
        steps = run_all.plan_steps("0xP", "2026-06-12", "2026-06-13", 24, figures_only=True)
        scripts = [st["run"][1] for st in steps if "run" in st]
        self.assertNotIn("uniswap_v3_pool_download_rpc.py", scripts)  # no download
        self.assertIn("orderbook.py", scripts)                       # but rebuilds figures
        self.assertIn("plot_orderbook.py", scripts)

    def test_no_baseline_rpc_flag(self):
        steps = run_all.plan_steps("0xP", "2026-06-12", "2026-06-13", 24, baseline_rpc=False)
        proc = next(st["run"] for st in steps if "run" in st and st["run"][1] == "process.py")
        self.assertNotIn("--baseline-rpc", proc)


if __name__ == "__main__":
    unittest.main()
