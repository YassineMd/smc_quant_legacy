"""RECENT-SWING LOW-VOLUME AREA — forecast S/R zones from the last swing-HIGH and swing-LOW legs.

Auction-theory idea (the user's): after an impulse leg, price tends to RETRACE into the leg's LOW-VOLUME region
before continuing. So the leg's LVN (+ its value-area context) forecasts the next S/R. We track the TWO most recent
legs — the one ending at the last swing HIGH (up-leg) and the one ending at the last swing LOW (down-leg) — and draw
a directional LVN ZONE for each.

Legs (causal): `structure._zigzag_confirmed` gives confirmed pivots; the CURRENT leg is the DEVELOPING one =
anchor at the last confirmed pivot -> the RUNNING extreme since it (so a leg tracks the live swing, not a lagged
confirmed pivot). The other of the two is the last CONFIRMED leg (piv[-2]->piv[-1]). Because ZigZag legs alternate,
one of the two ends at a HIGH (up-leg = swing_high) and the other ends at a LOW (down-leg = swing_low).

For each leg: sum its bars' footprints into ONE {price:{b,s}} ladder -> bar_quantiles LVN / value_area (VAL,VAH) /
vw-median / POC. Then the LVN ZONE (the user's rule), where `median` is the volume-weighted median:
  * swing HIGH (up-leg)  -> zone in the LOWER value area, capped below by VAL:  [VAL, min(LVN, median)]
                           (LVN below median -> [VAL, LVN];  LVN above median -> [VAL, median])
  * swing LOW  (down-leg)-> zone in the UPPER value area, capped above by VAH:  [max(LVN, median), VAH]
                           (LVN above median -> [LVN, VAH];  LVN below median -> [median, VAH])

detect(buckets, thr=SWING_THR) -> { 'swing_high': rec|—, 'swing_low': rec|— } or None, where rec =
  { b0,p0, b1,p1,          # leg endpoints (bar, price): p0->p1
    lvn, median, val, vah, poc,
    zlo, zhi }             # the LVN zone (zlo <= zhi)
"""
from __future__ import annotations

from . import structure as _st
from . import bar_quantiles as _bq

SWING_THR = 0.004     # ZigZag leg confirm threshold (FRACTION) = 0.4%. Tune + relaunch (0.25% dense .. 0.6% coarse).


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
    lvn = _bq.lvn(prof)
    if lvn != lvn:                                      # NaN
        return None
    val, vah = _bq.value_area(prof)
    if val != val or vah != vah or not (vah > val):
        return None
    med = _bq.vq(prof)[1]
    if med != med:
        return None
    poc = _bq.poc(prof)
    if ends_high:                                       # swing HIGH (up-leg): LOWER VA, capped below by VAL
        zlo, zhi = val, min(lvn, med)
    else:                                               # swing LOW (down-leg): UPPER VA, capped above by VAH
        zlo, zhi = max(lvn, med), vah
    if zhi <= zlo:
        return None
    return dict(lvn=lvn, median=med, val=val, vah=vah, poc=poc, zlo=zlo, zhi=zhi)


def detect(buckets, thr=None):
    n = len(buckets)
    if n < 4:
        return None
    if thr is None:
        thr = SWING_THR
    H = [float(b.get("high", 0.0) or 0.0) for b in buckets]
    L = [float(b.get("low", 0.0) or 0.0) for b in buckets]
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
    conf = (piv[-2][0], piv[-2][1], piv[-1][0], piv[-1][1], piv[-1][2])   # last CONFIRMED leg (ends at piv[-1])
    if dev[2] > dev[0] and dev_mag >= thr:             # developing leg valid -> {developing, last-confirmed}
        legs = [dev, conf]
    elif len(piv) >= 3:                                # degenerate developing -> last two CONFIRMED legs
        legs = [conf, (piv[-3][0], piv[-3][1], piv[-2][0], piv[-2][1], piv[-2][2])]
    else:
        legs = [conf]
    out = {}
    for (b0, p0, b1, p1, ends_high) in legs:
        if b1 <= b0:
            continue
        stats = _leg_stats(buckets, b0, b1, ends_high)
        if not stats:
            continue
        out["swing_high" if ends_high else "swing_low"] = dict(b0=b0, p0=p0, b1=b1, p1=p1, **stats)
    return out or None
