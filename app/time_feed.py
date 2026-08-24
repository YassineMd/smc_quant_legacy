"""Terminal-side CLOCK-CANDLE feed — the Time half of the Bucket<->Time chart toggle.

PUSH stream (real-time parity with volume buckets): ``TimeCandleFeed`` keeps ONE socket open, ``sub_time``-subscribes
to the active tf, receives the one-time full catch-up + then the daemon's live-edge PUSHES (the forming clock candle
every ~150ms + any new close), merges them by start_time, gap-fills locally, and shapes a snapshot byte-identical to
``PipeClientWorker.snapshot()`` — so the terminal's render/footprint/stats/overlays consume it unchanged; only the
source (clock vs volume) and x-axis (time vs bucket index) differ. This replaced the old poll model whose
request/response round-trip lagged the clock candles seconds behind the pushed buckets.

``fetch_time_candles`` (one-shot poll of ``get_time_candles``) is kept for tooling/diagnostics. Each served candle is
normalized to the volume-bucket wire schema by ``time_candles.to_bucket_wire`` (OHLC/footprint REAL; body colour = net
taker flow; engine-only scalars honest-zeroed). The GUI thread only ever calls :meth:`snapshot` (copies under a lock).
"""
from __future__ import annotations

import socket
import threading
import time
from typing import List, Optional

import numpy as np

from . import config, protocol
from .time_candles import to_bucket_wire

POLL_SECS = 1.5              # re-request cadence. 0.5 x 4 windows overloaded the (uncached) daemon to ~5s responses; the
#                              20Hz price fold gives smoothness between polls, so the poll only refreshes candle STRUCTURE.
_RECV_WINDOW = 9.0           # HARD cap on collecting one chunked reply (safety only; idle-detection ends it far sooner)
_FIRST_BYTE_TIMEOUT = 6.0    # wait up to this for the daemon's FIRST frame. Under multi-window load the daemon can take
#                              ~5s to build the candle set; a short timeout here made polls FAIL -> blank/gappy chart.
_IDLE_TIMEOUT = 0.30         # once frames are flowing, THIS much silence = reply complete -> return at once (the daemon
#                              never closes the socket or sends an end-marker, so idle-gap is how we know it's done)
_CONNECT_TIMEOUT = 3.0


def fetch_time_candles(tf: str, host: Optional[str] = None, port: Optional[int] = None,
                       window: float = _RECV_WINDOW) -> Optional[List[dict]]:
    """One request/response: return the daemon's gap-filled clock candles for ``tf`` (served open_price/close_price
    shape), or None on any socket error (so the caller can distinguish "no connection" from "connected, empty").

    The daemon streams the reply as chunked TimeCandlesPacket frames but does NOT close the socket or send an
    end-marker. So we wait generously for the FIRST frame, then switch to a SHORT idle timeout: the first `recv` that
    returns nothing for `_IDLE_TIMEOUT` means the (complete) reply has stopped arriving -> return immediately instead
    of burning the whole window. This cut per-poll fetch time from ~2.0s to ~0.3s (the old flat-window wait was the
    bulk of the clock-vs-bucket lag)."""
    host = host or config.IPC_HOST
    port = port or config.IPC_PORT
    try:
        s = socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT)
    except OSError:
        return None
    try:
        s.sendall((protocol.json.dumps({"action": "get_time_candles", "tf": tf}) + "\n").encode())
        buf = b""; out: List[dict] = []; deadline = time.monotonic() + window
        s.settimeout(_FIRST_BYTE_TIMEOUT)                 # generous wait for the daemon's first frame...
        while time.monotonic() < deadline:
            try:
                chunk = s.recv(65536)
            except socket.timeout:
                break                                     # idle gap -> the (complete) reply has stopped arriving
            if not chunk:
                break                                     # daemon closed the socket -> done
            s.settimeout(_IDLE_TIMEOUT)                   # ...then a SHORT idle timeout so end-of-reply is caught fast
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                pkt = protocol.parse_line(line.decode("utf-8", "ignore"))
                if isinstance(pkt, protocol.TimeCandlesPacket) and pkt.tf == tf:
                    out.extend(pkt.candles)
        return out
    except OSError:
        return None
    finally:
        try:
            s.close()
        except OSError:
            pass


