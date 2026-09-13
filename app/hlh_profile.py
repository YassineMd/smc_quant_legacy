# -*- coding: utf-8 -*-
"""HLH Volume Profile -- the ENGINE (pure Python + numpy, no Qt).

A faithful port of study/pine/hlh_volume_profile.pine (Pine v6; spec: study/pine/HLH_VOLUME_PROFILE_SPEC.md).
The ORDER of the steps IS the algorithm -- every step consumes the state the previous one left (used rows,
alive Ds, red LOWs) -- so nothing is reordered or fused here, and the names follow the Pine so the two read
side by side. Input = one PERIOD's intrabar candles (a day of 1-minute candles, a week of 5-minute candles);
output = a PeriodResult: the profile rows, the LOWs / HIGHs, every D (alive or merged), the uncovered areas
and their purple Ds, each D area's time profile, the blocs with their value areas. There is NO x geometry in
here: the drawing layer maps rows -> price and times -> x for whichever canvas it sits on.

Deviations from the Pine, all deliberate and all narrower than the source:
  * a candle whose LOW sits on the period high (a doji at the extreme) clamps to the top row instead of
    iterating a Pine `for` backwards out of the array;
  * Pine `int()` truncates toward zero -- every quantity it is applied to here is >= 0, so floor == trunc;
  * drawing caps / prune budgets / dev-vs-finished arrays are TradingView artefacts (spec section 18).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np

DAY_SECS = 86400
WEEK_SECS = 7 * DAY_SECS


@dataclass
class Params:
    rows: int = 60                 # price rows of the profile
    width_pct: int = 28            # profile width, % of the period (drawing layer)
    low_max_pct: float = 50.0      # a LOW must be < this % of the POC
    high_min_pct: float = 50.0     # a HIGH must be > this % of the POC
    shared_pct: float = 66.0       # shared LOW turns red above this % of an apex
    do_merge: bool = True
    do_uncov: bool = True
    show_d: bool = True
    show_dn: bool = True
    max_ds: int = 0                # 0 = until no HIGH is left
    va_pct: float = 70.0
    tp_bin_min_day: int = 30       # time-profile bin, minutes
    tp_bin_min_week: int = 240
    tp_both: str = "Keep both"     # or "Keep neither"
    tz: str = "UTC"                # days and weeks are cut in this zone


@dataclass
class Candles:
    """One period's intrabar candles. t = open time (epoch s), m = candle length in minutes."""
    t: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    m: np.ndarray

    def __len__(self):
        return int(self.t.shape[0])

    @staticmethod
    def from_lists(t, h, l, c, v, m) -> "Candles":
        return Candles(*(np.asarray(a, dtype=np.float64) for a in (t, h, l, c, v, m)))

    def keep(self, mask: np.ndarray) -> "Candles":
        return Candles(self.t[mask], self.h[mask], self.l[mask], self.c[mask], self.v[mask], self.m[mask])


@dataclass
class DShape:
    num: int                    # 1, 2, 3 ... build order (sets the colour); 0 = purple HIGH
    name: str                   # "D2", or "D2+D4" after a merge
    a: int                      # apex first row
    b: int                      # apex last row
    apexV: float                # apex width (volume)
    up: List[int]               # connected LOW ids, upper leg (closest first)
    dn: List[int]               # connected LOW ids, lower leg
    fbUp: int                   # lowest-point row when the upper leg has no LOW (-1 = none)
    fbDn: int
    alive: bool = True


@dataclass
class TPArea:
    name: str
    num: int                    # colour index of the D (0 = purple)
    yBot: float
    yTop: float
    m: np.ndarray               # minutes in the area per bin
    vo: np.ndarray              # volume traded in the area per bin
    r0: int                     # the area's lowest profile row
    r1: int                     # the area's highest profile row
    topRow: int = -1            # level rows (-1 = the side has no end point)
    botRow: int = -1


