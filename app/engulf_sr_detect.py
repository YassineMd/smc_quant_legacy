"""1h ENGULFING REVERSAL at Support/Resistance — LIVE terminal overlay detector (1h ONLY) — FORWARD CANDIDATE.

An engulfing reversal where candle 2 reverses off support (long) / resistance (short). Support/Resistance is an
AREA, sourced two ways (union): (a) the causal PREV-DAY 70% value-area edge (VAL = support / VAH = resistance),
and (b) the S/R indicator (app.support_resistance fractal-pivot zones — the pivot candle's whole high-low range).

LONG : c2 bullish + non-doji, c2 touches/opens at support (prev-day VAL, OR an S/R support zone c2 closes ABOVE —
       S/R trades where c2 closes INSIDE the zone are omitted), c2 close > c1 high. Candle 1 must be bearish + non-doji
       — UNLESS candle 2 is R-easy (absorption A <= -0.75) AND skew > 0, in which case c1 may be a doji OR same-direction.
       Those R-easy-relaxed entries are flagged `relaxed` (the terminal draws them BLUE instead of green/red).
SHORT: mirror (resistance = prev-day VAH or an S/R resistance zone c2 closes BELOW; c2 close < c1 open; relaxed needs
       A<=-0.75 AND skew<0).
GUARDS (S/R indicator only): skip a bar where any active support zone OVERLAPS any resistance zone; and skip a LONG
       whose c2 touches a resistance zone / a SHORT whose c2 touches a support zone (don't enter into the opposite S/R).
Exit : SL 0.1% beyond c2 extreme (structural); TP = 1:1.2 the stop distance; if the signal is a VA+SR CONFLUENCE
       (touches BOTH a VA edge and an S/R zone), TP = 1:2.

⚠ NOT a proven edge — verified IN-SAMPLE only on the 18-month reconstruction: the win rate sits at the fee-adjusted
break-even, it is short-biased in a downtrend, and it does NOT survive 15m/4h cross-scale or a strict holdout. The c1
relaxations degrade it monotonically in-sample (strict +30/+40% -> doji-if-R-easy +7/+3% -> doji-or-same-dir -3/-0%);
shipped this doji-or-same-dir variant 1h-only as a FORWARD-TEST candidate, operator choice against the evidence.

detect(buckets, skip_last=True) -> [{i, side(+1/-1), entry, sl, tp, src('VA'/'SR'/'VASR'), relaxed(bool)}]  (i in passed-list space).
"""
from __future__ import annotations

import datetime as _dt

from . import support_resistance as _sr
from . import absorption as _absorption
from .footprint_panel import profile_skewness

VA_BAND = 0.0015     # "opens at" the VA edge = the open within 0.15% of it
SL_PAD = 0.001       # structural stop 0.1% beyond candle 2's extreme
K = _sr.SR_PIVOT_K   # S/R fractal lookback each side (8)
R_EASY = -0.75       # candle 2 absorption A <= this = "R-easy" (an easy, low-absorption move) -> RELAX the c1 gate
FLOW_K = 3           # trailing flow window (buckets, ~3h on 1h) — RING when the PRE-signal flow ALIGNS with the trade
#                      side (LONG net-buy / SHORT net-sell). Highlight only, changes no trades. Parity:
#                      study/engulf_sr_flow_1h.py (flow-veto = align kept; lifted the LONG leg PF 1.20 -> 1.45).


def _ohlc(b):
    o = float(b.get("open", b.get("open_price", 0.0)) or 0.0)
    c = float(b.get("close", b.get("close_price", 0.0)) or 0.0)
    h = float(b.get("high", 0.0) or 0.0)
    l = float(b.get("low", 0.0) or 0.0)
    return o, c, h, l


def _va_poc(prof, pct=0.70):
    """70% value area (val, vah) around the POC — same construction study.mm_skew_feature_matrix.va_poc uses."""
    items = sorted((p, w) for p, w in (prof or {}).items() if w > 0)
    if len(items) < 3:
        return None
    total = sum(w for _, w in items); tgt = pct * total
    pi = max(range(len(items)), key=lambda k: items[k][1]); lo = hi = pi; va = items[pi][1]
    while va < tgt and (lo > 0 or hi < len(items) - 1):
        up = items[hi + 1][1] if hi < len(items) - 1 else -1
        dn = items[lo - 1][1] if lo > 0 else -1
        if up >= dn:
            hi += 1; va += items[hi][1]
        else:
            lo -= 1; va += items[lo][1]
    return items[lo][0], items[hi][0]                    # (val, vah)


def _daily_va(buckets):
    """Per-UTC-day (val, vah) from the day's summed footprint. A day = midnight..23:59 UTC."""
    days = {}
    for b in buckets:
        st = float(b.get("start_time", 0.0) or 0.0)
        if st <= 0:
            continue
        d = _dt.datetime.utcfromtimestamp(st).date()
        prof = days.setdefault(d, {})
        for pr, v in (b.get("levels") or {}).items():
            try:
                p = float(pr)
            except (TypeError, ValueError):
                continue
            prof[p] = prof.get(p, 0.0) + float(v.get("b", 0) or 0) + float(v.get("s", 0) or 0)
    out = {}
    for d, prof in days.items():
        va = _va_poc(prof)
        if va:
            out[d] = va
    return out


