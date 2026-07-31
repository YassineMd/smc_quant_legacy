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

detect(buckets, thr=None) -> [zone, ...] MOST-RECENT first, or None. Overlapping SAME-TYPE zones are MERGED, so a
zone is a consolidated band: { ends_high,   # True = support (up-leg) / False = resistance (down-leg)
    zlo, zhi,             # the merged LVN band (union of the overlapping zones, zlo <= zhi)
    lvn,                  # representative LVN (the most-recent constituent's)
    b0, b1,              # earliest constituent bar (band left extent) + most-recent constituent bar
    n }                   # how many raw zones were merged into this band
"""
from __future__ import annotations

import math

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
    return _merge_zones(out) or None


def _merge_zones(zones):
    """Merge overlapping SAME-TYPE LVN zones (support+support / resistance+resistance) into consolidated bands =
    the union of their [zlo,zhi]. Keeps the MOST-RECENT constituent's LVN + bar, the EARLIEST bar (band left
    extent), and a count `n` of how many zones were merged. Returns merged zone dicts sorted most-recent first."""
    merged = []
    for kind in (True, False):                          # supports (ends_high=True), then resistances
        grp = sorted((z for z in zones if z["ends_high"] == kind), key=lambda z: z["zlo"])
        cur = None
        for z in grp:
            if cur is not None and z["zlo"] <= cur["zhi"]:      # overlaps / touches -> merge into the running band
                cur["zlo"] = min(cur["zlo"], z["zlo"])
                cur["zhi"] = max(cur["zhi"], z["zhi"])
                cur["b0"] = min(cur["b0"], z["b0"])
                if z["b1"] >= cur["b1"]:                        # most-recent leg drives the representative LVN
                    cur["b1"] = z["b1"]; cur["lvn"] = z["lvn"]
                cur["n"] += 1
            else:
                if cur is not None:
                    merged.append(cur)
                cur = dict(ends_high=kind, zlo=z["zlo"], zhi=z["zhi"], lvn=z["lvn"], b0=z["b0"], b1=z["b1"], n=1)
        if cur is not None:
            merged.append(cur)
    merged.sort(key=lambda z: z["b1"], reverse=True)    # most-recent first (stable render-slot ordering)
    return merged


# WAVE-forecast knobs (CAUSAL — the ratios are MEASURED from the market's own recent waves, not fixed fibs).
WAVE_LEGS = 12        # how many recent CONFIRMED legs to measure the wave rhythm from
PCT_LO = 0.30         # "at least" percentile of the measured ratio distribution
PCT_HI = 0.75         # "maximum" percentile
RET_FALLBACK = (0.382, 0.618)   # retrace ratio (correction / prior impulse) when too few measured waves yet
EXP_FALLBACK = (0.80, 1.30)     # expansion ratio (impulse / prior impulse) fallback
RET_CLAMP = (0.10, 1.30)        # sane bounds on a measured retrace ratio
EXP_CLAMP = (0.40, 3.00)        # sane bounds on a measured expansion ratio


def _pct(vals, q):
    if not vals:
        return None
    sv = sorted(vals)
    return sv[min(len(sv) - 1, max(0, int(round(q * (len(sv) - 1)))))]


def _clamp(x, lo, hi):
    return lo if x < lo else (hi if x > hi else x)


def forecast(buckets, thr=None):
    """CAUSAL WAVE forecast: read the recent swing legs as a wave sequence, MEASURE this market's own retrace depth
    (correction/prior-impulse) and impulse expansion (impulse/prior-impulse) + duration, then project TWO gray lines
    from the current price — CONTINUATION (with the trend) and RETRACEMENT (against it) — each with a 'min' (at least)
    and 'max' (maximum) dot taken from the PCT_LO/PCT_HI percentiles of those measured ratios. Adapts every frame as
    the waves develop (deeper retraces / expanding impulses shift the targets). Falls back to fibs with too few waves.
    -> {b1, p1, is_up, cont:[(bar,px)min,(bar,px)max], retr:[(bar,px)min,(bar,px)max]} or None."""
    r = _dev_leg(buckets, thr)
    if not r:
        return None
    H, L, C, thr, piv, dev = r
    if dev is None or len(piv) < 3:
        return None
    n = len(buckets)
    b0, p0, b1, p1, dev_up = dev
    # wave sequence = confirmed legs + the developing leg, as (b0,p0,b1,p1,ends_high)
    legs = [(piv[k - 1][0], piv[k - 1][1], piv[k][0], piv[k][1], piv[k][2]) for k in range(1, len(piv))]
    legs.append(dev)
    # trend = net displacement over the recent legs
    ref_i = max(0, len(legs) - 6)
    s = 1 if legs[-1][3] >= legs[ref_i][1] else -1
    # measure ratios from the recent CONFIRMED legs (exclude the incomplete developing leg)
    recent = legs[-(WAVE_LEGS + 1):-1]
    if not recent:
        return None
    sizes = [abs(l[3] - l[1]) for l in recent]
    isimp = [(l[4] == (s > 0)) for l in recent]                      # impulse if the leg runs WITH the trend
    ret_r = []; exp_r = []
    for i in range(len(recent)):
        if not isimp[i] and i >= 1 and isimp[i - 1] and sizes[i - 1] > 0:     # correction after an impulse
            ret_r.append(sizes[i] / sizes[i - 1])
        if isimp[i] and i >= 2 and isimp[i - 2] and sizes[i - 2] > 0:         # impulse vs the prior impulse
            exp_r.append(sizes[i] / sizes[i - 2])
    _rl = _pct(ret_r, PCT_LO); _rh = _pct(ret_r, PCT_HI)
    _el = _pct(exp_r, PCT_LO); _eh = _pct(exp_r, PCT_HI)
    ret_lo = _clamp(_rl if _rl is not None else RET_FALLBACK[0], *RET_CLAMP)
    ret_hi = _clamp(_rh if _rh is not None else RET_FALLBACK[1], *RET_CLAMP)
    exp_lo = _clamp(_el if _el is not None else EXP_FALLBACK[0], *EXP_CLAMP)
    exp_hi = _clamp(_eh if _eh is not None else EXP_FALLBACK[1], *EXP_CLAMP)
    # reference impulse size = the most recent completed impulse leg (in trend direction)
    I_ref = next((abs(l[3] - l[1]) for l in reversed(recent) if l[4] == (s > 0) and abs(l[3] - l[1]) > 0), 0.0)
    if I_ref <= 0:
        I_ref = max(sizes, default=abs(p1 - p0))
    if I_ref <= 0:
        return None
    lastHigh = p1 if dev_up else p0                                  # structural refs (developing extreme is one of them)
    lastLow = p0 if dev_up else p1
    cur = C[-1] if C and C[-1] > 0 else p1
    # targets from the MEASURED ratios: continuation off the last low (up-trend) / high (down-trend); retrace off the other
    if s > 0:
        cont = [lastLow + exp_lo * I_ref, lastLow + exp_hi * I_ref]
        retr = [lastHigh - ret_lo * I_ref, lastHigh - ret_hi * I_ref]
    else:
        cont = [lastHigh - exp_lo * I_ref, lastHigh - exp_hi * I_ref]
        retr = [lastLow + ret_lo * I_ref, lastLow + ret_hi * I_ref]

    def _ahead(tp, d):                                              # keep the target on the correct side of price
        return max(tp, cur * 1.001) if d > 0 else min(tp, cur * 0.999)
    cont = [_ahead(cont[0], s), _ahead(cont[1], s)]
    retr = [_ahead(retr[0], -s), _ahead(retr[1], -s)]
    if s > 0:
        cont[1] = max(cont[1], cont[0]); retr[1] = min(retr[1], retr[0])   # max dot beyond min dot
    else:
        cont[1] = min(cont[1], cont[0]); retr[1] = max(retr[1], retr[0])
    durs = [piv[k][0] - piv[k - 1][0] for k in range(1, len(piv)) if piv[k][0] > piv[k - 1][0]]
    D = max(4, int(_pct(durs, 0.5))) if durs else max(4, b1 - b0)   # typical recent-leg duration (bars)
    t = n - 1
    cont_dots = [(t + max(2, round(0.5 * D)), cont[0]), (t + max(3, round(1.0 * D)), cont[1])]
    retr_dots = [(t + max(2, round(0.5 * D)), retr[0]), (t + max(3, round(1.0 * D)), retr[1])]
    return dict(b1=t, p1=cur, is_up=(s > 0), cont=cont_dots, retr=retr_dots)


# BIAS + CONFIDENCE knobs (all CAUSAL). Confidence = weighted blend of three 0..1 sub-scores.
ALIGN_SCALE = 0.010   # zone-alignment falloff: price this fraction (1%) of price away from the aligned zone -> ~0
W_AGREE = 0.35        # weight: trend-agreement (structure cleanliness)
W_DOM = 0.35          # weight: impulse dominance (impulses vs corrections)
W_ALIGN = 0.30        # weight: zone alignment (setup quality)


def bias(buckets, thr=None, zones=None):
    """CAUSAL directional bias + 0..1 confidence from the swing/LVN structure. dir = trend from the wave sequence
    (net higher-highs/lows -> long); confidence = W_AGREE*agreement + W_DOM*dominance + W_ALIGN*alignment where:
      agreement = fraction of recent same-type pivots moving WITH the trend (clean HH/HL vs mixed),
      dominance = median impulse / (median impulse + median correction) (shallow retraces -> trend in control),
      alignment = price's position vs the aligned live zone (in the support zone for a long -> 1; in the OPPOSITE
                  zone -> 0 conflict; far -> ~0 extended).
    state = 'setup' (price at the aligned zone) / 'extended' / 'conflict'; dir None (NEUTRAL) when structure is unclear.
    -> {dir, state, confidence, sub:{agreement, dominance, alignment}}."""
    r = _dev_leg(buckets, thr)
    _neutral = {"dir": None, "state": None, "confidence": 0.0,
                "sub": {"agreement": 0.0, "dominance": 0.0, "alignment": 0.0}}
    if not r:
        return _neutral
    H, L, C, thr, piv, dev = r
    if dev is None or len(piv) < 4:
        return _neutral
    legs = [(piv[k - 1][0], piv[k - 1][1], piv[k][0], piv[k][1], piv[k][2]) for k in range(1, len(piv))]
    legs.append(dev)
    ref_i = max(0, len(legs) - 6)
    s = 1 if legs[-1][3] >= legs[ref_i][1] else -1
    recent = legs[-(WAVE_LEGS + 1):-1]
    if not recent:
        return _neutral
    sizes = [abs(l[3] - l[1]) for l in recent]
    isimp = [(l[4] == (s > 0)) for l in recent]
    # 1. trend agreement: recent same-type pivots moving WITH the trend
    highs = [p[1] for p in piv if p[2]]
    lows = [p[1] for p in piv if not p[2]]

    def _frac(vals):
        v = vals[-4:]
        if len(v) < 2:
            return 0.5
        return sum(1 for i in range(1, len(v)) if (v[i] > v[i - 1]) == (s > 0)) / (len(v) - 1)
    agreement = 0.5 * _frac(highs) + 0.5 * _frac(lows)
    # 2. impulse dominance
    imp = [sizes[i] for i in range(len(recent)) if isimp[i]]
    cor = [sizes[i] for i in range(len(recent)) if not isimp[i]]
    if imp and cor:
        mi = sorted(imp)[len(imp) // 2]; mc = sorted(cor)[len(cor) // 2]
        dom_raw = mi / (mi + mc) if (mi + mc) > 0 else 0.5
    else:
        dom_raw = 0.85 if imp else 0.5
    dominance = _clamp((dom_raw - 0.45) / 0.35, 0.0, 1.0)
    # 3. zone alignment (aligned = support zones for a long / resistance for a short)
    cur = C[-1] if C and C[-1] > 0 else legs[-1][3]
    if zones is None:
        zones = detect(buckets, thr) or []
    aligned = [z for z in zones if z["ends_high"] == (s > 0)]
    opposite = [z for z in zones if z["ends_high"] != (s > 0)]
    if any(z["zlo"] <= cur <= z["zhi"] for z in opposite):
        alignment = 0.0; state = "conflict"
    else:
        best = None
        for z in aligned:
            d = 0.0 if z["zlo"] <= cur <= z["zhi"] else (abs(z["zlo"] - cur if cur < z["zlo"] else cur - z["zhi"]) / cur)
            best = d if best is None else min(best, d)
        if best is None:
            alignment = 0.0; state = "extended"
        else:
            alignment = _clamp(1.0 - best / ALIGN_SCALE, 0.0, 1.0)
            state = "setup" if alignment >= 0.5 else "extended"
    conf = W_AGREE * agreement + W_DOM * dominance + W_ALIGN * alignment
    direction = ("long" if s > 0 else "short") if (agreement >= 0.5 and len(recent) >= 3) else None
    if direction is None:                               # unclear trend -> neutral, damp the confidence
        state = None; conf *= 0.5
    return {"dir": direction, "state": state, "confidence": round(_clamp(conf, 0.0, 1.0), 3),
            "sub": {"agreement": round(agreement, 3), "dominance": round(dominance, 3), "alignment": round(alignment, 3)}}


# --------------------------------------------------------------------------------------------------
# SWING absorb-A — the whole-swing analog of app/absorption.py, measuring whether the CVD swing
# (net delta over the leg) is PROPORTIONAL to the price swing (leg displacement). Measured 2026-07-31:
# corr(dV,dP) ~ +0.76 (structural 15m), ~84% of swings proportional; down-legs track flow better than up.
#   A ~ 0  proportional (symmetric) · A > 0 ABSORBED (price lagged the flow) · A < 0 EASY (ran on little flow)
# Each swing is split at its MIDPOINT bar into two halves; A1 / A2 are the same measure on each half, baselined
# on prior legs' matching half (per-half baselines, like absorption.residual_halves).
# --------------------------------------------------------------------------------------------------
SL_WLEGS = 30         # trailing legs for the swing absorb-A z-score baseline (matches absorption.WINDOW)
SL_MINOBS = 12        # min prior legs before A is trusted (a live frame holds fewer legs than the 18mo study)


def _swing_A(win, cur):
    """A from a window of prior (dP, dV) leg-pairs + the current pair. + = absorbed, - = easy, ~0 = proportional.
    None when the baseline is too thin/degenerate. Mirrors absorption.residual: R = Z(dP) - rho*Z(dV), A oriented
    by the delta's sign."""
    if len(win) < SL_MINOBS:
        return None
    ps = [p[0] for p in win]; vs = [p[1] for p in win]; nw = float(len(win))
    mp = sum(ps) / nw; mv = sum(vs) / nw
    sp = math.sqrt(sum((x - mp) ** 2 for x in ps) / (nw - 1))
    sv = math.sqrt(sum((x - mv) ** 2 for x in vs) / (nw - 1))
    if sp <= 0 or sv <= 0:
        return None
    cov = sum((vs[k] - mv) * (ps[k] - mp) for k in range(len(win))) / (nw - 1)
    rho = max(-1.0, min(1.0, cov / (sv * sp)))
    dP, dV = cur
    R = (dP - mp) / sp - rho * (dV - mv) / sv
    return 0.0 if dV == 0 else (-R if dV > 0 else R)


