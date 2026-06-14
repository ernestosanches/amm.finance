# APP_WORK.md — build log for the Multiplayer AMM Game

Implementation of `APP_PLAN.md` §13, built in `app/`. One section per stage: what was done, what
worked, what didn't, and decisions taken. Checkbox state lives in `APP_PLAN.md`.

**Stack as built:** Python 3.10 · FastAPI 0.115 / uvicorn · SQLite (WAL) · **vanilla-JS frontend**
(no React/build/CDN — see the S0 note). Tests: stdlib `unittest`, run with `python app/tests.py`.

**How to run:** `pip install -r app/requirements.txt` then `python app/run.py` → http://127.0.0.1:8000.

---

## Stage S0 — Skeleton + frozen contracts ✅

**Done.** Repo layout under `app/` (`backend/`, `frontend/`, `tests/`, `data/`). Pinned the wire
contract in `backend/contracts.py` (Pydantic): REST auth/action/state/leaderboard models + a
WebSocket envelope `{type, data}` with a `ws()` helper and a closed set of frame types
(`d_tick/pool/clock/leaderboard/phase/hello`). Default game parameters (§10) in `backend/config.py`
as a `GameParams` dataclass (with mean-preserving `mu`, conservation tolerance, admin gate, db
path). Minimal FastAPI app (`backend/api.py`) serving `/health` + the static frontend; `run.py`
launcher (`--reset`, `--host/--port`). Frontend skeleton (`index.html`, `styles.css`, `app.js`)
with a tiny dependency-free DOM helper (`el`/`mount`/`api`/`fmt`) that pings `/health`.

**Decision / minor plan update — frontend is vanilla JS, not React.** The plan named React, but the
overriding constraints are *quick demo, offline replay, no build toolchain*. A no-build vanilla-ES-
module SPA (served statically by the backend, inline-SVG charts) needs only `python` to run and
retest — no node, npm, or CDN. Kept the structure component-like so it reads familiarly. Recorded
here and reflected in `APP_PLAN.md` §13 (F-stages).

**Worked:** contract round-trips and the health/static serving on the first run. **Issues:** none.

**Tests:** `test_contracts.py` — health/index/static served (200), every WS type round-trips +
unknown type rejected, REST read-models validate (and invalid pool rejected), params defaults/`mu`/
roundtrip. **10 tests green.**
