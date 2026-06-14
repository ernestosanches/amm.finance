"""B4 — REST + WebSocket surface (APP_PLAN.md §8). Server-authoritative: the client never
mutates state directly; every change runs through the Game (validate → log → apply).

A single `GameServer` holds the Store + Game behind an asyncio lock (the Game is sync and not
thread-safe). A background ticker advances the seeded oracle every `walk_step` seconds and
broadcasts to WebSocket clients. Admin endpoints are gated by name + password (§9).
"""
from __future__ import annotations

import asyncio
import hmac
import os
import time

from fastapi import Cookie, FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, contracts as C
from .game import Game, GameError
from .persistence import Store

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")


class GameServer:
    def __init__(self, db_path: str, params: config.GameParams | None = None, autotick: bool = True):
        self.store = Store(db_path)
        saved = self.store.get_meta("params")
        self.params = params or (config.GameParams.from_dict(saved) if saved else config.GameParams())
        if self.store.log_count() > 0:
            self.game = Game.load(self.store, self.params)
        else:
            self.game = Game(self.store, self.params)
            self.store.set_meta("params", self.params.to_dict())
        self.lock = asyncio.Lock()
        self.clients: set[WebSocket] = set()
        self.autotick = autotick
        self.price_history: dict[str, list[dict]] = {"v3": [], "curve": []}
        self._ticker_task: asyncio.Task | None = None

    # --- helpers ---
    def snapshot_prices(self) -> None:
        for label, pool in self.game.pools.items():
            self.price_history[label].append({"step": self.game.step, "price": pool.price()})

    def state_dict(self, aid: int | None) -> dict:
        g = self.game
        acc = g.accounts.get(aid) if aid is not None else None
        pools = []
        for label, pool in g.pools.items():
            your_pos, your_fees = [], 0.0
            if acc is not None:
                for p in g.positions_of(acc.id, label):
                    your_pos.append({
                        "position_id": p.id, "pool": label, "kind": p.kind,
                        "tick_lower": p.tick_lower, "tick_upper": p.tick_upper,
                        "value_usd0": pool.position_value_at_d(p.id, g.d),
                        "fees_usd0": float(p.fees_usd0) + float(p.fees_eth0) * g.d})
                    your_fees += float(p.fees_usd0) + float(p.fees_eth0) * g.d
            pools.append({"pool": label, "price": pool.price(), "tvl_usd0": pool.tvl_usd0(),
                          "your_fees_usd0": your_fees, "your_positions": your_pos})
        account = None
        if acc is not None:
            account = {"account_id": acc.id, "name": acc.name, "is_admin": acc.is_admin,
                       "balance_usd0": float(acc.balance_usd0), "balance_eth0": float(acc.balance_eth0)}
        return {"account": account, "d": g.d, "pools": pools, "clock": g.clock()}

    async def broadcast(self) -> None:
        if not self.clients:
            return
        g = self.game
        frames = [
            C.ws("d_tick", d=g.d, step=g.step),
            C.ws("clock", **g.clock()),
            C.ws("pool", pools=[{"pool": l, "price": p.price(), "tvl_usd0": p.tvl_usd0()}
                                for l, p in g.pools.items()]),
            C.ws("leaderboard", rows=g.leaderboard(), d=g.d),
        ]
        dead = []
        for ws in list(self.clients):
            try:
                for f in frames:
                    await ws.send_json(f)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    async def run_ticker(self) -> None:
        while True:
            await asyncio.sleep(max(0.05, self.params.walk_step))
            try:
                async with self.lock:
                    if self.game.phase in ("RUNNING", "FREEZE"):
                        self.game.tick(ts=time.time())
                        self.snapshot_prices()
                        if self.game.phase == "SETTLED":
                            self.store.set_meta("settled_at", time.time())
                await self.broadcast()
            except Exception:
                pass  # never let the ticker kill the event


def _aid_from_cookie(aid: str | None) -> int | None:
    try:
        return int(aid) if aid is not None else None
    except ValueError:
        return None


