"""Load the GCS cold-archive (gzipped NDJSON chunks written by ops/archive_buckets.py) into the SAME
``(bids, raws, gaps)`` shape as study/pivot_backtest.load_local_tape — so every existing backtest runs
unchanged over the full, un-capped history instead of the ~4-day snapshot window.

Workflow:
  1. study/pull_archive.ps1        # gsutil rsync gs://.../ -> study/archive_data/
  2. from study.archive_loader import load_archive
     bids, raws, gaps = load_archive("1m")

Chunks are append-only and keyed by global id; overlaps (should be none) are de-duped id-wins-latest.
Each archived row already carries the true per-tf ``bid`` (== terminal Idx), so no meta lookup is needed.

Optional: to_parquet() caches a tf to a single Parquet file locally for fast repeated backtests (needs
pyarrow — a LOCAL-only dependency; the VM never touches it).
"""
from __future__ import annotations

import glob
import gzip
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ARCHIVE_DIR = os.path.join(HERE, "archive_data")
DEGENERATE_TV = 6000.0     # target_vol at/below this on a non-1m tf = a daemon COLD-BOOT bucket (seeded at the flat 5000
#                            default before calibration). A ~11.5h window on 2026-06-20 produced ~2618 such degenerate
#                            1h/4h buckets. 1m's real size IS ~5000, so it is exempt. Fixed at source (build_engine_registry
#                            tf-scaled seed); this drops the historical junk so backtests aren't contaminated.


def load_archive(tf: str, root: str = ARCHIVE_DIR, drop_degenerate: bool = True):
    """(bids, raws, gaps) for one tf from the local archive mirror. ``raws`` are the bucket data dicts
    (feed to app.persistence._bucket_from_dict, exactly like load_local_tape's output). ``gaps`` are
    (a, b) bid pairs where b != a+1 — a missed-archiver window shows up here, never silently.
    ``drop_degenerate`` (default on) removes the cold-boot junk buckets (see DEGENERATE_TV); pass False for the raw feed."""
    by_bid: dict[int, dict] = {}
    pattern = os.path.join(root, tf, "%s_*.jsonl.gz" % tf)
    for fn in sorted(glob.glob(pattern)):
        with gzip.open(fn, "rt", encoding="utf-8") as gz:
            for line in gz:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                # later chunks win on any (id) overlap; data is stored as a JSON string, parsed once here.
                d = json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]
                if drop_degenerate and tf != "1m":
                    try:
                        tv = float(d.get("target_vol") or 0.0)
                    except (TypeError, ValueError):
                        tv = 0.0
                    if 0.0 < tv <= DEGENERATE_TV:            # cold-boot junk on a non-1m tf -> skip
                        continue
                by_bid[int(r["bid"])] = d
    bids = sorted(by_bid)
    gaps = [(a, b) for a, b in zip(bids, bids[1:]) if b != a + 1]
    return bids, [by_bid[b] for b in bids], gaps


def to_parquet(tf: str, out: str | None = None, root: str = ARCHIVE_DIR) -> str:
    """Cache a tf's archive to a single Parquet file (columns: bid + the flattened scalar bucket fields;
    the full bucket dict is kept in a ``data`` JSON column so nothing is lost). LOCAL-only (pyarrow)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    bids, raws, _ = load_archive(tf, root)
    if not bids:
        raise SystemExit("archive_loader: no archived buckets for tf=%s under %s" % (tf, root))
    scalar_keys = ("open_price", "close_price", "high", "low", "poc_price",
                   "buy_vol", "sell_vol", "curr_vol", "buyer_er", "seller_er",
                   "start_time", "end_time", "target_vol", "vel_ratio")
    cols: dict[str, list] = {"bid": list(bids)}
    for k in scalar_keys:
        cols[k] = [d.get(k) for d in raws]
    cols["data"] = [json.dumps(d, separators=(",", ":")) for d in raws]   # lossless payload
    out = out or os.path.join(root, "%s.parquet" % tf)
    pq.write_table(pa.table(cols), out, compression="zstd")
    return out


if __name__ == "__main__":
    import sys
    tf = sys.argv[1] if len(sys.argv) > 1 else "1m"
    bids, raws, gaps = load_archive(tf)
    if bids:
        print("archive %s: %d buckets, bid %d..%d, %d gap(s)%s"
              % (tf, len(bids), bids[0], bids[-1], len(gaps),
                 (" -> " + str(gaps[:5])) if gaps else ""))
    else:
        print("archive %s: empty (run study/pull_archive.ps1 first)" % tf)
