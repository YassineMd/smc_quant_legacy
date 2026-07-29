"""5m ENGULFING S/R (structure-continuation) — LIVE terminal overlay (5m ONLY) — EYEBALL candidate.

A continuation engulf in the direction the S/R structure is building. Fire a LONG when BOTH: (a) the newest-CREATED
S/R level is a SUPPORT *OR* the newest still-UNMITIGATED (active) level is a support, and (b) the most-recent
MITIGATION (last level broken, any kind) was a RESISTANCE. SHORT mirrors: (newest-created OR newest-unmitigated is a
resistance) AND the last break was a support. The OR catches a trend where the newest CREATED pivot is a fresh
counter-bounce (e.g. a downtrend's bounce-low support) while the newest ACTIVE level still reads the trend (a
resistance) — which the created-only rule missed. The engulf candle must be a body-engulf,
non-doji, and at an absorption EXTREME (very-easy A<=-1 OR heavy A>=+1 — the middle band is skipped, from the 5m sweep
where both tails beat the centre). No skew. (Condition (b) is the terminal's "last-mitigation" bias, per the 15m rule.)

EXCEPTION REVERSAL: a LONG may ALSO fire (bias bypassed) when the engulf is INSIDE a support that has HELD — no candle
body has dipped below the BODY of the FIRST candle that created that support (earliest i0, even across a merged
support), from its formation up to the signal. SHORT mirrors (engulf inside a resistance no body has closed above).

GUARDS (S/R indicator): skip a bar where a support zone overlaps a resistance zone; skip a LONG whose candle touches a
  resistance zone / a SHORT touching a support zone (don't fire into the opposite structure).
EXIT: SL 0.1% beyond the WIDEST of {previous candle, entry candle} extreme (lowest low for long / highest high for
  short). TP 1:1.5, bumped to 1:2 when the entry is a VA+SR CONFLUENCE (touches BOTH a prev-day value-area edge AND a
  same-side S/R zone). Entry = the engulf candle's close.

⚠ IN-PROGRESS eyeball candidate — verified IN-SAMPLE on the 5m reconstruction it does NOT clear the fee-adjusted
break-even (win rate ~43% vs ~47% needed at 1:1.5; PF ~0.8 across random-month draws). Shipped 5m-only so the user can
watch it live and iterate, NOT a proven edge. Parity ref: scratch harness engulf5m_v4 (easy/heavy tails).

detect(buckets, skip_last=True) -> [{i, side(+1/-1), entry, sl, tp, src('VASR' if confluence else ''), conf(bool)}]
"""
from __future__ import annotations

import datetime as _dt

from . import support_resistance as _sr
from . import absorption as _absorption
from .engulf_sr_detect import _ohlc, _daily_va

K = _sr.SR_PIVOT_K
SL_PAD = 0.001       # structural stop 0.1% beyond the widest extreme
RR = 1.5             # base reward:risk
RR_CONF = 2.0        # VA+SR confluence -> 1:2
VA_BAND = 0.0015     # "opens at" a VA edge = the open within 0.15% of it
ABS_EASY = -1.0      # entry absorption extreme: very-easy  (A <= this)
ABS_HEAVY = 1.0      # entry absorption extreme: heavy/absorbed (A >= this)
SE2_ABS = -0.5       # Case-2 (SE2) re-entry: looser absorption gate (A <= this)
GOLD_ABS = 2.5       # |A| >= this -> GOLD badge (signal) / GOLD sphere (non-signal)


def extreme_absorption(buckets, thr=GOLD_ABS, start=0):
    """Candles (from index `start`) whose |absorption| >= thr, as [(i, A), ...] — the terminal draws a GOLD SPHERE on
    the ones that are NOT signals. Absorption uses the full `buckets` for context; `start` skips the off-screen
    warm-up prefix. Scans per bar, so callers should cache by frame."""
    out = []
    for i in range(start, len(buckets)):
        try:
            a = _absorption.absorption(buckets, i)[0]
        except Exception:
            a = None
        if a is not None and (a <= -thr or a >= thr):
            out.append((i, a))
    return out


