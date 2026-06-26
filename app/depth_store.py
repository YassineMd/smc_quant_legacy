"""Phase 1 — Bookmap-style depth + trade capture store (daemon-only, SEPARATE depth.db).

A bounded, ephemeral 6h rolling window of:
  * ``depth_snapshots`` — full-book anchors every ``DEPTH_SNAPSHOT_SECS`` (+ one per diff-stream
    reconnect), so the delta chain always has a preceding anchor.
  * ``depth_deltas``    — the LOSSLESS per-diff level changes between anchors (nothing sampled away).
  * ``trade_tape``      — every aggTrade (for real per-trade bubbles).

Fully decoupled from the durable bucket history (``persistence.HistoryStore`` / ``history.db``): own
file, own WAL connection + lock, own sync loop, own transactions, own time-based prune. It can be deleted
without touching bucket state, and its writes never share a transaction with the bucket/close path.

Threading mirrors ``HistoryStore``: :meth:`prepare` DRAINS the capture buffers ON THE EVENT LOOP (race-
free), :meth:`_write` packs + inserts + prunes in a thread executor (disk I/O never blocks the loop).

Encoding (compact, real fidelity within the captured band): each level = ``(int32 price-in-ticks,
float32 qty)``; bids/asks are length-prefixed sub-arrays (side implicit). qty==0 = level removed (kept —
a removal is a real change). Binance diff depth carries ABSOLUTE quantities per level, so applying a diff
is idempotent and reconstruction is exact.
"""

from __future__ import annotations

import asyncio
import sqlite3
import struct
import threading
import time
from typing import Dict, List, Optional, Tuple

from . import config

_TICK = config.TICK_SIZE

_SCHEMA = """
CREATE TABLE IF NOT EXISTS depth_snapshots (
    ts_ms INTEGER PRIMARY KEY,   -- anchor wall-clock (ms)
    u     INTEGER,               -- last applied diff update-id at anchor time
    mid   REAL,                  -- mid price (reference)
    bids  BLOB,                  -- packed (int32 tick, float32 qty)[]
    asks  BLOB
);
CREATE INDEX IF NOT EXISTS idx_depth_snap_u ON depth_snapshots(u);
CREATE TABLE IF NOT EXISTS depth_deltas (
    u       INTEGER PRIMARY KEY, -- Binance final update-id (monotonic = order + gap detect)
    ts_ms   INTEGER NOT NULL,    -- diff event time (ms)
    changes BLOB NOT NULL        -- packed bid sub-array | ask sub-array
);
CREATE INDEX IF NOT EXISTS idx_depth_deltas_ts ON depth_deltas(ts_ms);
CREATE TABLE IF NOT EXISTS trade_tape (
    a     INTEGER PRIMARY KEY,   -- aggTrade id (unique, monotonic)
    ts_ms INTEGER NOT NULL,
    price REAL NOT NULL,
    qty   REAL NOT NULL,
    side  INTEGER NOT NULL        -- 1 = taker buy, 0 = taker sell
);
CREATE INDEX IF NOT EXISTS idx_trade_tape_ts ON trade_tape(ts_ms);
"""


# ---------------------------------------------------------------------------
# Packing  (int32 price-in-ticks, float32 qty); side implicit via sub-arrays
# ---------------------------------------------------------------------------
def _pack_levels(d: Dict[float, float]) -> bytes:
    """Pack a {price: qty} dict as ``<I count`` then count×``<i f``."""
    flat: list = []
    for p, q in d.items():
        flat.append(int(round(p / _TICK))); flat.append(q)
    return struct.pack("<I" + "if" * len(d), len(d), *flat)


def _unpack_levels(blob: bytes) -> Dict[int, float]:
    """-> {tick: qty} (tick-keyed for exact compares; ×TICK_SIZE at the boundary)."""
    (n,) = struct.unpack_from("<I", blob, 0)
    out: Dict[int, float] = {}
    off = 4
    for _ in range(n):
        t, q = struct.unpack_from("<if", blob, off); off += 8
        out[t] = q
    return out


def _pack_changes(bid_changes: list, ask_changes: list) -> bytes:
    """Two length-prefixed sub-arrays: [n_bid][bid (tick,qty)…][n_ask][ask (tick,qty)…]."""
    out = bytearray()
    for arr in (bid_changes, ask_changes):
        flat: list = []
        for p, q in arr:
            flat.append(int(round(p / _TICK))); flat.append(q)
        out += struct.pack("<I" + "if" * len(arr), len(arr), *flat)
    return bytes(out)


def _unpack_changes(blob: bytes) -> Tuple[list, list]:
    """-> (bid_changes, ask_changes), each a list of (tick, qty)."""
    res = []
    off = 0
    for _ in range(2):
        (n,) = struct.unpack_from("<I", blob, off); off += 4
        arr = []
        for _ in range(n):
            t, q = struct.unpack_from("<if", blob, off); off += 8
            arr.append((t, q))
        res.append(arr)
    return res[0], res[1]


