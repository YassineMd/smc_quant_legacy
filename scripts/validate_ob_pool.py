"""Step A proof #1 — the process-pool OB rescan is byte-IDENTICAL to the on-loop rescan.

calc_quant_obs is a PURE function of the engine's closed buckets, so moving WHERE it runs (a worker process
vs the event loop) must not change WHAT it computes. This asserts ``_recompute_ob_line(buckets, tf, vpin)``
produces the byte-for-byte same ObPacket line on-loop vs in a spawn ProcessPoolExecutor, on REAL buckets.

Real-bucket source (auto):
  * data/history.db present  -> rehydrate ALL 5 engines via the DAEMON'S OWN path (persistence) — use this on
    the VM for the production proof.
  * else                     -> data/cb_1m_6k.jsonl (6000 real 1m closed buckets, the heaviest engine).

Run:  python scripts/validate_ob_pool.py     (exit 0 iff every tf is byte-identical)
"""
from __future__ import annotations

import json
import multiprocessing
import os
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config                                    # noqa: E402
from app.feeds import _recompute_ob_line                  # the exact fn the daemon offloads  # noqa: E402
from app.persistence import _bucket_from_dict             # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY = os.path.join(ROOT, "data", "history.db")
CB_LOCAL = os.path.join(ROOT, "data", "cb_1m_6k.jsonl")


def _load_ro_sqlite(db: str):
    """READ-ONLY direct read of closed_buckets per tf (for the VM: point at a `.backup` snapshot so we never
    contend with the live daemon's history.db). Same SQL/shape persistence uses."""
    import sqlite3
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out = {}
    try:
        for tf in config.TIMEFRAMES:
            rows = con.execute("SELECT data FROM closed_buckets WHERE tf=? ORDER BY id DESC LIMIT ?",
                               (tf, config.CLOSED_BUCKETS_CAP)).fetchall()
            out[tf] = ([_bucket_from_dict(json.loads(r[0])) for r in reversed(rows)], 0.0)
    finally:
        con.close()
    print(f"source: {db} (read-only) — {sum(len(b) for b, _ in out.values())} buckets across {len(out)} tfs")
    return out


def load_real_engines():
    """{tf: (buckets, vpin)} from real data — OB_VALIDATE_DB (read-only snapshot) if set, else the daemon's
    rehydrate path if history.db exists, else the local cb_1m_6k.jsonl (1m only)."""
    env_db = os.environ.get("OB_VALIDATE_DB")
    if env_db and os.path.exists(env_db):
        return _load_ro_sqlite(env_db)
    if os.path.exists(HISTORY):
        from app.persistence import HistoryStore
        from app.quant_engine import build_engine_registry
        store = HistoryStore(); fp = store.bootstrap()
        engines = build_engine_registry()
        store.rehydrate_engines(engines, fp)
        print(f"source: data/history.db (daemon rehydrate path, {len(config.TIMEFRAMES)} engines)")
        return {tf: (list(engines[tf].closed_buckets), float(engines[tf].vpin)) for tf in config.TIMEFRAMES}
    if os.path.exists(CB_LOCAL):
        with open(CB_LOCAL) as f:
            buckets = [_bucket_from_dict(json.loads(ln)) for ln in f if ln.strip()]
        print(f"source: data/cb_1m_6k.jsonl ({len(buckets)} real 1m buckets)")
        return {"1m": (buckets, 0.0)}
    print("NO real-bucket source (need data/history.db or data/cb_1m_6k.jsonl)")
    sys.exit(2)


def main():
    data = load_real_engines()
    pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
    ok = True
    try:
        for tf, (buckets, vpin) in data.items():
            if not buckets:
                print(f"{tf:>3}: (no buckets, skip)"); continue
            onloop = _recompute_ob_line(buckets, tf, vpin)
            pooled = pool.submit(_recompute_ob_line, buckets, tf, vpin).result()
            same = onloop == pooled
            ok = ok and same
            print(f"{tf:>3}: buckets={len(buckets):>6}  on-loop={len(onloop):>8}B  pool={len(pooled):>8}B  "
                  f"IDENTICAL={same}")
    finally:
        pool.shutdown(wait=True)
    print("\nALL TIMEFRAMES BYTE-IDENTICAL:", ok)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
