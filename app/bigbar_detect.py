"""BIG BAR (Time candles) — ꕻ on candles whose BODY' (close-to-adverse-extreme; see below) is in the top
quintile (P80, configurable) of the last FOUR finished EMA-trend segments (user 2026-08-30; body' v3 2026-08-31).

The reference window is EXACTLY the one the Expensive/Equilibrium/Cheap bands use: the 2 preceding up + down
moves and the pair before them — i.e. the last 2 finished BULL and last 2 finished BEAR segments of the
EMA20/50 Stack-Flip walk (cross -> cross), with the same qualification rules the terminal's flip lines use
(cross + regime >= 20 bars old + HL-delta validation on both windows + same-colour dedupe).

BODY definition (user 2026-08-31, v3): the ORIGIN-side wick belongs to the body — the move is measured from
the adverse extreme to the close (the same ref convention as Mov.Magnitude):
    bullish (close > open):  body' = close - low    (body + lower wick)
    bearish (close < open):  body' = high - close   (body + upper wick)
Flat candles (close == open) have no direction -> never judged, never in the reference population.

BIG = body' STRICTLY ABOVE the P`pctl` (default P80 by rank) of the body' of every directional candle inside
the 4 reference segments, AND the CLOSE-side wick (the rejection beyond the close) <= `wick_max` (default 30%)
of body':
    bullish: upper wick (high - close) <= wick_max * body'
    bearish: lower wick (close - low)  <= wick_max * body'
A candle whose close was pushed back by more than 30% of its own move is not a conviction bar. Rank-based on purpose: the first cut used "top third of the size RANGE" — the literal
E/E/C mechanism — but candle sizes are heavily right-skewed, so ONE monster stretched the range and pushed
the threshold to P96-99 of actual sizes (~1% printed). A percentile is immune to that outlier; the comparison
is STRICT so a degenerate all-equal-body window marks nothing.

CAUSAL BY CONSTRUCTION: a finished segment enters the reference set only at the bar its CLOSING flip is
CONFIRMED (>= 19 bars after the cross), so at any bar the threshold uses only segments that were already
known — a mark never repaints when a later trend completes. Candles printed before 2+2 segments are known are
never marked. The reference candles themselves are never self-judged (their segments close before the
threshold exists).

detect(candles, skip_last=True, pctl=80.0, wick_max=0.30) -> [{i, side, size(body'), thr}]
Pure OHLC in / marks out — no Qt, reusable by studies.
"""
from __future__ import annotations


