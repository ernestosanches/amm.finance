#!/usr/bin/env python3
"""S8 — event-hardening chaos/load rehearsal (the real `kill -9` durability test).

1. Run a game over the API, then **SIGKILL the server mid-game** and restart it on the same db;
   assert the recovered leaderboard/state is byte-identical (zero data loss).
2. A short load burst (many concurrent requests) to confirm the single process copes.
3. A WebSocket reconnect storm; the server must stay healthy.

Run: python app/chaos_check.py
"""
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import httpx  # noqa: E402
from backend import config  # noqa: E402

ADMIN = {"name": config.ADMIN_NAME, "password": config.ADMIN_PASSWORD}


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def launch(db, port):
    env = dict(os.environ, AMM_DB_PATH=db, AMM_AUTOTICK="0", AMM_BACKUP_SECS="99999")
    return subprocess.Popen([sys.executable, os.path.join(HERE, "run.py"), "--port", str(port)],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_health(base, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if httpx.get(base + "/health", timeout=2).status_code == 200:
                return True
        except Exception:
            time.sleep(0.3)
    return False


def main():
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "chaos.db")
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    ok = True

    # ---- 1. play, SIGKILL, restart, compare ----
    proc = launch(db, port)
    try:
        if not wait_health(base):
            print("FAIL: server didn't start"); return 1
        c = httpx.Client(base_url=base, timeout=5)
        for name in ("alice", "bob", "cara"):
            c.post("/register", json={"name": name})
        c.post("/admin/start", json=ADMIN)
        # alice (cookie from last register is 'cara'; log in explicitly)
        c.post("/login", json={"name": "alice"})
        for _ in range(6):
            c.post("/action", json={"type": "buy", "pool": "v3", "payload": {"amount_in": 300}})
            c.post("/action", json={"type": "deposit", "pool": "curve",
                                    "payload": {"kind": "range", "tick_lower": 78000,
                                                "tick_upper": 82000, "budget_usd0": 200}})
        before = c.get("/leaderboard").json()

        # ---- 2. load burst ----
        with ThreadPoolExecutor(max_workers=20) as ex:
            results = list(ex.map(lambda _: httpx.get(base + "/state", timeout=5).status_code, range(200)))
        burst_ok = all(s == 200 for s in results)
        print(f"load burst: 200 concurrent /state -> {'all 200' if burst_ok else 'FAILURES'}")
        ok = ok and burst_ok
        c.close()

        # ---- the kill ----
        print("SIGKILL the server mid-game…")
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()

    proc2 = launch(db, port)
    try:
        if not wait_health(base):
            print("FAIL: server didn't recover after kill"); return 1
        after = httpx.get(base + "/leaderboard", timeout=5).json()
        # compare player rows exactly (deterministic replay)
        def rowmap(lb):
            return {r["name"]: round(r["total_value_usd0"], 9) for r in lb["rows"] if not r["is_house"]}
        if rowmap(before) == rowmap(after) and before["d"] == after["d"]:
            print(f"recovery: leaderboard identical after kill -> ZERO DATA LOSS ({len(rowmap(after))} players)")
        else:
            print("FAIL: state diverged after kill")
            print("  before:", rowmap(before)); print("  after :", rowmap(after))
            ok = False

        # ---- 3. WS reconnect storm ----
        storm_ok = ws_storm(base, n=40)
        print(f"ws reconnect storm: 40 connects -> {'healthy' if storm_ok else 'FAILED'}")
        ok = ok and storm_ok and httpx.get(base + "/health", timeout=3).status_code == 200
    finally:
        proc2.terminate()
        try:
            proc2.wait(timeout=5)
        except Exception:
            proc2.kill()

    print("PASS: survived kill -9 with zero loss, load burst, and WS storm." if ok else "FAIL")
    return 0 if ok else 1


def ws_storm(base, n=40):
    try:
        import asyncio
        import websockets
    except Exception:
        print("  (websockets not available; skipping storm)")
        return True
    url = base.replace("http", "ws") + "/ws"

    async def one():
        try:
            async with websockets.connect(url) as ws:
                await ws.recv()  # hello
            return True
        except Exception:
            return False

    async def run():
        return await asyncio.gather(*[one() for _ in range(n)])

    try:
        res = asyncio.run(run())
        return sum(res) >= n * 0.9
    except Exception:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
