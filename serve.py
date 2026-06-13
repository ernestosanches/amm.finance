#!/usr/bin/env python3
"""Serve the generated HTML in out/ for viewing.

DEFAULT is LOCAL-ONLY: binds 127.0.0.1, not reachable from the network. To view from another
machine, use an SSH / Cursor port-forward (authenticated, no public exposure).

Opt-in `--tunnel` ALSO starts a cloudflared quick tunnel and prints a PUBLIC URL — no account,
no auth, ephemeral/random, anyone with the link can view. Use it deliberately for a quick share;
it is never on by default and is torn down on exit.

  python serve.py                 # local: serve out/ on 127.0.0.1:8000, list URLs
  python serve.py orderbook.html  # highlight one page in the list
  python serve.py --port 9000
  python serve.py --tunnel        # ALSO expose via a public cloudflared quick tunnel (opt-in)
"""
import argparse
import atexit
import http.server
import os
import re
import shutil
import signal
import socketserver
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TRYCLOUDFLARE_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
CLOUDFLARED_FALLBACK = "/opt/instance-tools/bin/cloudflared"  # Vast.ai ships it here


def resolve_root(dir_arg):
    return dir_arg if os.path.isabs(dir_arg) else os.path.join(HERE, dir_arg)


def list_html_urls(root, base):
    """[(filename, full_url)] for every .html in root (pure; testable)."""
    base = base.rstrip("/")
    return [(f, f"{base}/{f}")
            for f in sorted(os.listdir(root)) if f.lower().endswith(".html")]


def format_link_lines(urls, page=None):
    """Pretty list of '  <url>' lines, marking `page` if given (pure; testable)."""
    return [f"  {url}" + ("  <-- this one" if page and f == page else "") for f, url in urls]


# The figures worth a one-click — surfaced first so they're easy to ctrl+click. The Stage 6.2
# virtual order book (the real bid/ask book) comes first; the Stage 4.3 range view is kept below it
# for comparison. Each level (L2 depth, L3 order book) has a with- and without-initial variant.
KEY_FILES = [
    "orderbook_virtual.html",                       # L3 virtual book, baseline + per-position orders
    "orderbook_virtual__without_initial.html",      # L3 virtual book, day's orders only
    "depth_virtual.html",                           # L2 virtual depth (bid/ask), with initial
    "depth_virtual__without_initial.html",          # L2 virtual depth (bid/ask), without initial
    "orderbook.html",                               # L3 range view with initial (baseline + orders)
    "orderbook__without_initial.html",              # L3 range view without initial (orders only)
    "depth_over_time.html",                         # L2 range view with initial (absolute)
    "depth_over_time__without_initial.html",        # L2 range view without initial (relative)
]


def key_link_lines(urls):
    """The KEY_FILES present in `urls`, in KEY_FILES order, as '  N) <url>' lines."""
    by_name = dict(urls)
    lines = []
    for f in KEY_FILES:
        if f in by_name:
            lines.append(f"  {len(lines) + 1}) {by_name[f]}")
    return lines


def print_link_block(urls, page=None):
    """Print the key links once; only non-key pages get an extra 'Other' list (no duplication)."""
    key = key_link_lines(urls)
    others = [(f, u) for f, u in urls if f not in set(KEY_FILES)]
    if key:
        print("Links (ctrl+click):")
        print("\n".join(key))
    if others:
        print("\nOther pages:")
        print("\n".join(format_link_lines(others, page)))
    if not key and not others:
        print("(no .html files yet — run run_all.py / plot_orderbook.py first)")


def make_server(root, port=8000):
    """Build (but don't start) a localhost-only directory server. port=0 picks a free port."""
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=root, **k)
    socketserver.TCPServer.allow_reuse_address = True
    return socketserver.TCPServer(("127.0.0.1", port), handler)


def find_tunnel_url(text):
    """Extract a https://<sub>.trycloudflare.com URL from a cloudflared log line (or None)."""
    m = TRYCLOUDFLARE_RE.search(text)
    return m.group(0) if m else None


