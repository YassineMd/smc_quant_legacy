"""Adaptive VPIN tiering — self-calibrating 'toxic' / 'warn' thresholds.

The legacy fixed **0.85** 'toxic' line is DEAD on SOL: the rolling-50 VPIN never exceeds
~0.57 on real data (800 live 1m buckets: p90=0.49, max=0.57), so the crimson tier never
fired and the gold 0.50 warning fired only ~8%. This module replaces the magic constant
with a rolling-window **percentile**: 'toxic' = VPIN in the top decile of the *recent* VPIN
distribution, 'warn' = top quartile — so toxicity is judged RELATIVE to recent conditions
and self-calibrates to whatever range SOL's VPIN actually occupies.

ONE mechanism, shared by every VPIN display site (Mode 6, bucket-canvas heatmap, hover
readout, selection box) so 'toxic' means the same thing everywhere. Percentile (not z-score
or median-band) is chosen because it is shape-agnostic: the rolling-50 VPIN is tight and
near-symmetric while the per-bucket / selection VPIN is right-skewed, and a single percentile
rule fits both without per-site tuning.

Pure (no Qt / pyqtgraph) so it stays unit-testable; callers map the returned tier
(``TOXIC`` | ``WARN`` | ``NORMAL``) to their own colours.
"""
from __future__ import annotations

from . import config

TOXIC, WARN, NORMAL = "toxic", "warn", "normal"


def rolling_vpin(buckets: list, n: int = config.VPIN_WINDOW) -> list:
    """Trailing-``n`` VPIN per bucket: ``Σ|buy-sell| / Σcurr_vol`` over the last ``n`` buckets
    — the exact quantity the Mode-6 / bucket-canvas panes plot."""
    out = []
    for i in range(len(buckets)):
        w = buckets[max(0, i - (n - 1)): i + 1]
        imb = sum(abs(b.get("buy_vol", 0.0) - b.get("sell_vol", 0.0)) for b in w)
        vol = sum(b.get("curr_vol", 0.0) for b in w)
        out.append(imb / vol if vol > 0 else 0.0)
    return out


def window_vpin_samples(buckets: list, n: int, target_vol: float) -> list:
    """VPIN of every ``n``-length window over ``buckets`` (the selection's own formula,
    ``Σ|buy-sell| / (n·target_vol)``) — the same-length-window baseline a selection of ``n``
    buckets is ranked against, so the comparison is apples-to-apples regardless of size."""
    if n <= 0 or target_vol <= 0 or len(buckets) < n:
        return []
    out = []
    for i in range(n, len(buckets) + 1):
        w = buckets[i - n:i]
        imb = sum(abs(b.get("buy_vol", 0.0) - b.get("sell_vol", 0.0)) for b in w)
        out.append(imb / (n * target_vol))
    return out


def _percentile(sorted_xs: list, p: float) -> float:
    """Linear-interpolated percentile (matches numpy 'linear'); ``sorted_xs`` non-empty."""
    if len(sorted_xs) == 1:
        return sorted_xs[0]
    rank = (p / 100.0) * (len(sorted_xs) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_xs) - 1)
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (rank - lo)


def vpin_cutpoints(samples) -> tuple:
    """``(warn_cut, toxic_cut)`` = the WARN / TOXIC percentiles of ``samples`` (the recent
    VPIN distribution), or ``(None, None)`` when there are too few samples (warm-up) — callers
    then treat everything as NORMAL rather than inventing a toxic tier from thin data."""
    xs = sorted(v for v in samples if v is not None)
    if len(xs) < config.VPIN_ADAPT_MIN:
        return None, None
    return _percentile(xs, config.VPIN_WARN_PCTL), _percentile(xs, config.VPIN_TOXIC_PCTL)


def vpin_tier(value: float, warn_cut, toxic_cut) -> str:
    """``TOXIC`` | ``WARN`` | ``NORMAL`` for ``value`` vs the cutpoints. ``None`` cutpoints
    (warm-up) → always ``NORMAL``."""
    if toxic_cut is not None and value >= toxic_cut:
        return TOXIC
    if warn_cut is not None and value >= warn_cut:
        return WARN
    return NORMAL
