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
STRONG_CIR, STRONG_WICK, STRONG_DS = 0.65, 0.35, 8.0        # gold tier


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


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
                strong = cir >= STRONG_CIR and lw >= STRONG_WICK and ds >= STRONG_DS
                out.append({"i": i, "side": 1, "tier": "gold" if strong else "normal", "strong": strong})
            elif H[i] >= max(H[i - LB:i]) and (H[i] - C[i]) / rng >= CIR and uw >= WICK_MIN and C[i] < O[i] and ds <= -DS:  # TOP
                strong = (H[i] - C[i]) / rng >= STRONG_CIR and uw >= STRONG_WICK and ds <= -STRONG_DS
                out.append({"i": i, "side": -1, "tier": "gold" if strong else "normal", "strong": strong})
        return out
    except Exception:
        return []
