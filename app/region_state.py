"""Pure region/bucket state helpers — shared by the GUI terminal AND the headless accumulator.

Extracted verbatim from ``terminal.py`` so the Step-5 exhaustion multipliers, the synthetic
aggregate-"bucket" builder, and the selection-state adapter can be imported WITHOUT pulling in
Qt / pyqtgraph (``scripts/pattern_accumulator.py`` runs headless on the VM). No behaviour change:
``terminal.py`` re-imports these under their original names, so every call site is unchanged and
the n==1 == hover-STATE faithfulness still holds.
"""
from __future__ import annotations

import math

from . import bucket_state, config

# ---------------------------------------------------------------------------
# Step 5 — adaptive exhaustion (Mode 3): scale-free, no rigid E/R tiers.
# Exhaustion intensity scales with how anomalous a bucket's E/R is vs its OWN rolling window
# (a z-score), not against absolute cutoffs. exp(K*tanh(z/Zs)) is monotonic in z, centred at 1.0
# (z=0 -> neutral) and BOUNDED to [exp(-K), exp(K)], so a degenerate/flat window can never explode
# it. The params below are statistical-hygiene knobs (window sizes, a CoV floor), not market
# thresholds.
# ---------------------------------------------------------------------------
EXH_WINDOW = 30           # rolling E/R baseline window (buckets)
EXH_MIN_WINDOW = 10       # 0.6/2 warm-up: below this the E/R multiplier is neutral (1.0)
EXH_CV_FLOOR = 0.10       # 0.6/1 coefficient-of-variation floor on the z denominator
EXH_K = math.log(2.0)     # E/R multiplier range exp(+/-K * tanh) = [0.5, 2.0]
EXH_Z_SCALE = 2.0         # z-score (sigma) scale of the smooth tanh ramp
EXH_OI_K = math.log(1.5)  # OI-direction term range [0.667, 1.5]
EXH_OI_SCALE = 0.5        # scale of the (delta_oi / curr_vol) tanh ramp


def _exh_z_mult(window_vals: list, val: float) -> float:
    """Smooth, bounded exhaustion multiplier from the z-score of ``val`` vs a rolling
    ``window_vals`` of recent same-side E/R (DIVERGES FROM LEGACY, Step 5).

    * Cold start (rule 0.6/2): a window shorter than ``EXH_MIN_WINDOW`` returns the NEUTRAL
      multiplier 1.0 — no z-score against an under-filled window.
    * Degenerate denominator (rule 0.6/1): std is floored at a coefficient-of-variation fraction
      of the mean (``EXH_CV_FLOOR*|mean|`` — scale-free, NOT a fixed absolute), and an all-zero
      window falls back to neutral, not div-by-0.
    * ``tanh`` bounds the exponent, so a noisy/flat window can never spike the multiplier beyond
      ``exp(EXH_K)``.
    """
    if len(window_vals) < EXH_MIN_WINDOW:
        return 1.0
    mean = sum(window_vals) / len(window_vals)
    var = sum((v - mean) ** 2 for v in window_vals) / len(window_vals)
    denom = max(var ** 0.5, EXH_CV_FLOOR * abs(mean))
    if denom <= 0:
        return 1.0
    z = (val - mean) / denom
    return math.exp(EXH_K * math.tanh(z / EXH_Z_SCALE))


def exhaustion_mults(buckets: list, i: int) -> "tuple[float, float, float]":
    """(buyer_er_mult, seller_er_mult, oi_mult) for bucket ``i`` (Step 5).

    The two E/R multipliers are the z-score smooth multiplier of each side's E/R against the
    PRECEDING rolling window. The OI-direction term is a smooth, bounded function of the Step-3 net
    ``delta_oi`` ( = (opL+opS) - (clL+clS) ) normalised by volume: exhaustion is AMPLIFIED when OI
    is contracting (positions closing, delta_oi < 0) and DAMPENED when expanding, neutral at 0.
    """
    b = buckets[i]
    win = buckets[max(0, i - EXH_WINDOW):i]
    b_mult = _exh_z_mult([w.get("buyer_er", 0.0) for w in win], b.get("buyer_er", 0.0))
    s_mult = _exh_z_mult([w.get("seller_er", 0.0) for w in win], b.get("seller_er", 0.0))
    delta_oi = (b.get("opL", 0.0) + b.get("opS", 0.0)) - (b.get("clL", 0.0) + b.get("clS", 0.0))
    r_oi = delta_oi / max(1.0, b.get("curr_vol", 0.0))
    oi_mult = math.exp(-EXH_OI_K * math.tanh(r_oi / EXH_OI_SCALE))
    return b_mult, s_mult, oi_mult