def _leg_key(buckets, b0, p0):
    """Stable per-leg identity across frames: the anchor pivot's (start_time, price*1000). Lets the terminal
    remember a leg's click-chosen division (÷2/÷3/÷4) as the frame scrolls and bar indices shift."""
    t = float(buckets[b0].get("start_time", 0.0) or 0.0) if 0 <= b0 < len(buckets) else 0.0
    return (int(t), int(round(p0 * 1000)))


def _leg_segments(cum, C, b0, p0, b1, p1, N):
    """Split the leg [b0..b1] into N even-by-BAR segments. RESULT = the real price path (pivot -> interior closes ->
    pivot); EFFORT = net delta over each segment. -> (pairs=[(dP_k,dV_k)], splitbars=[N-1 interior bars], N_used)."""
    span = b1 - b0
    if span < 1:
        return [], [], 1
    N = max(1, min(int(N), span))                            # can't have more parts than bars
    bnds = [b0 + int(round(span * k / N)) for k in range(N + 1)]
    bnds[0] = b0; bnds[-1] = b1
    for k in range(1, len(bnds)):                            # keep strictly increasing on tiny legs
        if bnds[k] <= bnds[k - 1]:
            bnds[k] = bnds[k - 1] + 1
    if bnds[-1] != b1:                                       # couldn't split cleanly -> one whole segment
        return [((p1 - p0) / p0 * 100.0 if p0 > 0 else 0.0, cum[b1 + 1] - cum[b0 + 1])], [], 1
    prices = [p0] + [(C[bnds[k]] if 0 <= bnds[k] < len(C) and C[bnds[k]] > 0 else p0) for k in range(1, N)] + [p1]
    pairs = []
    for k in range(N):
        a = bnds[k]; b = bnds[k + 1]; pa = prices[k]; pb = prices[k + 1]
        pairs.append(((pb - pa) / pa * 100.0 if pa > 0 else 0.0, cum[b + 1] - cum[a + 1]))
    return pairs, bnds[1:-1], N


