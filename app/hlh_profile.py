# -*- coding: utf-8 -*-
"""HLH Volume Profile -- the ENGINE (pure Python + numpy, no Qt).

A faithful port of study/pine/hlh_volume_profile.pine (Pine v6; the user's working file is
Desktop\hlh_volume_profile.pine; specs: study/pine/HLH_VOLUME_PROFILE_SPEC_v1.md + _v2.md, and since V2: the
"Bloc badges" switch, the Merges group with the 36h span cap / "day N alone", the outer 90% value area (dashed),
the orange / coloured / dark-gray bloc colours by volume rank, older finished periods taking part in the merges,
durations always in hours). The ORDER of the steps IS the algorithm -- every step consumes the state the previous one left (used rows,
alive Ds, red LOWs) -- so nothing is reordered or fused here, and the names follow the Pine so the two read
side by side. Input = one PERIOD's intrabar candles (a day of 1-minute candles, a week of 5-minute candles);
output = a PeriodResult: the profile rows, the LOWs / HIGHs, every D (alive or merged), the uncovered areas
and their purple Ds, each D area's time profile, the blocs with their value areas. There is NO x geometry in
here: the drawing layer maps rows -> price and times -> x for whichever canvas it sits on.

Deviations from the Pine, all deliberate and all narrower than the source:
  * a candle whose LOW sits on the period high (a doji at the extreme) clamps to the top row instead of
    iterating a Pine `for` backwards out of the array;
  * Pine `int()` truncates toward zero -- every quantity it is applied to here is >= 0, so floor == trunc;
  * drawing caps / prune budgets / dev-vs-finished arrays are TradingView artefacts (spec section 18);
  * the Pine deletes and redraws lines as the merges go -- here a period's FINAL state (its live rows after every
    merge, coloured) is what the drawing layer reads, so nothing is ever drawn and then deleted.

Two layers: compute_period() builds ONE period on its own (the profile, the Ds, the areas, the blocs, merges 1 and
2 -> its rows); Chain folds the finished periods oldest -> newest exactly as the Pine feeds them (merges 3 / 4 with
the earlier periods, the colours, the tables) and runs the forming period on top without touching that state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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
    # -- the Merges group (the rules that build the FINAL blocs)
    merge_blocs: bool = True       # merge 1: time-overlapping blocs, high-low >= inside_pct % inside
    inside_pct: float = 50.0
    d_merge: bool = True           # merge 2: same D, VAH-VAL >= d_merge_pct % inside (collage)
    d_merge_pct: float = 50.0
    day_merge: bool = True         # merge 3 (finished periods): day N + day N-1 OVERLAPPING >= day_merge_pct % of the smaller range
    day_merge_pct: float = 50.0
    ins_merge: bool = True         # merge 3: ... or one INSIDE the other, the smaller range >= ins_merge_pct % of the bigger
    ins_merge_pct: float = 50.0
    edge_merge: bool = True        # merge 4 (finished periods): blocs sharing a period, VAL = VAH within edge_ticks ticks
    edge_ticks: int = 1
    merge_span: str = "36h"        # "12h" ... "1 week", or "No merge (day N alone)"
    # -- bloc colours
    vol_look: int = 20
    gold_pct: float = 69.0
    gray_pct: float = 29.0
    c_gold: str = "#ff9800"
    c_gray: str = "#505050"
    # -- the outer value area, the symbol, the tables
    va2_pct: float = 90.0
    tick: float = 0.01             # syminfo.mintick
    price_decimals: int = 2        # format.mintick
    table1: bool = True
    table2: bool = True
    table3: bool = True

    @property
    def day_alone(self) -> bool:
        return self.merge_span == "No merge (day N alone)"

    @property
    def merge_max_h(self) -> float:
        return {"12h": 12.0, "24h": 24.0, "36h": 36.0, "48h": 48.0, "72h": 72.0, "96h": 96.0,
                "1 week": 168.0}.get(self.merge_span, 1.0e9)


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
    mCount: int = 0             # merged blocs named so far in this D (Dy-mB1, Dy-mB2 ...)
    mCount0: int = 0            # ... as compute_period left it (the chain resets to it before processing again)


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
    ref: str = ""               # "D1-B3", or "D1-mB2" once it is a merged bloc
    bHi: Optional[float] = None   # highest high / lowest low of the bloc's candles
    bLo: Optional[float] = None
    tA: Optional[float] = None    # first candle open / last candle close (epoch s)
    tB: Optional[float] = None
    gone: bool = False          # merged into another bloc: not drawn, not listed
    dGone: bool = False         # part of a merge of the same D: its own VAH / VAL and badge are not drawn
    members: str = ""           # names of the blocs merged into this one
    ids: List[int] = field(default_factory=list)   # indexes of the blocs merged into this one
    vah: Optional[float] = None   # VAH / VAL price of the bloc (+ the blocs merged into it)
    val: Optional[float] = None
    vaA: Optional[float] = None   # VAH / VAL line start / end (epoch s)
    vaB: Optional[float] = None
    vah2: Optional[float] = None  # outer value area (va2_pct %)
    val2: Optional[float] = None
    usd: float = 0.0            # $ traded by the bloc's candles (contracts x close), the badge's third line


@dataclass
class MB:
    """One ROW: a bloc, or several blocs merged together (the Pine's MB). grp = every original bloc whose candles
    it holds (empty for a bloc built by the chain's merge loop, which keeps the candles themselves)."""
    area: int
    name: str
    members: str
    grp: List[int]
    mins: float
    vol: float
    tA: Optional[float]
    tB: Optional[float]
    vah: Optional[float]
    val: Optional[float]
    vaA: Optional[float] = None   # VAH / VAL line start / end (epoch s)
    vaB: Optional[float] = None
    merged: bool = False          # built by a merge (same D, or day N + day N-1): the collage
    col: str = ""                 # D colour (hex)
    yLo: Optional[float] = None   # price band of the D areas it covers (the collage's rows)
    yHi: Optional[float] = None
    cH: Optional[np.ndarray] = None   # its candles: high, low, volume (kept for the next period's collage)
    cL: Optional[np.ndarray] = None
    cV: Optional[np.ndarray] = None
    vah2: Optional[float] = None
    val2: Optional[float] = None
    mergedFrom: str = ""          # what a merge joined: "D1-B2 (Mon) + D2-B1 [VAH-VAL]"
    pStep: Optional[float] = None   # row height of its period's profile
    dFirst: Optional[int] = None    # first period (ordinal) its candles come from
    dLast: Optional[int] = None     # last period its candles come from
    dName: str = ""               # "D1" (the D it belongs to)
    bHi: Optional[float] = None   # highest high / lowest low of its candles
    bLo: Optional[float] = None
    poc: Optional[float] = None   # the bloc's OWN point of control, filled lazily by bloc_poc() and cached
    tag: str = ""                 # MAX / MIN among the final blocs (table 1)
    todo: bool = False            # to be coloured by color_rows
    cls: Optional[int] = None     # 2 = orange, 1 = coloured, 0 = dark gray
    lnColor: Optional[str] = None   # the colour its VAH / VAL got (hex)
    width: int = 3                # ... and the width (px)
    usd: float = 0.0              # $ traded (adds up through every merge, like vol)


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
    rows: List[MB] = field(default_factory=list)   # the rows after merges 1 and 2 (the chain's input)
    n_m1: int = 0               # merge-1 count (blocs absorbed)
    n_d: int = 0                # merge-2 count (same D)

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
                bl.append(Bloc(ai, nb, bs, b, bM, bV, ref="%s-B%d" % (ar.name, nb)))
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
    """Compare ALL live blocs of the period (merged-away ones left out), whatever their D: MAX = most time AND
    most volume, MIN = least time AND least volume. Those stay at normal opacity; every other bloc is faded."""
    iTmax = iVmax = iTmin = iVmin = -1
    for i, bb in enumerate(bl):
        if bb.gone:
            continue
        if iTmax < 0 or bb.mins > bl[iTmax].mins:
            iTmax = i
        if iVmax < 0 or bb.vol > bl[iVmax].vol:
            iVmax = i
        if iTmin < 0 or bb.mins < bl[iTmin].mins:
            iTmin = i
        if iVmin < 0 or bb.vol < bl[iVmin].vol:
            iVmin = i
    if iTmax >= 0:
        _keep_extreme(bl, iTmax, iVmax, "MAX", tp_both)
        _keep_extreme(bl, iTmin, iVmin, "MIN", tp_both)


def fmt_dur(mins: float) -> str:
    """58 -> '58min', 94 -> '1h34m', 1832 -> '30h32m' (always hours, never days)."""
    t = int(round(mins))
    if t < 60:
        return "%dmin" % t
    return "%dh%02dm" % (t // 60, t % 60)


def local_midnight(t: float, tz: str) -> float:
    z = _zone(tz)
    d = datetime.fromtimestamp(float(t), z)
    return datetime(d.year, d.month, d.day, tzinfo=z).timestamp()


def fmt_bloc(mins: float, tA: Optional[float], tB: Optional[float], tz: str) -> str:
    """A bloc's duration, plus ' over N days' when its span (first candle -> last candle close) covers more than
    one calendar day: '30h32m over 2 days'."""
    out = fmt_dur(mins)
    if tA is not None and tB is not None:
        d0 = local_midnight(tA, tz)
        d1 = local_midnight(max(tA, tB - 0.001), tz)
        n_days = int(round((d1 - d0) / 86400.0)) + 1
        if n_days > 1:
            out += " over %d days" % n_days
    return out


_PINE_FMT = {"HH:mm": "%H:%M", "EEE HH:mm": "%a %H:%M", "dd MMM": "%d %b", "EEE": "%a", "EEE dd MMM": "%a %d %b",
             "dd MMM yyyy": "%d %b %Y"}


def fmt_time(t: float, fmt: str, tz: str) -> str:
    """str.format_time with the Pine's patterns."""
    return datetime.fromtimestamp(float(t), _zone(tz)).strftime(_PINE_FMT.get(fmt, fmt))


def fmt_price(x: Optional[float], decimals: int) -> str:
    return "-" if x is None else ("%%.%df" % int(decimals)) % float(x)


def fmt_usd(a: float) -> str:
    """The big-player bubbles' format (app/trades_tape._fmt_usd): $1.00M, $996K, $1.5K, $312 -- plus a billions
    tier a day's bloc can reach and a single print never does (the terminal's _fmt_usd_short has it too)."""
    a = float(a)
    if a >= 1_000_000_000:
        return "$%.2fB" % (a / 1_000_000_000)
    if a >= 1_000_000:
        return "$%.2fM" % (a / 1_000_000)
    if a >= 100_000:
        return "$%.0fK" % (a / 1_000)
    if a >= 1_000:
        return "$%.1fK" % (a / 1_000)
    return "$%s" % format(int(round(a)), ",")


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


def bloc_poc(m: "MB") -> Optional[float]:
    """The POC of ONE bloc: the price of the row holding the most volume, from the bloc's OWN candles.

    Built with the PERIOD's own rule so the two cannot disagree -- rows of `pStep` height, each candle's volume
    spread evenly over every row its range touches (`_spread`), and a run of equal maximum rows counted as ONE
    POC, reported at the middle of that run. `pStep` is the row height of the period the bloc came from, so a
    bloc's rows line up with its period's profile instead of being re-binned to its own range.

    Cached on the MB: `build_period` runs per redraw and a bloc's candles never change once merged."""
    if m.poc is not None:
        return m.poc
    if m.cH is None or m.cL is None or m.cV is None:
        return None
    h = np.asarray(m.cH, dtype=np.float64)
    l = np.asarray(m.cL, dtype=np.float64)
    v = np.asarray(m.cV, dtype=np.float64)
    if h.size == 0 or v.size != h.size or l.size != h.size:
        return None
    lo = float(np.min(l)); hi = float(np.max(h))
    if not hi > lo:
        return None
    step = float(m.pStep) if (m.pStep is not None and m.pStep > 0) else (hi - lo) / 24.0
    if not step > 0:
        return None
    rows = int(max(1, min(4096, np.ceil((hi - lo) / step))))
    iT = np.minimum(rows - 1, np.maximum(0, np.floor((h - lo) / step).astype(np.int64)))
    iB = np.minimum(rows - 1, np.maximum(0, np.floor((l - lo) / step).astype(np.int64)))
    iB = np.minimum(iB, iT)                      # a candle whose low rounds above its high would break _spread
    vp = _spread(v, iB, iT, rows)
    mx = float(vp.max()) if vp.size else 0.0
    if not mx > 0:
        return None
    pS = int(np.argmax(vp)); pE = pS
    while pE < rows - 1 and vp[pE + 1] == mx:
        pE += 1
    m.poc = lo + (pS + pE + 1) * 0.5 * step      # the MIDDLE of the POC run, the period's convention
    return m.poc


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

    # === simple names: the final Ds are D1, D2, D3 ... in build order (no more "D2+D4" or "U1"). D1 stays the
    # POC D. The colour follows the new number; a D built from a purple HIGH keeps its purple colour ===
    nameNo = 0
    for d in allDs:
        if d.alive:
            nameNo += 1
            d.name = "D%d" % nameNo
            if d.num > 0:
                d.num = nameNo

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

    # === the blocs: names, real stats, merge 1, MAX / MIN, value areas, merge 2 -> the rows ===
    blocs = find_blocs(areas)
    fill_bloc_stats(cand, areas, blocs, per_mid, bin_secs)
    n_m1 = 0
    if p.merge_blocs:
        merge_blocs(areas, blocs, p.inside_pct)
        n_m1 = sum(1 for bb in blocs if bb.gone)
    mark_extremes(blocs, p.tp_both)
    fill_bloc_va(cand, areas, blocs, lo, step, per_mid, bin_secs, p.va_pct, p.va2_pct)
    rows, n_d = merge_same_d(cand, areas, blocs, lo, step, per_mid, bin_secs, p)
    fill_row_candles(cand, areas, blocs, rows, per_mid, bin_secs, step)
    for ar in areas:
        ar.mCount0 = ar.mCount
    res.blocs = blocs
    res.rows = rows
    res.n_m1 = n_m1
    res.n_d = n_d
    return res


# ============================================================================ the Pine palette (hex; the drawing layer makes QColors)
D_COLS = ("#e91e63", "#00bcd4", "#9c27b0", "#4caf50", "#ff9800")   # D1, then D2..D5 repeating
C_HIST, C_LOW, C_HIGH, C_POC, C_SHARED, C_UNCOV = "#9598a1", "#f0b26b", "#6b9bd1", "#ffd54f", "#f23645", "#9c27b0"


def d_colour_hex(num: int) -> str:
    if num == 0:
        return C_UNCOV
    if num == 1:
        return D_COLS[0]
    return D_COLS[1 + (num - 2) % 4]


def _nmin(a, b):
    return b if a is None else (a if b is None else min(a, b))


def _nmax(a, b):
    return b if a is None else (a if b is None else max(a, b))


def _join_members(a: str, b: str) -> str:
    return b if a == "" else (a if b == "" else a + ", " + b)


def _stable_order(vals) -> List[int]:
    """array.sort_indices(vals, order.ascending): the indexes from the lowest value up, ties in list order."""
    return [int(i) for i in np.argsort(np.asarray(vals, dtype=np.float64), kind="stable")]


def _num(x: float) -> str:
    """str.tostring() of a setting: 50 -> '50', 36.5 -> '36.5'."""
    x = float(x)
    return str(int(x)) if x.is_integer() else ("%.2f" % x).rstrip("0").rstrip(".")


# ============================================================================ bloc stats, merge 1, group value areas
def _bloc_mask(cand: Candles, ar: TPArea, tS: float, tE: float) -> np.ndarray:
    """The candles of one bloc: open time inside its bins AND close inside its D area."""
    return (cand.t >= tS) & (cand.t < tE) & (cand.c >= ar.yBot) & (cand.c <= ar.yTop)


def fill_bloc_stats(cand: Candles, areas: List[TPArea], bl: List[Bloc], t0: float, bin_secs: int) -> None:
    """For every bloc: its real candles give its price range (highest high / lowest low) and its real time span
    (first open / last close)."""
    for bb in bl:
        sel = _bloc_mask(cand, areas[bb.area], t0 + bb.bs * bin_secs, t0 + bb.be * bin_secs)
        if sel.any():
            bb.bHi = float(cand.h[sel].max())
            bb.bLo = float(cand.l[sel].min())
            bb.tA = float(cand.t[sel].min())
            bb.tB = float((cand.t[sel] + cand.m[sel] * 60.0).max())
            bb.usd = float((cand.v[sel] * cand.c[sel]).sum())    # contracts x close: the $ the badge shows
        else:
            bb.bHi = bb.bLo = bb.tA = bb.tB = None
            bb.usd = 0.0


def time_overlap(a, o) -> bool:
    """Two blocs' real time spans overlap (Bloc or MB)."""
    return a.tA is not None and o.tA is not None and a.tA < o.tB and o.tA < a.tB


def inside_of(a, o) -> float:
    """How much of a's high-low range lies inside o's high-low range, in %."""
    if a.bHi is None or o.bHi is None:
        return 0.0
    ov = max(0.0, min(a.bHi, o.bHi) - max(a.bLo, o.bLo))
    rng = a.bHi - a.bLo
    if rng > 0:
        return ov / rng * 100.0
    return 100.0 if (a.bLo >= o.bLo and a.bHi <= o.bHi) else 0.0


def merge_blocs(areas: List[TPArea], bl: List[Bloc], inside_pct: float) -> None:
    """Merge 1 on the blocs, in the table order (lowest volume first). For bloc A: among the other live blocs
    whose time overlaps A and that A is at least inside_pct % inside, the one with the highest volume (B). The
    lower-volume of A and B becomes part of the higher-volume one: volume and time add up, high-low and the time
    span widen, and the absorbed bloc disappears. Every bloc that absorbed others is then Dy-mBx."""
    if len(bl) <= 1:
        return
    for ai in _stable_order([bb.vol for bb in bl]):
        a = bl[ai]
        if a.gone or a.tA is None:
            continue
        best = -1
        for k, o in enumerate(bl):
            if k != ai and not o.gone and time_overlap(a, o) and inside_of(a, o) >= inside_pct:
                if best < 0 or o.vol > bl[best].vol:
                    best = k
        if best >= 0:
            b = bl[best]
            wi, li = (ai, best) if a.vol >= b.vol else (best, ai)
            win, los = bl[wi], bl[li]
            win.ids = list(win.ids) + [li] + list(los.ids)
            win.vol += los.vol
            win.usd += los.usd
            win.mins += los.mins
            win.bHi = _nmax(win.bHi, los.bHi)
            win.bLo = _nmin(win.bLo, los.bLo)
            win.tA = _nmin(win.tA, los.tA)
            win.tB = _nmax(win.tB, los.tB)
            win.members = win.members + ("" if win.members == "" else ", ") + los.ref + ("" if los.members == "" else ", " + los.members)
            los.gone = True
    for bb in bl:
        if not bb.gone and bb.members != "":
            ar = areas[bb.area]
            ar.mCount += 1
            bb.ref = "%s-mB%d" % (ar.name, ar.mCount)


def group_va(cand: Candles, areas: List[TPArea], bl: List[Bloc], grp: List[int], lo: float, step: float, t0: float,
             bin_secs: int, va_pct: float, va2_pct: float) -> Tuple[int, int, int, int, float, float]:
    """Value area of a GROUP of blocs, ISOLATED: only the candles of those blocs, as if they were one continuous
    chart (candles of other blocs in between are ignored); a candle belongs to the group if it is a candle of at
    least one of its blocs. Rows = from the lowest to the highest row of the group's D areas; each candle's volume
    spread evenly over the rows its range touches. Returns (vLo, vHi, vLo2, vHi2, tS, tE): the value-area rows and
    the outer value-area rows (-1 = no volume), and the group's span (first bin start, last bin end)."""
    me = bl[grp[0]]
    ar0 = areas[me.area]
    r0, r1 = ar0.r0, ar0.r1
    tS = t0 + me.bs * bin_secs
    tE = t0 + me.be * bin_secs
    sel = np.zeros(len(cand), dtype=bool)
    for g in grp:
        gb = bl[g]
        ga = areas[gb.area]
        r0 = min(r0, ga.r0)
        r1 = max(r1, ga.r1)
        tS = min(tS, t0 + gb.bs * bin_secs)
        tE = max(tE, t0 + gb.be * bin_secs)
        sel |= _bloc_mask(cand, ga, t0 + gb.bs * bin_secs, t0 + gb.be * bin_secs)
    out = [-1, -1, -1, -1]
    if r1 >= r0 and sel.any():
        nR = r1 - r0 + 1
        h = cand.h[sel]
        l = cand.l[sel]
        v = cand.v[sel]
        iT = np.minimum(r1, np.floor((h - lo) / step).astype(np.int64))
        iB = np.maximum(r0, np.floor((l - lo) / step).astype(np.int64))
        ok = iT >= iB
        bv = _spread(v[ok], iB[ok] - r0, iT[ok] - r0, nR)
        vLo, vHi = value_area(bv, 0, nR - 1, va_pct)
        if vLo >= 0:
            out[0], out[1] = vLo + r0, vHi + r0
        vLo2, vHi2 = value_area(bv, 0, nR - 1, va2_pct)
        if vLo2 >= 0:
            out[2], out[3] = vLo2 + r0, vHi2 + r0
    return out[0], out[1], out[2], out[3], tS, tE


def fill_bloc_va(cand: Candles, areas: List[TPArea], bl: List[Bloc], lo: float, step: float, t0: float,
                 bin_secs: int, va_pct: float, va2_pct: float) -> None:
    """VAH / VAL (and the outer value area) of every live bloc together with the blocs merged into it, stored on
    the bloc: used by the Block Lines and by the rows."""
    for bi, bb in enumerate(bl):
        if bb.gone:
            continue
        vLo, vHi, vLo2, vHi2, tS, tE = group_va(cand, areas, bl, [bi] + list(bb.ids), lo, step, t0, bin_secs, va_pct, va2_pct)
        bb.vaA, bb.vaB = tS, tE
        bb.vaLo, bb.vaHi = vLo, vHi
        if vLo >= 0:
            bb.vah = lo + (vHi + 1) * step
            bb.val = lo + vLo * step
        if vLo2 >= 0:
            bb.vah2 = lo + (vHi2 + 1) * step
            bb.val2 = lo + vLo2 * step


def mb_inside(a: MB, o: MB) -> float:
    """How much of a's VAH-VAL range lies inside o's VAH-VAL range, in %."""
    if a.vah is None or o.vah is None:
        return 0.0
    ov = max(0.0, min(a.vah, o.vah) - max(a.val, o.val))
    rng = a.vah - a.val
    if rng > 0:
        return ov / rng * 100.0
    return 100.0 if (a.val >= o.val and a.vah <= o.vah) else 0.0


def merge_same_d(cand: Candles, areas: List[TPArea], bl: List[Bloc], lo: float, step: float, t0: float,
                 bin_secs: int, p: Params) -> Tuple[List[MB], int]:
    """Merge 2. The rows: one per live bloc. Then inside each D, from the LOWEST volume: the first bloc whose
    VAH-VAL is at least d_merge_pct % inside another bloc of the same D is merged with that bloc (the highest %
    if several). The new bloc = both blocs ISOLATED: time and volume add up, and its VAH / VAL come from a volume
    profile of ONLY their candles. Both leave, the new one enters, and the check starts again from the lowest
    volume. Every bloc that went into a merge is dGone (its own VAH / VAL is no longer drawn).
    Returns (the rows after the merges, number of merges)."""
    rows: List[MB] = []
    for bi, bb in enumerate(bl):
        if not bb.gone:
            ar = areas[bb.area]
            rows.append(MB(bb.area, bb.ref, bb.members, [bi] + list(bb.ids), bb.mins, bb.vol, bb.tA, bb.tB, bb.vah, bb.val,
                           bb.vaA, bb.vaB, False, col=d_colour_hex(ar.num), dName=ar.name, bHi=bb.bHi, bLo=bb.bLo,
                           vah2=bb.vah2, val2=bb.val2, usd=bb.usd))
    n = 0
    merging = p.d_merge and len(rows) > 1
    while merging:
        merging = False
        for ia in _stable_order([m.vol for m in rows]):
            a = rows[ia]
            if a.vah is None:
                continue
            best, bestP = -1, -1.0
            for k, c in enumerate(rows):
                if k != ia and c.area == a.area and c.vah is not None:
                    pp = mb_inside(a, c)
                    if pp >= p.d_merge_pct and pp > bestP:
                        best, bestP = k, pp
            if best >= 0:
                ob = rows[best]
                grp = list(a.grp) + list(ob.grp)
                vLo, vHi, vLo2, vHi2, gS, gE = group_va(cand, areas, bl, grp, lo, step, t0, bin_secs, p.va_pct, p.va2_pct)
                ar = areas[a.area]
                ar.mCount += 1
                nm = MB(a.area, "%s-mB%d" % (ar.name, ar.mCount), _join_members(a.members, ob.members), grp,
                        a.mins + ob.mins, a.vol + ob.vol, _nmin(a.tA, ob.tA), _nmax(a.tB, ob.tB),
                        lo + (vHi + 1) * step if vLo >= 0 else None, lo + vLo * step if vLo >= 0 else None,
                        gS, gE, True, col=d_colour_hex(ar.num), dName=ar.name, bHi=_nmax(a.bHi, ob.bHi),
                        bLo=_nmin(a.bLo, ob.bLo), mergedFrom=a.name + " + " + ob.name + " [VAH-VAL]",
                        vah2=lo + (vHi2 + 1) * step if vLo2 >= 0 else None, val2=lo + vLo2 * step if vLo2 >= 0 else None,
                        usd=a.usd + ob.usd)
                for idx in sorted((ia, best), reverse=True):
                    del rows[idx]
                rows.append(nm)
                n += 1
                merging = True
                break
    for m in rows:
        if m.merged:
            for g in m.grp:
                bl[g].dGone = True
    return rows, n


def fill_row_candles(cand: Candles, areas: List[TPArea], bl: List[Bloc], rows: List[MB], t0: float, bin_secs: int,
                     step: float) -> None:
    """For every row: keep its candles (high, low, volume) and its price band (the D areas it covers), so the
    NEXT period can still build a collage with it once this period's candles are gone."""
    for m in rows:
        if not m.grp:
            continue
        sel = np.zeros(len(cand), dtype=bool)
        yLo = yHi = None
        for g in m.grp:
            gb = bl[g]
            ga = areas[gb.area]
            yLo = ga.yBot if yLo is None else min(yLo, ga.yBot)
            yHi = ga.yTop if yHi is None else max(yHi, ga.yTop)
            sel |= _bloc_mask(cand, ga, t0 + gb.bs * bin_secs, t0 + gb.be * bin_secs)
        m.cH = cand.h[sel].copy()
        m.cL = cand.l[sel].copy()
        m.cV = cand.v[sel].copy()
        m.yLo, m.yHi = yLo, yHi
        m.pStep = step


# ============================================================================ the merge rules on rows (the chain)
def span_h(a: MB, b: MB) -> float:
    """How many hours a merge of a and b would span: the first candle of either -> the last close of either."""
    if a.tA is None or b.tA is None or a.tB is None or b.tB is None:
        return 0.0
    return (max(a.tB, b.tB) - min(a.tA, b.tA)) / 3600.0


def partial_pct(a: MB, o: MB) -> float:
    """% of the SMALLER VAH-VAL range that the two ranges share, for a PARTIAL overlap only (so it reads the same
    from either bloc). -1 = no overlap, or one range fully inside the other."""
    out = -1.0
    if a.vah is not None and o.vah is not None:
        ov = min(a.vah, o.vah) - max(a.val, o.val)
        rA = a.vah - a.val
        rO = o.vah - o.val
        if ov > 0 and rA > 0 and rO > 0:
            aIn = a.val >= o.val and a.vah <= o.vah
            oIn = o.val >= a.val and o.vah <= a.vah
            if not aIn and not oIn:
                out = ov / min(rA, rO) * 100.0
    return out


def inside_ratio(a: MB, o: MB) -> float:
    """'inside at X%': a's VAH-VAL range fully inside o's, X = a's range / o's range. -1 = not inside."""
    out = -1.0
    if a.vah is not None and o.vah is not None:
        rA = a.vah - a.val
        rO = o.vah - o.val
        if rA > 0 and rO > 0 and a.val >= o.val and a.vah <= o.vah:
            out = rA / rO * 100.0
    return out


def edge_gap(a: MB, b: MB, tick: float, edge_ticks: int) -> float:
    """The gap (in price) between a's VAL and b's VAH or b's VAL and a's VAH, when the two blocs share at least one
    period AND that gap is within edge_ticks ticks. -1 = does not qualify."""
    out = -1.0
    if (a.vah is not None and b.vah is not None and a.cH is not None and b.cH is not None
            and a.dFirst is not None and b.dFirst is not None):
        if a.dFirst <= b.dLast and b.dFirst <= a.dLast:
            def rt(x):
                return round(x / tick) * tick
            g = min(abs(rt(a.val) - rt(b.vah)), abs(rt(b.val) - rt(a.vah)))
            if g <= edge_ticks * tick + tick * 0.01:
                out = g
    return out


def day_relation(a: MB, o: MB) -> str:
    """VAH-VAL relation of a day N bloc (a) with a day N-1 bloc (o), in price. '' = no overlap."""
    out = ""
    if a.vah is not None and o.vah is not None:
        ov = min(a.vah, o.vah) - max(a.val, o.val)
        rA = a.vah - a.val
        rO = o.vah - o.val
        if ov > 0 and rA > 0 and rO > 0:
            if a.val >= o.val and a.vah <= o.vah:
                out = "inside at %d%%" % round(rA / rO * 100)
            elif o.val >= a.val and o.vah <= a.vah:
                out = "contains at %d%%" % round(rO / rA * 100)
            else:
                out = "overlapping at %d%%" % round(ov / min(rA, rO) * 100)
    return out


def chain_of(m: MB, s: int, is_week: bool, tz: str) -> str:
    """What a row is made of, for the 'Merged' column: its name, or its own merge chain in brackets; a row of an
    earlier period is marked with the day it starts on (the date for weeks)."""
    return ((m.name if m.mergedFrom == "" else "(" + m.mergedFrom + ")")
            + ("" if (s == 0 or m.tA is None) else " (" + fmt_time(m.tA, "dd MMM" if is_week else "EEE", tz) + ")"))


def collage(a: MB, o: MB, name: str, from_: str, p: Params) -> MB:
    """The collage of two blocs (a = the base, o = the other): ONLY their candles, as one continuous chart, on
    rows of a's row height spanning both blocs' price bands. Start at the busiest row, add the bigger neighbour
    until va_pct % is inside. Time and volume add up. The new bloc keeps a's area and colour."""
    h = np.concatenate((a.cH, o.cH))
    l = np.concatenate((a.cL, o.cL))
    v = np.concatenate((a.cV, o.cV))
    yLo = _nmin(a.yLo, o.yLo)
    yHi = _nmax(a.yHi, o.yHi)
    nVah = nVal = nVah2 = nVal2 = None
    if yLo is not None and yHi is not None and yHi > yLo and h.shape[0] > 0 and a.pStep:
        nR = max(1, min(400, int(np.ceil((yHi - yLo) / a.pStep - 0.000001))))
        stp = (yHi - yLo) / nR
        iT = np.minimum(nR - 1, np.floor((h - yLo) / stp).astype(np.int64))
        iB = np.maximum(0, np.floor((l - yLo) / stp).astype(np.int64))
        ok = iT >= iB
        bv = _spread(v[ok], iB[ok], iT[ok], nR)
        vLo, vHi = value_area(bv, 0, nR - 1, p.va_pct)
        if vLo >= 0:
            nVah = yLo + (vHi + 1) * stp
            nVal = yLo + vLo * stp
        vLo2, vHi2 = value_area(bv, 0, nR - 1, p.va2_pct)
        if vLo2 >= 0:
            nVah2 = yLo + (vHi2 + 1) * stp
            nVal2 = yLo + vLo2 * stp
    return MB(a.area, name, _join_members(a.members, o.members), [], a.mins + o.mins, a.vol + o.vol,
              _nmin(a.tA, o.tA), _nmax(a.tB, o.tB), nVah, nVal, _nmin(a.vaA, o.vaA), _nmax(a.vaB, o.vaB), True,
              col=a.col, yLo=yLo, yHi=yHi, cH=h, cL=l, cV=v, mergedFrom=from_, pStep=a.pStep,
              dFirst=_nmin(a.dFirst, o.dFirst), dLast=_nmax(a.dLast, o.dLast), dName=a.dName,
              bHi=_nmax(a.bHi, o.bHi), bLo=_nmin(a.bLo, o.bLo), vah2=nVah2, val2=nVal2, usd=a.usd + o.usd)


def pair_score(r: int, a: MB, b: MB, aS: int, bS: int, maxH: float, p: Params) -> float:
    """Score of the pair (a, b) under rule r: < 0 = does not qualify; the highest score = the best partner (then
    the highest volume). aS / bS = where the row is: 0 = this period, 1 = the previous period, 2 = an older
    finished period. At least one row of the pair is of this period.
      1  time-overlapping rows, a's high-low >= inside_pct % inside b's                    -> b's volume
      2  same D of this period, a's VAH-VAL >= d_merge_pct % inside b's                    -> that %
      3  a of this period, b of the previous period, or of an older one that ends the day before a's first day:
         VAH-VAL overlapping >= day_merge_pct % of the smaller range, or one inside the other with the smaller
         range >= ins_merge_pct % of the bigger                                            -> that %
      4  a and b sharing a period, one's VAL = the other's VAH +- edge_ticks               -> the smaller gap"""
    s = -1.0
    if (aS == 0 or bS == 0) and a.cH is not None and b.cH is not None and span_h(a, b) <= maxH:
        if r == 1:
            if time_overlap(a, b) and inside_of(a, b) >= p.inside_pct:
                s = b.vol
        elif r == 2:
            if aS == 0 and bS == 0 and a.dName == b.dName and a.vah is not None and b.vah is not None:
                pp = mb_inside(a, b)
                if pp >= p.d_merge_pct:
                    s = pp
        elif r == 3:
            if aS == 0 and bS != 0 and (bS == 1 or (a.dFirst is not None and b.dLast is not None and b.dLast >= a.dFirst - 1)):
                pO = partial_pct(a, b) if p.day_merge else -1.0
                pI = max(inside_ratio(a, b), inside_ratio(b, a)) if p.ins_merge else -1.0
                s = max(pO if pO >= p.day_merge_pct else -1.0, pI if pI >= p.ins_merge_pct else -1.0)
        elif r == 4:
            g = edge_gap(a, b, p.tick, p.edge_ticks)
            if g >= 0:
                s = (p.edge_ticks + 1) * p.tick - g
    return s


def merge_pass(areas: List[TPArea], curL: List[MB], prvL: List[MB], oldL: List[MB], r: int, is_week: bool, p: Params) -> int:
    """One merge rule (r) on the rows of this period (curL), of the previous one (prvL) and of the older finished
    periods (oldL), again and again until no pair qualifies. Rows taken from the LOWEST volume, each merged with
    its best partner. The base (keeps its D and colour, names the new bloc Dy-mBx) = the row of this period if
    only one of them is, else the higher volume. The new bloc = the collage of ONLY their candles; it goes to
    curL, the rows it replaces leave their lists. Returns the number of merges."""
    nM = 0
    maxH = p.merge_max_h * (7.0 if is_week else 1.0)
    merging = True
    while merging:
        merging = False
        all_ = list(curL) + list(prvL) + list(oldL)
        side = [0] * len(curL) + [1] * len(prvL) + [2] * len(oldL)
        pos = list(range(len(curL))) + list(range(len(prvL))) + list(range(len(oldL)))
        n = len(all_)
        if n > 1:
            for ia in _stable_order([m.vol for m in all_]):
                a = all_[ia]
                aS = side[ia]
                best, bestS = -1, -1.0
                for k in range(n):
                    if k != ia:
                        b = all_[k]
                        s = pair_score(r, a, b, aS, side[k], maxH, p)
                        if s >= 0 and (best < 0 or s > bestS or (s == bestS and b.vol > all_[best].vol)):
                            best, bestS = k, s
                if best >= 0:
                    b = all_[best]
                    bS = side[best]
                    aBase = (a.vol >= b.vol) if ((aS == 0) == (bS == 0)) else (aS == 0)
                    base, oth = (a, b) if aBase else (b, a)
                    nmName = base.name
                    if (aS == 0 if aBase else bS == 0) and base.area < len(areas):
                        ar = areas[base.area]
                        ar.mCount += 1
                        nmName = "%s-mB%d" % (ar.name, ar.mCount)
                    tag = {1: " [high-low]", 2: " [VAH-VAL]", 4: " [VAL=VAH]"}.get(r, "")
                    nm = collage(base, oth, nmName, chain_of(a, aS, is_week, p.tz) + " + " + chain_of(b, bS, is_week, p.tz) + tag, p)
                    pA, pB = pos[ia], pos[best]
                    lists = (curL, prvL, oldL)
                    lA, lB = lists[aS], lists[bS]
                    if aS == bS:
                        del lA[max(pA, pB)]
                        del lA[min(pA, pB)]
                    else:
                        del lA[pA]
                        del lB[pB]
                    curL.append(nm)
                    nM += 1
                    merging = True
                    break
    return nM


def merge_loop(areas: List[TPArea], curL: List[MB], prvL: List[MB], oldL: List[MB], finished: bool, is_week: bool,
               p: Params) -> Tuple[int, int, int, int]:
    """Every merge rule, round after round, until a full round merges nothing: a merged bloc is a new bloc, and it
    can qualify for a merge it was never checked for. Rules 3 and 4 only on finished periods.
    Returns (rule 1 merges, rule 2, rule 3, rule 4)."""
    n1 = n2 = n3 = n4 = 0
    again = True
    guard = 0
    while again and guard < 100:
        guard += 1
        k1 = merge_pass(areas, curL, prvL, oldL, 1, is_week, p) if p.merge_blocs else 0
        k2 = merge_pass(areas, curL, prvL, oldL, 2, is_week, p) if p.d_merge else 0
        k3 = merge_pass(areas, curL, prvL, oldL, 3, is_week, p) if (finished and (p.day_merge or p.ins_merge)) else 0
        k4 = merge_pass(areas, curL, prvL, oldL, 4, is_week, p) if (finished and p.edge_merge) else 0
        n1 += k1
        n2 += k2
        n3 += k3
        n4 += k4
        again = k1 + k2 + k3 + k4 > 0
    return n1, n2, n3, n4


# ============================================================================ bloc colours
def _rgb(c: str) -> Tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def bad_bloc_col(c: str, p: Params) -> bool:
    """A colour a 'coloured' bloc may not use: the orange, or any gray (red, green and blue close together)."""
    c = c.lower()
    rr, gg, bb = _rgb(c)
    return (c == p.c_gold.lower() or c == p.c_gray.lower()
            or (abs(rr - gg) < 20 and abs(gg - bb) < 20 and abs(rr - bb) < 20)
            or (rr > 200 and 100 < gg < 200 and bb < 60))


def color_rows(known: List[MB], rows: List[MB], p: Params) -> None:
    """Orange / coloured / dark gray VAH-VAL. known = every bloc to compare with (older blocs + rows), rows = the
    blocs to colour now. In time order, each row is compared with the vol_look blocs before it: rank = % of them
    with a LOWER volume. rank > gold_pct -> orange, 4 px; gray_pct < rank <= gold_pct -> coloured, 3 px (its D
    colour, never orange / gray, never the same colour as the last coloured bloc); rank <= gray_pct -> dark gray,
    2 px."""
    for m in rows:
        m.todo = True
    n = len(known)
    if n == 0:
        return
    idx = _stable_order([0.0 if m.tA is None else float(m.tA) for m in known])
    pal = [D_COLS[0], D_COLS[1], D_COLS[2], D_COLS[3], D_COLS[4], C_UNCOV, "#2962ff", "#cddc39", "#f23645"]
    lastCol: Optional[str] = None
    for j in range(n):
        m = known[idx[j]]
        if m.todo:
            nPrev = min(int(p.vol_look), j)
            below = 0
            for k in range(j - nPrev, j):
                if known[idx[k]].vol < m.vol:
                    below += 1
            rank = below * 100.0 / nPrev if nPrev > 0 else 50.0
            cls = 2 if rank > p.gold_pct else (1 if rank > p.gray_pct else 0)
            c = (m.col or "").lower() or None
            w = 3
            if cls == 2:
                c = p.c_gold.lower()
                w = 4
            elif cls == 0:
                c = p.c_gray.lower()
                w = 2
            else:
                for pc in pal:
                    if ((c is None or bad_bloc_col(c, p) or (lastCol is not None and c == lastCol))
                            and not bad_bloc_col(pc, p) and (lastCol is None or pc != lastCol)):
                        c = pc
            m.cls = cls
            m.lnColor = c
            m.width = w
            m.todo = False
        if m.cls == 1:
            lastCol = m.lnColor


# ============================================================================ the tables (text)
def pad_r(s: str, n: int) -> str:
    return s + " " * max(0, n - len(s))


def mb_hl(m: MB, p: Params) -> str:
    return "-" if m.bHi is None else fmt_price(m.bHi, p.price_decimals) + "-" + fmt_price(m.bLo, p.price_decimals)


def mb_va_text(m: MB, p: Params) -> str:
    return "-" if m.vah is None else fmt_price(m.vah, p.price_decimals) + "-" + fmt_price(m.val, p.price_decimals)


def mb_merged_text(m: MB) -> str:
    s = m.members
    if m.mergedFrom != "":
        s = m.mergedFrom if s == "" else s + ", " + m.mergedFrom
    return "-" if s == "" else s


def mb_multi(is_week: bool, rows: List[MB]) -> bool:
    """True when a bloc's span covers more than one period (its From-To then shows the weekday)."""
    multi = bool(is_week)
    for m in rows:
        if m.dFirst is not None and m.dFirst != m.dLast:
            multi = True
    return multi


def _mb_tag(rows: List[MB], iT: int, iV: int, tag: str, tp_both: str) -> None:
    if iT == iV:
        m = rows[iT]
        m.tag = tag if m.tag == "" else m.tag + " + " + tag
    elif tp_both == "Keep both":
        m1 = rows[iT]
        m1.tag = (tag + " time") if m1.tag == "" else m1.tag + " + " + tag + " time"
        m2 = rows[iV]
        m2.tag = (tag + " vol") if m2.tag == "" else m2.tag + " + " + tag + " vol"


def mb_tags(rows: List[MB], tp_both: str) -> None:
    """MAX / MIN among the final blocs: most time AND most volume / least time AND least volume."""
    n = len(rows)
    if n == 0:
        return
    for m in rows:
        m.tag = ""
    iTmax = iVmax = iTmin = iVmin = 0
    for i in range(n):
        m = rows[i]
        if m.mins > rows[iTmax].mins:
            iTmax = i
        if m.vol > rows[iVmax].vol:
            iVmax = i
        if m.mins < rows[iTmin].mins:
            iTmin = i
        if m.vol < rows[iVmin].vol:
            iVmin = i
    _mb_tag(rows, iTmax, iVmax, "MAX", tp_both)
    _mb_tag(rows, iTmin, iVmin, "MIN", tp_both)


def d_order(rows: List[MB]) -> List[str]:
    """The Ds present in the rows, in D order (D1, D2, D3 ...)."""
    names: List[str] = []
    nums: List[float] = []
    for m in rows:
        if m.dName not in names:
            names.append(m.dName)
            try:
                nums.append(float(m.dName.replace("D", "")))
            except ValueError:
                nums.append(999.0)
    return [names[i] for i in _stable_order(nums)]


def _span_text(m: MB, fmt: str, tz: str) -> str:
    return "-" if m.tA is None else fmt_time(m.tA, fmt, tz) + "-" + fmt_time(m.tB, fmt, tz)


def rows_table1(is_week: bool, rows: List[MB], nH: int, p: Params) -> str:
    """Table 1: every FINAL bloc of the period (all Ds, after all merges), sorted by volume from the lowest to the
    highest. 'Inside' = for every OTHER bloc whose time span overlaps this one: how much of THIS bloc's high-low
    range lies inside that bloc's (>= inside_pct % only). 'Merged' = what went into it."""
    if not rows:
        return ""
    mb_tags(rows, p.tp_both)
    multi = mb_multi(is_week, rows)
    fmt = "EEE HH:mm" if multi else "HH:mm"
    spanW = 23 if multi else 13
    nameW, hlW, tagW = 6, 10, 5
    for m in rows:
        nameW = max(nameW, len(m.name) + 2)
        hlW = max(hlW, len(mb_hl(m, p)) + 2)
        tagW = max(tagW, len(m.tag) + 2)
    idx = _stable_order([m.vol for m in rows])
    txt = ("W " if is_week else "") + "BLOCS BY VOLUME (lowest -> highest) - after all merges, every merge checked again until nothing is left to merge"
    if p.merge_blocs:
        txt += "\ntime-overlapping blocs merged when high-low >= " + _num(p.inside_pct) + "% inside: " + str(nH) + (" merge" if nH == 1 else " merges")
    txt += ("\n" + pad_r("#", 4) + pad_r("Bloc", nameW) + pad_r("From-To", spanW) + pad_r("Time", 20) + pad_r("Volume", 10)
            + pad_r("High-Low", hlW) + pad_r("Tag", tagW) + pad_r("Inside time-overlapping blocs", 32) + "Merged")
    for j, me in enumerate(idx):
        rb = rows[me]
        ins = ""
        for k, o in enumerate(rows):
            if k != me and time_overlap(rb, o):
                pct = inside_of(rb, o)
                if pct >= p.inside_pct:
                    ins += ("" if ins == "" else ", ") + o.name + " " + str(int(round(pct))) + "%"
        txt += ("\n" + pad_r(str(j + 1), 4) + pad_r(rb.name, nameW) + pad_r(_span_text(rb, fmt, p.tz), spanW)
                + pad_r(fmt_bloc(rb.mins, rb.tA, rb.tB, p.tz), 20) + pad_r(fmt_vol(rb.vol), 10) + pad_r(mb_hl(rb, p), hlW)
                + pad_r(rb.tag, tagW) + pad_r("-" if ins == "" else ins, 32) + mb_merged_text(rb))
    return txt


def d_table_text(is_week: bool, rows: List[MB], nMerges: int, p: Params) -> str:
    """Table 2: for every D its FINAL blocs (after all merges), lowest -> highest volume, with the VAH-VAL overlap
    with the other blocs of the same D."""
    if not rows:
        return ""
    multi = mb_multi(is_week, rows)
    fmt = "EEE HH:mm" if multi else "HH:mm"
    spanW = 23 if multi else 13
    nameW, vaW = 6, 9
    for m in rows:
        nameW = max(nameW, len(m.name) + 2)
        vaW = max(vaW, len(mb_va_text(m, p)) + 2)
    txt = ("W " if is_week else "") + "BLOCS PER D (lowest -> highest volume) - VAH-VAL overlap with the other blocs of the same D"
    if p.d_merge:
        txt += "\nblocs of the same D merged when VAH-VAL >= " + _num(p.d_merge_pct) + "% inside: " + str(nMerges) + (" merge" if nMerges == 1 else " merges")
    for dn in d_order(rows):
        live = [i for i, m in enumerate(rows) if m.dName == dn]
        if not live:
            continue
        nL = len(live)
        totM = sum(rows[i].mins for i in live)
        totV = sum(rows[i].vol for i in live)
        txt += "\n \n" + dn + "  -  " + fmt_dur(totM) + " | vol " + fmt_vol(totV) + " | " + str(nL) + (" bloc" if nL == 1 else " blocs")
        txt += ("\n" + pad_r("#", 4) + pad_r("Bloc", nameW) + pad_r("From-To", spanW) + pad_r("Time", 20) + pad_r("Volume", 10)
                + pad_r("VAH-VAL", vaW) + "VAH-VAL inside the other blocs")
        idx = _stable_order([rows[i].vol for i in live])
        for j in range(nL):
            me = live[idx[j]]
            rb = rows[me]
            ovs = ""
            for k in live:
                if k != me:
                    o = rows[k]
                    if rb.vah is not None and o.vah is not None:
                        ovs += ("" if ovs == "" else ", ") + o.name + " " + str(int(round(mb_inside(rb, o)))) + "%"
            txt += ("\n" + pad_r(str(j + 1), 4) + pad_r(rb.name, nameW) + pad_r(_span_text(rb, fmt, p.tz), spanW)
                    + pad_r(fmt_bloc(rb.mins, rb.tA, rb.tB, p.tz), 20) + pad_r(fmt_vol(rb.vol), 10) + pad_r(mb_va_text(rb, p), vaW)
                    + ("-" if ovs == "" else ovs))
    return txt


def cap_h(is_week: bool, p: Params) -> float:
    return p.merge_max_h * (7.0 if is_week else 1.0)


def cap_text(is_week: bool, p: Params) -> str:
    if p.day_alone:
        return "week N alone" if is_week else "day N alone"
    return "max " + _num(cap_h(is_week, p)) + "h"


def day_table_text(is_week: bool, cur: List[MB], prev: List[MB], nX: int, nE: int, t0: float, pt0: Optional[float], p: Params) -> str:
    """Table 3, FINISHED periods only: every final bloc of day N (this period, after all merges) and its VAH-VAL
    relation with every day N-1 bloc left. Rows grouped by D, then volume from the lowest to the highest."""
    if not cur:
        return ""
    unit = "WEEK" if is_week else "DAY"
    fmtD = "dd MMM yyyy" if is_week else "EEE dd MMM"
    nameW, vaW = 8, 9
    for m in cur:
        nameW = max(nameW, len(m.name) + 2)
        vaW = max(vaW, len(mb_va_text(m, p)) + 2)
    txt = (unit + " N (" + fmt_time(t0, fmtD, p.tz) + ") vs " + unit + " N-1 (" + ("-" if pt0 is None else fmt_time(pt0, fmtD, p.tz))
           + ") - VAH-VAL overlap in price")
    if p.day_merge or p.ins_merge:
        txt += ("\nday N and day N-1 blocs " + (("OVERLAPPING >= " + _num(p.day_merge_pct) + "% of the smaller VAH-VAL range") if p.day_merge else "")
                + (" or " if (p.day_merge and p.ins_merge) else "")
                + (("one INSIDE the other with the smaller range >= " + _num(p.ins_merge_pct) + "% of the bigger") if p.ins_merge else "")
                + " merged with a collage: " + str(nX) + (" merge" if nX == 1 else " merges")
                + ((" (no merge with the previous " + ("week" if is_week else "day") + ": " + cap_text(is_week, p) + ")") if p.day_alone
                   else " (a merged bloc spans at most " + _num(cap_h(is_week, p)) + "h)"))
    if p.edge_merge:
        txt += ("\nblocs sharing a day with VAL = VAH +-" + str(p.edge_ticks) + (" tick" if p.edge_ticks == 1 else " ticks")
                + " merged with a collage: " + str(nE) + (" merge" if nE == 1 else " merges"))
    txt += "\n" + pad_r("Bloc", nameW) + pad_r("VAH-VAL", vaW) + unit + " N-1 blocs"
    for dn in d_order(cur):
        live = [i for i, m in enumerate(cur) if m.dName == dn]
        if not live:
            continue
        idx = _stable_order([cur[i].vol for i in live])
        for j in range(len(idx)):
            rb = cur[live[idx[j]]]
            rel = ""
            for o in prev:
                r = day_relation(rb, o)
                if r != "":
                    pO = partial_pct(rb, o) if p.day_merge else -1.0
                    pI = max(inside_ratio(rb, o), inside_ratio(o, rb)) if p.ins_merge else -1.0
                    if (pO >= p.day_merge_pct or pI >= p.ins_merge_pct) and (p.day_alone or span_h(rb, o) > cap_h(is_week, p)):
                        r += " (not merged: " + cap_text(is_week, p) + ")"
                    rel += ("" if rel == "" else ", ") + o.name + " " + r
            txt += ("\n" + pad_r(rb.name, nameW) + pad_r(mb_va_text(rb, p), vaW)
                    + (("[merged " + rb.mergedFrom + "] ") if rb.mergedFrom != "" else "") + ("-" if rel == "" else rel))
    return txt


def table_texts(is_week: bool, rows: List[MB], nH: int, nD: int, finished: bool, cmp: Optional[List[MB]], nX: int, nE: int,
                t0: float, pt0: Optional[float], p: Params) -> List[str]:
    """The texts of every table that is on, all built from the same FINAL blocs (rows). finished = a finished
    period (table 3 only there); cmp = the previous period's blocs left."""
    tabs: List[str] = []
    if p.table1:
        t1 = rows_table1(is_week, rows, nH, p)
        if t1:
            tabs.append(t1)
    if p.table2:
        t2 = d_table_text(is_week, rows, nD, p)
        if t2:
            tabs.append(t2)
    if finished and p.table3 and cmp is not None and pt0 is not None:
        t3 = day_table_text(is_week, rows, cmp, nX, nE, t0, pt0, p)
        if t3:
            tabs.append(t3)
    return tabs


def rows_of_day(live: List[MB], d: int) -> List[MB]:
    """The CURRENT blocs holding candles of period d (a merged bloc shows in every period it covers)."""
    return [m for m in live if m.dFirst is not None and m.dFirst <= d <= m.dLast]


def cmp_of_day(live: List[MB], pd: Optional[int], d: int) -> List[MB]:
    """The CURRENT blocs of period pd that are not also in period d (what d's table 3 compares with)."""
    if pd is None:
        return []
    return [m for m in live if m.dFirst is not None and m.dFirst <= pd <= m.dLast and not (m.dFirst <= d <= m.dLast)]


# ============================================================================ the chain of periods
@dataclass
class PerRec:
    """One finished period's tables: where they hang and what they were built with, so they can be rebuilt from
    the CURRENT blocs whenever a later merge changes that period's blocs."""
    day: int
    t0: float
    lo: float
    tabs: List[str]
    nH: int
    nD: int
    nX: int
    nE: int
    pDay: Optional[int]
    pT0: Optional[float]


class Chain:
    """The per-period state the Pine's Per carries from one FINISHED period to the next (prevRows, histOld, recs,
    dayId), fed oldest -> newest. fold() processes a finished period and moves the state; forming() runs the
    forming period on top of it WITHOUT touching it (the Pine's dev pass: only rules 1 and 2, on its own rows), so
    it can run again on every poll. Every row's dFirst / dLast is the period's ordinal in this chain."""

    def __init__(self, is_week: bool, p: Params):
        self.is_week = bool(is_week)
        self.p = p
        self.prev_rows: Optional[List[MB]] = None
        self.prev_t0: Optional[float] = None
        self.hist_old: List[MB] = []
        self.recs: List[PerRec] = []
        self.day_id = 0

    # ---------------------------------------------------------------- one period (the Pine's renderPer, after the areas)
    def _process(self, res: PeriodResult, dev: bool) -> Tuple[List[MB], List[str], Tuple[int, int, int, int]]:
        p = self.p
        for ar in res.areas:
            ar.mCount = ar.mCount0                      # the names restart where compute_period left them
        rowsN = list(res.rows)
        for m in rowsN:
            m.dFirst = m.dLast = self.day_id            # fillRowCandles' P.dayId
        leftP = list(self.prev_rows) if (not dev and self.prev_rows is not None) else []
        oldL = [] if (dev or p.day_alone) else self.hist_old
        mergePrv = [] if p.day_alone else leftP
        n1, n2, xN, xE = merge_loop(res.areas, rowsN, mergePrv, oldL, not dev, self.is_week, p)
        nH = res.n_m1 + n1
        nDD = res.n_d + n2
        if not dev:
            # every earlier finished period touched by a merge gets its tables rebuilt from the CURRENT blocs
            fromDay = self.day_id
            for m in rowsN:
                if m.dFirst is not None:
                    fromDay = min(fromDay, m.dFirst)
            if self.prev_rows is not None and len(leftP) < len(self.prev_rows):
                fromDay = min(fromDay, self.day_id - 1)
            if fromDay < self.day_id:
                live = list(self.hist_old) + leftP + rowsN
                for r in self.recs:
                    if r.day >= fromDay:
                        r.tabs = table_texts(self.is_week, rows_of_day(live, r.day), r.nH, r.nD, True,
                                             cmp_of_day(live, r.pDay, r.day), r.nX, r.nE, r.t0, r.pT0, p)
        known = list(self.hist_old)
        if dev:
            if self.prev_rows is not None:
                known.extend(self.prev_rows)
        else:
            known.extend(leftP)
        known.extend(rowsN)
        color_rows(known, rowsN, p)
        if not dev:
            self.hist_old.extend(leftP)                 # the previous period's blocs left are final now
            while len(self.hist_old) > 200:
                self.hist_old.pop(0)
        tabs = table_texts(self.is_week, rowsN, nH, nDD, not dev, leftP, xN, xE, res.per_mid, self.prev_t0, p)
        if not dev:
            pDay = self.recs[-1].day if self.recs else None
            self.recs.append(PerRec(self.day_id, res.per_mid, res.lo, tabs, nH, nDD, xN, xE, pDay, self.prev_t0))
            while len(self.recs) > 30:
                self.recs.pop(0)
            self.prev_rows = rowsN
            self.prev_t0 = res.per_mid
            self.day_id += 1
        return rowsN, tabs, (nH, nDD, xN, xE)

    def fold(self, res: PeriodResult) -> None:
        """Feed one FINISHED period (in time order)."""
        self._process(res, dev=False)

    def forming(self, res: PeriodResult) -> Tuple[List[MB], List[str]]:
        """Run the forming period against the state as it stands. Repeatable."""
        rows, tabs, _ = self._process(res, dev=True)
        return rows, tabs

    def live_rows(self) -> List[MB]:
        """Every final row of the finished periods still alive."""
        return list(self.hist_old) + (list(self.prev_rows) if self.prev_rows is not None else [])

    def rows_for(self, ordinal: int) -> List[MB]:
        """The live rows drawn WITH period `ordinal`: a row belongs to the period that made it (dLast)."""
        return [m for m in self.live_rows() if m.dLast == ordinal]

    def tabs_for(self, ordinal: int) -> List[str]:
        for r in self.recs:
            if r.day == ordinal:
                return r.tabs
        return []