@dataclass
class Bloc:
    area: int                   # index in the period's areas list
    num: int                    # 1, 2, 3 ... inside its area
    bs: int                     # first bin
    be: int                     # one past the last bin
    mins: float
    vol: float
    keep: bool = False
    tag: str = ""
    vaLo: int = -1              # value-area rows (profile row indexes), -1 = no volume
    vaHi: int = -1


@dataclass
class PeriodResult:
    is_week: bool
    key: int
    n: int                      # candles in the period
    t_first: float              # first candle open (Pine P.startTime)
    t_last: float               # last candle open
    per_mid: float              # period start (00:00 / Monday 00:00)
    per_end: float              # period end (23:59 / Sunday 23:59)
    bin_secs: int
    n_bins: int
    lo: float
    hi: float
    step: float
    vp: np.ndarray
    maxVol: float
    pS: int
    pE: int
    lowS: List[int]
    lowE: List[int]
    lowId: List[int]
    highS: List[int]
    highE: List[int]
    isHi: List[bool]
    used: List[bool]
    isApex: List[bool]
    covered: List[bool]
    uncS: List[int]
    uncE: List[int]
    ds: List[DShape]            # the regular Ds, build order (dead ones included)
    pds: List[DShape]           # the purple Ds
    allDs: List[DShape]
    endCnt: List[int]
    endApex: List[float]
    areas: List[TPArea]
    blocs: List[Bloc]
    degenerate: bool = False    # hi == lo or no volume: nothing to draw

    # ------------------------------------------------------------------ read helpers for the drawing layer
    def row_y(self, r: int) -> float:
        return self.lo + r * self.step

    def low_red(self, i: int, shared_pct: float) -> bool:
        return self.endCnt[i] >= 2 and self.vp[self.lowS[i]] > self.endApex[i] * shared_pct / 100.0

    def leg_end_rows(self, d: DShape) -> Tuple[int, int]:
        """(topRow, botRow): the level rows of a D, -1 when a side has no end point."""
        upL = end_low(d.up)
        dnL = end_low(d.dn)
        topRow = self.lowE[upL] if upL >= 0 else d.fbUp
        botRow = self.lowS[dnL] if dnL >= 0 else d.fbDn
        return topRow, botRow


# ============================================================================ period keys and windows
def _zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except Exception:
        return ZoneInfo("UTC")


def period_key(t: float, is_week: bool, tz: str = "UTC") -> int:
    """Day key = yyyymmdd. Week key = the yyyymmdd of this instant's Monday (found from noon so a
    daylight-saving hour never lands on the wrong date)."""
    d = datetime.fromtimestamp(float(t), _zone(tz))
    if is_week:
        d = d.replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=d.weekday())
    return d.year * 10000 + d.month * 100 + d.day


def period_keys(t: np.ndarray, is_week: bool, tz: str = "UTC") -> np.ndarray:
    """period_key over an array. When the zone's UTC offset is the same at both ends of the array (no DST
    change inside it) the keys come from vectorised day arithmetic on unique days; otherwise, the per-candle
    path, which is exact but ~3 us a candle."""
    t = np.asarray(t, dtype=np.float64)
    n = int(t.shape[0])
    if n == 0:
        return np.zeros(0, dtype=np.int64)
    z = _zone(tz)
    off0 = datetime.fromtimestamp(float(t[0]), z).utcoffset().total_seconds()
    off1 = datetime.fromtimestamp(float(t[-1]), z).utcoffset().total_seconds()
    if off0 != off1:
        return np.fromiter((period_key(x, is_week, tz) for x in t), dtype=np.int64, count=n)
    dn = np.floor((t + off0) / DAY_SECS).astype(np.int64)      # local day number since the epoch
    if is_week:
        dn = dn - ((dn + 3) % 7)                                  # day 0 (1970-01-01) was a Thursday -> Monday
    uniq, inv = np.unique(dn, return_inverse=True)
    epoch = datetime(1970, 1, 1)
    keys = np.fromiter(((epoch + timedelta(days=int(d))).year * 10000 + (epoch + timedelta(days=int(d))).month * 100
                        + (epoch + timedelta(days=int(d))).day for d in uniq), dtype=np.int64, count=len(uniq))
    return keys[inv]


