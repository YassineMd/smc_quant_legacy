"""Mode 10 STATE ENGINE (A3b) — the single interpretive verdict on line 12.

Pure, side-effect-free, and decoupled from the GUI: :func:`classify_bucket` takes
the scanner's ``buckets`` list, an index, and the Step-5 exhaustion multipliers
(``b_mult``/``s_mult`` from ``terminal._exhaustion_mults`` — passed in so this module
never imports ``terminal``) and returns ``(state, confidence)``.

Design (locked with the operator):
* **Scored, not boolean.** Each state scores in [0,1] as the GEOMETRIC MEAN of its
  factors — every factor must be jointly strong, and any core factor at 0 forces the
  state to 0 (a hard gate). The verdict is the highest-scoring state; its score *is*
  the confidence. This keeps confidence honest: a marginal read scores low and renders
  faint, so a wrong verdict can't masquerade as a confident one.
* **Core vs soft factors.** Core factors are raw ramps (can fall to 0 → they gate).
  Soft factors are floored to ``[SOFT_FLOOR, 1]`` so they *shade* the score without
  killing it (e.g. a trap with imperfect POC placement still scores, just lower).
* **Gradient-preserving squeeze floor.** A decisive liq squeeze is floored to ≥0.80 so
  it wins, but the floor itself RISES with liq intensity — a violent squeeze reads ~95%,
  a marginal one ~80% (not flattened).
* **Understated.** The label is ``★ STATE NN%`` at ≥80% — a calibrated tool, no hype.

Liquidation-gated states (SHORT/LONG SQUEEZE, LIQUIDITY COIL) read on live + future
buckets only; buckets that formed before the A3b-pre liq feed have ``liq_*`` == 0 and
stay inert there (the data didn't exist then — not a bug).

EVERY threshold below is a named constant; tune live.
"""
from __future__ import annotations

from . import config

# ── windows ────────────────────────────────────────────────────────────────
SWEEP_WINDOW = 10            # buckets back for the swept-level extremes + range baseline
#   (the exhaustion z uses EXH_WINDOW=30 already, baked into the b_mult/s_mult passed in)

# ── factor shaping ─────────────────────────────────────────────────────────
SOFT_FLOOR = 0.55            # soft factors range [SOFT_FLOOR, 1] — shade, never kill

# ── ramp endpoints: ramp(x; LO, HI) = 0 at/below LO, 1 at/above HI ──────────
DELTA_LO, DELTA_HI = 0.10, 0.55          # |buy-sell|/vol — net aggression
OPEN_OI_LO, OPEN_OI_HI = 0.10, 0.50      # opL or opS /vol — fresh directional OI
DRAIN_OI_LO, DRAIN_OI_HI = 0.05, 0.45    # -ΔOI/vol — OI reducing (closing)
CLOSE_FLOW_LO, CLOSE_FLOW_HI = 0.15, 0.50  # clS or clL /vol — closing flow powering the move
ABSORB_LO, ABSORB_HI = 1.20, 2.00        # b_mult/s_mult — Step-5 z firing (absorption)
TRANSLATE_LO, TRANSLATE_HI = 1.30, 2.00  # b_mult/s_mult — 1-ramp = effort TRANSLATING
VEL_LO, VEL_HI = 1.00, 2.20              # vol_mult — mild pace (strong/quiet)
VEL_SQUEEZE_LO, VEL_SQUEEZE_HI = 1.30, 3.00  # vol_mult — violence (squeeze)
SWEEP_LO, SWEEP_HI = 0.03, 0.50          # (sweep depth)/range — liquidity grab
FAIL_LO, FAIL_HI = 0.05, 0.50            # |failed result|/range — the reversal depth
POC_LO, POC_HI = 0.50, 0.85              # poc_pos for a bull trap (POC stacked high)
RECLAIM_LO, RECLAIM_HI = 0.00, 0.30      # (close past the swept level)/range — squeeze reclaim
LIQ_LO, LIQ_HI = 0.03, 0.20              # liq_side/vol — forced flow
LIQ_COIL_LO, LIQ_COIL_HI = 0.03, 0.15    # liq_total/vol — liqs absorbed inside the coil
CHURN_LO, CHURN_HI = 0.55, 0.90          # churn/vol — OI-neutral transfer
OI_NEUTRAL_LO, OI_NEUTRAL_HI = 0.05, 0.35  # |ΔOI|/vol — low = rotation
RANGE_COMPRESS_LO, RANGE_COMPRESS_HI = 0.60, 1.20  # range/atr — inside-bar compression

