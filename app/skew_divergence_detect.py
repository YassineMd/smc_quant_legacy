"""SKEW DIVERGENCE overlay detector for the LIVE terminal (1h) — EXPLORATORY, not a frozen candidate.

Two consecutive candles moving one way while the bucket's volume PROFILE leans the other way: the close ran
away from where volume actually traded, so fade it.

    LONG   (green up-triangle 'L'):  candle i-1 AND candle i both BEARISH, and candle i's profile skew >= +0.5
                                     ("high" = volume mass at the HIGHER prices, thin tail reaching down).
    SHORT  (red down-triangle 'S'):  candle i-1 AND candle i both BULLISH, and candle i's profile skew <= -0.5
                                     ("low"  = volume mass at the LOWER prices,  thin tail reaching up).

    entry : candle i close.  exit : fixed 0.8% stop / 0.8% target (1:1), same as the study.

WHY IT IS HERE. In-sample on 2026 SOL 1h it is the only shape that showed a monotone skew gradient across
DISJOINT bands (bear pairs 42/50/60% low->high skew; bull pairs 56/51/36% low->high) and a positive residual
over entry-displacement on the short side. Pooled n=51, 60.8% win, shuffled-skew null p=0.069 — NOT
significant, NOT frozen, NOT tradeable. The badges exist so the setups can be eyeballed on the chart; the
+/-0.5 threshold is `skew_read()`'s own "high"/"low" cut, not a fitted one.

NO WARM-UP. Unlike da2/MMXSKEW nothing here is a running causal computation: profile skew is per-bucket from
`levels`, and the only cross-bucket input is the PRIOR candle's direction (i-1). So detect() needs no prefix.

CLOSED-ONLY (skip_last, default True): the terminal appends the still-forming bucket; its `levels` and close
keep moving, so its skew would repaint. Pass skip_last=False only for a closed-buckets-only list (replay).

DOM FILTER (forward-test candidate, 2026-07-23 — NOT in-sample-significant). Split the entry candle at its
price midpoint (high+low)/2 and read the EXPECTED aggressor's share of the EXTREME half: for a LONG the lower
half's SELL share (buyers absent at the lows), for a SHORT the upper half's BUY share (sellers absent at the
highs). `dom` ~ 1 means the counter-side has walked away there. In-sample the fades with dom <= 0.55 LOSE
(37.5% win, n=8) while dom > 0.55 win 65.1% (n=43) — a +27.6pp gap, but p=0.14 on an n=8 reject cell from a
mined threshold, so it is a HIGHLIGHT to study forward, not a gate: detect() still returns every setup, tagged
`pass_dom` (dom > DOM_MIN). The terminal draws pass_dom solid / fail hollow so both are visible on the tape.

CLIMAX-CLOSE FILTER (2nd forward-test filter, 2026-07-23 — also NOT in-sample-significant). The entry candle
closes HARD with the prior move — a LONG closes in the BOTTOM third of its range, a SHORT in the TOP third
(`ca` = 1-(c-l)/(h-l) for a long / (c-l)/(h-l) for a short; pass_climax = ca >= CLIMAX_MIN). An exhaustion tell.
On the dom>0.55 base (n=44) climax-third fades win 68.6% (n=35) vs 44.4% (n=9) NOT — +24.1pp, p=0.17; it is
SEMI-INDEPENDENT of dom (catches different rejects). Study forward, do not re-tune the thresholds.

R2-VACUUM FILTER (3rd forward-test filter, 2026-07-23 — user decision, WEAK evidence). Per-half absorption
(`absorption.absorption_halves`, A>0 = that half's aggressor ABSORBED): the fade wants the SECOND half NOT more
absorbed than the first — a thin-book VACUUM, not support forming. pass_absorb = dA (= A_h2 - A_h1) <=
ABSORB_MAX, OR dA unavailable (no price_h1 / <20 baselined priors -> NOT penalised, so it degrades to dom+climax
without the field). In-sample on the pass_full base (R1/R2 available 24/35): dA<=0 77.8% (n=18) vs dA>0 50.0%
(n=6) — the ORIGINAL "R2 MORE absorbed" hypothesis was backwards (p=0.96); this freezes the REVERSE.
⚠ n=6 vs 18, a post-hoc flip, a 3rd filter stacked on two mined ones = the WEAKEST of the three; forward tape
is the only real test.

MOVE-EXPANSION FILTER (4th forward-test filter, 2026-07-23 — user decision, WEAKEST). The whole-candle move
EXPANDS from candle 1 to candle 2 in the fade direction: a LONG wants candle 2 to be the bigger DOWN move
(dP1 > dP2), a SHORT the bigger UP move (dP1 < dP2), dP = (close-open)/open. pass_expand = side*(dP1-dP2) > 0.
Full coverage (no price_h1). On the pass_full base (n=30): pass 73.9% (n=23) vs 57.1% (n=7), +16.8pp, p=0.34 —
weakest of the four. ⚠ The cumulative funnel (Base 60.8% -> all four 75%, in-sample) is MOSTLY MECHANICAL: each
filter is chosen to drop in-sample losers, none of the four is individually significant, and n falls 51->24 on
one regime. Forward tape is the only real test.

`pass_full` = pass_dom AND pass_climax AND pass_absorb AND pass_expand (all four frozen filters). TERMINAL
RENDER: a triangle prints only when the CORE setup passes (pass_dom AND pass_climax — the two filters that
actually separate outcomes); a GOLD STAR is overlaid when pass_full (all four). A core-fail draws nothing (no
more hollow). So: star = 4-filter (higher per-trade odds, fewer signals), bare triangle = core dom+climax.

detect(buckets, skip_last=True) -> [{i, side(+1/-1), entry, sl, tp, skew, dom, pass_dom, ca, pass_climax,
    dA, pass_absorb, dp1, dp2, pass_expand, pass_full}]
"""
from __future__ import annotations

