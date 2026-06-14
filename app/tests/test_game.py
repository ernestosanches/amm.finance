"""B3 — game core: bags, seeding, lifecycle, oracle determinism, conservation, replay."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import config, engine as E
from backend.game import Game, GameError
from backend.persistence import Store


def small_params(**kw):
    p = config.GameParams(d0=3000.0, x=10_000.0, k=3.0, walk_step=1.0, game_length=12.0,
                          sigma=0.01, oracle_seed=42)
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def new_game(tmp, **kw):
    store = Store(os.path.join(tmp, "g.db"))
    return Game(store, small_params(**kw)), store


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)


class RegistrationTests(TmpCase):
    def test_balanced_bag(self):
        g, _ = new_game(self.tmp)
        r = g.register("alice")
        self.assertAlmostEqual(r["balance_usd0"], 5000.0)
        self.assertAlmostEqual(r["balance_eth0"], 5000.0 / 3000.0)

    def test_duplicate_name_rejected(self):
        g, _ = new_game(self.tmp)
        g.register("bob")
        with self.assertRaises(GameError):
            g.register("bob")


class LifecycleTests(TmpCase):
    def test_start_seeds_and_runs(self):
        g, _ = new_game(self.tmp)
        g.register("alice")
        g.start()
        self.assertEqual(g.phase, "RUNNING")
        self.assertGreater(g.pools["v3"].tvl_usd0(), 0)
        self.assertGreater(g.pools["curve"].tvl_usd0(), 0)
        de, du = g.conservation_drift()
        self.assertLess(abs(de), 1e-3)
        self.assertLess(abs(du), 1e-3)

    def test_trading_blocked_before_start(self):
        g, _ = new_game(self.tmp)
        a = g.register("alice")["account_id"]
        with self.assertRaises(GameError):
            g.act(a, "buy", {"pool": "v3", "amount_in": 100})

    def test_phase_transitions_to_settled(self):
        g, _ = new_game(self.tmp, game_length=5.0, walk_step=1.0, settlement_freeze_steps=1)
        g.register("alice")
        g.start()
        phases = []
        for _ in range(5):
            g.tick()
            phases.append(g.phase)
        self.assertIn("FREEZE", phases)
        self.assertEqual(g.phase, "SETTLED")


class TradingTests(TmpCase):
    def setUp(self):
        super().setUp()
        self.g, self.store = new_game(self.tmp)
        self.a = self.g.register("alice")["account_id"]
        self.b = self.g.register("bob")["account_id"]
        self.g.start()

    def test_buy_then_conservation_holds(self):
        self.g.act(self.a, "buy", {"pool": "v3", "amount_in": 1000.0})
        self.assertEqual(self.g.alerts, [])
        de, du = self.g.conservation_drift()
        self.assertLess(abs(de), 1e-2)
        self.assertLess(abs(du), 1e-2)

    def test_deposit_withdraw_and_fees(self):
        t = E.price_to_tick(self.g.pools["v3"].price())
        dep = self.g.act(self.b, "deposit",
                         {"pool": "v3", "kind": "range", "tick_lower": t - 1200,
                          "tick_upper": t + 1200, "budget_usd0": 2000.0})
        pid = dep["position_id"]
        # alice trades through bob's liquidity -> bob earns fees
        self.g.act(self.a, "buy", {"pool": "v3", "amount_in": 800.0})
        self.g.act(self.a, "sell", {"pool": "v3", "amount_in": 0.2})
        bob = self.g.accounts[self.b]
        self.assertGreater(float(bob.fees_usd0), 0.0)
        self.assertGreater(float(bob.maker_volume), 0.0)
        wd = self.g.act(self.b, "withdraw", {"pool": "v3", "position_id": pid})
        self.assertEqual(wd["position_id"], pid)
        self.assertEqual(self.g.alerts, [])

    def test_size_cap_enforced(self):
        ry = self.g.pools["v3"].engine.reserves()[1]
        with self.assertRaises(GameError):
            self.g.act(self.a, "buy", {"pool": "v3", "amount_in": ry})  # 100% > 10% cap

    def test_curve_deposit(self):
        t = E.price_to_tick(self.g.pools["curve"].price())
        s = self.g.params.tick_spacing
        prof = {t - 2 * s: 1.0, t - s: 2.0, t: 3.0, t + s: 2.0, t + 2 * s: 1.0}
        dep = self.g.act(self.b, "deposit",
                         {"pool": "curve", "kind": "curve", "profile": prof, "budget_usd0": 1500.0})
        self.assertIn("position_id", dep)
        self.assertEqual(self.g.alerts, [])

    def test_insufficient_funds_appends_nothing(self):
        before = self.store.log_count()
        with self.assertRaises(GameError):
            self.g.act(self.a, "buy", {"pool": "v3", "amount_in": 1e12})
        self.assertEqual(self.store.log_count(), before)  # clean reject -> no log entry


class OracleTests(TmpCase):
    def test_seed_reproducible(self):
        from backend.game import Oracle
        o1 = Oracle(7, 0.01, -0.5 * 0.01 ** 2)
        o2 = Oracle(7, 0.01, -0.5 * 0.01 ** 2)
        d1, d2 = 3000.0, 3000.0
        for _ in range(20):
            d1 = o1.step(d1)
            d2 = o2.step(d2)
        self.assertEqual(d1, d2)


class ReplayTests(TmpCase):
    def test_replay_reproduces_exact_state(self):
        g, store = new_game(self.tmp)
        a = g.register("alice")["account_id"]
        b = g.register("bob")["account_id"]
        g.start()
        t = E.price_to_tick(g.pools["v3"].price())
        g.act(b, "deposit", {"pool": "v3", "kind": "range", "tick_lower": t - 900,
                             "tick_upper": t + 900, "budget_usd0": 3000.0})
        g.act(a, "buy", {"pool": "v3", "amount_in": 1200.0})
        g.tick(); g.tick()
        g.act(a, "sell", {"pool": "v3", "amount_in": 0.1})
        g.tick()
        snapshot = {aid: (str(acc.balance_usd0), str(acc.balance_eth0), str(acc.fees_usd0),
                          str(acc.maker_volume), str(acc.taker_volume))
                    for aid, acc in g.accounts.items()}
        price_v3, price_curve = g.pools["v3"].price(), g.pools["curve"].price()
        d, step, phase = g.d, g.step, g.phase
        store.close()

        # reload purely from the log
        store2 = Store(os.path.join(self.tmp, "g.db"))
        g2 = Game.load(store2, small_params())
        self.assertEqual(g2.step, step)
        self.assertEqual(g2.phase, phase)
        self.assertEqual(g2.d, d)
        self.assertEqual(g2.pools["v3"].price(), price_v3)
        self.assertEqual(g2.pools["curve"].price(), price_curve)
        for aid, acc in g2.accounts.items():
            self.assertEqual(
                snapshot[aid],
                (str(acc.balance_usd0), str(acc.balance_eth0), str(acc.fees_usd0),
                 str(acc.maker_volume), str(acc.taker_volume)), f"account {aid} diverged")
        self.assertEqual(g2.alerts, [])


class LeaderboardTests(TmpCase):
    def test_leaderboard_has_house_benchmarks(self):
        g, _ = new_game(self.tmp)
        g.register("alice")
        g.register("bob")
        g.start()
        rows = g.leaderboard()
        houses = [r for r in rows if r["is_house"]]
        self.assertEqual(len(houses), 2)
        self.assertTrue(all(r["total_value_usd0"] > 0 for r in houses))
        players = [r for r in rows if not r["is_house"]]
        self.assertEqual(len(players), 2)


if __name__ == "__main__":
    unittest.main()