def period_window(t: float, is_week: bool, tz: str = "UTC") -> Tuple[float, float]:
    """(start, end) of the period holding instant t: Day 00:00 -> 23:59, Week Monday 00:00 -> Sunday 23:59."""
    z = _zone(tz)
    d = datetime.fromtimestamp(float(t), z)
    if is_week:
        noon = d.replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=d.weekday())
        mid = datetime(noon.year, noon.month, noon.day, tzinfo=z)
        end = mid + timedelta(days=7) - timedelta(minutes=1)
    else:
        mid = datetime(d.year, d.month, d.day, tzinfo=z)
        end = mid.replace(hour=23, minute=59)
    return mid.timestamp(), end.timestamp()


def split_periods(cand: Candles, is_week: bool, tz: str = "UTC") -> List[Tuple[int, Candles]]:
    """[(key, candles)] in time order. Candles with zero / NaN volume are skipped (Pine feedPer)."""
    if len(cand) == 0:
        return []
    ok = np.isfinite(cand.v) & (cand.v > 0)
    c = cand.keep(ok)
    if len(c) == 0:
        return []
    order = np.argsort(c.t, kind="stable")
    c = c.keep(order)
    keys = period_keys(c.t, is_week, tz)
    cuts = np.flatnonzero(np.diff(keys)) + 1
    starts = np.concatenate(([0], cuts))
    ends = np.concatenate((cuts, [len(c)]))
    out = []
    for s, e in zip(starts, ends):
        out.append((int(keys[s]), Candles(c.t[s:e], c.h[s:e], c.l[s:e], c.c[s:e], c.v[s:e], c.m[s:e])))
    return out


# ============================================================================ the leg rules
def walk_leg(vp, lowS, lowE, lowId, isHi, used, start: int, direction: int, rows: int) -> List[int]:
    """One leg of a D from row `start` in `direction` (+1 up, -1 down):
       - the closest LOW is ALWAYS connected first, even past a HIGH
       - keep extending while the next LOW is LOWER than the last connected one
       - stop at the first LOW that is not lower (it interrupts)
       - once the first LOW is connected, a HIGH interrupts too
       - never enter a row already used by a previous D
    Returns the ids of the connected LOWs, closest first. The leg ends at the last one."""
    seq: List[int] = []
    k = start
    while 0 <= k <= rows - 1:
        if used[k]:
            break
        if isHi[k] and seq:
            break
        lid = lowId[k]
        if lid >= 0:
            if seq and vp[lowS[lid]] >= vp[lowS[seq[-1]]]:
                break
            seq.append(lid)
            k = lowE[lid] + 1 if direction > 0 else lowS[lid] - 1
        else:
            k += direction
    return seq


def lowest_row(vp, used, start: int, direction: int, rows: int) -> int:
    """No LOW on a side: the thinnest row between the apex and the edge (or the first used row).
    Tie = farthest. -1 = none."""
    best = -1
    k = start
    while 0 <= k <= rows - 1:
        if used[k]:
            break
        if best < 0 or vp[k] <= vp[best]:
            best = k
        k += direction
    return best


def end_low(seq: List[int]) -> int:
    return seq[-1] if seq else -1


def compute_ends(ds: List[DShape], n_lows: int) -> Tuple[List[int], List[float]]:
    """For every LOW: how many live Ds end there, and the thinnest apex among them."""
    cnt = [0] * n_lows
    ap = [0.0] * n_lows
    for d in ds:
        if d.alive:
            for side in (d.up, d.dn):
                lid = end_low(side)
                if lid >= 0:
                    ap[lid] = d.apexV if cnt[lid] == 0 else min(ap[lid], d.apexV)
                    cnt[lid] += 1
    return cnt, ap


