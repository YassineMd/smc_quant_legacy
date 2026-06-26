"""Tier-3 terminal-only socket client.

``PipeClientWorker`` is the background ``threading.Thread`` that owns the TCP
connection to the daemon (spec §1.4.2). It parses newline-framed packets and
folds them into a thread-safe local cache under an explicit ``threading.Lock``.
The GUI thread never touches the socket — it only calls :meth:`snapshot`, which
copies the cache under the lock and releases it immediately (spec §1.4.2,
§9.2.3).

Performance note (Section 11 autonomy)
--------------------------------------
The spec sketches a ``pandas.DataFrame`` as the candle cache. We instead keep an
``OrderedDict`` keyed by candle open-time for O(1) tick upserts, and materialize
contiguous ``numpy`` arrays only inside :meth:`snapshot`. This eliminates the
per-tick ``DataFrame`` append/concat cost while preserving the lock semantics
and the ``.copy()`` hand-off the spec requires. Building arrays for ≤10k candles
at 20Hz is trivial.

Timezone (spec §2): the X coordinate is the raw Unix-second epoch. Localization
to the host OS timezone happens at render time in the axis formatter
(:mod:`app.chart_widgets`), so the data is never shifted — cleaner and
drop-free under high-frequency updates.
"""

from __future__ import annotations

import socket
import threading
import time
from collections import OrderedDict, deque
from typing import Dict, List, Optional

import numpy as np
import requests

from . import config, protocol


def fetch_baseline_candles(tf: str) -> "OrderedDict[int, list]":
    """Synchronous REST pull of the last N closed candles (spec §9.1.3).

    Returns an OrderedDict {open_time_seconds: [o, h, l, c, v]} sorted ascending.
    Binance returns kline open-times in milliseconds; we store seconds.
    """
    out: "OrderedDict[int, list]" = OrderedDict()
    try:
        res = requests.get(
            config.REST_KLINES,
            params={"symbol": config.SYMBOL, "interval": tf, "limit": config.BASELINE_CANDLES},
            timeout=5,
        )
        res.raise_for_status()
        for k in res.json():
            t = int(k[0]) // 1000
            out[t] = [float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])]
    except Exception:
        pass
    return out


