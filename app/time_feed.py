"""Terminal-side CLOCK-CANDLE feed — the Time half of the Bucket<->Time chart toggle.

Unlike ``PipeClientWorker`` (a persistent push stream of volume buckets), clock candles are served on demand:
the daemon answers ``{"action":"get_time_candles","tf":tf}`` with chunked ``TimeCandlesPacket`` frames. This
background thread POLLS that request every ``POLL_SECS`` for the active tf and folds the reply into a snapshot that is
byte-shaped EXACTLY like ``PipeClientWorker.snapshot()`` (same keys) — so the terminal's render, footprint, stats and
overlays consume it with zero changes; only the source (clock vs volume) and the x-axis (time vs bucket index) differ.

Each served candle is normalized to the volume-bucket wire schema by ``time_candles.to_bucket_wire`` (OHLC/footprint
REAL; body colour = net taker flow; engine-only scalars honest-zeroed). The GUI thread only ever calls
:meth:`snapshot`, which copies the cache under a lock and releases it immediately — same contract as the bucket worker.
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


class TimeCandleFeed(threading.Thread):
    """Background poller. ``start()`` begins polling the active tf; ``snapshot()`` returns the latest shaped snapshot
    (thread-safe); ``set_tf()`` retargets + triggers an immediate refetch; ``stop()`` ends the thread. A Thread cannot
    be restarted, so the terminal creates a fresh instance each time Time mode is (re)entered."""

    def __init__(self, tf: str = config.DEFAULT_TF):
        super().__init__(daemon=True)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()          # set_tf / start -> refetch now instead of waiting out POLL_SECS
        self._tf = tf
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
            self._tf = tf
            self._snap = build_snapshot([], tf, connected=False)   # drop stale-tf candles immediately
        self._wake.set()

    @property
    def tf(self) -> str:
        with self._lock:
            return self._tf

    def stop(self) -> None:
        self._stop.set(); self._wake.set()

    # -- worker thread -------------------------------------------------
    def run(self) -> None:
        while not self._stop.is_set():
            tf = self.tf
            served = fetch_time_candles(tf)
            if not self._stop.is_set() and tf == self.tf:          # ignore a reply for a tf we just switched away from
                if served:                                         # KEEP the last good snapshot on an empty/timeout reply:
                    with self._lock:                               # a slow/overloaded daemon must never BLANK the chart
                        self._snap = build_snapshot(served, tf, connected=True)   # (that was the visible clock-candle gap)
                elif served is None:                               # connection lost -> flag disconnected, keep the candles
                    with self._lock:
                        self._snap = dict(self._snap, connected=False)
            self._wake.wait(POLL_SECS)
            self._wake.clear()
