"""Tier-2 daemon-only persistence — instantaneous SQLite state store.

Replaces the legacy JSON "tick-replay" rehydration (too slow/heavy on a cloud
reboot) with a ``data/history.db`` SQLite store that lets the daemon **arm the
engines instantly** on boot — finalized state is loaded straight from disk, no
``process_tick`` recomputation.

Design
------
* :class:`HistoryStore` owns the single WAL connection (``check_same_thread=False``
  + a ``threading.Lock``) and ALL database I/O. The quant core (``quant_engine``)
  stays pure math — this module reaches into ``QuantEngine``/``QuantBucket``
  PUBLIC ATTRIBUTES for (de)serialization; no SQLite logic ever leaks into it.
* Threading split (the important bit): the daemon is single-threaded asyncio.
  :meth:`HistoryStore.prepare` runs ON THE EVENT LOOP — it reads the live engines
  + footprint dict (race-free, same thread) and SERIALIZES a plain payload.
  :meth:`HistoryStore._write` runs in a thread executor and only touches that
  immutable payload — disk I/O never blocks the loop and never races shared state.
* ``closed_buckets`` have no natural unique key (``start_time`` is not unique and
  every closed bucket carries ``curr_vol == target_vol``), so the table uses a
  surrogate autoincrement ``id`` and an APPEND-ONLY write cursor (a per-tf
  reference to the last-persisted bucket object). ``footprints`` DO have a natural
  ``(tf, utime)`` key → clean UPSERT.

Retention: last ``CLOSED_BUCKETS_CAP`` (10k) buckets/tf and ``FOOTPRINT_CAP``
footprint nodes/tf on disk, pruned during the background sync. The in-memory
footprint dict is trimmed to ``FOOTPRINT_MEM_CAP`` (kept ≥2h for ``recalibrate``).

The legacy JSON loader + tick-replay are kept solely as a ONE-TIME MIGRATION: a
fresh DB with an old ``server_footprints.json`` present replays once to warm the
engines, then the first sync seeds SQLite and every subsequent boot is instant.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
import time
from collections import deque
from typing import Dict, List, Optional, Tuple

from . import config
from .quant_engine import QuantBucket, QuantEngine   # Step A.2: calc_quant_obs/rank_obs no longer needed here

_INF = float("inf")
_NEG_INF = float("-inf")

# Bucket-vector schema version (rule 0.3). Bumped whenever the meaning or set of
# per-bucket fields changes, so a populated db from an older schema is never
# silently rehydrated under the new semantics (the silent mixed-meaning trap).
# v2 = Step 3 (OI-confirmed vectors + explicit `churn`). v3 = Step 19.5: NO field
# change, but the *meaning* of every bucket's levels changed (kline-smeared-onto-
# close -> aggTrade true-price), a DELIBERATE FIDELITY CUTOVER — the bump wipes the
# working db so kline-built and aggTrade-built buckets never share a rolling window
# (footprints survive: they are keyed separately and are the rebuild source).
# Anything older — incl. an unversioned legacy db, read as 0 — is cleared on boot.
BUCKET_SCHEMA_VERSION = 3


# ---------------------------------------------------------------------------
# QuantBucket <-> dict  (serialization lives HERE, not in the pure math core)
# ---------------------------------------------------------------------------
def _bucket_to_dict(b: QuantBucket) -> dict:
    """Full, reconstructable QuantBucket state incl. ``levels`` + liquidations.

    ``high``/``low`` of an untouched bucket are ±inf sentinels; stored as ``None``
    so the on-disk JSON stays standard (no ``Infinity`` literal)."""
    return {
        "target_vol": b.target_vol,
        "start_time": b.start_time,
        "end_time": b.end_time,
        "curr_vol": b.curr_vol,
        "buy_vol": b.buy_vol,
        "sell_vol": b.sell_vol,
        "opL": b.opL, "opS": b.opS, "clL": b.clL, "clS": b.clS,
        "churn": b.churn,
        "high": None if b.high == _NEG_INF else b.high,
        "low": None if b.low == _INF else b.low,
        "open_price": b.open_price,
        "close_price": b.close_price,
        "levels": b.levels,
        "liquidations": b.liquidations,
        "vel_ratio": b.vel_ratio,
        "poc_price": b.poc_price,
        "buyer_er": b.buyer_er,
        "seller_er": b.seller_er,
        # LARGE/SMALL size histograms — additive (_bucket_from_dict reads with a zero-filled default, so
        # NO BUCKET_SCHEMA_VERSION bump; pre-feature rows reload as zeros).
        "sz_cb": b.sz_cb, "sz_cs": b.sz_cs, "sz_vb": b.sz_vb, "sz_vs": b.sz_vs,
        # Same additive contract: CVD wicks (intrabar delta peak/trough) + "E/R per tick" (ticks price
        # actually travelled each way). These MUST be persisted — they are measured per aggTrade as the
        # bucket is built and can NEVER be recomputed from the stored aggregates, so dropping them here
        # would silently make them ephemeral: correct on the live wire, then gone at the next restart.
        "cvd_hi": b.cvd_hi, "cvd_lo": b.cvd_lo,
        "delta_h1": b.delta_h1,          # first-half net delta (50%-vol mark) for MMXSKEW delta_accel_2; None until reached
        "price_h1": b.price_h1,          # price at that same mark -> per-half absorption residual; None until reached
        "up_ticks": b.up_ticks, "dn_ticks": b.dn_ticks,
    }


def _bucket_from_dict(d: dict) -> QuantBucket:
    """Rebuild a QuantBucket from :func:`_bucket_to_dict` output (±inf restored)."""
    b = QuantBucket(d.get("target_vol", config.DEFAULT_TARGET_VOL), d.get("start_time", 0.0))
    b.end_time = d.get("end_time")
    b.curr_vol = d.get("curr_vol", 0.0)
    b.buy_vol = d.get("buy_vol", 0.0)
    b.sell_vol = d.get("sell_vol", 0.0)
    b.opL = d.get("opL", 0.0); b.opS = d.get("opS", 0.0)
    b.clL = d.get("clL", 0.0); b.clS = d.get("clS", 0.0)
    b.churn = d.get("churn", 0.0)
    b.high = _NEG_INF if d.get("high") is None else d["high"]
    b.low = _INF if d.get("low") is None else d["low"]
    b.open_price = d.get("open_price", 0.0)
    b.close_price = d.get("close_price", 0.0)
    b.levels = d.get("levels") or {}
    b.liquidations = d.get("liquidations") or []
    b.vel_ratio = d.get("vel_ratio", 1.0)
    b.poc_price = d.get("poc_price", 0.0)
    b.buyer_er = d.get("buyer_er", 1.0)
    b.seller_er = d.get("seller_er", 1.0)
    # Additive, zero-filled: a pre-feature row simply reloads with no wick / no per-tick E/R, which the
    # terminal renders as a plain body / "--" rather than inventing a value.
    b.cvd_hi = d.get("cvd_hi", 0.0); b.cvd_lo = d.get("cvd_lo", 0.0)
    b.delta_h1 = d.get("delta_h1")   # None if absent (pre-ship bucket) -> the terminal skips its da2 flag
    b.price_h1 = d.get("price_h1")   # None if absent -> per-half absorption unavailable, bucket-level R still works
    b.up_ticks = d.get("up_ticks", 0.0); b.dn_ticks = d.get("dn_ticks", 0.0)
    # LARGE/SMALL size histograms: override the __init__ zero arrays only when present AND the right length
    # (fixed edges => stable length; the guard keeps a stale/short row from corrupting the bucket).
    _nb = config.SIZE_HIST_NBINS
    for _k in ("sz_cb", "sz_cs", "sz_vb", "sz_vs"):
        _v = d.get(_k)
        if isinstance(_v, list) and len(_v) == _nb:
            setattr(b, _k, [float(x) for x in _v])
    return b


def bucket_from_snapshot(s: dict) -> QuantBucket:
    """Rebuild a QuantBucket from a BROADCAST :class:`BucketSnapshot` dict (the pipe form the TERMINAL
    holds — keys ``open``/``close``/``vol_mult``/``liq_short``/``liq_long``), distinct from
    :func:`_bucket_from_dict` (the on-disk ``_bucket_to_dict`` form — ``open_price``/``close_price``/
    ``vel_ratio``/``liquidations``). Lets the terminal run the excursion core on LIVE snapshot data with no
    local history.db (it talks to the remote daemon). ``target_vol`` isn't in the snapshot -> DEFAULT (only
    the VPIN denominator, whose fallback already handles it); ``liq_short``/``liq_long`` are re-expressed as
    a 2-entry liquidations list so a downstream ``full_snapshot()`` re-derives them faithfully (BUY=short-
    liq, SELL=long-liq, per QuantBucket._assemble)."""
    liq = []
    if s.get("liq_short", 0.0):
        liq.append({"side": "BUY", "qty": float(s["liq_short"])})
    if s.get("liq_long", 0.0):
        liq.append({"side": "SELL", "qty": float(s["liq_long"])})
    return _bucket_from_dict({
        "target_vol": config.DEFAULT_TARGET_VOL,
        "start_time": s.get("start_time", 0.0), "end_time": s.get("end_time", 0.0),
        "curr_vol": s.get("curr_vol", 0.0), "buy_vol": s.get("buy_vol", 0.0), "sell_vol": s.get("sell_vol", 0.0),
        "opL": s.get("opL", 0.0), "opS": s.get("opS", 0.0), "clL": s.get("clL", 0.0), "clS": s.get("clS", 0.0),
        "churn": s.get("churn", 0.0), "high": s.get("high"), "low": s.get("low"),
        "open_price": s.get("open", 0.0), "close_price": s.get("close", 0.0),
        "levels": s.get("levels") or {}, "liquidations": liq,
        "vel_ratio": s.get("vol_mult", 1.0), "poc_price": s.get("poc_price", 0.0),
        "buyer_er": s.get("buyer_er", 1.0), "seller_er": s.get("seller_er", 1.0),
        "sz_cb": s.get("sz_cb", []), "sz_cs": s.get("sz_cs", []),
        "sz_vb": s.get("sz_vb", []), "sz_vs": s.get("sz_vs", []),
        # wire form carries these under the SAME names (see BucketSnapshot); 0.0 when the daemon predates them
        "cvd_hi": s.get("cvd_hi", 0.0), "cvd_lo": s.get("cvd_lo", 0.0),
        "delta_h1": s.get("delta_h1"),
        "price_h1": s.get("price_h1"),
        "up_ticks": s.get("up_ticks", 0.0), "dn_ticks": s.get("dn_ticks", 0.0),
    })


# ---------------------------------------------------------------------------
# Legacy JSON path — kept ONLY for one-time migration into SQLite
# ---------------------------------------------------------------------------
def load_footprints_json() -> Dict[str, dict]:
    """Blocking read of the legacy ``server_footprints.json`` (migration source)."""
    config.ensure_data_dir()
    if not os.path.exists(config.FOOTPRINTS_FILE):
        return {}
    try:
        with open(config.FOOTPRINTS_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def rehydrate_engines_legacy(footprints_db: Dict[str, dict],
                             engines: Dict[str, QuantEngine]) -> None:
    """Verbatim legacy tick-replay (main.py:241) — used once on migration only.

    Distributes each minute's OI delta across its price nodes and feeds them back
    through ``process_tick`` so the rolling queues, bucket boundaries, and closed
    history are rebuilt exactly as they were live.
    """
    print("MIGRATION: replaying legacy footprints into the quant engines...")
    for tf_key, time_dict in footprints_db.items():
        if tf_key not in engines:
            continue
        engine = engines[tf_key]
        sorted_times = sorted(time_dict.keys(), key=lambda x: int(x))[-config.REHYDRATE_LIMIT:]
        for utime_str in sorted_times:
            tick_time = int(utime_str)
            fp = time_dict[utime_str]
            levels = fp.get("levels", {})
            minute_delta_oi = fp.get("oi_close", 0.0) - fp.get("oi_open", 0.0)
            total_vol = sum(v["b"] + v["s"] for v in levels.values())
            if total_vol <= 0:
                continue
            for p_str, vols in levels.items():
                level_vol = vols["b"] + vols["s"]
                if level_vol <= 0:
                    continue
                level_oi_delta = minute_delta_oi * (level_vol / total_vol)
                engine.process_tick(
                    price=float(p_str), vol=level_vol, taker_buy=vols["b"],
                    delta_oi=level_oi_delta, footprints_dict=time_dict, tick_time=tick_time,
                )
    print("MIGRATION REPLAY COMPLETE.")


# ---------------------------------------------------------------------------
# SQLite state store
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS engine_state (
    tf TEXT PRIMARY KEY,
    target_vol REAL, avg_velocity REAL, vpin REAL,
    rolling_velocity TEXT, vpin_queue TEXT, active_bucket TEXT,
    updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS closed_buckets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tf TEXT NOT NULL, start_time REAL, end_time REAL, data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cb_tf_id ON closed_buckets(tf, id);
CREATE TABLE IF NOT EXISTS order_blocks (
    tf TEXT NOT NULL, ob_id TEXT NOT NULL, data TEXT NOT NULL,
    PRIMARY KEY (tf, ob_id)
);
CREATE TABLE IF NOT EXISTS footprints (
    tf TEXT NOT NULL, utime INTEGER NOT NULL, node TEXT NOT NULL,
    PRIMARY KEY (tf, utime)
);
"""