def find_cloudflared():
    return shutil.which("cloudflared") or (CLOUDFLARED_FALLBACK
                                           if os.path.exists(CLOUDFLARED_FALLBACK) else None)


def terminate_process_group(proc, timeout=5):
    """Stop a subprocess and any children: SIGTERM the whole group, then SIGKILL if needed."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass


def serve_with_tunnel(root, port, page=None):
    """Serve `root` on localhost AND expose it via an opt-in cloudflared quick tunnel.

    Prints a PUBLIC no-auth URL. Torn down on every exit path (Ctrl+C / kill / SIGTERM / normal
    exit / cloudflared dying): cloudflared runs in its own session and is killed by process group.
    """
    cf = find_cloudflared()
    if not cf:
        raise SystemExit("cloudflared not found. Install it, or run without --tunnel and use an "
                         "SSH/port-forward instead.")
    httpd = make_server(root, port)
    actual = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"Serving {root} on 127.0.0.1:{actual}; starting cloudflared quick tunnel "
          f"(PUBLIC, no-auth, ephemeral)...\n")

    proc = subprocess.Popen([cf, "tunnel", "--url", f"http://localhost:{actual}"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                            start_new_session=True)

    done = threading.Event()

    def cleanup(*_):
        if done.is_set():
            return
        done.set()
        terminate_process_group(proc)
        httpd.shutdown()
        print("\ntunnel + server stopped.")

    atexit.register(cleanup)
    signal.signal(signal.SIGINT, lambda *a: (cleanup(), sys.exit(0)))
    signal.signal(signal.SIGTERM, lambda *a: (cleanup(), sys.exit(0)))

    base = {"url": None}

    def drain():  # keep the pipe drained; capture the URL when it appears
        for line in proc.stdout:
            if base["url"] is None:
                u = find_tunnel_url(line)
                if u:
                    base["url"] = u

    threading.Thread(target=drain, daemon=True).start()
    for _ in range(60):
        if base["url"] or proc.poll() is not None:
            break
        time.sleep(1)

    if base["url"]:
        print("PUBLIC tunnel URL (anyone with the link can view; ephemeral).\n")
        print_link_block(list_html_urls(root, base["url"]), page)
        print("\n(give the Cloudflare edge ~10s to warm up — first hit may show error 1033, "
              "then refresh)\n(Ctrl+C — or kill — to stop the tunnel and server cleanly)")
    else:
        print("Could not detect a tunnel URL yet; cloudflared is still running — check its output.")

    try:
        while proc.poll() is None:
            time.sleep(0.5)
    finally:
        cleanup()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("page", nargs="?", help="optional HTML file to highlight")
    ap.add_argument("--dir", default="out", help="directory to serve (default: out)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--tunnel", action="store_true",
                    help="ALSO expose via a public cloudflared quick tunnel (opt-in; no account; "
                         "ephemeral random URL; anyone with the link can view)")
    ap.add_argument("--log", action="store_true",
                    help="regenerate the figures with log-scaled absolute graphs before serving "
                         "(off by default; rebuilds from existing CSVs, no network)")
    args = ap.parse_args()

    root = resolve_root(args.dir)
    if not os.path.isdir(root):
        raise SystemExit(f"No such directory: {root} (run plot_orderbook.py / plot.py first?)")

    if args.log:  # rebuild the 4 HTML with the absolute depth in log scale, then serve
        print("Regenerating figures with --log (absolute depth in log scale)...")
        subprocess.run([sys.executable, "run_all.py", "--figures-only", "--log"],
                       cwd=HERE, check=True)

    if args.tunnel:
        serve_with_tunnel(root, args.port, args.page)
        return

    urls = list_html_urls(root, f"http://localhost:{args.port}")
    print(f"Serving {root} on 127.0.0.1:{args.port}  "
          f"(local only — view remotely via an SSH port-forward, or share with --tunnel)\n")
    print_link_block(urls, args.page)
    print("\n(Ctrl+C to stop)")

    with make_server(root, args.port) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped.")


if __name__ == "__main__":
    main()