def merge_red_lows(ds: List[DShape], used, isApex, vp, lowS, lowE, shared_pct: float) -> None:
    """Merge the 2 Ds of a red LOW (end LOW of 2 live Ds, wider than shared_pct % of at least one of their
    apexes). It is the LOWER end of the D above it (X) and the UPPER end of the D below (Y). The bigger apex
    wins and keeps its apex; the merged D runs from X's upper end to Y's lower end; the smaller apex and the
    red LOW end up inside (faded). Repeats until no red LOW is left to merge."""
    merging = len(ds) > 1
    while merging:
        cnt, ap = compute_ends(ds, len(lowS))
        found = False
        for i in range(len(lowS)):
            if found or cnt[i] < 2 or not (vp[lowS[i]] > ap[i] * shared_pct / 100.0):
                continue
            xi = yi = -1
            for j, dj in enumerate(ds):
                if dj.alive:
                    if end_low(dj.dn) == i:
                        xi = j
                    if end_low(dj.up) == i:
                        yi = j
            if xi >= 0 and yi >= 0 and xi != yi:
                found = True
                X, Y = ds[xi], ds[yi]
                if X.apexV >= Y.apexV:
                    X.dn = Y.dn
                    X.fbDn = Y.fbDn
                    X.name = X.name + "+" + Y.name
                    Y.alive = False
                    if Y.num > 1:
                        isApex[Y.a] = False
                else:
                    Y.up = X.up
                    Y.fbUp = X.fbUp
                    Y.name = Y.name + "+" + X.name
                    X.alive = False
                    if X.num > 1:
                        isApex[X.a] = False
                for kk in range(lowS[i], lowE[i] + 1):
                    used[kk] = True
        merging = found


def unc_leg(vp, lowId, covered, start: int, direction: int, rows: int) -> Tuple[int, int]:
    """One leg of a purple-HIGH D through uncovered rows. Returns (LOW id of the first covered row if it is
    a LOW else -1, lowest uncovered row on the way or -1)."""
    lowHit = -1
    best = -1
    k = start
    while 0 <= k <= rows - 1:
        if covered[k]:
            lowHit = lowId[k]
            break
        if best < 0 or vp[k] <= vp[best]:
            best = k
        k += direction
    return lowHit, best


# ============================================================================ value areas, time profiles, blocs
def value_area(vp, r0: int, r1: int, va_pct: float) -> Tuple[int, int]:
    """Value area of rows r0..r1 (inclusive): start at the busiest row, add the bigger neighbouring row
    (tie -> above) until va_pct % of those rows' volume is inside. (-1, -1) if the rows hold no volume."""
    tot = 0.0
    pk = r0
    for k in range(r0, r1 + 1):
        tot += vp[k]
        if vp[k] > vp[pk]:
            pk = k
    if not tot > 0:
        return -1, -1
    vaLo = vaHi = pk
    acc = vp[pk]
    target = tot * va_pct / 100.0
    while acc < target and (vaLo > r0 or vaHi < r1):
        up = vp[vaHi + 1] if vaHi < r1 else -1.0
        dn = vp[vaLo - 1] if vaLo > r0 else -1.0
        if up >= dn:
            vaHi += 1
            acc += up
        else:
            vaLo -= 1
            acc += dn
    return vaLo, vaHi


def _spread(v: np.ndarray, iB: np.ndarray, iT: np.ndarray, rows: int) -> np.ndarray:
    """vp[r] += v / (iT - iB + 1) for every r in iB..iT, per candle -- exact sums (np.add.at), no cumsum
    round-off, so an EMPTY row stays exactly 0.0 and the equal-row runs the Pine relies on survive."""
    out = np.zeros(rows, dtype=np.float64)
    if v.shape[0] == 0:
        return out
    counts = (iT - iB + 1).astype(np.int64)
    sh = v / counts
    tot = int(counts.sum())
    offs = np.repeat(np.cumsum(counts) - counts, counts)
    idx = np.repeat(iB, counts) + (np.arange(tot) - offs)
    np.add.at(out, idx, np.repeat(sh, counts))
    return out


