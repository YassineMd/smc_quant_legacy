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

import os
import socket
import threading
import time
from collections import OrderedDict, deque
from typing import Dict, List, Optional

import numpy as np
import requests

from . import bucket_cache, config, protocol


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
        self._liq_sweeps: dict = {}               # (idx,side) -> {ts,side,level,idx}; daemon-pushed 15m sweeps
        self.depth: Dict[str, list] = {"bids": [], "asks": []}
        self.oi: float = 0.0
        self.size_thr: List[float] = []   # rolling 60-min trade-size pctiles [p50,p90,p95,p99,p99.5] (contracts); [] = not warm
        self.vpin: float = 0.0
        self.target_vol: float = config.DEFAULT_TARGET_VOL
        # Phase 0 bucket pipeline: full closed-bucket history (seeded by CATCHUP,
        # grown by ObPacket.new_buckets) + the live pulsing active bucket. Both use
        # the protocol.BucketSnapshot schema.
        self.closed_buckets: List[dict] = []
        self._total_closed: int = 0   # absolute DB-id of closed_buckets[-1] (stable all-time bucket index)
        self.active_bucket: dict = {}
        self.connected: bool = False
        self._catchup_loading: bool = False   # True while a chunked catch-up streams
        # --- startup instrumentation (writes ONE line to data/startup_perf.log per catch-up) ---
        # Splits the "slow load" into its real phases so a fix targets the actual bottleneck:
        #   wall = CatchupStart->End elapsed; parse_s = time inside protocol.parse_line (JSON->objects);
        #   net_idle = wall - parse_s (socket-wait / daemon-build); bytes = catch-up payload size;
        #   n_buckets / n_levels = heap the parse produced. Near-zero cost (a few counters + one write).
        self._cu_active: bool = False
        self._cu_t0: float = 0.0
        self._cu_bytes: int = 0
        self._cu_parse: float = 0.0
        # --- persistent bucket cache / delta catch-up (see app.bucket_cache) ---
        self._pending_since = None      # the `since` cursor sent with the in-flight set_tf (for the delta check)
        self._delta_expect = None       # buckets a DELTA catch-up should deliver (None = full catch-up)
        self._delta_got: int = 0        # buckets actually appended during the current catch-up
        self._last_cache_save: float = 0.0
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
        self._tb_windows: deque = deque(maxlen=8)   # pending TradesWindowPackets — a LIST:
        #                                  the DOM's entry backfill + custom-start deep fetch can
        #                                  land in one drain interval; a single slot lost one
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
        """Announce ``tf`` to the daemon and seed the local cache so the catch-up is a DELTA when possible.

        Three cases:
          * RECONNECT (same tf, we still hold buckets): keep the in-memory base (freshest) and ask for the
            delta since it — never regress to the on-disk copy.
          * TF-SWITCH / cold start: persist the tf we're leaving, then seed the new tf from
            :mod:`app.bucket_cache` (if a valid cache exists) and ask for the delta since it; on a cache
            miss, clear and ask for a full catch-up exactly as before.
        On an old daemon (or a rejected cursor) the reply is a full ``delta=False`` catch-up that clears the
        seed — no benefit, no corruption. Disk I/O (save/load) happens OUTSIDE the lock."""
        # Only KEEP the in-memory base if the last catch-up actually COMPLETED — a partial window
        # (interrupted mid-stream) has closed_buckets that lag _total_closed, so trusting it + asking
        # since=_total_closed would leave a silent gap. If in doubt, reseed from disk / full-catch-up.
        reconnect_same = (tf == self.tf and bool(self.closed_buckets) and not self._catchup_loading)
        entry = None
        if not reconnect_same:
            self.save_cache_now()             # persist the outgoing tf (no-op if it has no data)
            entry = bucket_cache.load(tf)     # seed the incoming tf (disk read off-lock)
        with self.data_lock:
            self.tf = tf
            self.candles = OrderedDict()
            self.liquidations = []
            self._liq_sweeps = {}             # (idx,side) -> {ts,side,level,idx}; 15m sweeps pushed by the daemon
            self.active_bucket = {}
            if reconnect_same:
                since = self._total_closed    # keep in-memory closed_buckets/footprints/target_vol as-is
            elif entry is not None:
                self.closed_buckets = list(entry["buckets"])
                self._total_closed = int(entry.get("total_closed", 0))
                self.footprints = OrderedDict(
                    sorted(entry.get("footprints", {}).items(), key=lambda x: int(x[0])))
                self.target_vol = float(entry.get("target_vol", 0.0)) or config.DEFAULT_TARGET_VOL
                self.order_blocks = []        # OBs/absorptions arrive fresh in the catch-up start
                self.absorptions = []
                since = self._total_closed
            else:
                self.footprints = OrderedDict()
                self.order_blocks = []
                self.absorptions = []
                self.closed_buckets = []
                self._total_closed = 0
                self.target_vol = 0.0         # stale until the new tf's catch-up arrives (scale labels skip)
                since = None
            self._pending_since = since
            self._cb_ver += 1                 # invalidate COW caches on the reseed/clear
            self._ob_ver += 1
        frame = {"action": "set_tf", "tf": tf}
        if since is not None and since > 0:
            frame["since"] = since
        with self._send_lock:
            self._outgoing.append(protocol.json.dumps(frame) + "\n")
        # Repopulate baseline synchronously so the chart never blanks for long
        self.load_baseline(tf)

    def save_cache_now(self) -> None:
        """Persist the CURRENT tf's base window to disk (shallow-copy under the lock, pickle off-lock).
        No-op if there's nothing worth caching. Called periodically, on tf-switch, and on stop."""
        with self.data_lock:
            # Never persist a window mid-catch-up: closed_buckets is still filling behind _total_closed,
            # so the pair would be inconsistent (partial buckets, complete cursor) and seed a gap next open.
            if not self.closed_buckets or self._total_closed <= 0 or self._catchup_loading:
                return
            tf = self.tf
            buckets = list(self.closed_buckets)   # shallow: inner closed-bucket dicts are immutable
            tc = self._total_closed
            fps = dict(self.footprints)
            tv = self.target_vol
        bucket_cache.save(tf, buckets, tc, fps, tv)
        self._last_cache_save = time.time()

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
            self._tb_windows.clear()
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

    def depth_book(self):
        """Cheap book accessor for the DOM ladder: (bids, asks, latest_price) from the last PULSE —
        independent of the chart source (the TIME feed's identically-shaped snapshot has no depth, so
        the DOM must never read the book off _effective_snapshot())."""
        with self.data_lock:
            return list(self.depth["bids"]), list(self.depth["asks"]), self.latest_price

    def trades_state(self):
        """Trades accessor (heatmap/trades/dom modes, consume-once): ``(version, [windows],
        [live_batches])``; drains both queues. The trade arrays stay HERE, never on snapshot()."""
        with self.data_lock:
            ws = list(self._tb_windows)
            self._tb_windows.clear()
            batches = list(self._tb_batches)
            self._tb_batches.clear()
            return self._tb_ver, ws, batches

    def stop(self) -> None:
        self._stop.set()
        try:
            self.save_cache_now()   # best-effort clean-close persist (the periodic save is the backstop)
        except Exception:
            pass

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
                last_rx = time.monotonic()
                got_any = False
                while not self._stop.is_set():
                    self._flush_outgoing(sock)
                    if (self.closed_buckets and not self._catchup_loading
                            and time.time() - self._last_cache_save > config.BUCKET_CACHE_SAVE_SECS):
                        self.save_cache_now()   # refresh the on-disk base so the next open deltas
                    try:
                        data = sock.recv(65536)
                    except socket.timeout:
                        if self._force_reconnect.is_set():
                            break                  # manual refresh -> drop + reconnect (fallback path)
                        if time.monotonic() - last_rx > (15.0 if (got_any and not self._catchup_loading) else 120.0):
                            break                  # STALENESS WATCHDOG (2026-08-24): a wedged-but-open socket (half-
                        #                            open SSH tunnel, hung daemon) kept this loop "connected" but
                        #                            silent FOREVER — frozen price, no reconnect (user's pinned-window
                        #                            bug). The daemon pushes the live edge every ~150ms, so 15s of
                        #                            silence = dead; drop -> reconnect -> (delta) catch-up heals.
                        #                            ⚠ GRACE (120s) until the FIRST bytes of a connection AND while a
                        #                            chunked catch-up streams (_catchup_loading): a cold catch-up
                        #                            start is legitimately slow (OB+absorption build), and with
                        #                            several windows' 10k-bucket catch-ups interleaving on the
                        #                            daemon loop a client can wait >15s between ITS chunks — a 15s
                        #                            cutoff in either phase makes every window abort + re-request
                        #                            in lockstep, a self-sustaining thundering herd that pinned the
                        #                            daemon at ~92-108% CPU (py-spy-proven TWICE 2026-08-24: first
                        #                            in catchup_start/calc_absorption, then in _send_catchup
                        #                            serialization after the first grace only covered first-bytes).
                        continue
                    if not data:
                        break  # daemon closed
                    last_rx = time.monotonic()
                    got_any = True
                    buffer += data
                    if self._cu_active:
                        self._cu_bytes += len(data)
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        if self._cu_active:
                            _p = time.perf_counter()
                            pkt = protocol.parse_line(line.decode("utf-8", "ignore"))
                            self._cu_parse += time.perf_counter() - _p
                        else:
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

    def _write_startup_perf(self, tf, wall, parse, nbytes, n_buckets, n_levels) -> None:
        """Append ONE catch-up breakdown line to data/startup_perf.log (worker thread, best-effort).

        net_idle = wall - parse ~= socket-wait + daemon-side build; payload = catch-up bytes on the wire;
        obj~ = 3*n_levels (each price level is a dict + a 'b' + an 's' float) = the heap the parse leaves
        resident. This is the ground truth for whether the 'slow load' is network, parse, or heap."""
        try:
            net_idle = max(0.0, wall - parse)
            mb = nbytes / (1024.0 * 1024.0)
            line = ("CATCHUP tf=%s wall=%.2fs parse=%.2fs net_idle=%.2fs payload=%.1fMB "
                    "buckets=%d levels=%d obj~=%.2fM\n" % (
                        tf, wall, parse, net_idle, mb, n_buckets, n_levels, 3.0 * n_levels / 1e6))
            with open(os.path.join(config.DATA_DIR, "startup_perf.log"), "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
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
                self.footprints = new_fp     # the ~200 recent footprint nodes, current either way
                if pkt.delta:
                    # DELTA: keep the seeded base; the chunks APPEND the buckets closed since our cursor.
                    self._delta_expect = int(pkt.total_closed) - int(self._pending_since or 0)
                else:
                    # FULL: clear + rebuild from chunks (also the fallback when the cursor was rejected).
                    self.closed_buckets = []
                    self._delta_expect = None
                self._delta_got = 0
                self._total_closed = pkt.total_closed   # DB-id of the window's last bucket (chunks fill behind)
                self._cb_ver += 1
                self._ob_ver += 1
                self._catchup_loading = True
                self._cu_active = True                  # arm startup-phase timing (see __init__)
                self._cu_t0 = time.perf_counter()
                self._cu_bytes = 0
                self._cu_parse = 0.0
            return
        if isinstance(pkt, protocol.CatchupChunkPacket):
            if pkt.tf != self.tf:
                return
            batch = pkt.closed_buckets          # already deserialized on this worker thread
            with self.data_lock:
                if pkt.tf != self.tf:
                    return
                self.closed_buckets.extend(batch)
                self._delta_got += len(batch)
                self._cb_ver += 1
            return
        if isinstance(pkt, protocol.CatchupEndPacket):
            if pkt.tf != self.tf:
                return
            active = dict(pkt.active_bucket)
            _cu = False
            _bad_delta = False
            with self.data_lock:
                if pkt.tf != self.tf:
                    return
                self.active_bucket = active
                self.vpin = pkt.vpin
                if len(self.closed_buckets) > config.CLOSED_BUCKETS_CAP:
                    self.closed_buckets = self.closed_buckets[-config.CLOSED_BUCKETS_CAP:]
                    self._cb_ver += 1
                self._catchup_loading = False
                # Delta sanity: a DELTA must have delivered exactly the expected count. The daemon
                # validates the cursor on a single event loop so this can't fail in practice, but if it
                # ever did we must NOT trust the stitched base -> force a clean full reload below.
                if self._delta_expect is not None and self._delta_got != self._delta_expect:
                    _bad_delta = True
                self._delta_expect = None
                if self._cu_active:                    # capture startup-phase numbers under the lock
                    self._cu_active = False
                    _cu = True
                    _wall = time.perf_counter() - self._cu_t0
                    _parse, _bytes = self._cu_parse, self._cu_bytes
                    _nb = len(self.closed_buckets)
                    _nl = sum(len(b.get("levels") or ()) for b in self.closed_buckets)
            if _bad_delta:                             # drop the base + re-request a FULL catch-up (no `since`)
                bucket_cache.discard(self.tf)
                with self.data_lock:
                    self.closed_buckets = []
                    self._total_closed = 0
                    self._cb_ver += 1
                with self._send_lock:
                    self._outgoing.append(protocol.json.dumps({"action": "set_tf", "tf": self.tf}) + "\n")
            if _cu:                                    # write OUTSIDE the lock (no I/O under lock)
                self._write_startup_perf(self.tf, _wall, _parse, _bytes, _nb, _nl)
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
                self._total_closed = pkt.total_closed
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
                    self._total_closed = pkt.total_closed   # keep the absolute index aligned per close
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
                self.size_thr = pkt.size_thr
            elif isinstance(pkt, protocol.LiqSweepPacket):   # tf-agnostic: keep regardless of subscribed tf
                self._liq_sweeps[(pkt.idx, pkt.side)] = {
                    "ts": pkt.ts, "side": pkt.side, "level": pkt.level, "idx": pkt.idx}
            elif isinstance(pkt, protocol.DepthWindowPacket):
                self._hm_window = pkt          # ~MB grid stays in the delivery buffer (NOT snapshot())
                self._hm_ver += 1
            elif isinstance(pkt, protocol.DepthColumnPacket):
                self._hm_cols.append(pkt)
                self._hm_ver += 1
            elif isinstance(pkt, protocol.TradesWindowPacket):
                self._tb_windows.append(pkt)   # raw trade arrays stay in the delivery buffer (NOT snapshot())
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
    def live_price(self) -> "tuple[float, float]":
        """CHEAP live-price read for the clock-candle fold: (latest_price, active_bucket.end_time) under a brief lock,
        WITHOUT building the full snapshot() dict (which copies candles/buckets/OBs and is far too heavy to call at
        20Hz just for a price). end_time is the daemon's ~now send-stamp -> lets the caller detect a lagging worker."""
        with self.data_lock:
            return float(self.latest_price or 0.0), float((self.active_bucket or {}).get("end_time", 0.0) or 0.0)

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
                "size_thr": list(self.size_thr),
                "vpin": self.vpin,
                "target_vol": self.target_vol,
                "closed_buckets": self._cb_exp,
                "total_closed": self._total_closed,
                "liq_sweeps": list(self._liq_sweeps.values()),   # daemon-pushed 15m sweeps (read-only)
                "active_bucket": dict(self.active_bucket),
                "connected": self.connected,
                "catchup_loading": self._catchup_loading,
            }
        return snap
