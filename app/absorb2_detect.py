"""5m ABSORPTION-SEQUENCE setup — LIVE terminal overlay signal, shown inside the 5m Engulfing overlay as a BLUE (long)
/ ORANGE (short) LOSANGE. NOT an engulf: a two-candle absorption continuation in the S/R trend.

  C1 (prev bar): WITH-TREND (may be small-bodied), with an ORANGE/BLUE BORDER = a delta-vs-direction divergence. BLUE = a BULLISH
                 candle on NEGATIVE delta (price closed UP on net aggressive SELLING -> buyers absorbed it); ORANGE = a
                 BEARISH candle on POSITIVE delta (price closed DOWN on net aggressive BUYING -> sellers absorbed it).
                 delta = buy_vol - sell_vol. A long needs a BLUE C1, a short needs an ORANGE C1.
  C2 (signal bar, follows C1): WITH-TREND, non-doji, absorption A <= 0 (a with-trend candle that is NOT absorbed).

Same S/R machinery as the 5m engulf strategy for the CONTINUATION (losange): bias = last-mitigation + newest-created/
newest-active level; guards = no support/resistance overlap + don't fire INTO the opposite zone (widened 5m edges).
EXCEPTION REVERSAL (TRIANGLE, rev=True): when the pattern is valid but the S/R continuation bias/guards do NOT hold,
fire it ANYWAY against the bias, omitting the whole S/R rule (operator choice — the pattern outranks the structure).
EXIT identical: SL 0.1% beyond the widest of {C1,C2} extreme; TP 1:1.5, bumped to 1:2 on a VA+SR confluence. Entry = C2 close.

⚠ IN-SAMPLE candidate — NOT a proven edge. Study: study/absorb2_setup.py (delta-border variant).

detect(buckets, skip_last=True) -> [{i, side(+1/-1), entry, sl, tp, src('ABSORB2'), conf, rev(bool)}]
"""
from __future__ import annotations

import datetime as _dt

from . import support_resistance as _sr
from . import absorption as _absorption
from .engulf_sr_detect import _ohlc, _daily_va

K = _sr.SR_PIVOT_K
SL_PAD = 0.001
RR = 1.5
RR_CONF = 2.0
VA_BAND = 0.0015
ABS_C2 = 0.0         # C2 absorption gate: A <= 0 (a with-trend candle that is NOT absorbed / an easy push)


