#!/usr/bin/env python3
"""Stage 4.1 — tiny RPC usage counter for the keyless endpoint.

There is no account/dashboard on the public endpoint, so we count calls ourselves.
Wrap a Web3 provider; every JSON-RPC request is tallied by method. At the end of a run
call `dump()` to append one row to usage.csv (wide table: run_at, label, total, then one
column per RPC method) so total endpoint usage so far is visible across runs.

Usage:
    from usage import UsageCounter
    uc = UsageCounter(); uc.attach(w3)
    ... do work ...
    print(uc.summary()); uc.dump(label="tick_snapshot 2026-06-12")
"""
import csv
import os
from collections import Counter
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(HERE, "usage.csv")
META_COLS = ["run_at", "label", "total"]


class UsageCounter:
    def __init__(self):
        self.counts = Counter()

    def attach(self, w3):
        """Wrap the provider's make_request so every RPC call is counted by method."""
        provider = w3.provider
        original = provider.make_request

        def counting(method, params):
            self.counts[str(method)] += 1
            return original(method, params)

        provider.make_request = counting
        return self

    def total(self):
        return sum(self.counts.values())

    def summary(self):
        lines = [f"RPC calls this run: {self.total()}"]
        for method, n in sorted(self.counts.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"  {n:>5}  {method}")
        return "\n".join(lines)

    def dump(self, label, path=DEFAULT_PATH):
        """Append this run as a row; rewrite the file so the method columns stay a stable union."""
        run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        new_row = {"run_at": run_at, "label": label, "total": self.total()}
        new_row.update(self.counts)

        existing = []
        if os.path.exists(path) and os.path.getsize(path) > 0:
            with open(path, newline="") as f:
                existing = list(csv.DictReader(f))

        rows = existing + [new_row]
        methods = sorted({k for r in rows for k in r} - set(META_COLS))
        fieldnames = META_COLS + methods
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, 0) for k in fieldnames})
        print(f"  usage -> {path} ({self.total()} calls this run)")
        return path
