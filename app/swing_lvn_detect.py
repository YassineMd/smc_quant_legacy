"""RECENT-SWING LOW-VOLUME AREA — forecast S/R from the last completed swing leg's volume profile.

Auction-theory idea (the user's): after an impulse leg, price tends to RETRACE into the leg's LOW-VOLUME
NODE (the price it passed through fastest / on least volume) before continuing in the leg's direction. So the
LVN of the last completed leg is a forecast SUPPORT (up-leg -> bias long) or RESISTANCE (down-leg -> bias short).

Pipeline: ZigZag (structure._zigzag_confirmed, causal/confirmed pivots) -> the last COMPLETED leg
[pivot -> opposite pivot] -> sum that leg's bars' footprints into ONE price ladder -> bar_quantiles LVN /
value area / POC -> a contiguous LOW-VOLUME AREA band around the LVN.

The leg spans low->high (up-leg) or high->low (down-leg): its two endpoints are the confirmed ZigZag pivots,
which ARE the leg's extremes, so summing the leg bars' footprints gives the profile of exactly that swing.

detect(buckets, thr=SWING_THR) -> dict | None:
  { b0,p0, b1,p1,        # leg endpoints (bar, price): p0->p1
    is_up, bias,         # up-leg -> bias 'long' (LVN = support); down-leg -> 'short' (LVN = resistance)
    lvn, val, vah, poc,  # the leg profile's low-volume node + 70% value area + point of control
    blo, bhi }           # the contiguous low-volume AREA band containing the LVN (blo <= lvn <= bhi)
"""
from __future__ import annotations

from . import structure as _st
from . import bar_quantiles as _bq

SWING_THR = 0.004     # ZigZag leg confirm threshold (FRACTION) = 0.4%. Tune + relaunch (0.25% dense .. 0.6% coarse).
BAND_FRAC = 0.40      # low-volume AREA = contiguous VA-interior levels with total vol <= this * POC vol (containing the LVN)


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


def _lv_band(prof, val, vah, lvn, poc_vol):
    """Contiguous LOW-VOLUME AREA around the LVN: the run of VA-interior levels (val<price<vah) whose total
    volume stays <= BAND_FRAC*poc_vol, containing the LVN. Returns (blo, bhi); (lvn, lvn) if the LVN is isolated."""
    pr = sorted((p, c["b"] + c["s"]) for p, c in prof.items() if val < p < vah)
    if not pr or poc_vol <= 0:
        return lvn, lvn
    thr = BAND_FRAC * poc_vol
    idx = min(range(len(pr)), key=lambda k: abs(pr[k][0] - lvn))
    lo = hi = idx
    while lo - 1 >= 0 and pr[lo - 1][1] <= thr:
        lo -= 1
    while hi + 1 < len(pr) and pr[hi + 1][1] <= thr:
        hi += 1
    return pr[lo][0], pr[hi][0]


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
    (b0, p0, _ih0, _c0), (b1, p1, ih1, c1) = piv[-2], piv[-1]   # the last COMPLETED leg
    if b1 <= b0:
        return None
    prof = _leg_profile(buckets, b0, b1)
    if len(prof) < 3:
        return None
    lvn = _bq.lvn(prof)
    if lvn != lvn:                                     # NaN -> no interior low-volume node (degenerate profile)
        return None
    val, vah = _bq.value_area(prof)
    poc = _bq.poc(prof)
    poc_vol = max((c["b"] + c["s"]) for c in prof.values())
    blo, bhi = _lv_band(prof, val, vah, lvn, poc_vol)
    is_up = bool(ih1)                                  # last pivot a HIGH => up-leg (low p0 -> high p1)
    return dict(b0=b0, p0=p0, b1=b1, p1=p1, is_up=is_up,
                bias=("long" if is_up else "short"),
                lvn=lvn, val=val, vah=vah, poc=poc, blo=blo, bhi=bhi, confirm=c1)
