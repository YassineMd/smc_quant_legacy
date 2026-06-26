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
import json
import multiprocessing
import statistics
import time
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from types import SimpleNamespace
from typing import Callable, Dict, Optional

import pandas as pd
import requests
import websockets
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import config
from .protocol import (CatchupEndPacket, CatchupPacket, CatchupStartPacket,
                       LiquidationPacket, ObPacket, PulsePacket, TickPacket)
from .aggtrade import OiAttributor, candle_open, median_target_vol, trade_to_tick
from .quant_engine import (QuantEngine, build_engine_registry, calc_absorption,
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
                 tf_has_subscribers: Optional[Callable[[str], bool]] = None):
        self.footprints_db = footprints_db
        self.broadcast_tf = broadcast_tf
        self.broadcast_all = broadcast_all
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
        self._depth_snap_buf: deque = deque(maxlen=1000)
        self._last_depth_u: int = 0
        self._last_depth_ts: int = 0

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
        )

    # ------------------------------------------------------------------
    # Chunked CATCHUP builders (the daemon orchestrates START -> CHUNKs -> END)
    # ------------------------------------------------------------------
    def catchup_start(self, tf: str) -> CatchupStartPacket:
        """Frame #1: target_vol + the current OB matrix + recent footprints +
        the total bucket count (client renders these immediately + shows progress)."""
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
            footprints=footprints, total_buckets=len(engine.closed_buckets))

    def catchup_buckets(self, tf: str) -> list:
        """The full closed-bucket snapshot list; the daemon slices it into
        ``CATCHUP_CHUNK_SIZE`` batches, each shipped as a CATCHUP_CHUNK frame."""
        if tf not in self.engines:
            tf = config.DEFAULT_TF
        return [b.full_snapshot() for b in self.engines[tf].closed_buckets]

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
            )
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
                             new_buckets=new_buckets, vpin=engine.vpin).to_line(),
                )

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
        self._trade_buf.append((
            int(d.get("a", 0)), int(d.get("T", 0)),
            float(d.get("p", 0.0)), float(d.get("q", 0.0)),
            0 if d.get("m") else 1,
        ))

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
    async def pulse_broadcast_loop(self) -> None:
        ob = self.pulse_state["local_ob"]
        while True:
            await asyncio.sleep(config.PULSE_BROADCAST_SECS)
            sorted_bids = sorted(ob["bids"].items(), key=lambda x: x[0], reverse=True)[:config.DOM_LEVELS]
            sorted_asks = sorted(ob["asks"].items(), key=lambda x: x[0])[:config.DOM_LEVELS]
            self.broadcast_all(
                PulsePacket(
                    bids=[[str(k_), str(v_)] for k_, v_ in sorted_bids],
                    asks=[[str(k_), str(v_)] for k_, v_ in sorted_asks],
                    oi=self.pulse_state.get("oi", 0.0),
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

    async def live_edge_loop(self) -> None:
        """Broadcast the forming edge every LIVE_EDGE_SECS, decoupled from trade rate (19.3b)."""
        while True:
            await asyncio.sleep(config.LIVE_EDGE_SECS)
            try:
                self._broadcast_live_edge()
            except Exception as e:
                print(f"LIVE EDGE ERROR: {e}")

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
