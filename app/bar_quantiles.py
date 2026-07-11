"""SHARED per-bar ladder statistics — the single implementation used by BOTH the terminal's 'W'
whisker-bar render mode and the S4-GEO study, so M/P mean exactly the same thing everywhere.

All functions read a footprint ladder ``levels = {price_str: {"b": buy_vol, "s": sell_vol}}`` and use
TOTAL (b+s) volume per level, prices ascending. Unusable ladders (< 2 levels or zero volume) -> NaN.
"""
from __future__ import annotations

_NAN = float("nan")


def vq(levels: dict) -> "tuple[float, float, float]":
    """(q25, vw_median, q75): the first prices (ascending) where cumulative volume reaches
    25% / 50% / 75% of the bar's total — the 'W'-mode box bounds and median line."""
    if not levels or len(levels) < 2:
        return _NAN, _NAN, _NAN
    pr = sorted((float(pp), float(vv.get("b", 0.0)) + float(vv.get("s", 0.0)))
                for pp, vv in levels.items())
    V = sum(v for _, v in pr)
    if V <= 0:
        return _NAN, _NAN, _NAN
    cum = 0.0
    thr = (0.25 * V, 0.50 * V, 0.75 * V)
    got = []
    for price, v in pr:
        cum += v
        while len(got) < 3 and cum >= thr[len(got)] - 1e-12:
            got.append(price)
        if len(got) == 3:
            break
    if len(got) != 3:
        return _NAN, _NAN, _NAN
    return got[0], got[1], got[2]


def poc(levels: dict) -> float:
    """Point of Control: the price (ascending scan; FIRST on an exact volume tie) holding the largest
    total (b+s) volume. NaN for unusable ladders."""
    if not levels or len(levels) < 2:
        return _NAN
    pr = sorted((float(pp), float(vv.get("b", 0.0)) + float(vv.get("s", 0.0)))
                for pp, vv in levels.items())
    if sum(v for _, v in pr) <= 0:
        return _NAN
    best_p, best_v = pr[0][0], pr[0][1]
    for price, v in pr[1:]:
        if v > best_v:
            best_p, best_v = price, v
    return best_p


def value_area(levels: dict, pct: float = 0.70) -> "tuple[float, float]":
    """(VAL, VAH): the low/high price bounds of the Value Area — the contiguous band grown OUTWARD from the POC,
    at each step taking the heavier of the two adjacent levels, until it holds `pct` of total volume (standard
    Market-Profile 70% VA). NaN for unusable ladders."""
    if not levels or len(levels) < 2:
        return _NAN, _NAN
    pr = sorted((float(pp), float(vv.get("b", 0.0)) + float(vv.get("s", 0.0)))
                for pp, vv in levels.items())
    V = sum(v for _, v in pr)
    if V <= 0:
        return _NAN, _NAN
    poc_i = 0                                            # POC index (first on a tie, matching poc())
    for i in range(1, len(pr)):
        if pr[i][1] > pr[poc_i][1]:
            poc_i = i
    lo = hi = poc_i; acc = pr[poc_i][1]; target = pct * V
    while acc < target and (lo > 0 or hi < len(pr) - 1):
        up = pr[hi + 1][1] if hi < len(pr) - 1 else -1.0
        dn = pr[lo - 1][1] if lo > 0 else -1.0
        if up >= dn:
            hi += 1; acc += up
        else:
            lo -= 1; acc += dn
    return pr[lo][0], pr[hi][0]
