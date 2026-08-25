"""LONG WICK rejection (m10_longwick) — red/green DIAMOND badges, ALL chart tfs/sources (user 2026-08-25;
geometry v2 2026-08-25: the off-side wick constraint is a single 2x DOMINANCE rule).

  RED ♦ (above the candle)  — at a SELL (R) wall: BEARISH bar, UPPER wick > body AND upper wick >= 2x the
                              lower wick (the lower wick may be any size, even > body, as long as it's doubled)
                              — an upper-wick rejection into resistance.
  GREEN ♦ (below the candle) — mirror at a BUY (S) wall: BULLISH bar, lower wick > body AND lower wick >= 2x
                              the upper wick.

Walls = the CURRENT-tf Order-Flow Wall marks the chart draws (shared _absorb_marks cache; indices into the
same bucket list). A wall counts while alive at that bar: born (i0 <= i) and not yet at its break bar
(i < i1 for broken walls — same 'signals stop when the break starts' rule as Wall Surge). Candle range must
overlap the wall CORE (P ± band). CLOSED candles only. DESCRIPTIVE / eyeball — no tested edge is claimed.
"""
from __future__ import annotations


def _f(b, k, alt=None):
    v = b.get(k)
    if v is None and alt is not None:
        v = b.get(alt)
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def detect_combo(candles: list, skip_last: bool = True) -> list:
    """LONG WICK COMBO (m10_longwick_combo, gold ♦, user 2026-08-25; breakout-close condition added then
    REMOVED same day — honest test showed it cut ~40% of signals for a noise-level in-sample win-rate tick that
    inverted OOS) — 2-bar continuation-failure pair, NOT bound to walls: a BEARISH bar followed by a
    LONG-UPPER-WICK BEARISH bar (v2 wick geometry: upper wick > body AND >= 2x the lower wick) — buyers pushed
    higher and completely failed -> gold ♦ ABOVE bar 2 (side -1). Mirror for longs: bullish bar then
    long-LOWER-wick bullish bar -> gold ♦ BELOW (side +1). Returns [{i, side}]. Fail-safe: []."""
    n = len(candles)
    if n < 2:
        return []
    try:
        hi_n = (n - 1) if skip_last else n
        out = []
        for i in range(1, hi_n):
            b1 = candles[i - 1]; b2 = candles[i]
            o1 = _f(b1, "open", "open_price"); c1 = _f(b1, "close", "close_price")
            h1 = _f(b1, "high"); l1 = _f(b1, "low")
            o2 = _f(b2, "open", "open_price"); c2 = _f(b2, "close", "close_price")
            h2 = _f(b2, "high"); l2 = _f(b2, "low")
            if min(o1, c1, o2, c2) <= 0.0 or h2 <= l2 or h1 <= l1:
                continue
            body2 = abs(c2 - o2)
            if body2 <= 0.0:
                continue
            uw2 = h2 - max(o2, c2); lw2 = min(o2, c2) - l2
            if c1 < o1 and c2 < o2 and uw2 > body2 and uw2 >= 2.0 * lw2:
                out.append({"i": i, "side": -1})
            elif c1 > o1 and c2 > o2 and lw2 > body2 and lw2 >= 2.0 * uw2:
                out.append({"i": i, "side": 1})
        return out
    except Exception:
        return []


def detect_reclaim(candles: list, skip_last: bool = True) -> list:
    """WICK RECLAIM (m10_longwick_reclaim, cyan/magenta ♦; SIMPLIFIED per user 2026-08-25 — the earlier
    v2-geometry version pointed at the wrong bars). NOT bound to walls.
    LONG (cyan ♦ below bar 2, side +1): TWO CONSECUTIVE BULLISH bars where
      bar 1's UPPER wick >= 1/3 of its candle range, and
      bar 2's LOWER wick >= 1/3 of its candle range AND bar 2 CLOSES ABOVE bar 1's HIGH.
    SHORT mirror (magenta ♦ above, side -1): two bearish bars, bar 1 lower wick >= 1/3 of its range,
    bar 2 upper wick >= 1/3 of its range and closing BELOW bar 1's LOW.
    Returns [{i, side}] with i = bar 2. Fail-safe: []."""
    n = len(candles)
    if n < 2:
        return []
    try:
        hi_n = (n - 1) if skip_last else n
        out = []
        for i in range(1, hi_n):
            b1 = candles[i - 1]; b2 = candles[i]
            o1 = _f(b1, "open", "open_price"); c1 = _f(b1, "close", "close_price")
            h1 = _f(b1, "high"); l1 = _f(b1, "low")
            o2 = _f(b2, "open", "open_price"); c2 = _f(b2, "close", "close_price")
            h2 = _f(b2, "high"); l2 = _f(b2, "low")
            if min(o1, c1, o2, c2) <= 0.0 or h1 <= l1 or h2 <= l2:
                continue
            r1 = h1 - l1; r2 = h2 - l2
            uw1 = h1 - max(o1, c1); lw1 = min(o1, c1) - l1
            uw2 = h2 - max(o2, c2); lw2 = min(o2, c2) - l2
            if c1 > o1 and c2 > o2 and uw1 >= r1 / 3.0 and lw2 >= r2 / 3.0 and c2 > h1:
                out.append({"i": i, "side": 1})
            elif c1 < o1 and c2 < o2 and lw1 >= r1 / 3.0 and uw2 >= r2 / 3.0 and c2 < l1:
                out.append({"i": i, "side": -1})
        return out
    except Exception:
        return []


def detect(candles: list, walls: list, skip_last: bool = True) -> list:
    """[{i, side(+1 green/-1 red)}] over CLOSED candles. Fail-safe: []."""
    n = len(candles)
    if n < 1 or not walls:
        return []
    try:
        hi_n = (n - 1) if skip_last else n
        zones = []                                   # (side 'S'|'R', lo, hi, i0, i1_or_None)
        for m in walls:
            side = m.get("side"); P = float(m.get("price") or 0.0); band = float(m.get("band") or 0.0)
            if side not in ("S", "R") or P <= 0.0 or band <= 0.0:
                continue
            i1 = int(m["i1"]) if (bool(m.get("broken")) and m.get("i1") is not None) else None
            zones.append((side, P - band, P + band, int(m.get("i0", 0)), i1))
        if not zones:
            return []
        out = []
        for i in range(hi_n):
            b = candles[i]
            o = _f(b, "open", "open_price"); c = _f(b, "close", "close_price")
            h = _f(b, "high"); l = _f(b, "low")
            if o <= 0.0 or c <= 0.0 or h <= l:
                continue
            body = abs(c - o)
            uw = h - max(o, c); lw = min(o, c) - l
            if body <= 0.0:
                continue
            if c < o and uw > body and uw >= 2.0 * lw:
                want, side = "R", -1                 # upper-wick rejection into resistance -> red ♦
            elif c > o and lw > body and lw >= 2.0 * uw:
                want, side = "S", 1                  # lower-wick rejection into support -> green ♦
            else:
                continue
            for (ws, wlo, whi, i0, i1) in zones:
                if ws != want or i < i0 or (i1 is not None and i >= i1):
                    continue
                if l <= whi and h >= wlo:            # candle overlaps the wall CORE
                    out.append({"i": i, "side": side})
                    break
        return out
    except Exception:
        return []
