"""ONE-TIME backfill: ingest the locally-banked history_snapshot_*.db into the GCS cold-archive so the
older history (aged off the live DB's 10k cap) joins what ops/archive_buckets.py captures going forward.

Reads every study/data/history_snapshot_*.db, computes each bucket's true per-tf bid from that snapshot's
meta.total_closed_<tf> (== load_local_tape's base+j+1), merges across snapshots newest-wins, and writes the
SAME gzip-NDJSON chunk format the live archiver uses ({tf,id,bid,start_time,end_time,data}) — named with a
``_bf_`` marker + bid range so it never clobbers a live id-named chunk. Writes locally to study/archive_data/
AND uploads to GCS. The loader de-dupes by bid, so backfill + live overlap harmlessly.

Run:  python study/backfill_archive.py [--gcs gs://bucket/prefix] [--no-upload]
"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import os
import sqlite3
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
GCS_DEFAULT = "gs://smc-quant-archive/solusdt"        # match ops/archive_buckets.py
ARCHIVE_DIR = os.path.join(HERE, "archive_data")
TFS = ["1m", "5m", "15m", "1h", "4h"]
MAX_ROWS_PER_CHUNK = 20000


def _merged_by_bid(tf: str):
    """{bid: (id, start_time, end_time, data_json_str)} merged over all snapshots (newest db wins)."""
    by_bid: dict[int, tuple] = {}
    for db in sorted(glob.glob(os.path.join(REPO, "study", "data", "history_snapshot_*.db"))):
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        row = con.execute("SELECT value FROM meta WHERE key=?", ("total_closed_%s" % tf,)).fetchone()
        if row is not None:
            tc = int(row[0])
            rows = con.execute(
                "SELECT id, start_time, end_time, data FROM closed_buckets WHERE tf=? ORDER BY id", (tf,)
            ).fetchall()
            base = tc - len(rows)
            for j, (rid, st, et, data) in enumerate(rows):
                by_bid[base + j + 1] = (rid, st, et, data)     # later (newer) snapshot overwrites
        con.close()
    return by_bid


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gcs", default=GCS_DEFAULT)
    ap.add_argument("--no-upload", action="store_true", help="write local chunks only, skip gsutil upload")
    args = ap.parse_args()

    for tf in TFS:
        by_bid = _merged_by_bid(tf)
        if not by_bid:
            print("%-4s no snapshot data" % tf); continue
        bids = sorted(by_bid)
        gaps = [(a, b) for a, b in zip(bids, bids[1:]) if b != a + 1]
        out_dir = os.path.join(ARCHIVE_DIR, tf)
        os.makedirs(out_dir, exist_ok=True)
        wrote = 0
        for i in range(0, len(bids), MAX_ROWS_PER_CHUNK):
            part = bids[i:i + MAX_ROWS_PER_CHUNK]
            fn = "%s_bf_%09d_%09d.jsonl.gz" % (tf, part[0], part[-1])
            path = os.path.join(out_dir, fn)
            with gzip.open(path, "wt", encoding="utf-8") as gz:
                for bid in part:
                    rid, st, et, data = by_bid[bid]
                    gz.write(json.dumps(
                        {"tf": tf, "id": rid, "bid": bid, "start_time": st, "end_time": et, "data": data},
                        separators=(",", ":")))
                    gz.write("\n")
            if not args.no_upload:
                subprocess.run(["gsutil", "-q", "cp", path, "%s/%s/%s" % (args.gcs, tf, fn)], check=True)
            wrote += len(part)
        print("%-4s backfilled bid %d..%d (%d buckets, %d gap%s)%s"
              % (tf, bids[0], bids[-1], wrote, len(gaps), "" if len(gaps) == 1 else "s",
                 (" -> " + str(gaps[:3])) if gaps else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
