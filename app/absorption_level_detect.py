"""ORDER-FLOW WALLS — eyeball overlay (m10_absorblvl), ALL timeframes. Absorption + Aggression, strength = ejection.

A wall = where strong one-sided aggression (|net-delta%| >= T) meets its limit. TWO ways a wall forms:
  ABSORPTION (tiny body -> aggressor pushed, price didn't follow = absorbed AT the extreme):
     buy-absorbed  -> HIGH = RESISTANCE (red) ;  sell-absorbed -> LOW = SUPPORT (green)
  AGGRESSION (big body -> aggressor moved FROM a base, leaving an origin / order block):
     sell-aggression -> HIGH = RESISTANCE (red) ;  buy-aggression -> LOW = SUPPORT (green)

STRENGTH (drawn as opacity) = how far the wall EJECTED price (the favourable excursion after it formed), DECAYED 0.6x
per radar re-visit (each test consumes its liquidity). LIFETIME = the MARKET decides: a wall lives until a candle BODY
CLOSES beyond its RADAR (not merely through the wall) — no arbitrary age-out. Causal per-bar simulation.

⚠ DESCRIPTIVE ONLY — barely a signal. study/wall_levels.py (vs a random-line placebo, 15m): ABSORPTION 64.1% ==
placebo 63.4% (null); AGGRESSION 66.3% but the direction-shuffle also hits 65.0%, so only ~1.3pp is truly directional
and +3pp on a 63% geometric base does NOT clear the fee. Reads structure; does not predict. Fail-safe: [].

detect(buckets, skip_last=False) ->
  [{price, side('R'|'S'), src('abs'|'agg'|'mix'), i0, i1, strength(0..1), hits, band, radar_runs:[(k0,k1),..]}].
radar_runs = candle spans where price RE-ENTERED the radar area (= the wall + one wall-height above & below).
"""
from __future__ import annotations

from math import log1p as _log1p, exp as _exp

from .engulf_sr_detect import _ohlc

T = 20.0            # |net-delta%| = one-sided aggression that can build a wall (do NOT gate hard here — significance is
#                     judged by EJECTION strength downstream, not by how one-sided the candle was)
BODY_SMALL = 0.35  # |close-open|/range <= this = tiny body (absorbed at the extreme)
BODY_BIG = 0.60    # |close-open|/range >= this = decisive move (origin / order-block wall)
EPS = 0.0015       # touch / break tolerance (0.15%)
DECAY = 0.6        # strength multiplier applied per return-touch (each test weakens the wall)
EJ_WIN = 10        # the ejection = favourable excursion within this many bars of formation (the INITIAL rejection)
# GEOMETRY is VOLATILITY-RELATIVE (timeframe-invariant): band / radar / ejection scale with the local candle range
# (vpct = rolling-mean candle-range % over ATR_WIN bars), NOT a fixed % of price — else on 1h/4h the radar is tiny vs
# a candle and every wall breaks instantly. Coefficients tuned to reproduce the 15m geometry (vpct ~0.003 there).
ATR_WIN = 50       # bars for the rolling volatility unit
BAND_MIN = 0.10    # wall half-height as a fraction of the local candle range (weak wall)
BAND_RANGE = 0.233 # ... + this * base (strong wall) -> band = vpct * (0.10..0.333) of price
EJ_ATR_MULT = 3.3  # ejection = this * the local candle range -> full strength 1.0


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