def _f(x) -> float:
    try:
        return float(x or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _pctl(sorted_vals: list, p: float) -> float:
    """Nearest-rank percentile of an ASCENDING list (no interpolation — exact, test-mirrored)."""
    import math
    n = len(sorted_vals)
    k = max(0, min(n - 1, int(math.ceil(p / 100.0 * n)) - 1))
    return sorted_vals[k]


def detect(candles: list, skip_last: bool = True, pctl: float = 80.0,
           wick_max: float = 0.30) -> "list[dict]":
    n = len(candles)
    if n < 120:
        return []
    O = [0.0] * n; C = [0.0] * n; H = [0.0] * n; L = [0.0] * n
    for i, b in enumerate(candles):
        O[i] = _f(b.get("open")); C[i] = _f(b.get("close", b.get("close_price")))
        H[i] = _f(b.get("high")); L[i] = _f(b.get("low"))

    # EMA20/50, seeded on the first close — identical to the terminal's Stack-Flip series
    E = {}
    for p in (20, 50):
        a = 2.0 / (p + 1.0); y = [0.0] * n; prev = C[0]; y[0] = prev
        for i in range(1, n):
            c = C[i] or prev
            prev = y[i] = a * c + (1.0 - a) * prev
        E[p] = y
    e20, e50 = E[20], E[50]
    bull = [e20[i] > e50[i] for i in range(n)]
    bear = [e50[i] > e20[i] for i in range(n)]

    def hl_delta(bi, p, Ey):
        hp = hi2 = lp = li2 = None
        for k in range(max(0, bi - p + 1), bi + 1):
            h2 = H[k]; l2 = L[k]
            if h2 > 0 and (hp is None or h2 >= hp):
                hp = h2; hi2 = k
            if l2 > 0 and (lp is None or l2 <= lp):
                lp = l2; li2 = k
        if hp is None or lp is None or Ey[hi2] <= 0 or Ey[li2] <= 0:
            return None
        return (hp - Ey[hi2]) / Ey[hi2] + (lp - Ey[li2]) / Ey[li2]

    def valid(bi, up):
        d20 = hl_delta(bi, 20, e20); d50 = hl_delta(bi, 50, e50)
        if d20 is None or d50 is None:
            return False
        return (d20 > 0.0 and d50 > 0.0) if up else (d20 < 0.0 and d50 < 0.0)

    # the Stack-Flip walk: flips anchor at their CROSS bar; a finished segment (previous flip -> this flip)
    # becomes KNOWN at the bar this flip CONFIRMS.
    flips = []                      # (cross_bar, colour 'g'|'r')
    events = []                     # (known_bar, span_b0, span_b1, kind 'g' bull | 'r' bear)
    state = "g" if bull[50] else ("r" if bear[50] else None)
    pend = state is not None
    reg0 = 50 if pend else -1
    last_col = None
    for i in range(50, n):
        if bull[i] and not bull[i - 1]:
            state, pend, reg0 = "g", True, i
        elif bear[i] and not bear[i - 1]:
            state, pend, reg0 = "r", True, i
        if pend and i - reg0 < 19:
            continue                                     # min-age: regime must be >= 20 bars old
        if pend and state == "g" and bull[i] and valid(i, True):
            if last_col != "g":
                if flips:
                    events.append((i, flips[-1][0], reg0, flips[-1][1]))
                flips.append((reg0, "g")); last_col = "g"
            pend = False
        elif pend and state == "r" and bear[i] and valid(i, False):
            if last_col != "r":
                if flips:
                    events.append((i, flips[-1][0], reg0, flips[-1][1]))
                flips.append((reg0, "r")); last_col = "r"
            pend = False

    # causal judging pass: consume segment-known events in bar order; threshold = P`pctl` of the candle sizes
    # over the last 2 bull + 2 bear known segments (recomputed only when the reference set changes).
    out = []; ei = 0; bulls = []; bears = []; thr = None; dirty = False
    hi_n = (n - 1) if skip_last else n
    for j in range(50, hi_n):
        while ei < len(events) and events[ei][0] <= j:
            _kb, b0, b1, kd = events[ei]; ei += 1
            (bulls if kd == "g" else bears).append((b0, b1)); dirty = True
        if dirty and len(bulls) >= 2 and len(bears) >= 2:
            idxs = set()
            for (b0, b1) in bulls[-2:] + bears[-2:]:
                idxs.update(range(max(0, b0), min(n, b1 + 1)))
            szs = sorted((C[k] - L[k]) if C[k] > O[k] else (H[k] - C[k])          # body' = close to the
                         for k in idxs                                            # ADVERSE extreme (origin
                         if C[k] > 0 and O[k] > 0 and H[k] > 0 and L[k] > 0       # wick counts as body)
                         and C[k] != O[k])                                        # flats have no direction
            thr = _pctl(szs, pctl) if len(szs) >= 20 else None    # rank-based: outlier-immune
            dirty = False
        if thr is None or C[j] <= 0 or O[j] <= 0 or H[j] <= 0 or L[j] <= 0 or C[j] == O[j]:
            continue                                     # flats have no direction -> never judged
        if C[j] > O[j]:                                  # bullish: body' = close - low (lower wick IS body)
            sz = C[j] - L[j]
            wick = max(0.0, H[j] - C[j])                 # the CLOSE-side rejection (upper wick)
            side = 1
        else:                                            # bearish: body' = high - close (upper wick IS body)
            sz = H[j] - C[j]
            wick = max(0.0, C[j] - L[j])                 # the CLOSE-side rejection (lower wick)
            side = -1
        if sz <= 0 or wick > wick_max * sz:
            continue                                     # rejected: the close was pushed back by > 30% of the move
        if sz > thr:                                     # STRICT: an all-equal window marks nothing
            out.append(dict(i=j, side=side, size=sz, thr=thr))
    return out
