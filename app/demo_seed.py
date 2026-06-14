#!/usr/bin/env python3
"""Seed a deterministic, populated demo game into the SQLite db, then exit.

After this runs, `python app/run.py` loads the seeded game (via log replay) and continues it live
— so you can open the UI and immediately see players, liquidity, trades, and a populated level-3
book. Re-running with the same seed reproduces the identical game (replay/retest).

  python app/demo_seed.py [--db PATH] [--players 6] [--rounds 8] [--seed 7]
"""
import argparse
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend import config, engine as E
from backend.game import Game, GameError
from backend.persistence import Store

BOTS = ["Ava", "Ben", "Cara", "Dmitri", "Eve", "Finn", "Gita", "Hugo", "Iris", "Jun"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=config.DB_PATH)
    ap.add_argument("--players", type=int, default=6)
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--keep", action="store_true", help="append to an existing db instead of resetting")
    args = ap.parse_args()

    if not args.keep:
        for suf in ("", "-wal", "-shm"):
            p = args.db + suf
            if os.path.exists(p):
                os.remove(p)

    store = Store(args.db)
    params = config.GameParams(oracle_seed=args.seed)
    game = Game(store, params)

    rng = random.Random(args.seed)
    players = []
    for i in range(min(args.players, len(BOTS))):
        aid = game.register(BOTS[i])["account_id"]
        players.append(aid)
    game.start()
    print(f"registered {len(players)} players, seeded both pools at k*X = {params.k * params.x:,.0f}")

    spacing = params.tick_spacing
    for rnd in range(args.rounds):
        for aid in players:
            acc = game.accounts[aid]
            pool = rng.choice(["v3", "curve"])
            eng = game.pools[pool].engine
            t = E.price_to_tick(eng.price())
            try:
                roll = rng.random()
                if roll < 0.45:  # trade
                    if rng.random() < 0.5:
                        amt = float(acc.balance_usd0) * rng.uniform(0.02, 0.08)
                        game.act(aid, "buy", {"pool": pool, "amount_in": amt})
                    else:
                        amt = float(acc.balance_eth0) * rng.uniform(0.02, 0.08)
                        game.act(aid, "sell", {"pool": pool, "amount_in": amt})
                elif roll < 0.85:  # deposit
                    budget = float(acc.balance_usd0) * rng.uniform(0.05, 0.2)
                    if pool == "v3":
                        w = rng.choice([6, 12, 24]) * spacing
                        game.act(aid, "deposit", {"pool": pool, "kind": "range",
                                                  "tick_lower": t - w, "tick_upper": t + w,
                                                  "budget_usd0": budget})
                    else:
                        prof = {t + k * spacing: max(0.0, 1 - abs(k) / 6) for k in range(-5, 6)}
                        game.act(aid, "deposit", {"pool": pool, "kind": "curve",
                                                  "profile": prof, "budget_usd0": budget})
                else:  # withdraw one position if any
                    pos = game.positions_of(aid, pool)
                    if pos:
                        game.act(aid, "withdraw", {"pool": pool, "position_id": rng.choice(pos).id})
            except GameError:
                pass  # bot tried something it couldn't afford — skip
        game.tick()

    de, du = game.conservation_drift()
    print(f"after {args.rounds} rounds: step={game.step} phase={game.phase} "
          f"D={game.d:,.2f}  conservation Δ=({de:.2e}, {du:.2e})  alerts={len(game.alerts)}")
    print("\nleaderboard:")
    for r in game.leaderboard():
        tag = " (house)" if r["is_house"] else ""
        print(f"  {r['name']:<22}{tag:<9} ${r['total_value_usd0']:>12,.0f}")
    store.close()
    print(f"\nseeded db: {args.db}\nnow run:  python app/run.py   (loads this game and continues it live)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
