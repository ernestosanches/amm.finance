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
import re
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


# Markers identifying *our* app server in a process command line — so cleanup only ever touches
# a stale instance of this app, never some unrelated process that happens to hold the port.
APP_MARKERS = ("app/run.py", "app\\run.py", "backend.api", "backend/api")


def is_our_server(cmdline: str) -> bool:
    return any(m in cmdline for m in APP_MARKERS)


def _read_cmdline(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\x00", b" ").decode("utf-8", "replace").strip()
    except Exception:
        return ""


def pids_on_port(port: int) -> set:
    pids = set()
    try:
        out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                             capture_output=True, text=True, timeout=5).stdout
        pids.update(int(x) for x in out.split())
    except Exception:
        pass
    if not pids:  # fall back to ss
        try:
            out = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                if re.search(rf":{port}\b", line):
                    m = re.search(r"pid=(\d+)", line)
                    if m:
                        pids.add(int(m.group(1)))
        except Exception:
            pass
    return pids


def clean_stale_app_servers(port: int) -> list:
    """Terminate a stale instance of THIS app holding `port`. Refuse (and report) if some other
    process holds it. Returns the pids cleaned."""
    killed = []
    for pid in pids_on_port(port):
        if pid == os.getpid():
            continue
        cmd = _read_cmdline(pid)
        if is_our_server(cmd):
            try:
                os.kill(pid, signal.SIGTERM)
                killed.append(pid)
            except Exception:
                pass
        else:
            raise SystemExit(f"port {port} is held by another process (pid {pid}: {cmd[:80]}). "
                             f"Free it or pick another --port.")
    if killed:
        time.sleep(1.5)
        for pid in killed:
            if os.path.exists(f"/proc/{pid}"):
                try:
                    os.kill(pid, signal.SIGKILL)
                except Exception:
                    pass
        print(f"cleaned stale app server(s) on :{port}: {killed}")
    return killed


def creds_line(password: str) -> str:
    return f"admin login: {config.ADMIN_NAME} / {password}"


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

    # admin password: use AMM_ADMIN_PASSWORD if set, else generate one here and pass it to the
    # server subprocess so we can display it (no hardcoded default ever ships on a tunnel).
    admin_pw = os.environ.get("AMM_ADMIN_PASSWORD") or config.generate_admin_password()
    child_env = dict(os.environ, AMM_ADMIN_PASSWORD=admin_pw)

    # clean any stale instance of THIS app still bound to the port (the common "address already
    # in use" cause), so the new server can bind and the tunnel fronts the RIGHT server.
    clean_stale_app_servers(args.port)

    base = f"http://127.0.0.1:{args.port}"
    print(f"\n== launching app server on {args.host}:{args.port} ==")
    server = subprocess.Popen(server_cmd(args.host, args.port, args.reset and not args.seed),
                              env=child_env, start_new_session=True)

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

    # wait for OUR server to be healthy; abort if it dies (e.g. failed to bind) rather than
    # tunneling to whatever else might be answering on the port.
    healthy, t0 = False, time.time()
    while time.time() - t0 < 25:
        if server.poll() is not None:
            cleanup()
            raise SystemExit("the app server exited on startup (failed to bind? port in use?) — "
                             "see its output above")
        try:
            with urllib.request.urlopen(base + "/health", timeout=2) as r:
                if r.status == 200:
                    healthy = True
                    break
        except Exception:
            time.sleep(0.3)
    if not healthy:
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
            print("   " + creds_line(admin_pw))
            print("\n(give the Cloudflare edge ~10s to warm up — first hit may show error 1033, "
                  "then refresh)\n(Ctrl+C — or kill — to stop the tunnel and server cleanly)")
        else:
            print("Could not detect a tunnel URL yet; cloudflared is still running — check output.")
    else:
        print(f"\nLocal only — open {base} (view remotely via an SSH port-forward, or share "
              f"with --tunnel).\n   " + creds_line(admin_pw) + "\n(Ctrl+C to stop)")

    try:
        while server.poll() is None:
            time.sleep(0.5)
    finally:
        cleanup()


if __name__ == "__main__":
    main()
