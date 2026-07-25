"""15mReasy overlay detector for the LIVE terminal (15m) — FROZEN CANDIDATE (2026-07-25).

A "flow moved easily, in strong agreement, with prior momentum" entry:

    LONG  = bullish bucket AND absorption A <= -0.75 (aggressor moved price EASILY) AND profile skew >= +0.4
            AND the PREVIOUS candle was also bullish.
    SHORT = mirror (bearish, A <= -0.75, skew <= -0.4, previous bearish).

    entry : the entry candle's close.
    stop  : STRUCTURAL — 0.1% beyond the entry candle's extreme (long: low*0.999, short: high*1.001).
    target: RR x the stop distance (RR = 1.5 default).

A = app.absorption.absorption()[0], oriented so POSITIVE = that candle's aggressor was ABSORBED; A <= -0.75 =
the "light/easy" side. It needs a trailing window, so the caller prepends a warm-up prefix and shifts indices
back (same contract as da2_reversion_detect / mmxskew_detect). skew = footprint_panel.profile_skewness(levels).

SKEW-MAGNITUDE FREEZE (2026-07-25): the `|skew| >= 0.4` floor replaced the old sign-only `skew><0`. It is the
only 15m filter that improved outcomes MONOTONICALLY and on BOTH sides — the skew-threshold sweep (|skew| >=
0.0/0.1/0.2/0.3/0.4) climbed 50.7% -> 59.2% win at every step. At 0.4, in-sample (one ~34-day regime, forward
n=0): 103 signals (50L/53S), BOTH sides clear the fee-adjusted break-even at BOTH RRs — TP 1:1.0 win ~66% (BE*
~59%, PF 1.28, net +5.9%), TP 1:1.5 win ~54% (BE* ~48%, PF 1.21, net +5.8%) after 0.08% taker. NOT significance-
tested after the deep search (multiplicity), small n — frozen to judge FORWARD, not a proven edge.

detect(buckets, skip_last=True) -> [{i, side(+1/-1), entry, sl, tp}]  (i in the PASSED list's index space).
"""
from __future__ import annotations

from . import absorption as _absorption
from .footprint_panel import profile_skewness

R_EASY_MAX = -0.75     # A <= this = aggressor moved price EASILY (little resistance)
SKEW_MIN = 0.4         # |skew| >= this in the trade direction (FROZEN 2026-07-25; magnitude, not just sign)
SL_PAD = 0.001         # structural stop 0.1% beyond the entry candle's extreme
TP_RR = 1.5            # target = RR x stop distance


def _ohlc(b):
    o = float(b.get("open", b.get("open_price", 0.0)) or 0.0)
    c = float(b.get("close", b.get("close_price", 0.0)) or 0.0)
    h = float(b.get("high", 0.0) or 0.0)
    l = float(b.get("low", 0.0) or 0.0)
    return o, c, h, l


def detect(buckets: list, skip_last: bool = True) -> "list[dict]":
    n = len(buckets)
    if n < 2:
        return []
    out = []
    for i in range(1, n - 1 if skip_last else n):
        o, c, h, l = _ohlc(buckets[i])
        if o <= 0 or c <= 0 or h <= 0 or l <= 0:
            continue
        s = 1 if c > o else (-1 if c < o else 0)
        if s == 0:
            continue
        po, pc, _ph, _pl = _ohlc(buckets[i - 1])          # previous candle same direction
        pdir = 1 if pc > po else (-1 if pc < po else 0)
        if pdir != s:
            continue
        sk = profile_skewness(buckets[i].get("levels"))    # skew must agree AND be strong: |skew| >= 0.4
        if sk is None or (sk < SKEW_MIN if s > 0 else sk > -SKEW_MIN):
            continue
        try:
            a = _absorption.absorption(buckets, i)[0]       # R-easy: A <= -0.75
        except Exception:
            a = None
        if a is None or a > R_EASY_MAX:
            continue
        sl = l * (1 - SL_PAD) if s > 0 else h * (1 + SL_PAD)
        if (s > 0 and sl >= c) or (s < 0 and sl <= c):
            continue                                        # degenerate (close at/through its own stop)
        sld = (c - sl) if s > 0 else (sl - c)
        out.append(dict(i=i, side=s, entry=c, sl=sl, tp=c + TP_RR * sld * s))
    return out