class DepthStore:
    """Owns depth.db and all of its I/O (WAL, single connection, own lock)."""

    def __init__(self, db_path: Optional[str] = None):
        config.ensure_data_dir()
        self.db_path = db_path or config.DEPTH_DB
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # -- per-flush: drain on the loop, write in the executor ----------------
    def prepare(self, core) -> Tuple[list, list, list]:
        """DRAIN the capture buffers ON THE EVENT LOOP (race-free, no I/O)."""
        deltas = list(core._depth_delta_buf); core._depth_delta_buf.clear()
        snaps = list(core._depth_snap_buf); core._depth_snap_buf.clear()
        trades = list(core._trade_buf); core._trade_buf.clear()
        return deltas, snaps, trades

    def _write(self, payload: Tuple[list, list, list]) -> bool:
        """Pack + insert + 6h prune as ONE transaction (runs in the executor)."""
        deltas, snaps, trades = payload
        with self._lock:
            try:
                cur = self._conn.cursor()
                cur.execute("BEGIN")
                if snaps:
                    cur.executemany(
                        "INSERT OR REPLACE INTO depth_snapshots(ts_ms,u,mid,bids,asks) VALUES(?,?,?,?,?)",
                        [(ts, u, mid, _pack_levels(b), _pack_levels(a)) for (ts, u, mid, b, a) in snaps])
                if deltas:
                    cur.executemany(
                        "INSERT OR REPLACE INTO depth_deltas(u,ts_ms,changes) VALUES(?,?,?)",
                        [(u, ts, _pack_changes(bc, ac)) for (ts, u, bc, ac) in deltas])
                if trades:
                    cur.executemany(
                        "INSERT OR REPLACE INTO trade_tape(a,ts_ms,price,qty,side) VALUES(?,?,?,?,?)", trades)
                # HARD 6h time prune. Keep the ONE anchor at/just-before the cutoff so the oldest
                # retained delta still has a preceding snapshot (NULL MAX -> deletes nothing, safe).
                cutoff = int(time.time() * 1000) - int(config.DEPTH_RETENTION_HOURS * 3600 * 1000)
                cur.execute("DELETE FROM depth_deltas WHERE ts_ms < ?", (cutoff,))
                cur.execute("DELETE FROM trade_tape WHERE ts_ms < ?", (cutoff,))
                cur.execute(
                    "DELETE FROM depth_snapshots WHERE ts_ms < "
                    "(SELECT MAX(ts_ms) FROM depth_snapshots WHERE ts_ms <= ?)", (cutoff,))
                self._conn.commit()
                return True
            except Exception as e:
                try:
                    self._conn.rollback()
                except Exception:
                    pass
                print(f"DEPTH FLUSH ERROR: {e}")
                return False

    async def sync_loop(self, core) -> None:
        """Drain on the loop, write off the loop, forever — its OWN loop, own depth.db transaction."""
        loop = asyncio.get_event_loop()
        while True:
            await asyncio.sleep(config.DEPTH_SYNC_SECS)
            if not config.DEPTH_CAPTURE_ENABLED:
                continue
            try:
                payload = self.prepare(core)
            except Exception as e:
                print(f"DEPTH PREPARE ERROR: {e}")
                continue
            await loop.run_in_executor(None, self._write, payload)

    # -- reconstruction (Phase 2 + the lossless proof) ----------------------
    def reconstruct_at_u(self, u_max: int) -> "Optional[Tuple[Dict[int,float], Dict[int,float]]]":
        """Rebuild the book as of update-id ``u_max``: nearest snapshot with u<=u_max, then replay every
        delta with snap.u < u <= u_max. Returns tick-keyed ({tick:qty} bids, asks) or None if no anchor."""
        with self._lock:
            snap = self._conn.execute(
                "SELECT u,bids,asks FROM depth_snapshots WHERE u<=? ORDER BY u DESC LIMIT 1",
                (u_max,)).fetchone()
            if not snap:
                return None
            snap_u, bblob, ablob = snap
            rows = self._conn.execute(
                "SELECT changes FROM depth_deltas WHERE u>? AND u<=? ORDER BY u",
                (snap_u, u_max)).fetchall()
        bids = _unpack_levels(bblob); asks = _unpack_levels(ablob)
        for (blob,) in rows:
            bc, ac = _unpack_changes(blob)
            for t, q in bc:
                bids.pop(t, None) if q == 0.0 else bids.__setitem__(t, q)
            for t, q in ac:
                asks.pop(t, None) if q == 0.0 else asks.__setitem__(t, q)
        return bids, asks

    def stats(self) -> dict:
        """Row counts + time spans (for the validation report)."""
        with self._lock:
            c = self._conn
            dn, dmin, dmax = c.execute("SELECT COUNT(*),MIN(ts_ms),MAX(ts_ms) FROM depth_deltas").fetchone()
            sn, smin, smax = c.execute("SELECT COUNT(*),MIN(ts_ms),MAX(ts_ms) FROM depth_snapshots").fetchone()
            tn, tmin, tmax = c.execute("SELECT COUNT(*),MIN(ts_ms),MAX(ts_ms) FROM trade_tape").fetchone()
        return {"deltas": (dn, dmin, dmax), "snapshots": (sn, smin, smax), "trades": (tn, tmin, tmax)}

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
