"""REVERSAL POINT detector — EARLY / PREDICTIVE (m10_reversal overlay, ALL timeframes 1m..4h).

Fires ON candle 3 (the CURRENT, last-printed candle) — NOT after a confirmation. Decides from candles 1,2,3 only
(fully causal): candle 3 makes a FRESH local extreme AND prints as a rejection/absorption candle. No look-ahead, no
lag — the mark lands on the candle that is (predicted to be) the swing pivot itself. The SAME logic + thresholds
validated across 15m / 1h / 4h (study/reversal_predict_1h.py, reversal_predict_15m.py, scratchpad 4h check).

What predicts a reversal at candle 3's close (candidate = fresh LB-bar extreme; outcome = holds + reverses, scoring only):
  c3 close-in-range (hammer, AUC ~0.78) >> lower/upper wick (0.69) > candle turned (0.67) > delta absorbs / steps in
  (0.66) > delta-shift vs the 2 approach candles (0.64). The 'wide range' signal was a LOOK-AHEAD artifact (the pivot
  bar IS the extreme) and does NOT predict (AUC ~0.53) — so range is NOT used here.

  side 'bottom'  green ▲ BELOW a swing low  — fresh low + closes in the upper CIR of its range + lower wick + turned
                                              bullish + buyers step in (delta-shift up).
  side 'top'     red   ▼ ABOVE a swing high — mirror.
  STRONG (bigger): candle 3 also ENGULFS the prior candle (classic body-engulf — its body swallows the opposite-
                   coloured prior body). Lifts precision both years on every tf; beats the old run+sellconc tier on 1h/4h.

Calibrated PRECISION ~37-41% (2x the ~18% base), regime-STABLE both years; strong (engulf) tier ~43-52%. Early
detection is inherently ~40% (most fresh-low hammers still break down) — a heads-up marker, NOT a proven edge. Fail-safe: [].

detect(buckets, skip_last=True) -> [{i, side('top'|'bottom'), price, strong}]  (i = candle 3 = the pivot candle).
"""
from __future__ import annotations

from .engulf_sr_detect import _ohlc                        # parity-verified OHLC accessor (open_price/close_price safe)

LB = 6                  # candle 3 must be the extreme over the last LB bars (a FRESH low / high)
CIR = 0.55              # candle 3 closes in the favourable CIR of its OWN range (hammer / rejection close)
WICK_MIN = 0.25         # candle 3 rejection wick as a fraction of its range
DS = 3.0                # delta shift: candle-3 delta% minus the mean of the 2 approach candles (buyers/sellers step in)
# STRONG tier = candle 3 also ENGULFS the prior candle (classic body-engulf: opposite-coloured prior candle, c3 body
# swallows the prior body). study/reversal_engulf.py: lifts precision both years on every tf (hammer 15m 36->43% /
# 1h 39->49% / 4h 42->52%) and beats the old run_down<=2 & sellconc>=40 footprint tier on 1h/4h (where it added ~0).


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _engulf(O, C, i, down):
    """Classic body-engulf: candle i's body swallows the OPPOSITE-coloured prior candle's body (bullish engulf at a
    bottom / bearish engulf at a top). The one incremental filter that lifts precision both years on every tf."""
    if down:
        return C[i - 1] < O[i - 1] and O[i] <= C[i - 1] and C[i] >= O[i - 1]
    return C[i - 1] > O[i - 1] and O[i] >= C[i - 1] and C[i] <= O[i - 1]


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
                out.append({"i": i, "side": "bottom", "price": L[i], "strong": _engulf(O, C, i, True)})
            elif H[i] >= max(H[i - LB:i]) and (H[i] - C[i]) / rng >= CIR and uw >= WICK_MIN and C[i] < O[i] and ds <= -DS:  # TOP
                out.append({"i": i, "side": "top", "price": H[i], "strong": _engulf(O, C, i, False)})
        return out
    except Exception:
        return []
