"""1h NY Range-break — live overlay detector (1h ONLY).

Range = the NY-session 2-5pm window (13:00-16:00 UTC) 1h candles:
  rhi/rlo = the highest / lowest BODY edge (max/min of open & close over the window) ; whi/wlo = wick extremes (stop).
Break = the FIRST 1h candle after 5pm (16:00-21:00 UTC, SAME UTC weekday) whose CLOSE pierces the range:
  close > rhi -> LONG  (badge 'brB', green) ; close < rlo -> SHORT (badge 'brS', red).

Validated in-sample (SHORT side passed a random-timed-short null, P=0.003): enter at the break close,
SL 0.1% beyond the range WICK, TP = 1/2 the range distance (0.5 x (whi-wlo)).  Weekends excluded.  The study
edge used a 15m entry candle; this 1h-native overlay marks the first 1h close beyond the range instead.

detect() returns one dict per weekday that forms a valid range (the break is optional -> side=0 / break_i=None
so the range box can be drawn before price breaks).  Fail-safe: [] on any error.
"""
from __future__ import annotations
import math
from datetime import datetime, timezone

R0, R1, BRK_END = 13, 16, 21     # UTC hours: NY open / 5pm Morocco / NY close
SL_PAD = 0.001                   # SL 0.1% beyond the range wick
TP_RANGE = 0.5                   # TP = 1/2 the range distance (whi - wlo)

# Breakout-bar STRENGTH calibration — (mean, std) of each dim over the recon breaks, per timeframe. Used for an
# ABSOLUTE 0-100 score (window-independent) so a single break still gets a real strength. Dims: candle range %,
# aligned delta %, aligned effort/result.  study/ny_range_break.py breakout-bar split.
_CAL = {True:  ((0.720, 0.329), (12.43, 14.02), (7375.0, 5093.0)),      # hourly_range=True  -> 15m
        False: ((1.511, 0.721), (8.74, 9.18), (13341.0, 7813.0))}       # hourly_range=False -> 1h

# Break-side probability = logistic on the 2-5pm clock-hour candle directions (close vs open of hours 13/14/15 UTC).
# Fitted in-sample on the 1h recon breaks (n=307, 75.9% acc, well-calibrated): later hours dominate (4-5pm >> 3-4pm >>
# 2-3pm), matching the momentum finding. z = B0 + sum(Bh * dir_h); P(brB) = sigmoid(z). study/ny_break_34pm.py.
_PB0, _PB13, _PB14, _PB15 = -0.058, 0.346, 0.62, 1.252


def _c(b):
    return float(b.get("close", b.get("close_price", 0.0)) or 0.0)


def _o(b):
    return float(b.get("open", b.get("open_price", 0.0)) or 0.0)


def _h(b):
    return float(b.get("high", 0.0) or 0.0)


def _l(b):
    return float(b.get("low", 0.0) or 0.0)


def _strength_feats(b, side):
    """Raw breakout-bar strength dims (the validated Stats-box params): candle range %, aligned delta %, aligned E/R.
    A strong break = a big-range candle with aggressive flow in the trade direction. Percentile-ranked in detect()."""
    o = _o(b); c = _c(b); h = _h(b); l = _l(b)
    size = ((h - l) / c * 100.0) if c > 0 else 0.0
    bv = float(b.get("buy_vol", 0.0) or 0.0); sv = float(b.get("sell_vol", 0.0) or 0.0)
    cv = float(b.get("curr_vol", 0.0) or 0.0) or 1.0
    delta = (bv - sv) / cv * 100.0 * side
    er = float((b.get("buyer_er") if side > 0 else b.get("seller_er")) or 0.0)
    return size, delta, er


