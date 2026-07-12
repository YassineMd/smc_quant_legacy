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


def vpin_tiers_from_series(vp: list, adapt: int = config.VPIN_ADAPT_WINDOW) -> tuple:
    """Per-bucket **CAUSAL** adaptive VPIN state from a precomputed rolling-VPIN series ``vp``.

    Each bucket is tiered against ONLY the trailing ``adapt`` VPIN values ENDING AT it (no future data), so its
    tier + threshold **freeze the moment the bucket closes** and never repaint as later buckets shift the recent
    distribution. This is the fix for studying past data: the legacy display tiered every historical bar against
    the LATEST window's cutpoints, so a bar that printed red at time T re-coloured to gray once the window's
    percentiles rose later in the day. Here bar T keeps time-T's threshold forever.

    Returns ``(tiers, toxic_cuts)`` — lists the length of ``vp``; ``toxic_cut`` is ``None`` during warm-up
    (< ``VPIN_ADAPT_MIN`` trailing samples), which callers render as NORMAL / a gap in the threshold line.

    Perf: a rolling sorted window (bisect insert + evict), so it is **byte-identical** to calling ``vpin_cutpoints``
    on each trailing slice — the window at bar i IS ``sorted(vp[i-adapt+1 : i+1])`` and the SAME ``_percentile`` runs
    on it — but O(N·adapt) instead of O(N·adapt·log adapt), i.e. no per-bar re-sort. This is the cost that scaled
    with the loaded window on a Start-Date change; the output is unchanged. (``vp`` must be numeric — it is always
    ``rolling_vpin`` output.)
    """
    import bisect
    from collections import deque
    warn_p = config.VPIN_WARN_PCTL
    tox_p = config.VPIN_TOXIC_PCTL
    mn = config.VPIN_ADAPT_MIN
    tiers = []
    toxics = []
    win = []            # sorted multiset of the trailing-`adapt` values == sorted(vp[i-adapt+1 : i+1])
    order = deque()     # arrival order, for O(1) identity of the value to evict
    for x in vp:
        bisect.insort(win, x)
        order.append(x)
        if len(order) > adapt:
            win.pop(bisect.bisect_left(win, order.popleft()))
        if len(win) < mn:
            tiers.append(NORMAL)
            toxics.append(None)
        else:
            w = _percentile(win, warn_p)
            t = _percentile(win, tox_p)
            tiers.append(vpin_tier(x, w, t))
            toxics.append(t)
    return tiers, toxics


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