# ── squeeze floor (gradient-preserving) ────────────────────────────────────
SQUEEZE_FLOOR = 0.80         # decisive squeeze floors here ...
SQUEEZE_FLOOR_LIQ = 0.10     # ... when liq_side/vol >= this ...
SQUEEZE_FLOOR_SAT = 0.30     # ... and the floor rises to ~1.0 by this liq fraction

# ── verdict floor + rendering ──────────────────────────────────────────────
NEUTRAL_SCORE = 0.20         # baseline so there is always a verdict
STAR_THRESHOLD = 0.80        # score >= -> prepend ★
ALPHA_MIN, ALPHA_SPAN = 0.12, 0.75   # chip bg opacity = ALPHA_MIN + ALPHA_SPAN*score
PANEL_BG = (10, 12, 16)      # StatsOverlay bg (rgba 10,12,16) — soft factors blend toward it

STATE_COLORS = {
    "STRONG BULL": "#2ecc71",
    "STRONG BEAR": "#e74c3c",
    "BULL EXHAUSTION": "#e67e22",
    "BEAR EXHAUSTION": "#e67e22",
    "BULL TRAP": "#e74c3c",        # bearish outcome
    "BEAR TRAP": "#2ecc71",        # bullish outcome
    "SHORT SQUEEZE": "#2ecc71",    # bullish
    "LONG SQUEEZE": "#e74c3c",     # bearish
    "LIQUIDITY COIL": "#f1c40f",
    "ROTATION": "#5a7fa0",
    "CHOP": "#7f8c8d",
    "NEUTRAL": "#566070",
}


