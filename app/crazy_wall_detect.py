"""CRAZY WALL Ag/Ab — spot OUTLIER volume bubbles sitting at an Order-Flow Wall / its radar S/R zone.

A footprint "bubble" = the heaviest-volume price LEVEL in a bucket (buy+sell). It is CRAZY when its volume is an
outlier vs the recent bubbles — the neighbours run ~10K and this one is 27-47K, so it "doesn't fit". We only flag a
crazy bubble when it lands INSIDE an active wall's radar (wall price +/- 3*band), because that is where the size
means something: a big order meeting a wall either gets ABSORBED (price fails to break the wall) or is the
AGGRESSION driving through it.

  side  = the bubble's dominant taker side (buy -> GREEN star, sell -> RED star).
  kind  = 'Ab' (absorption: the aggression opposite the wall FAILED to close through it) or 'Ag' (aggression).

detect(buckets, walls, skip_last=False) ->
  [{i, price, side('buy'|'sell'), vol, ratio(vol/median), kind('Ab'|'Ag'), wall_side('R'|'S')}]
`walls` = the app.absorption_level_detect.detect() output (marks carry i0/i1/price/band/side). DESCRIPTIVE overlay.
"""
from __future__ import annotations

WIN = 50            # rolling window of prior bubbles that defines "normal"
MIN_N = 20          # need this many prior bubbles before judging craziness
CRAZY_MULT = 2.5    # bubble volume >= this * the rolling MEDIAN of recent bubbles = a crazy outlier
RADAR_MULT = 3.0    # wall radar = price +/- this * band (matches the overlay's radar)


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


def _top_level(b):
    """The heaviest-volume level in a bucket -> (price, total, buy, sell); (None, 0, 0, 0) if no footprint."""
    best_p = None; best_t = 0.0; best_b = 0.0; best_s = 0.0
    for p, vv in (b.get("levels") or {}).items():
        try:
            pr = float(p)
        except (TypeError, ValueError):
            continue
        buy = _f(vv.get("b")); sell = _f(vv.get("s")); tot = buy + sell
        if tot > best_t:
            best_t = tot; best_p = pr; best_b = buy; best_s = sell
    return best_p, best_t, best_b, best_s


def detect(buckets, walls, skip_last=False):
    n = len(buckets)
    if n < MIN_N + 2 or not walls:
        return []
    try:
        tops = [_top_level(b) for b in buckets]
        out = []
        hi_n = (n - 1) if skip_last else n
        for i in range(MIN_N, hi_n):
            p, tot, buy, sell = tops[i]
            if p is None or tot <= 0:
                continue
            window = [tops[j][1] for j in range(max(0, i - WIN), i) if tops[j][1] > 0]
            if len(window) < MIN_N:
                continue
            med = _median(window)
            if med <= 0 or tot < CRAZY_MULT * med:          # not an outlier vs its neighbours
                continue
            hit = None                                       # the bubble must land in an ACTIVE wall's radar
            for w in walls:
                if w["i0"] <= i <= min(int(w["i1"]), n - 1):
                    band = _f(w.get("band")); wp = _f(w.get("price"))
                    if band > 0 and (wp - RADAR_MULT * band) <= p <= (wp + RADAR_MULT * band):
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
            out.append({"i": i, "price": p, "side": side, "vol": tot,
                        "ratio": tot / med, "kind": kind, "wall_side": wside})
        return out
    except Exception:
        return []
