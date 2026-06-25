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


def _exh_z_mult(window_vals: list, val: float, min_window: int = EXH_MIN_WINDOW) -> float:
    """Smooth, bounded exhaustion multiplier from the z-score of ``val`` vs a rolling
    ``window_vals`` of recent same-side E/R (DIVERGES FROM LEGACY, Step 5).

    * Cold start (rule 0.6/2): a window shorter than ``min_window`` returns the NEUTRAL
      multiplier 1.0 — no z-score against an under-filled window. (``min_window`` defaults to the
      Step-5 EXH_MIN_WINDOW=10; the selection-scoped expanding baseline passes a smaller floor so the
      early intra-selection buckets still compute, accepting their thin/noisy baseline.)
    * Degenerate denominator (rule 0.6/1): std is floored at a coefficient-of-variation fraction
      of the mean (``EXH_CV_FLOOR*|mean|`` — scale-free, NOT a fixed absolute), and an all-zero
      window falls back to neutral, not div-by-0.
    * ``tanh`` bounds the exponent, so a noisy/flat window can never spike the multiplier beyond
      ``exp(EXH_K)``.
    """
    if len(window_vals) < min_window:
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


def balance_flip(series: list, net_dir: int) -> "dict | None":
    """DIRECTION-AWARE balance-of-power flip, scored by SUSTAIN — did the move's dominant side actually
    LOSE CONTROL and STAY lost (a regime change), not just briefly graze. DESCRIPTIVE (where the balance
    switched and how strongly it held), NOT a reversal probability (1m SOL flow is descriptive-not-
    predictive). A noisy early crossing that reverts is NOT a flip; a sustained one IS — even if the path
    to it was choppy (real absorption). Validated on real VM data: this kills ~78% of the brief-graze
    noise that the local-crossing definition mislabelled, and keeps genuine sustained switches.

    A real switch must be a TWO-SIDED turn: (i) in the move's RELEVANT direction — ``net_dir`` < 0 DOWN ->
    sellers lose control (S→B = E/R neg->pos), > 0 UP -> buyers lose (B→S), == 0 AMBIGUOUS -> any crossing,
    flagged ``ambig``; (ii) flanked by >= ``FLIP_MIN_REMAINDER`` buckets on BOTH sides (a start- or
    end-of-selection graze can't be confirmed); (iii) the OLD side HELD >= ``FLIP_SUSTAIN_MIN`` of the
    buckets BEFORE the cross AND the NEW side HOLDS >= ``FLIP_SUSTAIN_MIN`` of those after — held-then-lost,
    not a graze. The PRE-run requirement rejects edge crossings with no established prior control (the
    '@+1' noise: a flip one bucket in has no real pre-run). Confluence with the positioning vectors was
    investigated and REJECTED — OpL/OpS crosses every ~3.6 buckets so it 'agrees' with noise flips (64%)
    as much as real turns (66%), adding fake confidence; this two-sided test uses the aggression signal's
    OWN structure instead. The EARLIEST crossing meeting all is "where it switched"; its ``sustain``
    (fraction of the remainder the new side held) is the HEADLINE. Clean-vs-choppy texture (old clarity
    = min 1/N, local-persistence, separation) is a ``messy`` flag, NOT the headline (absorption is
    inherently choppy).

    FORMING (the SAME event at an EARLIER maturity — a WATCH heads-up, NEVER a signal/forecast): a
    candidate that passed the PRE half of the gate (relevant dir, >= REM buckets in, old side held
    >= SUSTAIN_MIN) but whose POST-run is still too SHORT to judge (``n - k`` < REM). ``forming`` True,
    ``sustain`` = held-SO-FAR (post over the buckets available), ``post_n``/``need`` = maturity (e.g.
    2/4). The MOST RECENT such candidate ("might be flipping right now") is returned. It SOLIDIFIES into
    the confirmed flip if the new side still holds once REM buckets accrue, or VANISHES (a crossing with
    the pre-run but ``n - k`` >= REM and post < SUSTAIN_MIN falls into NEITHER list -> no marker) if it
    reverts. Most forming markers vanish — most early crossings ARE the noise the confirmed gate filters;
    that is shown honestly, not hidden. No candidate at all -> ``no_flip``. Forming is keyed to the same
    ``net_dir`` as confirmed, so it catches the selection's MAIN turn earlier; a second OPPOSITE turn
    brewing after an already-confirmed flip is out of scope (v2). Returns ``{idx, sustain, no_flip,
    forming, post_n, need, dir, ambig, messy}`` (``dir`` = 'S→B' / 'B→S' / '—') or None."""
    n = len(series)
    M = max((abs(x) for x in series), default=0.0)
    if n < 2 or M == 0.0:
        return None
    cr = [k for k in range(1, n) if series[k - 1] != 0 and series[k] != 0
          and (series[k - 1] < 0) != (series[k] < 0)]
    N = len(cr)
    if net_dir < 0:
        rel, want = [k for k in cr if series[k - 1] < 0 < series[k]], "S→B"
    elif net_dir > 0:
        rel, want = [k for k in cr if series[k - 1] > 0 > series[k]], "B→S"
    else:
        rel, want = cr, "—"

    def post_sustain(k):
        after = series[k:]
        new_neg = series[k] < 0
        return sum(1 for x in after if (x < 0) == new_neg) / len(after)

    def pre_sustain(k):
        before = series[:k]
        old_neg = series[k - 1] < 0
        return (sum(1 for x in before if (x < 0) == old_neg) / len(before)) if before else 0.0

    REM = config.FLIP_MIN_REMAINDER
    # PRE half of the two-sided gate: candidate turns where the OLD side genuinely HELD before the cross
    # (>= REM buckets in, pre-run >= SUSTAIN_MIN). This rejects edge-of-selection grazes with no
    # established prior control (the @+1 noise) — for BOTH the confirmed and the forming verdict.
    pre_ok = [k for k in rel
              if k >= REM and pre_sustain(k) >= config.FLIP_SUSTAIN_MIN]
    # CONFIRMED = pre_ok AND the new side has had >= REM buckets to prove itself AND held >= SUSTAIN_MIN
    # of them (the POST half). Identical to the previous gate; the EARLIEST such crossing is the flip.
    confirmed = [k for k in pre_ok
                 if (n - k) >= REM and post_sustain(k) >= config.FLIP_SUSTAIN_MIN]
    if confirmed:
        k = min(confirmed)   # EARLIEST two-sided turn = where the dominant side first lost control
        before, after = series[:k], series[k:]
        old_neg, new_neg = series[k - 1] < 0, series[k] < 0
        rb = ra = 0
        i = k - 1
        while i >= 0 and series[i] != 0 and (series[i] < 0) == old_neg:
            rb += 1; i -= 1
        i = k
        while i < n and series[i] != 0 and (series[i] < 0) == new_neg:
            ra += 1; i += 1
        local_persist = ((rb / len(before)) * (ra / len(after))) ** 0.5
        separation = ((abs(sum(before) / len(before)) / M) * (abs(sum(after) / len(after)) / M)) ** 0.5
        clarity = min(1.0 / N, local_persist, separation)   # crossing cleanness -> '·messy' texture only
        return {"idx": k, "sustain": post_sustain(k), "no_flip": False, "forming": False,
                "post_n": n - k, "need": REM, "dir": "S→B" if series[k - 1] < 0 else "B→S",
                "ambig": net_dir == 0, "messy": clarity < config.FLIP_MESSY_CLARITY}

    # FORMING = pre_ok but the POST-run is still too SHORT to judge (n - k < REM). Same event, shown
    # BEFORE it matures — tentative WATCH only. A pre_ok crossing with n - k >= REM but post < SUSTAIN_MIN
    # is in NEITHER list -> no marker (it had its chance and reverted = VANISHES). Most forming vanish.
    forming = [k for k in pre_ok if (n - k) < REM]
    if forming:
        k = max(forming)   # MOST RECENT candidate = "the balance might be flipping right now"
        return {"idx": k, "sustain": post_sustain(k), "no_flip": False, "forming": True,
                "post_n": n - k, "need": REM, "dir": "S→B" if series[k - 1] < 0 else "B→S",
                "ambig": net_dir == 0, "messy": False}

    return {"idx": min(range(n), key=lambda i: abs(series[i])), "sustain": 0.0, "no_flip": True,
            "forming": False, "post_n": 0, "need": REM, "dir": want,
            "ambig": net_dir == 0, "messy": False}


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


