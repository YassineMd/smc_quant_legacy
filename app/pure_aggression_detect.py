# -*- coding: utf-8 -*-
"""PURE AGGRESSION — the mirror of Easy Gold: EVERY footprint bubble AGREES with the candle, at a same-side wall.

For each Order-Flow Wall (app.absorption_level_detect.detect mark: price / band / side / i0 / i1), scan the bars the
wall is active (i0..i1) and label EVERY candle whose RANGE (low..high) OVERLAPS the wall's radar (price +/-
RADAR_MULT*band) -- i.e. the candle TRADED at the wall -- that is a ONE-SIDED aggression print:

    BUY wall (S, support):     BULLISH candle (close>open) AND every top-3 bubble is GREEN (buy >= sell)  -> green |
    SELL wall (R, resistance): BEARISH candle (close<open) AND every top-3 bubble is RED   (sell > buy)   -> red   |

"Bubbles" = the drawn top-3 footprint levels by total volume; green iff buy >= sell, red iff sell > buy (exactly the
footprint_layers colouring). This is PURE AGGRESSION -- no absorption, no divergence: at a support wall buyers own
every heavy level and close it up; at a resistance wall sellers own every level and close it down. (Contrast Easy
Gold, where the TAPE leans AGAINST the candle.) Dedup by bar -> one badge per candle.

DESCRIPTIVE label only.

from_walls(buckets, walls) -> [{i, side('long'|'short'), wlo, whi}]   (walls = absorption_level_detect.detect marks)
detect(buckets, walls, skip_last=False) -> same (headless/study alias).
"""
from __future__ import annotations

RADAR_MULT = 3.0    # wall radar = price +/- this * band -- MUST match crazy_wall_detect.RADAR_MULT (the wall zone)


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def from_walls(buckets, walls):
    """Label every one-sided-aggression candle sitting in a same-side active wall's radar. `walls` =
    absorption_level_detect.detect() marks. Dedup by bar -> one badge per candle."""
    from app.crazy_wall_detect import _bubbles          # the drawn top-3 footprint levels [(price, tot, buy, sell)]
    n = len(buckets)
    if n < 2 or not walls:
        return []
    try:
        out = []
        seen = set()
        bcache = {}                                       # top-3 bubbles per bar, reused across overlapping walls
        for w in walls:
            band = _f(w.get("band")); wp = _f(w.get("price"))
            if band <= 0 or wp <= 0:
                continue
            wlo = wp - RADAR_MULT * band
            whi = wp + RADAR_MULT * band
            s = w.get("side", "R")                        # S = buy wall (support) / R = sell wall (resistance)
            i0 = max(1, int(w.get("i0", 1)))
            i1 = min(n - 1, int(w.get("i1", n - 1)))
            for j in range(i0, i1 + 1):
                if j in seen:                             # already carries a badge (from an earlier wall)
                    continue
                b = buckets[j]
                lo = _f(b.get("low")); hi = _f(b.get("high"))
                if hi <= 0 or lo <= 0 or lo > whi or hi < wlo:   # candle range must OVERLAP the radar (traded at wall)
                    continue
                o = _f(b.get("open", b.get("open_price")))
                c = _f(b.get("close", b.get("close_price")))
                bubs = bcache.get(j)
                if bubs is None:
                    bubs = _bubbles(b); bcache[j] = bubs
                if not bubs:                              # no footprint bubbles -> no aggression read
                    continue
                if s == "S":                              # buy wall -> BULLISH candle + every bubble GREEN (buy>=sell)
                    if not (c > o and all(buy >= sell for (_p, _t, buy, sell) in bubs)):
                        continue
                    side = "long"
                else:                                     # sell wall -> BEARISH candle + every bubble RED (sell>buy)
                    if not (c < o and all(sell > buy for (_p, _t, buy, sell) in bubs)):
                        continue
                    side = "short"
                out.append({"i": j, "side": side, "wlo": wlo, "whi": whi})
                seen.add(j)
        out.sort(key=lambda g: g["i"])
        return out
    except Exception:
        return []


def detect(buckets, walls, skip_last=False):
    """Headless/study alias. `walls` = app.absorption_level_detect.detect() marks. skip_last accepted for signature
    parity but unused (labels are per-candle)."""
    return from_walls(buckets, walls)