def build_snapshot(served: List[dict], tf: str, connected: bool) -> dict:
    """Shape served clock candles into a PipeClientWorker.snapshot()-identical dict. The LAST served candle is the
    live forming interval (its footprint keeps growing between polls); the rest are closed."""
    wire = [to_bucket_wire(c) for c in (served or [])]
    if wire:
        closed = wire[:-1]
        active = dict(wire[-1])
    else:
        closed = []
        active = {}
    last_px = float(active.get("close", closed[-1].get("close", 0.0) if closed else 0.0)) if (active or closed) else 0.0
    if wire:
        times = np.fromiter((w["start_time"] for w in wire), dtype=np.float64, count=len(wire))
        ohlcv = np.array([[w["open"], w["high"], w["low"], w["close"], w["curr_vol"]] for w in wire], dtype=np.float64)
    else:
        times = np.empty(0, dtype=np.float64); ohlcv = np.empty((0, 5), dtype=np.float64)
    return {
        "tf": tf,
        "times": times,
        "ohlcv": ohlcv,
        "footprints": {},                      # live forming-candle side-pane source; per-bucket footprints ride `levels`
        "latest_price": last_px,
        "forming_time": (int(active["start_time"]) if active else None),
        "order_blocks": [], "absorptions": [], "liquidations": [], "liq_sweeps": [],   # engine-only -> empty in time mode
        "depth": {"bids": [], "asks": []},
        "oi": 0.0, "size_thr": [], "vpin": 0.0, "target_vol": 0.0,
        "closed_buckets": closed,
        "total_closed": len(closed),
        "active_bucket": active,
        "connected": bool(connected),
        "catchup_loading": False,
    }


_RECV_TICK = 0.5            # recv timeout -> loop to check stop / tf-change + periodic rebuild (forming countdown)
_CANDLE_CAP = 1000         # keep the most-recent N clock candles in the merge store
_STALE_RECONNECT = 15.0    # no frame for this long while "connected" -> assume a stalled/half-open stream and force a
#                            reconnect (fresh sub_time = full catch-up, which HEALS dropped/missing candles). ⚠ was 4.0,
#                            which sat exactly AT the daemon's stalled ~4.5s burst cadence (pre-_KLN fix) -> reconnect
#                            churn. 15s only fires on a genuinely dead stream; the live price rides the worker fold.
_RESYNC_SECS = 60.0        # belt-and-braces: even while frames flow, one-shot re-fetch + merge every this often — a
#                            close frame silently dropped by the daemon's bounded queue is otherwise NEVER re-sent
#                            (global advance-only cursor), leaving a stale partial candle (open != prev close) or a
#                            missing candle until the next reconnect. (User bug report 2026-08-24.)


def _snapshot_from_cands(cands: dict, tf: str, connected: bool) -> dict:
    """Gap-fill the merged (real, non-empty) clock candles locally + shape into a PipeClientWorker.snapshot()-identical
    dict. The client owns the gap-fill so the tiny live-edge PUSH frames need only carry real candles."""
    if not cands:
        return build_snapshot([], tf, connected)
    from .time_candles import gapfill_wire
    ordered = [cands[k] for k in sorted(cands)]
    wire = [to_bucket_wire(c) for c in ordered]
    filled = gapfill_wire(wire, config.TF_SECONDS.get(tf, 60))
    return build_snapshot(filled, tf, connected)


