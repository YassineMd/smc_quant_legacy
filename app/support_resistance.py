"""SUPPORT & RESISTANCE horizontal levels for the terminal overlay, with STRENGTH-GRADED thickness.

A fractal PIVOT HIGH (its high strictly greater than the K candles on each side) becomes a RESISTANCE at that
high; a fractal PIVOT LOW becomes a SUPPORT at that low. Each level then EXTENDS to the right as new candles
form and STOPS only when a candle CLOSES through it:
    RESISTANCE stops at the first later candle whose close is ABOVE the level.
    SUPPORT    stops at the first later candle whose close is BELOW the level.
An unbroken level keeps extending to the live edge (i1 = None -> caller draws to the last bar).

STRENGTH = how far price is REJECTED by the level. Each time price TOUCHES the level (comes within
SR_TOUCH_TOL) and is thrown back the other way, the throw-back distance is measured; the level's running
strength is the LARGEST such rejection seen so far, as a percent of price. It is CAUSAL and MONOTONIC — it
only uses bars up to the current one and can only grow — so the line gets THICKER as price develops and
FREEZES the moment a close breaks the level (the develop loop ends at the break bar). A level price never comes
back to test stays at the thinnest tier. Strength is quantised into len(SR_TIER_WIDTHS) width tiers by
SR_STRENGTH_EDGES (percent-of-price cut points, calibrated on 2026 SOL 1h; higher timeframes skew thicker).

detect(buckets, k=SR_PIVOT_K) ->
    [{"kind": "R"|"S", "price": float, "i0": pivot_index, "i1": break_index|None,
      "strength": float(%), "tier": int, "segs": [(x0, x1, tier), ...]}]
where `segs` tiles [i0 .. break/live-edge] into contiguous runs of constant width tier (thin near the pivot,
stepping thicker at each new deepest rejection) so the caller can draw one curve per tier.
"""
from __future__ import annotations

SR_PIVOT_K = 8          # fractal lookback each side; higher = fewer, more significant levels
SR_MAX_LEVELS = 16      # keep only the most-recent N (by pivot index) so a wide window stays uncluttered

SR_TOUCH_TOL = 0.0005                       # price within 0.05% of the line counts as a touch (starts a rejection)
SR_STRENGTH_EDGES = (0.35, 0.95, 2.60, 5.40)  # % of price; strength > edge -> next tier. 5 tiers total.
SR_TIER_WIDTHS = (1.5, 2.5, 3.6, 5.0, 6.8)    # cosmetic line width (px) per tier, thin -> thick


def _ohlc(b):
    return (float(b.get("open", b.get("open_price", 0.0)) or 0.0),
            float(b.get("high", 0.0) or 0.0),
            float(b.get("low", 0.0) or 0.0),
            float(b.get("close", b.get("close_price", 0.0)) or 0.0))


def strength_tier(pct: float) -> int:
    """Width tier for a rejection strength (percent of price). SR_STRENGTH_EDGES are increasing cut points."""
    t = 0
    for e in SR_STRENGTH_EDGES:
        if pct > e:
            t += 1
        else:
            break
    return t


def detect(buckets: list, k: int = SR_PIVOT_K) -> "list[dict]":
    n = len(buckets)
    if n < 2 * k + 1:
        return []
    H = [0.0] * n; L = [0.0] * n; C = [0.0] * n; ok = [False] * n
    for i, b in enumerate(buckets):
        _o, h, l, c = _ohlc(b)
        H[i] = h; L[i] = l; C[i] = c
        ok[i] = h > 0 and l > 0 and c > 0 and h >= l
    out = []
    for p in range(k, n - k):
        if not ok[p]:
            continue
        hp = H[p]
        if (all(ok[j] and hp > H[j] for j in range(p - k, p))
                and all(ok[j] and hp > H[j] for j in range(p + 1, p + k + 1))):
            i1 = None
            for j in range(p + 1, n):
                if ok[j] and C[j] > hp:
                    i1 = j; break
            out.append(_level("R", hp, p, i1, n, H, L, ok))
        lp = L[p]
        if (all(ok[j] and lp < L[j] for j in range(p - k, p))
                and all(ok[j] and lp < L[j] for j in range(p + 1, p + k + 1))):
            i1 = None
            for j in range(p + 1, n):
                if ok[j] and C[j] < lp:
                    i1 = j; break
            out.append(_level("S", lp, p, i1, n, H, L, ok))
    return out


def _level(kind, price, p, i1, n, H, L, ok) -> dict:
    """Build one level dict, walking bars p+1 .. (break|end) to accrue the causal, monotonic rejection strength
    and tile the line into constant-tier segments."""
    end = i1 if i1 is not None else n            # develop loop is exclusive of the break bar
    x_end = i1 if i1 is not None else (n - 1)     # the line is drawn from p to here
    strength = 0.0
    touched = False
    extreme = 0.0
    cur_tier = 0
    seg_start = p
    segs = []
    for j in range(p + 1, end):
        if not ok[j]:
            continue
        if kind == "R":
            if H[j] >= price * (1.0 - SR_TOUCH_TOL):     # rallied up to resistance -> (re)touch, reset the throw
                touched = True; extreme = L[j]
            elif touched and L[j] < extreme:
                extreme = L[j]                           # price falling away -> rejection deepens
            if touched:
                d = (price - extreme) / price * 100.0
                if d > strength:
                    strength = d
        else:
            if L[j] <= price * (1.0 + SR_TOUCH_TOL):     # fell to support -> (re)touch
                touched = True; extreme = H[j]
            elif touched and H[j] > extreme:
                extreme = H[j]
            if touched:
                d = (extreme - price) / price * 100.0
                if d > strength:
                    strength = d
        t = strength_tier(strength)
        if t != cur_tier:                                # tier stepped up at bar j -> close the run here
            segs.append((seg_start, j, cur_tier))
            seg_start = j; cur_tier = t
    if x_end > seg_start:
        segs.append((seg_start, x_end, cur_tier))
    return {"kind": kind, "price": price, "i0": p, "i1": i1,
            "strength": strength, "tier": strength_tier(strength), "segs": segs}
