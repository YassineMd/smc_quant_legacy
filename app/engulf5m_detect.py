"""5m ABSORPTION S/R (structure-continuation engulf) — LIVE terminal overlay (5m ONLY) — EYEBALL candidate.

A continuation engulf in the direction the S/R structure is building. Fire a LONG when BOTH: (a) the newest-CREATED
S/R level is a SUPPORT *OR* the newest still-UNMITIGATED (active) level is a support, and (b) the most-recent
MITIGATION (last level broken, any kind) was a RESISTANCE. SHORT mirrors: (newest-created OR newest-unmitigated is a
resistance) AND the last break was a support. The OR catches a trend where the newest CREATED pivot is a fresh
counter-bounce (e.g. a downtrend's bounce-low support) while the newest ACTIVE level still reads the trend (a
resistance) — which the created-only rule missed. The engulf candle must be a body-engulf, non-doji, and at an
absorption EXTREME (very-easy A<=-1 OR heavy A>=+1): |A|>=1 -> RED/GREEN badge, |A|>=2 -> GOLD badge.

NO reversal exception (removed 2026-07-30): the strategy fires ONLY on the continuation bias. Every signal is drawn as
a TRIANGLE (up = long, down = short) — no losanges, no bias-bypass reversals.

GUARDS (S/R indicator zones only, NOT VP): skip a bar where a support zone overlaps a resistance zone; skip a LONG
  whose candle touches a resistance zone / a SHORT touching a support zone (don't fire into the opposite structure).
  Zones are AREAS: the pivot candle's full range, extended half its height BELOW a support / ABOVE a resistance.
EXIT: SL 0.1% beyond the WIDEST of {previous candle, entry candle} extreme (lowest low for long / highest high for
  short). TP 1:1.5, bumped to 1:2 when the entry is a VA+SR CONFLUENCE (touches BOTH a prev-day value-area edge AND a
  same-side S/R zone). Entry = the engulf candle's close.

⚠ IN-PROGRESS eyeball candidate — verified IN-SAMPLE on the 5m reconstruction it does NOT clear the fee-adjusted
break-even. Shipped 5m-only so the user can watch it live and iterate, NOT a proven edge.

detect(buckets, skip_last=True) -> [{i, side(+1/-1), entry, sl, tp, src('VASR' if confluence else ''), conf(bool), gold(bool)}]
"""
from __future__ import annotations

import datetime as _dt

from . import support_resistance as _sr
from . import absorption as _absorption
from .engulf_sr_detect import _ohlc, _daily_va

K = _sr.SR_PIVOT_K
SL_PAD = 0.001       # structural stop 0.1% beyond the widest of {prev, entry} extreme
RR = 1.5             # base reward:risk
RR_CONF = 2.0        # VA+SR confluence -> 1:2
VA_BAND = 0.0015     # "opens at" a VA edge = the open within 0.15% of it
ABS_EASY = -1.0      # RED/GREEN badge gate: very-easy      (A <= this)
ABS_HEAVY = 1.0      # RED/GREEN badge gate: heavy/absorbed (A >= this)
GOLD_ABS = 2.0       # GOLD badge: |A| >= this


def current_bias(buckets, levels=None):
    """The 5m ENGULF strategy's CURRENT structural bias at the live edge (last bucket): 'long' when the last
    MITIGATION broke a RESISTANCE and the newest (created or still-active) level is a SUPPORT, 'short' is the
    mirror, None when there's no clear structure or an S/R overlap. This is the directional lean the LONG/SHORT
    signals key off (lastmit + newest-level) — the per-candle absorption/touch ENTRY gates are NOT applied, so
    it's a bias readout, not a signal."""
    n = len(buckets)
    if n < 2 * K + 2:
        return None
    if levels is None:
        levels = _sr.detect(buckets, K, zone_mitigation=True)
    i = n - 1
    SUP = [x for x in levels if x["kind"] == "S"]; RES = [x for x in levels if x["kind"] == "R"]

    def _active(levs):
        return [x for x in levs if x["i0"] + K <= i and (x["i1"] is None or x["i1"] > i)]

    act_sup = _active(SUP); act_res = _active(RES)
    for a in act_sup:                                   # S/R overlap at the live edge -> ambiguous, no bias
        for b in act_res:
            if a["zlo"] <= b["zhi"] and a["zhi"] >= b["zlo"]:
                return None
    lastmit = None                                      # kind of the most-recent mitigated (broken) level, i1 <= i
    for i1, kind in sorted((x["i1"], x["kind"]) for x in levels if x["i1"] is not None):
        if i1 <= i:
            lastmit = kind
        else:
            break
    mrc = None                                          # kind of the most-recent CREATED level (i0 + K <= i)
    for x in sorted(levels, key=lambda z: z["i0"]):
        if x["i0"] + K <= i:
            mrc = x["kind"]
        else:
            break
    best = None; bi = -1                                # kind of the most-recent still-ACTIVE level
    for x in act_sup + act_res:
        if x["i0"] > bi:
            bi = x["i0"]; best = x["kind"]
    mru = best
    if lastmit == "R" and (mrc == "S" or mru == "S"):
        return "long"
    if lastmit == "S" and (mrc == "R" or mru == "R"):
        return "short"
    return None


