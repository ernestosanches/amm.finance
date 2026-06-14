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

---

## Stage B1 — Engine: the live swap loop ✅

**Done.** `backend/engine.py` — a *live* AMM engine that computes output + new price from order
arrival (not a replay). Key design choice: **band-native representation**. Because every position
is `tickSpacing`-aligned (§1/§6), liquidity is a per-band total `band_L[bt]` and a position is a
profile `{bt: L_i}`. This makes the cross-tick swap loop and arbitrary "curve" profiles simple and
exact, and active liquidity at spot is just `band_L[band(spot)]` — no liquidityNet/bitmap needed.

Implemented: pure tick/price math + `inventory` (§3); `quote_range` / `quote_curve` (shape →
budget → per-band L, with non-negativity validation and both-token amounts for the UI to gate on);
`add_position` / `remove_position`; the **cross-tick `swap`** (exact-input, fee skimmed off input,
halt on empty region §11, fee accrued pro-rata-by-L to in-range positions); `reserves` / `tvl_usd0`
/ `position_value_usd0`; and a `book()` L2/L3 snapshot with bid/ask/straddle sides read off spot.

**Worked:** every ORDERS §7 invariant holds against the live loop on the first green run —
path-independence at γ=0 (round-trip returns price + amount), geometric-mean fill, fee = γ·input,
marginal spread = fee, L never mutated by a swap, per-swap token conservation (reserves Δ = net-in /
−out, Σ fee_by_position = fee), cross-tick active-L change, and empty-region halt (consumed < asked).

**Issues:** none blocking. Band-boundary bookkeeping (which band you're in after landing exactly on
an edge) is the only fiddly part — handled by persisting the current band index `_band` and stepping
it ±spacing on each crossing rather than re-deriving from a float price.

**Tests:** `test_engine.py` — deposit math (above/below/straddle, reserves = Σ positions, negative
curve rejected), swap invariants (geo-mean, path-independence, no-L-mutation, conservation, fee=γ,
spread), cross-tick (active-L change, empty halt), add/remove round-trip, book sides. **15 tests; 25
total green.**
