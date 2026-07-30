"""RECENT-SWING LOW-VOLUME AREA — forecast S/R zones from the last swing-HIGH and swing-LOW legs.

Auction-theory idea (the user's): after an impulse leg, price tends to RETRACE into the leg's LOW-VOLUME region
before continuing. So the leg's LVN (+ its value-area context) forecasts the next S/R. We keep EVERY still-UNMITIGATED
LVN zone from the recent legs (a zone persists on the chart until price closes through it).

Legs (causal): `structure._zigzag_confirmed` gives confirmed pivots; the CURRENT leg is the DEVELOPING one =
anchor at the last confirmed pivot -> the RUNNING extreme since it (so a leg tracks the live swing, not a lagged
confirmed pivot). The rest are the CONFIRMED legs walking back (piv[-2]->piv[-1], piv[-3]->piv[-2], ...). ZigZag
legs alternate up/down: an up-leg ENDS at a high (swing HIGH, support zone), a down-leg ENDS at a low (swing LOW,
resistance zone). A leg with no exterior low-volume node (its value area reaches the leg extreme) is omitted.

CONSUMED (mitigated) zones are dropped: a resistance zone once price CLOSES above it (above the zone's high), a
support zone once price CLOSES below it (below the zone's low) — checked from the leg's extreme onward. We examine up
to SCAN_LEGS recent legs and keep EVERY still-unmitigated zone, so live zones persist on the chart until broken
(the drawn set can be any size and non-alternating — e.g. several stacked supports if the resistances were consumed).

For each leg: sum its bars' footprints into ONE {price:{b,s}} ladder -> bar_quantiles LVN / value_area (VAL,VAH) /
vw-median / POC. Then the LVN ZONE (the user's rule), where `median` is the volume-weighted median and the zone runs
between the near INTERIOR edge (the interior LVN or the median, whichever is nearer the value-area centre) and the
VALUE-AREA boundary on the trade side:
  * swing HIGH (up-leg)  -> [VAL, min(LVN, median)]   (LVN<median -> inner=LVN; LVN>median -> inner=median)
  * swing LOW  (down-leg)-> [max(LVN, median), VAH]   (LVN>median -> inner=LVN; LVN<median -> inner=median)

The leg size is VOLATILITY-ADAPTIVE by default (thr=None -> `_adaptive_thr`), so swings stay structural as the
regime changes; pass an explicit `thr` (fraction) to override.

detect(buckets, thr=None) -> [rec, ...] (up to MAX_LEGS legs, MOST-RECENT first) or None, where rec =
  { b0,p0, b1,p1,          # leg endpoints (bar, price): p0->p1
    ends_high,             # True = up-leg (swing HIGH, support zone) / False = down-leg (swing LOW, resistance zone)
    lvn, median, val, vah, poc,
    zlo, zhi }             # the LVN zone (zlo <= zhi)
"""
from __future__ import annotations

from . import structure as _st
from . import bar_quantiles as _bq

SWING_THR = 0.004     # FALLBACK leg threshold (fraction) if the adaptive estimate can't compute.
SCAN_LEGS = 30        # how many recent legs to examine — EVERY still-UNMITIGATED zone among them is kept/drawn
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
    if ends_high:                                       # swing HIGH (up-leg): LOWER value area, capped below by VAL
        zlo, zhi = val, min(lvn, med)
    else:                                               # swing LOW (down-leg): UPPER value area, capped above by VAH
        zlo, zhi = max(lvn, med), vah
    if zhi <= zlo:                                      # degenerate zone
        return None
    return dict(lvn=lvn, median=med, val=val, vah=vah, poc=poc, zlo=zlo, zhi=zhi)


def _dev_leg(buckets, thr=None):
    """Shared front-end: (H, L, C, thr, piv, dev). dev = the DEVELOPING leg (b0,p0,b1,p1,ends_high) — anchored at the
    last confirmed pivot, extended to the RUNNING extreme — or None. Returns None (whole) if the window is too short."""
    n = len(buckets)
    if n < 4:
        return None
    H = [float(b.get("high", 0.0) or 0.0) for b in buckets]
    L = [float(b.get("low", 0.0) or 0.0) for b in buckets]
    C = [float(b.get("close", b.get("close_price", 0.0)) or 0.0) for b in buckets]
    if thr is None:
        thr = _adaptive_thr(H, L, C)                    # volatility-adaptive leg size (see _adaptive_thr)
    piv = _st._zigzag_confirmed(H, L, thr)              # [(pivot_bar, price, is_high, confirm_bar)], alternating
    dev = None
    if piv:
        pb, pprice, anchor_is_high, _cb = piv[-1]       # anchor of the developing leg
        if anchor_is_high:                             # developing DOWN leg -> running LOW (ends at a low)
            j = pb
            for k in range(pb + 1, n):
                if L[k] < L[j]:
                    j = k
            dev = (pb, pprice, j, L[j], False)
        else:                                          # developing UP leg -> running HIGH (ends at a high)
            j = pb
            for k in range(pb + 1, n):
                if H[k] > H[j]:
                    j = k
            dev = (pb, pprice, j, H[j], True)
    return H, L, C, thr, piv, dev


