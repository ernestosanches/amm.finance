"""B2 — persistence spine: durable append, replay-rebuilds-exact-state, restart survives,
projections, off-box backup."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.persistence import Store, replay


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "t.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_append_and_read_order(self):
        s = Store(self.path)
        s.append("register", 1, {"name": "a"}, ts=1.0)
        s.append("buy", 1, {"amount_in": 10}, {"out": 0.003}, ts=2.0)
        s.append("oracle", None, {"step": 1, "d": 3010}, ts=3.0)
        log = list(s.read_log())
        self.assertEqual([e.kind for e in log], ["register", "buy", "oracle"])
        self.assertEqual(log[0].payload["name"], "a")
        self.assertEqual(log[1].result["out"], 0.003)
        s.close()

    def test_pragmas_durable(self):
        s = Store(self.path)
        jm = s.conn.execute("PRAGMA journal_mode;").fetchone()[0]
        sync = s.conn.execute("PRAGMA synchronous;").fetchone()[0]
        self.assertEqual(jm.lower(), "wal")
        self.assertEqual(int(sync), 2)  # FULL
        s.close()

    def test_restart_preserves_log(self):
        # simulate a process crash: write, drop the handle, reopen the same file.
        s = Store(self.path)
        for i in range(50):
            s.append("buy", 1, {"i": i}, ts=float(i))
        s.close()
        s2 = Store(self.path)
        log = list(s2.read_log())
        self.assertEqual(len(log), 50)
        self.assertEqual([e.payload["i"] for e in log], list(range(50)))
        s2.close()

    def test_replay_rebuilds_exact_state(self):
        s = Store(self.path)
        s.append("credit", 1, {"amt": 100}, ts=1)
        s.append("credit", 1, {"amt": 25}, ts=2)
        s.append("debit", 1, {"amt": 40}, ts=3)
        s.close()
        # rebuild a balance purely from the log
        s2 = Store(self.path)
        bal = {"v": 0}

        def apply(e):
            if e.kind == "credit":
                bal["v"] += e.payload["amt"]
            elif e.kind == "debit":
                bal["v"] -= e.payload["amt"]

        n = replay(s2, apply)
        self.assertEqual(n, 3)
        self.assertEqual(bal["v"], 85)
        s2.close()

    def test_meta_roundtrip(self):
        s = Store(self.path)
        s.set_meta("params", {"d0": 3000, "k": 3})
        s.set_meta("phase", "RUNNING")
        s.set_meta("phase", "FREEZE")  # overwrite
        self.assertEqual(s.get_meta("params")["k"], 3)
        self.assertEqual(s.get_meta("phase"), "FREEZE")
        self.assertIsNone(s.get_meta("missing"))
        s.close()

    def test_projections(self):
        s = Store(self.path)
        s.upsert_account(id=1, name="alice", is_house=0, is_admin=0,
                         balance_usd0="5000.5", balance_eth0="1.25", fees_usd0="0",
                         taker_volume="0", maker_volume="0")
        s.upsert_account(id=1, name="alice", balance_usd0="4000", balance_eth0="1.5",
                         is_house=0, is_admin=0, fees_usd0="3.2",
                         taker_volume="100", maker_volume="0")
        row = s.conn.execute("SELECT * FROM accounts WHERE id=1").fetchone()
        self.assertEqual(row["balance_usd0"], "4000")
        s.record_oracle_tick(1, 1.0, 3000)
        s.record_oracle_tick(2, 2.0, 3015)
        series = s.d_series()
        self.assertEqual(len(series), 2)
        self.assertEqual(series[1]["d"], 3015)
        s.close()

    def test_backup_is_consistent_copy(self):
        s = Store(self.path)
        for i in range(10):
            s.append("x", None, {"i": i}, ts=float(i))
        dest = os.path.join(self.tmp, "backup", "copy.db")
        s.backup_to(dest)
        s.close()
        b = Store(dest)
        self.assertEqual(b.log_count(), 10)
        b.close()


if __name__ == "__main__":
    unittest.main()