def bloc_va(cand: Candles, ar: TPArea, lo: float, step: float, tA: float, tB: float, va_pct: float) -> Tuple[int, int]:
    """Value area of ONE bloc: a profile of only the candles with open time in [tA, tB) AND close inside the
    area, on the area's rows; each candle's volume spread evenly over the area rows its range touches."""
    nR = ar.r1 - ar.r0 + 1
    if nR <= 0:
        return -1, -1
    sel = (cand.t >= tA) & (cand.t < tB) & (cand.c >= ar.yBot) & (cand.c <= ar.yTop)
    if not sel.any():
        return -1, -1
    h = cand.h[sel]
    l = cand.l[sel]
    v = cand.v[sel]
    iT = np.minimum(ar.r1, np.floor((h - lo) / step).astype(np.int64))
    iB = np.maximum(ar.r0, np.floor((l - lo) / step).astype(np.int64))
    ok = iT >= iB
    if not ok.any():
        return -1, -1
    bv = _spread(v[ok], iB[ok] - ar.r0, iT[ok] - ar.r0, nR)
    vLo, vHi = value_area(bv, 0, nR - 1, va_pct)
    return (vLo + ar.r0 if vLo >= 0 else -1), (vHi + ar.r0 if vHi >= 0 else -1)


def tp_bins(cand: Candles, yBot: float, yTop: float, t0: float, bin_secs: int, n_bins: int) -> Tuple[np.ndarray, np.ndarray]:
    """Time profile of ONE D area: a candle is IN the area only if it CLOSES inside it; its minutes and its
    WHOLE volume then count in the bin of its open time."""
    b = np.floor((cand.t - t0) / float(bin_secs)).astype(np.int64)
    sel = (cand.c >= yBot) & (cand.c <= yTop) & (b >= 0) & (b < n_bins)
    m = np.bincount(b[sel], weights=cand.m[sel], minlength=n_bins)[:n_bins].astype(np.float64)
    vo = np.bincount(b[sel], weights=cand.v[sel], minlength=n_bins)[:n_bins].astype(np.float64)
    return m, vo


def find_blocs(areas: List[TPArea]) -> List[Bloc]:
    """Every bloc of every D area: a run of consecutive non-empty bins."""
    bl: List[Bloc] = []
    for ai, ar in enumerate(areas):
        nb = 0
        bs = -1
        bM = bV = 0.0
        n = int(ar.m.shape[0])
        for b in range(n + 1):
            val = float(ar.m[b]) if b < n else 0.0
            if val > 0:
                if bs < 0:
                    bs = b
                    bM = bV = 0.0
                bM += val
                bV += float(ar.vo[b])
            elif bs >= 0:
                nb += 1
                bl.append(Bloc(ai, nb, bs, b, bM, bV))
                bs = -1
    return bl


def _keep_extreme(bl: List[Bloc], iT: int, iV: int, tag: str, tp_both: str) -> None:
    if iT == iV:
        bb = bl[iT]
        bb.keep = True
        bb.tag = tag if bb.tag == "" else bb.tag + " + " + tag
    elif tp_both == "Keep both":
        b1 = bl[iT]
        b1.keep = True
        b1.tag = (tag + " time") if b1.tag == "" else b1.tag + " + " + tag + " time"
        b2 = bl[iV]
        b2.keep = True
        b2.tag = (tag + " vol") if b2.tag == "" else b2.tag + " + " + tag + " vol"


