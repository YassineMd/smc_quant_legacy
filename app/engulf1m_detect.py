"""1m ENGULFING absorption spheres — LIVE terminal overlay, 1m ONLY (hamburger 'm10_engulf1m').

A SPHERE badge on any 1m candle that:
  * ENGULFS its previous candle (bull: opens <= prev body low, closes >= prev body high; bear: mirror),
  * is NOT a doji (body strictly larger than BOTH wicks),
  * and hits an ABSORPTION extreme A = the side-adjusted 'Absorb R' (app/absorption.absorption[0]):
       tier 1  |A| >= 1   (A <= -1 easy / not-absorbed, OR A >= +1 heavy / absorbed)   -> red (short) / green (long)
       tier 2  |A| >= 2                                                                -> magenta (short) / cyan (long)

Colour encodes the ENGULF SIDE (green/cyan = bullish, red/magenta = bearish); the tier encodes |A|. Both the
easy (<=-1) and heavy (>=+1) absorption extremes qualify — the sign of A is NOT the colour. Descriptive marker,
NOT a proven edge (no S/R / trend context, unlike the 5m/15m/1h engulf strategies).

detect(buckets, skip_last=False, absorp=None) -> [{i, side(+1/-1), tier(1/2), a}]
"""
from __future__ import annotations

from . import absorption as _absorption
from .engulf_sr_detect import _ohlc

T1 = 1.0    # |A| >= this -> red/green sphere
T2 = 2.0    # |A| >= this -> magenta/cyan sphere


def _non_doji(o, c, h, l) -> bool:
    """Body strictly larger than BOTH the upper and the lower wick."""
    body = abs(c - o)
    return body > (h - max(o, c)) and body > (min(o, c) - l)


def detect(buckets, skip_last=False, absorp=None):
    n = len(buckets)
    if n < 2:
        return []
    out = []
    for i in range(1, (n - 1) if skip_last else n):
        o, c, h, l = _ohlc(buckets[i])
        if o <= 0 or c <= 0 or (h - l) <= 0:
            continue
        po, pc, ph, pl = _ohlc(buckets[i - 1])
        if po <= 0 or pc <= 0 or (ph - pl) <= 0:
            continue
        pbhi = max(po, pc); pblo = min(po, pc)
        bull = c > o and o <= pblo and c >= pbhi     # engulfs the previous body, up
        bear = c < o and o >= pbhi and c <= pblo     # engulfs the previous body, down
        if not (bull or bear) or not _non_doji(o, c, h, l):
            continue
        if absorp is not None:
            a = absorp[i]
        else:
            try:
                a = _absorption.absorption(buckets, i)[0]
            except Exception:
                a = None
        if a is None:
            continue
        aa = abs(a)
        if aa < T1:                                   # not an absorption extreme -> no sphere
            continue
        out.append(dict(i=i, side=(1 if bull else -1), tier=(2 if aa >= T2 else 1), a=a))
    return out
