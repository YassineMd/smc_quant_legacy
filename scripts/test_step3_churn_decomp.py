"""Step 3A authoritative synthetic gate (rule 0.7): honest churn decomposition.

History-independent, deterministic. Three parts:
  1. MATH  - opL+opS+clL+clS+churn == curr_vol EXACTLY across delta_oi>0,
     delta_oi<0, OI-neutral (pure churn), and the defensive |delta_oi|>vol case;
     and the 4 vectors carry ONLY the OI-confirmed portion (no 50/50 invention).
  2. SCHEMA round-trip - `churn` survives both serializers (wire `_assemble` /
     full_snapshot/live_snapshot AND persistence _bucket_to_dict/_from_dict).
  3. SCHEMA GUARD (rule 0.3) - a db tagged with an OLDER bucket schema version is
     cleared (not silently rehydrated under new semantics); a matching version
     loads and round-trips `churn`.

Run:
    python scripts/test_step3_churn_decomp.py
Exit 0 = all assertions passed.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app import config                                              # noqa: E402
from app.quant_engine import QuantBucket, QuantEngine               # noqa: E402
from app.persistence import (BUCKET_SCHEMA_VERSION, HistoryStore,   # noqa: E402
                            _bucket_from_dict, _bucket_to_dict)

_FAILS: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not cond:
        _FAILS.append(label)


def approx(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol


def _vsum(b) -> float:
    return b.opL + b.opS + b.clL + b.clS + b.churn


def _one_tick(vol, taker, doi):
    """Feed a single tick into a fresh engine (target_vol huge -> one open bucket)."""
    e = QuantEngine(target_vol=1e12)
    e.process_tick(price=150.0, vol=vol, taker_buy=taker, delta_oi=doi,
                   footprints_dict={}, tick_time=1000.0)
    return e.active_bucket


def part1_math() -> None:
    print("\n[1] MATH -5-component conservation + OI-confirmed-only vectors")

    # delta_oi > 0  -> opens only; clL=clS=0
    b = _one_tick(100.0, 60.0, 40.0)
    print(f"    doi>0  : opL/opS/clL/clS/churn = {b.opL}/{b.opS}/{b.clL}/{b.clS}/{b.churn}")
    check("doi>0: conservation opL+opS+clL+clS+churn == curr_vol",
          approx(_vsum(b), b.curr_vol), f"sum-cv={_vsum(b) - b.curr_vol:.2e}")
    check("doi>0: opens carry OI-confirmed split (24/16), no churn leak into vectors",
          approx(b.opL, 24.0) and approx(b.opS, 16.0) and b.clL == 0.0 and b.clS == 0.0)
    check("doi>0: churn == vol - |doi| (60)", approx(b.churn, 60.0))

    # delta_oi < 0  -> closes only; opL=opS=0  (clL<-sellers, clS<-buyers)
    b = _one_tick(100.0, 70.0, -30.0)
    print(f"    doi<0  : opL/opS/clL/clS/churn = {b.opL}/{b.opS}/{b.clL}/{b.clS}/{b.churn}")
    check("doi<0: conservation holds", approx(_vsum(b), b.curr_vol),
          f"sum-cv={_vsum(b) - b.curr_vol:.2e}")
    check("doi<0: closes carry OI-confirmed split (clL 9 / clS 21), opens 0",
          approx(b.clL, 9.0) and approx(b.clS, 21.0) and b.opL == 0.0 and b.opS == 0.0)
    check("doi<0: churn == vol - |doi| (70)", approx(b.churn, 70.0))

    # OI-neutral (pure churn): all 4 vectors 0, churn == vol
    b = _one_tick(100.0, 50.0, 0.0)
    print(f"    doi==0 : opL/opS/clL/clS/churn = {b.opL}/{b.opS}/{b.clL}/{b.clS}/{b.churn}")
    check("OI-neutral: all 4 vectors are 0 (no fabricated open/close)",
          b.opL == 0.0 and b.opS == 0.0 and b.clL == 0.0 and b.clS == 0.0)
    check("OI-neutral: churn == curr_vol (100), conservation holds",
          approx(b.churn, 100.0) and approx(_vsum(b), b.curr_vol))

    # defensive |delta_oi| > vol (unclamped caller, e.g. replay) -> still conserves
    b = _one_tick(100.0, 60.0, 250.0)
    print(f"    |doi|>v: opL/opS/clL/clS/churn = {b.opL}/{b.opS}/{b.clL}/{b.clS}/{b.churn}")
    check("|doi|>vol: oi_mag floored at vol -> conservation still exact, churn 0",
          approx(_vsum(b), b.curr_vol) and approx(b.churn, 0.0) and approx(b.opL, 60.0))
    check("no vector or churn is negative / NaN / inf",
          all(x >= -1e-12 and math.isfinite(x) for x in (b.opL, b.opS, b.clL, b.clS, b.churn)))


def part2_roundtrip() -> None:
    print("\n[2] SCHEMA round-trip -churn survives wire + persistence serializers")
    b = _one_tick(100.0, 60.0, 40.0)   # churn == 60

    snap = b.live_snapshot(2000.0, 1.0)
    check("wire live_snapshot carries 'churn'", "churn" in snap, f"keys={'churn' in snap}")
    check("wire snapshot churn == 60 and conserves",
          approx(snap.get("churn", -1), 60.0)
          and approx(snap["opL"] + snap["opS"] + snap["clL"] + snap["clS"] + snap["churn"],
                     snap["curr_vol"]))

    # closed-bucket wire (full_snapshot) path
    b.end_time = 1010.0
    fs = b.full_snapshot()
    check("wire full_snapshot carries 'churn'", "churn" in fs and approx(fs["churn"], 60.0))

    # persistence DB serializer round-trip
    d = _bucket_to_dict(b)
    check("persistence _bucket_to_dict writes 'churn'", "churn" in d and approx(d["churn"], 60.0))
    b2 = _bucket_from_dict(json.loads(json.dumps(d)))
    check("persistence _bucket_from_dict restores churn + conservation",
          approx(b2.churn, 60.0) and approx(_vsum(b2), b2.curr_vol))


def _seed_db(store: HistoryStore, churn_val: float, version: int) -> None:
    """Insert one engine_state + one closed bucket + a meta schema version."""
    b = QuantBucket(5000.0, 1000.0)
    b.end_time, b.curr_vol = 1010.0, 5000.0
    b.opL, b.opS, b.churn = 3000.0, 2000.0, churn_val
    c = store._conn
    c.execute("INSERT INTO engine_state(tf,target_vol,avg_velocity,vpin,"
              "rolling_velocity,vpin_queue,active_bucket,updated_at) VALUES(?,?,?,?,?,?,?,?)",
              ("1m", 5000.0, 1.0, 0.0, "[]", "[]",
               json.dumps(_bucket_to_dict(QuantBucket(5000.0, 1000.0))), 0))
    c.execute("INSERT INTO closed_buckets(tf,start_time,end_time,data) VALUES(?,?,?,?)",
              ("1m", b.start_time, b.end_time, json.dumps(_bucket_to_dict(b))))
    # an order_block row + a footprint row, so the guard test can prove the OB/engine
    # rows are cleared while the FOOTPRINT (the separately-keyed rebuild source) survives.
    c.execute("INSERT INTO order_blocks(tf,ob_id,data) VALUES(?,?,?)",
              ("1m", "ob_seed", json.dumps({"ob_id": "ob_seed", "type": "bullish"})))
    c.execute("INSERT INTO footprints(tf,utime,node) VALUES(?,?,?)",
              ("1m", 1700000000, json.dumps({"lastVol": 0.0, "levels": {"150.00": {"b": 1.0, "s": 2.0}}})))
    c.execute("INSERT INTO meta(key,value) VALUES('bucket_schema_version',?) "
              "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(version),))
    c.commit()


def part3_schema_guard() -> None:
    print("\n[3] SCHEMA GUARD (rule 0.3) -stale schema cleared, current schema kept")
    tmp = tempfile.mkdtemp(prefix="step3_guard_")
    try:
        # (a) OLDER version -> must be cleared, engines cold-start
        old_db = os.path.join(tmp, "old.db")
        s_old = HistoryStore(db_path=old_db)
        _seed_db(s_old, churn_val=999.0, version=BUCKET_SCHEMA_VERSION - 1)
        engines = {tf: QuantEngine() for tf in config.TIMEFRAMES}
        s_old.rehydrate_engines(engines, {})
        n_rows = s_old._conn.execute("SELECT COUNT(*) FROM closed_buckets").fetchone()[0]
        ob_rows = s_old._conn.execute("SELECT COUNT(*) FROM order_blocks").fetchone()[0]
        eng_rows = s_old._conn.execute("SELECT COUNT(*) FROM engine_state").fetchone()[0]
        fp_rows = s_old._conn.execute("SELECT COUNT(*) FROM footprints").fetchone()[0]
        check("older schema: stale closed_buckets cleared", n_rows == 0, f"rows={n_rows}")
        check("older schema: stale order_blocks cleared", ob_rows == 0, f"rows={ob_rows}")
        check("older schema: stale engine_state cleared", eng_rows == 0, f"rows={eng_rows}")
        check("19.5 cutover: FOOTPRINTS SURVIVE the wipe (separately-keyed rebuild source kept)",
              fp_rows == 1, f"rows={fp_rows}")
        check("older schema: engines cold-start (no buckets loaded)",
              all(len(e.closed_buckets) == 0 for e in engines.values()))
        s_old.close()

        # (b) MATCHING version -> loads + round-trips churn
        cur_db = os.path.join(tmp, "cur.db")
        s_cur = HistoryStore(db_path=cur_db)
        _seed_db(s_cur, churn_val=123.0, version=BUCKET_SCHEMA_VERSION)
        engines2 = {tf: QuantEngine() for tf in config.TIMEFRAMES}
        s_cur.rehydrate_engines(engines2, {})
        loaded = engines2["1m"].closed_buckets
        check("current schema: bucket loaded (not cleared)", len(loaded) == 1, f"n={len(loaded)}")
        check("current schema: churn round-tripped through rehydrate",
              len(loaded) == 1 and approx(loaded[0].churn, 123.0),
              f"churn={loaded[0].churn if loaded else 'n/a'}")
        s_cur.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    part1_math()
    part2_roundtrip()
    part3_schema_guard()
    print("\n" + "=" * 60)
    if _FAILS:
        print(f"RESULT: FAILED ({len(_FAILS)}): {_FAILS}")
        return 1
    print("RESULT: ALL ASSERTIONS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
