"""FLOW FLIP overlay detector for the LIVE terminal (1h) — EXPLORATORY candidate (2026-07-23).

A big REVERSAL candle: two consecutive candles pointing opposite ways, the second one large. Enter with the
second candle, betting the turn continues.

    SHORT (red sphere):  candle i-1 BULLISH, candle i BEARISH, mov_magn(i) > MM_MIN.
    LONG  (green sphere): candle i-1 BEARISH, candle i BULLISH, mov_magn(i) > MM_MIN.

    entry : candle i close.
    stop  : STRUCTURAL — candle i's opposite wick +/- 0.1% (short: high*1.001, long: low*0.999).
    target: FIXED 0.5% off entry.

MOV_MAGN is the terminal's own bucket magnitude: ((close*100/ref - 100)**2)*100, ref = low for an up
candle / high for a down candle — the squared % move from the reversed extreme. MM_MIN=50 is the in-sample
threshold, not fitted below it.

IN-SAMPLE (2026 SOL 1h, structural stop / 0.5% TP): the SHORT is the working side — n=63, 82.5% win vs a ~73%
break-even (the stop is wide, so break-even is high), and it beats random big-bearish shorts at the SAME
bracket 96% of the time (bracket-fixed null p=0.039). It grades by the effective-aggressor SHIFT across the
two candles (dE = effagg(i) - effagg(i-1)): the more flow flipped bearish, the stronger — monotone across
disjoint bands (71%->100% as dE falls). The LONG side is on break-even and NOT tradeable; it is drawn only as
the symmetric counterpart. NOT frozen, NOT significant after pricing the search, one 23-day window.

NO WARM-UP: candle direction and mov_magn are per-bucket; the only cross-bucket input is the prior candle's
direction. detect() needs no history prefix. The dE grade is a display extra computed by the caller (it needs
eff_causal_share, which does warm up) and is NOT required to fire a badge.

CLOSED-ONLY (skip_last, default True): the still-forming bucket's close/mov_magn repaint, so it is skipped
unless the list is closed-only (replay).

detect(buckets, skip_last=True) -> [{i, side(+1/-1), entry, sl, tp, mm}]
"""
from __future__ import annotations

SL_PAD = 0.001         # structural stop sits this far beyond the entry candle's opposite wick
TP_PCT = 0.005         # fixed 0.5% target
MM_MIN = 50.0          # mov_magn floor on the entry candle


def _ohlc(b):
    o = float(b.get("open", b.get("open_price", 0.0)) or 0.0)
    c = float(b.get("close", b.get("close_price", 0.0)) or 0.0)
    h = float(b.get("high", 0.0) or 0.0)
    l = float(b.get("low", 0.0) or 0.0)
    return o, c, h, l


def mov_magn(b):
    """The terminal's bucket magnitude: squared % move from the REVERSED extreme, x100. None if degenerate."""
    o, c, h, l = _ohlc(b)
    if o <= 0 or c <= 0:
        return None
    ref = l if c > o else (h if c < o else o)
    if ref <= 0:
        return None
    return ((((c * 100.0) / ref) - 100.0) ** 2) * 100.0


def detect(buckets: list, skip_last: bool = True) -> "list[dict]":
    n = len(buckets)
    if n < 2:
        return []
    out = []
    for i in range(1, n - 1 if skip_last else n):
        o1, c1, _h1, _l1 = _ohlc(buckets[i - 1])
        o2, c2, h2, l2 = _ohlc(buckets[i])
        if min(o1, c1, o2, c2, h2, l2) <= 0:
            continue
        mm = mov_magn(buckets[i])
        if mm is None or mm <= MM_MIN:
            continue
        if c1 > o1 and c2 < o2:
            s = -1                                       # bull then big bear -> short the turn
        elif c1 < o1 and c2 > o2:
            s = 1                                        # bear then big bull -> long the turn
        else:
            continue
        stop = l2 * (1 - SL_PAD) if s > 0 else h2 * (1 + SL_PAD)
        if (s > 0 and stop >= c2) or (s < 0 and stop <= c2):
            continue                                     # degenerate (close at/through its own structural stop)
        out.append(dict(i=i, side=s, entry=c2, mm=float(mm), sl=stop,
                        tp=c2 * (1 + TP_PCT) if s > 0 else c2 * (1 - TP_PCT)))
    return out
