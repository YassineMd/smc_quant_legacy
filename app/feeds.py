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
import time
from typing import Callable, Dict

import pandas as pd
import requests
import websockets
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import config
from .protocol import (CatchupEndPacket, CatchupPacket, CatchupStartPacket,
                       LiquidationPacket, ObPacket, PulsePacket, TickPacket)
from .aggtrade import OiAttributor, candle_open, median_target_vol, trade_to_tick
from .quant_engine import QuantEngine, build_engine_registry, calc_quant_obs, rank_obs

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


class MarketDataCore:
    """Single point of contact for all external exchange data (spec §1.2.1)."""

    def __init__(self, footprints_db: Dict[str, dict],
                 broadcast_tf: Callable[[str, str], None],
                 broadcast_all: Callable[[str], None]):
        self.footprints_db = footprints_db
        self.broadcast_tf = broadcast_tf
        self.broadcast_all = broadcast_all
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
        while True:
            try:
                res = self.session.get(config.REST_OPEN_INTEREST, timeout=3)
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
            last_bucket_count = len(engine.closed_buckets)
            engine.process_tick(
                price=targs.price,
                vol=targs.vol,
                taker_buy=targs.taker_buy,
                delta_oi=share,
                footprints_dict=self.footprints_db.get(tf_key, {}),
                tick_time=targs.tick_time,
            )
            if len(engine.closed_buckets) > last_bucket_count:
                # DIVERGES FROM LEGACY (Step 19.4): the per-close path neither RECOMPUTES
                # nor RE-SERIALIZES the OB matrix (both moved to recompute_loop). It only
                # ships the newly closed buckets — order_blocks=[] marks a CLOSE piggyback
                # so the client grows scanner history but leaves its OB matrix untouched.
                # The close path is now O(levels), independent of OB/bucket count -> flat,
                # no stall as history grows.
                new_buckets = [b.full_snapshot()
                               for b in engine.closed_buckets[last_bucket_count:]]
                self.broadcast_tf(
                    tf_key,
                    ObPacket(tf=tf_key, order_blocks=[],
                             new_buckets=new_buckets, vpin=engine.vpin).to_line(),
                )

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

        while True:
            ws = None
            try:
                ws = await websockets.connect(config.WS_DEPTH)
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
    def _recompute_engine(self, tf_key: str) -> list:
        """Recalibrate ``target_vol`` + rescan order blocks for one engine (Step 19.4).

        The expensive O(2h-window) optimizer + O(history) OB rescan that USED to run
        synchronously on every bucket close. SYNCHRONOUS (no await) so it is atomic
        w.r.t. that engine's closes on the single asyncio loop — no torn read is
        possible. The only write back into shared state is one atomic assignment,
        ``engine.target_vol`` (a float, inside ``recalibrate``). Returns the fresh OB
        set for ``recompute_loop`` to broadcast (the per-close path never ships it).
        """
        engine = self.engines[tf_key]
        engine.recalibrate(engine.last_tick_time, self.footprints_db.get(tf_key, {}))
        return rank_obs(calc_quant_obs(engine, tf_key))

    async def recompute_loop(self) -> None:
        """Periodic recalibrate + OB rescan, DECOUPLED from per-close (Step 19.4).

        Each engine's :meth:`_recompute_engine` runs as a single synchronous block, so
        no consumer can observe a half-updated ``target_vol`` or a torn OB set. Yields
        between engines so the full sweep never starves the socket drain, and
        broadcasts the refreshed OB matrix for each timeframe.
        """
        while True:
            await asyncio.sleep(config.RECOMPUTE_SECS)
            for tf_key in config.TIMEFRAMES:
                try:
                    obs = self._recompute_engine(tf_key)
                    self.broadcast_tf(
                        tf_key,
                        ObPacket(tf=tf_key, order_blocks=obs, new_buckets=[],
                                 vpin=self.engines[tf_key].vpin).to_line(),
                    )
                except Exception as e:
                    print(f"RECOMPUTE ERROR ({tf_key}): {e}")
                await asyncio.sleep(0)   # yield between engines — don't starve the drain

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
        return [
            asyncio.create_task(self.fetch_oi_loop()),
            asyncio.create_task(self.liquidations_stream()),
            asyncio.create_task(self.dynamic_stream()),
            asyncio.create_task(self.aggtrade_stream()),
            asyncio.create_task(self.public_stream()),
            asyncio.create_task(self.pulse_broadcast_loop()),
            asyncio.create_task(self.recompute_loop()),
            asyncio.create_task(self.live_edge_loop()),
        ]
