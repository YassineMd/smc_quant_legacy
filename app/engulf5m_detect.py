"""5m ABSORPTION WALL — an absorption/engulf REJECTION off an Order-Flow WALL's radar area — LIVE overlay (5m ONLY).
(display name "5m Absorption Wall"; internal keys stay m10_engulf5m / engulf5m_detect / _e5m_*.)

REWORKED 2026-08-13 (user): no longer S/R-dependent — the context is now WALLS / radar area, mirroring the 15m
Engulfing Wall rework. A BOUNCE/defend setup: inside a BUY wall (support) radar a bullish absorption-engulf rejects
UP -> LONG; inside a SELL wall (resistance) radar a bearish one rejects DOWN -> SHORT. The candle criteria are
preserved from the 5m Absorption S/R (body engulf + absorption |A|>=1 EXTREME + non-doji + rejection body); only the
S/R regime / continuation-bias / overlap+into-zone guards / VA confluence are replaced by "is this candle inside a
same-side wall's radar visit" (absorption_level_detect radar_runs).

LONG (SHORT mirrors): the signal candle sits in a SUPPORT wall's radar; a bullish body-ENGULF (open<=prev body low,
  close>=prev body top) whose body > UPPER wick (a rejection up), at an absorption extreme |A|>=1.
TIERS: |A|>=1 -> GREEN(long)/RED(short) triangle; |A|>=2 -> GOLD triangle + 1:2 TP. NO reversal exception.
EXIT: SL 0.1% beyond the WIDEST of {previous candle, entry candle} extreme. TP 1:1.5 (GOLD 1:2). Entry = engulf close.

detect(buckets, walls=None, skip_last=True, absorp=None) -> [{i, side(+1/-1), entry, sl, tp, src('WALL'), gold(bool)}].
`walls` = absorption_level_detect.detect() marks (detected internally if None). ⚠ IN-PROGRESS eyeball variant, NOT proven.
"""
from __future__ import annotations

from . import absorption_level_detect as _al
from . import absorption as _absorption
from .engulf_sr_detect import _ohlc                        # reuse the parity-verified OHLC accessor

SL_PAD = 0.001       # structural stop 0.1% beyond the widest of {prev, entry} extreme
RR = 1.5             # base reward:risk
RR_GOLD = 2.0        # GOLD (|A| >= 2) -> 1:2
ABS_EASY = -1.0      # badge gate: very-easy      (A <= this)
ABS_HEAVY = 1.0      # badge gate: heavy/absorbed (A >= this)
GOLD_ABS = 2.0       # GOLD badge + 1:2 TP: |A| >= this


def _radar_flags(buckets, walls, n):
    """Per-bar (in_support_radar, in_resistance_radar) from the walls' radar visit windows. Detects walls if None."""
    if walls is None:
        try:
            walls = _al.detect(buckets, skip_last=False)
        except Exception:
            walls = []
    sup = [False] * n; res = [False] * n
    for w in (walls or []):
        s = w.get("side")
        for r in w.get("radar_runs", ()):
            if len(r) < 2:
                continue
            rk0 = max(0, int(r[0])); rk1 = min(n - 1, int(r[1]))
            for k in range(rk0, rk1 + 1):
                if s == "S":
                    sup[k] = True
                elif s == "R":
                    res[k] = True
    return sup, res


def current_bias(buckets, walls=None):
    """The overlay's CURRENT wall lean at the live edge: 'long' when the last bar sits in a SUPPORT wall's radar
    (buyers defending below), 'short' in a RESISTANCE wall's radar, None when neither or both (ambiguous)."""
    n = len(buckets)
    if n < 3:
        return None
    sup, res = _radar_flags(buckets, walls, n)
    i = n - 1
    if sup[i] and not res[i]:
        return "long"
    if res[i] and not sup[i]:
        return "short"
    return None


def detect(buckets, walls=None, skip_last=True, absorp=None):
    n = len(buckets)
    if n < 3:
        return []
    O = [0.0] * n; C = [0.0] * n; Hi = [0.0] * n; Lo = [0.0] * n
    for i, b in enumerate(buckets):
        O[i], C[i], Hi[i], Lo[i] = _ohlc(b)
    sup_radar, res_radar = _radar_flags(buckets, walls, n)

    def nd(i):
        b = abs(C[i] - O[i]); return b > (Hi[i] - max(O[i], C[i])) and b > (min(O[i], C[i]) - Lo[i])

    out = []
    for i in range(1, (n - 1) if skip_last else n):
        o, c, h, l = O[i], C[i], Hi[i], Lo[i]
        if o <= 0 or c <= 0 or (h - l) <= 0 or not nd(i):
            continue
        if not (sup_radar[i] or res_radar[i]):                   # must sit AT a wall's radar area (else no signal)
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
        if not (a <= ABS_EASY or a >= ABS_HEAVY):                # badge gate: |A| >= 1 (middle band skipped)
            continue
        pbhi = max(O[i - 1], C[i - 1]); pblo = min(O[i - 1], C[i - 1]); body = abs(c - o)
        side = 0
        long_eng = c > o and o <= pblo and c >= pbhi
        short_eng = c < o and o >= pbhi and c <= pblo
        if sup_radar[i] and long_eng and body > (h - max(o, c)):        # bounce UP off a support wall (rejection body)
            side = 1
        elif res_radar[i] and short_eng and body > (min(o, c) - l):     # bounce DOWN off a resistance wall
            side = -1
        if side == 0:
            continue
        if side > 0:
            ext = min(Lo[i], Lo[i - 1]); sl = ext * (1 - SL_PAD)  # SL 0.1% beyond the WIDEST (lowest) of {prev, entry}
            if sl >= c:
                continue
        else:
            ext = max(Hi[i], Hi[i - 1]); sl = ext * (1 + SL_PAD)  # SL 0.1% beyond the WIDEST (highest) of {prev, entry}
            if sl <= c:
                continue
        sld = (c - sl) if side > 0 else (sl - c)
        gold = a <= -GOLD_ABS or a >= GOLD_ABS
        rr = RR_GOLD if gold else RR                              # gold -> 1:2, else 1:1.5
        out.append(dict(i=i, side=side, entry=c, sl=sl, tp=c + rr * sld * side, src="WALL", conf=False, gold=gold))
    return out