def absorption_vol(buckets: list, i: int, window: int) -> "tuple[float, float, float]":
    """Per-bucket BULL / BEAR absorption in VOLUME — aggressive volume on a side that FAILED to move price
    its way, scaled by how SUPPRESSED the move was RELATIVE to the region's own volume->displacement norm
    (self-calibrating, no absolute impact model). DESCRIPTIVE, not a prediction.

    norm ``k`` = Σ|close-open| / Σ curr_vol over the trailing ``window`` buckets (price moved per unit
    volume, recently). suppression ``s`` = clamp(1 - (|disp|/curr_vol)/k, 0, 1) — 1 when this bucket's
    volume moved price far LESS than the norm (absorbed), 0 when it moved efficiently or more. GROSS +
    DIRECTIONAL: the absorption is credited only to the heavier AGGRESSOR that failed —
    ``bull = sell_vol*s`` when sellers dominated (their selling didn't drop price = buyers soaked it up),
    ``bear = buy_vol*s`` when buyers dominated; the other (defending) side is 0. Read the bull:bear ratio
    over a selection for the directional lean. Returns ``(bull, bear, s)`` in volume units."""
    b = buckets[i]
    o, c = float(b.get("open", 0.0)), float(b.get("close", 0.0))
    bv, sv = float(b.get("buy_vol", 0.0)), float(b.get("sell_vol", 0.0))
    cv = float(b.get("curr_vol", 0.0)) or (bv + sv)
    win = buckets[max(0, i - window):i]
    sd = sum(abs(float(w.get("close", 0.0)) - float(w.get("open", 0.0))) for w in win)
    scv = sum((float(w.get("curr_vol", 0.0)) or (float(w.get("buy_vol", 0.0)) + float(w.get("sell_vol", 0.0))))
              for w in win)
    k = (sd / scv) if scv > 0 else 0.0
    eff = (abs(c - o) / cv) if cv > 0 else 0.0
    s = max(0.0, min(1.0, 1.0 - eff / k)) if k > 0 else 0.0
    bull = sv * s if sv > bv else 0.0
    bear = bv * s if bv > sv else 0.0
    return bull, bear, s