from . import absorption as _absorption   # Qt-free; per-half absorption residual for the R2-vacuum filter

SL_PCT = 0.008
TP_PCT = 0.008
SKEW_HI = 0.5            # skew_read()'s "high"/"low" boundary (app/footprint_panel.py) — NOT fitted here
DOM_MIN = 0.55          # extreme-half dominance threshold — see the DOM FILTER note above (forward-test only)
CLIMAX_MIN = 2.0 / 3.0  # aligned close must sit in the bottom⅓ (long) / top⅓ (short) — see CLIMAX-CLOSE note
ABSORB_MAX = 0.0        # dA = A_h2 - A_h1 must be <= this (2nd half NOT more absorbed) — see R2-VACUUM note


def _oc(b):
    """(open, close) — robust to wire (open/close) and persisted (open_price/close_price) bucket dicts."""
    return (float(b.get("open", b.get("open_price", 0.0)) or 0.0),
            float(b.get("close", b.get("close_price", 0.0)) or 0.0))


def profile_skew(b):
    """Volume-weighted profile skewness of a bucket's `levels`, PROFILE-READ convention (sign flipped so
    >0 = mass HIGH / tail down, <0 = mass LOW / tail up). None with <3 priced levels or no dispersion.
    A standalone copy of footprint_panel.profile_skewness so this detector stays Qt-free and study-usable."""
    pts = []
    W = 0.0
    for ps, v in (b.get("levels") or {}).items():
        try:
            p = float(ps)
        except (TypeError, ValueError):
            continue
        w = float(v.get("b", 0.0)) + float(v.get("s", 0.0))
        if w > 0.0:
            pts.append((p, w)); W += w
    if len(pts) < 3 or W <= 0.0:
        return None
    mean = sum(p * w for p, w in pts) / W
    m2 = sum(w * (p - mean) ** 2 for p, w in pts) / W
    if m2 <= 0.0:
        return None
    m3 = sum(w * (p - mean) ** 3 for p, w in pts) / W
    return -(m3 / (m2 ** 1.5))


