# -*- coding: utf-8 -*-
"""EASY GOLD — labels EVERY tape/candle DIVERGENCE candle sitting in an ACTIVE wall's radar, on the wall's side.

For each Order-Flow Wall (app.absorption_level_detect.detect mark: price / band / side / i0 / i1), scan the bars the
wall is active (i0..i1) and label EVERY candle whose RANGE (low..high) OVERLAPS the wall's radar (price +/-
RADAR_MULT*band) -- i.e. the candle TRADED at the wall. (Gating on the CLOSE is wrong: a support bounce / resistance
rejection closes in the wall-hold direction, which is often OUTSIDE the radar, so it would be missed.) That candle is
labelled when it is BOTH:

    * an EASY-leaning absorption:  A = absorption(buckets, j)[0] < ABSR_MAX (-0.5) -- the tooltip "Absorb R" value, AND
    * a TAPE / CANDLE DIVERGENCE -- the candle CLOSES in the WALL-HOLD direction while the TAPE's dominant side is
      AGAINST it (the trapped side still aggressing on the tape):
        SUPPORT wall (S) -> LONG : bullish candle (close>open) AND Tape-S > Tape-B  -> gold UP badge below the low
        RESIST  wall (R) -> SHORT: bearish candle (close<open) AND Tape-B > Tape-S  -> gold DOWN badge above the high

NO Big/Crazy absorption precursor is required (2026-08-11, user: "label every Easy Gold that happens on a wall
whether there is a Big/Crazy absorption or not"). It is simply an Easy-Gold divergence candle ON a wall. Dedup by
bar -> one gold badge per candle. Tape-B/Tape-S = per-print buy/sell size per second (sz_cb/sz_cs over the duration).

DESCRIPTIVE label only (direction UNTESTED). NOTE: study/absorb_tape_contra.py's -44%@1:1 verdict tested a
mirror-flipped, absorption-gated set of bars -> VOID for this rule; re-test pending on this walls-based universe.

from_walls(buckets, walls) -> [{i, side('long'|'short'), wlo, whi}]   (walls = absorption_level_detect.detect marks)
detect(buckets, walls, skip_last=False) -> same (headless/study alias).
"""
from __future__ import annotations

ABSR_MAX = -0.5     # candle: A (the tooltip "Absorb R" value) below this. Lowered -0.75->-0.5 (2026-08-11, user).
RADAR_MULT = 3.0    # wall radar = price +/- this * band -- MUST match crazy_wall_detect.RADAR_MULT (the ✪/★ zone)


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _tape(b):
    """(Tape-B, Tape-S) = buy/sell print-size per second over the bucket's duration (0 if no per-print sizes)."""
    dur = max(1.0, _f(b.get("end_time")) - _f(b.get("start_time")))
    return sum(b.get("sz_cb") or []) / dur, sum(b.get("sz_cs") or []) / dur


def from_walls(buckets, walls):
    """Label every Easy-Gold divergence candle sitting in any active wall's radar. `walls` =
    absorption_level_detect.detect() marks (price/band/side/i0/i1). Dedup by bar -> one badge per candle."""
    from app import absorption as ABS
    n = len(buckets)
    if n < 2 or not walls:
        return []
    try:
        out = []
        seen = set()
        acache = {}                                            # A per bar, reused across overlapping wall radars
        for w in walls:
            band = _f(w.get("band")); wp = _f(w.get("price"))
            if band <= 0 or wp <= 0:
                continue
            wlo = wp - RADAR_MULT * band
            whi = wp + RADAR_MULT * band
            d = 1 if w.get("side", "R") == "S" else -1         # S -> LONG (support) / R -> SHORT (resistance)
            i0 = max(1, int(w.get("i0", 1)))
            i1 = min(n - 1, int(w.get("i1", n - 1)))            # active window (i1 = last bar while unbroken)
            for j in range(i0, i1 + 1):
                if j in seen:                                  # already carries a badge (from an earlier wall)
                    continue
                b = buckets[j]
                lo = _f(b.get("low")); hi = _f(b.get("high"))
                if hi <= 0 or lo <= 0 or lo > whi or hi < wlo:  # candle range must OVERLAP the radar (traded at wall)
                    continue
                A = acache.get(j)
                if A is None:
                    _a = ABS.absorption(buckets, j)[0]
                    A = _a if _a is not None else 999.0        # None (thin history) -> a sentinel that fails < cutoff
                    acache[j] = A
                if A >= ABSR_MAX:                              # need an EASY-leaning absorption candle
                    continue
                o = _f(b.get("open", b.get("open_price")))
                c = _f(b.get("close", b.get("close_price")))
                tb, ts = _tape(b)
                # candle closes in the WALL-HOLD direction, tape's dominant side AGAINST it (the divergence):
                #   support (S, d>0) -> LONG : bullish (close>open) + Tape-S > Tape-B
                #   resist  (R, d<0) -> SHORT: bearish (close<open) + Tape-B > Tape-S
                ok = (c > o and ts > tb) if d > 0 else (c < o and tb > ts)
                if not ok:
                    continue
                out.append({"i": j, "side": "long" if d > 0 else "short", "wlo": wlo, "whi": whi})
                seen.add(j)
        out.sort(key=lambda g: g["i"])
        return out
    except Exception:
        return []


def detect(buckets, walls, skip_last=False):
    """Headless/study alias. `walls` = app.absorption_level_detect.detect() marks. skip_last is accepted for
    signature parity with the other detectors but unused (labels are per-candle, not edge-gated)."""
    return from_walls(buckets, walls)