def detect(buckets, skip_last=True):
    n = len(buckets)
    if n < 2 * K + 2:
        return []
    O = [0.0] * n; C = [0.0] * n; H = [0.0] * n; Lo = [0.0] * n
    for i, b in enumerate(buckets):
        O[i], C[i], H[i], Lo[i] = _ohlc(b)
    # PRE-signal trailing-flow RSV over bars [i-FLOW_K, i-1] (EXCLUDES the reversal bar) — for the alignment ring only
    bvp = [0.0] * (n + 1); svp = [0.0] * (n + 1)
    for i, b in enumerate(buckets):
        bvp[i + 1] = bvp[i] + float(b.get("buy_vol", 0.0) or 0.0)
        svp[i + 1] = svp[i] + float(b.get("sell_vol", 0.0) or 0.0)

    def _flow(i):
        a = max(0, i - FLOW_K); bs = bvp[i] - bvp[a]; ss = svp[i] - svp[a]
        return (bs - ss) / (bs + ss) if (bs + ss) > 0 else 0.0

    dayva = _daily_va(buckets)
    levels = _sr.detect(buckets, K)
    SUP = [x for x in levels if x["kind"] == "S"]; RES = [x for x in levels if x["kind"] == "R"]

    def nd(i):
        b = abs(C[i] - O[i]); return b > (H[i] - max(O[i], C[i])) and b > (min(O[i], C[i]) - Lo[i])

    def va_ref(i):
        st = float(buckets[i].get("start_time", 0.0) or 0.0)
        if st <= 0:
            return None, None
        va = dayva.get(_dt.datetime.utcfromtimestamp(st).date() - _dt.timedelta(days=1))   # yesterday, causal
        return (va[1], va[0]) if va else (None, None)    # (vah, val)

    def active(levs, i):
        return [x for x in levs if x["i0"] + K <= i and (x["i1"] is None or x["i1"] > i)]

    def at_va(i, level):
        return level is not None and ((Lo[i] <= level <= H[i]) or abs(O[i] - level) <= VA_BAND * O[i])

    def at_sr(i, levs, support):
        for x in active(levs, i):
            i0 = x["i0"]; zlo = min(Lo[i0], H[i0]); zhi = max(Lo[i0], H[i0])
            touch = (Lo[i] <= zhi and H[i] >= zlo) or (zlo <= O[i] <= zhi)
            if touch and (C[i] > zhi if support else C[i] < zlo):   # close BEYOND the zone -> clean rejection
                return True
        return False

    def easy_c2(i, side):
        try:
            a = _absorption.absorption(buckets, i)[0]               # c2 absorption A (POSITIVE = absorbed, <=-0.75 = R-easy)
        except Exception:
            return False
        if a is None or a > R_EASY:
            return False
        sk = profile_skewness(buckets[i].get("levels"))            # + directional skew: long needs skew>0, short skew<0
        return sk is not None and ((sk > 0) if side > 0 else (sk < 0))

    def touches(i, levs):                                          # candle i overlaps ANY active S/R zone in levs (touch only)
        for x in active(levs, i):
            i0 = x["i0"]; zlo = min(Lo[i0], H[i0]); zhi = max(Lo[i0], H[i0])
            if (Lo[i] <= zhi and H[i] >= zlo) or (zlo <= O[i] <= zhi):
                return True
        return False

    def overlap(i):                                               # any active support zone overlaps any resistance zone (price)
        sup = [(min(Lo[x["i0"]], H[x["i0"]]), max(Lo[x["i0"]], H[x["i0"]])) for x in active(SUP, i)]
        res = [(min(Lo[x["i0"]], H[x["i0"]]), max(Lo[x["i0"]], H[x["i0"]])) for x in active(RES, i)]
        return any(slo <= rhi and shi >= rlo for slo, shi in sup for rlo, rhi in res)

    out = []
    for i in range(1, (n - 1) if skip_last else n):
        o, c, h, l = O[i], C[i], H[i], Lo[i]
        if o <= 0 or c <= 0 or h <= 0 or l <= 0 or not nd(i):       # c2 must ALWAYS be non-doji
            continue
        if overlap(i):                                             # ADD#1: support & resistance zones overlap -> skip
            continue
        vah, val = va_ref(i); side = 0; src = ""; relaxed = False
        if c > o and c > H[i - 1]:                                  # LONG: c2 bullish, closes above candle 1's high
            if touches(i, RES):                                    # ADD#2: bullish signal touching a resistance zone -> skip
                continue
            strict = (C[i - 1] < O[i - 1]) and nd(i - 1)           # strict c1 = bearish + non-doji
            if (strict or easy_c2(i, 1)):                          # else needs c2 R-easy + skew>0 (c1 doji OR same-dir)
                va = at_va(i, val); sr = at_sr(i, SUP, True)
                if va or sr:
                    side = 1; src = ("VA" if va else "") + ("SR" if sr else ""); relaxed = not strict
        elif c < o and c < O[i - 1]:                               # SHORT: c2 bearish, closes below candle 1's open
            if touches(i, SUP):                                    # ADD#2: bearish signal touching a support zone -> skip
                continue
            strict = (C[i - 1] > O[i - 1]) and nd(i - 1)           # strict c1 = bullish + non-doji
            if (strict or easy_c2(i, -1)):                         # else needs c2 R-easy + skew<0
                va = at_va(i, vah); sr = at_sr(i, RES, False)
                if va or sr:
                    side = -1; src = ("VA" if va else "") + ("SR" if sr else ""); relaxed = not strict
        if side == 0:
            continue
        sl = l * (1 - SL_PAD) if side > 0 else h * (1 + SL_PAD)
        if (side > 0 and sl >= c) or (side < 0 and sl <= c):
            continue                                                # degenerate (close at/through its own stop)
        sld = (c - sl) if side > 0 else (sl - c)
        rr = 2.0 if src == "VASR" else 1.2                          # VA+SR confluence -> 1:2, else 1:1.2
        fl = _flow(i)
        out.append(dict(i=i, side=side, entry=c, sl=sl, tp=c + rr * sld * side, src=src, relaxed=relaxed,
                        flow=fl, flow_align=((fl > 0) if side > 0 else (fl < 0))))
    return out
