"""B2 — persistence spine (APP_PLAN.md §7).

The append-only `actions` log is the **source of truth**; in-memory game state is a replayable
cache. Every mutation is appended and durably committed BEFORE it is acknowledged (WAL +
`synchronous=FULL` → fsync on commit), so a process crash loses nothing: on restart we replay the
log to rebuild exact state.

Three tables (§7):
  actions       — the ordered log of every event (register/buy/sell/deposit/withdraw/oracle/
                  freeze/settle/start/admin/name). THE source of truth.
  accounts      — projection cache (rebuilt from the log on boot; handy for SQL inspection).
  oracle_ticks  — projection of the external-price series for the D graph.
  meta          — small key/value config (params, seed, phase, timestamps).

Everything except `actions` is derivable; the projections exist for convenience/auditing.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS actions (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL    NOT NULL,
    kind       TEXT    NOT NULL,
    account_id INTEGER,
    payload    TEXT    NOT NULL DEFAULT '{}',
    result     TEXT    NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS accounts (
    id            INTEGER PRIMARY KEY,
    name          TEXT,
    is_house      INTEGER NOT NULL DEFAULT 0,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    balance_usd0  TEXT    NOT NULL DEFAULT '0',
    balance_eth0  TEXT    NOT NULL DEFAULT '0',
    fees_usd0     TEXT    NOT NULL DEFAULT '0',
    taker_volume  TEXT    NOT NULL DEFAULT '0',
    maker_volume  TEXT    NOT NULL DEFAULT '0'
);
CREATE TABLE IF NOT EXISTS oracle_ticks (
    step  INTEGER PRIMARY KEY,
    ts    REAL NOT NULL,
    d     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass
class LogEntry:
    seq: int
    ts: float
    kind: str
    account_id: Optional[int]
    payload: dict[str, Any]
    result: dict[str, Any]


class Store:
    def __init__(self, path: str):
        self.path = path
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # Durability: WAL + FULL fsync on commit. The single most important setting here.
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=FULL;")
        self.conn.executescript(SCHEMA)

    # --- the log (source of truth) ---
    def append(self, kind: str, account_id: Optional[int], payload: dict,
               result: Optional[dict] = None, ts: float = 0.0) -> int:
        """Append one event and durably commit it. Returns its seq. Caller acks only after this."""
        cur = self.conn.execute(
            "INSERT INTO actions (ts, kind, account_id, payload, result) VALUES (?,?,?,?,?)",
            (ts, kind, account_id, json.dumps(payload or {}), json.dumps(result or {})),
        )
        # isolation_level=None => each statement autocommits; synchronous=FULL => fsync'd here.
        return int(cur.lastrowid)

    def read_log(self) -> Iterator[LogEntry]:
        for r in self.conn.execute("SELECT seq, ts, kind, account_id, payload, result "
                                   "FROM actions ORDER BY seq ASC"):
            yield LogEntry(r["seq"], r["ts"], r["kind"], r["account_id"],
                           json.loads(r["payload"]), json.loads(r["result"]))

    def log_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) AS n FROM actions").fetchone()["n"])

    # --- meta ---
    def set_meta(self, key: str, value: Any) -> None:
        self.conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) "
                          "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (key, json.dumps(value)))

    def get_meta(self, key: str, default: Any = None) -> Any:
        r = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    # --- projections (rebuilt from the log on boot) ---
    def reset_projections(self) -> None:
        self.conn.execute("DELETE FROM accounts")
        self.conn.execute("DELETE FROM oracle_ticks")

    def upsert_account(self, **f: Any) -> None:
        cols = ["id", "name", "is_house", "is_admin", "balance_usd0", "balance_eth0",
                "fees_usd0", "taker_volume", "maker_volume"]
        vals = [f.get(c) for c in cols]
        for i, c in enumerate(cols):
            if c in ("balance_usd0", "balance_eth0", "fees_usd0", "taker_volume", "maker_volume") and vals[i] is not None:
                vals[i] = str(vals[i])
        placeholders = ",".join("?" * len(cols))
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "id")
        self.conn.execute(
            f"INSERT INTO accounts ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}", vals)

    def record_oracle_tick(self, step: int, ts: float, d: float) -> None:
        self.conn.execute("INSERT INTO oracle_ticks (step, ts, d) VALUES (?,?,?) "
                          "ON CONFLICT(step) DO UPDATE SET d=excluded.d, ts=excluded.ts",
                          (step, ts, d))

    def d_series(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT step, ts, d FROM oracle_ticks ORDER BY step ASC")]

    # --- durability helpers ---
    def checkpoint(self) -> None:
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")

    def backup_to(self, dest: str) -> str:
        """Consistent off-box copy (§7 mechanism 3). Uses SQLite's online backup API."""
        d = os.path.dirname(dest)
        if d:
            os.makedirs(d, exist_ok=True)
        with sqlite3.connect(dest) as bck:
            self.conn.backup(bck)
        return dest

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass


def replay(store: Store, apply_fn: Callable[[LogEntry], None]) -> int:
    """Feed the whole ordered log through apply_fn to rebuild state. Returns the count replayed."""
    n = 0
    for entry in store.read_log():
        apply_fn(entry)
        n += 1
    return n
