"""NO-WICK BAR WALL — eyeball overlay (m10_nowickwall), ALL timeframes. Same wall design as the Order-Flow Walls
(app/absorption_level_detect) but WITHOUT the radar area, and the wall band = the ENTIRE candle height (low..high).

A wall is born at a NO-WICK bar:
  BULLISH bar with NO LOWER wick (open == low)  -> SUPPORT (green),    zone = [low, high] of that candle.
  BEARISH bar with NO UPPER wick (open == high) -> RESISTANCE (red),   zone = [low, high] of that candle.
"No wick" = that side's wick is EXACTLY zero (<= 0.1% of range, robust to float noise; any visible wick is excluded).
The wall projects
forward from its formation bar until a later candle BODY CLOSES beyond the zone (support: close < low; resistance:
close > high) = MITIGATED; the draw layer drops a mitigated wall a few bars after the break. DESCRIPTIVE / eyeball only.

detect(buckets, skip_last=True) -> [{i0, i1, side('S'|'R'), lo, hi, broken}] (i0/i1 = formation / current-or-break bar
index in the passed list). Fail-safe: [] on any error.
"""
from __future__ import annotations

WICK_TOL = 0.001     # STRICT "no wick" = the relevant side's wick <= 0.1% of the candle range (= exact zero in the data;
#                      the data has a clean gap — wicks are either exactly 0 or > 0.1% of range — so this is truly no-wick,
#                      just robust to sub-tick float noise. Any visible wick is excluded.


def _o(b):
    return float(b.get("open", b.get("open_price", 0.0)) or 0.0)


def _c(b):
    return float(b.get("close", b.get("close_price", 0.0)) or 0.0)


def _h(b):
    return float(b.get("high", 0.0) or 0.0)


def _l(b):
    return float(b.get("low", 0.0) or 0.0)


def detect(buckets, skip_last=True):
    n = len(buckets)
    if n < 2:
        return []
    try:
        hi_n = (n - 1) if skip_last else n            # never birth a wall on a still-forming last bar
        done = []; active = []                        # active wall: {i0, i1, side, lo, hi, broken}
        for i in range(hi_n):
            o = _o(buckets[i]); c = _c(buckets[i]); h = _h(buckets[i]); l = _l(buckets[i])
            still = []
            for w in active:                          # extend / mitigate the open walls
                broke = (c < w["lo"]) if w["side"] == "S" else (c > w["hi"])   # BODY close beyond the zone
                w["i1"] = i
                if broke:
                    w["broken"] = True; done.append(w)
                else:
                    still.append(w)
            active = still
            rng = h - l
            if rng <= 0:
                continue
            if c > o and (o - l) <= WICK_TOL * rng:                            # bullish, no lower wick -> SUPPORT
                active.append({"i0": i, "i1": i, "side": "S", "lo": l, "hi": h, "broken": False})
            elif c < o and (h - o) <= WICK_TOL * rng:                          # bearish, no upper wick -> RESISTANCE
                active.append({"i0": i, "i1": i, "side": "R", "lo": l, "hi": h, "broken": False})
        return done + active
    except Exception:
        return []