class PipeClientWorker(threading.Thread):
    def __init__(self, tf: str = config.DEFAULT_TF):
        super().__init__(daemon=True)
        self.data_lock = threading.Lock()       # master thread synchronization lock
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self._outgoing: deque[str] = deque()

        # --- cache (guarded by data_lock) ---
        self.tf = tf
        self.candles: "OrderedDict[int, list]" = OrderedDict()  # open_time_s -> [o,h,l,c,v]
        self.footprints: "OrderedDict[str, dict]" = OrderedDict()  # utime_str -> node
        self.latest_price: float = 0.0
        self.forming_time: Optional[int] = None  # open-time of the live forming candle
        self.order_blocks: List[dict] = []
        self.absorptions: List[dict] = []        # whale-absorption marks (new wire field; liquidations-style plain copy, no OB COW)
        self.liquidations: List[dict] = []       # {side, price, qty, time}
        self.depth: Dict[str, list] = {"bids": [], "asks": []}
        self.oi: float = 0.0
        self.vpin: float = 0.0
        self.target_vol: float = config.DEFAULT_TARGET_VOL
        # Phase 0 bucket pipeline: full closed-bucket history (seeded by CATCHUP,
        # grown by ObPacket.new_buckets) + the live pulsing active bucket. Both use
        # the protocol.BucketSnapshot schema.
        self.closed_buckets: List[dict] = []
        self.active_bucket: dict = {}
        self.connected: bool = False
        self._catchup_loading: bool = False   # True while a chunked catch-up streams
        self._force_reconnect = threading.Event()   # manual refresh: drop the socket + reconnect now
        self._sock: Optional[socket.socket] = None  # active socket, exposed for refresh()'s force-drop
        # copy-on-write export caches: bump the version on every write to the heavy,
        # infrequently-changing lists; snapshot() re-copies only when it moved, so a
        # 10k-bucket history costs one copy per change, not one O(N) copy per frame.
        self._cb_ver = 0
        self._cb_exp: List[dict] = []
        self._cb_exp_ver = -1
        self._ob_ver = 0
        self._ob_exp: List[dict] = []
        self._ob_exp_ver = -1
        # Phase 2b heatmap DELIVERY buffer (guarded by data_lock). The ~MB depth grid lives HERE and is
        # handed to the heatmap mode ONLY via depth_heatmap_state() — it NEVER rides the 20Hz snapshot(),
        # so it costs nothing when the heatmap mode isn't open (the isolation guarantee).
        self._hm_window = None                 # last DepthWindowPacket (consumed once by the heatmap mode)
        self._hm_cols: deque = deque(maxlen=512)   # pending live DepthColumnPackets (drained each access)
        self._hm_ver = 0                       # bumps on any heatmap frame -> lets the mode self-gate
        # Phase 3 bubbles delivery buffer (guarded by data_lock) — same isolation: the trade arrays live HERE,
        # handed to the heatmap mode only via trades_state(), NEVER on the 20Hz snapshot().
        self._tb_window = None                 # last TradesWindowPacket (consumed once)
        self._tb_batches: deque = deque(maxlen=512)   # pending live TradeBatchPackets (drained each access)
        self._tb_ver = 0

    # ------------------------------------------------------------------
    # Boot baseline (called from GUI thread before the window shows)
    # ------------------------------------------------------------------
    def load_baseline(self, tf: str) -> None:
        baseline = fetch_baseline_candles(tf)
        with self.data_lock:
            self.tf = tf
            self.candles = baseline
            if baseline:
                last_t = next(reversed(baseline))
                self.latest_price = baseline[last_t][3]  # close
                self.forming_time = last_t

    # ------------------------------------------------------------------
    # Outbound control
    # ------------------------------------------------------------------
    def request_timeframe(self, tf: str) -> None:
        """Queue a set_tf control frame and reset the local candle cache."""
        with self.data_lock:
            self.tf = tf
            self.candles = OrderedDict()
            self.footprints = OrderedDict()
            self.order_blocks = []
            self.absorptions = []
            self.liquidations = []
            self.closed_buckets = []
            self.active_bucket = {}
            self.target_vol = 0.0   # stale until the new tf's catch-up arrives (scale labels skip till then)
            self._cb_ver += 1   # invalidate COW caches on the tf-switch clear
            self._ob_ver += 1
        with self._send_lock:
            self._outgoing.append(protocol.json.dumps({"action": "set_tf", "tf": tf}) + "\n")
        # Repopulate baseline synchronously so the chart never blanks for long
        self.load_baseline(tf)

    def request_depth_window(self, t0: int, t1: int, cols: int,
                             ylo: float, yhi: float, ybins: int) -> None:
        """Queue a depth_window request (heatmap mode-select / pan / zoom). Also subscribes this client to
        the daemon's live-column pushes for the given band."""
        with self._send_lock:
            self._outgoing.append(protocol.json.dumps({
                "action": "depth_window", "t0": int(t0), "t1": int(t1), "cols": int(cols),
                "ylo": float(ylo), "yhi": float(yhi), "ybins": int(ybins)}) + "\n")

    def request_trades_window(self, t0: int, t1: int, ylo: float, yhi: float) -> None:
        """Queue a trades_window request (Phase 3 bubbles — heatmap enter / pan / zoom). Live batches ride the
        existing depth_window subscription (client.heatmap)."""
        with self._send_lock:
            self._outgoing.append(protocol.json.dumps({
                "action": "trades_window", "t0": int(t0), "t1": int(t1),
                "ylo": float(ylo), "yhi": float(yhi)}) + "\n")

    def stop_depth_window(self) -> None:
        """Queue a depth_window_stop (heatmap mode-exit) so the daemon stops pushing live columns."""
        with self._send_lock:
            self._outgoing.append(protocol.json.dumps({"action": "depth_window_stop"}) + "\n")
        with self.data_lock:
            self._hm_window = None
            self._hm_cols.clear()
            self._tb_window = None
            self._tb_batches.clear()

    def depth_heatmap_state(self):
        """Heatmap-ONLY accessor (called by _scan_depth_heatmap when that mode is active). Returns
        ``(version, window_or_None, [live_columns])`` and CLEARS the delivered window + drains the live
        columns so each is consumed once. The grid is here, never on snapshot()."""
        with self.data_lock:
            w = self._hm_window
            self._hm_window = None
            cols = list(self._hm_cols)
            self._hm_cols.clear()
            return self._hm_ver, w, cols

    def trades_state(self):
        """Phase 3 bubbles accessor (heatmap mode only, consume-once): ``(version, window_or_None,
        [live_batches])``; clears the delivered window + drains the live batches. The trade arrays stay HERE,
        never on snapshot()."""
        with self.data_lock:
            w = self._tb_window
            self._tb_window = None
            batches = list(self._tb_batches)
            self._tb_batches.clear()
            return self._tb_ver, w, batches

    def stop(self) -> None:
        self._stop.set()

    def refresh(self) -> None:
        """Force-drop the current socket and reconnect immediately (manual chart refresh).

        A net blip can leave a half-open socket the OS never reports as dead, so the worker
        blocks in recv() forever with no data — the chart freezes. Shutting the socket down
        unblocks that recv; run() then reconnects and re-requests the catch-up without the
        usual backoff. Safe if already mid-reconnect (no live socket to drop).
        """
        self._force_reconnect.set()
        with self.data_lock:
            s = self._sock
        if s is not None:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Thread body
    # ------------------------------------------------------------------
    def run(self) -> None:
        while not self._stop.is_set():
            sock = None
            try:
                sock = socket.create_connection((config.IPC_HOST, config.IPC_PORT), timeout=5)
                sock.settimeout(1.0)
                with self.data_lock:
                    self.connected = True
                    self._sock = sock              # expose for refresh()'s force-drop
                self._force_reconnect.clear()      # fresh connection -> arm a future refresh
                # announce our timeframe to the daemon on connect
                self.request_timeframe(self.tf)

                buffer = b""
                while not self._stop.is_set():
                    self._flush_outgoing(sock)
                    try:
                        data = sock.recv(65536)
                    except socket.timeout:
                        if self._force_reconnect.is_set():
                            break                  # manual refresh -> drop + reconnect (fallback path)
                        continue
                    if not data:
                        break  # daemon closed
                    buffer += data
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        pkt = protocol.parse_line(line.decode("utf-8", "ignore"))
                        if pkt is not None:
                            self._apply(pkt)
            except (ConnectionError, OSError):
                pass
            finally:
                with self.data_lock:
                    self.connected = False
                    self._sock = None
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
            # Reconnect immediately on a manual refresh; otherwise back off.
            if not self._stop.is_set() and not self._force_reconnect.is_set():
                time.sleep(config.RECONNECT_SECS)

    def _flush_outgoing(self, sock: socket.socket) -> None:
        with self._send_lock:
            pending = list(self._outgoing)
            self._outgoing.clear()
        for line in pending:
            try:
                sock.sendall(line.encode("utf-8"))
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Packet application (all writes under data_lock)
    # ------------------------------------------------------------------
    def _apply(self, pkt) -> None:
        # --- chunked catch-up: build the heavy structures OUTSIDE the lock, then
        # take it only for a pointer-swap / list.extend, so the 20Hz GUI loop slips
        # in between chunks and the UI never stalls during a large (10k) dump. The
        # tf is re-checked under the lock (the GUI thread may switch tf concurrently).
        if isinstance(pkt, protocol.CatchupStartPacket):
            if pkt.tf != self.tf:
                return
            new_fp = OrderedDict(sorted(pkt.footprints.items(), key=lambda x: int(x[0])))
            new_obs = list(pkt.order_blocks)
            new_abs = list(pkt.absorptions)
            tgt = pkt.target_vol
            with self.data_lock:
                if pkt.tf != self.tf:
                    return
                self.target_vol = tgt
                self.order_blocks = new_obs
                self.absorptions = new_abs   # paint whale bands on boot, before the first recompute
                self.footprints = new_fp
                self.closed_buckets = []
                self._cb_ver += 1
                self._ob_ver += 1
                self._catchup_loading = True
            return
        if isinstance(pkt, protocol.CatchupChunkPacket):
            if pkt.tf != self.tf:
                return
            batch = pkt.closed_buckets          # already deserialized on this worker thread
            with self.data_lock:
                if pkt.tf != self.tf:
                    return
                self.closed_buckets.extend(batch)
                self._cb_ver += 1
            return
        if isinstance(pkt, protocol.CatchupEndPacket):
            if pkt.tf != self.tf:
                return
            active = dict(pkt.active_bucket)
            with self.data_lock:
                if pkt.tf != self.tf:
                    return
                self.active_bucket = active
                self.vpin = pkt.vpin
                if len(self.closed_buckets) > config.CLOSED_BUCKETS_CAP:
                    self.closed_buckets = self.closed_buckets[-config.CLOSED_BUCKETS_CAP:]
                    self._cb_ver += 1
                self._catchup_loading = False
            return

        # --- light / high-frequency frames: one short lock, as before ---
        with self.data_lock:
            # Timeframe-bearing frames are filtered against our subscription so a
            # stale in-flight frame from a previous tf can never corrupt the cache.
            if isinstance(pkt, protocol.TickPacket):
                if pkt.tf != self.tf:
                    return
                c = pkt.candle
                t = int(c["time"])
                self.candles[t] = [c["open"], c["high"], c["low"], c["close"], c["volume"]]
                self.latest_price = pkt.price
                self.forming_time = t
                self.active_bucket = pkt.active_bucket   # live pulsing right edge
                if pkt.footprint:
                    self.footprints[str(t)] = pkt.footprint
                self._enforce_cap()
            elif isinstance(pkt, protocol.CatchupPacket):
                # legacy monolithic catch-up (back-compat; daemon now streams chunks)
                if pkt.tf != self.tf:
                    return
                self.target_vol = pkt.target_vol
                self.order_blocks = pkt.order_blocks
                self.absorptions = list(pkt.absorptions)   # back-compat monolithic catch-up
                self.footprints = OrderedDict(sorted(pkt.footprints.items(), key=lambda x: int(x[0])))
                self.closed_buckets = list(pkt.closed_buckets)   # seed scanner history
                self.active_bucket = dict(pkt.active_bucket)
                self.vpin = pkt.vpin
                self._cb_ver += 1
                self._ob_ver += 1
            elif isinstance(pkt, protocol.ObPacket):
                if pkt.tf != self.tf:
                    return
                # Step 19.4: two ObPacket roles, distinguished by new_buckets.
                #  * CLOSE piggyback (new_buckets present, order_blocks=[]): grow the
                #    scanner history promptly; the OB matrix is NOT re-shipped per close,
                #    so leave it untouched (no torn/cleared matrix on every close).
                #  * OB-MATRIX REFRESH from recompute_loop (no new_buckets): authoritative
                #    for order_blocks, including clearing to [] when all OBs are gone.
                if pkt.new_buckets:
                    self.closed_buckets.extend(pkt.new_buckets)
                    if len(self.closed_buckets) > config.CLOSED_BUCKETS_CAP:
                        self.closed_buckets = self.closed_buckets[-config.CLOSED_BUCKETS_CAP:]
                    self._cb_ver += 1
                else:
                    self.order_blocks = pkt.order_blocks
                    # Full-recompute broadcast ONLY: feeds attaches absorptions here (recompute_loop),
                    # never on the close-piggyback above (which ships absorptions=[]). Storing it in
                    # the if-branch would WIPE every mark on each bucket close -> flicker/vanish.
                    self.absorptions = pkt.absorptions
                    self._ob_ver += 1
                self.vpin = pkt.vpin
            elif isinstance(pkt, protocol.LiquidationPacket):
                self.liquidations.append(
                    {"side": pkt.side, "price": pkt.price, "qty": pkt.qty, "time": pkt.time}
                )
                if len(self.liquidations) > 500:
                    self.liquidations = self.liquidations[-500:]
            elif isinstance(pkt, protocol.PulsePacket):
                self.depth = {"bids": pkt.bids, "asks": pkt.asks}
                self.oi = pkt.oi
            elif isinstance(pkt, protocol.DepthWindowPacket):
                self._hm_window = pkt          # ~MB grid stays in the delivery buffer (NOT snapshot())
                self._hm_ver += 1
            elif isinstance(pkt, protocol.DepthColumnPacket):
                self._hm_cols.append(pkt)
                self._hm_ver += 1
            elif isinstance(pkt, protocol.TradesWindowPacket):
                self._tb_window = pkt          # raw trade arrays stay in the delivery buffer (NOT snapshot())
                self._tb_ver += 1
            elif isinstance(pkt, protocol.TradeBatchPacket):
                self._tb_batches.append(pkt)
                self._tb_ver += 1

    def _enforce_cap(self) -> None:
        """Trim the candle cache to the viewport limit (spec §1.1.2)."""
        while len(self.candles) > config.CHART_CACHE_CAP:
            self.candles.popitem(last=False)
        while len(self.footprints) > config.CHART_CACHE_CAP:
            self.footprints.popitem(last=False)

    # ------------------------------------------------------------------
    # GUI-thread snapshot (lock held briefly, then released)
    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        """Return a self-consistent copy of the cache for one render frame.

        The heavy ``order_blocks`` / ``closed_buckets`` lists use copy-on-write:
        re-exported only when their version moved, so a 10k-bucket history costs
        one copy per data change — not one O(N) copy per 20Hz frame. Every
        consumer treats the returned objects read-only (``_build_scanner_buckets``
        copies before mutating), so sharing the cached export is safe.
        """
        with self.data_lock:
            times = np.fromiter(self.candles.keys(), dtype=np.float64, count=len(self.candles))
            if len(self.candles):
                ohlcv = np.array(list(self.candles.values()), dtype=np.float64)
            else:
                ohlcv = np.empty((0, 5), dtype=np.float64)
            if self._cb_exp_ver != self._cb_ver:
                self._cb_exp = list(self.closed_buckets)
                self._cb_exp_ver = self._cb_ver
            if self._ob_exp_ver != self._ob_ver:
                self._ob_exp = list(self.order_blocks)
                self._ob_exp_ver = self._ob_ver
            snap = {
                "tf": self.tf,
                "times": times,
                "ohlcv": ohlcv,
                "footprints": dict(self.footprints),
                "latest_price": self.latest_price,
                "forming_time": self.forming_time,
                "order_blocks": self._ob_exp,
                "absorptions": list(self.absorptions),
                "liquidations": list(self.liquidations),
                "depth": {"bids": list(self.depth["bids"]), "asks": list(self.depth["asks"])},
                "oi": self.oi,
                "vpin": self.vpin,
                "target_vol": self.target_vol,
                "closed_buckets": self._cb_exp,
                "active_bucket": dict(self.active_bucket),
                "connected": self.connected,
                "catchup_loading": self._catchup_loading,
            }
        return snap
