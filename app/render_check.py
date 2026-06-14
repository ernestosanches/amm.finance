#!/usr/bin/env python3
"""Headless-browser smoke check for the frontend (closes the "did the JS actually run" loop).

Launches a real server, drives Chromium through register -> start -> trade, screenshots each
state, and FAILS on any browser console error or unhandled page exception (which is how a broken
ES-module import or runtime bug in the vanilla JS would surface). Run: python app/render_check.py
"""
import os
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_health(base, timeout=20):
    import httpx
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if httpx.get(base + "/health", timeout=2).status_code == 200:
                return True
        except Exception:
            time.sleep(0.3)
    return False


def main():
    import httpx
    from backend import config
    from playwright.sync_api import sync_playwright

    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "render.db")
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    out = os.path.join(HERE, "out")
    os.makedirs(out, exist_ok=True)

    admin_pw = "render-check-pw"
    env = dict(os.environ, AMM_DB_PATH=db, AMM_AUTOTICK="0",  # no auto-tick: stable for the check
               AMM_ADMIN_PASSWORD=admin_pw)
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "run.py"),
                             "--port", str(port), "--reset"], env=env)
    errors, shots = [], []
    try:
        if not wait_health(base):
            print("FAIL: server did not become healthy")
            return 1

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page()
            page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

            page.goto(base, wait_until="domcontentloaded")
            # register
            page.wait_for_selector("#reg-name", timeout=8000)
            page.fill("#reg-name", "alice")
            page.get_by_role("button", name="Register").click()
            page.get_by_text("Total value").wait_for(timeout=8000)
            shots.append(os.path.join(out, "ui_1_lobby.png"))
            page.screenshot(path=shots[-1], full_page=True)
            assert "Portfolio" in page.content()

            # start the game via admin API, then reload
            r = httpx.post(base + "/admin/start",
                           json={"name": config.ADMIN_NAME, "password": admin_pw}, timeout=5)
            assert r.status_code == 200, r.text
            page.reload(wait_until="domcontentloaded")
            page.get_by_text("Total value").wait_for(timeout=8000)

            # buy in the v3 pool (first buy box)
            page.get_by_placeholder("USD0 in").first.fill("500")
            page.get_by_role("button", name="Buy ETH0").first.click()
            page.wait_for_timeout(1500)
            shots.append(os.path.join(out, "ui_2_running.png"))
            page.screenshot(path=shots[-1], full_page=True)

            # confirm the trade actually moved state (server-authoritative): pool price changed
            det = httpx.get(base + "/pool/v3/detail", timeout=5).json()
            assert det["price"] != config.GameParams().d0, "buy did not move the pool price"

            # deposit liquidity in v3 (range, defaults pre-filled), so the L3 book has a player order
            page.get_by_text("Deposit liquidity").first.click()
            page.get_by_role("button", name="Deposit", exact=True).first.click()
            page.wait_for_timeout(1000)

            # F6 read pages: leaderboard, pool detail (level-3 book), profile
            for route, marker, shot in [
                ("#/leaderboard", "Leaderboard", "ui_3_leaderboard.png"),
                ("#/pool/v3", "Level-3 order book", "ui_4_pool_detail.png"),
                ("#/profile/alice", "Profile", "ui_5_profile.png"),
            ]:
                page.goto(base + route, wait_until="domcontentloaded")
                page.get_by_text(marker, exact=False).first.wait_for(timeout=8000)
                p = os.path.join(out, shot)
                page.screenshot(path=p, full_page=True)
                shots.append(p)

            # F7 admin: log in and view the live monitor
            page.goto(base + "#/admin", wait_until="domcontentloaded")
            page.get_by_placeholder("admin password").fill(admin_pw)
            page.get_by_role("button", name="Enter").click()
            page.get_by_text("Monitor", exact=True).wait_for(timeout=8000)
            page.get_by_text("Conservation").wait_for(timeout=4000)
            shots.append(os.path.join(out, "ui_6_admin.png"))
            page.screenshot(path=shots[-1], full_page=True)

            browser.close()

        hard = [e for e in errors if e.startswith("pageerror") or "console.error" in e]
        if hard:
            print("FAIL: browser errors:")
            for e in hard:
                print("  ", e)
            return 1
        print("PASS: UI rendered, flow worked, no JS errors.")
        print("screenshots:", ", ".join(shots))
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