def absorption_series(buckets: list, lo_i: int, hi_i: int, window: int) -> "tuple[list, list, list]":
    """Per-bucket ``(bull, bear, s)`` arrays over a selection [lo_i, hi_i] (index 0 = bucket lo_i), in ONE
    pass via :func:`absorption_vol` — so the adaptive default, the zones, and the box totals all share it.
    Returns ``(bull, bear, sval)`` lists (empty if hi_i < lo_i)."""
    bull, bear, sval = [], [], []
    for i in range(lo_i, hi_i + 1):
        bu, be, sv = absorption_vol(buckets, i, window)
        bull.append(bu); bear.append(be); sval.append(sv)
    return bull, bear, sval


def absorption_default_s(bull: list, bear: list, sval: list) -> float:
    """Adaptive default zone threshold = the selection's MEDIAN nonzero suppression over its DIRECTIONAL
    absorbing buckets (defended selection -> high default, quiet -> low; self-calibrating, like adaptive
    VPIN). 1.0 (draws nothing) when the selection has no absorption."""
    nz = sorted(sval[k] for k in range(len(sval)) if (bull[k] > 0 or bear[k] > 0) and sval[k] > 0)
    return nz[len(nz) // 2] if nz else 1.0


def zones_from_series(bull: list, bear: list, sval: list, lo_i: int, buckets: list,
                      s_threshold: float, min_run: int) -> "list[dict]":
    """Absorption ZONES from precomputed per-bucket bull/bear/s arrays. A bucket ABSORBS if it is
    directional (bull or bear > 0) AND its suppression ``s >= s_threshold`` (the slider); a zone = a run
    of >= ``min_run`` consecutive absorbing buckets on the SAME side. FLOORLESS: a lower threshold ->
    more/weaker zones, but always SUSTAINED runs (no single-bucket zones); a clean trend stays empty since
    its buckets have s~0. Each zone: ``{side ('bull'|'bear'), start, end (abs idx), vol (absorbed volume
    over the run), plo, phi (price range = min low / max high)}``. DESCRIPTIVE — above the slider's
    yellow-dot floor = validated-strength, below = weaker (a gradient); not a prediction the level holds.
    Underlying primitive = the validated :func:`absorption_vol`, unchanged."""
    zones = []
    for side, vals in (("bull", bull), ("bear", bear)):
        ab = [vals[k] > 0 and sval[k] >= s_threshold for k in range(len(vals))]
        k, m = 0, len(vals)
        while k < m:
            if not ab[k]:
                k += 1
                continue
            st = k
            while k < m and ab[k]:
                k += 1
            if (k - st) >= min_run:
                a, b = lo_i + st, lo_i + k - 1
                lows = [float(buckets[j].get("low", 0.0)) for j in range(a, b + 1)]
                highs = [float(buckets[j].get("high", 0.0)) for j in range(a, b + 1)]
                zones.append({"side": side, "start": a, "end": b, "vol": sum(vals[st:k]),
                              "plo": min(lows), "phi": max(highs)})
    return zones


# ---------------------------------------------------------------------------
# EFFECTIVE AGGRESSION — the validated MIRROR of absorption. Absorption = dominant aggressor's volume that
# FAILED to move price its way (V*s, high s). Effective aggression = dominant aggressor's volume that MOVED
# price ITS way (V*(1-s)), gated on direction. Reuses the EXACT s from absorption_vol; for an in-direction
# bucket absorption + eff_agg == V (they SPLIT the dominant volume by suppression — proven on real data).
# DESCRIPTIVE: "this side FORCEFULLY moved price (heavy volume that worked)", not a prediction.
# ---------------------------------------------------------------------------
def effective_aggression(buckets: list, i: int, window: int) -> "tuple[float, float, float]":
    """Per-bucket (eff_agg_bull, eff_agg_bear, s) — the mirror of :func:`absorption_vol`, reusing its
    suppression ``s`` exactly. ``eff_agg_bull = buy_vol*(1-s)`` when buyers dominate volume AND price closed
    UP (buyers forced it up); ``eff_agg_bear = sell_vol*(1-s)`` when sellers dominate AND price closed DOWN.
    Zero on the other side / on a divergent close. Volume units, like absorption."""
    b = buckets[i]
    o, c = float(b.get("open", 0.0)), float(b.get("close", 0.0))
    bv, sv = float(b.get("buy_vol", 0.0)), float(b.get("sell_vol", 0.0))
    _bu, _be, s = absorption_vol(buckets, i, window)   # reuse the VALIDATED s (and its trailing norm k)
    eff_bull = bv * (1.0 - s) if (bv > sv and c > o) else 0.0
    eff_bear = sv * (1.0 - s) if (sv > bv and c < o) else 0.0
    return eff_bull, eff_bear, s


def _vol_norm(buckets: list, i: int, force_window: int) -> float:
    """Trailing-mean curr_vol over ``force_window`` (the self-calibrated FORCE reference, mirroring k's
    window). Floors at 1.0 so the force ratio never divides by zero on a cold/flat window."""
    win = buckets[max(0, i - force_window):i] or [buckets[i]]
    tot = sum((float(w.get("curr_vol", 0.0)) or (float(w.get("buy_vol", 0.0)) + float(w.get("sell_vol", 0.0))))
              for w in win)
    vn = tot / len(win)
    return vn if vn > 0 else 1.0


def eff_agg_series(buckets: list, lo_i: int, hi_i: int, window: int,
                   force_window: int) -> "tuple[list, list, list]":
    """Per-bucket ``(eff_bull, eff_bear, fval)`` over a selection, in ONE pass. ``fval`` = the directional
    eff-agg in VOLUME / the trailing FORCE norm (``_vol_norm``) — a self-calibrated RELATIVE force ratio
    (~[0,1]; the slider rides it). 0 on non-eff-agg buckets. Mirrors :func:`absorption_series`."""
    eff_bull, eff_bear, fval = [], [], []
    for i in range(lo_i, hi_i + 1):
        eb, es, _s = effective_aggression(buckets, i, window)
        e = eb if eb > 0 else es
        f = (e / _vol_norm(buckets, i, force_window)) if e > 0 else 0.0
        eff_bull.append(eb); eff_bear.append(es); fval.append(f)
    return eff_bull, eff_bear, fval


def eff_agg_default_f(eff_bull: list, eff_bear: list, fval: list) -> float:
    """Adaptive default eff-agg zone threshold = the selection's MEDIAN nonzero FORCE over its directional
    eff-agg buckets (forceful selection -> high default, ordinary -> low; self-calibrating). Sits BELOW the
    forceful dot for an ordinary selection (exactly like absorption's median-s vs its dot). 1.0 when none."""
    nz = sorted(fval[k] for k in range(len(fval)) if (eff_bull[k] > 0 or eff_bear[k] > 0) and fval[k] > 0)
    return nz[len(nz) // 2] if nz else 1.0


def eff_zones_from_series(eff_bull: list, eff_bear: list, fval: list, lo_i: int, buckets: list,
                          f_threshold: float, min_run: int) -> "list[dict]":
    """Effective-aggression ZONES (mirror of :func:`zones_from_series`). A bucket is FORCEFUL if it is
    directional eff-agg (bull or bear > 0) AND its force ``f >= f_threshold`` (the slider); a zone = a run
    of >= ``min_run`` consecutive forceful buckets on the SAME side. FLOORLESS. Above the slider's dot
    (force ~0.75 = top-quartile) = distinctively forceful; below = ordinary directional volume. Each zone:
    ``{side, start, end, vol (summed eff-agg over the run), plo, phi}``."""
    zones = []
    for side, vals in (("bull", eff_bull), ("bear", eff_bear)):
        ag = [vals[k] > 0 and fval[k] >= f_threshold for k in range(len(vals))]
        k, m = 0, len(vals)
        while k < m:
            if not ag[k]:
                k += 1
                continue
            st = k
            while k < m and ag[k]:
                k += 1
            if (k - st) >= min_run:
                a, b = lo_i + st, lo_i + k - 1
                lows = [float(buckets[j].get("low", 0.0)) for j in range(a, b + 1)]
                highs = [float(buckets[j].get("high", 0.0)) for j in range(a, b + 1)]
                zones.append({"side": side, "start": a, "end": b, "vol": sum(vals[st:k]),
                              "plo": min(lows), "phi": max(highs)})
    return zones


# ---------------------------------------------------------------------------
# GATED EXHAUSTION (the TRUE worn-out signal) — for the smoothed Mode-10 exhaustion cloud. The state
# engine's BULL/BEAR EXHAUSTION score = geomean(absorb, drain/cover, Δsoft): effort-z hot AND OI
# draining/closing-flow powered. This is what SEPARATES true exhaustion (worn out) from strong
# continuation (effort hot but fresh OI opening) — a 36x separation on real data, vs the raw b/s_mult
# which conflates the two. DESCRIPTIVE ("this side is worn out here"), NOT a reversal forecast.
# ---------------------------------------------------------------------------
def gated_exhaustion(buckets: list, i: int) -> "tuple[float, float]":
    """Per-bucket TRUE (gated) exhaustion score, in [0,1], for the bucket's MATCHING close side: BULL on a
    green bucket, BEAR on a red one (the state engine only scores the close-direction side; the other is 0).
    Returns ``(bull_exh, bear_exh)``."""
    bm, sm, _ = exhaustion_mults(buckets, i)
    scores = bucket_state.state_scores(buckets, i, bm, sm)
    return scores.get("BULL EXHAUSTION", 0.0), scores.get("BEAR EXHAUSTION", 0.0)


def selection_exhaustion(sel: list, measure: str, sel_min_window: int) -> "list[tuple[float, float]]":
    """Per-bucket ``(bull_exh, bear_exh)`` over a SELECTION, z-scored FRESH FROM THE SELECTION START: each
    bucket's E/R z-baseline is ONLY the PRIOR buckets WITHIN the selection (an EXPANDING window), never the
    buckets before the selection. Bucket 0 has no baseline (neutral); the early buckets have a thin, noisy
    baseline (accepted, drawn normal). Below ``sel_min_window`` prior buckets the z-mult is neutral (1.0).

    ``measure``:
      * ``'raw'``   — the effort-z itself, normalized: ``clamp((mult-0.5)/1.5, 0, 1)``. Continuous,
        both-sided (a smooth weave) — but it's 'effort running hot', NOT true worn-out (a winning side
        reads hot).
      * ``'gated'`` — the true worn-out score (effort hot AND OI draining/closing-flow), via the state
        engine fed the EXPANDING-window mults. Sparse; only fires when a side is genuinely exhausted.
    Recompute per selection (the baseline IS the selection), so the same bucket reads differently depending
    on where the selection is drawn — selection-relative by design."""
    ber = [float(b.get("buyer_er", 0.0)) for b in sel]
    ser = [float(b.get("seller_er", 0.0)) for b in sel]
    out = []
    for k in range(len(sel)):
        bm = _exh_z_mult(ber[:k], ber[k], sel_min_window)     # baseline = prior selected buckets only
        sm = _exh_z_mult(ser[:k], ser[k], sel_min_window)
        if measure == "raw":
            eb = max(0.0, min(1.0, (bm - 0.5) / 1.5))
            es = max(0.0, min(1.0, (sm - 0.5) / 1.5))
        else:   # 'gated' — the worn-out score with the expanding-window mults
            sc = bucket_state.state_scores(sel, k, bm, sm)
            eb = sc.get("BULL EXHAUSTION", 0.0)
            es = sc.get("BEAR EXHAUSTION", 0.0)
        out.append((eb, es))
    return out


def envelope_series(x: list, release: float) -> list:
    """Causal ATTACK-RELEASE envelope: instant rise to the true value, exponential fade. Each value swells
    to its own level then decays by ``release`` per bucket (``out[i] = max(x[i], out[i-1]*release)``). Used
    to turn the sparse, spiky gated-exhaustion signal into a readable swell-and-decay cloud WITHOUT
    understating the true magnitude (a plain EMA would damp a 1-bucket 0.95 spike to ~0.47). Causal so the
    live forming edge is honest (no lookahead)."""
    out = []
    prev = 0.0
    for v in x:
        cur = v if v > prev * release else prev * release
        out.append(cur)
        prev = cur
    return out


def envelope_symmetric(x: list, release: float) -> list:
    """ZERO-PHASE (symmetric) envelope: the per-bucket MAX of a forward and a backward attack-release
    envelope. Each spike gets a symmetric tent (rises into it from the left AND decays after it to the
    right), peak preserved, no causal lag — so two such smoothed series CROSS at the true balance shift,
    not at a point dragged right by a one-sided decay tail. Non-causal (uses both neighbours), which is
    fine for a SELECTION (a descriptive look at a fully-known region)."""
    fwd = envelope_series(x, release)
    bwd = envelope_series(list(reversed(x)), release)[::-1]
    return [max(a, b) for a, b in zip(fwd, bwd)]


def rolling_share(bull: list, bear: list, window: int) -> list:
    """Per-bucket bull SHARE — ``Σbull / Σ(bull+bear)`` — over a CENTERED rolling window of ``window`` buckets
    (clamped at the edges). Non-cumulative: tracks the LOCAL lean and SHIFTS across the region, instead of a
    cumulative share that converges to a flat line. 0.5 (even) where the window holds no bull/bear at all. The
    bear share is just ``1 - this``. Works for the one-sided measures (absorption/eff-agg) because a window is
    wide enough to contain both sides; an all-one-side window correctly reads ~1.0 (locally only that side)."""
    n = len(bull)
    h = max(1, window) // 2
    out = []
    for i in range(n):
        a, b = max(0, i - h), min(n, i + h + 1)
        sb = sum(bull[a:b]); sr = sum(bear[a:b])
        tot = sb + sr
        out.append((sb / tot) if tot > 0 else 0.5)
    return out
