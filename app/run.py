#!/usr/bin/env python3
"""Launch the game server.

  python app/run.py [--host 127.0.0.1] [--port 8000] [--reset]

`--reset` deletes the SQLite db first (fresh game). Default host is loopback; pass
--host 0.0.0.0 to expose on a LAN for the event.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reset", action="store_true", help="delete the SQLite db before starting")
    args = ap.parse_args()

    from backend import config
    if args.reset and os.path.exists(config.DB_PATH):
        for suffix in ("", "-wal", "-shm"):
            p = config.DB_PATH + suffix
            if os.path.exists(p):
                os.remove(p)
        print(f"reset: removed {config.DB_PATH}")

    import uvicorn
    uvicorn.run("backend.api:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
