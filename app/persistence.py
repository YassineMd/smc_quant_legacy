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
from .quant_engine import QuantBucket, QuantEngine, calc_quant_obs, rank_obs

_INF = float("inf")
_NEG_INF = float("-inf")


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
    return b


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

            obs = rank_obs(calc_quant_obs(engine, tf))   # pure read of engine state

            tfd = footprints_db.get(tf, {})
            self._trim_mem(tfd)                          # mutate in-memory dict on the loop
            fp_rows = [(tf, int(u), json.dumps(node)) for u, node in tfd.items()]

            payload["per_tf"].append({
                "tf": tf,
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
