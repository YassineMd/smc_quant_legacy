"""RECENT-SWING LOW-VOLUME AREA — forecast S/R zones from the last swing-HIGH and swing-LOW legs.

Auction-theory idea (the user's): after an impulse leg, price tends to RETRACE into the leg's LOW-VOLUME region
before continuing. So the leg's LVN (+ its value-area context) forecasts the next S/R. We track the MAX_LEGS (3) most
recent legs and draw a directional LVN ZONE for each.

Legs (causal): `structure._zigzag_confirmed` gives confirmed pivots; the CURRENT leg is the DEVELOPING one =
anchor at the last confirmed pivot -> the RUNNING extreme since it (so a leg tracks the live swing, not a lagged
confirmed pivot). The rest are the CONFIRMED legs walking back (piv[-2]->piv[-1], piv[-3]->piv[-2], ...). ZigZag
legs alternate, so the 3 legs are up/down/up or down/up/down: an up-leg ENDS at a high (swing HIGH), a down-leg ENDS
at a low (swing LOW). A leg with no exterior low-volume node (its value area reaches the leg extreme) is omitted.

For each leg: sum its bars' footprints into ONE {price:{b,s}} ladder -> bar_quantiles interior LVN / value_area
(VAL,VAH) / vw-median / POC, PLUS the EXTERIOR low-volume node just outside the value area (the lowest-volume price
BELOW VAL / ABOVE VAH). Then the LVN ZONE (the user's rule), where `median` is the volume-weighted median and the
zone runs between the near INTERIOR edge (the interior LVN or the median, whichever is nearer the value-area centre)
and the EXTERIOR low-volume node on the trade side:
  * swing HIGH (up-leg)  -> [LVN_below_VAL, min(LVN, median)]   (LVN<median -> inner=LVN; LVN>median -> inner=median)
  * swing LOW  (down-leg)-> [max(LVN, median), LVN_above_VAH]   (LVN>median -> inner=LVN; LVN<median -> inner=median)
  where LVN_below_VAL / LVN_above_VAH = the lowest-volume price in the leg profile strictly outside the value area
  on that side (the low-volume 'gap' beyond the traded core).

The leg size is VOLATILITY-ADAPTIVE by default (thr=None -> `_adaptive_thr`), so swings stay structural as the
regime changes; pass an explicit `thr` (fraction) to override.

detect(buckets, thr=None) -> [rec, ...] (up to MAX_LEGS legs, MOST-RECENT first) or None, where rec =
  { b0,p0, b1,p1,          # leg endpoints (bar, price): p0->p1
    ends_high,             # True = up-leg (swing HIGH, support zone) / False = down-leg (swing LOW, resistance zone)
    lvn, median, val, vah, poc, lvn_ext,   # lvn_ext = the exterior low-vol node (the zone's outer edge)
    zlo, zhi }             # the LVN zone (zlo <= zhi)
"""
from __future__ import annotations

from . import structure as _st
from . import bar_quantiles as _bq

_NAN = float("nan")
SWING_THR = 0.004     # FALLBACK leg threshold (fraction) if the adaptive estimate can't compute.
MAX_LEGS = 3          # how many of the most-recent legs to draw LVN zones for
# ADAPTIVE swing threshold — scale the ZigZag leg-confirm retracement to recent VOLATILITY so a "leg" stays a
# structurally-significant move as the regime changes (quiet -> smaller swings / volatile -> bigger), rather than a
# fixed % that over-segments in calm markets and under-segments in volatile ones. Recomputed each frame, so the
# swing scale tracks the market as it develops. threshold = ATR_MULT * (mean bucket range% over ATR_WINDOW), clamped.
ATR_WINDOW = 80       # trailing buckets for the volatility estimate
ATR_MULT = 3.0        # << MAIN eyeball knob >> leg threshold = this * mean bucket-range%. Higher = fewer/bigger swings.
THR_MIN = 0.004       # clamp: adaptive threshold never below 0.4%
THR_MAX = 0.020       # clamp: never above 2.0%


def _adaptive_thr(H, L, C, window=ATR_WINDOW, mult=ATR_MULT):
    """Volatility-scaled ZigZag threshold = mult * (mean (high-low)/close over the last `window` buckets), clamped
    to [THR_MIN, THR_MAX]. On 5m SOL the mean bucket range is ~0.3%, so mult~3 -> ~0.9% legs (~8-10 bars = structural,
    matching hand-drawn swings); a calmer/hotter regime shrinks/grows it automatically."""
    rr = []
    for i in range(max(0, len(C) - window), len(C)):
        if C[i] > 0 and H[i] >= L[i]:
            rr.append((H[i] - L[i]) / C[i])
    if not rr:
        return SWING_THR
    return max(THR_MIN, min(THR_MAX, mult * (sum(rr) / len(rr))))


