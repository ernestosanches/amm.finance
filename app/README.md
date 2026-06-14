# Multiplayer AMM Game

A closed-economy, multiplayer market-making game for a ~1-hour live event (build spec:
`../APP_PLAN.md`; build log: `../APP_WORK.md`). Players get a balanced bag of two virtual tokens
(ETH0 / USD0) and compete for the highest USD0-marked portfolio at settlement, acting only through
two internal pools — a Uniswap-v3-style range pool and a "draw-your-curve" pool.

**Stack:** Python 3.10 · FastAPI / uvicorn · SQLite (WAL) · dependency-free vanilla-JS frontend
(no build step, no CDN — runs offline).

## Run

```bash
pip install -r app/requirements.txt

# quickest: seed a deterministic populated game and launch it live
bash app/run_demo.sh                      # -> http://127.0.0.1:8000

# or a blank game you start yourself from the Admin page
python app/run.py                          # add --reset for a fresh db, --host 0.0.0.0 for LAN
```

Admin login (Admin page → Start game, set parameters): `admin` / `letmein-demo-admin`
(override via `AMM_ADMIN_NAME` / `AMM_ADMIN_PASSWORD`).

## Replay / retest

The SQLite **action log is the source of truth**, so the game is fully deterministic and
replayable:

- `python app/demo_seed.py --seed 7` writes a reproducible game to the db; `python app/run.py`
  loads it (by replaying the log) and continues it live. Same seed → identical game.
- Killing the server (even `kill -9`) loses nothing: on restart it replays the log to the exact
  prior state. Verified by `python app/chaos_check.py`.

## Tests

```bash
python app/tests.py            # offline unit suite (engine, persistence, game, API, durability)
python app/render_check.py     # headless-browser smoke: drives the full UI flow, screenshots to app/out/
python app/chaos_check.py      # kill -9 recovery + load burst + WS reconnect storm
```

## Layout

```
backend/  engine.py (AMM order-book) · persistence.py (SQLite log) · game.py (accounts/pools/oracle/
          state machine) · api.py (REST+WS) · config.py · contracts.py
frontend/ index.html · lib.js · app.js · pages_play.js · pages_read.js · pages_admin.js  (vanilla JS)
run.py · demo_seed.py · run_demo.sh · render_check.py · chaos_check.py · tests.py
```
