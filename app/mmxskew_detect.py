"""MMXSKEW entry detector for the LIVE terminal (1h) — the NO-POC candidate family (2026-07-21).

Causal + self-contained: reuses only app.pivot_detect.eff_causal_share, app.footprint_panel.profile_skewness
and app.config, so it stays import-safe and never touches the pivot subsystem.

BASE (v1.1-NP), per bucket b. The POC-baseline filter was DROPPED after an ablation across every version showed
it adds NO edge (study/MMXSKEW_NOPOC.md) — it only culled ~17% of signals (mostly shorts):
    LONG  = close>open AND skew>0 AND panel-2 NON-LOCKED spread >= +35 AND delta < +15%
    SHORT = close<open AND skew<0 AND spread <= -35

Three NESTED tiers are tagged per signal (each has its OWN terminal toggle / badge style):
    v11  — the base signal itself (always True on a returned row)          -> plain badge
    v12d — v1.2-Dynamic: run_pos <= 4 AND mov_mag_ratio >= 1.25            -> green/red background
           run_pos = consecutive same-side count over the base sequence; mov_mag_ratio = mov_mag /
           trailing-EMA50(mov_mag) with the EMA EXCLUDING the current bucket (causal); mov_mag =
           ((close*100/ref - 100)^2)*100, ref = low(bull) / high(bear) / open(doji).
    v13  — v1.3: mov_mag >= 39 AND raw da2 > 0                             -> gold background
           ASYMMETRIC delta-accel: long = buying ACCELERATING into the close, short = selling
           DECELERATING/absorbed — BOTH pass on raw da2 > 0. da2 = (buy_vol - sell_vol - 2*delta_h1)/curr_vol
           and needs the daemon `delta_h1` field (post-deploy / backfilled buckets); absent -> v13 False.

detect(buckets, rr) -> [{i, side(+1/-1), entry, sl, tp, v11, v12d, v13}]. SL = 0.1% beyond the bucket extreme,
TP = rr * SL distance (rr=1.5 default). Feed the SAME bucket window the terminal renders.
"""
from __future__ import annotations
import numpy as np

from . import config  # noqa: F401  (kept for parity with the sibling overlay modules)
from .pivot_detect import eff_causal_share
from .footprint_panel import profile_skewness

SPREAD_THR = 35.0
DELTA_MAX = 15.0
SL_BUF = 0.001
DEF_RR = 1.5
RUN_MAX = 4          # v1.2-Dynamic
RATIO_MIN = 1.25     # v1.2-Dynamic
MM_MIN_V13 = 39.0    # v1.3


def _oc(b):
    """(open, close) — robust to wire (open/close) and persisted (open_price/close_price) bucket dicts."""
    return (float(b.get("open", b.get("open_price", 0.0)) or 0.0),
            float(b.get("close", b.get("close_price", 0.0)) or 0.0))


def _mov_mag(o, c, h, l):
    ref = l if c > o else (h if c < o else o)
    return ((((c * 100.0) / ref) - 100.0) ** 2) * 100.0 if ref > 0 else 0.0


def detect(buckets: list, rr: float = DEF_RR) -> "list[dict]":
    n = len(buckets)
    if n == 0:
        return []
    spr = (2.0 * np.asarray(eff_causal_share(buckets), float) - 1.0) * 100.0
    # per-bucket mov_mag + its trailing EMA-50 ratio (EMA excludes the current bucket -> causal)
    mm = [0.0] * n; ratio = [1.0] * n; ema = None
    for i in range(n):
        o, c = _oc(buckets[i])
        mm[i] = _mov_mag(o, c, float(buckets[i].get("high", 0.0) or 0.0), float(buckets[i].get("low", 0.0) or 0.0))
        ratio[i] = (mm[i] / ema) if (ema and ema > 0) else 1.0
        ema = mm[i] if ema is None else mm[i] * (2.0 / 51.0) + ema * (1.0 - 2.0 / 51.0)

    out = []; run = 0; prev = 0
    for i in range(n):
        b = buckets[i]
        o, c = _oc(b)
        if o <= 0 or c <= 0:
            continue
        sk = profile_skewness(b.get("levels"))
        if sk is None:
            continue
        cv = float(b.get("curr_vol", 0.0)) or 1.0
        tot = float(b.get("buy_vol", 0.0)) - float(b.get("sell_vol", 0.0))
        delta = tot / cv * 100.0
        if c > o and sk > 0 and spr[i] >= SPREAD_THR and delta < DELTA_MAX:
            s = 1
        elif c < o and sk < 0 and spr[i] <= -SPREAD_THR:
            s = -1
        else:
            continue
        run = run + 1 if s == prev else 1               # run_pos over the BASE-signal sequence
        prev = s
        hi = float(b.get("high", 0.0) or 0.0); lo = float(b.get("low", 0.0) or 0.0)
        sl = lo * (1 - SL_BUF) if s > 0 else hi * (1 + SL_BUF)
        sld = (c - sl) if s > 0 else (sl - c)
        if sld <= 0:
            continue
        dh1 = b.get("delta_h1"); da2 = None             # v1.3 needs the daemon delta_h1 field
        if dh1 is not None and cv > 0:
            da2 = (tot - 2.0 * float(dh1)) / cv
        out.append(dict(i=i, side=s, entry=c, sl=sl, tp=c + rr * sld * s,
                        v11=True,
                        v12d=(run <= RUN_MAX and ratio[i] >= RATIO_MIN),
                        v13=(mm[i] >= MM_MIN_V13 and da2 is not None and da2 > 0)))
    return out
