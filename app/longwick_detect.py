"""LONG WICK rejection (m10_longwick) — red/green DIAMOND badges, ALL chart tfs/sources (user 2026-08-25).

  RED ♦ (above the candle)  — at a SELL (R) wall: BEARISH bar whose UPPER wick is bigger than the body AND
                              bigger than the lower wick, with the lower wick smaller than the body (or absent)
                              — an upper-wick rejection into resistance.
  GREEN ♦ (below the candle) — mirror at a BUY (S) wall: BULLISH bar, lower wick > body and > upper wick,
                              upper wick < body (or absent) — a lower-wick rejection into support.

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
            if c < o and uw > body and uw > lw and lw < body:
                want, side = "R", -1                 # upper-wick rejection into resistance -> red ♦
            elif c > o and lw > body and lw > uw and uw < body:
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