def detect(buckets, skip_last=True, levels=None, absorp=None, dayva=None):
    n = len(buckets)
    if n < 2 * K + 2:
        return []
    O = [0.0] * n; C = [0.0] * n; Hi = [0.0] * n; Lo = [0.0] * n
    for i, b in enumerate(buckets):
        O[i], C[i], Hi[i], Lo[i] = _ohlc(b)
    if levels is None:                                     # `levels`/`absorp`/`dayva` may be shared in from the terminal
        levels = _sr.detect(buckets, K, zone_mitigation=True)   # 5m: a level breaks only past the WIDENED area edge
    SUP = [x for x in levels if x["kind"] == "S"]; RES = [x for x in levels if x["kind"] == "R"]

    # ---- most-recently-CREATED (confirmed) level kind at each bar
    lev_sorted = sorted(levels, key=lambda x: x["i0"]); mrc = [None] * n; pi = 0; cur = None
    for i in range(n):
        while pi < len(lev_sorted) and lev_sorted[pi]["i0"] + K <= i:
            cur = lev_sorted[pi]["kind"]; pi += 1
        mrc[i] = cur
    # last-MITIGATION kind at each bar: the kind of the level whose break (i1) is the most recent one <= i (causal).
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
        """Kind of the most-recent UNMITIGATED (still-active) level at bar i = the largest-i0 confirmed, unbroken
        level. In a downtrend the newest CREATED level is often a bounce-low support, but the newest still-active
        level is a resistance (a lower high price hasn't reclaimed) — this catches that."""
        best = None; bi = -1
        for x in active(SUP, i) + active(RES, i):
            if x["i0"] > bi:
                bi = x["i0"]; best = x["kind"]
        return best

    def touches(i, levs):
        for x in active(levs, i):
            zlo = x["zlo"]; zhi = x["zhi"]                            # WIDENED S/R area (support extends below / resistance above)
            if (Lo[i] <= zhi and Hi[i] >= zlo) or (zlo <= O[i] <= zhi):
                return True
        return False

    def overlap(i):
        sup = [(x["zlo"], x["zhi"]) for x in active(SUP, i)]
        res = [(x["zlo"], x["zhi"]) for x in active(RES, i)]
        return any(slo <= rhi and shi >= rlo for slo, shi in sup for rlo, rhi in res)

    def va_ref(i):
        st = float(buckets[i].get("start_time", 0.0) or 0.0)
        if st <= 0:
            return None, None
        va = dayva.get(_dt.datetime.utcfromtimestamp(st).date() - _dt.timedelta(days=1))
        return (va[1], va[0]) if va else (None, None)      # (vah, val)

    def at_va(i, level):
        return level is not None and ((Lo[i] <= level <= Hi[i]) or abs(O[i] - level) <= VA_BAND * O[i])

    out = []
    for i in range(1, (n - 1) if skip_last else n):
        o, c, h, l = O[i], C[i], Hi[i], Lo[i]
        if o <= 0 or c <= 0 or (h - l) <= 0 or not nd(i):
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
        if not (a <= ABS_EASY or a >= ABS_HEAVY):                # RED/GREEN badge gate: |A| >= 1 (middle band skipped)
            continue
        if overlap(i):                                           # FILTER: an active support zone overlaps a resistance -> no trade
            continue
        pbhi = max(O[i - 1], C[i - 1]); pblo = min(O[i - 1], C[i - 1]); vah, val = va_ref(i)
        side = 0; conf = False
        long_eng = c > o and o <= pblo and c >= pbhi
        short_eng = c < o and o >= pbhi and c <= pblo
        # CONTINUATION bias ONLY (no reversal exception): last-mitigation = opposite kind, newest created/active = our kind,
        # and the candle does NOT fire INTO the opposite zone.
        if long_eng and not touches(i, RES):
            if lastmit[i] == "R" and (mrc[i] == "S" or mru(i) == "S"):
                side = 1; conf = at_va(i, val) and touches(i, SUP)
        elif short_eng and not touches(i, SUP):
            if lastmit[i] == "S" and (mrc[i] == "R" or mru(i) == "R"):
                side = -1; conf = at_va(i, vah) and touches(i, RES)
        if side == 0:
            continue
        if side > 0:
            ext = min(Lo[i], Lo[i - 1]); sl = ext * (1 - SL_PAD)   # SL 0.1% beyond the WIDEST (lowest) of {prev, entry}
            if sl >= c:
                continue
        else:
            ext = max(Hi[i], Hi[i - 1]); sl = ext * (1 + SL_PAD)   # SL 0.1% beyond the WIDEST (highest) of {prev, entry}
            if sl <= c:
                continue
        sld = (c - sl) if side > 0 else (sl - c)
        rr = RR_CONF if conf else RR; tp = c + rr * sld * side     # 1:1.5, or 1:2 on a VA+SR confluence
        out.append(dict(i=i, side=side, entry=c, sl=sl, tp=tp, src=("VASR" if conf else ""),
                        conf=conf, gold=(a <= -GOLD_ABS or a >= GOLD_ABS)))
    return out
