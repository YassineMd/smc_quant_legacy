"""ORDER-FLOW WALLS — eyeball overlay (m10_absorblvl), ALL timeframes. Absorption + Aggression, strength = ejection.

A wall = where strong one-sided aggression (|net-delta%| >= T) meets its limit. TWO ways a wall forms:
  ABSORPTION (tiny body -> aggressor pushed, price didn't follow = absorbed AT the extreme):
     buy-absorbed  -> HIGH = RESISTANCE (red) ;  sell-absorbed -> LOW = SUPPORT (green)
  AGGRESSION (big body -> aggressor moved FROM a base, leaving an origin / order block):
     sell-aggression -> HIGH = RESISTANCE (red) ;  buy-aggression -> LOW = SUPPORT (green)

STRENGTH (drawn as opacity + thickness) = how far the wall EJECTED price (the favourable excursion after it formed),
DECAYED 0.6x per return-touch (each test consumes its liquidity). LIFETIME = the MARKET decides: a wall lives from
where it formed until price CLOSES through it — no arbitrary age-out. Causal per-bar simulation.

⚠ DESCRIPTIVE ONLY — barely a signal. study/wall_levels.py (vs a random-line placebo, 15m): ABSORPTION 64.1% ==
placebo 63.4% (null); AGGRESSION 66.3% but the direction-shuffle also hits 65.0%, so only ~1.3pp is truly directional
and +3pp on a 63% geometric base does NOT clear the fee. Reads structure; does not predict. Fail-safe: [].

detect(buckets, skip_last=False) ->
  [{price, side('R'|'S'), src('abs'|'agg'|'mix'), i0, i1, strength(0..1), hits, band, radar_runs:[(k0,k1),..]}].
radar_runs = candle spans where price RE-ENTERED the radar area (= the wall + one wall-height above & below).
"""
from __future__ import annotations

from .engulf_sr_detect import _ohlc

T = 20.0            # |net-delta%| = one-sided aggression that can build a wall (do NOT gate hard here — significance is
#                     judged by EJECTION strength downstream, not by how one-sided the candle was)
BODY_SMALL = 0.35  # |close-open|/range <= this = tiny body (absorbed at the extreme)
BODY_BIG = 0.60    # |close-open|/range >= this = decisive move (origin / order-block wall)
EPS = 0.0015       # touch / break tolerance (0.15%)
DECAY = 0.6        # strength multiplier applied per return-touch (each test weakens the wall)
EJ_WIN = 10        # the ejection = favourable excursion within this many bars of formation (the INITIAL rejection)
REF_EJ = 0.010     # ejection that maps to full strength 1.0 (1.0%)


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _wall_at(i, O, C, H, L, DP):
    """Return (price, side, src) if candle i births a wall, else None."""
    rng = H[i] - L[i]
    if rng <= 0 or O[i] <= 0 or abs(DP[i]) < T:
        return None
    body = abs(C[i] - O[i]) / rng
    if body <= BODY_SMALL:                                  # ABSORPTION — wall AT the failed extreme
        return (H[i], "R", "abs") if DP[i] > 0 else (L[i], "S", "abs")
    if body >= BODY_BIG and ((DP[i] > 0) == (C[i] > O[i])):  # AGGRESSION — wall at the move's ORIGIN
        return (L[i], "S", "agg") if DP[i] > 0 else (H[i], "R", "agg")
    return None


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
        active = []; done = []                              # walls: {P, side, src, i0, hits, inzone, broken, i1}
        for i in range(hi_n):
            still = []
            for w in active:
                P = w["P"]
                if w["side"] == "R" and C[i] > P * (1 + EPS):       # MARKET breaks it (close through)
                    w["i1"] = i; w["broken"] = True; done.append(w); continue
                if w["side"] == "S" and C[i] < P * (1 - EPS):
                    w["i1"] = i; w["broken"] = True; done.append(w); continue
                inzone = (H[i] >= P * (1 - EPS)) if w["side"] == "R" else (L[i] <= P * (1 + EPS))
                if inzone and not w["inzone"] and i - w["i0"] > 1:  # price RETURNED to test the wall = a hit
                    w["hits"] += 1; w["inzone"] = True
                elif not inzone:
                    w["inzone"] = False
                still.append(w)
            active = still
            hit = _wall_at(i, O, C, H, L, DP)
            if hit is not None:
                price, side, src = hit
                near = None
                for w in active:
                    if w["side"] == side and abs(w["P"] - price) <= price * EPS * 2:
                        near = w; break
                if near is None:                            # a fresh wall (else it is just a re-touch of an active one)
                    active.append({"P": price, "side": side, "src": src, "i0": i,
                                   "hits": 0, "inzone": True, "broken": False, "i1": None})
                elif near["src"] != src:
                    near["src"] = "mix"
        out = []
        for w in done + active:
            i0 = w["i0"]; i1 = w["i1"] if w["broken"] else (n - 1); P = w["P"]
            k1 = min(i1, i0 + EJ_WIN, n - 1)                # INITIAL rejection: excursion within EJ_WIN bars of birth
            ej = 0.0
            for k in range(i0 + 1, k1 + 1):
                fav = (P - L[k]) / P if w["side"] == "R" else (H[k] - P) / P
                if fav > ej:
                    ej = fav
            strength = min(1.0, ej / REF_EJ) * (DECAY ** w["hits"])
            band = P * (0.0003 + strength * 0.0007)         # half-height of the wall zone (visual, matches the terminal)
            r_lo = P - 3.0 * band; r_hi = P + 3.0 * band     # radar area = wall + one wall-height above + below
            # RADAR visit-runs: each contiguous stretch where a bar RE-ENTERS the radar area (price left, then returned)
            runs = []; in_r = False; ever_left = False; rs = 0; counts = False
            for k in range(i0, i1 + 1):
                if L[k] <= r_hi and H[k] >= r_lo:
                    if not in_r:
                        in_r = True; rs = k; counts = ever_left    # a run counts only if price had left the area first
                else:
                    ever_left = True
                    if in_r:
                        in_r = False
                        if counts:
                            runs.append((rs, k - 1))
            if in_r and counts:
                runs.append((rs, i1))
            out.append({"price": P, "side": w["side"], "src": w["src"], "i0": i0, "i1": i1,
                        "strength": strength, "hits": w["hits"], "band": band, "radar_runs": runs})
        return out
    except Exception:
        return []
