"""Market-structure swing labels — HH / HL / LH / LL. A confirmed swing HIGH is HH if it prints above the
prior swing high, else LH; a swing LOW is HL if it prints above the prior swing low, else LL. Two swing
engines, both pure/stdlib so they run identically live and in study:

  * detect_structure(H, L)            — fixed-bar k=5 fractal (app.liq_detect._pivots). Used by the concluded
                                        structure studies; kept for reproducibility. Under-labels on trends.
  * detect_structure_zigzag(H, L, thr)— %-retracement ZigZag (LIVE overlay). A swing confirms only when price
                                        retraces `thr` FROM the extreme, so it catches trend-pullback swings
                                        the fractal misses and matches the eye. One knob: ZIGZAG_PCT.

The FIRST swing of each kind has no prior to compare -> unlabeled.
"""
from __future__ import annotations

from .liq_detect import _pivots

ZIGZAG_PCT = 0.20        # SCALP overlay swing sensitivity, in PERCENT. A leg confirms when price retraces this
#                          much from the running extreme. Lower = more/smaller swings; higher = only big turns.
#                          Tune this one number (0.12 dense .. 0.25 clean) then relaunch the terminal.
ZIGZAG_SWING_PCT = 0.60  # SWING overlay sensitivity, in PERCENT — the coarse structure (major turns only) for
#                          swing trading. Same ZigZag engine, bigger threshold. Tune (0.4 .. 1.0) + relaunch.


def detect_structure(highs, lows):
    """-> [(i, price, label, is_high)] in bar order; label in {'HH','LH','HL','LL'}, is_high True for a swing
    high (HH/LH) and False for a swing low (HL/LL). Fixed-bar k=5 fractal. Feed the buckets' high/low arrays."""
    ph, pl = _pivots(highs, lows)
    out = []
    prev = None
    for i in sorted(ph):
        p = ph[i]
        if prev is not None:
            out.append((i, p, "HH" if p > prev else "LH", True))
        prev = p
    prev = None
    for i in sorted(pl):
        p = pl[i]
        if prev is not None:
            out.append((i, p, "HL" if p > prev else "LL", False))
        prev = p
    out.sort(key=lambda t: t[0])
    return out


def _zigzag_confirmed(highs, lows, thr):
    """%-retracement ZigZag WITH the causal confirm bar. Ride the running extreme of the current leg; when the
    opposite wick retraces `thr` (fractional) from it, CONFIRM that extreme as a pivot and flip. Emits pivots
    in bar order, strictly alternating high/low. Only CONFIRMED pivots — the still-forming leg's running
    extreme is never emitted. -> [(pivot_bar, price, is_high, confirm_bar)] (confirm_bar = the retrace bar)."""
    n = len(highs)
    if n < 2:
        return []
    piv = []
    direction = 0                                    # 0 undetermined, +1 up-leg (seeking a high), -1 down-leg
    hi = highs[0]; hi_i = 0
    lo = lows[0]; lo_i = 0
    for i in range(1, n):
        h = highs[i]; l = lows[i]
        if direction >= 0:                           # up-leg (or undetermined): track the high, watch for a
            if h > hi:                               # down-retrace to confirm it
                hi, hi_i = h, i
            elif l <= hi * (1.0 - thr):
                piv.append((hi_i, hi, True, i))
                direction = -1; lo, lo_i = l, i
                continue
        if direction <= 0:                           # down-leg (or undetermined): track the low, watch for an
            if l < lo:                               # up-retrace to confirm it
                lo, lo_i = l, i
            elif h >= lo * (1.0 + thr):
                piv.append((lo_i, lo, False, i))
                direction = 1; hi, hi_i = h, i
    return piv


def _zigzag_pivots(highs, lows, thr):
    """As _zigzag_confirmed but without the confirm bar. -> [(i, price, is_high)]."""
    return [(pb, p, ih) for pb, p, ih, _cb in _zigzag_confirmed(highs, lows, thr)]


def detect_structure_zigzag(highs, lows, thr=None):
    """ZigZag-based structure labels (LIVE overlay). Same output contract as detect_structure. `thr` is a
    FRACTION (0.002 = 0.2%); defaults to ZIGZAG_PCT / 100."""
    if thr is None:
        thr = ZIGZAG_PCT / 100.0
    out = []
    prev_hi = prev_lo = None
    for i, p, is_high in _zigzag_pivots(highs, lows, thr):     # already in bar order, alternating
        if is_high:
            if prev_hi is not None:
                out.append((i, p, "HH" if p > prev_hi else "LH", True))
            prev_hi = p
        else:
            if prev_lo is not None:
                out.append((i, p, "HL" if p > prev_lo else "LL", False))
            prev_lo = p
    return out


def detect_choch(highs, lows, closes, thr=None):
    """Change-of-Character on the scalp ZigZag: the FIRST close-break of the last opposing swing AGAINST the
    prevailing micro-trend. In an up-leg (bias set by the last HH), a close BELOW the last confirmed swing low
    (the HL) is a BEARISH CHoCH; in a down-leg (last LL), a close ABOVE the last confirmed swing high (the LH)
    is a BULLISH CHoCH. Bias bootstraps from the first HH/LL then flips only at each CHoCH. Causal — a level
    can only break after its swing CONFIRMS. -> [(swing_bar, swing_price, break_bar, 'bull'|'bear')]."""
    if thr is None:
        thr = ZIGZAG_PCT / 100.0
    n = len(highs)
    if n < 2:
        return []
    by_confirm = {}                                  # confirm_bar -> [(pivot_bar, price, is_high)]
    prev_hi = prev_lo = None
    for pb, p, ih, cb in _zigzag_confirmed(highs, lows, thr):
        prev_hi = p if ih else prev_hi               # (labels not needed here, but track for bootstrap below)
        prev_lo = p if not ih else prev_lo
        by_confirm.setdefault(cb, []).append((pb, p, ih))
    # bootstrap bias from the first higher-high / lower-low seen, then flip only on CHoCH
    prev_hi = prev_lo = None
    bias = 0
    last_sh = last_sl = None                          # (bar, price) of the last confirmed swing high / low
    out = []
    for i in range(n):
        for pb, p, ih in by_confirm.get(i, ()):
            if ih:
                if bias == 0 and prev_hi is not None and p > prev_hi:
                    bias = 1                          # first higher-high -> up character
                prev_hi = p; last_sh = (pb, p)
            else:
                if bias == 0 and prev_lo is not None and p < prev_lo:
                    bias = -1                         # first lower-low -> down character
                prev_lo = p; last_sl = (pb, p)
        c = closes[i]
        if bias == 1 and last_sl is not None and c < last_sl[1]:
            out.append((last_sl[0], last_sl[1], i, "bear")); bias = -1
        elif bias == -1 and last_sh is not None and c > last_sh[1]:
            out.append((last_sh[0], last_sh[1], i, "bull")); bias = 1
    return out
