# -*- coding: utf-8 -*-
"""REWARD-PER-EFFORT — who is getting PAID for their aggression vs who is being ABSORBED.

Over any window of buckets:
  * EFFORT of a side = its taker VOLUME (buy_vol for buyers, sell_vol for sellers).
  * REWARD of a side = the price movement that went its way (sum of up-moves credited to buyers,
    down-moves to sellers), as a fraction of price.
  * reward-per-effort (rpe) = reward / effort — how much price a side moved per unit of its aggression.

`share()` returns the BUY side's % of the total reward-per-effort. buy>50% => buyers are being rewarded
(sellers absorbed); <50% => the mirror; 50/50 on a flat / no-volume window. DESCRIPTIVE / COINCIDENT —
it reads who is winning the exchange as it happens, NOT a forecast (no forward edge: radar_hold_causal.py).
"""
from __future__ import annotations


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def pools(buckets, i0, i1):
    """Accumulate (reward_buy, reward_sell, effort_buy, effort_sell) over buckets[i0..i1] inclusive."""
    n = len(buckets)
    rwb = rws = eb = es = 0.0
    for k in range(max(0, i0), min(n - 1, i1) + 1):
        b = buckets[k]
        o = _f(b.get("open", b.get("open_price", 0.0)))
        c = _f(b.get("close", b.get("close_price", 0.0)))
        if o > 0:
            dp = (c - o) / o
            if dp > 0:
                rwb += dp
            elif dp < 0:
                rws += -dp
        eb += _f(b.get("buy_vol", 0.0))
        es += _f(b.get("sell_vol", 0.0))
    return rwb, rws, eb, es


def share(buckets, i0, i1):
    """BUY-side share (0..100) of reward-per-effort over buckets[i0..i1] inclusive.
    Returns (buy_share, has_data); has_data=False (buy_share=50.0) when the window is flat / has no volume."""
    if not buckets or i1 < i0:
        return 50.0, False
    rwb, rws, eb, es = pools(buckets, i0, i1)
    rpeb = (rwb / eb) if eb > 0 else 0.0
    rpes = (rws / es) if es > 0 else 0.0
    t = rpeb + rpes
    if t <= 0:
        return 50.0, False
    return 100.0 * rpeb / t, True


