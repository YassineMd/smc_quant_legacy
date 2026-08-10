"""CRAZY WALL Ag/Ab — spot OUTLIER volume bubbles sitting at an Order-Flow Wall / its radar S/R zone.

A footprint "bubble" = a heavy-volume price LEVEL in a bucket (buy+sell). It is CRAZY when its volume is a
STATISTICAL OUTLIER vs the bubbles of the last WIN candles — NOT a fixed multiple (that ignores dispersion and
breaks across regimes / timeframes), but a robust z-score: how many robust-sigma (MAD) above the median it sits.
The threshold MAD_K is a statistical significance level, so it self-adapts — in a tight, low-dispersion tape a
modestly-bigger bubble already clears it; in a wild, wide-dispersion tape it takes a far bigger one. We only flag a
crazy bubble that also lands INSIDE an active wall's radar (price +/- 3*band), where the size means something:
the big order either gets ABSORBED (price fails to close through the wall) or is the AGGRESSION driving it.

  side  = the bubble's dominant taker side (buy -> GREEN star, sell -> RED star).
  z     = robust z-score = (vol - median) / (1.4826 * MAD) over the last WIN candles' bubbles.
  kind  = 'Ab' (absorption: the aggression opposite the wall FAILED to close through it) or 'Ag' (aggression).

detect(buckets, walls, skip_last=False) ->
  [{i, price, side('buy'|'sell'), vol, z, kind('Ab'|'Ag'), wall_side('R'|'S')}]
`walls` = the app.absorption_level_detect.detect() output (marks carry i0/i1/price/band/side). DESCRIPTIVE overlay.
"""
from __future__ import annotations

WIN = 30            # look back over the last N candles to define "normal" (per the user; adapts with the tape)
MIN_N = 15          # need at least this many candles-with-a-bubble in the window to judge (robust stats need a sample)
MAD_K = 2.0         # CRAZY tier = >= this many robust-sigma (1.4826*MAD) above the median = a statistical outlier
BIG_K = 1.0         # BIG tier = >= this (but < MAD_K) sigma -> a big-but-not-crazy absorbed bubble
RADAR_MULT = 3.0    # wall radar = price +/- this * band (matches the overlay's radar)
_MAD_SCALE = 1.4826 # MAD -> sigma-equivalent for a normal distribution


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _median(vals) -> float:
    m = len(vals)
    if m == 0:
        return 0.0
    s = sorted(vals)
    return s[m // 2] if m % 2 else (s[m // 2 - 1] + s[m // 2]) / 2.0


def _center_scale(vals):
    """Robust centre + spread: (median, 1.4826*MAD). Spread is 0 only for a degenerate (mostly-identical) window."""
    med = _median(vals)
    mad = _median([abs(v - med) for v in vals])
    return med, _MAD_SCALE * mad


def _bubbles(b):
    """The drawn bubbles = the TOP-3 footprint levels by volume -> [(price, total, buy, sell), ...] desc; [] if none."""
    lv = []
    for p, vv in (b.get("levels") or {}).items():
        try:
            pr = float(p)
        except (TypeError, ValueError):
            continue
        buy = _f(vv.get("b")); sell = _f(vv.get("s")); tot = buy + sell
        if tot > 0:
            lv.append((pr, tot, buy, sell))
    lv.sort(key=lambda t: t[1], reverse=True)
    return lv[:3]


def _top1(b) -> float:
    """The heaviest single footprint level's volume in a bucket (0 if no footprint)."""
    best = 0.0
    for _p, vv in (b.get("levels") or {}).items():
        t = _f(vv.get("b")) + _f(vv.get("s"))
        if t > best:
            best = t
    return best


def crazy_thresholds(buckets):
    """WALL-INDEPENDENT per-bucket CRAZY-VOLUME threshold: the volume above which a footprint bubble is a
    >= MAD_K robust-sigma outlier vs the PAST WIN candles' TOP bubbles (median + MAD_K*1.4826*MAD). None where
    there isn't enough history. Same statistic as detect(), minus the wall gate — used to recolour the Candle
    Bubbles cyan(buy)/magenta(sell). A bubble is crazy iff its total volume >= thresholds[i]."""
    n = len(buckets)
    out = [None] * n
    if n < WIN + 1:
        return out
    try:
        top1 = [_top1(b) for b in buckets]
        for i in range(WIN, n):
            base = [top1[j] for j in range(i - WIN, i) if top1[j] > 0]
            if len(base) < MIN_N:
                continue
            med, sigma = _center_scale(base)
            if sigma > 0:
                out[i] = med + MAD_K * sigma
            elif med > 0:
                out[i] = 2.0 * med
        return out
    except Exception:
        return out


def detect(buckets, walls, skip_last=False):
    n = len(buckets)
    if n < WIN + 2 or not walls:
        return []
    try:
        bubs = [_bubbles(b) for b in buckets]                # per-candle top-3 bubbles
        out = []
        hi_n = (n - 1) if skip_last else n
        for i in range(WIN, hi_n):
            cur = bubs[i]
            if not cur:
                continue
            price, tot, buy, sell = cur[0]                   # candidate = THIS candle's biggest bubble
            base = [bubs[j][0][1] for j in range(i - WIN, i) if bubs[j]]   # LIKE-FOR-LIKE: past candles' biggest bubbles
            if len(base) < MIN_N:
                continue
            med, sigma = _center_scale(base)
            if sigma > 0:                                    # robust z-score
                z = (tot - med) / sigma
            else:                                            # degenerate spread -> 2x-median fallback
                z = float("inf") if (med > 0 and tot >= 2.0 * med) else 0.0
            if z >= MAD_K:                                    # tier by size
                tier = "crazy"
            elif z >= BIG_K:
                tier = "big"
            else:
                continue                                     # too small for either tier
            hit = None                                       # the bubble must land in an ACTIVE wall's radar
            for w in walls:
                if w["i0"] <= i <= min(int(w["i1"]), n - 1):
                    band = _f(w.get("band")); wp = _f(w.get("price"))
                    if band > 0 and (wp - RADAR_MULT * band) <= price <= (wp + RADAR_MULT * band):
                        hit = w; break
            if hit is None:
                continue
            side = "buy" if buy >= sell else "sell"           # the crazy bubble's dominant taker side
            close = _f(buckets[i].get("close", buckets[i].get("close_price")))
            wside = hit.get("side", "R")
            # TRUE ABSORPTION ONLY — the crazy aggression must be the OPPOSITE side of the wall AND get rejected
            # (price closed STRICTLY THROUGH the bubble in the wall's favour; closing AT it is not a rejection):
            #   BUY wall (S, support):     a crazy SELL absorbed -> price closed ABOVE the bubble -> held -> GREEN below
            #   SELL wall (R, resistance): a crazy BUY  absorbed -> price closed BELOW the bubble -> held -> RED above
            if wside == "S":
                if not (side == "sell" and close > price):    # need a crazy SELL that failed to close below support
                    continue
            else:
                if not (side == "buy" and close < price):     # need a crazy BUY that failed to close above resistance
                    continue
            out.append({"i": i, "price": price, "side": side, "vol": tot,
                        "z": z, "kind": "Ab", "wall_side": wside, "tier": tier})
        return out
    except Exception:
        return []