def detect(buckets, hourly_range=False):
    """[{i0, i1, rhi, rlo, whi, wlo, side, break_i, entry, sl, tp}] per weekday NY range. Fail-safe: [].

    Range = highest / lowest BODY edge (max/min of open & close) over the 2-5pm window.
    hourly_range=False (1h chart): taken over the 2-5pm buckets directly.
    hourly_range=True  (15m chart): each CLOCK HOUR (13/14/15 UTC) is first collapsed to a synthetic candle
    (open = first sub-bar's open, close = last sub-bar's close) and the body edges are taken over those 3 hours — a
    narrow, 1h-like range reconstructed from the sub-hour buckets. whi/wlo (the stop reference) are tf-invariant.
    """
    n = len(buckets)
    if n < 3:
        return []
    try:
        days = {}                                             # UTC date -> [(hour, bar_index)] in time order
        for i, b in enumerate(buckets):
            st = float(b.get("start_time", 0.0) or 0.0)
            if st <= 0:
                continue
            t = datetime.fromtimestamp(st, tz=timezone.utc)
            if t.weekday() >= 5:                              # weekdays only
                continue
            days.setdefault(t.date(), []).append((t.hour, i))
        out = []
        for d, lst in days.items():
            rng = [(hh, i) for (hh, i) in lst if R0 <= hh < R1]   # 2-5pm candles form the range
            if len(rng) < 2:
                continue
            i0 = rng[0][1]; i1 = rng[-1][1]
            whi = max(_h(buckets[i]) for _, i in rng); wlo = min(_l(buckets[i]) for _, i in rng)   # wick extremes (stop)
            if hourly_range:                                  # 15m chart: reconstruct each clock hour's candle -> its body edges
                hours = {}                                    # hour -> [open_of_first_bar, close_of_last_bar]  (rng is time-ordered)
                for hh, i in rng:
                    if hh not in hours:
                        hours[hh] = [_o(buckets[i]), _c(buckets[i])]
                    hours[hh][1] = _c(buckets[i])
                if len(hours) < 2:
                    continue
                rhi = max(max(oc) for oc in hours.values()); rlo = min(min(oc) for oc in hours.values())
            else:                                             # 1h chart: highest / lowest BODY edge (open or close) directly
                rhi = max(max(_o(buckets[i]), _c(buckets[i])) for _, i in rng)
                rlo = min(min(_o(buckets[i]), _c(buckets[i])) for _, i in rng)
            if not (rhi > rlo) or not (whi > wlo):
                continue
            hc = {}                                           # clock hour -> [open_of_first_bar, close_of_last_bar]
            for hh, i in rng:
                if hh not in hc:
                    hc[hh] = [_o(buckets[i]), _c(buckets[i])]
                hc[hh][1] = _c(buckets[i])
            hd = {}                                           # per-hour direction (close vs open), 0 if the hour is absent
            for h in (13, 14, 15):
                v = hc.get(h)
                hd[h] = 0 if not v else (1 if v[1] > v[0] else (-1 if v[1] < v[0] else 0))
            z = _PB0 + _PB13 * hd[13] + _PB14 * hd[14] + _PB15 * hd[15]
            prob_up = 1.0 / (1.0 + math.exp(-z))              # P(brB); brS = 1 - prob_up
            side = 0; bi = None; entry = sl = tp = 0.0; _szr = _dlr = _err = 0.0
            for (hh, j) in lst:                               # first 1h close beyond the range, 5pm..NY close
                if j <= i1 or not (R1 <= hh < BRK_END):
                    continue
                c = _c(buckets[j])
                if c > rhi:
                    side = 1
                elif c < rlo:
                    side = -1
                else:
                    continue                                  # inside the range -> keep scanning
                bi = j; entry = c
                sl = wlo * (1 - SL_PAD) if side > 0 else whi * (1 + SL_PAD)
                tp = entry + side * TP_RANGE * (whi - wlo)
                _szr, _dlr, _err = _strength_feats(buckets[j], side)   # raw strength dims of the breakout bar
                break
            out.append(dict(i0=i0, i1=i1, rhi=rhi, rlo=rlo, whi=whi, wlo=wlo, side=side, break_i=bi,
                            entry=entry, sl=sl, tp=tp, prob_up=prob_up, _szr=_szr, _dlr=_dlr, _err=_err))
        # STRENGTH 0-100 (ABSOLUTE, window-independent): z-score each breakout-bar dim vs its recon reference (mean,std),
        # sum, standardise, and map through the normal CDF -> spreads 0-100 for ANY number of breaks (even one). No
        # window ranking, no >=N fallback. strong = >=50 -> gold tier.
        (ms, ss), (md, sd), (me, se) = _CAL[bool(hourly_range)]
        for r in out:
            if r.get("break_i") is None or r["side"] == 0:
                r["strength"] = None; r["strong"] = False; continue
            zc = ((r["_szr"] - ms) / ss + (r["_dlr"] - md) / sd + (r["_err"] - me) / se) / 1.7320508   # sum / sqrt(3)
            st = int(round(50.0 * (1.0 + math.erf(zc / 1.4142136))))        # normal CDF x100
            r["strength"] = max(1, min(99, st)); r["strong"] = st >= 50
        return out
    except Exception:
        return []