def half_dom(b, side):
    """Side-aligned dominance of the EXPECTED aggressor in the entry candle's EXTREME half (split at (hi+lo)/2):
    LONG -> lower-half SELL share, SHORT -> upper-half BUY share. -> 1.0 means the OTHER side is nearly ABSENT
    there. None when the profile is empty or the range is degenerate."""
    lv = b.get("levels") or {}
    hi = float(b.get("high", 0.0) or 0.0); lo = float(b.get("low", 0.0) or 0.0)
    if not lv or hi <= 0.0 or lo <= 0.0 or hi < lo:
        return None
    mid = (hi + lo) / 2.0
    ub = us = lb = ls = 0.0
    for ps, v in lv.items():
        try:
            p = float(ps)
        except (TypeError, ValueError):
            continue
        bb = float(v.get("b", 0.0) or 0.0); ss = float(v.get("s", 0.0) or 0.0)
        if p >= mid:
            ub += bb; us += ss
        else:
            lb += bb; ls += ss
    tot = (lb + ls) if side > 0 else (ub + us)
    if tot <= 0.0:
        return None
    return (ls / tot) if side > 0 else (ub / tot)


def climax_close(b, side):
    """Aligned close position in the entry candle's own range: LONG -> how far the close sits toward the LOW,
    SHORT -> toward the HIGH (1.0 = closed AT that extreme). >= CLIMAX_MIN means a climax close in the bottom/top
    third — the candle closed HARD with the prior move. None on a zero-range or degenerate candle."""
    h = float(b.get("high", 0.0) or 0.0); l = float(b.get("low", 0.0) or 0.0)
    _o, c = _oc(b)
    rng = h - l
    if rng <= 0.0 or c <= 0.0:
        return None
    cp = (c - l) / rng                          # 0 = close at LOW, 1 = close at HIGH
    return (1.0 - cp) if side > 0 else cp       # aligned so high => close in the CLIMAX third


def detect(buckets: list, skip_last: bool = True) -> "list[dict]":
    n = len(buckets)
    if n < 2:
        return []
    out = []
    for i in range(1, n - 1 if skip_last else n):
        o1, c1 = _oc(buckets[i - 1])
        o2, c2 = _oc(buckets[i])
        if o1 <= 0 or c1 <= 0 or o2 <= 0 or c2 <= 0:
            continue
        sk = profile_skew(buckets[i])
        if sk is None:
            continue
        if c1 < o1 and c2 < o2 and sk >= SKEW_HI:
            s = 1                                    # bearish pair, profile leans HIGH -> fade UP (long)
        elif c1 > o1 and c2 > o2 and sk <= -SKEW_HI:
            s = -1                                   # bullish pair, profile leans LOW -> fade DOWN (short)
        else:
            continue
        dm = half_dom(buckets[i], s); ca = climax_close(buckets[i], s)
        _pd = (dm is not None and dm > DOM_MIN)
        _pc = (ca is not None and ca >= CLIMAX_MIN)
        try:
            _a1, _a2 = _absorption.absorption_halves(buckets, i)     # per-half absorption (needs delta_h1+price_h1)
        except Exception:
            _a1 = _a2 = None
        _dA = (_a2 - _a1) if (_a1 is not None and _a2 is not None) else None
        _pa = (_dA is None) or (_dA <= ABSORB_MAX)                   # fail only on a COMPUTABLE h2-more-absorbed
        _dp1 = (c1 - o1) / o1 * 100.0; _dp2 = (c2 - o2) / o2 * 100.0  # whole-candle % move of each candle
        _pe = (s * (_dp1 - _dp2)) > 0.0                              # move EXPANDS c1->c2 (LONG dP1>dP2 / SHORT dP1<dP2)
        out.append(dict(i=i, side=s, entry=c2, skew=float(sk),
                        sl=c2 * (1 - SL_PCT) if s > 0 else c2 * (1 + SL_PCT),
                        tp=c2 * (1 + TP_PCT) if s > 0 else c2 * (1 - TP_PCT),
                        dom=dm, pass_dom=_pd, ca=ca, pass_climax=_pc, dA=_dA, pass_absorb=_pa,
                        dp1=_dp1, dp2=_dp2, pass_expand=_pe, pass_full=(_pd and _pc and _pa and _pe)))
    return out