def mark_extremes(bl: List[Bloc], tp_both: str) -> None:
    """Compare ALL blocs of the period, whatever their D: MAX = most time AND most volume, MIN = least time
    AND least volume. Those stay at normal opacity; every other bloc is faded."""
    if not bl:
        return
    iTmax = iVmax = iTmin = iVmin = 0
    for i in range(1, len(bl)):
        bb = bl[i]
        if bb.mins > bl[iTmax].mins:
            iTmax = i
        if bb.vol > bl[iVmax].vol:
            iVmax = i
        if bb.mins < bl[iTmin].mins:
            iTmin = i
        if bb.vol < bl[iVmin].vol:
            iVmin = i
    _keep_extreme(bl, iTmax, iVmax, "MAX", tp_both)
    _keep_extreme(bl, iTmin, iVmin, "MIN", tp_both)


def fmt_dur(mins: float) -> str:
    """58 -> '58min', 94 -> '1h34m', 1590 -> '1d2h30m'."""
    t = int(round(mins))
    dd, hh, mm = t // 1440, (t % 1440) // 60, t % 60
    if t < 60:
        return "%dmin" % t
    return ("%dd" % dd if dd > 0 else "") + "%dh%02dm" % (hh, mm)


def fmt_vol(v: float) -> str:
    """Pine format.volume: 312K, 1.42M, 2.1B."""
    a = abs(float(v))
    if a >= 1e9:
        return "%.2fB" % (v / 1e9)
    if a >= 1e6:
        return "%.2fM" % (v / 1e6)
    if a >= 1e3:
        return "%.0fK" % (v / 1e3)
    return "%.0f" % v


# ============================================================================ one period
def _empty(is_week, key, n, t_first, t_last, per_mid, per_end, bin_secs, n_bins, lo, hi, rows, vp) -> PeriodResult:
    return PeriodResult(is_week, key, n, t_first, t_last, per_mid, per_end, bin_secs, n_bins, lo, hi,
                        (hi - lo) / rows if hi > lo else 0.0, vp, float(vp.max()) if vp.size else 0.0, 0, 0,
                        [], [], [-1] * rows, [], [], [False] * rows, [False] * rows, [False] * rows,
                        [False] * rows, [], [], [], [], [], [], [], [], [], degenerate=True)


