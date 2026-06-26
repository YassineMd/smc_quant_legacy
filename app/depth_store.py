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

import array
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
    vals = struct.unpack_from("<" + "if" * n, blob, 4)   # one call (interleaved tick,qty)
    return dict(zip(vals[0::2], vals[1::2]))


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
        vals = struct.unpack_from("<" + "if" * n, blob, off); off += 8 * n   # one call per side
        res.append(list(zip(vals[0::2], vals[1::2])))
    return res[0], res[1]


def bin_live_book(bids: Dict[float, float], asks: Dict[float, float],
                  ylo: float, yhi: float, ybins: int):
    """Phase 2a live edge: bin a PRICE-keyed book ({price: qty}, e.g. pulse_state['local_ob']) into ``ybins``
    over [ylo, yhi]. Returns ``(col_bytes, best_bid, best_ask)`` — col_bytes = ybins little-endian float32 of
    RAW resting size per bin. No depth.db; pure read of the in-RAM book."""
    bins = [0.0] * ybins
    span = (yhi - ylo) or 1.0
    for d in (bids, asks):
        for price, q in d.items():
            if ylo <= price < yhi and q > 0.0:
                b = int((price - ylo) / span * ybins)
                bins[b if b < ybins else ybins - 1] += q
    bb = max((p for p, q in bids.items() if q > 0.0), default=0.0)
    ba = min((p for p, q in asks.items() if q > 0.0), default=0.0)
    return struct.pack("<%df" % ybins, *bins), bb, ba


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

    # -- Phase 3: executed-trade bubbles window (read-only, off-loop) -------
    def trades_window(self, t0: int, t1: int, ylo: float, yhi: float):
        """RAW executed trades in [t0,t1]×[ylo,yhi], LOSSLESS (no sampling). Returns
        ``(n, ts_blob int64 LE ms, price_blob f32 LE, qty_blob f32 LE, side_blob u8)`` ready for base64.
        Its OWN ``mode=ro`` connection + the ts_ms index → runs in an executor with zero lock contention vs
        the live writer; never blocks the event loop (mirrors build_window)."""
        c = sqlite3.connect("file:%s?mode=ro" % self.db_path, uri=True)
        try:
            rows = c.execute(
                "SELECT ts_ms, price, qty, side FROM trade_tape "
                "WHERE ts_ms>=? AND ts_ms<=? AND price>=? AND price<=? ORDER BY ts_ms",
                (int(t0), int(t1), float(ylo), float(yhi))).fetchall()
        finally:
            c.close()
        n = len(rows)
        if n == 0:
            return 0, b"", b"", b"", b""
        ts = struct.pack("<%dq" % n, *(int(r[0]) for r in rows))
        pr = struct.pack("<%dd" % n, *(float(r[1]) for r in rows))   # float64 = bit-identical to trade_tape
        qt = struct.pack("<%dd" % n, *(float(r[2]) for r in rows))
        sd = struct.pack("<%dB" % n, *(1 if r[3] else 0 for r in rows))
        return n, ts, pr, qt, sd

    # -- Phase 2a: heatmap window builder (read-only, off-loop) -------------
    def build_window(self, t0: int, t1: int, cols: int, ylo: float, yhi: float, ybins: int):
        """Build a Bookmap heatmap window in ONE forward pass — O(deltas in [t0,t1]), not W reconstructs.

        Anchor the book at t0 (nearest snapshot + replay), seed the in-band bins, then walk the deltas
        (t0,t1] once, updating the running book AND the affected price-bin incrementally; snapshot the
        binned column at each pixel-time boundary. Returns ``(grid_bytes, bbo_bid, bbo_ask)`` where
        grid_bytes is ``cols*ybins`` little-endian float32 row-major [col][bin] of RAW resting size.

        READ-ONLY: opens its OWN ``mode=ro`` connection, so it runs in an executor with zero lock
        contention against the live writer (capture / sync). Touches nothing but depth.db reads."""
        c = sqlite3.connect("file:%s?mode=ro" % self.db_path, uri=True)
        try:
            tlo = int(round(ylo / _TICK)); thi = int(round(yhi / _TICK)); span = max(1, thi - tlo)
            def binof(tk):
                if tk < tlo or tk >= thi:
                    return -1
                b = (tk - tlo) * ybins // span
                return b if b < ybins else ybins - 1
            # Anchor: latest snapshot at/before t0 (fallback: earliest snapshot). ONE consistent forward pass
            # from the anchor, ordered by (ts_ms, u): the event time is NOT strictly monotonic in u, so gating
            # purely by u would mis-bucket a delated-ts delta — (ts_ms, u) makes the per-column ts gate monotonic
            # (no skip) and ties break by sequence id. The (sts, t0] deltas are applied inside the first column's
            # while-loop (col_end_0 = t0+step >= t0), so there is no separate anchor-replay seam.
            snap = c.execute("SELECT ts_ms,bids,asks FROM depth_snapshots WHERE ts_ms<=? "
                             "ORDER BY ts_ms DESC LIMIT 1", (t0,)).fetchone()
            if snap is None:
                snap = c.execute("SELECT ts_ms,bids,asks FROM depth_snapshots "
                                 "ORDER BY ts_ms ASC LIMIT 1").fetchone()
            if snap is None:
                return b"", [0.0] * cols, [0.0] * cols   # no data captured yet
            sts, bb, ab = snap
            bids = _unpack_levels(bb); asks = _unpack_levels(ab)
            bins = [0.0] * ybins
            for tk, q in bids.items():
                b = binof(tk)
                if b >= 0: bins[b] += q
            for tk, q in asks.items():
                b = binof(tk)
                if b >= 0: bins[b] += q
            # HYBRID merge-walk: snapshots (ground truth) + deltas (fill between), in ts order. At each
            # intermediate snapshot RE-ANCHOR the book — this corrects any delta-chain drift (a Binance
            # @depth gap with no gap-recovery), which is precisely what the periodic snapshots are for.
            # Walking deltas alone from one anchor would accumulate that drift across the window.
            snaps = c.execute("SELECT ts_ms, bids, asks FROM depth_snapshots WHERE ts_ms>? AND ts_ms<=? "
                              "ORDER BY ts_ms", (sts, t1)).fetchall()
            deltas = c.execute("SELECT ts_ms, changes FROM depth_deltas WHERE ts_ms>? AND ts_ms<=? "
                               "ORDER BY ts_ms, u", (sts, t1)).fetchall()
            step = (t1 - t0) / cols if cols > 0 else (t1 - t0)
            grid = array.array("f"); bbo_bid = []; bbo_ask = []   # 'f' = little-endian float32 on x86
            si, di, ns, nd = 0, 0, len(snaps), len(deltas)
            _INF = float("inf")

            ybm1 = ybins - 1
            zeros = [0.0] * ybins

            def _reseed():
                bins[:] = zeros                          # reset
                for _tk, _q in bids.items():
                    if tlo <= _tk < thi:
                        _bn = (_tk - tlo) * ybins // span
                        bins[_bn if _bn < ybins else ybm1] += _q
                for _tk, _q in asks.items():
                    if tlo <= _tk < thi:
                        _bn = (_tk - tlo) * ybins // span
                        bins[_bn if _bn < ybins else ybm1] += _q

            for ci in range(cols):
                col_end = t1 if ci == cols - 1 else t0 + (ci + 1) * step   # exact final boundary
                while True:
                    s_ts = snaps[si][0] if si < ns else _INF
                    d_ts = deltas[di][0] if di < nd else _INF
                    if s_ts > col_end and d_ts > col_end:
                        break
                    if s_ts <= d_ts:                       # RE-ANCHOR to the snapshot (corrects drift)
                        bids = _unpack_levels(snaps[si][1]); asks = _unpack_levels(snaps[si][2])
                        _reseed(); si += 1
                    else:                                  # apply one diff incrementally (binof inlined — hot)
                        bc, ac = _unpack_changes(deltas[di][1])
                        for tk, q in bc:
                            if tlo <= tk < thi:
                                _bn = (tk - tlo) * ybins // span
                                bins[_bn if _bn < ybins else ybm1] += q - bids.get(tk, 0.0)
                            bids.pop(tk, None) if q == 0.0 else bids.__setitem__(tk, q)
                        for tk, q in ac:
                            if tlo <= tk < thi:
                                _bn = (tk - tlo) * ybins // span
                                bins[_bn if _bn < ybins else ybm1] += q - asks.get(tk, 0.0)
                            asks.pop(tk, None) if q == 0.0 else asks.__setitem__(tk, q)
                        di += 1
                grid.extend(bins)
                bbo_bid.append((max(bids) if bids else 0) * _TICK)
                bbo_ask.append((min(asks) if asks else 0) * _TICK)
        finally:
            c.close()
        return grid.tobytes(), bbo_bid, bbo_ask

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
