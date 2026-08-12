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
