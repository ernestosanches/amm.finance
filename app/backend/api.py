"""S0 — minimal app: /health + static frontend mount. Expanded to the full REST/WS surface
in Stage B4 (this factory stays the entry point)."""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")


def create_app() -> FastAPI:
    app = FastAPI(title="Multiplayer AMM Game", version="0.1.0")

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "service": "amm-game"}

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
    return app


app = create_app()