class HistoryStore:
    """Owns the SQLite history DB and all of its I/O (WAL, single connection)."""

    def __init__(self, db_path: Optional[str] = None):
        config.ensure_data_dir()
        self.db_path = db_path or config.HISTORY_DB
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        # per-tf append cursor: the last closed bucket already on disk (by identity)
        self._cursor: Dict[str, Optional[QuantBucket]] = {}
        self.init_schema()

    # -- schema / lifecycle -------------------------------------------------
    def init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def _has_engine_state(self) -> bool:
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) FROM engine_state")
            return cur.fetchone()[0] > 0

    def _read_schema_version(self) -> int:
        """Stored bucket schema version (0 = unversioned / legacy db)."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT value FROM meta WHERE key='bucket_schema_version'")
            row = cur.fetchone()
        try:
            return int(row[0]) if row else 0
        except (TypeError, ValueError):
            return 0

    # -- boot ---------------------------------------------------------------
    def bootstrap(self) -> Dict[str, dict]:
        """Return the in-memory ``footprints_db`` for boot.

        Prefer the SQLite recent window; on a fresh DB with a legacy JSON present,
        return the JSON (it becomes the migration source + is seeded on first sync).
        """
        fp = self.load_footprints()
        if not any(fp.values()) and os.path.exists(config.FOOTPRINTS_FILE):
            legacy = load_footprints_json()
            if any(legacy.values()):
                print("LEGACY server_footprints.json found — migrating to SQLite on first sync.")
                return legacy
        return fp

    def load_footprints(self) -> Dict[str, dict]:
        """Load the most-recent ``FOOTPRINT_MEM_CAP`` footprint nodes per tf."""
        db: Dict[str, dict] = {}
        with self._lock:
            for tf in config.TIMEFRAMES:
                cur = self._conn.execute(
                    "SELECT utime, node FROM footprints WHERE tf=? ORDER BY utime DESC LIMIT ?",
                    (tf, config.FOOTPRINT_MEM_CAP))
                rows = cur.fetchall()
                if rows:
                    db[tf] = {str(u): json.loads(node) for u, node in reversed(rows)}
        return db

    def rehydrate_engines(self, engines: Dict[str, QuantEngine],
                          footprints_db: Optional[Dict[str, dict]] = None) -> None:
        """Arm the engines from SQLite (no tick replay).

        On an empty DB: one-time legacy migration if ``footprints_db`` carries
        legacy nodes, else a clean cold start. Either way the append cursor is
        primed so the first sync persists the current closed history.
        """
        # 0.3 schema guard: a populated db whose stored bucket vectors predate the
        # current schema would silently mix old/new semantics on rehydrate. Clear
        # the vector-bearing tables (footprints are schema-stable and kept) and
        # cold-start; new buckets re-accumulate under the current schema.
        db_ver = self._read_schema_version()
        if self._has_engine_state() and db_ver != BUCKET_SCHEMA_VERSION:
            with self._lock:
                self._conn.execute("DELETE FROM closed_buckets")
                self._conn.execute("DELETE FROM order_blocks")
                self._conn.execute("DELETE FROM engine_state")
                self._conn.commit()
            print(f"SCHEMA MIGRATION: db bucket schema v{db_ver} != code "
                  f"v{BUCKET_SCHEMA_VERSION}; cleared stale bucket/OB/engine state "
                  f"(footprints kept). Engines cold-start; history re-accumulates.")
            for tf in engines:
                self._cursor[tf] = None
            return

        if not self._has_engine_state():
            if footprints_db and any(footprints_db.values()):
                rehydrate_engines_legacy(footprints_db, engines)
            else:
                print("COLD START — no SQLite state, no legacy footprints.")
            for tf in engines:
                self._cursor[tf] = None   # first sync appends the full current history
            return

        total = 0
        with self._lock:
            for tf, engine in engines.items():
                row = self._conn.execute(
                    "SELECT target_vol, avg_velocity, vpin, rolling_velocity, vpin_queue, "
                    "active_bucket FROM engine_state WHERE tf=?", (tf,)).fetchone()
                if row:
                    target_vol, avg_v, vpin, rv_json, vq_json, ab_json = row
                    engine.target_vol = target_vol or config.DEFAULT_TARGET_VOL
                    engine.avg_velocity = avg_v or 1.0
                    engine.vpin = vpin or 0.0
                    engine.rolling_velocity = deque(
                        json.loads(rv_json or "[]"), maxlen=config.VELOCITY_LOOKBACK)
                    engine.vpin_queue = deque(
                        json.loads(vq_json or "[]"), maxlen=config.VPIN_WINDOW)
                    if ab_json:
                        engine.active_bucket = _bucket_from_dict(json.loads(ab_json))
                rows = self._conn.execute(
                    "SELECT data FROM closed_buckets WHERE tf=? ORDER BY id ASC", (tf,)).fetchall()
                engine.closed_buckets = [_bucket_from_dict(json.loads(r[0])) for r in rows]
                # ABSOLUTE bucket index: a PER-TF monotonic close counter (the stable bucket idx the client
                # shows). RESTORE it from meta so it survives restarts; bootstrap ONCE (first run after this
                # change) from the retained row count. NOT derived from the DB autoincrement id — that id is
                # shared across all 5 timeframes, so it is gapped per tf and would drift the idx on every
                # restart. (Inlined meta read: _lock is held here and is not re-entrant.)
                mrow = self._conn.execute(
                    "SELECT value FROM meta WHERE key=?", (f"total_closed_{tf}",)).fetchone()
                try:
                    engine.total_closed = int(mrow[0]) if mrow and mrow[0] is not None else len(rows)
                except (TypeError, ValueError):
                    engine.total_closed = len(rows)
                self._cursor[tf] = engine.closed_buckets[-1] if engine.closed_buckets else None
                total += len(engine.closed_buckets)
        print(f"SQLITE REHYDRATE COMPLETE — {total} closed buckets armed across "
              f"{len(engines)} engines (no tick replay).")

    # -- per-flush: prepare on the loop, write in the executor --------------
    def _new_closed(self, tf: str, closed: List[QuantBucket]
                    ) -> Tuple[str, List[QuantBucket], Optional[QuantBucket]]:
        """(mode, new_buckets, new_cursor) using the identity cursor.

        ``append`` for the normal tail growth; ``replace`` only if the cursor fell
        off the retention cap between syncs (resync that tf from scratch).
        """
        ref = self._cursor.get(tf)
        if ref is None:
            return "append", list(closed), (closed[-1] if closed else None)
        for i in range(len(closed) - 1, -1, -1):
            if closed[i] is ref:
                new = closed[i + 1:]
                return "append", new, (new[-1] if new else ref)
        return "replace", list(closed), (closed[-1] if closed else None)

    @staticmethod
    def _trim_mem(tfd: dict) -> None:
        """Trim an in-memory footprint dict to ``FOOTPRINT_MEM_CAP`` (oldest first)."""
        if len(tfd) <= config.FOOTPRINT_MEM_CAP:
            return
        for k in sorted(tfd.keys(), key=lambda x: int(x))[:-config.FOOTPRINT_MEM_CAP]:
            del tfd[k]

    def prepare(self, core) -> Tuple[dict, Dict[str, Optional[QuantBucket]]]:
        """Serialize a flush payload ON THE EVENT LOOP (race-free, no I/O).

        Returns ``(payload, new_cursors)``; the caller advances ``self._cursor``
        only after the executor write succeeds, so a failed write is retried.
        """
        engines = core.engines
        footprints_db = core.footprints_db
        now = int(time.time())
        payload: dict = {"per_tf": []}
        new_cursors: Dict[str, Optional[QuantBucket]] = {}

        for tf, engine in engines.items():
            mode, new_buckets, new_cursor = self._new_closed(tf, engine.closed_buckets)
            new_cursors[tf] = new_cursor

            # Step A.2: the order_blocks TABLE is WRITE-ONLY — never SELECTed in production (rehydrate
            # recomputes OBs fresh via catchup_start; the lone SELECT is a schema-guard test using its own
            # inserted row). So this O(obs×buckets) rescan ×5 ON the event loop every SYNC_INTERVAL_SECS=10
            # was pure waste — the residual live-price hitch (~0.8s, 3s+ in busy markets). Dropped.
            obs: list = []

            tfd = footprints_db.get(tf, {})
            self._trim_mem(tfd)                          # mutate in-memory dict on the loop
            fp_rows = [(tf, int(u), json.dumps(node)) for u, node in tfd.items()]

            payload["per_tf"].append({
                "tf": tf,
                "total_closed": engine.total_closed,   # persist the per-tf bucket-index counter (stable idx)
                "engine_state": (
                    tf, engine.target_vol, engine.avg_velocity, engine.vpin,
                    json.dumps(list(engine.rolling_velocity)),
                    json.dumps(list(engine.vpin_queue)),
                    json.dumps(_bucket_to_dict(engine.active_bucket)), now),
                "buckets_mode": mode,
                "bucket_rows": [
                    (tf, b.start_time, b.end_time, json.dumps(_bucket_to_dict(b)))
                    for b in new_buckets],
                "ob_rows": [(tf, ob.get("ob_id") or f"{tf}_{i}", json.dumps(ob))
                            for i, ob in enumerate(obs)],
                "fp_rows": fp_rows,
            })
        return payload, new_cursors

    def _write(self, payload: dict) -> bool:
        """Execute a prepared payload as one transaction (runs in the executor)."""
        with self._lock:
            try:
                cur = self._conn.cursor()
                cur.execute("BEGIN")
                for t in payload["per_tf"]:
                    tf = t["tf"]
                    cur.execute(                              # persist the per-tf bucket-index counter
                        "INSERT INTO meta(key,value) VALUES(?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (f"total_closed_{tf}", str(t["total_closed"])))
                    cur.execute(
                        "INSERT INTO engine_state(tf,target_vol,avg_velocity,vpin,"
                        "rolling_velocity,vpin_queue,active_bucket,updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(tf) DO UPDATE SET target_vol=excluded.target_vol,"
                        "avg_velocity=excluded.avg_velocity,vpin=excluded.vpin,"
                        "rolling_velocity=excluded.rolling_velocity,vpin_queue=excluded.vpin_queue,"
                        "active_bucket=excluded.active_bucket,updated_at=excluded.updated_at",
                        t["engine_state"])

                    if t["buckets_mode"] == "replace":
                        cur.execute("DELETE FROM closed_buckets WHERE tf=?", (tf,))
                    if t["bucket_rows"]:
                        cur.executemany(
                            "INSERT INTO closed_buckets(tf,start_time,end_time,data) "
                            "VALUES(?,?,?,?)", t["bucket_rows"])
                    cur.execute(
                        "DELETE FROM closed_buckets WHERE tf=? AND id NOT IN "
                        "(SELECT id FROM closed_buckets WHERE tf=? ORDER BY id DESC LIMIT ?)",
                        (tf, tf, config.CLOSED_BUCKETS_CAP))

                    cur.execute("DELETE FROM order_blocks WHERE tf=?", (tf,))
                    if t["ob_rows"]:
                        cur.executemany(
                            "INSERT OR REPLACE INTO order_blocks(tf,ob_id,data) VALUES(?,?,?)",
                            t["ob_rows"])

                    if t["fp_rows"]:
                        cur.executemany(
                            "INSERT INTO footprints(tf,utime,node) VALUES(?,?,?) "
                            "ON CONFLICT(tf,utime) DO UPDATE SET node=excluded.node",
                            t["fp_rows"])
                        cur.execute(
                            "DELETE FROM footprints WHERE tf=? AND utime NOT IN "
                            "(SELECT utime FROM footprints WHERE tf=? ORDER BY utime DESC LIMIT ?)",
                            (tf, tf, config.FOOTPRINT_CAP))
                cur.execute(
                    "INSERT INTO meta(key,value) VALUES('bucket_schema_version',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(BUCKET_SCHEMA_VERSION),))
                self._conn.commit()
                return True
            except Exception as e:
                try:
                    self._conn.rollback()
                except Exception:
                    pass
                print(f"HISTORY FLUSH ERROR: {e}")
                return False

    # -- background sync ----------------------------------------------------
    async def sync_loop(self, core) -> None:
        """Async upsert loop: serialize on the loop, write off the loop, forever."""
        loop = asyncio.get_event_loop()
        while True:
            await asyncio.sleep(config.SYNC_INTERVAL_SECS)
            try:
                payload, new_cursors = self.prepare(core)
            except Exception as e:
                print(f"HISTORY PREPARE ERROR: {e}")
                continue
            ok = await loop.run_in_executor(None, self._write, payload)
            if ok:
                self._cursor.update(new_cursors)

    def close(self, core=None) -> None:
        """Final synchronous flush (best-effort) + close the connection."""
        if core is not None:
            try:
                payload, new_cursors = self.prepare(core)
                if self._write(payload):
                    self._cursor.update(new_cursors)
            except Exception as e:
                print(f"HISTORY CLOSE FLUSH ERROR: {e}")
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
