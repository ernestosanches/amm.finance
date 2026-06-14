#!/usr/bin/env bash
# One-command demo: seed a deterministic populated game, then launch the live server.
# Re-run anytime to reset+replay the same game. Override host/port via env HOST/PORT.
set -euo pipefail
cd "$(dirname "$0")/.."

DB="${AMM_DB_PATH:-app/data/game.db}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

echo "== seeding demo game =="
python3 app/demo_seed.py --db "$DB" "$@"

echo
echo "== launching server on http://${HOST}:${PORT} =="
echo "   admin login:  ${AMM_ADMIN_NAME:-admin} / ${AMM_ADMIN_PASSWORD:-letmein-demo-admin}"
echo "   (the game is already RUNNING and seeded; open the URL to play/observe)"
echo
exec python3 app/run.py --host "$HOST" --port "$PORT"
