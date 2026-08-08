"""1h REVERSAL detector — EARLY / PREDICTIVE (m10_reversal overlay, 1h only).

Fires ON candle 3 (the CURRENT, last-printed candle) — NOT after a confirmation. Decides from candles 1,2,3 only
(fully causal): candle 3 makes a FRESH local extreme AND prints as a rejection/absorption candle. No look-ahead, no
lag — the mark lands on the candle that is (predicted to be) the swing pivot itself.

Validated PREDICTIVELY in study/reversal_predict_1h.py (candidate = fresh LB-bar extreme; outcome = holds + reverses
>=0.6% within 6 bars, used only to SCORE, never as a feature). What predicts a reversal at candle 3's close:
  c3 close-in-range (hammer, AUC 0.78) >> lower/upper wick (0.69) > candle turned (0.67) > delta absorbs / steps in
  (0.66) > delta-shift vs the 2 approach candles (0.64). The old 'wide range' signal was a LOOK-AHEAD artifact (the
  pivot bar IS the extreme) and does NOT predict (AUC ~0.53) — so range is NOT used here.

  side +1  green lozenge BELOW a BOTTOM  — fresh low + closes in the upper CIR of its range + lower wick + turned
                                           bullish + buyers step in (delta-shift up).
  side -1  red   lozenge ABOVE a TOP     — mirror.
  STRONG (gold ring): tighter hammer + a bigger flow flip.

Calibrated PRECISION ~39% (2x the 18% base) at hitting an actual reversal — regime-STABLE (2025 39% / 2026 40%),
catching ~25% of reversals IN REAL TIME. Early detection is inherently ~40% (most fresh-low hammers still break down);
this is a heads-up marker, NOT a proven edge. Fail-safe: [] on any error.

detect(buckets, skip_last=True) -> [{i, side(+1/-1), tier('gold'|'normal'), strong}]  (i = candle 3 = the pivot candle).
"""
from __future__ import annotations

from .engulf_sr_detect import _ohlc                        # parity-verified OHLC accessor (open_price/close_price safe)

LB = 6                  # candle 3 must be the extreme over the last LB bars (a FRESH low / high)
CIR = 0.55              # candle 3 closes in the favourable CIR of its OWN range (hammer / rejection close)
WICK_MIN = 0.25         # candle 3 rejection wick as a fraction of its range
DS = 3.0                # delta shift: candle-3 delta% minus the mean of the 2 approach candles (buyers/sellers step in)
# GOLD tier = (a) CHOPPY approach into the extreme (<= STRONG_RUN_MAX consecutive same-dir candles, not a straight
# crash) AND (b) a CAPITULATION flush — >= STRONG_SELLCONC% of the candle's SELLING (bottom) / BUYING (top) dumped in
# the extreme third (footprint). Both robust across tf + years (study/reversal_longwindow_15m + reversal_footprint):
# hammer 37-39% -> +run_down 40-42% -> +sellconc ~44% both tf. (The footprint's other reads were redundant w/ the hammer.)
STRONG_RUN_MAX = 2
STRONG_SELLCONC = 40.0
RUN_CAP = 6             # look back at most this far for the streak


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _run(C, O, i, down, cap=RUN_CAP):
    """Consecutive same-direction candles ending at i-1 (the streak INTO the extreme), capped at `cap`."""
    r = 0
    for k in range(i - 1, max(-1, i - 1 - cap), -1):
        if (C[k] < O[k]) if down else (C[k] > O[k]):
            r += 1
        else:
            break
    return r


def _conc(b, hi, lo, down):
    """Capitulation flush from the footprint: fraction (%) of SELL vol in the bottom third (down) / BUY vol in the
    top third (up). High => the losing side dumped INTO the extreme and got absorbed. 0 if no footprint."""
    lv = b.get("levels") or {}
    rng = hi - lo
    if not lv or rng <= 0:
        return 0.0
    thr = (lo + rng / 3.0) if down else (hi - rng / 3.0)
    seg = tot = 0.0
    for ps, vv in lv.items():
        try:
            p = float(ps)
        except (TypeError, ValueError):
            continue
        v = _f(vv.get("s")) if down else _f(vv.get("b"))
        tot += v
        if (p <= thr) if down else (p >= thr):
            seg += v
    return (seg / tot * 100.0) if tot > 0 else 0.0


def detect(buckets, skip_last=True):
    n = len(buckets)
    if n < LB + 2:
        return []
    try:
        O = [0.0] * n; C = [0.0] * n; H = [0.0] * n; L = [0.0] * n; DP = [0.0] * n
        for i, b in enumerate(buckets):
            O[i], C[i], H[i], L[i] = _ohlc(b)
            cv = _f(b.get("curr_vol"))
            if cv > 0:
                DP[i] = (_f(b.get("buy_vol")) - _f(b.get("sell_vol"))) / cv * 100.0
        hi_n = (n - 1) if skip_last else n
        out = []
        for i in range(LB, hi_n):
            rng = H[i] - L[i]
            if rng <= 0 or O[i] <= 0:
                continue
            cir = (C[i] - L[i]) / rng                            # close position in candle 3's own range
            lw = (min(O[i], C[i]) - L[i]) / rng                  # lower wick fraction
            uw = (H[i] - max(O[i], C[i])) / rng                  # upper wick fraction
            ds = DP[i] - (DP[i - 2] + DP[i - 1]) / 2.0           # flow shift at candle 3 vs the 2 approach candles
            if L[i] <= min(L[i - LB:i]) and cir >= CIR and lw >= WICK_MIN and C[i] > O[i] and ds >= DS:       # BOTTOM
                strong = _run(C, O, i, True) <= STRONG_RUN_MAX and _conc(buckets[i], H[i], L[i], True) >= STRONG_SELLCONC
                out.append({"i": i, "side": 1, "tier": "gold" if strong else "normal", "strong": strong})
            elif H[i] >= max(H[i - LB:i]) and (H[i] - C[i]) / rng >= CIR and uw >= WICK_MIN and C[i] < O[i] and ds <= -DS:  # TOP
                strong = _run(C, O, i, False) <= STRONG_RUN_MAX and _conc(buckets[i], H[i], L[i], False) >= STRONG_SELLCONC
                out.append({"i": i, "side": -1, "tier": "gold" if strong else "normal", "strong": strong})
        return out
    except Exception:
        return []
