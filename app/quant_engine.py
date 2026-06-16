"""Tier-1 pure quantitative core.

This is the one module shared, byte-for-byte in its math, between the daemon and
(for multi-timeframe overlay calc) the terminal. Everything here is a VERBATIM
port of the legacy ``main.py`` quant routines, with two surgical additions noted
explicitly:

  1. VPIN flow-toxicity queue (spec §3.4). Legacy ``main.py`` shipped only
     ``rolling_velocity`` / ``vel_ratio``; the order-block engine depends on that
     and is left untouched. VPIN is added as a *parallel* rolling queue so the
     toxicity display has its mandated metric without perturbing OB detection.
  2. Pure-stdlib timestamp helpers (no pandas) so the core carries zero heavy
     dependencies. The UTC round-trip is numerically identical to the legacy
     pandas path: forward = utcfromtimestamp().strftime, reverse = strptime in
     UTC. The per-tick hot path contains no datetime work at all.

PRESERVE-VERBATIM anchors (spec §10.2.1):
    QuantEngine.process_tick        — 4-vector OpL/OpS/ClL/ClS split
    QuantEngine._close_active_bucket — tick-count E/R against 20-bucket lookback
    calculate_dynamic_band          — 50-step Otsu + first-derivative expansion
    calc_quant_obs                  — instant mitigation on edge pierce
"""

from __future__ import annotations

import statistics
import time
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List

from . import config
from .protocol import BucketSnapshot

