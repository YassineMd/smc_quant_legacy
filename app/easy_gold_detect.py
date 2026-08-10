# -*- coding: utf-8 -*-
"""EASY GOLD — labels the tape/candle DIVERGENCE candle that follows a Wall-Absorption, under the SAME radar.

After a Big/Crazy Wall-Absorption (app.crazy_wall_detect.detect), scan forward up to K candles WHILE price is still
inside that event's radar (wlo..whi, and not past the wall's active window wi1). The FIRST candle that is BOTH:

    * an EASY absorption:  A = absorption(buckets, j)[0] < ABSR_MAX (-0.75)  -- price ran far on little effort, AND
    * a TAPE / CANDLE DIVERGENCE (the tape leans AGAINST the candle body):
        SUPPORT wall (S) -> LONG : bearish candle (close<open) AND Tape-B > Tape-S  -> gold UP badge below the low
        RESIST  wall (R) -> SHORT: bullish candle (close>open) AND Tape-S > Tape-B  -> gold DOWN badge above the high

is labelled. One label per event (dedup by bar). Tape-B/Tape-S = per-print buy/sell size per second (sz_cb/sz_cs
over the bucket duration).

DESCRIPTIVE label only. The setup's own direction is ANTI-predictive: study/absorb_tape_contra.py shows the exact
entries win 64% ONLY because of a wide radar-edge SL, and just 44% at a fair 1:1 SL (both recon years) -- so this
marks the candles for the eye, it does NOT assert an edge.

detect(buckets, walls, skip_last=False) -> [{i, side('long'|'short'), wlo, whi}]  (self-contained; runs CW.detect)
from_events(buckets, events)             -> same, but reuses already-computed crazy_wall_detect events (terminal).
"""
from __future__ import annotations

ABSR_MAX = -0.75    # entry candle must be an EASY absorption (A below this) -- price ran far on thin effort
K = 24              # scan at most this many candles past the wall-absorption event for the divergence candle


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _tape(b):
    """(Tape-B, Tape-S) = buy/sell print-size per second over the bucket's duration (0 if no per-print sizes)."""
    dur = max(1.0, _f(b.get("end_time")) - _f(b.get("start_time")))
    return sum(b.get("sz_cb") or []) / dur, sum(b.get("sz_cs") or []) / dur


def from_events(buckets, events):
    """Label the divergence candle for each Wall-Absorption event. `events` = crazy_wall_detect.detect() output
    (each carries i / wall_side / wlo / whi / wi1). Dedup by bar so two events can't double-label one candle."""
    from app import absorption as ABS
    n = len(buckets)
    if n < 2 or not events:
        return []
    try:
        out = []
        seen = set()
        for e in events:
            ei = int(e.get("i", -1))
            wlo = e.get("wlo")
            whi = e.get("whi")
            if ei < 0 or wlo is None or whi is None:
                continue
            wi1 = int(e.get("wi1", ei))
            d = 1 if e.get("wall_side", "R") == "S" else -1        # S -> LONG (support held) / R -> SHORT
            for j in range(ei + 1, min(n, ei + 1 + K, wi1 + 1)):    # forward, still under this radar
                c = _f(buckets[j].get("close", buckets[j].get("close_price")))
                if not (wlo <= c <= whi):
                    continue
                A = ABS.absorption(buckets, j)[0]
                if A is None or A >= ABSR_MAX:                      # need an EASY absorption candle
                    continue
                o = _f(buckets[j].get("open", buckets[j].get("open_price")))
                tb, ts = _tape(buckets[j])
                # tape must CONTRADICT the candle body (divergence), in the wall-hold direction:
                ok = (c < o and tb > ts) if d > 0 else (c > o and ts > tb)
                if not ok:
                    continue
                if j not in seen:
                    out.append({"i": j, "side": "long" if d > 0 else "short", "wlo": wlo, "whi": whi})
                    seen.add(j)
                break                                              # first qualifying candle per event only
        out.sort(key=lambda g: g["i"])
        return out
    except Exception:
        return []


def detect(buckets, walls, skip_last=False):
    """Self-contained: run the Wall-Absorption detector, then label each event's divergence candle. For study /
    headless use. `walls` = app.absorption_level_detect.detect() marks."""
    try:
        from app import crazy_wall_detect as CW
        return from_events(buckets, CW.detect(buckets, walls, skip_last=skip_last))
    except Exception:
        return []
