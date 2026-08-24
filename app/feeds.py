"""Tier-2 daemon-only live market feeds — concurrent multi-timeframe core.

``MarketDataCore`` owns all mutable market state (5 per-timeframe engines, the
footprint DB, the order book, OI) and emits :mod:`app.protocol` frames through
two injected fan-out callables:

    broadcast_tf(tf, line)  -> TICK / NEW_QUANT_OB  (delivered only to windows
                               subscribed to that timeframe)
    broadcast_all(line)     -> LIQUIDATION / PULSE  (timeframe-agnostic, all)

Section 11 upgrade: all FIVE Binance kline timeframes stream concurrently over a
single combined websocket (``/stream?streams=...kline_1m/...kline_5m/...``).
Every message is routed by ``k["i"]`` to its own engine + footprint bucket, so
any number of terminal windows can each subscribe to a different timeframe with
zero contention. There is no offline/mock/replay path (Purge Protocol §10.1.4).
"""

from __future__ import annotations

import asyncio
import gzip
import json
import multiprocessing
import os
import statistics
import time
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from types import SimpleNamespace
from typing import Callable, Dict, Optional

import numpy as np
import pandas as pd
import requests
import websockets
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import config
from .protocol import (CatchupEndPacket, CatchupPacket, CatchupStartPacket,
                       LiqSweepPacket, LiquidationPacket, ObPacket, PulsePacket, TickPacket)
from .aggtrade import OiAttributor, candle_open, median_target_vol, trade_to_tick
from .quant_engine import (ClockEngine, QuantEngine, build_engine_registry, calc_absorption,
                           calc_quant_obs, rank_obs)

# Recent observed candles shipped in a CATCHUP footprint payload (bounds frame size).
CATCHUP_FOOTPRINT_LIMIT = 200


def _make_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def _combined_kline_url() -> str:
    streams = "/".join(f"solusdt@kline_{tf}" for tf in config.TIMEFRAMES)
    return f"wss://fstream.binance.com/market/stream?streams={streams}"


# ---------------------------------------------------------------------------
# Step A — process-pool offload of the OB rescan. calc_quant_obs is O(obs×buckets)
# (~3s on a 10k-bucket engine, the mitigation loop) and PURE: moving it off the
# single asyncio loop onto a worker PROCESS (spawn, 2nd core) keeps the broadcast
# loop from EVER blocking, regardless of timeframe (4h) or market speed. Fallback:
# on ANY pool failure -> on-loop compute (the pre-offload freeze), permanently
# after repeated failures — a broken pool degrades to today's behavior, never
# "daemon down" and never thrash-recreating.
# ---------------------------------------------------------------------------
_OB_POOL_MAX_FAILS = 3   # after this many failures, latch permanently to on-loop (stable, no thrash)
LIQ_SWEEP_TF = "15m"     # the sweep signal timeframe pushed tf-agnostically to every client
LIQ_SWEEP_WINDOW = 140   # per-close detector tail (> LOOKBACK+Z_BASE+K); a new sweep is always at the edge


def _recompute_ob_line(buckets: list, tf_key: str, vpin: float) -> str:
    """PURE (no shared state): OB rescan + absorption marks + ObPacket serialization over an IMMUTABLE bucket
    snapshot. Runs ON the worker process (or on-loop as the fallback); the result is byte-IDENTICAL either way
    (calc_quant_obs / calc_absorption only read the buckets). Proof: scripts/validate_ob_pool.py."""
    obs = rank_obs(calc_quant_obs(SimpleNamespace(closed_buckets=buckets), tf_key))
    return ObPacket(tf=tf_key, order_blocks=obs, absorptions=calc_absorption(buckets),
                    new_buckets=[], vpin=vpin).to_line()


def _ob_pool_warmup() -> bool:
    """Trivial task to force the spawn worker to start at daemon boot (off the critical path)."""
    return True