def sphere_markers(buckets, gold_thr=GOLD_ABS, easy_thr=-1.0, start=0, absorp=None):
    """ONE absorption pass -> (gold, blue) descriptive markers (NOT signals/tiers):
      gold = [(i, A)] with |A| >= gold_thr (the terminal skips the ones that are signals);
      blue = [(i, dir)] = the SECOND candle of TWO consecutive SAME-SIDE candles each with A < easy_thr (a two-push
             easy move; dir +1 both bullish / -1 both bearish).
    Absorption uses the full `buckets` for context; `start` skips the off-screen warm-up prefix. `absorp` (a precomputed
    per-bar A list) is reused when passed, so the terminal can share ONE absorption pass across the 5m overlays."""
    n = len(buckets); A = [None] * n; D = [0] * n
    for i, b in enumerate(buckets):
        o, c, _h, _l = _ohlc(b); D[i] = 1 if c > o else (-1 if c < o else 0)
        if absorp is not None:
            A[i] = absorp[i]
        else:
            try:
                A[i] = _absorption.absorption(buckets, i)[0]
            except Exception:
                A[i] = None
    gold = []; blue = []
    for i in range(len(buckets)):
        a = A[i]
        if i >= start and a is not None and (a <= -gold_thr or a >= gold_thr):
            gold.append((i, a))
        if (i >= max(1, start) and D[i] != 0 and D[i] == D[i - 1]
                and a is not None and A[i - 1] is not None and a < easy_thr and A[i - 1] < easy_thr):
            blue.append((i, D[i]))
    return gold, blue


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

    # ---- most-recently-CREATED (confirmed) level kind at each bar, + earliest opposite mitigation
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

    def _in_zone(i, x):
        zlo = x["zlo"]; zhi = x["zhi"]
        return (Lo[i] <= zhi and Hi[i] >= zlo) or (zlo <= O[i] <= zhi)

    def sup_reversal(i):
        """EXCEPTION: bullish engulf INSIDE a support that has HELD — no candle body dipped below the BODY of the
        FIRST (earliest i0, even across a merged support) candle that created it, from its formation up to (not incl.)
        the signal. Lets a reversal long fire even when the continuation bias fails."""
        ts = [x for x in active(SUP, i) if _in_zone(i, x)]
        if not ts:
            return False
        i0 = min(x["i0"] for x in ts)
        ref = min(O[i0], C[i0])                               # first support candle's BODY bottom
        for j in range(i0 + 1, i):
            if min(O[j], C[j]) < ref:
                return False
        return True

    def res_reversal(i):
        tr = [x for x in active(RES, i) if _in_zone(i, x)]
        if not tr:
            return False
        i0 = min(x["i0"] for x in tr)
        ref = max(O[i0], C[i0])                               # first resistance candle's BODY top
        for j in range(i0 + 1, i):
            if max(O[j], C[j]) > ref:
                return False
        return True

    # Case-2 (SE2) re-entry state: per merged-area KEY (earliest i0 of the touched same-side zones), remember the last
    # signal that fired AT it (SE1) as (bar, open, tp); a later engulf in the same area can re-enter as SE2.
    se1_long = {}; se1_short = {}

    def _sup_key(i):
        return min((x["i0"] for x in active(SUP, i) if _in_zone(i, x)), default=None)

    def _res_key(i):
        return min((x["i0"] for x in active(RES, i) if _in_zone(i, x)), default=None)

    def se2_long(i, a):
        if a > SE2_ABS:                                          # SE2 needs absorption <= -0.5 (easier than the extreme)
            return False
        k = _sup_key(i)
        if k is None or k not in se1_long:
            return False
        s1i, s1o, s1tp = se1_long[k]
        if i <= s1i or O[i] <= s1o:                              # SE2 must open ABOVE SE1's open
            return False
        return all(Hi[j] < s1tp for j in range(s1i + 1, i))      # SE1 TP not reached before SE2

    def se2_short(i, a):
        if a > SE2_ABS:
            return False
        k = _res_key(i)
        if k is None or k not in se1_short:
            return False
        s1i, s1o, s1tp = se1_short[k]
        if i <= s1i or O[i] >= s1o:                              # SE2 must open BELOW SE1's open
            return False
        return all(Lo[j] > s1tp for j in range(s1i + 1, i))

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
        _extreme = a <= ABS_EASY or a >= ABS_HEAVY               # -1/+1 extreme (bias & Case-1 reversal)
        _ov = overlap(i)
        pbhi = max(O[i - 1], C[i - 1]); pblo = min(O[i - 1], C[i - 1]); vah, val = va_ref(i)
        side = 0; conf = False; rev = False; mru_only = False; c2 = False
        long_eng = c > o and o <= pblo and c >= pbhi
        short_eng = c < o and o >= pbhi and c <= pblo
        # bias (needs NO overlap + not INTO the opposite zone) OR a reversal exception (Case-1 held-zone / Case-2 SE2 —
        # both OMIT the overlap + opposite-zone guards, per "overlapping S/R rule can be omitted").
        if long_eng:
            _lm = lastmit[i] == "R"; _cr = _lm and mrc[i] == "S"
            bias = _extreme and (not _ov) and (not touches(i, RES)) and (_cr or (_lm and mru(i) == "S"))
            _r1 = (not bias) and _extreme and sup_reversal(i)
            _r2 = (not bias) and (not _r1) and se2_long(i, a)
            if bias or _r1 or _r2:
                side = 1; conf = at_va(i, val) and touches(i, SUP); rev = not bias; mru_only = bias and not _cr; c2 = _r2
        elif short_eng:
            _lm = lastmit[i] == "S"; _cr = _lm and mrc[i] == "R"
            bias = _extreme and (not _ov) and (not touches(i, SUP)) and (_cr or (_lm and mru(i) == "R"))
            _r1 = (not bias) and _extreme and res_reversal(i)
            _r2 = (not bias) and (not _r1) and se2_short(i, a)
            if bias or _r1 or _r2:
                side = -1; conf = at_va(i, vah) and touches(i, RES); rev = not bias; mru_only = bias and not _cr; c2 = _r2
        if side == 0:
            continue
        if side > 0:
            ext = min(Lo[i], Lo[i - 1]); sl = ext * (1 - SL_PAD)
            if sl >= c:
                continue
        else:
            ext = max(Hi[i], Hi[i - 1]); sl = ext * (1 + SL_PAD)
            if sl <= c:
                continue
        sld = (c - sl) if side > 0 else (sl - c)
        rr = RR_CONF if conf else RR; tp = c + rr * sld * side
        if side > 0 and touches(i, SUP):                         # record this as the area's SE1 for a future re-entry
            k = _sup_key(i)
            if k is not None:
                se1_long[k] = (i, o, tp)
        elif side < 0 and touches(i, RES):
            k = _res_key(i)
            if k is not None:
                se1_short[k] = (i, o, tp)
        out.append(dict(i=i, side=side, entry=c, sl=sl, tp=tp, src=("VASR" if conf else ""),
                        conf=conf, rev=rev, mru_only=mru_only, c2=c2, gold=(a <= -GOLD_ABS or a >= GOLD_ABS)))
    return out
