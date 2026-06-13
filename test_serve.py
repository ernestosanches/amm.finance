#!/usr/bin/env python3
"""Stage 4.4 tests — serve.py (local-only viewer).

A pure test of the URL listing/formatting, plus a real bind-and-GET smoke test on an
ephemeral port (proves the server serves a file with HTTP 200 and 404s a missing one).
"""
import os
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

import serve


class ListUrlsTests(unittest.TestCase):
    def test_lists_only_html_with_full_urls(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "orderbook.html"), "w").close()
            open(os.path.join(d, "depth.html"), "w").close()
            open(os.path.join(d, "notes.txt"), "w").close()
            urls = serve.list_html_urls(d, "http://localhost:8000/")  # trailing slash trimmed
            self.assertEqual(urls, [
                ("depth.html", "http://localhost:8000/depth.html"),
                ("orderbook.html", "http://localhost:8000/orderbook.html"),
            ])

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(serve.list_html_urls(d, "http://x"), [])

    def test_format_link_lines_marks_page(self):
        urls = [("a.html", "http://x/a.html"), ("b.html", "http://x/b.html")]
        lines = serve.format_link_lines(urls, page="b.html")
        self.assertEqual(lines[0], "  http://x/a.html")
        self.assertTrue(lines[1].endswith("b.html  <-- this one"))

    def test_key_link_lines_surfaces_key_files_in_order(self):
        urls = [("zzz.html", "http://x/zzz.html"),
                ("depth_over_time.html", "http://x/depth_over_time.html"),
                ("orderbook.html", "http://x/orderbook.html")]
        lines = serve.key_link_lines(urls)
        # KEY_FILES order: orderbook first, then the depth views; numbered; absent ones skipped
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("  1)") and lines[0].endswith("orderbook.html"))
        self.assertTrue(lines[1].startswith("  2)") and lines[1].endswith("depth_over_time.html"))


class TunnelHelperTests(unittest.TestCase):
    def test_find_tunnel_url(self):
        line = "2026-... INF |  https://nuke-unknown-bomb-lap.trycloudflare.com  |"
        self.assertEqual(serve.find_tunnel_url(line),
                         "https://nuke-unknown-bomb-lap.trycloudflare.com")
        self.assertIsNone(serve.find_tunnel_url("INF Registered tunnel connection connIndex=0"))

    def test_terminate_process_group_kills_child(self):
        # the opt-in tunnel must be killable on exit; verify the group-kill ends a real child
        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        self.assertIsNone(proc.poll())          # running
        serve.terminate_process_group(proc, timeout=5)
        self.assertIsNotNone(proc.poll())       # stopped
        serve.terminate_process_group(proc)     # idempotent / safe on a dead proc


class ServeSmokeTest(unittest.TestCase):
    def test_serves_file_200_and_404(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "page.html"), "w") as f:
                f.write("<html>hello-orderbook</html>")
            httpd = serve.make_server(d, 0)  # port 0 = free ephemeral port, bound to 127.0.0.1
            self.assertEqual(httpd.server_address[0], "127.0.0.1")  # local-only default
            port = httpd.server_address[1]
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/page.html", timeout=5) as r:
                    self.assertEqual(r.status, 200)
                    self.assertIn("hello-orderbook", r.read().decode())
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/missing.html", timeout=5)
                self.assertEqual(cm.exception.code, 404)
            finally:
                httpd.shutdown()
                httpd.server_close()
                t.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
