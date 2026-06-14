"""Tests for app_serve.py pure helpers (the tunnel/teardown path is exercised by serve.py's tests
and verified manually — it shells out to cloudflared)."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import app_serve


class AppServeTests(unittest.TestCase):
    def test_server_cmd_includes_host_port(self):
        cmd = app_serve.server_cmd("0.0.0.0", 9000, reset=False)
        self.assertIn("--host", cmd)
        self.assertIn("0.0.0.0", cmd)
        self.assertIn("9000", cmd)
        self.assertIn("run.py", " ".join(cmd))
        self.assertNotIn("--reset", cmd)

    def test_server_cmd_reset(self):
        self.assertIn("--reset", app_serve.server_cmd("127.0.0.1", 8000, reset=True))

    def test_creds_line(self):
        line = app_serve.creds_line("secret-xyz")
        self.assertIn("admin login:", line)
        self.assertIn("secret-xyz", line)

    def test_is_our_server_matches_only_app(self):
        self.assertTrue(app_serve.is_our_server("/usr/bin/python3 /home/dev/uni/app/run.py --port 8000"))
        self.assertTrue(app_serve.is_our_server("uvicorn backend.api:app"))
        self.assertFalse(app_serve.is_our_server("python3 some_other_server.py --port 8000"))
        self.assertFalse(app_serve.is_our_server("nginx: worker process"))


if __name__ == "__main__":
    unittest.main()