# P(RESIST) = 5-factor logistic on log1p(volume-intensity vr), entry PENETRATION pen, entry CLOSE-position clpos,
# entry BODY (toward the break edge), and EJECTION base (formation shove, 0..1). Evolved from the 2-factor (vr,pen)
# bilinear grid -> +close-position/body (study/wall_entry_close.py) -> +ejection (study/wall_strength_pr.py,
# study/wall_pr_fit5.py). OOS AUC(resist) 0.741/0.740 (4-factor was 0.731/0.725; 2-factor 0.711/0.701); full-sample
# 0.741. Fit on the causal multi-bar visit set both years (10124 visits, base RESIST 70.6%); deciles calibrated
# (slightly conservative at the extremes). vr = box-vol/bar / rolling-median curr_vol; pen/clpos/body from the entry
# candle; ej = the wall's formation ejection (the same value used for opacity) — all causal at render time. NOTE b_pen
# is POSITIVE: given the CLOSE, a higher HIGH = bigger rejection wick -> more resist (pen = clpos + wick; the CLOSE
# carries the break signal). Prior-visit DECAY is NULL for hold-prob (opacity only), so only the ejection enters here.
# DESCRIPTIVE odds — NOT a trade signal.
_PR_COEF = (1.44776, -2.69445, 1.06722, -2.11646, -0.71343, 1.80385)   # (b0, ln1p(vr), pen, clpos, body, ej)


def _p_resist(vr, pen, clpos, body, ej):                      # 5-factor logistic -> P(resist) in %
    b = -1.0 if body < -1.0 else (2.0 if body > 2.0 else body)   # guard rare tiny-span body outliers
    e = 0.0 if ej < 0.0 else (1.0 if ej > 1.0 else ej)           # ejection base is bounded [0,1]
    z = (_PR_COEF[0] + _PR_COEF[1] * _log1p(vr if vr > 0.0 else 0.0) + _PR_COEF[2] * pen
         + _PR_COEF[3] * clpos + _PR_COEF[4] * b + _PR_COEF[5] * e)
    z = 30.0 if z > 30.0 else (-30.0 if z < -30.0 else z)     # overflow guard
    return 100.0 / (1.0 + _exp(-z))


def _box_vol_lv(b, r_lo, r_hi):
    """Footprint volume (buy+sell) at levels inside the radar [r_lo, r_hi]."""
    tot = 0.0
    for ps, vv in (b.get("levels") or {}).items():
        try:
            p = float(ps)
        except (TypeError, ValueError):
            continue
        if r_lo <= p <= r_hi:
            tot += _f(vv.get("b")) + _f(vv.get("s"))
    return tot