def compute_period(cand: Candles, is_week: bool, p: Params, key: Optional[int] = None) -> Optional[PeriodResult]:
    """Build ONE period (a day or a week) from its candles. None when there is nothing to build."""
    n = len(cand)
    if n == 0:
        return None
    rows = int(p.rows)
    t_first = float(cand.t[0])
    t_last = float(cand.t[-1])
    per_mid, per_end = period_window(t_first, is_week, p.tz)
    bin_secs = int(p.tp_bin_min_week if is_week else p.tp_bin_min_day) * 60
    n_bins = int(np.ceil((WEEK_SECS if is_week else DAY_SECS) / float(bin_secs)))
    if key is None:
        key = period_key(t_first, is_week, p.tz)
    hi = float(np.max(cand.h))
    lo = float(np.min(cand.l))
    if not hi > lo:
        return _empty(is_week, key, n, t_first, t_last, per_mid, per_end, bin_secs, n_bins, lo, hi, rows, np.zeros(rows))
    step = (hi - lo) / rows

    # -- the profile: each candle's volume spread evenly over every row its range touches
    iT = np.minimum(rows - 1, np.floor((cand.h - lo) / step).astype(np.int64))
    iB = np.minimum(rows - 1, np.maximum(0, np.floor((cand.l - lo) / step).astype(np.int64)))
    vp = _spread(cand.v, iB, iT, rows)
    maxVol = float(vp.max())
    if not maxVol > 0:
        return _empty(is_week, key, n, t_first, t_last, per_mid, per_end, bin_secs, n_bins, lo, hi, rows, vp)

    # -- POC (a run of equal max rows counts as one POC)
    pS = int(np.argmax(vp))
    pE = pS
    while pE < rows - 1 and vp[pE + 1] == maxVol:
        pE += 1

    # === STEP 1 + 2 : mark LOWs / HIGHs, filter by % of the POC ===
    lowLim = maxVol * p.low_max_pct / 100.0
    highLim = maxVol * p.high_min_pct / 100.0
    lowS: List[int] = []
    lowE: List[int] = []
    lowId = [-1] * rows
    highS: List[int] = []
    highE: List[int] = []
    isHi = [False] * rows
    r = 1
    while r < rows - 1:
        e = r
        while e < rows - 1 and vp[e + 1] == vp[r]:
            e += 1
        if e + 1 <= rows - 1:
            v = vp[r]
            below = vp[r - 1]
            above = vp[e + 1]
            if below > v and above > v and v < lowLim:
                lid = len(lowS)
                lowS.append(r)
                lowE.append(e)
                for kk in range(r, e + 1):
                    lowId[kk] = lid
            if below < v and above < v and v > highLim:
                highS.append(r)
                highE.append(e)
                for kk in range(r, e + 1):
                    isHi[kk] = True
        r = e + 1

    ds: List[DShape] = []
    used = [False] * rows
    isApex = [False] * rows

    # === STEP 3 + 4 : D1 from the POC, then what it has used up ===
    if p.show_d:
        noneUsed = [False] * rows
        up1 = walk_leg(vp, lowS, lowE, lowId, isHi, noneUsed, pE + 1, 1, rows)
        dn1 = walk_leg(vp, lowS, lowE, lowId, isHi, noneUsed, pS - 1, -1, rows)
        fu1 = lowest_row(vp, noneUsed, pE + 1, 1, rows) if not up1 else -1
        fd1 = lowest_row(vp, noneUsed, pS - 1, -1, rows) if not dn1 else -1
        ds.append(DShape(1, "D1", pS, pE, maxVol, up1, dn1, fu1, fd1, True))
        d1Top = lowS[up1[-1]] - 1 if up1 else (fu1 - 1 if fu1 >= 0 else rows - 1)
        d1Bot = lowE[dn1[-1]] + 1 if dn1 else (fd1 + 1 if fd1 >= 0 else 0)
        if d1Top >= d1Bot:
            for kk in range(d1Bot, d1Top + 1):
                used[kk] = True

    # === STEP 5+ : D2, D3, D4 ... ONE at a time ===
    searching = p.show_d and p.show_dn and len(highS) > 0
    while searching:
        if p.max_ds > 0 and len(ds) >= p.max_ds:
            break
        a2 = b2 = -1
        for i, hs in enumerate(highS):
            if not used[hs] and (hs < pS or hs > pE):
                if a2 < 0 or vp[hs] > vp[a2]:
                    a2 = hs
                    b2 = highE[i]
        if a2 < 0:
            break
        up2 = walk_leg(vp, lowS, lowE, lowId, isHi, used, b2 + 1, 1, rows)
        dn2 = walk_leg(vp, lowS, lowE, lowId, isHi, used, a2 - 1, -1, rows)
        fu = lowest_row(vp, used, b2 + 1, 1, rows) if not up2 else -1
        fd = lowest_row(vp, used, a2 - 1, -1, rows) if not dn2 else -1
        num = len(ds) + 1
        ds.append(DShape(num, "D%d" % num, a2, b2, float(vp[a2]), up2, dn2, fu, fd, True))
        isApex[a2] = True
        dTop = lowS[up2[-1]] - 1 if up2 else (fu - 1 if fu >= 0 else b2)
        dBot = lowE[dn2[-1]] + 1 if dn2 else (fd + 1 if fd >= 0 else a2)
        for kk in range(min(dBot, a2), max(dTop, b2) + 1):
            used[kk] = True

    # === STEP 6 : merge the 2 Ds of a red LOW ===
    if p.do_merge:
        merge_red_lows(ds, used, isApex, vp, lowS, lowE, p.shared_pct)

    # === STEP 7 : uncovered areas ===
    covered = list(used)
    for d in ds:
        if d.alive:
            upL = end_low(d.up)
            dnL = end_low(d.dn)
            dTop = lowE[upL] if upL >= 0 else (d.fbUp if d.fbUp >= 0 else d.b)
            dBot = lowS[dnL] if dnL >= 0 else (d.fbDn if d.fbDn >= 0 else d.a)
            for kk in range(dBot, dTop + 1):
                covered[kk] = True
    uncS: List[int] = []
    uncE: List[int] = []
    if p.do_uncov:
        k = 0
        while k < rows:
            if covered[k]:
                k += 1
                continue
            s = k
            while k < rows and not covered[k]:
                k += 1
            e = k - 1
            best = bestE = -1
            rr = max(1, s)
            while rr <= e:
                pe = rr
                while pe < e and vp[pe + 1] == vp[rr]:
                    pe += 1
                if pe + 1 <= rows - 1 and vp[rr - 1] < vp[rr] and vp[pe + 1] < vp[rr]:
                    if best < 0 or vp[rr] > vp[best]:
                        best = rr
                        bestE = pe
                rr = pe + 1
            if best >= 0:
                uncS.append(best)
                uncE.append(bestE)

    # === STEP 8 : a D from each purple HIGH ===
    pds: List[DShape] = []
    for i, (ua, ub) in enumerate(zip(uncS, uncE)):
        uLow, uFb = unc_leg(vp, lowId, covered, ub + 1, 1, rows)
        dLow, dFb = unc_leg(vp, lowId, covered, ua - 1, -1, rows)
        pds.append(DShape(0, "U%d" % (i + 1), ua, ub, float(vp[ua]),
                          [uLow] if uLow >= 0 else [], [dLow] if dLow >= 0 else [],
                          -1 if uLow >= 0 else uFb, -1 if dLow >= 0 else dFb, True))

    # === STEP 9 : merge again, now with the purple Ds ===
    allDs = list(ds) + list(pds)
    if p.do_merge:
        merge_red_lows(allDs, used, isApex, vp, lowS, lowE, p.shared_pct)
    endCnt, endApex = compute_ends(allDs, len(lowS))

    # === D levels + the time profile of each D area ===
    areas: List[TPArea] = []
    res = PeriodResult(is_week, key, n, t_first, t_last, per_mid, per_end, bin_secs, n_bins, lo, hi, step,
                       vp, maxVol, pS, pE, lowS, lowE, lowId, highS, highE, isHi, used, isApex, covered,
                       uncS, uncE, ds, pds, allDs, endCnt, endApex, areas, [])
    for d in allDs:
        if not d.alive:
            continue
        topRow, botRow = res.leg_end_rows(d)
        aTop = lo + (topRow + 1) * step if topRow >= 0 else lo + (d.b + 1) * step
        aBot = lo + botRow * step if botRow >= 0 else lo + d.a * step
        m, vo = tp_bins(cand, aBot, aTop, per_mid, bin_secs, n_bins)
        aR0 = botRow if botRow >= 0 else d.a
        aR1 = topRow if topRow >= 0 else d.b
        areas.append(TPArea(d.name, d.num, aBot, aTop, m, vo, aR0, aR1, topRow, botRow))

    # === compare every bloc of the period, whatever its D; each bloc's own value area ===
    blocs = find_blocs(areas)
    mark_extremes(blocs, p.tp_both)
    for bb in blocs:
        ar = areas[bb.area]
        if ar.r1 >= ar.r0:
            tA = per_mid + bb.bs * bin_secs
            tB = per_mid + bb.be * bin_secs
            bb.vaLo, bb.vaHi = bloc_va(cand, ar, lo, step, tA, tB, p.va_pct)
    res.blocs = blocs
    return res