def _leg_profile(buckets, b0, b1):
    """Sum the footprints of buckets [b0..b1] into one {price: {'b','s'}} ladder (the swing's volume profile)."""
    prof = {}
    for k in range(b0, b1 + 1):
        for pr, v in (buckets[k].get("levels") or {}).items():
            try:
                p = float(pr)
            except (TypeError, ValueError):
                continue
            cell = prof.setdefault(p, {"b": 0.0, "s": 0.0})
            cell["b"] += float(v.get("b", 0) or 0.0)
            cell["s"] += float(v.get("s", 0) or 0.0)
    return prof


def _lvn_outside(prof, bound, below):
    """Lowest-total-volume price strictly BELOW `bound` (below=True) or strictly ABOVE it (below=False), among
    levels with volume > 0 — the low-volume 'gap' just outside the value area. NaN if there is no such level."""
    best_p = _NAN; best_v = None
    for pp, vv in prof.items():
        p = float(pp); v = float(vv.get("b", 0.0)) + float(vv.get("s", 0.0))
        if v <= 0:
            continue
        if (p < bound) if below else (p > bound):
            if best_v is None or v < best_v:
                best_v = v; best_p = p
    return best_p


def _leg_stats(buckets, b0, b1, ends_high):
    """Volume-profile stats + LVN ZONE for the leg [b0..b1]. `ends_high` True = up-leg (swing high). None if degenerate."""
    prof = _leg_profile(buckets, b0, b1)
    if len(prof) < 3:
        return None
    lvn = _bq.lvn(prof)                                 # INTERIOR LVN (strictly inside the value area)
    if lvn != lvn:                                      # NaN
        return None
    val, vah = _bq.value_area(prof)
    if val != val or vah != vah or not (vah > val):
        return None
    med = _bq.vq(prof)[1]
    if med != med:
        return None
    poc = _bq.poc(prof)
    if ends_high:                                       # swing HIGH (up-leg): from the low-vol node BELOW VAL up to
        ext = _lvn_outside(prof, val, True)             # the near interior edge = min(LVN, median)
        zlo, zhi = ext, min(lvn, med)
    else:                                               # swing LOW (down-leg): from the near interior edge = max(LVN,
        ext = _lvn_outside(prof, vah, False)            # median) up to the low-vol node ABOVE VAH
        zlo, zhi = max(lvn, med), ext
    if ext != ext or zhi <= zlo:                        # no exterior low-vol node, or degenerate zone
        return None
    return dict(lvn=lvn, median=med, val=val, vah=vah, poc=poc, lvn_ext=ext, zlo=zlo, zhi=zhi)


def detect(buckets, thr=None):
    n = len(buckets)
    if n < 4:
        return None
    H = [float(b.get("high", 0.0) or 0.0) for b in buckets]
    L = [float(b.get("low", 0.0) or 0.0) for b in buckets]
    C = [float(b.get("close", b.get("close_price", 0.0)) or 0.0) for b in buckets]
    if thr is None:
        thr = _adaptive_thr(H, L, C)                    # volatility-adaptive leg size (see _adaptive_thr)
    piv = _st._zigzag_confirmed(H, L, thr)              # [(pivot_bar, price, is_high, confirm_bar)], alternating
    if len(piv) < 2:
        return None
    pb, pprice, anchor_is_high, _cb = piv[-1]           # anchor of the developing leg
    if anchor_is_high:                                 # developing DOWN leg -> running LOW (ends at a low)
        j = pb
        for k in range(pb + 1, n):
            if L[k] < L[j]:
                j = k
        dev = (pb, pprice, j, L[j], False)
    else:                                              # developing UP leg -> running HIGH (ends at a high)
        j = pb
        for k in range(pb + 1, n):
            if H[k] > H[j]:
                j = k
        dev = (pb, pprice, j, H[j], True)
    dev_mag = abs(dev[3] - dev[1]) / dev[1] if dev[1] > 0 else 0.0
    legs_raw = []
    if dev[2] > dev[0] and dev_mag >= thr:             # developing leg = the most recent (live) leg
        legs_raw.append(dev)
    for k in range(len(piv) - 1, 0, -1):               # then the CONFIRMED legs, most recent first
        a = piv[k - 1]; b = piv[k]
        legs_raw.append((a[0], a[1], b[0], b[1], b[2]))   # ends_high = b.is_high
    out = []
    for (b0, p0, b1, p1, ends_high) in legs_raw[:MAX_LEGS]:   # the MAX_LEGS most recent legs
        if b1 <= b0:
            continue
        stats = _leg_stats(buckets, b0, b1, ends_high)
        if not stats:                                  # no exterior low-vol node -> omit this leg's zone
            continue
        out.append(dict(b0=b0, p0=p0, b1=b1, p1=p1, ends_high=ends_high, **stats))
    return out or None
