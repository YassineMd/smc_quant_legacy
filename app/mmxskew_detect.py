"""MMXSKEW entry detector for the LIVE terminal (1h) — the NO-POC candidate family (2026-07-21).

Causal + self-contained: reuses only app.pivot_detect.eff_causal_share, app.footprint_panel.profile_skewness
and app.config, so it stays import-safe and never touches the pivot subsystem.

BASE (v1.1-NP), per bucket b. The POC-baseline filter was DROPPED after an ablation across every version showed
it adds NO edge (study/MMXSKEW_NOPOC.md) — it only culled ~17% of signals (mostly shorts):
    LONG  = close>open AND skew>0 AND panel-2 NON-LOCKED spread >= +35 AND delta < +15%
    SHORT = close<open AND skew<0 AND spread <= -35

Three NESTED tiers are tagged per signal (each has its OWN terminal toggle / badge style):
    v11  — the base signal itself (always True on a returned row)          -> plain badge
    v12d — v1.2-Dynamic: run_pos <= 4 AND mov_mag_ratio >= 1.30            -> green/red background
           run_pos = consecutive same-side count over the base sequence; mov_mag_ratio = mov_mag /
           trailing-EMA50(mov_mag) with the EMA EXCLUDING the current bucket (causal); mov_mag =
           ((close*100/ref - 100)^2)*100, ref = low(bull) / high(bear) / open(doji).
    v13  — v1.3: mov_mag >= 39 AND raw da2 > 0                             -> gold background
           ASYMMETRIC delta-accel: long = buying ACCELERATING into the close, short = selling
           DECELERATING/absorbed — BOTH pass on raw da2 > 0. da2 = (buy_vol - sell_vol - 2*delta_h1)/curr_vol
           and needs the daemon `delta_h1` field (post-deploy / backfilled buckets); absent -> v13 False.

detect(buckets, rr) -> [{i, side(+1/-1), entry, sl, tp, v11, v12d, v13}]. SL = 0.1% beyond the bucket extreme,
TP = rr * SL distance (rr=1.5 default).

*** NO TRADE SEQUENCING HERE. *** detect() returns EVERY qualifying signal independently. The frozen study
baselines are NOT computed that way: study taken()/walk() enforce a non-overlap rule with a declared same-bucket
re-entry convention — `if sg["i"] <= last: continue`, where `last` is the PRIOR TRADE'S EXIT BAR, i.e. a signal
firing on the bucket in which the previous trade exited is SKIPPED. That rule is load-bearing (it removes ~11%
of signals, all post-take-profit same-side re-loads) and it is audited/hard-locked — see study/MMXSKEW_NOPOC.md
"Execution contract". Because nothing in this module or its callers implements it, any live/forward execution
built on these badges MUST enforce it explicitly, or the forward tape will accumulate trades the freeze never
priced and the audit will diverge while every baseline guard still passes.

TWO CALLER CONTRACTS, both required for the emitted gate to equal the FROZEN study gate:
  1. WARM-UP — prepend >= WARMUP_MIN buckets of history (see below); indices come back in that extended space.
  2. CLOSED-ONLY — `skip_last` controls whether the final element is evaluated, and the caller MUST set it to
     match what it actually passes:
        skip_last=True  (default) — the list ENDS WITH A STILL-FORMING bucket. It is dropped, mirroring the
                        study's `range(first, len(A) - 1)`, so a badge appears only once its bucket has closed
                        and never repaints. This is the live draw path (the terminal appends the active bucket).
        skip_last=False — the list is CLOSED BUCKETS ONLY, so the final element is a legitimate signal bar.
                        This is the audio-alert path (`closed[-400:]`) and REPLAY (which never appends an
                        active bucket). Getting this wrong silently suppresses the newest signal.
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
RATIO_MIN = 1.30     # v1.2-Dynamic — MUST equal study/mm_skew_v12d_validate.T_OPT (re-frozen 1.25->1.30
                     # on 2026-07-21: identical trade set, but immune to data jitter that flips 1.25 36-52%
                     # of the time; 1.30016 = sqrt(1.24716*1.35539), the midpoint of the two material cliffs)
MM_MIN_V13 = 39.0    # v1.3

# detect() is WINDOW-SENSITIVE: the EMA-50, run_pos and eff_causal_share all restart at index 0 of whatever
# list it is handed, while the frozen study always evaluates against full history. Feeding it a short window
# therefore produces a DIFFERENT gate than the registered one. Measured on the 174 in-sample base signals,
# Pre-fix (no prefix at all) this cost 14/174 wrong v1.2-Dynamic verdicts at the real now-24h window — 10
# false-positive, 4 false-negative, the over-firing bias coming from a truncated window also UNDERSTATING
# run_pos. Sufficiency swept over warm-up length x scan anchor (wrong/174, anchors 1h/3h/6h/12h/24h/48h/168h):
#     100 -> 1,0,1,1,1,1,0    150 -> 1,1,1,0,0,0,0    200 -> 0,1,1,1,0,0,0
#     250 -> 0,0,0,0,0,0,0    300/400/500 -> all 0
# 250 is the FIRST length exact at every anchor, so that is the value. Not higher: detect() runs on the 20Hz
# GUI thread (GUI_TIMER_MS=50) and cost is linear in list length — 41-bucket window alone ~2.9ms, +250 warm
# ~14ms (29% of a frame), +500 warm ~27ms (55%). 500 doubles the cost to buy nothing measurable.
# Callers MUST prepend >= WARMUP_MIN buckets of history and discard entries landing in the prefix.
WARMUP_MIN = 250


def _oc(b):
    """(open, close) — robust to wire (open/close) and persisted (open_price/close_price) bucket dicts."""
    return (float(b.get("open", b.get("open_price", 0.0)) or 0.0),
            float(b.get("close", b.get("close_price", 0.0)) or 0.0))


def _mov_mag(o, c, h, l):
    ref = l if c > o else (h if c < o else o)
    return ((((c * 100.0) / ref) - 100.0) ** 2) * 100.0 if ref > 0 else 0.0


def detect(buckets: list, rr: float = DEF_RR, skip_last: bool = True) -> "list[dict]":
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

    # CLOSED BUCKETS ONLY (skip_last=True). A partial bucket's mov_mag/skew/delta are not the closed bucket's —
    # measured against 1m-reconstructed partials, the v1.2-Dynamic verdict differs from the final one on 25% of
    # signals at 25% formation, 21% at 50% and 16% at 75%, so badging it repaints (a signal can appear then
    # vanish). The frozen study never evaluates it either — every study build() loops `range(first, len(A)-1)`.
    # Safe for the earlier bars because eff_causal_share is genuinely causal (verified: appending a bucket
    # changes no earlier share by >0.0) and the trailing EMA excludes the current bucket, so the dropped bar
    # feeds nothing that is emitted. Callers passing a CLOSED-ONLY list must set skip_last=False or the newest
    # signal is silently swallowed.
    out = []; run = 0; prev = 0
    for i in range(n - 1 if skip_last else n):
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
