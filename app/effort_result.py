"""EFFORT vs RESULT per candle (user 2026-08-23: "strong delta, weak result — we have no metric that calculates that").
How much price a candle's NET DELTA actually bought, versus what that much delta NORMALLY buys (trailing baseline).
Causal, per bar, clock or bucket candles. Pure readability — the stats-box Absorb R (z-residual) and the Strength
'abs' quadrant already FLAG absorption; this puts a MAGNITUDE on it in ticks.

  side       = sign of the bar's net delta (buy_vol - sell_vol)
  result_t   = (close - open) in ticks, SIGNED by side  (+ = price moved the aggressor's way)
  exc_t      = the excursion the effort reached before rejection: (high - open) for net buying, (open - low) for net
               selling, in ticks
  expected_t = |delta| x median over the previous WIN bars of ( |close-open| ticks / |delta| ), using only bars whose
               |delta| is a meaningful fraction of their volume (MIN_DELTA_FRAC) so near-zero-delta bars can't blow the
               ratio up
  eff        = result_t / expected_t   -> <= ABSORBED_MAX 'ABSORBED' (big effort, little result) · >= EASY_MIN 'EASY'
               (little effort, big result) · else 'normal'
  retention  = result_t / exc_t (how much of the excursion was KEPT at the close)

compute(buckets, i, win=WIN) -> dict(result_t, exc_t, delta, expected_t, eff, retention, label, tpk) | None.
`tpk` = the baseline in ticks per 10K delta. Fail-safe: None on any error / no delta / too little history.
NOT a tested edge (absorpR scored AUC 0.46-0.48 vs the 0.5% outcome on 2/6 tfs): descriptive only."""
from __future__ import annotations
from . import config

WIN = 30               # trailing bars for the ticks-per-delta baseline
MIN_OBS = 10           # need this many meaningful-delta bars in the window, else no baseline (eff None)
MIN_DELTA_FRAC = 0.02  # a bar counts toward the baseline only if |delta| >= 2% of its volume
ABSORBED_MAX = 0.35    # eff at/below -> ABSORBED
EASY_MIN = 1.5         # eff at/above -> EASY
TICK = float(getattr(config, "TICK_SIZE", 0.01) or 0.01)


def _f(b, k, alt=None):
    v = b.get(k)
    if v is None and alt is not None:
        v = b.get(alt)
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _oc(b):
    return _f(b, "open", "open_price"), _f(b, "close", "close_price")


def baseline_tpd(buckets, i, win: int = WIN):
    """Median ticks-per-unit-|delta| over buckets[i-win .. i-1] (meaningful-delta bars only). None if < MIN_OBS."""
    ratios = []
    for j in range(max(0, i - win), i):
        b = buckets[j]
        d = abs(_f(b, "buy_vol") - _f(b, "sell_vol")); vol = _f(b, "curr_vol")
        if vol <= 0 or d < MIN_DELTA_FRAC * vol:
            continue
        o, c = _oc(b)
        ratios.append(abs(c - o) / TICK / d)
    if len(ratios) < MIN_OBS:
        return None
    ratios.sort()
    m = len(ratios) // 2
    return ratios[m] if len(ratios) % 2 else 0.5 * (ratios[m - 1] + ratios[m])


def compute(buckets, i, win: int = WIN):
    try:
        b = buckets[i]
        o, c = _oc(b); h = _f(b, "high"); l = _f(b, "low")
        delta = _f(b, "buy_vol") - _f(b, "sell_vol")
        if o <= 0 or c <= 0 or delta == 0.0:
            return None
        side = 1 if delta > 0 else -1
        result_t = side * (c - o) / TICK
        exc_t = ((h - o) if side > 0 else (o - l)) / TICK
        tpd = baseline_tpd(buckets, i, win)
        expected_t = (tpd * abs(delta)) if tpd is not None else None
        eff = (result_t / expected_t) if (expected_t is not None and expected_t > 0) else None
        retention = (result_t / exc_t) if exc_t > 0 else None
        if eff is None:
            label = "n/a"
        elif eff <= ABSORBED_MAX:
            label = "ABSORBED"
        elif eff >= EASY_MIN:
            label = "EASY"
        else:
            label = "normal"
        return dict(result_t=result_t, exc_t=exc_t, delta=delta, expected_t=expected_t, eff=eff,
                    retention=retention, label=label, tpk=(tpd * 10000.0) if tpd is not None else None)
    except Exception:
        return None
