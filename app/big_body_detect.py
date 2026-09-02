"""BIG BODY (m10_bigbody): mark a candle whose BODY (|close - open|) is STRICTLY bigger than the
body of EACH of the last LOOKBACK (5) candles.

Deliberately the SIMPLEST possible size rule (user 2026-09-03) — a max-of-the-last-5 comparison,
no percentiles, no trend segments. Distinct from (and NOT a rebuild of) the removed ꕻ Big Bar
(percentile-vs-EMA-segments, deleted 2026-09-01 on user request) — this one has its own fresh
mandate and rule. Causal: bar i compares only against i-1..i-5, so marks never repaint; the
still-forming last bar is skipped via skip_last. Source-agnostic (volume buckets and clock
candles) through the shared _ohlc reader. A zero body never marks (0 is not > anything)."""

from __future__ import annotations

from .engulf_sr_detect import _ohlc

LOOKBACK = 5


def detect(bars, skip_last: bool = True, lookback: int = LOOKBACK):
    """[{i, side(+1 bull / -1 bear)}] — i in the passed-list index space; needs `lookback` closed
    bars of context, so the first `lookback` bars can never mark."""
    n = len(bars)
    hi = n - 1 if skip_last else n
    bodies = []
    for i in range(n):
        o, c, _h, _l = _ohlc(bars[i])
        bodies.append(abs(c - o))
    out = []
    for i in range(lookback, hi):
        b = bodies[i]
        if b > 0.0 and all(b > bodies[i - j] for j in range(1, lookback + 1)):
            o, c, _h, _l = _ohlc(bars[i])
            out.append({"i": i, "side": 1 if c >= o else -1})
    return out