class MarketDataCore:
    """Single point of contact for all external exchange data (spec §1.2.1)."""

    def __init__(self, footprints_db: Dict[str, dict],
                 broadcast_tf: Callable[[str, str], None],
                 broadcast_all: Callable[[str], None],
                 tf_has_subscribers: Optional[Callable[[str], bool]] = None,
                 broadcast_time: Optional[Callable[[str, str], None]] = None,
                 time_tf_has_subscribers: Optional[Callable[[str], bool]] = None):
        self.footprints_db = footprints_db
        self.broadcast_tf = broadcast_tf
        self.broadcast_all = broadcast_all
        # CLOCK-candle PUSH (real-time parity with volume buckets): the daemon PUSHES the forming clock candle + any
        # new closes to TIME-subscribed clients on the live-edge loop, so clock candles no longer lag behind a poll.
        self.broadcast_time: Callable[[str, str], None] = broadcast_time or (lambda _tf, _l: None)
        self._time_subbed: Callable[[str], bool] = time_tf_has_subscribers or (lambda _tf: False)
        self._time_push_n: Dict[str, int] = {}          # tf -> last closed-count we pushed (init lazily; no backfill)
        # Only DO per-tf work (OB rescan + serialize the forming footprint/OB matrix) for timeframes a client
        # is actually subscribed to. Without this the loop recomputes + serializes ALL 5 tfs every cycle —
        # the 1h/4h forming footprints are huge — starving the broadcast loop for seconds (the live-price lag).
        self._tf_subbed: Callable[[str], bool] = tf_has_subscribers or (lambda _tf: True)
        # Step A: lazy spawn process pool for the OB rescan. _ob_pool_disabled latches True after
        # _OB_POOL_MAX_FAILS failures -> permanent, stable on-loop fallback (no thrash, no worker leak).
        self._ob_pool: Optional[ProcessPoolExecutor] = None
        self._ob_pool_disabled = False
        self._ob_pool_fails = 0
        self.engines: Dict[str, QuantEngine] = build_engine_registry()
        # CLOCK-candle engines (full-fidelity time chart): one per tf, fed the SAME tick stream as the volume engines
        # but closing on clock boundaries -> complete BucketSnapshot dicts (real opL/opS, buyer/seller-ER, cvd, ticks,
        # levels, POC) for get_time_candles. Bounded RAM (TIME_ENGINE_CAP/tf); rebuilt live from ticks after a restart.
        self.clock_engines: Dict[str, ClockEngine] = {
            tf: ClockEngine(config.TF_SECONDS[tf], tf) for tf in config.TIMEFRAMES}
        # CLOCK-candle PERSISTENCE: closed clock candles are saved to disk + reloaded so a daemon restart does NOT wipe
        # clock history (they'd otherwise cold-boot empty; 15m/30m take days to refill). {tf: {start_time_int: wire}}.
        self._tc_store: Dict[str, Dict[int, dict]] = {}
        self._tc_loaded: bool = False          # lazy load-once guard (on the first get_time_candles)
        self._tc_last_save: float = 0.0         # save throttle
        self.session = _make_session()

        self.latest_live_price: float | None = None
        self.latest_utime: Dict[str, str] = {}   # tf -> uTime of forming candle
        self.pulse_state = {
            "local_ob": {"bids": {}, "asks": {}, "lastUpdateId": 0},
            "oi": 0.0,
        }
        # 19.3: global OI pending-balance attributor. fetch_oi_loop drives on_poll
        # once per OI poll; aggtrade_stream drives on_trade per trade + on_reconnect
        # on a stream gap.
        self.oi_attr = OiAttributor()
        # 19.3b: last kline candle per tf, cached so live_edge_loop can re-emit the
        # forming edge at ~150ms (between the 1/s kline heartbeats) without trades.
        self.last_candle: Dict[str, dict] = {}
        # Phase 1: bounded (drop-oldest) capture buffers for the depth/trade store. Filled by O(1) tees on
        # the loop (public_stream / aggtrade_stream); drained off-loop by DepthStore.sync_loop. Fully
        # independent of the bucket/close path. _last_depth_u/_ts = the last applied diff, stamped onto
        # snapshot anchors so reconstruction can replay deltas with u > anchor.u.
        self._depth_delta_buf: deque = deque(maxlen=config.DEPTH_BUFFER_CAP)
        self._trade_buf: deque = deque(maxlen=config.DEPTH_BUFFER_CAP)
        self._trades_live_buf: deque = deque(maxlen=20000)   # Phase 3: live trade bubbles (drained per pulse)
        self._depth_snap_buf: deque = deque(maxlen=1000)
        self._last_depth_u: int = 0
        self._last_depth_ts: int = 0
        # LARGE/SMALL: rolling 60-min (ts_ms, qty) window for the size-percentile baseline. Fed O(1) per
        # aggTrade; pruned + percentiled every SIZE_THR_RECOMPUTE_SECS on the pulse loop. self._size_thr is
        # the shipped [p50,p90,p95,p99,p99.5] (contracts); [] = not warm yet (cold-start guard).
        self._size_win: deque = deque()
        self._size_thr: list = []
        self._size_thr_t: float = 0.0    # last recompute (ms; throttle)
        # LIVE 15m SWEEP PUSH: the frozen Tier-A detector runs on 15m closes (ungated) and broadcast_all's each
        # NEW sweep so any client (even one on 1m) can place it by ts. _liq_sweeps = the current set (for
        # catch-up); _liq_emitted = (idx,side) dedup keys; seeded once from the rehydrated history at startup.
        self._liq_sweeps: list = []
        self._liq_emitted: set = set()
        self._liq_seeded: bool = False

    # ------------------------------------------------------------------
    # CATCHUP builder (per subscribing client's timeframe)
    # ------------------------------------------------------------------
    def build_catchup(self, tf: str) -> CatchupPacket:
        if tf not in self.engines:
            tf = config.DEFAULT_TF
        engine = self.engines[tf]
        order_blocks = rank_obs(calc_quant_obs(engine, tf))
        # Full bucket vectors for the scanner's history + the live pulsing edge.
        closed = [b.full_snapshot() for b in engine.closed_buckets]
        active = engine.active_bucket.live_snapshot(time.time(), engine.avg_velocity)

        tf_db = self.footprints_db.get(tf, {})
        recent_keys = sorted(tf_db.keys(), key=lambda x: int(x))[-CATCHUP_FOOTPRINT_LIMIT:]
        footprints = {k: tf_db[k] for k in recent_keys}

        return CatchupPacket(
            tf=tf,
            target_vol=engine.target_vol,
            closed_buckets=closed,
            active_bucket=active,
            order_blocks=order_blocks,
            absorptions=self._absorption_marks(tf),
            footprints=footprints,
            vpin=engine.vpin,
            total_closed=engine.total_closed,
        )

    # ------------------------------------------------------------------
    # Chunked CATCHUP builders (the daemon orchestrates START -> CHUNKs -> END)
    # ------------------------------------------------------------------
    def catchup_start(self, tf: str, delta: bool = False) -> CatchupStartPacket:
        """Frame #1: target_vol + the current OB matrix + recent footprints +
        the total bucket count (client renders these immediately + shows progress).

        ``delta=True`` tells the client to APPEND the following chunks to its cached base
        (see :mod:`app.bucket_cache`) instead of clearing — used when the client supplied a valid
        ``since`` cursor. The metadata (OBs / footprints / target_vol / total_closed) is the current
        set either way, so a delta client still refreshes them."""
        if tf not in self.engines:
            tf = config.DEFAULT_TF
        engine = self.engines[tf]
        order_blocks = rank_obs(calc_quant_obs(engine, tf))
        tf_db = self.footprints_db.get(tf, {})
        recent_keys = sorted(tf_db.keys(), key=lambda x: int(x))[-CATCHUP_FOOTPRINT_LIMIT:]
        footprints = {k: tf_db[k] for k in recent_keys}
        return CatchupStartPacket(
            tf=tf, target_vol=engine.target_vol, order_blocks=order_blocks,
            absorptions=self._absorption_marks(tf),
            footprints=footprints, total_buckets=len(engine.closed_buckets),
            total_closed=engine.total_closed, delta=delta)

    def catchup_buckets(self, tf: str) -> list:
        """The full closed-bucket snapshot list; the daemon slices it into
        ``CATCHUP_CHUNK_SIZE`` batches, each shipped as a CATCHUP_CHUNK frame."""
        if tf not in self.engines:
            tf = config.DEFAULT_TF
        return [b.full_snapshot() for b in self.engines[tf].closed_buckets]

    def catchup_time_candles(self, tf: str) -> list:
        """FULL-FIDELITY CLOCK (time) candles for `tf` from the dedicated CLOCK ENGINE -> gap-filled complete
        BucketSnapshot dicts (open/high/low/close + opL/opS/clL/clS, buyer_er/seller_er, cvd wicks, up/dn ticks,
        per-price levels, POC) — every field the terminal renders for a VOLUME bucket, computed identically, only
        closed on clock boundaries. Recent window only (TIME_ENGINE_CAP buckets/tf), rebuilt from ticks after a
        restart. ADDITIVE + read-only w.r.t. the volume-bucket engines. Fail-safe: [] on any error."""
        try:
            from .time_candles import gapfill_wire
            secs = config.TF_SECONDS.get(tf)
            ce = self.clock_engines.get(tf)
            if not secs or ce is None:
                return []
            self._tc_load()                                        # lazy (once): load persisted store + footprint seed
            # AUTHORITATIVE OHLC: override open/high/low/close from the kline-framed footprint nodes (Binance-exact,
            # same source the recon uses); footprint / volume / OI / cvd / ticks stay engine-derived.
            fp = self.footprints_db.get(tf) or {}
            kln = {}
            for ut, n in fp.items():
                if isinstance(n, dict) and "open" in n:
                    try:
                        kln[int(ut)] = n
                    except (TypeError, ValueError):
                        pass

            def _ovr(c):                                           # kline OHLC override for a CLOSED candle (Binance-exact)
                try:
                    n = kln.get(int(c.get("start_time", 0)))
                    if n is not None:
                        c["open"] = float(n["open"]); c["high"] = float(n["high"])
                        c["low"] = float(n["low"]); c["close"] = float(n["close"])
                except (TypeError, ValueError, KeyError):
                    pass                                           # one bad node must not blank the whole serve
                return c

            # PERSISTENT store = source of truth for CLOSED clock candles. Saved to disk + reloaded, so a daemon restart
            # NO LONGER wipes clock history (the whole point of this feature). Closed candles are immutable -> each is
            # built ONCE (as the engine closes it) and kept; a restart repopulates from disk (+ footprint seed for a
            # first/empty run). This also killed the ~5s latency: rebuilding all ~800 full_snapshots per request had
            # blanked/gapped the clients under multi-window poll load.
            store = self._tc_store.setdefault(tf, {})
            for b in ce.closed_buckets:                            # fold the engine's newly-closed candles into the store
                stk = int(b.start_time) if b.start_time is not None else None
                if stk is not None and stk not in store:
                    store[stk] = b.full_snapshot()
            for c in store.values():                               # RE-APPLY the kline override on EVERY serve (2026-08-24):
                _ovr(c)                                            # the push path stores a candle ~150ms after close, BEFORE
            #                                                        Binance's final kline exists; build-once froze that stale
            #                                                        close forever (visible open != prev-close gaps). Mutating
            #                                                        the stored dicts heals push-path + persisted entries too.
            cap = int(getattr(config, "TIME_ENGINE_CAP", 800))
            if len(store) > cap:                                   # keep the most-recent `cap` intervals
                for k in sorted(store)[:len(store) - cap]:
                    del store[k]
            out = [store[k] for k in sorted(store)]
            ab = ce.active_bucket
            if ab.start_time is not None and ab.curr_vol > 0:      # forming candle: keep the LIVE tick OHLC (NOT kline-
                out.append(ab.live_snapshot(time.time(), ce.avg_velocity))   # overridden -> close is fresher than the ~1-2s kline)
            return gapfill_wire(out, secs)
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Clock-candle PERSISTENCE: closed clock candles survive a daemon restart (else they cold-boot empty and take
    # hours/days to refill — 15m/30m are unusable for a long time). Store = {tf: {start_time_int: wire}}. Saved gzipped
    # to data/time_candles.json.gz on a throttle (sync loop) + on shutdown; loaded once on the first serve.
    # ------------------------------------------------------------------
    def _tc_path(self) -> str:
        return os.path.join(config.DATA_DIR, "time_candles.json.gz")

    def _tc_load(self) -> None:
        if getattr(self, "_tc_loaded", False):
            return
        self._tc_loaded = True
        try:                                                       # 1) reload the persisted store (prior runs)
            p = self._tc_path()
            if os.path.exists(p):
                with gzip.open(p, "rt", encoding="utf-8") as f:
                    data = json.load(f) or {}
                for tf, cands in data.items():
                    if tf in config.TF_SECONDS and isinstance(cands, dict):
                        self._tc_store[tf] = {int(k): v for k, v in cands.items()}
                print(f"CLOCK-CANDLES: loaded {sum(len(v) for v in self._tc_store.values())} persisted candles.")
        except Exception as e:
            print(f"CLOCK-CANDLE LOAD ERROR: {e}")
        try:                                                       # 2) seed missing intervals from kline footprints so a
            from .time_candles import candles_from_footprint_nodes, to_bucket_wire   # first/empty run isn't blank
            for tf in config.TIMEFRAMES:
                secs = config.TF_SECONDS.get(tf)
                if not secs:
                    continue
                store = self._tc_store.setdefault(tf, {})
                for c in candles_from_footprint_nodes(self.footprints_db.get(tf) or {}, secs):
                    stk = int(c.get("start_time", 0) or 0)
                    if stk and stk not in store:
                        store[stk] = to_bucket_wire(c)
        except Exception as e:
            print(f"CLOCK-CANDLE SEED ERROR: {e}")
        try:                                                       # 3) official-kline REST heal (background; see method)
            import threading
            threading.Thread(target=self._tc_rest_heal_loop, daemon=True).start()
        except Exception as e:
            print(f"CLOCK-CANDLE REST-HEAL ERROR: {e}")

    def _tc_rest_heal_loop(self) -> None:
        """Boot heal + HOURLY re-heal. Boot covers restart scars; the periodic pass covers the one leak boot can't:
        a brief upstream kline-websocket gap leaves that minute's node non-final (wire-observed 2026-08-24: one live
        candle settled $0.02 off official after an ~18s push stall). 6 tiny REST calls/hour; atomic swaps -> safe."""
        while True:
            try:
                self._tc_rest_heal()
            except Exception as e:
                print(f"CLOCK-CANDLE REST-HEAL ERROR: {e}")
            time.sleep(3600.0)

    def _tc_rest_heal(self) -> None:
        """BOOT HEAL from OFFICIAL Binance klines (REST, background thread; 2026-08-24). The kline pipeline is
        live-websocket-only, so a daemon restart leaves scars the stream can never repair: (a) intervals fully inside
        the downtime have NO kline node -> the footprint seed leaves holes (user-visible MISSING clock candles);
        (b) the last pre-shutdown candle keeps its last STREAMED, non-final kline -> one stale close per restart;
        (c) closes persisted before the re-apply fix are stale on disk. One request per tf covers the whole retained
        window (cap 800 < 1000-row REST limit): stored candles get official OHLC, downtime holes become honest
        kline-only candles (real OHLC + taker-buy/sell volume; footprint/engine scalars stay honest-zero). The
        matching footprints_db node's OHLC is corrected too — else the serve-time _ovr/_KLN re-apply would re-stale
        the boundary candle from the old node (nodes are only UPDATED, never created: kline-only nodes must not leak
        into the volume-bucket/persistence paths). Runs OFF the event loop; every visible write is ONE atomic dict
        swap (no torn/concurrent-resize reads on the serving side); a close folded into the old store dict during the
        tiny copy->swap window is re-folded from ce.closed_buckets on the next serve. Fail-safe per tf: any error
        (offline, rate-limit, bad row) leaves that tf exactly as it was."""
        cap = int(getattr(config, "TIME_ENGINE_CAP", 800))
        healed = created = 0
        for tf in config.TIMEFRAMES:
            secs = config.TF_SECONDS.get(tf)
            if not secs:
                continue
            try:
                r = requests.get(config.REST_KLINES, timeout=6,
                                 params={"symbol": config.SYMBOL, "interval": tf, "limit": min(1000, cap + 5)})
                rows = r.json()
                if not isinstance(rows, list):
                    continue                                       # error payload (rate-limit dict etc.)
            except Exception:
                continue
            cur = self._tc_store.get(tf) or {}
            new = dict(cur)
            fp = self.footprints_db.get(tf) or {}
            now = time.time()
            for row in rows:
                try:
                    stk = int(row[0]) // 1000
                    o, h, l, c, v, tb = (float(row[1]), float(row[2]), float(row[3]),
                                         float(row[4]), float(row[5]), float(row[9]))
                except (TypeError, ValueError, IndexError):
                    continue
                if stk + secs > now:                               # forming interval -> the live engine owns it
                    continue
                w = new.get(stk)
                if w is not None and w.get("empty"):               # seed-stored gap-fill FLAT (no kline node at boot):
                    w = None                                       # treat as a hole -> REPLACE with the real kline
                #                                                    candle, else the "missing candle" stays a flat
                #                                                    with healed-but-invisible OHLC (probe 2026-08-24)
                if w is not None:                                  # stored -> official OHLC wins
                    try:
                        if (abs(float(w.get("open", 0.0)) - o) > 1e-9 or abs(float(w.get("high", 0.0)) - h) > 1e-9
                                or abs(float(w.get("low", 0.0)) - l) > 1e-9
                                or abs(float(w.get("close", 0.0)) - c) > 1e-9):
                            nw = dict(w)
                            nw["open"] = o; nw["high"] = h; nw["low"] = l; nw["close"] = c
                            new[stk] = nw
                            healed += 1
                    except (TypeError, ValueError):
                        pass
                else:                                              # downtime hole -> honest kline-only candle
                    from .time_candles import to_bucket_wire
                    new[stk] = to_bucket_wire({
                        "start_time": float(stk), "end_time": float(stk + secs),
                        "open_price": o, "close_price": c, "high": h, "low": l,
                        "buy_vol": tb, "sell_vol": max(v - tb, 0.0), "curr_vol": v,
                        "poc_price": c, "n_trades": 0, "empty": False, "levels": {}})
                    created += 1
                key = str(stk) if str(stk) in fp else (stk if stk in fp else None)
                nd = fp.get(key) if key is not None else None
                if isinstance(nd, dict) and "open" in nd:          # keep the _ovr source consistent with the heal
                    try:
                        if (abs(float(nd["open"]) - o) > 1e-9 or abs(float(nd["high"]) - h) > 1e-9
                                or abs(float(nd["low"]) - l) > 1e-9 or abs(float(nd["close"]) - c) > 1e-9):
                            nn = dict(nd)
                            nn["open"] = o; nn["high"] = h; nn["low"] = l; nn["close"] = c
                            fp[key] = nn                           # ONE atomic swap per node
                    except (TypeError, ValueError, KeyError):
                        pass
            self._tc_store[tf] = new                               # ONE atomic swap per tf
        if healed or created:
            print(f"CLOCK-CANDLES REST-HEAL: {healed} stale OHLC fixed, {created} downtime candles created.")

    def _tc_save(self, force: bool = False) -> None:
        """Persist the clock-candle store (throttled ~60s; force=True on shutdown). Atomic replace; fully guarded."""
        try:
            now = time.time()
            if not force and now - getattr(self, "_tc_last_save", 0.0) < 60.0:
                return
            self._tc_last_save = now
            if not self._tc_store:
                return
            # dict(store) is an atomic (GIL-held) snapshot -> safe to run off the event loop while catchup_time_candles
            # (on the loop) may be adding a newly-closed candle; no "dict changed size during iteration".
            data = {tf: {str(k): v for k, v in dict(store).items()} for tf, store in list(self._tc_store.items()) if store}
            p = self._tc_path(); tmp = p + ".tmp"
            with gzip.open(tmp, "wt", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, p)                                     # atomic swap (never a torn file)
        except Exception as e:
            print(f"CLOCK-CANDLE SAVE ERROR: {e}")

    def catchup_delta(self, tf: str, since):
        """How many buckets to ship as a DELTA to a client whose cached last-bucket DB-id is ``since``.

        Returns ``n_new`` (>= 0) iff the delta is CONTIGUOUS with this engine's retained window — the
        client's cursor sits in ``[total_closed - len(window) .. total_closed]`` — else ``None`` (the
        daemon then falls back to a full catch-up). ``since`` is client-supplied so it is validated
        defensively: non-int, negative, ahead-of-us, or older than what we still retain all -> ``None``.
        Reads ``total_closed`` / ``closed_buckets`` with no ``await`` between them, so the count matches
        :meth:`catchup_delta_buckets` and :meth:`catchup_start`'s ``total_closed`` exactly."""
        if tf not in self.engines:
            tf = config.DEFAULT_TF
        engine = self.engines[tf]
        try:
            since = int(since)
        except (TypeError, ValueError):
            return None
        if since < 0:
            return None
        n_new = int(engine.total_closed) - since
        nb = len(engine.closed_buckets)
        if 0 <= n_new <= nb:          # behind us by n_new AND we still retain the join point
            return n_new
        return None

    def catchup_delta_buckets(self, tf: str, n_new: int) -> list:
        """The last ``n_new`` closed-bucket snapshots (the delta the client appends). ``n_new<=0`` -> []."""
        if tf not in self.engines:
            tf = config.DEFAULT_TF
        if n_new <= 0:
            return []
        return [b.full_snapshot() for b in self.engines[tf].closed_buckets[-n_new:]]

    def catchup_end(self, tf: str) -> CatchupEndPacket:
        """Frame #N: the live pulsing ``active_bucket`` + the rolling vpin scalar."""
        if tf not in self.engines:
            tf = config.DEFAULT_TF
        engine = self.engines[tf]
        active = engine.active_bucket.live_snapshot(time.time(), engine.avg_velocity)
        return CatchupEndPacket(tf=tf, active_bucket=active, vpin=engine.vpin)

    # ------------------------------------------------------------------
    # Stream: open interest poll (main.py:621)
    # ------------------------------------------------------------------
    async def fetch_oi_loop(self) -> None:
        loop = asyncio.get_event_loop()
        while True:
            try:
                # The synchronous requests.get (~0.28s) used to run ON the event loop every OI_POLL_SECS,
                # hitching the broadcast (the residual ~0.28s/5s gap). Network I/O releases the GIL, so a
                # thread executor truly parallelizes — fetch OFF the loop, apply the result ON the loop.
                res = await loop.run_in_executor(
                    None, lambda: self.session.get(config.REST_OPEN_INTEREST, timeout=3))
                if res.status_code == 200:
                    self._apply_oi(float(res.json().get("openInterest", 0)))
            except Exception:
                pass
            await asyncio.sleep(config.OI_POLL_SECS)

    def _apply_oi(self, new_oi: float) -> None:
        """Publish a fresh OI reading + drive the attributor's poll (Step 19.3).

        Factored out of ``fetch_oi_loop`` so the 19.3 gate exercises the REAL poll
        wiring (``pulse_state`` update + exactly one ``on_poll`` with the symmetric
        global floor reference = the MEDIAN engine ``target_vol``), not a copy.
        """
        self.pulse_state["oi"] = new_oi
        ref = median_target_vol([e.target_vol for e in self.engines.values()])
        self.oi_attr.on_poll(new_oi, ref)

    # ------------------------------------------------------------------
    # Stream: liquidations (main.py:631) — attach to every tf's forming candle
    # ------------------------------------------------------------------
    async def liquidations_stream(self) -> None:
        while True:
            ws = None
            try:
                ws = await websockets.connect(config.WS_LIQUIDATIONS)
                while True:
                    try:
                        raw_msg = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    except asyncio.TimeoutError:
                        continue

                    o = json.loads(raw_msg).get("o", {})
                    if not (o and o.get("s") == config.SYMBOL):
                        continue

                    side = o.get("S")
                    price = float(o.get("p", 0))
                    qty = float(o.get("q", 0))
                    ts_sec = int(o.get("T", 0)) // 1000

                    # Persist to each timeframe's current candle for historical reloads
                    for tf, active_time in self.latest_utime.items():
                        node = self.footprints_db.get(tf, {}).get(active_time)
                        if node is not None:
                            node.setdefault("liquidations", []).append(
                                {"side": side, "price": price, "qty": qty}
                            )
                        # A3b-pre: also attribute the forced order to that tf's live
                        # volume bucket (the previously-dormant b.liquidations list) so
                        # Mode 10's state engine can read per-bucket liq volume. Same
                        # daemon event loop as _process_aggtrade -> race-free.
                        eng = self.engines.get(tf)
                        if eng is not None and eng.active_bucket is not None:
                            eng.active_bucket.liquidations.append(
                                {"side": side, "price": price, "qty": qty}
                            )

                    self.broadcast_all(
                        LiquidationPacket(side=side, price=price, qty=qty, time=ts_sec).to_line()
                    )
            except Exception as e:
                print(f"Liquidation Stream Error: {e}")
                await asyncio.sleep(2)
            finally:
                if ws:
                    try:
                        await ws.close()
                    except Exception:
                        pass

    # ------------------------------------------------------------------
    # Stream: combined kline (all 5 timeframes) -> tick + order blocks
    # ------------------------------------------------------------------
    async def dynamic_stream(self) -> None:
        url = _combined_kline_url()
        while True:
            ws = None
            try:
                ws = await websockets.connect(url)
                while True:
                    res = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    raw_msg = json.loads(res)
                    payload = raw_msg.get("data", raw_msg)
                    if not (payload and "k" in payload):
                        continue
                    k = payload["k"]
                    tf_key = k.get("i")
                    if tf_key in self.engines:
                        # Step 1: hand the event time (payload["E"], epoch ms) to the
                        # quant clock; uTime (candle open) stays the footprint/DB key.
                        self._process_kline(k, tf_key, payload.get("E"))
            except Exception:
                await asyncio.sleep(2)
            finally:
                if ws:
                    try:
                        await ws.close()
                    except Exception:
                        pass

    # ------------------------------------------------------------------
    # Stream: aggTrade (order-by-order tape) -> tick + order blocks (Step 19.3)
    # ------------------------------------------------------------------
    async def aggtrade_stream(self) -> None:
        """Dedicated ``@aggTrade`` websocket -> per-trade routing (Step 19.3).

        Single raw stream (``config.WS_AGGTRADE``). On a RE-connect, resync the OI
        attributor to the current OI: the gap's trades are unreconstructable, so the
        gap's OI delta must not be dumped onto resumed trades (on_reconnect).
        """
        connected_once = False
        while True:
            ws = None
            try:
                ws = await websockets.connect(config.WS_AGGTRADE)
                if connected_once:
                    self.oi_attr.on_reconnect(self.pulse_state.get("oi", 0.0))
                connected_once = True
                while True:
                    res = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    d = json.loads(res)
                    d = d.get("data", d)   # tolerate combined-wrapper; /ws/ is raw
                    if d.get("e") == "aggTrade":
                        if config.DEPTH_CAPTURE_ENABLED:
                            self._capture_trade(d)        # Phase 1 tape tee — AROUND, not inside, the bucket path
                        self._process_aggtrade(d)
            except Exception:
                await asyncio.sleep(2)
            finally:
                if ws:
                    try:
                        await ws.close()
                    except Exception:
                        pass

    def _ensure_fp_node(self, tf_key: str, uTime: str) -> dict:
        """Return the footprint node for (tf, uTime), creating it if absent.

        DIVERGES FROM LEGACY (Step 19.3): the SINGLE node-creation point for BOTH the
        kline (framing) and aggTrade (levels) paths, so whichever event for a candle
        arrives first creates it — and ``oi_open`` is always seeded from the live
        ``pulse_state["oi"]`` here, so an aggTrade-first node can never read a
        default/zero opening OI (clock-coherent OI framing, #1).
        """
        db = self.footprints_db
        if tf_key not in db:
            db[tf_key] = {}
        if uTime not in db[tf_key]:
            oi = self.pulse_state.get("oi", 0.0)
            db[tf_key][uTime] = {
                "lastVol": 0.0,
                "lastTakerBuy": 0.0,
                "levels": {},
                "oi_open": oi,
                "oi_close": oi,
                "liquidations": [],
            }
        return db[tf_key][uTime]

    def _process_kline(self, k: dict, tf_key: str, event_ms: float | None = None) -> None:
        """Candle FRAMING only (Step 19.3): OHLC + oi_close stamp + 1/s heartbeat.

        DIVERGES FROM LEGACY (Step 19.3): klines no longer birth ticks. The
        deltaVol/deltaBuy tick-birth block is gone — ``aggtrade_stream`` ->
        :meth:`_process_aggtrade` now owns level accumulation and ``process_tick`` at
        true trade prices. Klines are retained for OHLC candle framing, the per-candle
        ``oi_close`` timestamp, the candle key/``latest_utime``, and the 1/s live-edge
        ``TickPacket`` heartbeat. ``lastVol``/``lastTakerBuy`` become a kline-volume
        reconciliation record (schema untouched — 19.5 owns any change).
        """
        uTime = str(int(pd.to_datetime(k["t"], unit="ms").timestamp()))
        self.latest_utime[tf_key] = uTime
        fp = self._ensure_fp_node(tf_key, uTime)

        curr_vol = float(k["v"])
        curr_taker_buy = float(k.get("V", 0.0))
        close_price = float(k["c"])

        fp["lastVol"] = curr_vol            # kline-volume reconciliation (no longer a tick source)
        fp["lastTakerBuy"] = curr_taker_buy
        fp["oi_close"] = self.pulse_state.get("oi", 0.0)
        fp["close"] = close_price           # candle close -> absorption close-through invalidation
        fp["open"] = float(k["o"]); fp["high"] = float(k["h"]); fp["low"] = float(k["l"])   # OHLC framing for TIME candles (exact Binance)
        self.latest_live_price = close_price

        candle = {
            "time": int(uTime),
            "open": float(k["o"]),
            "high": float(k["h"]),
            "low": float(k["l"]),
            "close": close_price,
            "volume": curr_vol,
            "taker_buy": curr_taker_buy,
        }
        self.last_candle[tf_key] = candle   # 19.3b: cache for the 150ms live-edge refresh
        if self._tf_subbed(tf_key):         # only build+serialize the heartbeat for a watched tf
            tick = TickPacket(
                tf=tf_key,
                price=close_price,
                candle=candle,
                active_bucket=self.engines[tf_key].active_bucket.live_snapshot(
                    time.time(), self.engines[tf_key].avg_velocity),
                footprint=fp,
                is_closed=bool(k["x"]),
            )
            self.broadcast_tf(tf_key, tick.to_line())

    def _process_aggtrade(self, d: dict) -> None:
        """Route ONE aggTrade into all five engines + footprint levels (Step 19.3).

        The aggTrade is the tick now: true price/qty + EXACT aggressor side (19.1),
        with its OI share from the global pending-balance attributor (19.2). Per
        timeframe: key the node by the integer ``candle_open`` (byte-identical to the
        kline key — #2), add the trade to that node's levels at its TRUE price, and
        feed ``process_tick`` (which also accumulates the bucket's own levels). On a
        bucket close, fire the lightning ``ObPacket`` (moved here from the old kline
        path). NO per-trade broadcast — the live edge stays on the 1/s kline heartbeat
        (the 150 ms refresh is 19.3b).
        """
        targs = trade_to_tick(d)
        if targs.vol <= 0.0:
            return
        share = self.oi_attr.on_trade(targs.vol)         # per-trade delta_oi (Step-2 clamp)
        pStr = f"{targs.price:.2f}"
        side = "b" if targs.taker_buy > 0.0 else "s"     # exact: buy -> b, sell -> s
        t_ms = int(d["T"])
        _sb = config.size_bin(targs.vol)                 # LARGE/SMALL size bin (same for all tfs)
        self._size_win.append((t_ms, targs.vol))         # feed the rolling 60-min size-percentile window
        for tf_key in config.TIMEFRAMES:
            uTime = str(candle_open(t_ms, config.TF_SECONDS[tf_key]))
            self.latest_utime[tf_key] = uTime
            fp = self._ensure_fp_node(tf_key, uTime)
            level = fp["levels"].get(pStr)
            if level is None:
                level = fp["levels"][pStr] = {"b": 0.0, "s": 0.0}
            level[side] += targs.vol

            engine = self.engines[tf_key]
            last_total = engine.total_closed
            engine.process_tick(
                price=targs.price,
                vol=targs.vol,
                taker_buy=targs.taker_buy,
                delta_oi=share,
                footprints_dict=self.footprints_db.get(tf_key, {}),
                tick_time=targs.tick_time,
                size_bin=_sb,
            )
            # CLOCK engine: same tick, clock-boundary close -> full-fidelity time candles (its own bucket levels;
            # footprints_dict is unused by process_tick, so pass an empty throwaway). Guarded: a clock-engine fault
            # must never disturb the live volume-bucket path.
            ce = self.clock_engines.get(tf_key)
            if ce is not None:
                try:
                    ce.process_tick(price=targs.price, vol=targs.vol, taker_buy=targs.taker_buy,
                                    delta_oi=share, footprints_dict={},
                                    tick_time=targs.tick_time, size_bin=_sb)
                except Exception:
                    pass
            # Detect closes by the MONOTONIC total_closed delta, NOT by len(closed_buckets) growth:
            # closed_buckets is capped (append+pop), so its length stops growing at CLOSED_BUCKETS_CAP and
            # the old `len() > last` check silently stopped firing once the cap was reached — freezing the
            # client's scanner history (closes never broadcast until a fresh catch-up on reconnect).
            delta = engine.total_closed - last_total
            if delta > 0 and self._tf_subbed(tf_key):
                # DIVERGES FROM LEGACY (Step 19.4): the per-close path neither RECOMPUTES
                # nor RE-SERIALIZES the OB matrix (both moved to recompute_loop). It only
                # ships the newly closed buckets — order_blocks=[] marks a CLOSE piggyback
                # so the client grows scanner history but leaves its OB matrix untouched.
                # The close path is now O(levels), independent of OB/bucket count -> flat,
                # no stall as history grows. The LAST `delta` entries are exactly the buckets
                # that just closed (append puts them at the tail; the cap-trim pops the FRONT),
                # correct for a single OR multi-bucket close in one tick.
                new_buckets = [b.full_snapshot()
                               for b in engine.closed_buckets[-delta:]]
                self.broadcast_tf(
                    tf_key,
                    ObPacket(tf=tf_key, order_blocks=[],
                             new_buckets=new_buckets, vpin=engine.vpin,
                             total_closed=engine.total_closed).to_line(),
                )
            # LIVE 15m SWEEP PUSH — UNGATED (runs whether or not anyone subscribes to 15m): a 1m client must
            # still get 15m sweeps. Windowed detect on the just-closed 15m bars -> broadcast_all any new Tier-A.
            if delta > 0 and tf_key == LIQ_SWEEP_TF:
                self._emit_liq_sweeps(engine)

    # ------------------------------------------------------------------
    # Live 15m liquidity-sweep push (tf-agnostic; the SAME frozen app.liq_detect the study/terminal use)
    # ------------------------------------------------------------------
    def _liq_scan(self, engine: "QuantEngine", full: bool) -> None:
        """Detect Tier-A sweeps on the 15m closed buckets and record + (live) broadcast any NEW one.
        ``full`` scans the whole history (one-time seed); otherwise a windowed tail (per close). Deduped by
        (idx, side); idx is the ABSOLUTE 15m Idx so seed and live never double-count. O(window) steady-state."""
        from app import liq_detect                          # local import: keep daemon import graph lean
        cb = engine.closed_buckets
        n = len(cb)
        if n < liq_detect.Z_BASE + liq_detect.K + 1:
            return
        win = n if full else min(n, LIQ_SWEEP_WINDOW)
        try:                                                 # NEVER let a bad bucket crash startup or the loop
            tail = [b.full_snapshot() for b in list(cb)[-win:]]  # wire dicts ('close' not 'close_price' — detector copes)
            evs = liq_detect.detect_sweeps(tail)
        except Exception as ex:
            print(f"LIQ SWEEP SCAN ERROR (full={full}): {ex}")
            return
        base_idx = engine.total_closed - len(tail) + 1       # absolute 15m Idx of tail[0]
        for e in evs:
            if e["tier"] != "A":                             # TIER-A ONLY on the wire
                continue
            idx = base_idx + e["i"]
            key = (idx, e["side"])
            if key in self._liq_emitted:
                continue
            self._liq_emitted.add(key)
            d = tail[e["i"]]
            rec = dict(ts=round(float(d.get("end_time", 0.0)), 3), side=e["side"],
                       level=round(float(e["level"]), 4), idx=int(idx))
            self._liq_sweeps.append(rec)
            if not full:                                     # seed is silent; live pushes to everyone
                self.broadcast_all(LiqSweepPacket(**rec).to_line())

    def seed_liq_sweeps(self) -> None:
        """One-time full scan of the rehydrated 15m history so a client connecting before the first live 15m
        close still gets the recent sweeps as catch-up. Called at startup, off the trade loop."""
        if self._liq_seeded:
            return
        eng = self.engines.get(LIQ_SWEEP_TF)
        if eng is not None:
            self._liq_scan(eng, full=True)
        self._liq_seeded = True

    def _emit_liq_sweeps(self, engine: "QuantEngine") -> None:
        self._liq_scan(engine, full=False)

    def liq_sweep_catchup_lines(self) -> list:
        """The current 15m Tier-A set as wire lines — sent to each client on connect (one packet per sweep)."""
        return [LiqSweepPacket(**rec).to_line() for rec in self._liq_sweeps]

    # ------------------------------------------------------------------
    # Phase 1: depth/trade capture tees (O(1), on-loop; bucket path untouched)
    # ------------------------------------------------------------------
    def _capture_depth_diff(self, msg: dict, ob: dict) -> None:
        """Tee a depth diff into the buffer — called AFTER public_stream applies it to local_ob. Whole book
        by default (DEPTH_BAND_PCT<=0); else ±band% of the live mid. qty==0 (a removal) is KEPT — it's a
        real change. Updates _last_depth_u/_ts (the anchor reference). Pure append; never touches buckets."""
        u = int(msg.get("u", 0)); ts = int(msg.get("E", 0))
        self._last_depth_u = u; self._last_depth_ts = ts
        band = config.DEPTH_BAND_PCT
        if band > 0:
            bids, asks = ob["bids"], ob["asks"]
            mid = ((max(bids) + min(asks)) / 2.0) if (bids and asks) else 0.0
            lim = mid * band / 100.0
            keep = (lambda p: abs(p - mid) <= lim) if mid > 0 else (lambda p: True)
            bc = [(float(x[0]), float(x[1])) for x in msg.get("b", []) if keep(float(x[0]))]
            ac = [(float(x[0]), float(x[1])) for x in msg.get("a", []) if keep(float(x[0]))]
        else:
            bc = [(float(x[0]), float(x[1])) for x in msg.get("b", [])]
            ac = [(float(x[0]), float(x[1])) for x in msg.get("a", [])]
        if bc or ac:
            self._depth_delta_buf.append((ts, u, bc, ac))

    def _capture_depth_snapshot(self, ob: dict) -> None:
        """Buffer a full (or banded) book ANCHOR — a read of local_ob on the loop, stamped with the last
        applied diff's u so reconstruction replays deltas with u > anchor.u. Called every
        DEPTH_SNAPSHOT_SECS and once per diff-stream (re)connect (so the chain never has an un-anchored gap)."""
        bids, asks = ob["bids"], ob["asks"]
        if not bids or not asks:
            return
        mid = (max(bids) + min(asks)) / 2.0
        band = config.DEPTH_BAND_PCT
        if band > 0:
            lim = mid * band / 100.0
            b = {p: q for p, q in bids.items() if abs(p - mid) <= lim}
            a = {p: q for p, q in asks.items() if abs(p - mid) <= lim}
        else:
            b = dict(bids); a = dict(asks)
        self._depth_snap_buf.append((int(time.time() * 1000), self._last_depth_u, mid, b, a))

    def _capture_trade(self, d: dict) -> None:
        """Tee one aggTrade into the trade-tape buffer — called in aggtrade_stream AROUND (never inside)
        _process_aggtrade. side: 1 = taker buy, 0 = taker sell (Binance 'm' = buyer-maker flag)."""
        ts = int(d.get("T", 0)); price = float(d.get("p", 0.0))
        qty = float(d.get("q", 0.0)); side = 0 if d.get("m") else 1
        self._trade_buf.append((int(d.get("a", 0)), ts, price, qty, side))
        self._trades_live_buf.append((ts, price, qty, side))   # Phase 3: live bubble batch (drained per pulse)

    def drain_trades_live(self) -> list:
        """Drain the live-trades buffer ON the loop (O(n) ref-copy + clear; n ≈ trades/pulse, ~tens). Feeds the
        Phase 3 live TradeBatch push."""
        buf = list(self._trades_live_buf)
        self._trades_live_buf.clear()
        return buf

    async def depth_snapshot_loop(self) -> None:
        """Buffer a full-book anchor every DEPTH_SNAPSHOT_SECS (reconnect anchors are added in
        public_stream). Read-only of local_ob; gated by the master switch."""
        while True:
            await asyncio.sleep(config.DEPTH_SNAPSHOT_SECS)
            if not config.DEPTH_CAPTURE_ENABLED:
                continue
            try:
                self._capture_depth_snapshot(self.pulse_state["local_ob"])
            except Exception as e:
                print(f"DEPTH SNAPSHOT ERROR: {e}")

    # ------------------------------------------------------------------
    # Stream: order book depth maintenance (main.py:832)
    # ------------------------------------------------------------------
    async def public_stream(self) -> None:
        ob = self.pulse_state["local_ob"]
        try:
            res = self.session.get(config.REST_DEPTH, timeout=5)
            res.raise_for_status()
            data = res.json()
        except Exception:
            data = {"lastUpdateId": 0, "bids": [], "asks": []}

        ob["lastUpdateId"] = data.get("lastUpdateId", 0)
        ob["bids"] = {float(b[0]): float(b[1]) for b in data.get("bids", [])}
        ob["asks"] = {float(a[0]): float(a[1]) for a in data.get("asks", [])}
        self._last_depth_u = ob["lastUpdateId"]   # Phase 1: anchor reference for the first snapshot

        while True:
            ws = None
            try:
                ws = await websockets.connect(config.WS_DEPTH)
                if config.DEPTH_CAPTURE_ENABLED:
                    self._capture_depth_snapshot(ob)   # anchor on every (re)connect — no un-anchored gap
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("u", 0) < ob["lastUpdateId"]:
                        continue
                    for b in msg.get("b", []):
                        p, q = float(b[0]), float(b[1])
                        if q == 0:
                            ob["bids"].pop(p, None)
                        else:
                            ob["bids"][p] = q
                    for a in msg.get("a", []):
                        p, q = float(a[0]), float(a[1])
                        if q == 0:
                            ob["asks"].pop(p, None)
                        else:
                            ob["asks"][p] = q
                    if config.DEPTH_CAPTURE_ENABLED:
                        self._capture_depth_diff(msg, ob)   # tee AFTER applying — lossless per-diff record
            except Exception:
                await asyncio.sleep(2)
            finally:
                if ws:
                    try:
                        await ws.close()
                    except Exception:
                        pass

    # ------------------------------------------------------------------
    # Stream: depth + OI pulse broadcast (main.py:875)
    # ------------------------------------------------------------------
    def _recompute_size_thr(self, now_ms: int) -> None:
        """Prune the rolling window to 60 min and recompute the size percentiles [p50,p90,p95,p99,p99.5]
        (contracts). np.percentile (method='linear') on ~7k floats is sub-ms on the pulse loop and touches
        NO per-close path. Cold-start: ship [] until SIZE_THR_MIN_SAMPLES so the terminal never thresholds a
        half-warm distribution."""
        win = self._size_win
        cutoff = now_ms - config.SIZE_PCTILE_WINDOW_MS
        while win and win[0][0] < cutoff:
            win.popleft()
        n = len(win)
        if n < config.SIZE_THR_MIN_SAMPLES:
            self._size_thr = []
            return
        qs = np.fromiter((q for _, q in win), dtype=float, count=n)
        self._size_thr = [float(v) for v in np.percentile(qs, (50.0, 90.0, 95.0, 99.0, 99.5))]

    async def pulse_broadcast_loop(self) -> None:
        ob = self.pulse_state["local_ob"]
        while True:
            await asyncio.sleep(config.PULSE_BROADCAST_SECS)
            now_ms = int(time.time() * 1000)
            if now_ms - self._size_thr_t >= config.SIZE_THR_RECOMPUTE_SECS * 1000.0:
                self._recompute_size_thr(now_ms)
                self._size_thr_t = now_ms
            sorted_bids = sorted(ob["bids"].items(), key=lambda x: x[0], reverse=True)[:config.DOM_LEVELS]
            sorted_asks = sorted(ob["asks"].items(), key=lambda x: x[0])[:config.DOM_LEVELS]
            self.broadcast_all(
                PulsePacket(
                    bids=[[str(k_), str(v_)] for k_, v_ in sorted_bids],
                    asks=[[str(k_), str(v_)] for k_, v_ in sorted_asks],
                    oi=self.pulse_state.get("oi", 0.0),
                    size_thr=list(self._size_thr),
                ).to_line()
            )

    # ------------------------------------------------------------------
    # Periodic recalibrate + OB rescan — OFF the per-close hot path (Step 19.4)
    # ------------------------------------------------------------------
    def _resize_engines(self) -> None:
        """Median-anchored bucket sizing for ALL timeframes — one mechanism, applied atomically
        each recompute sweep. Replaces the old variance optimizer, which on real data hit its
        ``0.5 * avg_vol`` search floor ~85% of the time and lurched 50–77% chasing volume bursts.

        The 1m engine is the sole anchor::

            target_vol[1m] = BUCKET_MEDIAN_CANDLES * median(per-1m-candle volume)
            target_vol[tf] = target_vol[1m] * (tf_seconds / 60)

        Median over the *in-RAM* 1m footprints (``footprints_db['1m']``, already capped at
        ``FOOTPRINT_MEM_CAP``) is immune to the ~2x right-skew that the old mean chased, and it
        can NEVER read pruned data — it only ever sees what is retained, so the window IS the
        retention knob, not a new magic constant. Every other tf is a deterministic multiple of
        the one robust anchor, so all five are co-stable and the sparse-high-tf-candle problem
        (4h can never gather 10 candles in any sane window) is sidestepped entirely.
        """
        vols = []
        for node in self.footprints_db.get("1m", {}).values():
            v = sum(c.get("b", 0.0) + c.get("s", 0.0) for c in node.get("levels", {}).values())
            if v > 0:
                vols.append(v)
        if len(vols) < 10:                      # cold boot / too few candles -> leave sizes untouched
            return
        anchor = config.BUCKET_MEDIAN_CANDLES * statistics.median(vols)
        if anchor <= 0:
            return
        for tf_key, engine in self.engines.items():
            sec = config.TF_SECONDS.get(tf_key)
            if sec:
                engine.target_vol = anchor * (sec / 60.0)   # atomic float write (single asyncio loop)

    def _recompute_engine(self, tf_key: str) -> list:
        """Rescan order blocks for one engine (Step 19.4). Bucket sizing is hoisted out to
        :meth:`_resize_engines` — one median anchor sizes all tfs — so this is OB-rescan only.
        SYNCHRONOUS (no await) so it stays atomic w.r.t. that engine's closes on the single
        asyncio loop. Returns the fresh OB set for ``recompute_loop`` to broadcast.
        """
        return rank_obs(calc_quant_obs(self.engines[tf_key], tf_key))

    def _absorption_marks(self, tf_key: str) -> list:
        """Whale-absorption standing levels for one tf — BUCKET-NATIVE: replayed over the engine's
        closed VOLUME BUCKETS (the same axis as the OB matrix), so detection and display share one
        volume-bucket axis. Stateless like the OB matrix; marks anchor on bucket start_times and
        die on bucket close-throughs."""
        eng = self.engines.get(tf_key)
        if eng is None or not eng.closed_buckets:
            return []
        return calc_absorption(eng.closed_buckets)

    # -- Step A: OB-rescan process pool (off-loop) + stable on-loop fallback ----
    def _ensure_ob_pool(self) -> Optional[ProcessPoolExecutor]:
        """A live spawn pool, or None to signal on-loop. Permanently None after _OB_POOL_MAX_FAILS failures."""
        if self._ob_pool_disabled:
            return None
        if self._ob_pool is None:
            try:
                self._ob_pool = ProcessPoolExecutor(
                    max_workers=1, mp_context=multiprocessing.get_context("spawn"))
            except Exception as e:
                self._note_ob_pool_fail(f"create {type(e).__name__}: {e}")
                return None
        return self._ob_pool

    def _note_ob_pool_fail(self, why: str) -> None:
        """Tear down the broken pool (reaps the worker -> no process leak), count it, and PERMANENTLY degrade
        to on-loop after _OB_POOL_MAX_FAILS — STABLE: it never thrash-recreates a doomed pool forever."""
        pool, self._ob_pool = self._ob_pool, None
        if pool is not None:
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
        self._ob_pool_fails += 1
        if self._ob_pool_fails >= _OB_POOL_MAX_FAILS:
            self._ob_pool_disabled = True
            print(f"OB POOL permanently DISABLED after {self._ob_pool_fails} failures — recompute is now stable "
                  f"on-loop (= pre-offload behavior; daemon stays up, no thrash). last: {why}")
        else:
            print(f"OB POOL failure {self._ob_pool_fails}/{_OB_POOL_MAX_FAILS} ({why}) — on-loop this cycle, retry next.")

    async def _recompute_ob_line_async(self, loop, buckets: list, tf_key: str, vpin: float) -> str:
        """Off-loop via the spawn pool when healthy; degrade to ON-LOOP (the pre-offload freeze) on ANY pool
        failure — the daemon never goes down, worst case is exactly today's behavior."""
        pool = self._ensure_ob_pool()
        if pool is not None:
            try:
                return await loop.run_in_executor(pool, _recompute_ob_line, buckets, tf_key, vpin)
            except Exception as e:
                self._note_ob_pool_fail(f"{type(e).__name__}: {e}")
        return _recompute_ob_line(buckets, tf_key, vpin)   # on-loop fallback

    async def warm_ob_pool(self) -> None:
        """Spawn the worker at daemon boot (off the critical path) so the first OB refresh isn't delayed."""
        try:
            loop = asyncio.get_event_loop()
            pool = self._ensure_ob_pool()
            if pool is not None:
                await loop.run_in_executor(pool, _ob_pool_warmup)
                print("OB POOL warm — worker process up (2nd core).")
        except Exception as e:
            self._note_ob_pool_fail(f"warm {type(e).__name__}: {e}")

    def shutdown_ob_pool(self, timeout: float = 5.0) -> None:
        """Tear the OB process pool down cleanly on daemon shutdown so multiprocessing doesn't double-unlink
        its named semaphores (the FileNotFoundError / 'leaked semaphore' warnings on restart). BOUNDED by a
        timeout in a daemon thread, so a worker stuck mid-recompute can NEVER block SIGTERM handling — if it
        exceeds the bound we leave the OS to reap and let the restart proceed."""
        pool, self._ob_pool = self._ob_pool, None
        self._ob_pool_disabled = True   # don't let anything recreate it during shutdown
        if pool is None:
            return
        import threading
        t = threading.Thread(target=lambda: pool.shutdown(wait=True), daemon=True)
        t.start(); t.join(timeout)
        print("OB POOL shut down cleanly." if not t.is_alive()
              else f"OB POOL shutdown exceeded {timeout}s — leaving to OS reap (restart not blocked).")

    async def recompute_loop(self) -> None:
        """Periodic OB rescan, DECOUPLED from per-close (19.4) and run OFF the event loop (Step A).

        The rescan is a PURE function of the closed buckets, so it runs on a worker PROCESS (the spawn pool,
        2nd core) and NEVER blocks the broadcast loop — regardless of timeframe (4h) or market speed.
        Subscription-gating + skip-if-unchanged in front (the pool is hit only on a real close); on ANY pool
        failure it degrades to on-loop (the pre-offload behavior), permanently after repeated failures.
        """
        loop = asyncio.get_event_loop()
        last_closed: Dict[str, int] = {}   # tf -> engine.total_closed at its last rescan
        while True:
            await asyncio.sleep(config.RECOMPUTE_SECS)
            self._resize_engines()                 # median-anchored sizing for all tfs (light, on-loop)
            for tf_key in config.TIMEFRAMES:
                if not self._tf_subbed(tf_key):    # nobody watching this tf -> skip the heavy rescan+serialize
                    continue
                eng = self.engines[tf_key]
                if last_closed.get(tf_key) == eng.total_closed:   # nothing closed -> OB set unchanged, skip
                    continue
                last_closed[tf_key] = eng.total_closed
                try:
                    buckets = list(eng.closed_buckets)   # IMMUTABLE snapshot ON the loop (cheap ref-copy)
                    line = await self._recompute_ob_line_async(loop, buckets, tf_key, eng.vpin)
                    self.broadcast_tf(tf_key, line)
                except Exception as e:
                    print(f"RECOMPUTE ERROR ({tf_key}): {e}")
                await asyncio.sleep(0)   # yield between engines (covers the on-loop fallback path)

    # ------------------------------------------------------------------
    # Live-edge refresh — sub-second forming bucket, decoupled from trades (19.3b)
    # ------------------------------------------------------------------
    def _broadcast_live_edge(self) -> None:
        """Re-emit each tf's forming edge (cached candle + FRESH active bucket) — 19.3b.

        Reads current state only — NEVER fires per trade. Reuses TickPacket (no wire
        change): the cached last candle stands in for OHLC between the 1/s klines while
        the active bucket + forming footprint pulse sub-second. Bounded rate
        (1/LIVE_EDGE_SECS per tf), fully decoupled from the aggTrade message rate.
        """
        now = time.time()
        for tf_key, candle in self.last_candle.items():
            if not self._tf_subbed(tf_key):    # skip serializing the forming footprint (huge on 1h/4h) nobody wants
                continue
            engine = self.engines[tf_key]
            fp = self.footprints_db.get(tf_key, {}).get(self.latest_utime.get(tf_key, ""), {})
            tick = TickPacket(
                tf=tf_key,
                price=(self.latest_live_price if self.latest_live_price is not None
                       else candle.get("close", 0.0)),
                candle=candle,
                active_bucket=engine.active_bucket.live_snapshot(now, engine.avg_velocity),
                footprint=fp,
                is_closed=False,
            )
            self.broadcast_tf(tf_key, tick.to_line())

    def _time_wire_closed(self, tf: str, b, kln: dict) -> "Optional[dict]":
        """Build + store a CLOSED clock candle's wire, kline-OHLC-overridden. Shares the persistent store.
        ⚠ The override is RE-APPLIED on every call (2026-08-24): the first build happens ~150ms after the close,
        BEFORE Binance's final kline for that minute exists — the old build-once cache froze the ENGINE close forever
        (engine closes run a few ticks stale vs the official kline), so every next candle visibly gapped its open vs
        the stale prev close (wire-measured: our closes 95.61/95.62 vs Binance 95.64/95.66 while opens matched).
        Re-applying is idempotent and per-key cheap; the 12s re-push then delivers the corrected close to clients."""
        stk = int(b.start_time) if b.start_time is not None else None
        if stk is None:
            return None
        store = self._tc_store.setdefault(tf, {})
        w = store.get(stk)
        if w is None:
            w = b.full_snapshot()
            store[stk] = w
        node = kln.get(stk)
        if node is not None:
            try:
                w["open"] = float(node["open"]); w["high"] = float(node["high"])
                w["low"] = float(node["low"]); w["close"] = float(node["close"])
            except (TypeError, ValueError, KeyError):
                pass
        return w

    def mark_time_pushed(self, tf: str) -> None:
        """On a fresh sub_time the client just received the full catch-up, so start the delta cursor at the current
        close-count -> the live-edge loop only pushes candles that close AFTER the subscribe (no giant re-push)."""
        try:
            ce = self.clock_engines.get(tf)
            if ce is not None:
                self._time_push_n[tf] = len(ce.closed_buckets)
        except Exception:
            pass

    def _broadcast_time_edge(self) -> None:
        """Push each TIME-subscribed tf's forming clock candle (every pulse) + any newly-CLOSED candle (when it closes)
        — small delta frames, so clock candles are PUSHED real-time (no poll lag). On-loop like the bucket edge; the
        one-time full set still comes from sub_time/get_time_candles. Fully guarded (a fault never breaks bucket push)."""
        from .protocol import TimeCandlesPacket
        now = time.time()
        for tf in config.TIMEFRAMES:
            if not self._time_subbed(tf):
                continue
            ce = self.clock_engines.get(tf)
            if ce is None:
                continue
            try:
                self._tc_load()
                fp = self.footprints_db.get(tf) or {}

                class _KLN:
                    """Lazy per-key view over footprints_db. The old code built a FULL {int(ut): node} dict from the
                    entire footprints DB EVERY pulse PER tf — an O(DB) cost on the 0.15s live-edge loop that stalled
                    it to a ~4.5s burst cadence (measured on the wire 2026-08-24; the clock-chart lag root cause).
                    _time_wire_closed only ever looks up ONE key, so resolve per key instead."""
                    def __init__(self, _fp):
                        self._fp = _fp

                    def get(self, stk):
                        node = self._fp.get(str(stk))
                        if node is None:
                            node = self._fp.get(stk)
                        return node if (isinstance(node, dict) and "open" in node) else None
                kln = _KLN(fp)
                push = []
                ncur = len(ce.closed_buckets)
                last = self._time_push_n.get(tf)
                if last is None:                              # first pulse for this tf -> no backfill (client has catchup)
                    last = self._time_push_n[tf] = ncur
                if ncur > last:                               # candle(s) closed since last pulse -> push them
                    for b in list(ce.closed_buckets)[last:ncur]:
                        w = self._time_wire_closed(tf, b, kln)
                        if w is not None:
                            push.append(w)
                    self._time_push_n[tf] = ncur
                elif ncur >= 1:
                    # RE-PUSH heal (2026-08-24): a close frame dropped by a client's bounded send queue is otherwise
                    # NEVER re-sent (advance-only cursor) -> that client keeps a stale partial candle forever (its next
                    # open != prev close) or misses the candle outright. Re-pushing the freshest close for a few
                    # seconds after it closed is IDEMPOTENT (clients merge by start_time) and heals a drop in ~1 pulse.
                    b = list(ce.closed_buckets)[-1]
                    _st = float(getattr(b, "start_time", 0) or 0)
                    if _st and (now - (_st + config.TF_SECONDS.get(tf, 60))) < 12.0:
                        w = self._time_wire_closed(tf, b, kln)
                        if w is not None:
                            push.append(w)
                ab = ce.active_bucket                          # + always the live forming candle
                if ab.start_time is not None and ab.curr_vol > 0:
                    push.append(ab.live_snapshot(now, ce.avg_velocity))
                if push:
                    self.broadcast_time(tf, TimeCandlesPacket(tf=tf, seq=0, candles=push).to_line())
            except Exception:
                continue

    async def live_edge_loop(self) -> None:
        """Broadcast the forming edge every LIVE_EDGE_SECS, decoupled from trade rate (19.3b)."""
        while True:
            await asyncio.sleep(config.LIVE_EDGE_SECS)
            try:
                self._broadcast_live_edge()
            except Exception as e:
                print(f"LIVE EDGE ERROR: {e}")
            try:
                self._broadcast_time_edge()      # PUSH clock candles to time-subscribers (real-time, no poll)
            except Exception as e:
                print(f"TIME EDGE ERROR: {e}")

    # ------------------------------------------------------------------
    def start_tasks(self) -> list[asyncio.Task]:
        tasks = [
            asyncio.create_task(self.fetch_oi_loop()),
            asyncio.create_task(self.liquidations_stream()),
            asyncio.create_task(self.dynamic_stream()),
            asyncio.create_task(self.aggtrade_stream()),
            asyncio.create_task(self.public_stream()),
            asyncio.create_task(self.pulse_broadcast_loop()),
            asyncio.create_task(self.recompute_loop()),
            asyncio.create_task(self.live_edge_loop()),
            asyncio.create_task(self.warm_ob_pool()),   # Step A: spawn the OB worker at boot (off critical path)
        ]
        if config.DEPTH_CAPTURE_ENABLED:   # Phase 1: full-book anchor cadence (the DepthStore sync loop is
            tasks.append(asyncio.create_task(self.depth_snapshot_loop()))   # wired in daemon.serve())
        return tasks
