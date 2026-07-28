"""15m ENGULFING S/R — engulf breakout in the last-mitigation S/R regime — LIVE terminal overlay (15m ONLY).
(display name "15m Engulfing S/R"; internal keys stay m10_momentum / momentum_detect / _mom_*.)

A momentum/continuation breakout: when the S/R structure last broke UP (a resistance was mitigated) take longs; when
it last broke DOWN (a support was mitigated) take shorts. Fire on a strong engulfing candle in that direction. The
S/R indicator is used ONLY for the regime (last mitigation), the two "don't fire into the opposite zone" guards, and a
TP bump when the signal sits AT a same-side zone. No candle-1 condition, no "at S/R" entry requirement, no VA.

LONG (SHORT mirrors):
  regime = last mitigated S/R zone is a RESISTANCE (short: a SUPPORT).
  c2 = bullish ENGULFING (body engulfs c1) that is non-doji, body > UPPER wick, easy [absorption A<=-0.3 OR
       (A_h2 < -0.5 AND A < 0) i.e. the 2nd half was easy], close > prev candle's BODY top (max open/close).
       SHORT: bearish engulf, body > LOWER wick, same easy gate, close < prev candle's BODY bottom (min open/close).
  SKEW is TIER-DEPENDENT (from the aligned/anti study): GOLD or BLUE need aligned skew (LONG skew>0 / SHORT skew<0);
  the NORMAL tier needs ANTI-aligned skew (LONG skew<0 / SHORT skew>0).
GUARD (S/R indicator only): skip a bar where a support zone overlaps a resistance zone.
BADGE tiers (disjoint, priority gold > blue > US > normal): GOLD = very-easy c2 (A<=-2); BLUE = at-S/R confluence
  (LONG touching a support / SHORT touching a resistance); US = a normal-tier candle meeting the edge pocket
  (VPIN>0.14 & OI-opening & UTC hour>=13) -> NEON green(long)/red(short); else plain green(long)/red(short).
EXIT: SL 0.1% beyond the PREVIOUS candle's extreme (c1 low for long / c1 high for short). TP 1:1.2 — bumped to 1:2 for
  GOLD or BLUE badges. Entry = c2 close.

Parity-matched to study/absorb_engulf_lastmit_15m.py (body_break=True, opp_guard=False, ab2=-0.3). ⚠ IN-PROGRESS operator
variant — the full mechanical rule loses; the at-S/R CONFLUENCE (blue, TP 1:2) is the net-positive subset (~PF 1.28 near
this threshold). Shipped for live eyeballing / iteration, NOT a proven edge.

detect(buckets, skip_last=True) -> [{i, side(+1/-1), entry, sl, tp, src('SR' if at-zone confluence else ''), relaxed(bool)}]
  (relaxed == confluence == the 1:2-TP trades; i in passed-list space).
"""
from __future__ import annotations

import datetime as _dt

from . import support_resistance as _sr
from . import absorption as _absorption
from .footprint_panel import profile_skewness
from .engulf_sr_detect import _ohlc                        # reuse the parity-verified OHLC accessor

AB2 = -0.3           # candle 2 absorption ceiling (A <= this = an "easy" move) — entry gate
R2_EASY = -0.5       # OR-branch: 2nd-half absorption A_h2 < this (easy second half) AND whole-bucket A < 0
GOLD_A = -2.0        # c2 absorption A <= this = a VERY-easy move -> GOLD badge + 1:2 TP
SL_PAD = 0.001       # structural stop 0.1% beyond the previous candle's extreme
RR = 1.2             # base reward:risk
RR_CONF = 2.0        # gold (A<=-2) OR at-S/R confluence -> 1:2
K = _sr.SR_PIVOT_K
VPIN_THR = 0.14      # US tier: signal-candle VPIN (|delta|/vol) > this (= normal-tier median)
US_HOUR = 13         # US tier: UTC hour >= this (US session)
FLOW_K = 12          # trailing flow window (buckets, ~3h on 15m) — RING when this flow ALIGNS with the trade side
#                      (LONG wants net-buy / SHORT wants net-sell). Highlight only, changes no trades. Parity:
#                      study/engulf_flowalign_15m.py (align gate lifted the tradeable cohort PF 1.20 -> 1.40).


def _us_pocket(b) -> bool:
    """US TIER filter for a NORMAL-tier candle: toxic/imbalanced flow (VPIN > 0.14) AND fresh OI opening
    ((opL+opS)-(clL+clS) > 0) AND US session (UTC hour >= 13). The forward-candidate edge pocket."""
    bv = float(b.get("buy_vol", 0.0) or 0.0); sv = float(b.get("sell_vol", 0.0) or 0.0)
    vol = bv + sv
    if vol <= 0:
        return False
    if abs(bv - sv) / vol <= VPIN_THR:                      # high VPIN (toxic/imbalanced tape)
        return False
    oi = (float(b.get("opL", 0.0) or 0.0) + float(b.get("opS", 0.0) or 0.0)) \
        - (float(b.get("clL", 0.0) or 0.0) + float(b.get("clS", 0.0) or 0.0))
    if oi <= 0:                                             # net NEW positions opening
        return False
    st = float(b.get("start_time", 0.0) or 0.0)
    return st > 0 and _dt.datetime.utcfromtimestamp(st).hour >= US_HOUR