class TimeCandleFeed(threading.Thread):
    """Persistent CLOCK-candle PUSH stream (real-time parity with volume buckets). It ``sub_time``-subscribes on a kept-
    open socket, receives the one-time full set + then the daemon's live-edge PUSHES (forming candle every ~150ms + any
    new close), merges them by start_time, and rebuilds a PipeClientWorker.snapshot()-identical dict. No polling ->
    no request/response lag. ``snapshot()`` is thread-safe; ``set_tf()`` retargets (loop reconnects with the new
    sub_time); ``stop()`` ends the thread. A Thread can't restart, so the terminal makes a fresh one per Time entry."""

    def __init__(self, tf: str = config.DEFAULT_TF):
        super().__init__(daemon=True)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._tf = tf
        self._cands: dict = {}                  # {start_time_int: raw served candle} — merged catch-up + live pushes
        self._connected = False
        self._snap = build_snapshot([], tf, connected=False)

    # -- GUI thread ----------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            return self._snap

    def set_tf(self, tf: str) -> None:
        if tf not in config.TF_SECONDS:
            return
        with self._lock:
            if tf == self._tf:
                return
            self._tf = tf                       # the stream loop sees tf changed -> drops this sub, reconnects + re-subs
            self._cands = {}
            self._snap = build_snapshot([], tf, connected=False)

    @property
    def tf(self) -> str:
        with self._lock:
            return self._tf

    def stop(self) -> None:
        self._stop.set()

    # -- worker thread -------------------------------------------------
    def _merge(self, candles) -> None:
        with self._lock:
            for c in candles:
                if c.get("empty"):              # gap-fill flats are regenerated locally on rebuild -> don't store
                    continue
                try:
                    stk = int(c.get("start_time", 0) or 0)
                except (TypeError, ValueError):
                    continue
                if stk:
                    self._cands[stk] = c        # a close overwrites the same start_time's forming entry -> finalizes it
            if len(self._cands) > _CANDLE_CAP:
                for k in sorted(self._cands)[:len(self._cands) - _CANDLE_CAP]:
                    del self._cands[k]

    def _rebuild(self) -> None:
        with self._lock:
            cands = dict(self._cands); tf = self._tf; connected = self._connected
        snap = _snapshot_from_cands(cands, tf, connected)
        with self._lock:
            if tf == self._tf:                  # don't clobber a snapshot for a tf we already switched away from
                self._snap = snap

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self._stream_once()
            except Exception:
                pass
            with self._lock:
                self._connected = False
            if not self._stop.is_set():
                time.sleep(0.5)                 # brief backoff before reconnect

    def _stream_once(self) -> None:
        tf = self.tf
        try:
            s = socket.create_connection((config.IPC_HOST, config.IPC_PORT), timeout=_CONNECT_TIMEOUT)
        except OSError:
            return
        try:
            s.sendall((protocol.json.dumps({"action": "sub_time", "tf": tf}) + "\n").encode())
            s.settimeout(_RECV_TICK)
            with self._lock:
                self._connected = True
            buf = b""
            last_frame = time.monotonic(); next_resync = time.monotonic() + _RESYNC_SECS
            while not self._stop.is_set() and tf == self.tf:
                now_m = time.monotonic()
                if now_m - last_frame > _STALE_RECONNECT:
                    break                       # stalled/half-open stream -> reconnect; the fresh catch-up heals gaps
                if now_m >= next_resync:        # periodic heal for silently-dropped close frames (never re-pushed)
                    next_resync = now_m + _RESYNC_SECS
                    try:
                        served = fetch_time_candles(tf)
                        if served:
                            self._merge(served); self._rebuild()
                    except Exception:
                        pass
                try:
                    chunk = s.recv(65536)
                except socket.timeout:
                    self._rebuild()             # no frame this tick -> still refresh (forming countdown / connected)
                    continue
                if not chunk:
                    break                       # daemon closed the socket -> reconnect
                buf += chunk
                got = False
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    try:
                        pkt = protocol.parse_line(line.decode("utf-8", "ignore"))
                    except Exception:
                        continue
                    if isinstance(pkt, protocol.TimeCandlesPacket) and pkt.tf == tf:
                        self._merge(pkt.candles); got = True
                if got:
                    last_frame = time.monotonic()
                    self._rebuild()
        finally:
            try:
                s.close()
            except OSError:
                pass
