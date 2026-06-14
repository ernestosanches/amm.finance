"""S8 — durability/robustness unit tests (the kill -9 test runs for real in chaos_check.py).

Covers the two §7 guarantees that can be checked in-process: a conservation breach raises an
alert but NEVER halts the game, and a crash mid-sequence loses nothing (reload == live state).
"""
import os
import sys
import tempfile
import unittest
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import config, engine as E
from backend.game import Game
from backend.persistence import Store


def params():
    return config.GameParams(d0=3000.0, x=10_000.0, k=3.0, walk_step=1.0, game_length=60.0)


class GracefulDegradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "d.db"))
        self.g = Game(self.store, params())
        self.a = self.g.register("alice")["account_id"]
        self.g.register("bob")
        self.g.start()

    def tearDown(self):
        import shutil
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_conservation_breach_alerts_but_does_not_halt(self):
        # inject phantom value to break the invariant (simulating a bug)
        self.g.accounts[self.a].balance_usd0 += D("1000")
        # a subsequent action must still succeed; the detector records an alert, never raises
        self.g.act(self.a, "buy", {"pool": "v3", "amount_in": 100.0})
        self.assertTrue(self.g.alerts, "expected a conservation alert")
        # and the game keeps serving more actions
        self.g.act(self.a, "buy", {"pool": "v3", "amount_in": 50.0})
        self.assertEqual(self.g.phase, "RUNNING")

    def test_alert_is_persisted(self):
        self.g.accounts[self.a].balance_eth0 += D("5")
        self.g.act(self.a, "sell", {"pool": "v3", "amount_in": 0.1})
        kinds = [e.kind for e in self.store.read_log()]
        self.assertIn("alert", kinds)


class CrashRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_abrupt_drop_loses_nothing(self):
        path = os.path.join(self.tmp, "c.db")
        store = Store(path)
        g = Game(store, params())
        a = g.register("alice")["account_id"]
        g.start()
        for _ in range(5):
            g.act(a, "buy", {"pool": "v3", "amount_in": 200.0})
            g.tick()
        live = (str(g.accounts[a].balance_usd0), str(g.accounts[a].balance_eth0),
                g.pools["v3"].price(), g.step, g.phase)
        # simulate an abrupt crash: just drop the handle without any clean shutdown
        del g
        store.close()

        store2 = Store(path)
        g2 = Game.load(store2, params())
        recovered = (str(g2.accounts[a].balance_usd0), str(g2.accounts[a].balance_eth0),
                     g2.pools["v3"].price(), g2.step, g2.phase)
        self.assertEqual(live, recovered)
        self.assertEqual(g2.alerts, [])
        store2.close()


if __name__ == "__main__":
    unittest.main()
