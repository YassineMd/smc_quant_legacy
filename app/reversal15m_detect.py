"""15m REVERSAL detector — EARLY / PREDICTIVE (Indicator overlay, m10_reversal15, 15m only).

Fires ON candle 3 (the current, last-CLOSED candle) — decided from candles 1,2,3 only (fully causal), NO look-ahead,
NO waiting for a ZigZag confirmation. The mark lands on the candle that is (predicted to be) the swing pivot itself.

Validated PREDICTIVELY in study/reversal_predict_15m.py (candidate = fresh LB-bar extreme; outcome = holds + reverses
>=0.4% within 6 bars, used only to SCORE, never as a feature). What predicts a reversal at candle 3's close:
  c3 close-in-range/hammer (AUC 0.78) >> wick (0.68) > candle turned (0.68) > delta absorbs/steps-in (0.64) >
  delta-shift (0.63). RANGE does NOT predict (0.54) — the old 'wide range' was a look-ahead artifact (pivot bar IS the
  extreme), so it is not used.

  green ▲ (below) = BOTTOM  — fresh low + closes in the upper CIR of its range + lower wick + turned bullish +
                              buyers step in (delta-shift up).
  red   ▼ (above) = TOP     — mirror.
  STRONG (bigger) = tighter hammer + a bigger flow flip.

Calibrated PRECISION ~37% (2x the 17% base) at hitting an actual reversal — regime-STABLE (2025 37% / 2026 38%),
in REAL TIME on the candle itself. Early detection is inherently ~37% (most fresh-low hammers still break down) —
a heads-up marker, NOT a proven edge. Fail-safe: [] on any error.

detect(buckets) -> [{i, side('top'|'bottom'), price, strong}]  (i = candle 3 = the pivot candle; the forming last bar is skipped).
"""
from __future__ import annotations

LB = 6                  # candle 3 must be the extreme over the last LB bars (a FRESH low / high)
CIR = 0.55              # candle 3 closes in the favourable CIR of its OWN range (hammer / rejection close)
WICK_MIN = 0.25         # candle 3 rejection wick as a fraction of its range
DS = 3.0                # delta shift: candle-3 delta% minus the mean of the 2 approach candles (buyers/sellers step in)
# STRONG (bigger) tier = the approach into the extreme was CHOPPY (<= this many consecutive same-direction candles),
# not a straight-down/up crash. Robust both tf + both years: choppy approach reverses ~41% vs a straight run ~33%
# (base ~17%). study/reversal_longwindow_15m.py. (The old tighter cir/wick/ds 'strong' did not lift precision.)
STRONG_RUN_MAX = 2
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


def _o(b): return _f(b.get("open", b.get("open_price", 0.0)))
def _c(b): return _f(b.get("close", b.get("close_price", 0.0)))
def _h(b): return _f(b.get("high", 0.0))
def _l(b): return _f(b.get("low", 0.0))


def detect(buckets):
    n = len(buckets)
    if n < LB + 3:
        return []
    try:
        O = [_o(b) for b in buckets]; C = [_c(b) for b in buckets]
        H = [_h(b) for b in buckets]; L = [_l(b) for b in buckets]
        DP = [0.0] * n
        for i in range(n):
            cv = _f(buckets[i].get("curr_vol"))
            if cv > 0:
                DP[i] = (_f(buckets[i].get("buy_vol")) - _f(buckets[i].get("sell_vol"))) / cv * 100.0
        out = []
        for i in range(LB, n - 1):                              # skip the forming last bar; fire on the last CLOSED candle
            rng = H[i] - L[i]
            if rng <= 0 or O[i] <= 0:
                continue
            cir = (C[i] - L[i]) / rng                            # close position in candle 3's own range
            lw = (min(O[i], C[i]) - L[i]) / rng                  # lower wick fraction
            uw = (H[i] - max(O[i], C[i])) / rng                  # upper wick fraction
            ds = DP[i] - (DP[i - 2] + DP[i - 1]) / 2.0           # flow shift at candle 3 vs the 2 approach candles
            if L[i] <= min(L[i - LB:i]) and cir >= CIR and lw >= WICK_MIN and C[i] > O[i] and ds >= DS:        # BOTTOM
                strong = _run(C, O, i, True) <= STRONG_RUN_MAX                # choppy down-approach, not a straight crash
                out.append({"i": i, "side": "bottom", "price": L[i], "strong": strong})
            elif H[i] >= max(H[i - LB:i]) and (H[i] - C[i]) / rng >= CIR and uw >= WICK_MIN and C[i] < O[i] and ds <= -DS:  # TOP
                strong = _run(C, O, i, False) <= STRONG_RUN_MAX
                out.append({"i": i, "side": "top", "price": H[i], "strong": strong})
        return out
    except Exception:
        return []
