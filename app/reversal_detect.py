"""1h REVERSAL candle — CAUSAL detector for the live '1h reversal' overlay (m10_reversal, 1h only).

Fires a RED lozenge ABOVE a TOP-reversal candle (up->down flip expected) and a GREEN lozenge BELOW a BOTTOM-reversal
candle. Built from study/reversal_candle_1h.py, which found a 1h reversal candle is a WIDE-RANGE REJECTION candle: a
big OPPOSING wick, a SMALL body, a CLOSE back off the extreme it made, and (strong tier) an EFF-AGG exhaustion
blow-off. Every input is CAUSAL — the candle itself + a trailing window — unlike the descriptive study's ZigZag pivots
(which use look-ahead). So this is the study's anatomy turned into a live, no-look-ahead signal.

TOP    (side -1, red above):   upper_wick/range >= WICK, close_in_range <= CLOSE, range% >= RANGE_MULT x trailing median.
BOTTOM (side +1, green below): lower_wick/range >= WICK, close_in_range >= 1-CLOSE, range% >= RANGE_MULT x median.
STRONG tier (gold ring): + an eff-agg exhaustion blow-off in the TREND direction (effagg_sp >= +EFF at a top / <= -EFF
  at a bottom) — the causal flow signature of the flip. Highlight only; it does not change which candles fire.

detect(buckets, skip_last=True) -> [{i, side(+1/-1), tier('gold'|'normal'), strong(bool)}]  (i in passed-list space)
"""
from __future__ import annotations

from .engulf_sr_detect import _ohlc                        # parity-verified OHLC accessor (open_price/close_price safe)

WICK = 0.40          # opposing wick >= this fraction of the candle's range (the rejection)
CLOSE = 0.40         # close finishes within this fraction of the range on the reversal side (closed off the extreme)
RANGE_MULT = 1.3     # candle range% >= this x the trailing-median range% (climactic / wide-range)
RANGE_WIN = 30       # trailing window for the median range% baseline
EFF = 15.0           # |eff-agg spread| for the STRONG (exhaustion blow-off) tier
MIN_MED = 1e-9       # guard: skip when the trailing median range is degenerate


def _median(xs):
    s = sorted(xs); m = len(s)
    if m == 0:
        return 0.0
    return s[m // 2] if (m % 2) else 0.5 * (s[m // 2 - 1] + s[m // 2])


def detect(buckets, skip_last=True):
    n = len(buckets)
    if n < RANGE_WIN + 2:
        return []
    O = [0.0] * n; C = [0.0] * n; H = [0.0] * n; L = [0.0] * n; RP = [0.0] * n
    for i, b in enumerate(buckets):
        O[i], C[i], H[i], L[i] = _ohlc(b)
        RP[i] = ((H[i] - L[i]) / O[i] * 100.0) if (O[i] > 0 and H[i] > L[i]) else 0.0
    # eff-agg causal spread (trailing 150 inside eff_causal_share) -> STRONG tier only; absence just drops the ring
    eff = None
    try:
        from . import pivot_detect as _PD
        share = _PD.eff_causal_share(buckets)
        eff = [((2.0 * float(s) - 1.0) * 100.0 if s == s else None) for s in share]
    except Exception:
        eff = None
    hi_n = (n - 1) if skip_last else n
    out = []
    for i in range(RANGE_WIN, hi_n):
        o, c, h, l = O[i], C[i], H[i], L[i]
        rng = h - l
        if rng <= 0 or o <= 0:
            continue
        med = _median([RP[j] for j in range(i - RANGE_WIN, i) if RP[j] > 0])
        if med <= MIN_MED or RP[i] < RANGE_MULT * med:          # not climactic -> not a reversal candle
            continue
        uw = (h - max(o, c)) / rng                              # upper-wick fraction (top rejection)
        lw = (min(o, c) - l) / rng                              # lower-wick fraction (bottom rejection)
        cir = (c - l) / rng                                     # close position in range (0 = at low, 1 = at high)
        if uw >= WICK and cir <= CLOSE:
            side = -1                                           # TOP  -> red lozenge ABOVE
        elif lw >= WICK and cir >= (1.0 - CLOSE):
            side = 1                                            # BOTTOM -> green lozenge BELOW
        else:
            continue
        strong = False
        if eff is not None and eff[i] is not None:
            strong = (eff[i] >= EFF) if side < 0 else (eff[i] <= -EFF)
        out.append({"i": i, "side": side, "tier": "gold" if strong else "normal", "strong": strong})
    return out
