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

detect(buckets, skip_last=True) -> [{i, side(+1/-1), entry, sl, tp, skew}]
"""
from __future__ import annotations

SL_PCT = 0.008
TP_PCT = 0.008
SKEW_HI = 0.5            # skew_read()'s "high"/"low" boundary (app/footprint_panel.py) — NOT fitted here


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
        out.append(dict(i=i, side=s, entry=c2, skew=float(sk),
                        sl=c2 * (1 - SL_PCT) if s > 0 else c2 * (1 + SL_PCT),
                        tp=c2 * (1 + TP_PCT) if s > 0 else c2 * (1 - TP_PCT)))
    return out
