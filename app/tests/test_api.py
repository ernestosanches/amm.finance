"""B4 — REST + WebSocket surface: auth, server-authoritative actions, admin gate, WS push."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from backend import config
from backend.api import create_app

ADMIN_PW = "test-admin-pw"
ADMIN = {"name": config.ADMIN_NAME, "password": ADMIN_PW}


class ApiCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        params = config.GameParams(d0=3000.0, x=10_000.0, k=3.0, walk_step=1.0, game_length=20.0)
        self.app = create_app(db_path=os.path.join(self.tmp, "api.db"), params=params,
                              autotick=False, admin_password=ADMIN_PW)
        self.client = TestClient(self.app)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def reg(self, name):
        return self.client.post("/register", json={"name": name})


class AuthTests(ApiCase):
    def test_register_returns_bag_and_cookie(self):
        r = self.reg("alice")
        self.assertEqual(r.status_code, 200)
        self.assertAlmostEqual(r.json()["balance_usd0"], 5000.0)
        self.assertIn("aid", r.cookies)

    def test_duplicate_register_rejected(self):
        self.reg("alice")
        r = self.reg("alice")
        self.assertEqual(r.status_code, 400)

    def test_state_account_from_cookie(self):
        r = self.client.get("/state")
        self.assertIsNone(r.json()["account"])
        self.reg("alice")
        r = self.client.get("/state")
        self.assertEqual(r.json()["account"]["name"], "alice")
        self.assertEqual(len(r.json()["pools"]), 2)


class ActionTests(ApiCase):
    def test_trade_blocked_before_start(self):
        self.reg("alice")
        r = self.client.post("/action", json={"type": "buy", "pool": "v3", "payload": {"amount_in": 100}})
        self.assertEqual(r.status_code, 400)

    def test_admin_gate(self):
        bad = self.client.post("/admin/start", json={"name": "admin", "password": "nope"})
        self.assertEqual(bad.status_code, 403)

    def test_full_flow_start_and_trade(self):
        self.reg("alice")
        self.reg("bob")
        s = self.client.post("/admin/start", json=ADMIN)
        self.assertEqual(s.status_code, 200)
        self.assertEqual(s.json()["phase"], "RUNNING")
        # alice logs in (cookie), buys
        self.client.post("/login", json={"name": "alice"})
        r = self.client.post("/action", json={"type": "buy", "pool": "v3", "payload": {"amount_in": 500}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["ok"])
        # leaderboard has 2 players + 2 house benchmark rows
        lb = self.client.get("/leaderboard").json()
        self.assertEqual(len([x for x in lb["rows"] if x["is_house"]]), 2)
        # monitor reports conservation ok
        mon = self.client.post("/admin/monitor", json=ADMIN).json()
        self.assertTrue(mon["conservation"]["ok"])

    def test_deposit_then_pool_detail_has_book(self):
        self.reg("alice")
        self.client.post("/admin/start", json=ADMIN)
        self.client.post("/login", json={"name": "alice"})
        from backend.engine import price_to_tick
        st = self.client.get("/state").json()
        t = price_to_tick(st["pools"][0]["price"])
        r = self.client.post("/action", json={
            "type": "deposit", "pool": "v3",
            "payload": {"kind": "range", "tick_lower": t - 600, "tick_upper": t + 600,
                        "budget_usd0": 1000.0}})
        self.assertEqual(r.status_code, 200, r.text)
        det = self.client.get("/pool/v3/detail").json()
        self.assertTrue(det["book"])
        self.assertGreater(det["tvl_usd0"], 0)


class ProfileTests(ApiCase):
    def test_profile_and_name_change(self):
        self.reg("alice")
        self.client.post("/profile/name", json={"new_name": "alice2"})
        p = self.client.get("/profile/alice2").json()
        self.assertEqual(p["name"], "alice2")
        self.assertIn("alice2", p["name_history"])


class WebSocketTests(ApiCase):
    def test_ws_hello_and_leaderboard(self):
        self.reg("alice")
        with TestClient(self.app) as client:
            client.cookies.set("aid", "1")
            with client.websocket_connect("/ws") as ws:
                hello = ws.receive_json()
                self.assertEqual(hello["type"], "hello")
                self.assertIn("state", hello["data"])
                lb = ws.receive_json()
                self.assertEqual(lb["type"], "leaderboard")


if __name__ == "__main__":
    unittest.main()
