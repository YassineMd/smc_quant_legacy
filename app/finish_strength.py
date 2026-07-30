"""STRONG-FINISH test for a 5m signal candle, from its 1m constituents.

Validated in the 1m-internal study (study/breakout_1m_internal + breakout_1m_tradeable): the way a 5m break/engulf
FINISHES on the 1m tape predicts follow-through — a candle still being pushed at its close (last 1m bar closing at
the signal-side extreme, aligned, thrust sustained not front-loaded) follows through far more often (56% vs 42% for
breaks) than one that fades/exhausts. It cleared the null-calibration + split-half gates that killed every prior
sub-candle scan. This is a CONTEXT/tier signal (real, monotonic, stable), not a standalone tradeable edge — the
0.08% fee still dominates a ~0.22% ATR move.

Rule (2 of 3, computed at the signal's close, causal): the LAST 1m bar (a) closes in the signal direction,
(b) closes in the signal-side half of its range, and (c) the aligned delta is NOT front-loaded (>=~half accrues in
the back half of the bucket). side = +1 up-break / long, -1 down-break / short.

strong_finish(sub1m, side, min_bars=3) -> True (strong) / False (weak) / None (too few 1m bars to judge).
"""
from __future__ import annotations

CLOC_MIN = 0.10       # last 1m bar closes at least this far past mid toward the signal side
FRONT_MAX = 0.55      # aligned-delta share by the bucket midpoint must be <= this (thrust sustained, not spent early)
MIN_BARS = 3
STRONG_BODY = 0.55    # body/range >= this = a "strong" (body-dominant) 1m bar
NOWICK = 0.06         # wick/range <= this = "no wick" on that side
EFF_MIN = 0.80        # Kaufman efficiency ratio of the 1m CLOSE path >= this = a clean directional push (ring feature)


def _efficiency(cl) -> "float | None":
    """Kaufman efficiency ratio of a 1m CLOSE path: |net move| / gross path, in [0,1]. 1.0 = perfectly
    straight, ~0 = choppy. Direction-agnostic. None if < 3 closes or a flat path. (Mirrors terminal._er_from_subs.)"""
    if not cl or len(cl) < 3:
        return None
    gross = 0.0
    for k in range(1, len(cl)):
        gross += abs(cl[k] - cl[k - 1])
    if gross <= 1e-12:
        return None
    return abs(cl[-1] - cl[0]) / gross


def _ohlc(b):
    o = float(b.get("open", b.get("open_price", 0.0)) or 0.0)
    c = float(b.get("close", b.get("close_price", 0.0)) or 0.0)
    h = float(b.get("high", 0.0) or 0.0)
    l = float(b.get("low", 0.0) or 0.0)
    return o, c, h, l


def strong_finish(sub1m, side, min_bars=MIN_BARS):
    if not sub1m or len(sub1m) < min_bars or side == 0:
        return None
    o = c = h = l = 0.0
    cum = 0.0; cums = []
    for b in sub1m:
        o, c, h, l = _ohlc(b)                                        # keeps the LAST bar's OHLC after the loop
        d = (float(b.get("buy_vol", 0.0) or 0.0) - float(b.get("sell_vol", 0.0) or 0.0)) * side
        cum += d; cums.append(cum)
    m = len(cums); lr = max(h - l, 1e-9)
    last_aln = (c > o) if side > 0 else (c < o)                      # (a) last 1m bar closes with the signal
    cloc_ok = ((c - l) / lr - 0.5) * side >= CLOC_MIN                # (b) ...at the signal-side extreme of its range
    net = cums[-1]                                                   # (c) aligned delta not front-loaded
    front_ok = (net > 1e-9) and (cums[m // 2 - 1] / net <= FRONT_MAX)
    return (int(last_aln) + int(cloc_ok) + int(front_ok)) >= 2


def ring_tier(sub1m, side, min_bars=MIN_BARS):
    """RING tier from the 1m constituents (side +1 long / -1 short). 0 = no ring, 1 = red/green ring, 2 = gold ring.
    ALL tiers require the signal to END with a STRONG WITH-POSITION 1m bar.
    Red/green = that base + ANY of: a with-position ENGULFING 1m bar, OR the LAST bar has NO trend-side wick
      (no top wick long / no bottom wick short), OR the 1m path efficiency (Kaufman ER) >= EFF_MIN (0.80).
    Gold = that base + ANY of: ALL 1m bars with-position, OR a full-marubozu with-trend LAST bar, OR ALL THREE
      red/green features together (engulfing AND last-no-wick AND efficient)."""
    if not sub1m or len(sub1m) < min_bars or side == 0:
        return 0
    o = []; c = []; h = []; l = []
    for b in sub1m:
        oo, cc, hh, ll = _ohlc(b); o.append(oo); c.append(cc); h.append(hh); l.append(ll)
    m = len(o)

    def dirn(j): return 1 if c[j] > o[j] else (-1 if c[j] < o[j] else 0)
    def rng(j): return max(h[j] - l[j], 1e-9)
    def with_pos(j): return dirn(j) == side
    def strong(j): return abs(c[j] - o[j]) / rng(j) >= STRONG_BODY
    def upper(j): return (h[j] - max(o[j], c[j])) / rng(j)
    def lower(j): return (min(o[j], c[j]) - l[j]) / rng(j)
    def no_trend_wick(j): return (upper(j) <= NOWICK) if side > 0 else (lower(j) <= NOWICK)

    last = m - 1
    if not (with_pos(last) and strong(last)):                        # BASE: ends with a strong with-position bar
        return 0
    eng = False
    for j in range(1, m):                                           # (a) a with-position ENGULFING 1m bar (any bar)
        if not with_pos(j):
            continue
        plo = min(o[j - 1], c[j - 1]); phi = max(o[j - 1], c[j - 1])
        if (side > 0 and o[j] <= plo and c[j] >= phi) or (side < 0 and o[j] >= phi and c[j] <= plo):
            eng = True; break
    nowick = no_trend_wick(last)                                    # (b) the LAST bar (already strong+with-pos) has no trend-side wick
    _e = _efficiency(c); eff_ok = _e is not None and _e >= EFF_MIN  # (c) the 1m close path is efficient (ER >= 0.80)
    all_pos = all(with_pos(j) for j in range(m))                    # every 1m bar with-position
    last_maru = upper(last) <= NOWICK and lower(last) <= NOWICK     # last = full marubozu (base guarantees with-trend)
    if all_pos or last_maru or (eng and nowick and eff_ok):         # GOLD: all-pos OR last-marubozu OR all-three red/green features
        return 2
    if eng or nowick or eff_ok:                                    # RED/GREEN: engulfing OR last-no-wick OR efficient path
        return 1
    return 0
