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
MAD_K = 3.0         # CRAZY = >= this many robust-sigma (1.4826*MAD) above the median = a statistical outlier
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
            if sigma > 0:                                    # robust z-score outlier test
                z = (tot - med) / sigma
                if z < MAD_K:
                    continue
            else:                                            # degenerate spread -> fall back to 2x the median
                if med <= 0 or tot < 2.0 * med:
                    continue
                z = float("inf")
            hit = None                                       # the bubble must land in an ACTIVE wall's radar
            for w in walls:
                if w["i0"] <= i <= min(int(w["i1"]), n - 1):
                    band = _f(w.get("band")); wp = _f(w.get("price"))
                    if band > 0 and (wp - RADAR_MULT * band) <= price <= (wp + RADAR_MULT * band):
                        hit = w; break
            if hit is None:
                continue
            side = "buy" if buy >= sell else "sell"
            close = _f(buckets[i].get("close", buckets[i].get("close_price")))
            wside = hit.get("side", "R"); wp = _f(hit.get("price"))
            # Absorption = the aggression OPPOSITE the wall's hold direction failed to close through it:
            #   support (S): a big SELL that still closed >= the wall -> absorbed; else aggression
            #   resistance (R): a big BUY that still closed <= the wall -> absorbed; else aggression
            if wside == "S":
                kind = "Ab" if (side == "sell" and close >= wp) else "Ag"
            else:
                kind = "Ab" if (side == "buy" and close <= wp) else "Ag"
            out.append({"i": i, "price": price, "side": side, "vol": tot,
                        "z": z, "kind": kind, "wall_side": wside})
        return out
    except Exception:
        return []
