"""5m BREAKOUT indicator — squared 'Br' badges on breakout (S/R-mitigation) candles (5m ONLY) — LIVE terminal overlay.

A breakout candle is the bar that MITIGATES an S/R level: it closes THROUGH the level, past the WIDENED 5m area edge
(the same `zone_mitigation` the 5m S/R display + the 5m strategies use, so a badge appears exactly when a level as drawn
on the chart is broken). GREEN 'Br' = up-break (a RESISTANCE broken); RED 'Br' = down-break (a SUPPORT broken).

Causal: the badge marks the breaking candle at its OWN close (the level's i1) — no look-ahead. This is a DESCRIPTIVE
marker, not a signal. Study basis (study/mit_candle_study + study/mit_predict): break candles are wide-body marubozus
that close at the break extreme on aligned toxic flow, with OI OPENING AGAINST the move (covering-driven); those
features do NOT predict a break in advance, so a 'Br' badge highlights a break, it does not forecast one.

detect(buckets) -> [{i, kind('R'/'S'), side(+1 up / -1 down)}]  (i in passed-list space)
"""
from __future__ import annotations

from . import support_resistance as _sr

K = _sr.SR_PIVOT_K


def detect(buckets):
    if len(buckets) < 2 * K + 2:
        return []
    levels = _sr.detect(buckets, K, zone_mitigation=True)   # 5m: a level breaks only past the WIDENED area edge
    kinds = {}
    for lv in levels:
        i1 = lv.get("i1")
        if i1 is not None:
            kinds.setdefault(i1, lv["kind"])                # the candle that closed through -> its break kind (R/S)
    return [dict(i=i, kind=k, side=(1 if k == "R" else -1)) for i, k in sorted(kinds.items())]