def detect(buckets, skip_last=True):
    n = len(buckets)
    if n < 2 * K + 2:
        return []
    O = [0.0] * n; C = [0.0] * n; H = [0.0] * n; Lo = [0.0] * n
    for i, b in enumerate(buckets):
        O[i], C[i], H[i], Lo[i] = _ohlc(b)
    # trailing-flow RSV over the last FLOW_K buckets (INCLUSIVE of the signal bar) — for the alignment ring only
    bvp = [0.0] * (n + 1); svp = [0.0] * (n + 1)
    for i, b in enumerate(buckets):
        bvp[i + 1] = bvp[i] + float(b.get("buy_vol", 0.0) or 0.0)
        svp[i + 1] = svp[i] + float(b.get("sell_vol", 0.0) or 0.0)

    def _flow(i):
        a = max(0, i - FLOW_K + 1); bs = bvp[i + 1] - bvp[a]; ss = svp[i + 1] - svp[a]
        return (bs - ss) / (bs + ss) if (bs + ss) > 0 else 0.0

    levels = _sr.detect(buckets, K)
    SUP = [x for x in levels if x["kind"] == "S"]; RES = [x for x in levels if x["kind"] == "R"]

    # ---- last-mitigation regime: +1 after a RESISTANCE mitigation, -1 after a SUPPORT mitigation, 0 before any
    mits = []
    for x in RES:
        if x["i1"] is not None:
            mits.append((x["i1"], 1))
    for x in SUP:
        if x["i1"] is not None:
            mits.append((x["i1"], -1))
    mits.sort()
    bias = [0] * n; state = 0; mi = 0
    for i in range(n):
        while mi < len(mits) and mits[mi][0] <= i:
            state = mits[mi][1]; mi += 1
        bias[i] = state

    def nd(i):
        b = abs(C[i] - O[i]); return b > (H[i] - max(O[i], C[i])) and b > (min(O[i], C[i]) - Lo[i])

    def active(levs, i):
        return [x for x in levs if x["i0"] + K <= i and (x["i1"] is None or x["i1"] > i)]

    def touches(i, levs):
        for x in active(levs, i):
            i0 = x["i0"]; zlo = min(Lo[i0], H[i0]); zhi = max(Lo[i0], H[i0])
            if (Lo[i] <= zhi and H[i] >= zlo) or (zlo <= O[i] <= zhi):
                return True
        return False

    def overlap(i):
        sup = [(min(Lo[x["i0"]], H[x["i0"]]), max(Lo[x["i0"]], H[x["i0"]])) for x in active(SUP, i)]
        res = [(min(Lo[x["i0"]], H[x["i0"]]), max(Lo[x["i0"]], H[x["i0"]])) for x in active(RES, i)]
        return any(slo <= rhi and shi >= rlo for slo, shi in sup for rlo, rhi in res)

    out = []
    for i in range(1, (n - 1) if skip_last else n):
        o, c, h, l = O[i], C[i], H[i], Lo[i]
        rng = h - l
        if o <= 0 or c <= 0 or rng <= 0 or not nd(i) or overlap(i):
            continue
        try:
            a2 = _absorption.absorption(buckets, i)[0]
        except Exception:
            continue
        if a2 is None:
            continue
        if a2 > AB2:                                         # whole-bucket not easy enough...
            try:
                r2 = _absorption.absorption_halves(buckets, i)[1]   # ...unless the 2nd half was easy (A_h2 < -0.5, A < 0)
            except Exception:
                r2 = None
            if not (r2 is not None and r2 < R2_EASY and a2 < 0):
                continue
        sk = profile_skewness(buckets[i].get("levels"))
        if sk is None:
            continue
        body = abs(c - o); side = 0; conf = False
        pbhi = max(O[i - 1], C[i - 1]); pblo = min(O[i - 1], C[i - 1])   # previous candle's BODY top / bottom
        gold = a2 <= GOLD_A                                  # very-easy c2 -> GOLD tier (priority over blue)
        # LONG: regime up, bullish engulf breakout closing above prev BODY, body > upper wick
        if (bias[i] == 1 and c > o and c > pbhi and o <= pblo and body > (h - max(o, c))):
            conf = touches(i, SUP)                           # at a support zone -> BLUE
            if (sk > 0) if (gold or conf) else (sk < 0):     # gold/blue: skew>0 (aligned); normal: skew<0 (anti)
                side = 1
        # SHORT: regime down, bearish engulf breakout closing below prev BODY, body > lower wick
        elif (bias[i] == -1 and c < o and c < pblo and o >= pbhi and body > (min(o, c) - l)):
            conf = touches(i, RES)                           # at a resistance zone -> BLUE
            if (sk < 0) if (gold or conf) else (sk > 0):     # gold/blue: skew<0 (aligned); normal: skew>0 (anti)
                side = -1
        if side == 0:
            continue
        ext = Lo[i - 1] if side > 0 else H[i - 1]             # SL 0.1% beyond the PREVIOUS candle's extreme
        sl = ext * (1 - SL_PAD) if side > 0 else ext * (1 + SL_PAD)
        if (side > 0 and sl >= c) or (side < 0 and sl <= c):
            continue                                          # degenerate stop
        sld = (c - sl) if side > 0 else (sl - c)
        rr = RR_CONF if (gold or conf) else RR               # gold OR confluence -> 1:2
        if gold:
            tier = "gold"
        elif conf:
            tier = "conf"
        else:                                                # normal-tier subset that meets the US-pocket edge -> US tier
            tier = "us" if _us_pocket(buckets[i]) else "normal"
        fl = _flow(i)
        out.append(dict(i=i, side=side, entry=c, sl=sl, tp=c + rr * sld * side,
                        src=("SR" if conf else ""), tier=tier, relaxed=(tier != "normal"),
                        flow=fl, flow_align=((fl > 0) if side > 0 else (fl < 0))))
    return out
