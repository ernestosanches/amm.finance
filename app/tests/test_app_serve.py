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


if __name__ == "__main__":
    unittest.main()