# DIVERGES FROM LEGACY (Step 1, rule 0.6/2): velocity warm-up gate. vel_ratio
# stays neutral (1.0) until the rolling window holds at least this many real
# samples, so a cold or under-filled deque cannot emit a misleading ratio. Tied
# to VELOCITY_LOOKBACK so it scales with the window instead of being a separate
# magic number.
_VEL_WARMUP_MIN = max(2, config.VELOCITY_LOOKBACK // 4)

# ---------------------------------------------------------------------------
# Stdlib timestamp helpers (replace pandas clean_ts; numerically identical, UTC)
# ---------------------------------------------------------------------------
def clean_ts(unix_seconds: float) -> str:
    """Format a Unix-seconds value as the canonical ``%Y-%m-%d %H:%M:00`` key.

    Mirrors legacy ``clean_ts(pd.to_datetime(t, unit='s'))`` (main.py:299): UTC,
    seconds floored to ``:00``.
    """
    return datetime.utcfromtimestamp(unix_seconds).strftime(config.TS_FORMAT)


def parse_ts(ts: str) -> float:
    """Inverse of :func:`clean_ts` — string key back to Unix seconds (UTC)."""
    return datetime.strptime(ts, config.TS_FORMAT).replace(
        tzinfo=timezone.utc
    ).timestamp()


def _effort_ticks(levels: dict) -> float:
    """DIVERGES FROM LEGACY (Step 4): the effort/result denominator.

    Volume-weighted price dispersion (the std dev of trade price weighted by each
    level's volume) divided by TICK_SIZE, floored at 1.0 physical tick. Replaces
    the legacy ``|high-low| / TICK_SIZE`` range, which a single wick inflated.

    * Wick-robust: a lone far level carries near-zero weight, so it perturbs the
      dispersion by its tiny volume, not by the full price range.
    * Degenerate-safe (rule 0.6/1): a single-price / zero-dispersion bucket floors
      to 1.0 tick -> er = vol (large but BOUNDED), never the ~1e7 explosion an
      epsilon floor would give. Identical to legacy on that single-price case.
    """
    total = 0.0
    wsum = 0.0
    for p_str, v in levels.items():
        vol = v["b"] + v["s"]
        if vol <= 0:
            continue
        total += vol
        wsum += vol * float(p_str)
    if total <= 0:
        return 1.0
    vwap = wsum / total
    var = 0.0
    for p_str, v in levels.items():
        vol = v["b"] + v["s"]
        if vol <= 0:
            continue
        d = float(p_str) - vwap
        var += vol * d * d
    dispersion = (var / total) ** 0.5
    return max(1.0, dispersion / config.TICK_SIZE)


# ---------------------------------------------------------------------------
# QuantBucket  (verbatim — main.py:21)
# ---------------------------------------------------------------------------
class QuantBucket:
    def __init__(self, target_vol, start_time=None):
        self.target_vol = target_vol
        # DIVERGES FROM LEGACY: start_time may be None until the first tick seeds it
        # from event-time (QuantEngine.process_tick) — no wall-clock seed at build.
        self.start_time = start_time
        self.end_time = None

        self.curr_vol = 0.0
        self.buy_vol = 0.0
        self.sell_vol = 0.0
        self.opL, self.opS, self.clL, self.clS = 0.0, 0.0, 0.0, 0.0
        # DIVERGES FROM LEGACY (Step 3): OI-neutral "unattributed transfer" volume.
        # Conservation law: opL + opS + clL + clS + churn == curr_vol.
        self.churn = 0.0

        self.high = -float("inf")
        self.low = float("inf")
        # Constant-volume OHLC: open = first tick price, close = last tick price.
        # Tracked here (not in legacy) so Mode 10 can draw bucket candlesticks.
        self.open_price = 0.0
        self.close_price = 0.0
        self.levels = {}
        self.liquidations = []
        self.vel_ratio = 1.0
        self.poc_price = 0.0
        self.buyer_er = 1.0
        self.seller_er = 1.0

    # ------------------------------------------------------------------
    # Full bucket wire serialization (Phase 0 — Pure Quant scanner)
    # ------------------------------------------------------------------
    def _assemble(self, end_time: float, poc_price: float, buyer_er: float,
                  seller_er: float, vol_mult: float) -> BucketSnapshot:
        """Build the complete :class:`BucketSnapshot` from the bucket's state.

        Shared by closed and live snapshots; the four computed scalars (end_time,
        poc, ER, vel) are passed in because they are finalized at close for a
        closed bucket but computed on the fly for the live one.
        """
        hi = self.high if self.high != -float("inf") else self.close_price
        lo = self.low if self.low != float("inf") else self.close_price
        # DIVERGES FROM LEGACY: start_time can be None before the first tick seeds
        # it; fall back to end_time so a pre-tick live snapshot stays JSON-safe.
        st = self.start_time
        if st is None:
            st = end_time if end_time is not None else 0.0
        return {
            "start_time": float(st),
            "end_time": float(end_time if end_time is not None else st),
            "open": float(self.open_price),
            "high": float(hi),
            "low": float(lo),
            "close": float(self.close_price),
            "poc_price": float(poc_price),
            "buy_vol": float(self.buy_vol),
            "sell_vol": float(self.sell_vol),
            "curr_vol": float(self.curr_vol),
            "opL": float(self.opL),
            "opS": float(self.opS),
            "clL": float(self.clL),
            "clS": float(self.clS),
            "churn": float(self.churn),
            "buyer_er": float(buyer_er),
            "seller_er": float(seller_er),
            "vol_mult": float(vol_mult),
            # A3b-pre: per-bucket forced-liquidation volume by side. SHORTS liquidated =
            # side "BUY" (forced buys) -> liq_short; LONGS liquidated = side "SELL" ->
            # liq_long. WIRE-ADDITIVE, same rationale as `levels` below: _bucket_to_dict
            # already persists b.liquidations, so NO BUCKET_SCHEMA_VERSION bump and NO
            # history.db wipe. (b.liquidations is fed from the live liq stream in feeds.py.)
            "liq_short": float(sum(q.get("qty", 0.0) for q in self.liquidations
                                   if q.get("side") == "BUY")),
            "liq_long": float(sum(q.get("qty", 0.0) for q in self.liquidations
                                  if q.get("side") == "SELL")),
            # Stage 1 (Option A): carry the per-bucket footprint distribution on the
            # wire so Mode 10 can draw the ladder + true POC. WIRE-ADDITIVE — persistence
            # already inlines levels (_bucket_to_dict), so NO BUCKET_SCHEMA_VERSION bump
            # and NO history.db wipe. dict() snapshots the outer map; the inner {b,s} are
            # serialized to JSON immediately on the daemon's single loop (no tear).
            "levels": dict(self.levels),
        }

    def full_snapshot(self) -> BucketSnapshot:
        """Finalized snapshot of a CLOSED bucket — scalars already computed at close."""
        return self._assemble(self.end_time, self.poc_price, self.buyer_er,
                              self.seller_er, self.vel_ratio)

    def live_snapshot(self, now: float, avg_velocity: float) -> BucketSnapshot:
        """Live snapshot of the ACTIVE (forming) bucket.

        POC, effort/result and the velocity ratio are not finalized until close,
        so they are computed here against the bucket's current state, with
        ``end_time`` proxied to ``now`` so duration-based metrics (kinetic, neon
        velocity) read a sensible value for the pulsing right edge.
        """
        poc_price, poc_v = self.close_price, 0.0
        for p_str, vols in self.levels.items():
            tot = vols["b"] + vols["s"]
            if tot > poc_v:
                poc_v, poc_price = tot, float(p_str)

        # DIVERGES FROM LEGACY (Step 4): same volume-weighted dispersion effort
        # denominator (1.0-tick floor) as _close_active_bucket, so the live edge
        # and closed buckets read on one scale.
        ticks = _effort_ticks(self.levels)
        buyer_er = self.buy_vol / ticks
        seller_er = self.sell_vol / ticks

        # DIVERGES FROM LEGACY: start_time may be unset until the first tick; proxy
        # to `now` so the live edge reads a floored duration instead of crashing.
        start = self.start_time if self.start_time is not None else now
        duration = max(1.0, now - start)
        vel = self.curr_vol / duration
        vol_mult = (vel / avg_velocity) if avg_velocity > 0 else 1.0
        return self._assemble(now, poc_price, buyer_er, seller_er, vol_mult)


# ---------------------------------------------------------------------------
# QuantEngine  (verbatim — main.py:41)  + VPIN addition
# ---------------------------------------------------------------------------
class QuantEngine:
    def __init__(self, target_vol=config.DEFAULT_TARGET_VOL):
        self.target_vol = target_vol
        # DIVERGES FROM LEGACY: do not seed start_time from wall-clock; leave it None
        # so the first event-time tick seeds it (coherent event-time bucket clock).
        self.active_bucket = QuantBucket(target_vol, None)
        self.closed_buckets = []
        self.rolling_velocity = deque(maxlen=config.VELOCITY_LOOKBACK)
        self.avg_velocity = 1.0
        self.last_tick_time = 0.0   # DIVERGES FROM LEGACY (Step 19.4): event-time for periodic recalibrate

        # --- ADDITION: VPIN toxicity queue (spec §3.4), parallel to vel_ratio ---
        self.vpin_queue = deque(maxlen=config.VPIN_WINDOW)
        self.vpin = 0.0

    def process_tick(self, price, vol, taker_buy, delta_oi, footprints_dict,
                     liquidations=None, tick_time=None):
        if tick_time is None:
            tick_time = time.time()

        if vol <= 0:
            return

        # DIVERGES FROM LEGACY (Step 19.4): remember the latest event time so the
        # periodic recompute_loop windows recalibrate at event-time, not wall-clock.
        self.last_tick_time = tick_time

        # DIVERGES FROM LEGACY: lazily seed the active bucket's start from event-time
        # on its first tick (was wall-clock at construction) so start_time/end_time
        # share one clock — kills the mixed-clock negative/quantized durations.
        if self.active_bucket.start_time is None:
            self.active_bucket.start_time = tick_time

        b_ratio = taker_buy / vol
        s_ratio = (vol - taker_buy) / vol
        # DIVERGES FROM LEGACY (Step 3): the 4 position vectors carry ONLY the
        # OI-confirmed portion. There is no L1 info to attribute OI-neutral volume
        # to opening vs closing, so the legacy churn/2 invention is removed; the
        # unattributed remainder is tracked as `churn`. OI-confirmed volume cannot
        # exceed the tick's volume (Step 2 clamps the live feed; min() also guards
        # replay/tests). Conservation: opL + opS + clL + clS + churn == curr_vol.
        oi_mag = min(abs(delta_oi), vol)
        churn = vol - oi_mag

        opL_r = (oi_mag * b_ratio / vol) if delta_oi > 0 else 0.0
        opS_r = (oi_mag * s_ratio / vol) if delta_oi > 0 else 0.0
        clL_r = (oi_mag * s_ratio / vol) if delta_oi < 0 else 0.0
        clS_r = (oi_mag * b_ratio / vol) if delta_oi < 0 else 0.0
        churn_r = churn / vol

        remaining_vol = vol

        while remaining_vol > 0:
            space_left = self.target_vol - self.active_bucket.curr_vol
            if remaining_vol <= space_left:
                self._add_to_bucket(price, remaining_vol, b_ratio, s_ratio,
                                    opL_r, opS_r, clL_r, clS_r, churn_r, liquidations)
                remaining_vol = 0
            else:
                self._add_to_bucket(price, space_left, b_ratio, s_ratio,
                                    opL_r, opS_r, clL_r, clS_r, churn_r, liquidations)
                # Uses the historical timestamp instead of live server time
                self._close_active_bucket(tick_time, footprints_dict)
                remaining_vol -= space_left

    def _add_to_bucket(self, price, chunk_vol, b_r, s_r, opL_r, opS_r, clL_r, clS_r, churn_r, liqs):
        b = self.active_bucket
        # Constant-volume OHLC: first tick sets the open, every tick updates close.
        # SOL price is always > 0, so 0.0 reliably marks an untouched bucket.
        if b.open_price == 0.0:
            b.open_price = price
        b.close_price = price

        b.curr_vol += chunk_vol
        b.buy_vol += chunk_vol * b_r
        b.sell_vol += chunk_vol * s_r
        b.opL += chunk_vol * opL_r
        b.opS += chunk_vol * opS_r
        b.clL += chunk_vol * clL_r
        b.clS += chunk_vol * clS_r
        b.churn += chunk_vol * churn_r

        if price > b.high:
            b.high = price
        if price < b.low:
            b.low = price

        p_str = f"{price:.2f}"
        if p_str not in b.levels:
            b.levels[p_str] = {"b": 0.0, "s": 0.0}
        b.levels[p_str]["b"] += chunk_vol * b_r
        b.levels[p_str]["s"] += chunk_vol * s_r

        if liqs:
            b.liquidations.extend(liqs)

    def _close_active_bucket(self, current_time, footprints_dict):
        b = self.active_bucket
        b.end_time = current_time

        # 1. Velocity (Toxicity indicator)
        # DIVERGES FROM LEGACY: the rigid `max(1.0, end-start)` floor (a) inverted
        # reality on fast fills (instant fill -> LOW velocity) and (b) masked the
        # degenerate buckets the Phase-0 baseline exposed. New contract:
        #   * end <= start (or unset start) is NOT a real fill — incl. the ws
        #     reconnect case where a push's event time lands behind the previous —
        #     so skip it: no rolling_velocity sample, neutral vel_ratio. A negative
        #     or epsilon-exploded velocity can never enter the baseline (rule 0.6/3).
        #   * genuine sub-second fills floor at the event-clock resolution (1 ms),
        #     a physical bound, so a burst reads high-but-finite, not low.
        #   * avg_velocity is the MEDIAN of the window (outlier-resistant, 0.6/3);
        #     vel_ratio stays neutral until the window warms up (0.6/2).
        delta = (b.end_time - b.start_time) if b.start_time is not None else -1.0
        if delta <= 0:
            b.vel_ratio = 1.0
        else:
            duration = max(0.001, delta)
            vel = self.target_vol / duration
            self.rolling_velocity.append(vel)
            self.avg_velocity = statistics.median(self.rolling_velocity)
            if len(self.rolling_velocity) >= _VEL_WARMUP_MIN and self.avg_velocity > 0:
                b.vel_ratio = vel / self.avg_velocity
            else:
                b.vel_ratio = 1.0

        # 2. Extract POC (Sniper Target)
        max_v = 0
        for p_str, vols in b.levels.items():
            tot = vols["b"] + vols["s"]
            if tot > max_v:
                max_v = tot
                b.poc_price = float(p_str)

        # 3. Absorption Math
        # DIVERGES FROM LEGACY (Step 4): effort denominator is volume-weighted price
        # dispersion (wick-robust), not the |high-low| range; floored at 1.0 tick.
        ticks = _effort_ticks(b.levels)
        b.buyer_er = b.buy_vol / ticks
        b.seller_er = b.sell_vol / ticks

        self.closed_buckets.append(b)
        if len(self.closed_buckets) > config.CLOSED_BUCKETS_CAP:
            self.closed_buckets.pop(0)

        # --- ADDITION: roll the VPIN queue (spec §3.4.1) ------------------
        # VPIN = Σ|V_buy − V_sell| / (N · V_target) over the last N buckets.
        self.vpin_queue.append(abs(b.buy_vol - b.sell_vol))
        n = len(self.vpin_queue)
        denom = n * self.target_vol
        self.vpin = (sum(self.vpin_queue) / denom) if denom > 0 else 0.0

        # DIVERGES FROM LEGACY (Step 19.4): recalibrate (the 20-step optimizer over the
        # 2h footprint window) moved OFF this per-close hot path to the daemon's
        # periodic recompute_loop. It ran synchronously on EVERY close (~1.8ms close-
        # class p99 in the 19.3 gate vs ~19us steady); a volume burst clusters closes
        # and stalls the socket drain. target_vol still adapts on the periodic cadence;
        # the new bucket spawns with its latest value. (footprints_dict unused here now.)
        self.active_bucket = QuantBucket(self.target_vol, current_time)

    def recalibrate(self, current_time, footprints_dict):
        threshold_time = current_time - config.RECALIB_WINDOW_SECS
        historical_flow = []
        for utime_str in sorted(footprints_dict.keys()):
            if int(utime_str) >= threshold_time:
                fp = footprints_dict[utime_str]
                c_buy = sum(v["b"] for v in fp.get("levels", {}).values())
                c_sell = sum(v["s"] for v in fp.get("levels", {}).values())
                if (c_buy + c_sell) > 0:
                    historical_flow.append({"vol": c_buy + c_sell, "b": c_buy, "s": c_sell})

        if len(historical_flow) >= 10:
            self.optimize_bucket_size(historical_flow)

    def optimize_bucket_size(self, historical_flow_data):
        total_vol = sum(f["vol"] for f in historical_flow_data)
        if total_vol < 1000:
            return

        avg_node_vol = total_vol / len(historical_flow_data)
        min_test_v = max(100.0, avg_node_vol * 0.5)
        max_test_v = max(1000.0, avg_node_vol * 15.0)
        step = (max_test_v - min_test_v) / 20.0

        best_v = self.target_vol
        max_variance = -1.0

        test_v = min_test_v
        while test_v <= max_test_v + 0.0001:
            imbalances = []
            curr_vol, curr_b, curr_s = 0.0, 0.0, 0.0

            for f in historical_flow_data:
                remaining = f["vol"]
                b_ratio = f["b"] / f["vol"]
                s_ratio = f["s"] / f["vol"]

                while remaining > 0:
                    space = test_v - curr_vol
                    if remaining <= space:
                        curr_vol += remaining
                        curr_b += remaining * b_ratio
                        curr_s += remaining * s_ratio
                        remaining = 0
                    else:
                        curr_vol += space
                        curr_b += space * b_ratio
                        curr_s += space * s_ratio
                        imbalances.append(abs(curr_b - curr_s) / test_v)
                        curr_vol, curr_b, curr_s = 0.0, 0.0, 0.0
                        remaining -= space

            if len(imbalances) > 5:
                mean = sum(imbalances) / len(imbalances)
                variance = sum((x - mean) ** 2 for x in imbalances) / len(imbalances)
                if variance > max_variance:
                    max_variance = variance
                    best_v = test_v
            test_v += step

        self.target_vol = best_v


# ---------------------------------------------------------------------------
# Otsu + first-derivative band  (verbatim — main.py:392)
# ---------------------------------------------------------------------------
def calculate_dynamic_band(levels_dict, poc_price, tick_size=config.TICK_SIZE):
    """Fuse Otsu statistical thresholding with first-derivative calculus to map
    the true physical edge of an institutional limit wall (spec §3.3)."""
    vols = [v["b"] + v["s"] for v in levels_dict.values()]
    if not vols:
        return poc_price + 0.05, poc_price - 0.05

    max_v = max(vols)
    min_v = min(vols)

    # Failsafe: completely flat bucket (no variance)
    if max_v == min_v:
        return poc_price + 0.05, poc_price - 0.05

    # 1. OTSU CLUSTERING — maximize between-class variance over 50 thresholds
    best_t = min_v
    max_variance = -1.0

    step = (max_v - min_v) / config.OTSU_ITERATIONS
    t = min_v + step
    while t < max_v:
        class0 = [v for v in vols if v < t]    # Retail noise
        class1 = [v for v in vols if v >= t]   # Whale volume

        if class0 and class1:
            w0 = len(class0) / len(vols)
            w1 = len(class1) / len(vols)
            mu0 = sum(class0) / len(class0)
            mu1 = sum(class1) / len(class1)

            var_between = w0 * w1 * (mu0 - mu1) ** 2
            if var_between > max_variance:
                max_variance = var_between
                best_t = t
        t += step

    t_otsu = best_t  # Mathematically perfect institutional-volume boundary

    def get_v(p):
        p_str = f"{p:.2f}"
        node = levels_dict.get(p_str, {"b": 0.0, "s": 0.0})
        return node["b"] + node["s"]

    # 2. CALCULUS EXPANSION — map the physical shape outward from the POC
    # --- UPWARD (p_upper) ---
    p_upper = poc_price
    curr_v = get_v(p_upper)

    for _ in range(config.EXPANSION_MAX_TICKS):
        next_p = p_upper + tick_size
        next_v = get_v(next_p)
        delta_v = curr_v - next_v  # First derivative

        if delta_v > 0:
            # Sliding down the volume mountain
            p_upper = next_p
            curr_v = next_v
        else:
            # Inflection point — shelf check against the institutional baseline
            if curr_v >= t_otsu:
                p_upper = next_p
                curr_v = next_v
            else:
                break

    # --- DOWNWARD (p_lower) ---
    p_lower = poc_price
    curr_v = get_v(p_lower)

    for _ in range(config.EXPANSION_MAX_TICKS):
        next_p = p_lower - tick_size
        next_v = get_v(next_p)
        delta_v = curr_v - next_v

        if delta_v > 0:
            p_lower = next_p
            curr_v = next_v
        else:
            if curr_v >= t_otsu:
                p_lower = next_p
                curr_v = next_v
            else:
                break

    # 1-tick minimum buffer for UI rendering
    return max(p_upper, poc_price + tick_size), min(p_lower, poc_price - tick_size)


# ---------------------------------------------------------------------------
# Order-block extraction + mitigation  (verbatim — main.py:490)
# ---------------------------------------------------------------------------
def calc_quant_obs(engine: QuantEngine, timeframe: str) -> List[dict]:
    obs: List[dict] = []
    buckets = engine.closed_buckets
    if len(buckets) < config.OB_MIN_BUCKETS:
        return obs

    buyer_ers = [b.buyer_er for b in buckets]
    seller_ers = [b.seller_er for b in buckets]

    for i in range(config.OB_MIN_BUCKETS, len(buckets)):
        b0 = buckets[i - 1]
        b1 = buckets[i]

        lo = i - config.ER_LOOKBACK
        avg_b_er = max(0.1, sum(buyer_ers[lo:i]) / config.ER_LOOKBACK)
        avg_s_er = max(0.1, sum(seller_ers[lo:i]) / config.ER_LOOKBACK)

        # BULLISH QUANT OB: Absorbed sellers -> new long ignition
        if b0.seller_er > avg_s_er * 1.5:
            if b1.buy_vol > b1.sell_vol and b1.opL > b1.opS:
                if b1.vel_ratio > config.VELOCITY_TIER_HIGHLIGHT:
                    dyn_top, dyn_bot = calculate_dynamic_band(b0.levels, b0.poc_price)
                    power_score = (b0.seller_er / avg_s_er) * b1.vel_ratio * 100

                    obs.append({
                        "type": "bullish",
                        "top": dyn_top,
                        "bottom": dyn_bot,
                        "start": clean_ts(b0.start_time),
                        "confirm": clean_ts(b1.end_time),
                        "end": None,
                        "active": True,
                        "power_score": float(power_score),
                        "vol_mult": float(b1.vel_ratio),
                        "tf": timeframe,
                        "is_merged": False,
                        "ob_id": f"qob_bull_{int(b0.start_time)}_{b0.poc_price}",
                        "buffer_pct": 0,
                        "vol_percent": float(b1.vel_ratio * 100),
                    })

        # BEARISH QUANT OB: Absorbed buyers -> new short ignition
        elif b0.buyer_er > avg_b_er * 1.5:
            if b1.sell_vol > b1.buy_vol and b1.opS > b1.opL:
                if b1.vel_ratio > config.VELOCITY_TIER_HIGHLIGHT:
                    dyn_top, dyn_bot = calculate_dynamic_band(b0.levels, b0.poc_price)
                    power_score = (b0.buyer_er / avg_b_er) * b1.vel_ratio * 100

                    obs.append({
                        "type": "bearish",
                        "top": dyn_top,
                        "bottom": dyn_bot,
                        "start": clean_ts(b0.start_time),
                        "confirm": clean_ts(b1.end_time),
                        "end": None,
                        "active": True,
                        "power_score": float(power_score),
                        "vol_mult": float(b1.vel_ratio),
                        "tf": timeframe,
                        "is_merged": False,
                        "ob_id": f"qob_bear_{int(b0.start_time)}_{b0.poc_price}",
                        "buffer_pct": 0,
                        "vol_percent": float(b1.vel_ratio * 100),
                    })

    # MITIGATION ENGINE (invalidation): dynamic width means a pierce kills it.
    for ob in obs:
        confirm_time = parse_ts(ob["confirm"])
        for b in buckets:
            if b.end_time is not None and b.end_time > confirm_time:
                if ob["type"] == "bullish" and b.low <= ob["top"]:
                    ob["active"] = False
                    ob["end"] = clean_ts(b.end_time)
                    break
                elif ob["type"] == "bearish" and b.high >= ob["bottom"]:
                    ob["active"] = False
                    ob["end"] = clean_ts(b.end_time)
                    break

    return obs


def rank_obs(obs: List[dict]) -> List[dict]:
    """Power-score ranking with the legacy 10%-cluster tiering (main.py:600).

    Mutates and returns the list sorted by descending power_score with a ``rank``
    field assigned. Kept here so both the daemon broadcast and the REST-style
    catchup path rank identically.
    """
    obs.sort(key=lambda x: x["power_score"], reverse=True)
    if not obs:
        return obs
    current_rank = 0
    obs[0]["rank"] = current_rank
    for idx in range(1, len(obs)):
        prev_score = obs[idx - 1]["power_score"]
        curr_score = obs[idx]["power_score"]
        if prev_score == 0 or (prev_score - curr_score) / prev_score <= 0.10:
            obs[idx]["rank"] = current_rank
        else:
            current_rank += 1
            obs[idx]["rank"] = current_rank
    return obs


# ---------------------------------------------------------------------------
# Per-timeframe engine registry (main.py:193)
# ---------------------------------------------------------------------------
def build_engine_registry() -> Dict[str, QuantEngine]:
    return {tf: QuantEngine() for tf in config.TIMEFRAMES}