def synth_bucket(sel: list) -> dict:
    """Collapse the selected buckets into ONE synthetic 'aggregate bucket' shaped exactly like a
    real bucket dict, so the SAME 12-state classifier can read it. Every key is a span aggregate
    that reduces to the real bucket's own value when the selection is a single bucket: extensive
    scalars (volumes, 4-vector, churn, liqs) SUM; OHLC takes first-open / max-high / min-low /
    last-close; POC is the argmax of the MERGED price ladder.

    Intensive per-bucket RATES — ``vol_mult`` and ``buyer_er`` / ``seller_er`` — are the
    VOLUME-WEIGHTED MEAN of the selected buckets' own values, NOT recomputed from span totals.
    This is deliberate: ``buyer_er = Σbuy / dispersion`` recomputed over the merged ladder grows
    ~linearly with the bucket count (Σbuy sums; merged-ladder dispersion grows sub-linearly), so
    the synthetic E/R lands ~n× the single-bucket scale and SATURATES the exhaustion z-mults
    (``b_mult``/``s_mult``) — which then gates STRONG via its ``translate`` factor and collapses
    trending regions to NEUTRAL/ROTATION. The volume-weighted mean keeps E/R on single-bucket scale
    (so the z vs single-bucket priors stays meaningful), preserves the buy/sell asymmetry, and still
    reduces to the bucket's own E/R at n==1. STATE is a SPAN concept (positioning has no per-price
    split) — the price band only refines the FLOW readout, not the classification."""
    def S(k): return sum(float(b.get(k, 0.0)) for b in sel)
    cv = S("curr_vol")

    def W(k, default=0.0):
        """Volume-weighted mean of an intensive per-bucket rate (stays single-bucket scale)."""
        if cv > 0:
            return sum(float(b.get(k, default)) * float(b.get("curr_vol", 0.0)) for b in sel) / cv
        return sum(float(b.get(k, default)) for b in sel) / len(sel)

    merged: dict = {}
    for b in sel:
        for ps, lv in (b.get("levels", {}) or {}).items():
            m = merged.setdefault(ps, {"b": 0.0, "s": 0.0})
            m["b"] += float(lv.get("b", 0.0))
            m["s"] += float(lv.get("s", 0.0))
    poc_price = (float(max(merged, key=lambda p: merged[p]["b"] + merged[p]["s"]))
                 if merged else float(sel[-1].get("close", 0.0)))
    highs = [float(b.get("high", 0.0)) for b in sel if b.get("high")]
    lows = [float(b.get("low", 0.0)) for b in sel if b.get("low")]
    return {
        "curr_vol": cv, "buy_vol": S("buy_vol"), "sell_vol": S("sell_vol"),
        "opL": S("opL"), "opS": S("opS"), "clL": S("clL"), "clS": S("clS"),
        "churn": S("churn"),
        "open": float(sel[0].get("open", 0.0)), "close": float(sel[-1].get("close", 0.0)),
        "high": max(highs) if highs else 0.0, "low": min(lows) if lows else 0.0,
        "poc_price": poc_price, "vol_mult": W("vol_mult", 1.0),
        "liq_short": S("liq_short"), "liq_long": S("liq_long"),
        "buyer_er": W("buyer_er"), "seller_er": W("seller_er"),
    }


def selection_state(filtered: list, lo_i: int, hi_i: int):
    """Run the existing 12-state classifier on the selected region. The synthetic aggregate bucket
    sits right after the REAL buckets immediately preceding the selection, so the classifier's
    rolling windows (sweep 10, exhaustion 30) read the true pre-selection context — and a one-bucket
    selection reduces to ``bucket_state.classify_bucket`` on that bucket EXACTLY (same synthetic,
    same priors, same b/s-mults), i.e. it matches the per-bucket hover STATE. No state logic is
    duplicated — this is purely an adapter. Returns ``(state, conf, dbg)`` or ``(None, None, [])``."""
    sel = filtered[lo_i:hi_i + 1]
    if not sel:
        return None, None, []
    syn = synth_bucket(sel)
    pw = max(bucket_state.SWEEP_WINDOW, EXH_WINDOW)   # widest window the classifier needs
    priors = filtered[max(0, lo_i - pw):lo_i]
    seq = priors + [syn]
    idx = len(priors)
    bm, sm, _om = exhaustion_mults(seq, idx)
    state, conf = bucket_state.classify_bucket(seq, idx, bm, sm)
    # same calibration breakdown the per-bucket box shows (top-3 states + winner factors),
    # read from the SAME synthetic sequence so it can't drift from the STATE line.
    dbg = bucket_state.render_debug_lines(seq, idx, bm, sm)
    return state, conf, dbg
