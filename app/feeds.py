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
                    self.pulse_state["oi"] = float(res.json().get("openInterest", 0))
            except Exception:
                pass
            await asyncio.sleep(config.OI_POLL_SECS)

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

    def _process_kline(self, k: dict, tf_key: str, event_ms: float | None = None) -> None:
        """Footprint accumulation + quant tick + broadcast (main.py:733)."""
        uTime = str(int(pd.to_datetime(k["t"], unit="ms").timestamp()))
        self.latest_utime[tf_key] = uTime

        db = self.footprints_db
        if tf_key not in db:
            db[tf_key] = {}
        if uTime not in db[tf_key]:
            db[tf_key][uTime] = {
                "lastVol": 0.0,
                "lastTakerBuy": 0.0,
                "levels": {},
                "oi_open": self.pulse_state.get("oi", 0.0),
                "oi_close": self.pulse_state.get("oi", 0.0),
                "liquidations": [],
            }
        fp = db[tf_key][uTime]

        curr_vol = float(k["v"])
        curr_taker_buy = float(k.get("V", 0.0))
        deltaVol = curr_vol - fp.get("lastVol", 0.0)
        deltaBuy = curr_taker_buy - fp.get("lastTakerBuy", 0.0)
        deltaSell = deltaVol - deltaBuy

        close_price = float(k["c"])
        pStr = f"{close_price:.2f}"

        current_oi = self.pulse_state.get("oi", 0.0)
        delta_oi = current_oi - fp.get("oi_close", current_oi)

        if deltaVol > 0:
            if pStr not in fp["levels"]:
                fp["levels"][pStr] = {"b": 0.0, "s": 0.0}
            fp["levels"][pStr]["b"] += max(0.0, deltaBuy)
            fp["levels"][pStr]["s"] += max(0.0, deltaSell)

            engine = self.engines[tf_key]
            last_bucket_count = len(engine.closed_buckets)

            # DIVERGES FROM LEGACY (Step 2): clamp the ΔOI / taker-buy sampling
            # artifacts at the feeds boundary (engine math stays untouched).
            #   * OI is polled every 5s while klines push ~1/s, so one push can
            #     absorb several seconds of OI change against a single push's
            #     volume -> |delta_oi| > deltaVol -> the 4-vector ratios exceed 1
            #     and opL+opS+clL+clS > curr_vol. An OI change cannot physically
            #     exceed the volume that produced it; clamp to [-deltaVol, deltaVol].
            #   * clamp taker_buy into [0, deltaVol] so b_ratio = taker_buy/vol and
            #     s_ratio stay in [0,1] even on a non-monotonic frame (deltaBuy >
            #     deltaVol); the legacy max(0.0, .) only guarded the lower bound.
            clamped_oi = max(-deltaVol, min(deltaVol, delta_oi))
            clamped_taker = max(0.0, min(deltaVol, deltaBuy))

            engine.process_tick(
                price=close_price,
                vol=deltaVol,
                taker_buy=clamped_taker,
                delta_oi=clamped_oi,
                footprints_dict=db.get(tf_key, {}),
                # DIVERGES FROM LEGACY: event-time clock (payload["E"]/1000) instead
                # of int(uTime) (candle open). uTime still keys the footprint DB above.
                tick_time=(event_ms / 1000.0) if event_ms is not None else time.time(),
            )

            # Lightning trigger: one or more buckets just closed -> recompute OBs
            # and piggyback every newly-closed bucket's full vectors (Option A) so
            # the terminal's scanner history grows without a separate frame type.
            if len(engine.closed_buckets) > last_bucket_count:
                fresh_obs = rank_obs(calc_quant_obs(engine, tf_key))
                new_buckets = [b.full_snapshot()
                               for b in engine.closed_buckets[last_bucket_count:]]
                self.broadcast_tf(
                    tf_key,
                    ObPacket(tf=tf_key, order_blocks=fresh_obs,
                             new_buckets=new_buckets, vpin=engine.vpin).to_line(),
                )

        fp["lastVol"] = curr_vol
        fp["lastTakerBuy"] = curr_taker_buy
        fp["oi_close"] = self.pulse_state.get("oi", 0.0)
        self.latest_live_price = close_price

        tick = TickPacket(
            tf=tf_key,
            price=close_price,
            candle={
                "time": int(uTime),
                "open": float(k["o"]),
                "high": float(k["h"]),
                "low": float(k["l"]),
                "close": close_price,
                "volume": curr_vol,
                "taker_buy": curr_taker_buy,
            },
            active_bucket=self.engines[tf_key].active_bucket.live_snapshot(
                time.time(), self.engines[tf_key].avg_velocity),
            footprint=fp,
            is_closed=bool(k["x"]),
        )
        self.broadcast_tf(tf_key, tick.to_line())

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
    def start_tasks(self) -> list[asyncio.Task]:
        return [
            asyncio.create_task(self.fetch_oi_loop()),
            asyncio.create_task(self.liquidations_stream()),
            asyncio.create_task(self.dynamic_stream()),
            asyncio.create_task(self.public_stream()),
            asyncio.create_task(self.pulse_broadcast_loop()),
        ]
