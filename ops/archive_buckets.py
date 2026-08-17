#!/usr/bin/env python3
"""Incremental COLD-ARCHIVE of the daemon's closed_buckets -> gzipped NDJSON -> GCS.

WHY: history.db is capped at ~10k buckets/tf (a ~4-day rolling 1m window), so long-horizon backtests
can't run off the live DB. This captures every bucket into an append-only cold store BEFORE the cap can
delete it, so the archive grows forever and stays contiguous — while the live daemon keeps its small, fast,
capped hot table untouched.

DESIGN (deliberately decoupled from the daemon):
  * Runs as a cron job on the VM, NOT inside the daemon -> zero added daemon latency, no restart needed.
  * Opens history.db READ-ONLY in WAL mode (readers never block the daemon's writes) inside ONE snapshot
    transaction, so meta.total_closed_<tf> and the bucket rows are mutually consistent.
  * Per-tf HIGH-WATER-MARK (last archived global id) in a small state file -> contiguous, no re-archiving.
  * Each row carries the TRUE per-tf ``bid`` (== the terminal's "Idx"), derived from total_closed exactly
    as study/pivot_backtest.load_local_tape does, so cited buckets line up with the live terminal.
  * Pure stdlib + `gsutil` for upload -> tiny footprint on the memory-constrained e2-small box. Parquet
    conversion happens LOCALLY (study/archive_loader.py), where it's free.

Contiguity guarantee: run this on a cadence WELL under the cap's age (1m cap ~4 days) — e.g. every 6h — so
no bucket ages off between runs. A missed window shows up as a bid gap in the loader (never silent).

Usage:  python3 ops/archive_buckets.py [--db PATH] [--gcs gs://bucket/prefix] [--dry-run]
Cron (as the daemon's service user, every 6h):
  0 */6 * * * cd /home/yassine.mdouari/OrderFlowPlatform && /usr/bin/python3 ops/archive_buckets.py >> data/archive.log 2>&1
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sqlite3
import subprocess
import sys
import time

# --- configuration (override via CLI) --------------------------------------------------------------
DB_DEFAULT = "/home/yassine.mdouari/OrderFlowPlatform/data/history.db"
GCS_DEFAULT = "gs://smc-quant-archive/solusdt"        # <-- CREATE this bucket + grant the VM SA access
STATE_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "archive_state.json")
STAGE_DIR = "/tmp/bucket_archive"                      # local staging before upload (cleaned after)
TFS = ["1m", "5m", "15m", "30m", "1h", "4h"]   # 30m added 2026-08-17: it was the ONLY tradeable tf without a cold-archive,
#                                                so it lacked the deep immutable history the others have -> its walls could
#                                                re-shape on reload and Radar Runner signals REPAINTED. Archiving it makes
#                                                30m as stable as 15m/1h (needs a deploy + it accumulates going forward).
MAX_ROWS_PER_CHUNK = 20000                             # keep a single gz chunk bounded


def _load_state(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(path: str, state: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=0)
    os.replace(tmp, path)                              # atomic: a crash mid-write never corrupts state


def _upload(local: str, remote: str) -> None:
    subprocess.run(["gsutil", "-q", "cp", local, remote], check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DB_DEFAULT)
    ap.add_argument("--gcs", default=GCS_DEFAULT, help="gs://bucket/prefix (per-tf subdirs are appended)")
    ap.add_argument("--state", default=STATE_DEFAULT)
    ap.add_argument("--dry-run", action="store_true", help="stage chunks + compute bids, but do NOT upload or advance the hwm")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print("archive: db not found: %s" % args.db, file=sys.stderr)
        return 2
    os.makedirs(STAGE_DIR, exist_ok=True)
    state = _load_state(args.state)

    con = sqlite3.connect("file:%s?mode=ro" % args.db, uri=True)
    con.execute("BEGIN")                               # one consistent read snapshot across meta + rows
    total = 0
    try:
        for tf in TFS:
            hwm = int(state.get(tf, 0))
            row = con.execute("SELECT value FROM meta WHERE key=?", ("total_closed_%s" % tf,)).fetchone()
            if row is None:
                continue
            total_closed = int(row[0])
            rows = con.execute(
                "SELECT id, start_time, end_time, data FROM closed_buckets WHERE tf=? AND id>? ORDER BY id",
                (tf, hwm)).fetchall()
            if not rows:
                continue
            # bid == true per-tf Idx: the newest fetched bucket is total_closed; each earlier one is -1.
            # (rows are the latest contiguous run, so this reproduces load_local_tape's base+j+1 exactly.)
            n = len(rows)
            base_bid = total_closed - (n - 1)
            new_hwm = hwm
            for i in range(0, n, MAX_ROWS_PER_CHUNK):
                chunk = rows[i:i + MAX_ROWS_PER_CHUNK]
                first_id, last_id = chunk[0][0], chunk[-1][0]
                fn = "%s_%09d_%09d.jsonl.gz" % (tf, first_id, last_id)
                path = os.path.join(STAGE_DIR, fn)
                with gzip.open(path, "wt", encoding="utf-8") as gz:
                    for j, (rid, st, et, data) in enumerate(chunk):
                        gz.write(json.dumps(
                            {"tf": tf, "id": rid, "bid": base_bid + i + j,
                             "start_time": st, "end_time": et, "data": data},
                            separators=(",", ":")))
                        gz.write("\n")
                if not args.dry_run:
                    _upload(path, "%s/%s/%s" % (args.gcs, tf, fn))
                    os.remove(path)
                new_hwm = last_id
                total += len(chunk)
            state[tf] = new_hwm
    finally:
        con.close()

    if not args.dry_run:
        _save_state(args.state, state)
    print("%s archive: %d new buckets | hwm=%s%s" % (
        time.strftime("%Y-%m-%d %H:%M:%S"), total,
        {tf: state.get(tf) for tf in TFS}, "  [DRY-RUN]" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