def _median(vals):
    m = len(vals)
    if not m:
        return 0.0
    s = sorted(vals)
    return s[m // 2] if m % 2 else (s[m // 2 - 1] + s[m // 2]) / 2.0


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
        O = [0.0] * n; C = [0.0] * n; H = [0.0] * n; L = [0.0] * n; DP = [0.0] * n; CV = [0.0] * n
        for i, b in enumerate(buckets):
            O[i], C[i], H[i], L[i] = _ohlc(b)
            cv = _f(b.get("curr_vol")); CV[i] = cv
            if cv > 0:
                DP[i] = (_f(b.get("buy_vol")) - _f(b.get("sell_vol"))) / cv * 100.0
        vpct = [0.0] * n; _s = 0.0                          # rolling-mean candle-range % (the volatility unit)
        for i in range(n):
            _s += (H[i] - L[i]) / C[i] if C[i] > 0 else 0.0
            if i >= ATR_WIN:
                _s -= (H[i - ATR_WIN] - L[i - ATR_WIN]) / C[i - ATR_WIN] if C[i - ATR_WIN] > 0 else 0.0
            vpct[i] = _s / min(i + 1, ATR_WIN)
        hi_n = (n - 1) if skip_last else n
        active = []; done = []            # wall: {P, side, src, i0, ej, inzone, ever_left, runs, broken, i1}
        for i in range(hi_n):
            still = []
            for w in active:
                P = w["P"]
                if i - w["i0"] <= EJ_WIN:                         # formation ejection (freezes after EJ_WIN bars)
                    fav = (P - L[i]) / P if w["side"] == "R" else (H[i] - P) / P
                    if fav > w["ej"]:
                        w["ej"] = fav
                base = min(1.0, w["ej"] / (EJ_ATR_MULT * w["v0"])) if w["v0"] > 0 else 0.0
                band = P * w["v0"] * (BAND_MIN + base * BAND_RANGE)   # volatility-relative -> timeframe-invariant
                r_lo = P - 3.0 * band; r_hi = P + 3.0 * band     # radar area = wall + one wall-height above & below
                if (w["side"] == "R" and C[i] > r_hi) or (w["side"] == "S" and C[i] < r_lo):
                    w["i1"] = i; w["broken"] = True; done.append(w); continue   # BODY CLOSES beyond the RADAR -> broken
                inside = (L[i] <= r_hi and H[i] >= r_lo)          # radar visit tracking
                if inside:
                    if not w["inzone"] and w["ever_left"]:        # fresh re-entry -> a new visit run (a "hit")
                        w["runs"].append([i, i])
                    if w["ever_left"] and w["runs"]:
                        w["runs"][-1][1] = i                      # extend the open run while price stays inside
                    w["inzone"] = True
                else:
                    w["inzone"] = False; w["ever_left"] = True
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
                    active.append({"P": price, "side": side, "src": src, "base_src": src, "mix_bar": -1,
                                   "i0": i, "ej": 0.0, "v0": vpct[i], "inzone": True, "ever_left": False,
                                   "runs": [], "broken": False, "i1": None})
                elif near["src"] != "mix" and near["src"] != src:
                    near["src"] = "mix"; near["mix_bar"] = i   # bar it BECAME mix (for causal src checks)
        out = []
        for w in done + active:
            i0 = w["i0"]; i1 = w["i1"] if w["broken"] else (n - 1); P = w["P"]
            base = min(1.0, w["ej"] / (EJ_ATR_MULT * w["v0"])) if w["v0"] > 0 else 0.0   # ejection sets the geometry
            hits = len(w["runs"])                           # each radar re-visit is a hit
            # do NOT pre-decay for the visit price is CURRENTLY making (inzone) — else a wall vanishes the instant
            # price enters its radar to test it. The decay for this visit lands only once it completes.
            eff = hits - 1 if (not w["broken"] and w.get("inzone") and hits >= 1) else hits
            strength = base * (DECAY ** eff)                # decays with COMPLETED hits -> opacity
            band = P * w["v0"] * (BAND_MIN + base * BAND_RANGE)   # volatility-relative band (timeframe-invariant)
            r_lo = P - 3.0 * band; r_hi = P + 3.0 * band
            runs = []                                            # (k0, k1, P_resist%) — odds the wall holds this visit
            for r in w["runs"]:
                if r[0] > i1:
                    continue
                rk0 = r[0]; rk1 = min(r[1], i1); bars = rk1 - rk0 + 1
                bx = 0.0
                for k in range(rk0, rk1 + 1):
                    bx += _box_vol_lv(buckets[k], r_lo, r_hi)
                rm = _median([c for c in CV[max(0, rk0 - 200):rk0] if c > 0])
                vr = (bx / bars) / rm if (rm > 0 and bars > 0) else 0.0
                span = r_hi - r_lo                            # entry candle geometry within the radar (0..1)
                if span > 0:
                    isR = w["side"] == "R"
                    pen = (H[rk0] - r_lo) if isR else (r_hi - L[rk0])        # depth the HIGH poked in
                    pen = pen / span
                    pen = 0.0 if pen < 0.0 else (1.0 if pen > 1.0 else pen)
                    clpos = ((C[rk0] - r_lo) if isR else (r_hi - C[rk0])) / span   # where it CLOSED (clean penetration)
                    clpos = 0.0 if clpos < 0.0 else (1.0 if clpos > 1.0 else clpos)
                    body = (C[rk0] - O[rk0]) * (1.0 if isR else -1.0) / span       # body oriented toward the break edge
                else:
                    pen = clpos = body = 0.0
                runs.append((rk0, rk1, round(_p_resist(vr, pen, clpos, body, base), 1)))
            out.append({"price": P, "side": w["side"], "src": w["src"], "i0": i0, "i1": i1,
                        "strength": strength, "hits": hits, "band": band, "radar_runs": runs,
                        "base_src": w.get("base_src", w["src"]), "mix_bar": w.get("mix_bar", -1)})
        return out
    except Exception:
        return []