def _admin_ok(name: str, password: str) -> bool:
    return hmac.compare_digest(name or "", config.ADMIN_NAME) and \
        hmac.compare_digest(password or "", config.ADMIN_PASSWORD)


def create_app(db_path: str | None = None, params: config.GameParams | None = None,
               autotick: bool | None = None) -> FastAPI:
    db_path = db_path or config.DB_PATH
    if autotick is None:
        autotick = os.environ.get("AMM_AUTOTICK", "1") != "0"

    app = FastAPI(title="Multiplayer AMM Game", version="1.0.0")
    server = GameServer(db_path, params=params, autotick=autotick)
    app.state.server = server

    @app.on_event("startup")
    async def _startup():
        if server.autotick:
            server._ticker_task = asyncio.create_task(server.run_ticker())

    @app.on_event("shutdown")
    async def _shutdown():
        if server._ticker_task:
            server._ticker_task.cancel()
        server.store.close()

    # ---- health / static ----
    @app.get("/health")
    def health():
        return {"ok": True, "service": "amm-game", "phase": server.game.phase}

    @app.get("/")
    def index():
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

    @app.get("/config")
    def cfg():
        p = server.params
        return {"tick_spacing": p.tick_spacing, "base_symbol": p.base_symbol,
                "quote_symbol": p.quote_symbol, "d0": p.d0, "fee": p.fee,
                "size_cap_frac": p.size_cap_frac, "walk_step": p.walk_step,
                "game_length": p.game_length, "range_factor": p.range_factor,
                "phase": server.game.phase}

    # ---- auth ----
    @app.post("/register")
    async def register(req: C.RegisterRequest, response: Response):
        async with server.lock:
            try:
                r = server.game.register(req.name, ts=time.time())
            except GameError as e:
                return JSONResponse({"detail": str(e)}, status_code=400)
        response.set_cookie("aid", str(r["account_id"]), httponly=True, samesite="lax")
        response.set_cookie("registered", "1", samesite="lax")
        return {"account_id": r["account_id"], "name": req.name, "is_admin": False,
                "balance_usd0": r["balance_usd0"], "balance_eth0": r["balance_eth0"]}

    @app.post("/login")
    async def login(req: C.LoginRequest, response: Response):
        acc = next((a for a in server.game.accounts.values() if a.name == req.name), None)
        if acc is None:
            return JSONResponse({"detail": "no such account"}, status_code=404)
        response.set_cookie("aid", str(acc.id), httponly=True, samesite="lax")
        return {"account_id": acc.id, "name": acc.name, "is_admin": acc.is_admin,
                "balance_usd0": float(acc.balance_usd0), "balance_eth0": float(acc.balance_eth0)}

    # ---- state / actions ----
    @app.get("/state")
    def state(aid: str | None = Cookie(default=None)):
        return server.state_dict(_aid_from_cookie(aid))

    @app.post("/action")
    async def action(req: C.ActionRequest, aid: str | None = Cookie(default=None)):
        account_id = _aid_from_cookie(aid)
        if account_id is None or account_id not in server.game.accounts:
            return JSONResponse({"detail": "not logged in"}, status_code=401)
        async with server.lock:
            try:
                payload = dict(req.payload)
                payload["pool"] = req.pool
                detail = server.game.act(account_id, req.type, payload, ts=time.time())
                server.snapshot_prices()
            except GameError as e:
                return JSONResponse({"ok": False, "type": req.type, "pool": req.pool,
                                     "error": str(e)}, status_code=400)
        await server.broadcast()
        return {"ok": True, "type": req.type, "pool": req.pool, "detail": detail}

    @app.get("/leaderboard")
    def leaderboard():
        return {"rows": server.game.leaderboard(), "d": server.game.d}

    @app.get("/pool/{pool}/detail")
    def pool_detail(pool: str):
        if pool not in server.game.pools:
            return JSONResponse({"detail": "no such pool"}, status_code=404)
        p = server.game.pools[pool]
        return {"pool": pool, "price": p.price(), "tvl_usd0": p.tvl_usd0(),
                "price_history": server.price_history[pool], "d_series": server.store.d_series(),
                "book": p.engine.book(), "d": server.game.d}

    # ---- profile ----
    @app.get("/profile/{name}")
    def profile(name: str):
        acc = next((a for a in server.game.accounts.values() if a.name == name), None)
        if acc is None:
            return JSONResponse({"detail": "no such account"}, status_code=404)
        history = [{"seq": e.seq, "kind": e.kind, "payload": e.payload, "ts": e.ts}
                   for e in server.store.read_log() if e.account_id == acc.id]
        names = [e.payload.get("new_name") for e in server.store.read_log()
                 if e.account_id == acc.id and e.kind == "name"]
        positions = []
        for label, pool in server.game.pools.items():
            for pp in server.game.positions_of(acc.id, label):
                positions.append({"position_id": pp.id, "pool": label, "kind": pp.kind,
                                  "value_usd0": pool.position_value_at_d(pp.id, server.game.d)})
        return {"name": acc.name, "account_id": acc.id, "name_history": names,
                "balance_usd0": float(acc.balance_usd0), "balance_eth0": float(acc.balance_eth0),
                "fees_usd0": float(acc.fees_usd0), "taker_volume_usd0": float(acc.taker_volume),
                "maker_volume_usd0": float(acc.maker_volume), "positions": positions,
                "history": history, "d": server.game.d}

    @app.post("/profile/name")
    async def change_name(req: dict, aid: str | None = Cookie(default=None)):
        account_id = _aid_from_cookie(aid)
        if account_id is None or account_id not in server.game.accounts:
            return JSONResponse({"detail": "not logged in"}, status_code=401)
        async with server.lock:
            try:
                return server.game.name_change(account_id, str(req.get("new_name", "")), ts=time.time())
            except GameError as e:
                return JSONResponse({"detail": str(e)}, status_code=400)

    # ---- admin (name + password gate) ----
    @app.post("/admin/params")
    async def admin_params(req: C.AdminParamsRequest):
        if not _admin_ok(req.name, req.password):
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        if server.game.phase != "LOBBY":
            return JSONResponse({"detail": "params locked after start"}, status_code=400)
        async with server.lock:
            merged = {**server.params.to_dict(), **(req.params or {})}
            server.params = config.GameParams.from_dict(merged)
            server.store.set_meta("params", server.params.to_dict())
            server.game = Game.load(server.store, server.params)  # replay registers under new params
        return {"ok": True, "params": server.params.to_dict()}

    @app.post("/admin/start")
    async def admin_start(req: C.AdminAuth):
        if not _admin_ok(req.name, req.password):
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        async with server.lock:
            try:
                r = server.game.start(ts=time.time())
                server.store.set_meta("started_at", time.time())
                server.snapshot_prices()
            except GameError as e:
                return JSONResponse({"detail": str(e)}, status_code=400)
        await server.broadcast()
        return r

    @app.post("/admin/monitor")
    def admin_monitor(req: C.AdminAuth):
        if not _admin_ok(req.name, req.password):
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        g = server.game
        de, du = g.conservation_drift()
        players = []
        for a in g.accounts.values():
            pos = sum(len(g.positions_of(a.id, l)) for l in g.pools)
            players.append({"account_id": a.id, "name": a.name, "is_house": a.is_house,
                            "balance_usd0": float(a.balance_usd0), "balance_eth0": float(a.balance_eth0),
                            "positions": pos})
        return {"phase": g.phase, "d": g.d, "clock": g.clock(),
                "conservation": {"d_eth0": de, "d_usd0": du, "ok": not g.alerts},
                "alerts": g.alerts[-20:], "players": players, "params": server.params.to_dict()}

    # ---- websocket ----
    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        server.clients.add(ws)
        try:
            aid = _aid_from_cookie(ws.cookies.get("aid"))
            await ws.send_json(C.ws("hello", state=server.state_dict(aid)))
            await ws.send_json(C.ws("leaderboard", rows=server.game.leaderboard(), d=server.game.d))
            while True:
                await ws.receive_text()  # keepalive / ignore client messages
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            server.clients.discard(ws)

    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
    return app


app = create_app()
