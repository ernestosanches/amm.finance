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
        # Stage 1 first; link (6.0) after download & before process; process before plot
        self.assertEqual(scripts[0], "uniswap_v3_pool_download_rpc.py")
        for s in ("link_positions.py", "tick_snapshot.py", "process.py", "plot.py",
                  "orderbook.py", "plot_orderbook.py", "build_book.py", "plot_book.py"):
            self.assertIn(s, scripts)
        self.assertLess(scripts.index("link_positions.py"), scripts.index("process.py"))
        self.assertLess(scripts.index("tick_snapshot.py"), scripts.index("plot.py"))
        self.assertLess(scripts.index("process.py"), scripts.index("plot.py"))
        # the virtual book (6.2) builds before it plots
        self.assertLess(scripts.index("build_book.py"), scripts.index("plot_book.py"))

    def test_orderbook_runs_both_variants(self):
        ob_cmds = [st["run"] for st in self.steps if "run" in st and st["run"][1] == "orderbook.py"]
        self.assertEqual(len(ob_cmds), 2)
        with_flag = ["--no-initial-liquidity" in c for c in ob_cmds]
        self.assertIn(True, with_flag)   # one without initial
        self.assertIn(False, with_flag)  # one with initial

    def test_build_book_runs_both_variants(self):
        cmds = [st["run"] for st in self.steps if "run" in st and st["run"][1] == "build_book.py"]
        self.assertEqual(len(cmds), 2)
        flags = ["--no-initial-liquidity" in c for c in cmds]
        self.assertIn(True, flags)   # one without initial (orders only)
        self.assertIn(False, flags)  # one with initial (canonical + daily_metrics)

    def test_passes_pool_and_dates(self):
        dl = next(st["run"] for st in self.steps if "run" in st
                  and st["run"][1] == "uniswap_v3_pool_download_rpc.py")
        self.assertIn("0xPOOL", dl)
        self.assertIn("2026-06-12", dl)
        self.assertIn("2026-06-13", dl)

    def test_two_versions_per_level_no_with_initial_dup(self):
        targets = [dst for _, dst in self._copies()]
        # every level (range L2/L3 + virtual L2/L3) gets a __without_initial copy; the with-initial
        # views are the canonicals -> no __with_initial duplicates.
        self.assertEqual(sorted(targets), [
            "out/depth_over_time__without_initial.html",
            "out/depth_virtual__without_initial.html",
            "out/orderbook__without_initial.html",
            "out/orderbook_virtual__without_initial.html"])
        self.assertFalse(any("__with_initial" in t for t in targets))
        # all 8 HTML (4 virtual + 4 range view) are declared
        self.assertEqual(len(run_all.EXPECTED_HTML), 8)
        for h in ("out/orderbook_virtual.html", "out/orderbook_virtual__without_initial.html",
                  "out/depth_virtual.html", "out/depth_virtual__without_initial.html",
                  "out/orderbook.html", "out/orderbook__without_initial.html",
                  "out/depth_over_time.html", "out/depth_over_time__without_initial.html"):
            self.assertIn(h, run_all.EXPECTED_HTML)

    def test_with_initial_runs_last(self):
        # canonical out/*.html must end as the absolute (with-initial) view -> the LAST orderbook.py
        # and build_book.py runs have no --no-initial-liquidity.
        runs = [st["run"] for st in self.steps if "run" in st]
        for script in ("orderbook.py", "build_book.py"):
            last = max(i for i, c in enumerate(runs) if c[1] == script)
            self.assertNotIn("--no-initial-liquidity", runs[last])

    def test_log_flag_propagates_to_figures(self):
        steps = run_all.plan_steps("0xP", "2026-06-12", "2026-06-13", 24, log=True)
        for script in ("plot_orderbook.py", "plot_book.py"):
            plots = [st["run"] for st in steps if "run" in st and st["run"][1] == script]
            self.assertTrue(plots and all("--log" in c for c in plots), script)
        # off by default
        plots0 = [st["run"] for st in self.steps if "run" in st
                  and st["run"][1] in ("plot_orderbook.py", "plot_book.py")]
        self.assertTrue(all("--log" not in c for c in plots0))

    def test_figures_only_skips_pipeline(self):
        steps = run_all.plan_steps("0xP", "2026-06-12", "2026-06-13", 24, figures_only=True)
        scripts = [st["run"][1] for st in steps if "run" in st]
        self.assertNotIn("uniswap_v3_pool_download_rpc.py", scripts)  # no download
        self.assertNotIn("link_positions.py", scripts)               # no network linkage
        for s in ("orderbook.py", "plot_orderbook.py", "build_book.py", "plot_book.py"):
            self.assertIn(s, scripts)                                # but rebuilds all figures

    def test_no_baseline_rpc_flag(self):
        steps = run_all.plan_steps("0xP", "2026-06-12", "2026-06-13", 24, baseline_rpc=False)
        proc = next(st["run"] for st in steps if "run" in st and st["run"][1] == "process.py")
        self.assertNotIn("--baseline-rpc", proc)


if __name__ == "__main__":
    unittest.main()
