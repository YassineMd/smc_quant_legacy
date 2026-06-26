"""Step A.2 proof — rehydrate loads the real crash-recovery state correctly with obs=[] in prepare().

The fix drops ONLY the (write-only, never-read) order_blocks population. rehydrate_engines reads
engine_state + closed_buckets + footprints and NEVER order_blocks. This round-trip proves: write a history
with obs=[], rehydrate into fresh engines, and the buckets + engine_state + footprints come back
byte-identical; order_blocks ends up empty (the dropped write) and rehydrate doesn't care.

Run: python scripts/validate_rehydrate_no_obs.py   (exit 0 iff rehydrate is intact)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config                                                      # noqa: E402
from app.persistence import HistoryStore, _bucket_from_dict, _bucket_to_dict  # noqa: E402
from app.quant_engine import QuantEngine                                    # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    with open(os.path.join(ROOT, "data", "cb_1m_6k.jsonl")) as f:
        src = [_bucket_from_dict(json.loads(l)) for l in f if l.strip()][:200]
    eng = QuantEngine()
    eng.closed_buckets = list(src)
    eng.target_vol = 5000.0
    eng.vpin = 0.4242
    src_fp = {"1m": {1700000000: {"lastVol": 0.0, "levels": {"150.00": {"b": 1.0, "s": 2.0}}}}}
    core = SimpleNamespace(engines={"1m": eng}, footprints_db=src_fp)

    db = os.path.join(tempfile.mkdtemp(prefix="reh_noobs_"), "h.db")
    store = HistoryStore(db_path=db)
    payload, cursors = store.prepare(core)            # obs=[] now -> ob_rows empty
    assert store._write(payload), "write failed"
    store._cursor.update(cursors)

    fresh = {tf: QuantEngine() for tf in config.TIMEFRAMES}
    store.rehydrate_engines(fresh, {})                 # buckets + engine_state (footprints load via bootstrap())

    rb = fresh["1m"].closed_buckets
    ok_count = len(rb) == len(src)
    ok_bytes = ok_count and all(_bucket_to_dict(a) == _bucket_to_dict(b) for a, b in zip(rb, src))
    ok_tv = abs(fresh["1m"].target_vol - 5000.0) < 1e-9
    ok_vpin = abs(fresh["1m"].vpin - 0.4242) < 1e-9
    # footprints: the daemon loads these via bootstrap(); prove the WRITE round-trips at the table level
    # (untouched by the fix — fp_rows is built independently of obs)
    fp_row = store._conn.execute("SELECT node FROM footprints WHERE tf='1m'").fetchone()
    ok_fp = fp_row is not None and json.loads(fp_row[0]).get("levels") == {"150.00": {"b": 1.0, "s": 2.0}}
    ob_rows = store._conn.execute("SELECT COUNT(*) FROM order_blocks").fetchone()[0]
    store.close()

    print("closed_buckets : rehydrated=%d source=%d  count_ok=%s  BYTE-IDENTICAL=%s" % (
        len(rb), len(src), ok_count, ok_bytes))
    print("engine_state   : target_vol_ok=%s  vpin_ok=%s" % (ok_tv, ok_vpin))
    print("footprints     : round-trip_ok=%s" % ok_fp)
    print("order_blocks   : %d rows (the DROPPED write) — rehydrate never reads it" % ob_rows)
    ok = ok_count and ok_bytes and ok_tv and ok_vpin and ok_fp and ob_rows == 0
    print("\nREHYDRATE INTACT WITH obs=[]:", ok)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