def detect(buckets, skip_last=True, levels=None, absorp=None, dayva=None, require_border=True):
    n = len(buckets)
    if n < 2 * K + 2:
        return []
    O = [0.0] * n; C = [0.0] * n; Hi = [0.0] * n; Lo = [0.0] * n; DLT = [0.0] * n
    for i, b in enumerate(buckets):
        O[i], C[i], Hi[i], Lo[i] = _ohlc(b)
        DLT[i] = float(b.get("buy_vol", 0.0) or 0.0) - float(b.get("sell_vol", 0.0) or 0.0)   # delta = buy - sell aggression
    if levels is None:                                           # shared in from the terminal to avoid recompute
        levels = _sr.detect(buckets, K, zone_mitigation=True)    # 5m: break past the WIDENED area edge
    SUP = [x for x in levels if x["kind"] == "S"]; RES = [x for x in levels if x["kind"] == "R"]
    lev_sorted = sorted(levels, key=lambda x: x["i0"]); mrc = [None] * n; pi = 0; cur = None
    for i in range(n):
        while pi < len(lev_sorted) and lev_sorted[pi]["i0"] + K <= i:
            cur = lev_sorted[pi]["kind"]; pi += 1
        mrc[i] = cur
    mits = sorted((x["i1"], x["kind"]) for x in levels if x["i1"] is not None)
    lastmit = [None] * n; mj = 0; mk = None
    for i in range(n):
        while mj < len(mits) and mits[mj][0] <= i:
            mk = mits[mj][1]; mj += 1
        lastmit[i] = mk
    if dayva is None:
        dayva = _daily_va(buckets)

    def nd(i):
        b = abs(C[i] - O[i]); return b > (Hi[i] - max(O[i], C[i])) and b > (min(O[i], C[i]) - Lo[i])

    def active(levs, i):
        return [x for x in levs if x["i0"] + K <= i and (x["i1"] is None or x["i1"] > i)]

    def mru(i):
        best = None; bi = -1
        for x in active(SUP, i) + active(RES, i):
            if x["i0"] > bi:
                bi = x["i0"]; best = x["kind"]
        return best

    def touches(i, levs):
        for x in active(levs, i):
            zlo = x["zlo"]; zhi = x["zhi"]
            if (Lo[i] <= zhi and Hi[i] >= zlo) or (zlo <= O[i] <= zhi):
                return True
        return False

    def overlap(i):
        sup = [(x["zlo"], x["zhi"]) for x in active(SUP, i)]; res = [(x["zlo"], x["zhi"]) for x in active(RES, i)]
        return any(slo <= rhi and shi >= rlo for slo, shi in sup for rlo, rhi in res)

    def va_ref(i):
        st = float(buckets[i].get("start_time", 0.0) or 0.0)
        if st <= 0:
            return None, None
        va = dayva.get(_dt.datetime.utcfromtimestamp(st).date() - _dt.timedelta(days=1))
        return (va[1], va[0]) if va else (None, None)

    def at_va(i, level):
        return level is not None and ((Lo[i] <= level <= Hi[i]) or abs(O[i] - level) <= VA_BAND * O[i])

    def border(i):
        """(blue, orange) BORDER = delta-vs-direction divergence. BLUE = a BULLISH candle on NEGATIVE delta (price up on
        net selling -> buyers absorbing). ORANGE = a BEARISH candle on POSITIVE delta (price down on net buying)."""
        return (C[i] > O[i] and DLT[i] < 0.0), (C[i] < O[i] and DLT[i] > 0.0)

    out = []
    for i in range((n - 1) if skip_last else n):
        if i < K + 1:
            continue
        if O[i] <= 0 or C[i] <= 0 or (Hi[i] - Lo[i]) <= 0 or O[i - 1] <= 0 or (Hi[i - 1] - Lo[i - 1]) <= 0:
            continue
        if absorp is not None:
            a2 = absorp[i]
        else:
            try:
                a2 = _absorption.absorption(buckets, i)[0]
            except Exception:
                a2 = None
        if a2 is None or a2 > ABS_C2 or not nd(i):    # C2 must be a real (non-doji) move; C1 need NOT be — the border
            continue                                  # (a bullish/bearish candle on divergent delta) can be small-bodied
        _ov = overlap(i); blue1, orange1 = border(i - 1)
        for side in (1, -1):
            if side > 0:
                bias = lastmit[i] == "R" and (mrc[i] == "S" or mru(i) == "S")
                trend = (C[i] > O[i]) and (C[i - 1] > O[i - 1]); imb_ok = blue1
                guard = (not _ov) and (not touches(i, RES))
            else:
                bias = lastmit[i] == "S" and (mrc[i] == "R" or mru(i) == "R")
                trend = (C[i] < O[i]) and (C[i - 1] < O[i - 1]); imb_ok = orange1
                guard = (not _ov) and (not touches(i, SUP))
            if not (trend and (imb_ok or not require_border)):    # C1 border + C2 with-trend direction (A<=0 already gated)
                continue
            # continuation (LOSANGE) when the S/R bias + guards hold; else EXCEPTION REVERSAL (TRIANGLE) — fire the pattern
            # against the bias, omitting the whole S/R rule (operator choice: pattern > S/R structure here).
            rev = not (bias and guard)
            c = C[i]
            if side > 0:
                sl = min(Lo[i], Lo[i - 1]) * (1 - SL_PAD)
                if sl >= c:
                    continue
                conf = at_va(i, va_ref(i)[1]) and touches(i, SUP)
            else:
                sl = max(Hi[i], Hi[i - 1]) * (1 + SL_PAD)
                if sl <= c:
                    continue
                conf = at_va(i, va_ref(i)[0]) and touches(i, RES)
            sld = (c - sl) if side > 0 else (sl - c)
            rr = RR_CONF if conf else RR
            out.append(dict(i=i, side=side, entry=c, sl=sl, tp=c + rr * sld * side, src="ABSORB2", conf=conf, rev=rev))
            break
    return out
