"""ORDER-FLOW WALLS — eyeball overlay (m10_absorblvl), ALL timeframes. Absorption + Aggression combined.

A wall = where strong one-sided aggression (|net-delta%| >= T) meets its limit. TWO ways a wall forms:
  ABSORPTION (tiny body -> aggressor pushed, price didn't follow = absorbed AT the extreme):
     buy-absorbed  -> HIGH = RESISTANCE (red) ;  sell-absorbed -> LOW = SUPPORT (green)
  AGGRESSION (big body -> aggressor moved FROM a base, leaving an origin / order block):
     sell-aggression -> HIGH = RESISTANCE (red) ;  buy-aggression -> LOW = SUPPORT (green)
Nearby same-side walls CLUSTER (count++) into one stronger level (drawn brighter). A level runs from where it formed
until price CLOSES through it, or expires (L_LIFE bars).

⚠ DESCRIPTIVE ONLY — barely a signal. study/wall_levels.py (vs a random-line placebo, 15m): ABSORPTION 64.1% == placebo
63.4% (p=0.30, null); AGGRESSION 66.3% (p<1e-3) — but the direction-shuffle also hits 65.0%, so only ~1.3pp is truly
directional and +3pp on a 63% geometric base does NOT clear the fee. Reads the structure; does not predict. Fail-safe: [].

detect(buckets, skip_last=False) -> [{price, side('R'|'S'), src('abs'|'agg'|'mix'), i0, i1, count}].
"""
from __future__ import annotations

from .engulf_sr_detect import _ohlc

T = 20.0            # |net-delta%| = strong one-sided aggression
BODY_SMALL = 0.35  # |close-open|/range <= this = tiny body (absorbed at the extreme)
BODY_BIG = 0.60    # |close-open|/range >= this = decisive move (origin / order-block wall)
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
        levels = []                                             # {price, side, src, i0, count, last}
        for i in range(hi_n):
            rng = H[i] - L[i]
            if rng <= 0 or O[i] <= 0 or abs(DP[i]) < T:
                continue
            body = abs(C[i] - O[i]) / rng
            if body <= BODY_SMALL:                              # ABSORPTION — wall AT the failed extreme
                price, side, src = (H[i], "R", "abs") if DP[i] > 0 else (L[i], "S", "abs")
            elif body >= BODY_BIG and ((DP[i] > 0) == (C[i] > O[i])):   # AGGRESSION — wall at the move's ORIGIN
                price, side, src = (L[i], "S", "agg") if DP[i] > 0 else (H[i], "R", "agg")
            else:
                continue
            merged = None
            for lv in levels:
                if lv["side"] == side and abs(lv["price"] - price) <= price * EPS * 2 and i - lv["last"] <= L_LIFE:
                    merged = lv; break
            if merged is not None:
                merged["count"] += 1; merged["last"] = i
                merged["price"] = (merged["price"] * (merged["count"] - 1) + price) / merged["count"]
                if merged["src"] != src:
                    merged["src"] = "mix"
            else:
                levels.append({"price": price, "side": side, "src": src, "i0": i, "count": 1, "last": i})
        out = []
        for lv in levels:
            exp = min(n - 1, lv["last"] + L_LIFE)
            i1 = exp
            for k in range(lv["i0"] + 1, exp + 1):              # stop the level where price CLOSES through it
                if lv["side"] == "R" and C[k] > lv["price"] * (1 + EPS):
                    i1 = k; break
                if lv["side"] == "S" and C[k] < lv["price"] * (1 - EPS):
                    i1 = k; break
            out.append({"price": lv["price"], "side": lv["side"], "src": lv["src"],
                        "i0": lv["i0"], "i1": i1, "count": lv["count"]})
        return out
    except Exception:
        return []
