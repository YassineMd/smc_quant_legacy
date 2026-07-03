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
