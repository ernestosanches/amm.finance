#!/usr/bin/env python3
"""Serve the live AMM game app, with an opt-in public cloudflared tunnel.

Unlike repo-root `serve.py` (which serves the static `out/` figures), this runs the FastAPI app
(uvicorn, via run.py) and — with `--tunnel` — fronts it with a Cloudflare quick tunnel so remote
players can join over a public URL. The cloudflared teardown (process-group kill on every exit
path) is reused from `serve.py`.

DEFAULT is LOCAL-ONLY (127.0.0.1) — reach it via an SSH/Cursor port-forward. `--tunnel` prints a
PUBLIC, no-auth, ephemeral URL anyone with the link can use; it is never on by default.

  python app/app_serve.py                 # local only  -> http://127.0.0.1:8000
  python app/app_serve.py --seed          # seed a deterministic demo game first, then serve
  python app/app_serve.py --tunnel        # ALSO expose via a public cloudflared quick tunnel
  python app/app_serve.py --tunnel --seed # the usual "share a populated game" combo
  python app/app_serve.py --reset --port 9000
"""
import argparse
import atexit
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)   # import the cloudflared helpers from serve.py
sys.path.insert(0, HERE)   # import backend.config

from serve import find_cloudflared, find_tunnel_url, terminate_process_group  # noqa: E402
from backend import config  # noqa: E402


def server_cmd(host: str, port: int, reset: bool) -> list:
    """The run.py invocation for the app server (pure; testable)."""
    cmd = [sys.executable, os.path.join(HERE, "run.py"), "--host", host, "--port", str(port)]
    if reset:
        cmd.append("--reset")
    return cmd


def wait_health(base: str, timeout: float = 25.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(base + "/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def creds_line() -> str:
    return f"admin login: {config.ADMIN_NAME} / {config.ADMIN_PASSWORD}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address (default 127.0.0.1; 0.0.0.0 for LAN)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reset", action="store_true", help="start from a fresh db")
    ap.add_argument("--seed", action="store_true",
                    help="seed a deterministic populated demo game first (implies a fresh db)")
    ap.add_argument("--tunnel", action="store_true",
                    help="ALSO expose via a public cloudflared quick tunnel (opt-in; no account; "
                         "ephemeral random URL; anyone with the link can play)")
    args = ap.parse_args()

    # optional: seed a populated game (demo_seed resets the db unless --keep)
    if args.seed:
        print("== seeding demo game ==")
        subprocess.run([sys.executable, os.path.join(HERE, "demo_seed.py")], cwd=ROOT, check=True)

    cf = None
    if args.tunnel:
        cf = find_cloudflared()
        if not cf:
            raise SystemExit("cloudflared not found. Install it, or run without --tunnel and use "
                             "an SSH/port-forward instead.")

    base = f"http://127.0.0.1:{args.port}"
    print(f"\n== launching app server on {args.host}:{args.port} ==")
    server = subprocess.Popen(server_cmd(args.host, args.port, args.reset and not args.seed),
                              start_new_session=True)

    done = threading.Event()

    def cleanup(*_):
        if done.is_set():
            return
        done.set()
        if cf_proc["p"]:
            terminate_process_group(cf_proc["p"])
        terminate_process_group(server)
        print("\nserver + tunnel stopped.")

    cf_proc = {"p": None}
    atexit.register(cleanup)
    signal.signal(signal.SIGINT, lambda *a: (cleanup(), sys.exit(0)))
    signal.signal(signal.SIGTERM, lambda *a: (cleanup(), sys.exit(0)))

    if not wait_health(base):
        cleanup()
        raise SystemExit("server did not become healthy — check its output above")
    print("server healthy.")

    if args.tunnel:
        print("starting cloudflared quick tunnel (PUBLIC, no-auth, ephemeral)...\n")
        proc = subprocess.Popen([cf, "tunnel", "--url", f"http://localhost:{args.port}"],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                bufsize=1, start_new_session=True)
        cf_proc["p"] = proc
        url = {"v": None}

        def drain():
            for line in proc.stdout:
                if url["v"] is None:
                    u = find_tunnel_url(line)
                    if u:
                        url["v"] = u

        threading.Thread(target=drain, daemon=True).start()
        for _ in range(60):
            if url["v"] or proc.poll() is not None:
                break
            time.sleep(1)

        if url["v"]:
            print(f"PUBLIC game URL (anyone with the link can play; ephemeral):\n\n    {url['v']}\n")
            print("   " + creds_line())
            print("\n(give the Cloudflare edge ~10s to warm up — first hit may show error 1033, "
                  "then refresh)\n(Ctrl+C — or kill — to stop the tunnel and server cleanly)")
        else:
            print("Could not detect a tunnel URL yet; cloudflared is still running — check output.")
    else:
        print(f"\nLocal only — open {base} (view remotely via an SSH port-forward, or share "
              f"with --tunnel).\n   " + creds_line() + "\n(Ctrl+C to stop)")

    try:
        while server.poll() is None:
            time.sleep(0.5)
    finally:
        cleanup()


if __name__ == "__main__":
    main()
