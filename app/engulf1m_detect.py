"""ABSORPTION CANDLE indicator — LIVE terminal overlay, ALL timeframes (hamburger key 'm10_engulf1m',
shown in the dropdown as 'Absorption Candle indicator').

A small LOSANGE badge on a candle, in one of three tiers (highest priority first — one badge per candle):
  * CYAN (long) / MAGENTA (short): an ENGULFING candle (non-doji) with |A| >= 2  (extreme-absorption engulf).
  * BLUE (long) / ORANGE (short):  TWO consecutive SAME-SIDE candles, EACH with |A| >= 1 (badge on the 2nd).
  * GREEN (long) / RED (short):    an ENGULFING candle (non-doji) with |A| >= 1  (moderate-absorption engulf).

A = the side-adjusted 'Absorb R' (app/absorption.absorption[0]). BOTH the easy (<=-1) and heavy (>=+1)
absorption extremes qualify — the sign of A is NOT the colour; the colour encodes the candle/pair SIDE
(bull vs bear). Descriptive marker, NOT a proven edge (no S/R / trend context, unlike the 5m/15m/1h engulf
strategies). Works on any tf's buckets (absorption is per-bucket, tf-agnostic).

detect(buckets, skip_last=False, absorp=None) -> [{i, side(+1/-1), kind('cm'|'ob'|'rg'), a}]
"""
from __future__ import annotations

from . import absorption as _absorption
from .engulf_sr_detect import _ohlc

T1 = 1.0    # |A| >= this -> green/red (engulf) or blue/orange (same-side pair)
T2 = 2.0    # |A| >= this -> cyan/magenta (engulf)


def _non_doji(o, c, h, l) -> bool:
    """Body strictly larger than BOTH the upper and the lower wick."""
    body = abs(c - o)
    return body > (h - max(o, c)) and body > (min(o, c) - l)


def _is_engulf(o, c, po, pc) -> int:
    """+1 = bull-engulf of the previous BODY, -1 = bear-engulf, 0 = neither."""
    pbhi = max(po, pc); pblo = min(po, pc)
    if c > o and o <= pblo and c >= pbhi:
        return 1
    if c < o and o >= pbhi and c <= pblo:
        return -1
    return 0


def detect(buckets, skip_last=False, absorp=None):
    n = len(buckets)
    if n < 2:
        return []
    if absorp is None:                                    # ONE absorption pass, reused for candle i and i-1
        absorp = []
        for k in range(n):
            try:
                absorp.append(_absorption.absorption(buckets, k)[0])
            except Exception:
                absorp.append(None)
    out = []
    hi = (n - 1) if skip_last else n
    for i in range(1, hi):
        o, c, h, l = _ohlc(buckets[i])
        if o <= 0 or c <= 0 or (h - l) <= 0:
            continue
        po, pc, ph, pl = _ohlc(buckets[i - 1])
        if po <= 0 or pc <= 0 or (ph - pl) <= 0:
            continue
        a = absorp[i]
        if a is None:
            continue
        aa = abs(a)
        eng = _is_engulf(o, c, po, pc)                    # +1 / -1 / 0
        eng_ok = eng != 0 and _non_doji(o, c, h, l)
        # --- tier ladder, first match wins (user's listed priority) ---
        if eng_ok and aa >= T2:                           # 1) CYAN/MAGENTA — extreme-absorption engulf
            out.append(dict(i=i, side=eng, kind="cm", a=a))
            continue
        d_i = 1 if c > o else (-1 if c < o else 0)        # this candle's direction
        d_p = 1 if pc > po else (-1 if pc < po else 0)    # previous candle's direction
        pa = absorp[i - 1]
        if (d_i != 0 and d_i == d_p and aa >= T1          # 2) BLUE/ORANGE — two same-side |A|>=1 candles
                and pa is not None and abs(pa) >= T1):
            out.append(dict(i=i, side=d_i, kind="ob", a=a))
            continue
        if eng_ok and aa >= T1:                           # 3) GREEN/RED — moderate-absorption engulf
            out.append(dict(i=i, side=eng, kind="rg", a=a))
    return out