def swing_lines(buckets, thr=None, divs=None):
    """Every recent ZigZag leg (confirmed + developing) as a line with its swing absorb-A. Each leg is split into
    N parts (N = divs[key] or 2, cycled by clicking the line) -> per-part absorb-A. CAUSAL: each part is z-scored
    against the PRIOR legs split the SAME way. Returns OLDEST->NEWEST:
      [{b0,p0,b1,p1, ends_high, developing, key, b0_time, N, dots:[(bar,price)], segs:[A_1..A_N], A, dP, dV}]."""
    r = _dev_leg(buckets, thr)
    if not r:
        return []
    H, L, C, thr, piv, dev = r
    n = len(buckets)
    geo = []                                            # OLDEST -> NEWEST so the trailing baseline is prior legs
    for k in range(1, len(piv)):
        a = piv[k - 1]; b = piv[k]
        if b[0] > a[0]:
            geo.append((a[0], a[1], b[0], b[1], b[2], False))
    if dev is not None and dev[2] > dev[0] and dev[1] > 0 and abs(dev[3] - dev[1]) / dev[1] >= thr * 0.5:
        geo.append((dev[0], dev[1], dev[2], dev[3], dev[4], True))   # developing leg = the live, newest one
    geo = geo[-SCAN_LEGS:]
    if not geo:
        return []
    dlt = [float(b.get("buy_vol", 0.0) or 0.0) - float(b.get("sell_vol", 0.0) or 0.0) for b in buckets]
    cum = [0.0] * (n + 1)
    for i in range(n):
        cum[i + 1] = cum[i] + dlt[i]
    divs = divs or {}
    seg_by_N = {}                                       # N -> [ (pairs, splitbars, N_used) | None ] per leg
    for N in (1, 2, 3, 4):
        seg_by_N[N] = [(_leg_segments(cum, C, g[0], g[1], g[2], g[3], N) if g[2] > g[0] and g[1] > 0 else None)
                       for g in geo]
    out = []
    for m, g in enumerate(geo):
        if seg_by_N[1][m] is None:
            continue
        b0, p0, b1, p1, eh, dv = g
        key = _leg_key(buckets, b0, p0)
        reqN = min(4, max(2, int(divs.get(key, 2))))
        pairs, splitbars, aN = seg_by_N[reqN][m] or seg_by_N[2][m] or seg_by_N[1][m]
        prior = range(max(0, m - SL_WLEGS), m)
        wpair = seg_by_N[1][m][0][0]                    # whole-leg (dP, dV)
        wbase = [seg_by_N[1][j][0][0] for j in prior if seg_by_N[1][j] is not None]
        segs = []
        for k in range(aN):
            base = [seg_by_N[aN][j][0][k] for j in prior
                    if seg_by_N[aN][j] is not None and k < len(seg_by_N[aN][j][0])]
            segs.append(_swing_A(base, pairs[k]))
        dots = []
        for sb in splitbars:
            frac = (sb - b0) / (b1 - b0) if b1 > b0 else 0.0
            dots.append((sb, p0 + (p1 - p0) * frac))
        out.append(dict(b0=b0, p0=p0, b1=b1, p1=p1, ends_high=eh, developing=dv, key=key,
                        b0_time=float(buckets[b0].get("start_time", 0.0) or 0.0) if 0 <= b0 < n else 0.0,
                        N=aN, dots=dots, segs=segs, A=_swing_A(wbase, wpair), dP=wpair[0], dV=wpair[1]))
    return out
