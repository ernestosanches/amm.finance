"""S0 — contract + skeleton tests: health/index served, WS frames of every type round-trip,
and the REST read-models validate sample payloads."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from backend import contracts as C
from backend.api import create_app
from backend.config import GameParams


class HealthTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.client = TestClient(create_app(db_path=os.path.join(self.tmp, "h.db"), autotick=False))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_health(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])

    def test_index_served(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("<div id=\"app\">", r.text)

    def test_static_js(self):
        r = self.client.get("/static/app.js")
        self.assertEqual(r.status_code, 200)


class WSContractTests(unittest.TestCase):
    def test_every_ws_type_roundtrips(self):
        for t in ("d_tick", "pool", "clock", "leaderboard", "phase", "hello"):
            frame = C.ws(t, value=1)
            self.assertEqual(frame["type"], t)
            again = C.WSMessage(**frame)
            self.assertEqual(again.type, t)
            self.assertEqual(again.data["value"], 1)

    def test_ws_rejects_unknown_type(self):
        with self.assertRaises(Exception):
            C.WSMessage(type="bogus", data={})


class RestModelTests(unittest.TestCase):
    def test_action_request(self):
        a = C.ActionRequest(type="buy", pool="v3", payload={"amount_in": 100.0})
        self.assertEqual(a.type, "buy")
        self.assertEqual(a.payload["amount_in"], 100.0)

    def test_state_response(self):
        s = C.StateResponse(
            account=None, d=3000.0,
            pools=[C.PoolView(pool="v3", price=3000.0, tvl_usd0=0.0)],
            clock=C.ClockView(phase="LOBBY", elapsed=0, remaining=3600, step=0),
        )
        self.assertEqual(s.clock.phase, "LOBBY")

    def test_invalid_pool_rejected(self):
        with self.assertRaises(Exception):
            C.PoolView(pool="amm", price=1, tvl_usd0=0)


class ParamsTests(unittest.TestCase):
    def test_defaults_and_mu(self):
        p = GameParams()
        self.assertAlmostEqual(p.mu, -0.5 * p.sigma ** 2)
        self.assertEqual(p.tick_spacing, 60)
        self.assertGreater(p.size_cap_frac, 0)  # cap ON by default

    def test_roundtrip(self):
        p = GameParams(d0=2500, sigma=0.002)
        q = GameParams.from_dict(p.to_dict())
        self.assertEqual(q.d0, 2500)
        self.assertAlmostEqual(q.sigma, 0.002)


if __name__ == "__main__":
    unittest.main()
