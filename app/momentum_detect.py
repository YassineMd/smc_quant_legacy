"""15m ENGULFING WALL — a strong engulf REJECTION off an Order-Flow WALL's radar area — LIVE overlay (15m ONLY).
(display name "15m Engulfing Wall"; internal keys stay m10_momentum / momentum_detect / _mom_*.)

REWORKED 2026-08-13 (user): no longer S/R-dependent — the context is now WALLS / radar area. A BOUNCE/defend setup:
inside a BUY wall (support) radar a bullish engulf rejects UP -> LONG; inside a SELL wall (resistance) radar a bearish
engulf rejects DOWN -> SHORT. The candle criteria (engulf + easy absorption + non-doji + aligned skew) are unchanged;
only the S/R regime/confluence/guards are replaced by "is this candle in a same-side wall's radar visit".

LONG (SHORT mirrors):
  context = the signal candle sits inside a SUPPORT wall's radar visit (absorption_level_detect radar_runs).
  c2 = bullish ENGULFING (body engulfs c1) that is non-doji, body > UPPER wick, easy [absorption A<=-0.3 OR
       (A_h2 < -0.5 AND A < 0)], close > prev candle's BODY top, and buy-skewed footprint (skew>0) — aligned bounce.
       SHORT: at a RESISTANCE wall's radar, bearish engulf, body > LOWER wick, close < prev BODY bottom, skew<0.
BADGE tiers (disjoint, priority gold > US > normal): GOLD = very-easy c2 (A<=-2, TP 1:2); US = a normal-tier candle
  meeting the edge pocket (VPIN>0.14 & OI-opening & UTC hour>=13) -> NEON; else plain green(long)/red(short).
EXIT: SL 0.1% beyond the PREVIOUS candle's extreme. TP 1:1.2 — bumped to 1:2 for GOLD. Entry = c2 close.

detect(buckets, walls=None, skip_last=True) -> [{i, side(+1/-1), entry, sl, tp, src('WALL'), tier, relaxed(bool),
  flow, flow_align}]. `walls` = absorption_level_detect.detect() marks (detected internally if None). i in passed-list
  space. ⚠ IN-PROGRESS operator variant — for live eyeballing / iteration, NOT a proven edge (not backtested).
"""
from __future__ import annotations

import datetime as _dt

from . import absorption_level_detect as _al
from . import absorption as _absorption
from .footprint_panel import profile_skewness
from .engulf_sr_detect import _ohlc                        # reuse the parity-verified OHLC accessor

AB2 = -0.3           # candle 2 absorption ceiling (A <= this = an "easy" move) — entry gate
R2_EASY = -0.5       # OR-branch: 2nd-half absorption A_h2 < this (easy second half) AND whole-bucket A < 0
GOLD_A = -2.0        # c2 absorption A <= this = a VERY-easy move -> GOLD badge + 1:2 TP
SL_PAD = 0.001       # structural stop 0.1% beyond the previous candle's extreme
RR = 1.2             # base reward:risk
RR_CONF = 2.0        # gold (A<=-2) -> 1:2
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


def detect(buckets, walls=None, skip_last=True):
    n = len(buckets)
    if n < 3:
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

    # WALL / RADAR context (replaces S/R): which candles sit INSIDE a SUPPORT (S) / RESISTANCE (R) wall's radar
    # visit. `walls` = app.absorption_level_detect.detect() marks (radar_runs carry the visit windows); detected
    # here if not supplied by the caller.
    if walls is None:
        try:
            walls = _al.detect(buckets, skip_last=False)
        except Exception:
            walls = []
    sup_radar = [False] * n; res_radar = [False] * n
    for w in (walls or []):
        _s = w.get("side")
        for r in w.get("radar_runs", ()):
            if len(r) < 2:
                continue
            rk0 = max(0, int(r[0])); rk1 = min(n - 1, int(r[1]))
            for k in range(rk0, rk1 + 1):
                if _s == "S":
                    sup_radar[k] = True
                elif _s == "R":
                    res_radar[k] = True

    def nd(i):
        b = abs(C[i] - O[i]); return b > (H[i] - max(O[i], C[i])) and b > (min(O[i], C[i]) - Lo[i])

    out = []
    for i in range(1, (n - 1) if skip_last else n):
        o, c, h, l = O[i], C[i], H[i], Lo[i]
        rng = h - l
        if o <= 0 or c <= 0 or rng <= 0 or not nd(i):
            continue
        if not (sup_radar[i] or res_radar[i]):               # must sit AT a wall's radar area (else no signal)
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
        body = abs(c - o); side = 0
        pbhi = max(O[i - 1], C[i - 1]); pblo = min(O[i - 1], C[i - 1])   # previous candle's BODY top / bottom
        gold = a2 <= GOLD_A                                  # very-easy c2 -> GOLD tier + 1:2 TP
        # LONG: a SUPPORT wall DEFENDS -> a bullish engulf REJECTION off it (body engulfs prev, body > upper wick,
        # buy-skewed footprint aligned with the bounce).
        if sup_radar[i] and c > o and c > pbhi and o <= pblo and body > (h - max(o, c)) and sk > 0:
            side = 1
        # SHORT: a RESISTANCE wall DEFENDS -> a bearish engulf REJECTION off it (body > lower wick, sell-skewed).
        elif res_radar[i] and c < o and c < pblo and o >= pbhi and body > (min(o, c) - l) and sk < 0:
            side = -1
        if side == 0:
            continue
        ext = Lo[i - 1] if side > 0 else H[i - 1]             # SL 0.1% beyond the PREVIOUS candle's extreme
        sl = ext * (1 - SL_PAD) if side > 0 else ext * (1 + SL_PAD)
        if (side > 0 and sl >= c) or (side < 0 and sl <= c):
            continue                                          # degenerate stop
        sld = (c - sl) if side > 0 else (sl - c)
        rr = RR_CONF if gold else RR                          # gold -> 1:2, else 1:1.2
        tier = "gold" if gold else ("us" if _us_pocket(buckets[i]) else "normal")
        fl = _flow(i)
        out.append(dict(i=i, side=side, entry=c, sl=sl, tp=c + rr * sld * side,
                        src="WALL", tier=tier, relaxed=(tier != "normal"),
                        flow=fl, flow_align=((fl > 0) if side > 0 else (fl < 0))))
    return out