def _median(vals) -> float:
    m = len(vals)
    if m == 0:
        return 0.0
    s = sorted(vals)
    return s[m // 2] if (m % 2) else 0.5 * (s[m // 2 - 1] + s[m // 2])


# STRENGTH = give each side an ABSOLUTE score the reward/eff SHARE throws away (the share is a ratio -> 70/30 reads the
# same for a massive fought push or a thin drift). Two independent axes per side:
#   EFFORT (participation) = robust z-score of the side's mean per-candle taker volume vs a trailing baseline.
#   EFFICIENCY (conviction) = its reward-per-effort LEAN (the share; >50 = that side is the efficient one).
# Crossing them gives the tape's quadrant: DOMINANT (efficient + forceful), ABSORBED (forceful but out-efficiency'd =
# pushing hard, being denied at a level), EASY (efficient on little effort = unopposed drift), QUIET (neither). All
# still built from open->close body returns + taker volume, so DESCRIPTIVE / COINCIDENT like share() — a readout.
STR_BASE_WIN = 300       # trailing candles for the per-candle volume baseline (robust median + MAD)
STR_EFFORT_HI = 0.5      # effort z-score at/above this = "high participation" (forceful) side


def _elapsed(b):
    """Bucket fill time in seconds (end - start); 0 when unknown (e.g. a live forming bucket)."""
    d = _f(b.get("end_time", 0.0)) - _f(b.get("start_time", 0.0))
    return d if d > 0 else 0.0


def _robust(vals):
    if len(vals) < 8:
        return None
    m = _median(vals); s = 1.4826 * _median([abs(v - m) for v in vals])
    return (m, s) if s > 0 else None


def strength_baseline(buckets, i1, win=STR_BASE_WIN):
    """Robust (median, 1.4826*MAD) baselines over the `win` candles ending at i1 for BOTH per-candle single-side taker
    VOLUME (size) and RATE = volume/elapsed (speed). Returns {'vol':(m,s)|None, 'rate':(m,s)|None} (both sides pooled
    -> one common scale). Compute ONCE and pass to strength(..., base=) to score many windows against one recent norm."""
    n = len(buckets)
    if not buckets:
        return None
    i1 = min(n - 1, i1); b0 = max(0, i1 - win + 1)
    vols = []; rates = []
    for k in range(b0, i1 + 1):
        b = buckets[k]; bv = _f(b.get("buy_vol", 0.0)); sv = _f(b.get("sell_vol", 0.0)); el = _elapsed(b)
        if bv > 0:
            vols.append(bv)
            if el > 0:
                rates.append(bv / el)
        if sv > 0:
            vols.append(sv)
            if el > 0:
                rates.append(sv / el)
    return {"vol": _robust(vols), "rate": _robust(rates)}


def volume_baseline(buckets, i1, win=STR_BASE_WIN):        # back-compat: just the VOLUME norm
    b = strength_baseline(buckets, i1, win)
    return b["vol"] if b else None


def strength(buckets, i0, i1, base_i0=None, base=None):
    """Per-side STRENGTH over buckets[i0..i1]. Each side scored on two axes then combined:
      SIZE  = z-score of mean per-candle taker VOLUME vs baseline (how much aggression).
      SPEED = z-score of the taker RATE (volume/elapsed) vs baseline (fill velocity — a strong side fills FAST).
      EFFORT (displayed) = max(size, speed): a side is FORCEFUL if it pushes hard by size OR by speed.
    Crossed with the reward/eff EFFICIENCY lean -> quadrant (dominant/absorbed/easy/quiet). TEMPO tags the lead side
    'impulsive' (speed-led) vs 'grind' (size-led, slow) when forceful. `base` = a strength_baseline() dict shared
    across rows; else computed from base_i0..i1 (default trailing STR_BASE_WIN). Returns {'ok','share','lead','tempo',
    'buy':{'effort_z','size_z','speed_z','rpe','eff_share','quad'}, 'sell':{...}}."""
    n = len(buckets)
    _z = {"effort_z": 0.0, "size_z": 0.0, "speed_z": 0.0, "rpe": 0.0, "eff_share": 50.0, "quad": "quiet"}
    out = {"ok": False, "share": 50.0, "lead": None, "tempo": "", "buy": dict(_z), "sell": dict(_z)}
    if not buckets or i1 < i0:
        return out
    i0 = max(0, i0); i1 = min(n - 1, i1); m = i1 - i0 + 1
    if m <= 0:
        return out
    if base is None:
        base = strength_baseline(buckets, i1, (i1 - base_i0 + 1) if base_i0 is not None else STR_BASE_WIN)
    if not base or base.get("vol") is None:
        return out
    medv, sigv = base["vol"]
    rwb, rws, eb, es = pools(buckets, i0, i1)
    ez_b = ((eb / m) - medv) / sigv; ez_s = ((es / m) - medv) / sigv        # SIZE z (volume level)
    el_tot = 0.0
    for k in range(i0, i1 + 1):
        el_tot += _elapsed(buckets[k])
    rz_b = rz_s = 0.0                                                        # SPEED z (taker rate = vol/elapsed)
    if base.get("rate") is not None and el_tot > 0:
        medr, sigr = base["rate"]
        rz_b = ((eb / el_tot) - medr) / sigr; rz_s = ((es / el_tot) - medr) / sigr
    pz_b = max(ez_b, rz_b); pz_s = max(ez_s, rz_s)                           # EFFORT = forceful by size OR speed
    rpeb = (rwb / eb) if eb > 0 else 0.0; rpes = (rws / es) if es > 0 else 0.0
    t = rpeb + rpes
    if t <= 0:
        return out
    share = 100.0 * rpeb / t; eff_buy = rpeb >= rpes

    def quad(is_eff, pz):
        hi = pz >= STR_EFFORT_HI
        return ("dominant" if hi else "easy") if is_eff else ("absorbed" if hi else "quiet")

    out["ok"] = True; out["share"] = share; out["lead"] = "buy" if eff_buy else "sell"
    out["buy"] = {"effort_z": pz_b, "size_z": ez_b, "speed_z": rz_b, "rpe": rpeb,
                  "eff_share": share, "quad": quad(eff_buy, pz_b)}
    out["sell"] = {"effort_z": pz_s, "size_z": ez_s, "speed_z": rz_s, "rpe": rpes,
                   "eff_share": 100.0 - share, "quad": quad(not eff_buy, pz_s)}
    ld = out["buy"] if eff_buy else out["sell"]                              # TEMPO of the winning side (when forceful)
    if ld["effort_z"] >= STR_EFFORT_HI:
        out["tempo"] = "impulsive" if ld["speed_z"] >= ld["size_z"] else "grind"
    return out


# Reward-side FLIP detection defaults. WIN matches the stats-box "Reward/eff" rolling window so the flip marks line
# up with when that readout crosses. LO/HI are a hysteresis band around 50 so a flip is a genuine hand-over, not
# chatter: buyers take over only once the share climbs to >= HI, sellers only once it falls to <= LO.
SWITCH_WIN = 20
SWITCH_LO = 45.0
SWITCH_HI = 55.0


def switches(buckets, win=SWITCH_WIN, lo=SWITCH_LO, hi=SWITCH_HI):
    """Bucket indices where the rolling reward-per-effort BUY share (over `win` buckets) FLIPS the rewarded side,
    with hysteresis. Returns [(i, side, strength), ...] in order: side 'buy' = the candle where BUYERS take over
    from a prior seller regime (sellers stopped being rewarded, buyers started); 'sell' = the mirror. `strength` =
    2*|50 - extreme| (0..100) where `extreme` is the MOST one-sided reward-per-effort share the regime BEING REVERSED
    reached — i.e. how strongly the opposite side was established before this hand-over. A switch that overturns a
    deeply one-sided regime scores high; one that flips a near-balanced tape scores low. The FIRST regime the window
    settles into is NOT emitted (no prior side to flip from). O(n) sliding window. DESCRIPTIVE / COINCIDENT."""
    n = len(buckets)
    if n == 0:
        return []
    up = [0.0] * n; dn = [0.0] * n; bv = [0.0] * n; sv = [0.0] * n
    for k in range(n):
        b = buckets[k]
        o = _f(b.get("open", b.get("open_price", 0.0)))
        c = _f(b.get("close", b.get("close_price", 0.0)))
        if o > 0:
            dp = (c - o) / o
            if dp > 0:
                up[k] = dp
            elif dp < 0:
                dn[k] = -dp
        bv[k] = _f(b.get("buy_vol", 0.0)); sv[k] = _f(b.get("sell_vol", 0.0))
    out = []; state = None
    su = sd = eb = es = 0.0
    extreme = 50.0                                           # most one-sided share reached in the CURRENT regime
    for i in range(n):
        su += up[i]; sd += dn[i]; eb += bv[i]; es += sv[i]
        j = i - win
        if j >= 0:
            su -= up[j]; sd -= dn[j]; eb -= bv[j]; es -= sv[j]
        rpeb = (su / eb) if eb > 0 else 0.0
        rpes = (sd / es) if es > 0 else 0.0
        t = rpeb + rpes
        if t <= 0:
            continue
        s = 100.0 * rpeb / t
        if state == "sell":
            extreme = min(extreme, s)                       # deepest seller dominance so far
        elif state == "buy":
            extreme = max(extreme, s)                       # strongest buyer dominance so far
        strength = min(100.0, 2.0 * abs(50.0 - extreme))    # depth of the regime being reversed (0..100)
        if s >= hi and state != "buy":
            if state is not None:
                out.append((i, "buy", strength))
            state = "buy"; extreme = s                       # start tracking the new regime from here
        elif s <= lo and state != "sell":
            if state is not None:
                out.append((i, "sell", strength))
            state = "sell"; extreme = s
    return out
