"""ABSORPTION S/R LEVELS — eyeball overlay (m10_absorblvl), ALL timeframes.

Discovers levels where AGGRESSION gets ABSORBED and marks them as S/R zones:
  buy-absorbed  (strong net-BUY delta, tiny body -> buyers pushed, price didn't follow) -> the HIGH = RESISTANCE (red)
  sell-absorbed (strong net-SELL delta, tiny body -> sellers pushed, price didn't drop) -> the LOW  = SUPPORT   (green)
Nearby same-side events CLUSTER (count++) into one stronger level (drawn brighter). A level runs from where it formed
until price CLOSES through it (broken) or it expires (L_LIFE bars).

⚠ DESCRIPTIVE ONLY — NOT a signal. These levels reject NO more than a random line (study/absorption_levels.py:
aligned 63.9% == clustered 63.6% == anti 65.0% == placebo 63.4%, p=0.39). Absorption DESCRIBES a reversal that
already happened; it does not predict the next. This overlay just lets you SEE the pattern. Fail-safe: [].

detect(buckets, skip_last=False) -> [{price, side('R'|'S'), i0, i1, count}]   (i0 born bar, i1 broken/expiry bar).
"""
from __future__ import annotations

from .engulf_sr_detect import _ohlc

T = 20.0            # |net-delta%| that counts as strong one-sided aggression
BODY = 0.35        # |close-open| / range <= this = tiny body (no price progress -> absorbed)
EPS = 0.0015       # cluster / break tolerance (0.15%)
L_LIFE = 96        # a level stays drawable at most this many bars past its last touch


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def detect(buckets, skip_last=False):
    n = len(buckets)
    if n < 4:
        return []
    try:
        O = [0.0] * n; C = [0.0] * n; H = [0.0] * n; L = [0.0] * n; DP = [0.0] * n
        for i, b in enumerate(buckets):
            O[i], C[i], H[i], L[i] = _ohlc(b)
            cv = _f(b.get("curr_vol"))
            if cv > 0:
                DP[i] = (_f(b.get("buy_vol")) - _f(b.get("sell_vol"))) / cv * 100.0
        hi_n = (n - 1) if skip_last else n
        levels = []                                              # {price, side, i0, count, last}
        for i in range(hi_n):
            rng = H[i] - L[i]
            if rng <= 0 or O[i] <= 0:
                continue
            if abs(DP[i]) < T or abs(C[i] - O[i]) / rng > BODY:  # need strong flow AND a tiny (absorbed) body
                continue
            if DP[i] > 0:
                price, side = H[i], "R"                          # buyers absorbed at the high -> resistance
            else:
                price, side = L[i], "S"                          # sellers absorbed at the low -> support
            merged = None
            for lv in levels:
                if lv["side"] == side and abs(lv["price"] - price) <= price * EPS * 2 and i - lv["last"] <= L_LIFE:
                    merged = lv; break
            if merged is not None:
                merged["count"] += 1; merged["last"] = i
                merged["price"] = (merged["price"] * (merged["count"] - 1) + price) / merged["count"]
            else:
                levels.append({"price": price, "side": side, "i0": i, "count": 1, "last": i})
        out = []
        for lv in levels:
            exp = min(n - 1, lv["last"] + L_LIFE)
            i1 = exp
            for k in range(lv["i0"] + 1, exp + 1):               # stop the level where price CLOSES through it
                if lv["side"] == "R" and C[k] > lv["price"] * (1 + EPS):
                    i1 = k; break
                if lv["side"] == "S" and C[k] < lv["price"] * (1 - EPS):
                    i1 = k; break
            out.append({"price": lv["price"], "side": lv["side"], "i0": lv["i0"], "i1": i1, "count": lv["count"]})
        return out
    except Exception:
        return []