def detect(buckets, thr=None):
    r = _dev_leg(buckets, thr)
    if not r:
        return None
    H, L, C, thr, piv, dev = r
    if len(piv) < 2 or dev is None:
        return None
    n = len(buckets)
    dev_mag = abs(dev[3] - dev[1]) / dev[1] if dev[1] > 0 else 0.0
    legs_raw = []
    if dev[2] > dev[0] and dev_mag >= thr:             # developing leg = the most recent (live) leg
        legs_raw.append(dev)
    for k in range(len(piv) - 1, 0, -1):               # then the CONFIRMED legs, most recent first
        a = piv[k - 1]; b = piv[k]
        legs_raw.append((a[0], a[1], b[0], b[1], b[2]))   # ends_high = b.is_high
    out = []
    for (b0, p0, b1, p1, ends_high) in legs_raw[:SCAN_LEGS]:   # keep EVERY still-unmitigated zone among the recent legs
        if b1 <= b0:
            continue
        stats = _leg_stats(buckets, b0, b1, ends_high)
        if not stats:                                  # no exterior low-vol node -> omit this leg's zone
            continue
        zlo = stats["zlo"]; zhi = stats["zhi"]
        # CONSUMED: price CLOSED beyond the far edge AFTER the leg's extreme (resistance broken UP / support broken DOWN)
        consumed = False
        for k in range(b1 + 1, n):
            if (ends_high and C[k] < zlo) or (not ends_high and C[k] > zhi):
                consumed = True
                break
        if consumed:
            continue
        out.append(dict(b0=b0, p0=p0, b1=b1, p1=p1, ends_high=ends_high, **stats))
    return out or None


# Forecast projection knobs (all CAUSAL — computed from the developing leg + past legs only).
CONT_MIN = 0.5        # continuation: min ("at least") target = leg size * this, beyond the current extreme
CONT_MAX = 1.0        # continuation: max ("maximum") target = a full measured move (leg size * this)
RETR_MIN_FIB = 0.382  # fallback retrace (no zone): min = this * leg size back from the extreme
RETR_MAX_FIB = 0.618  # fallback retrace (no zone): max = this * leg size back from the extreme


def forecast(buckets, thr=None):
    """CAUSAL forecast of the developing swing as TWO gray lines fanning from the current swing extreme, each with a
    'min' (at least) and 'max' (maximum) dot, angled by projecting to the target over the typical recent-leg duration:
      * CONTINUATION (with the move): min = extreme + CONT_MIN*legsize, max = extreme + CONT_MAX*legsize (measured move).
      * RETRACEMENT (against the move): into the developing leg's OWN zone — min = the zone edge NEAR the extreme,
        max = the FAR edge; falls back to fib retracements of the leg if the leg has no drawable zone.
    -> {b1, p1, is_up, cont:[(bar,px)min,(bar,px)max], retr:[(bar,px)min,(bar,px)max]} or None.
    Re-aims every frame as the extreme / leg size / durations update, so it adjusts as price develops."""
    r = _dev_leg(buckets, thr)
    if not r:
        return None
    H, L, C, thr, piv, dev = r
    if dev is None:
        return None
    b0, p0, b1, p1, is_up = dev
    if b1 <= b0 or p1 <= 0:
        return None
    lsz = abs(p1 - p0)                                   # current leg size (price)
    if lsz <= 0:
        return None
    s = 1 if is_up else -1
    durs = [piv[k][0] - piv[k - 1][0] for k in range(1, len(piv)) if piv[k][0] > piv[k - 1][0]]
    D = max(4, int(sorted(durs)[len(durs) // 2])) if durs else max(4, b1 - b0)   # typical recent-leg duration (bars)
    # CONTINUATION (with the move): extend past the extreme by [CONT_MIN, CONT_MAX] * leg size
    cont = [(b1 + max(2, round(0.75 * D)), p1 + s * CONT_MIN * lsz),
            (b1 + max(3, round(1.5 * D)), p1 + s * CONT_MAX * lsz)]
    # RETRACEMENT (against the move): into the developing leg's own zone (near edge -> far edge), else fib retrace
    st = _leg_stats(buckets, b0, b1, is_up)
    if st:
        near, far = (st["zhi"], st["zlo"]) if is_up else (st["zlo"], st["zhi"])   # near = zone edge closer to the extreme
    else:
        near, far = p1 - s * RETR_MIN_FIB * lsz, p1 - s * RETR_MAX_FIB * lsz
    retr = [(b1 + max(2, round(0.5 * D)), near),
            (b1 + max(3, round(1.0 * D)), far)]
    return dict(b1=b1, p1=p1, is_up=is_up, cont=cont, retr=retr)