# ── helpers ────────────────────────────────────────────────────────────────
def _ramp(x: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 1.0 if x >= hi else 0.0
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def _soft(x: float, lo: float, hi: float) -> float:
    """A factor that shades the score but never kills it: [SOFT_FLOOR, 1]."""
    return SOFT_FLOOR + (1.0 - SOFT_FLOOR) * _ramp(x, lo, hi)


def _geo(*vals: float) -> float:
    """Geometric mean. Any 0 -> 0 (core factors gate); soft factors are floored > 0."""
    prod = 1.0
    for v in vals:
        prod *= max(0.0, v)
    return prod ** (1.0 / len(vals)) if vals else 0.0


def _score_and_factors(buckets: list, idx: int, b_mult: float, s_mult: float):
    """Core scorer → ``(scores, factors, floored)``. ``factors`` is ``{state: {name: value}}``
    — the geomean inputs and the SINGLE SOURCE for both the score (``_geo(*values)``) and the
    debug breakdown, so the two can never drift. ``floored`` = squeeze states the gradient
    floor lifted over their geomean."""
    b = buckets[idx]
    vol = b.get("curr_vol", 0.0)
    if vol <= 0.0:
        return {"NEUTRAL": NEUTRAL_SCORE}, {}, set()
    eps = config.TICK_SIZE

    o = b.get("open", 0.0); h = b.get("high", 0.0)
    lo = b.get("low", 0.0); c = b.get("close", 0.0)
    rng = max(h - lo, eps)
    buy = b.get("buy_vol", 0.0); sell = b.get("sell_vol", 0.0)
    opL = b.get("opL", 0.0); opS = b.get("opS", 0.0)
    clL = b.get("clL", 0.0); clS = b.get("clS", 0.0)
    churn = b.get("churn", 0.0)
    poc = b.get("poc_price", c)
    vel = b.get("vol_mult", 1.0)
    liq_short = b.get("liq_short", 0.0)
    liq_long = b.get("liq_long", 0.0)

    delta_frac = (buy - sell) / vol
    doi_frac = ((opL + opS) - (clL + clS)) / vol
    churn_frac = churn / vol
    poc_pos = (poc - lo) / rng
    result = (c - o) / rng                 # signed follow-through (>0 green, <0 red)
    green = c >= o
    liqS_frac = liq_short / vol
    liqL_frac = liq_long / vol
    liq_total_frac = (liq_short + liq_long) / vol

    # rolling window: swept-level extremes + range (ATR) baseline
    win = buckets[max(0, idx - SWEEP_WINDOW):idx]
    if win:
        prev_high = max(w.get("high", h) for w in win)
        prev_low = min(w.get("low", lo) for w in win)
        atr = max(eps, sum(max(0.0, w.get("high", 0.0) - w.get("low", 0.0)) for w in win) / len(win))
    else:
        prev_high, prev_low, atr = h, lo, rng
    swept_above = max(0.0, h - prev_high) / rng
    swept_below = max(0.0, prev_low - lo) / rng
    inside_bar = (h <= prev_high and lo >= prev_low)
    reclaim_up = ((c - prev_low) / rng) if lo < prev_low else -1.0    # -1 -> no sweep -> soft floor
    reclaim_dn = ((prev_high - c) / rng) if h > prev_high else -1.0

    absb = _ramp(b_mult, ABSORB_LO, ABSORB_HI)
    abss = _ramp(s_mult, ABSORB_LO, ABSORB_HI)
    trans_b = 1.0 - _ramp(b_mult, TRANSLATE_LO, TRANSLATE_HI)   # effort translating (strong)
    trans_s = 1.0 - _ramp(s_mult, TRANSLATE_LO, TRANSLATE_HI)

    # Named factors per state — the SINGLE SOURCE for both the score (geomean of the
    # values, below) and the debug breakdown, so the two can never drift apart.
    F: "dict[str, dict[str, float]]" = {}

    # ── STRONG: clean conviction — fresh OI, price follows, effort TRANSLATES ──
    if green:
        F["STRONG BULL"] = {
            "Δaggr": _ramp(delta_frac, DELTA_LO, DELTA_HI),
            "freshOI": _ramp(opL / vol, OPEN_OI_LO, OPEN_OI_HI),
            "translate": trans_b,
            "vel": _soft(vel, VEL_LO, VEL_HI),
        }
    else:
        F["STRONG BEAR"] = {
            "Δaggr": _ramp(-delta_frac, DELTA_LO, DELTA_HI),
            "freshOI": _ramp(opS / vol, OPEN_OI_LO, OPEN_OI_HI),
            "translate": trans_s,
            "vel": _soft(vel, VEL_LO, VEL_HI),
        }

    # ── EXHAUSTION: Step-5 z firing + OI draining / closing-flow powered ──
    if green:
        F["BULL EXHAUSTION"] = {
            "absorb": absb,
            "drain/cover": max(_ramp(-doi_frac, DRAIN_OI_LO, DRAIN_OI_HI),
                               _ramp(clS / vol, CLOSE_FLOW_LO, CLOSE_FLOW_HI)),
            "Δsoft": _soft(delta_frac, DELTA_LO, DELTA_HI),
        }
    else:
        F["BEAR EXHAUSTION"] = {
            "absorb": abss,
            "drain/cover": max(_ramp(-doi_frac, DRAIN_OI_LO, DRAIN_OI_HI),
                               _ramp(clL / vol, CLOSE_FLOW_LO, CLOSE_FLOW_HI)),
            "Δsoft": _soft(-delta_frac, DELTA_LO, DELTA_HI),
        }

    # ── TRAP: effort ABSORBED → reversal. Hard gate = result against the aggressor.
    #    Core = aggression × failure depth; soft = swept level, POC placement, absorption.
    if result < 0.0:    # closed RED despite buying = BULL trap
        F["BULL TRAP"] = {
            "Δaggr": _ramp(delta_frac, DELTA_LO, DELTA_HI),       # buyers were the aggressor
            "fail": _ramp(-result, FAIL_LO, FAIL_HI),            # the failure (red depth)
            "swept": _soft(swept_above, SWEEP_LO, SWEEP_HI),     # poked the high (bait)
            "pocHigh": _soft(poc_pos, POC_LO, POC_HI),           # POC stacked HIGH (reload)
            "absorb": _soft(b_mult, ABSORB_LO, ABSORB_HI),       # absorption confirm
        }
    if result > 0.0:    # closed GREEN despite selling = BEAR trap
        F["BEAR TRAP"] = {
            "Δaggr": _ramp(-delta_frac, DELTA_LO, DELTA_HI),
            "fail": _ramp(result, FAIL_LO, FAIL_HI),
            "swept": _soft(swept_below, SWEEP_LO, SWEEP_HI),
            "pocLow": _soft(1.0 - poc_pos, POC_LO, POC_HI),      # POC stacked LOW
            "absorb": _soft(s_mult, ABSORB_LO, ABSORB_HI),
        }

    # ── SQUEEZE: forced covering (gated on liqs); gradient floor applied after the geomean ──
    if green and liq_short > 0.0:
        F["SHORT SQUEEZE"] = {
            "liq": _ramp(liqS_frac, LIQ_LO, LIQ_HI),
            "drainOI": _ramp(-doi_frac, DRAIN_OI_LO, DRAIN_OI_HI),   # OI draining = forced CLOSING
            "vel": _ramp(vel, VEL_SQUEEZE_LO, VEL_SQUEEZE_HI),
            "reclaim": _soft(reclaim_up, RECLAIM_LO, RECLAIM_HI),
        }
    if (not green) and liq_long > 0.0:
        F["LONG SQUEEZE"] = {
            "liq": _ramp(liqL_frac, LIQ_LO, LIQ_HI),
            "drainOI": _ramp(-doi_frac, DRAIN_OI_LO, DRAIN_OI_HI),
            "vel": _ramp(vel, VEL_SQUEEZE_LO, VEL_SQUEEZE_HI),
            "reclaim": _soft(reclaim_dn, RECLAIM_LO, RECLAIM_HI),
        }

    # ── LIQUIDITY COIL: inside-bar compression absorbing forced flow (gated) ──
    if inside_bar and (liq_short + liq_long) > 0.0:
        F["LIQUIDITY COIL"] = {
            "compress": 1.0 - _ramp(rng / atr, RANGE_COMPRESS_LO, RANGE_COMPRESS_HI),
            "liqAbs": _ramp(liq_total_frac, LIQ_COIL_LO, LIQ_COIL_HI),
            "calm": 1.0 - _ramp(abs(delta_frac), DELTA_LO, DELTA_HI),
        }

    # ── ROTATION / CHURN: OI-neutral breathing — high churn, low net OI + direction ──
    F["ROTATION"] = {
        "churn": _ramp(churn_frac, CHURN_LO, CHURN_HI),
        "oiNeutral": 1.0 - _ramp(abs(doi_frac), OI_NEUTRAL_LO, OI_NEUTRAL_HI),
        "calm": 1.0 - _ramp(abs(delta_frac), DELTA_LO, DELTA_HI),
    }

    # ── CHOP: inside-bar + quiet + no direction (and no forced flow -> that's a COIL) ──
    if inside_bar:
        F["CHOP"] = {
            "quiet": 1.0 - _ramp(vel, VEL_LO, VEL_HI),
            "calm": 1.0 - _ramp(abs(delta_frac), DELTA_LO, DELTA_HI),
            "noLiq": 1.0 - _ramp(liq_total_frac, LIQ_COIL_LO, LIQ_COIL_HI),
        }

    # score = geomean of each state's factors (identical to the prior per-state _geo calls)
    S: "dict[str, float]" = {k: _geo(*v.values()) for k, v in F.items()}
    floored: "set[str]" = set()

    # gradient-preserving squeeze floor — overrides the geomean when liqs are decisive
    if "SHORT SQUEEZE" in S and liqS_frac >= SQUEEZE_FLOOR_LIQ:
        fl = SQUEEZE_FLOOR + (1.0 - SQUEEZE_FLOOR) * _ramp(liqS_frac, SQUEEZE_FLOOR_LIQ, SQUEEZE_FLOOR_SAT)
        if fl > S["SHORT SQUEEZE"]:
            S["SHORT SQUEEZE"] = fl; floored.add("SHORT SQUEEZE")
    if "LONG SQUEEZE" in S and liqL_frac >= SQUEEZE_FLOOR_LIQ:
        fl = SQUEEZE_FLOOR + (1.0 - SQUEEZE_FLOOR) * _ramp(liqL_frac, SQUEEZE_FLOOR_LIQ, SQUEEZE_FLOOR_SAT)
        if fl > S["LONG SQUEEZE"]:
            S["LONG SQUEEZE"] = fl; floored.add("LONG SQUEEZE")

    # ── NEUTRAL floor: always a verdict ──
    S["NEUTRAL"] = NEUTRAL_SCORE
    return S, F, floored


def state_scores(buckets: list, idx: int, b_mult: float, s_mult: float) -> "dict[str, float]":
    """Every state's raw score for bucket ``idx`` (exposed for tests/debug)."""
    return _score_and_factors(buckets, idx, b_mult, s_mult)[0]


def classify_bucket(buckets: list, idx: int, b_mult: float, s_mult: float) -> "tuple[str, int]":
    """Return ``(state, confidence%)`` — the highest-scoring state and its score as %."""
    scores = state_scores(buckets, idx, b_mult, s_mult)
    state = max(scores, key=scores.get)
    confidence = max(1, min(100, round(scores[state] * 100.0)))
    return state, confidence


# ── rendering (line 12 chip) ───────────────────────────────────────────────
def _hex_to_rgb(h: str) -> "tuple[int, int, int]":
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _tint(hex_color: str, alpha: float) -> str:
    """Blend the state color toward the panel bg by ``alpha`` -> a solid hex Qt always
    renders (rgba() support in Qt rich text is unreliable)."""
    r, g, b = _hex_to_rgb(hex_color)
    br, bg, bb = PANEL_BG
    R = round(r * alpha + br * (1.0 - alpha))
    G = round(g * alpha + bg * (1.0 - alpha))
    B = round(b * alpha + bb * (1.0 - alpha))
    return f"#{R:02x}{G:02x}{B:02x}"


def render_state_line(state: str, confidence: int) -> str:
    """HTML chip for line 12: ``★ STATE NN%`` on a confidence-scaled background tint."""
    score = confidence / 100.0
    alpha = ALPHA_MIN + ALPHA_SPAN * score
    bg = _tint(STATE_COLORS.get(state, STATE_COLORS["NEUTRAL"]), alpha)
    star = "★ " if score >= STAR_THRESHOLD else ""
    return (f"<span style='background-color:{bg};color:#f5f5f5'>"
            f"&nbsp;{star}{state} {confidence}%&nbsp;</span>")


def render_debug_lines(buckets: list, idx: int, b_mult: float, s_mult: float) -> "list[str]":
    """Calibration instrument (Mode 10 ``State Debug`` toggle) — two HTML lines for the stats
    box. Line 1: the TOP-3 states + scores (winner bold). Line 2: the WINNER's factor breakdown
    (the geomean inputs), with the BINDING (lowest) factor bold — that's the knob to raise to make
    the verdict pickier; a ``⌊floor⌋`` tag marks a squeeze whose gradient floor overrode the
    geomean. Reads the same ``_score_and_factors`` the verdict comes from, so it never lies about why."""
    scores, factors, floored = _score_and_factors(buckets, idx, b_mult, s_mult)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    winner = ranked[0][0]

    chips = []
    for rank, (st, sc) in enumerate(ranked[:3]):
        pct = max(1, min(100, round(sc * 100.0)))
        col = STATE_COLORS.get(st, STATE_COLORS["NEUTRAL"])
        body = f"{st} {pct}"
        chips.append(f"<span style='color:{col}'>{'<b>' + body + '</b>' if rank == 0 else body}</span>")
    line1 = "<span style='color:#7f8c8d'>DBG </span>" + " · ".join(chips)

    wf = factors.get(winner)
    if wf:
        bind = min(wf, key=wf.get)   # lowest factor = binding constraint = the tuning lever
        segs = []
        for name, val in wf.items():
            seg = f"{name}&nbsp;{val:.2f}"
            segs.append(f"<b>{seg}</b>" if name == bind else seg)
        tag = " <span style='color:#e67e22'>⌊floor⌋</span>" if winner in floored else ""
        line2 = "<span style='color:#7f8c8d'>why </span>" + " · ".join(segs) + tag
    else:
        line2 = "<span style='color:#7f8c8d'>why </span><i>NEUTRAL floor — no state cleared the bar</i>"
    return [line1, line2]
