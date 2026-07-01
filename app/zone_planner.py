"""Zone planner (ZONE_PLANNER_SPEC + addendum v1.1) — ``propose_zones(direction, anchor) -> ZonePlan``.

Terminal-side, STATELESS, ENVELOPE-only (addendum A3): cohort + first-passage + all geometry run over
``closed_buckets`` with zero daemon dependency. Reuses ``excursion.py`` (cohort matcher + first-passage
bracket) and the detectors ``calc_absorption`` / ``calc_quant_obs`` / ``_effort_ticks`` for the STRUCTURAL
anchors. Order blocks are usually empty on SOL (A4) → absorption carries the structure; a plan with no
structural anchor degrades (``confidence *= STAT_ONLY_CONF``) rather than lying.

Honesty rails (spec §15) enforced here: horizon in BUCKETS; signal bar excluded (excursion.py); fill-
conditional distributions re-based to the fill ``f`` (NOT the anchor ``P0``); ``eff_n`` never raw N;
incomplete-horizon anchors censored (cohort matcher); every width from MEASURED dispersion
(``_effort_ticks`` + spread), never a tick constant; low confidence -> faded; ``eff_n < MIN`` -> note only.

SHORT MIRRORS LONG by reflection: ``sgn = +1`` (long) / ``-1`` (short) flips every price comparison
(support<->resistance, below<->above, U<->D). Implemented once, direction-parameterised.

Build state: commit 4 ships the dataclasses, the orchestrator, and the two-pass ENTRY builder. The
STOP / TP / scale-out are PROVISIONAL placeholders (marked ``# commit 5/6/7``) so every commit returns a
coherent, testable ``ZonePlan``; commits 5-7 replace them one concern at a time.
"""
from __future__ import annotations

import math
import types
from dataclasses import dataclass, field
from typing import List, Optional

from . import config, excursion, quant_engine

TICK = config.TICK_SIZE
PD = config.PRICE_DECIMALS
_q = excursion.quantile


# --------------------------------------------------------------------------- #
# §2 — contract dataclasses
# --------------------------------------------------------------------------- #
@dataclass
class Anchor:
    direction: str            # "long" | "short"
    p0: float                 # anchor price = bucket close (or live-edge latest price)
    anchor_epoch: float       # closed_buckets.start_time of the anchor bucket
    anchor_idx: int           # index into the loaded closed_buckets[tf] list
    tf: str                   # volume-bucket tf ("1m"), NOT time
    is_live_edge: bool = False


@dataclass
class ZoneBox:
    kind: str                 # "entry" | "stop" | "tp"
    lo: float
    hi: float
    start_epoch: float
    end_epoch: float
    span_buckets: int
    line: float               # bright recommended level inside [lo, hi]
    prob: float               # entry->P(fill) | stop->P(stop first) | tp->P(touch before stop)
    exp_r: float              # this box's contribution to plan E[R] (R units)
    snapped_to: Optional[str] = None
    median_buckets_to_touch: Optional[int] = None   # tp only
    fidelity: str = "envelope"
    confidence: float = 1.0
    meta: dict = field(default_factory=dict)


@dataclass
class ZonePlan:
    direction: str
    entry: ZoneBox
    stop: ZoneBox
    tps: List[ZoneBox]
    scale_out: List[float]
    gross_exp_r: float
    net_exp_r: float
    cohort_n: int
    eff_n: int
    fidelity: str
    confidence: float
    warnings: List[str] = field(default_factory=list)
    gross_ci: Optional[dict] = None    # greedy-segment bootstrap band over eff_n on gross E[R] — LOAD-BEARING 1m
    er_bracket: Optional[tuple] = None  # uncertainty (commit 7); er_bracket = A2 [lo, hi] (hairline on 1m)
    cohort_eff_n: int = 0              # R1: greedy eff_n over ALL cohort members (the MIN_EFF_N adequacy gate)
    eff_n_floor: int = 0              # R1: ceil(n/H) conservative-floor annotation (divergence flag)


# --------------------------------------------------------------------------- #
# structural anchors (as-of the anchor bucket — NO look-ahead)
# --------------------------------------------------------------------------- #
def _structural(buckets: list, anchor_idx: int, tf: str):
    """Active OB + absorption marks as-of the anchor bucket. Both detectors replay their full
    lifecycle over ``buckets[:anchor_idx+1]`` (stateless) so 'active' == still standing at the anchor;
    no forward bucket is seen. OBs are usually empty on SOL (A4) — absorption is the workhorse."""
    hist = buckets[:anchor_idx + 1]
    try:
        marks = [m for m in quant_engine.calc_absorption(hist) if m.get("active")]
    except Exception:
        marks = []
    try:
        shim = types.SimpleNamespace(closed_buckets=hist)
        obs = [o for o in quant_engine.calc_quant_obs(shim, tf) if o.get("active")]
    except Exception:
        obs = []
    return obs, marks


def _entry_struct(obs: list, marks: list, p0: float, long: bool):
    """Ranked structural support (long) / resistance (short), first whose TOUCH edge sits on the
    pullback side of P0 (below for long, above for short). Rank 1 = OB near-edge, rank 2 = absorption
    edge (A4: no HVN tier in v1). Ties -> nearest edge to P0. Returns dict(edge, lo, hi, id, rank) or
    None."""
    cand = []
    for o in obs:
        if long and o.get("type") == "bullish" and o["top"] < p0:
            cand.append((1, o["top"], min(o["bottom"], o["top"]), max(o["bottom"], o["top"]), o["ob_id"]))
        elif (not long) and o.get("type") == "bearish" and o["bottom"] > p0:
            cand.append((1, o["bottom"], min(o["bottom"], o["top"]), max(o["bottom"], o["top"]), o["ob_id"]))
    for m in marks:
        if long and m["side"] == "BUY" and m["phi"] < p0:
            cand.append((2, m["phi"], m["plo"], m["phi"], m["id"]))
        elif (not long) and m["side"] == "SELL" and m["plo"] > p0:
            cand.append((2, m["plo"], m["plo"], m["phi"], m["id"]))
    if not cand:
        return None
    cand.sort(key=lambda c: (c[0], abs(p0 - c[1])))
    r, edge, blo, bhi, cid = cand[0]
    return dict(edge=edge, lo=blo, hi=bhi, id=cid, rank=r)


def _tp_magnets(obs: list, marks: list, buckets: list, anchor_idx: int, long: bool):
    """Opposing magnets for TP snapping (commit 6): long -> resistance ABOVE (bearish OB -> SELL
    absorption -> liq cluster); short -> support BELOW (bullish OB -> BUY absorption -> liq). Each:
    (rank, price, id). Liquidation clusters come from the anchor-window ``liquidations`` on the
    buckets (terminal-side, A4)."""
    out = []
    for o in obs:
        if long and o.get("type") == "bearish":
            out.append((1, o["bottom"], o["ob_id"]))          # near edge of supply = first touch
        elif (not long) and o.get("type") == "bullish":
            out.append((1, o["top"], o["ob_id"]))
    for m in marks:
        if long and m["side"] == "SELL":
            out.append((2, m["plo"], m["id"]))
        elif (not long) and m["side"] == "BUY":
            out.append((2, m["phi"], m["id"]))
    return out


# --------------------------------------------------------------------------- #
# geometry helpers
# --------------------------------------------------------------------------- #
def _tick(x: float) -> float:
    return round(x, PD)


def _apply_floor(lo: float, hi: float, floor: float):
    """Widen [lo,hi] symmetrically about its mid to at least ``floor`` (the thickness floor)."""
    w = hi - lo
    if w >= floor:
        return lo, hi
    mid = 0.5 * (lo + hi)
    return mid - 0.5 * floor, mid + 0.5 * floor


def _min_low(buckets: list, j: int, span: int) -> float:
    end = min(j + span, len(buckets) - 1)
    return min(float(buckets[i].low) for i in range(j + 1, end + 1))


def _max_high(buckets: list, j: int, span: int) -> float:
    end = min(j + span, len(buckets) - 1)
    return max(float(buckets[i].high) for i in range(j + 1, end + 1))


# --------------------------------------------------------------------------- #
# §4/§5 — two-pass ENTRY builder (structural ∩ pullback quantiles, fill-conditional re-base)
# --------------------------------------------------------------------------- #
def _build_entry(buckets, anchor, cohort, direction, H):
    """Two-pass winner resolution + fill-conditional re-base (spec §4/§5). Returns
    ``(entry_box, ctx)`` where ctx carries everything the STOP/TP/optimiser stages need:
    ``f`` (fill ref), ``filled`` (member idx), ``Uf``/``Df`` (fill-conditional favourable/adverse
    excursion per filled member, from ``f``), ``winners`` (converged target-first set), ``entry_span``,
    and the structural handles. NO look-ahead: excursions come from excursion.forward_extremes."""
    long = direction == "long"
    p0 = float(anchor.p0)
    members = list(cohort.members)
    n = len(buckets)
    fmax, fmin = excursion.forward_extremes(buckets, H)          # per absolute idx, signal bar excluded

    s = {j: float(buckets[j].close_price) for j in members}
    # pullback depth over the first ENTRY_PULLBACK_LOOKBACK buckets (magnitude, >= 0)
    LB = config.ENTRY_PULLBACK_LOOKBACK
    d = {}
    to_low = {}                                                  # buckets-to-pullback-extreme (for entry span)
    for j in members:
        if long:
            d[j] = max(0.0, s[j] - _min_low(buckets, j, LB))
        else:
            d[j] = max(0.0, _max_high(buckets, j, LB) - s[j])
        # offset of the deepest pullback extreme within the full horizon (1..H)
        end = min(j + H, n - 1)
        seq = [(float(buckets[i].low) if long else float(buckets[i].high)) for i in range(j + 1, end + 1)]
        to_low[j] = (seq.index(min(seq) if long else max(seq)) + 1) if seq else 1

    obs, marks = _structural(buckets, anchor.anchor_idx, anchor.tf)
    struct = _entry_struct(obs, marks, p0, long)
    effort = quant_engine._effort_ticks(buckets[anchor.anchor_idx].levels or {})
    floor_stat = TICK + config.THICK_DISP_MULT * effort * TICK   # spread proxy (1 tick) + 1 std intrabar
    struct_w = (struct["hi"] - struct["lo"]) if struct else 0.0
    floor = max(struct_w, floor_stat)

    def build_from(W):
        """Given a winner set W, build the entry band + fill ref + filled set + fill-conditional U/D."""
        dv = [d[j] for j in W] or [d[j] for j in members]
        q_lo = _q(dv, config.ENTRY_DEPTH_Q_LO) or 0.0            # shallow
        q_hi = _q(dv, config.ENTRY_DEPTH_Q_HI) or 0.0            # deep
        if long:
            stat_lo, stat_hi = p0 - q_hi, p0 - q_lo
        else:
            stat_lo, stat_hi = p0 + q_lo, p0 + q_hi
        # reconcile structural ∩ statistical
        g_agree = 1.0
        snapped = None
        if struct is None:
            elo, ehi = stat_lo, stat_hi
        else:
            ilo, ihi = max(stat_lo, struct["lo"]), min(stat_hi, struct["hi"])
            if ilo <= ihi:
                elo, ehi, snapped = ilo, ihi, struct["id"]
            else:                                                # disjoint -> stat wins, haircut by gap
                gap = (stat_lo - struct["hi"]) if stat_lo > struct["hi"] else (struct["lo"] - stat_hi)
                bw = max(stat_hi - stat_lo, TICK)
                g_agree = math.exp(-abs(gap) / (config.G_AGREE_LAMBDA * bw))
                elo, ehi = stat_lo, stat_hi
        elo, ehi = _apply_floor(elo, ehi, floor)
        elo, ehi = _tick(elo), _tick(ehi)
        f = _tick(0.5 * (elo + ehi))
        # entry span = q75 of buckets-to-pullback-extreme over W (then the setup goes stale)
        span = int(math.ceil(_q([to_low[j] for j in W] or [to_low[j] for j in members], 0.75) or 1))
        span = max(1, min(span, H))
        # filled = members whose forward path enters [elo,ehi] within the entry span
        if long:
            filled = [j for j in members if _min_low(buckets, j, span) <= ehi]
        else:
            filled = [j for j in members if _max_high(buckets, j, span) >= elo]
        # fill-conditional favourable (U) / adverse (D) excursion, re-based to f
        Uf, Df = {}, {}
        for j in filled:
            if long:
                Uf[j] = max(0.0, fmax[j] - f); Df[j] = max(0.0, f - fmin[j])
            else:
                Uf[j] = max(0.0, f - fmin[j]); Df[j] = max(0.0, fmax[j] - f)
        return dict(lo=elo, hi=ehi, f=f, span=span, filled=filled, Uf=Uf, Df=Df,
                    snapped=snapped, g_agree=g_agree)

    # ---- Pass 0 (§4): entry band + fill f built ONCE from the net-favourable seed, then FIXED. The
    #      seed U>=D is barrier-INDEPENDENT (no circularity). f, filled, Uf, Df, tp1 do NOT move after
    #      this — only the STOP and its target-first winner set iterate below (a tight fixed point). ----
    W0 = set(j for j in members if max(0.0, (fmax[j] - s[j]) if long else (s[j] - fmin[j]))
             >= max(0.0, (s[j] - fmin[j]) if long else (fmax[j] - s[j])))
    b = build_from(W0)
    f = b["f"]; filled = b["filled"]; Uf = b["Uf"]; Df = b["Df"]
    tp1off = _q([Uf[j] for j in filled], config.TP_QUANTILES[0]) or 0.0
    tp1 = (f + tp1off) if long else (f - tp1off)                 # FIXED (q50 favourable over all filled)

    def winners_at(stop):
        return set(j for j in filled
                   if 0 in excursion.first_passage_member(buckets, j, f, stop, [tp1],
                                                          direction, H, config.STOP_EXEC).reached_lo)

    def stop_of(W):
        adv = [Df[j] for j in W if j in Df] or [Df[j] for j in filled] or [0.0]
        heat = _q(adv, config.STOP_HEAT_Q) or 0.0
        return _tick((f - heat) if long else (f + heat))

    # The winners->stop map converges to a unique fixed point on most anchors (3-5 iters), but discrete
    # quantiles over a finite filled set can 2-cycle or JITTER in a low band that never quite clears 5%
    # set-shift (a handful of boundary members flip as q85(D) crosses a tick). Since f / Uf / Df — the
    # quantities that PROPAGATE to commits 5-7 — are fixed in pass 0 and do NOT iterate, and commit 5
    # RE-RESOLVES the real stop, the winner set only needs a deterministic, terminating resolution:
    # converge on set-shift when clean, else settle on the MEDIAN stop LEVEL over the post-transient tail.
    W = set(j for j in filled if j in W0)
    history = [("seed_filled", len(W))]
    converged = False; stabilized = False; stop_trail = []
    for _ in range(config.MAX_REFINE):
        st = stop_of(W)
        Wn = winners_at(st)
        shift = len(W ^ Wn) / max(1, len(W))
        history.append(("refine", len(Wn), round(shift, 4)))
        stop_trail.append(st); W = Wn
        if shift < config.WINNER_SHIFT_TOL:                       # clean fixed point
            converged = True
            break
    if not converged and stop_trail:                             # discrete jitter / cycle -> median-stop resolution
        tail = sorted(stop_trail[len(stop_trail) // 2:])         # drop the transient half
        med_stop = tail[len(tail) // 2]
        W = winners_at(med_stop)
        stabilized = True

    pfill = len(filled) / len(members) if members else 0.0
    conf = b["g_agree"]
    warns = []
    if struct is None:
        conf *= config.STAT_ONLY_CONF
        warns.append("no structural entry anchor")
    end_idx = min(anchor.anchor_idx + b["span"], n - 1)
    box = ZoneBox(kind="entry", lo=b["lo"], hi=b["hi"],
                  start_epoch=float(anchor.anchor_epoch), end_epoch=float(buckets[end_idx].start_time),
                  span_buckets=b["span"], line=f, prob=pfill, exp_r=0.0,
                  snapped_to=b["snapped"], fidelity="envelope", confidence=conf,
                  meta=dict(winner_history=history, converged=converged, stabilized=stabilized,
                            effort_ticks=effort, floor=floor, n_filled=len(filled), n_members=len(members)))
    ctx = dict(f=f, filled=filled, Uf=Uf, Df=Df, winners=W, entry_span=b["span"], tp1=tp1,
               obs=obs, marks=marks, fmax=fmax, fmin=fmin, warns=warns)
    return box, ctx


# --------------------------------------------------------------------------- #
# orchestrator
# --------------------------------------------------------------------------- #
def propose_zones(direction: str, anchor: Anchor, cohort_mode: str = "knn",
                  size: float = None, buckets: Optional[list] = None,
                  H: int = None) -> Optional[ZonePlan]:
    """Emit a ZonePlan for ``direction`` anchored at ``anchor``. Returns None on InsufficientSample
    (the tool then draws only the note). ``buckets`` injectable for tests; else loaded from the tf."""
    size = config.DEFAULT_SIZE if size is None else size
    H = config.H_DEFAULT if H is None else H
    if buckets is None:
        buckets = excursion.load_closed_buckets(anchor.tf)
    long = direction == "long"

    try:
        cohort = excursion.build_cohort(buckets, anchor.anchor_idx, direction, mode=cohort_mode, H=H)
    except excursion.InsufficientSample:
        return None

    entry, ctx = _build_entry(buckets, anchor, cohort, direction, H)
    stop = _build_stop(buckets, anchor, cohort, ctx, direction, H)             # commit 5
    tps = _build_tps(buckets, anchor, cohort, ctx, stop, direction, H)         # commit 6
    scale_out, gross, gross_ci, er_bracket = _optimise_scaleout(buckets, ctx, stop, tps, direction, H)  # commit 7

    warns = list(ctx.get("warns", []))
    if ctx.get("amb_frac", 0.0) > config.AMB_WARN:
        warns.append("amb_frac %.2f > AMB_WARN — same-bucket order uncertain" % ctx["amb_frac"])
    # R1: g_effn uses the PLAN eff_n = greedy disjoint count over the E[R] members (== the band's n_seg),
    # ONE source of truth for the haircut and the band. cohort.eff_n (over all members) is the MIN_EFF_N
    # adequacy gate. Two axes stay separate: g_effn (sample sufficiency) vs the E[R] CI (signal uncertainty).
    plan_eff_n = gross_ci["n_seg"] if gross_ci else cohort.eff_n
    conf = min(entry.confidence, stop.confidence, *(t.confidence for t in tps)) if tps else entry.confidence
    conf *= plan_eff_n / (plan_eff_n + config.N0_EFFN)                          # g_effn (§10, R1)
    return ZonePlan(direction=direction, entry=entry, stop=stop, tps=tps,
                    scale_out=scale_out, gross_exp_r=gross, net_exp_r=gross,    # net==gross until cost (commit 8)
                    cohort_n=cohort.n_used, eff_n=plan_eff_n, fidelity="envelope",
                    confidence=conf, gross_ci=gross_ci, er_bracket=er_bracket, cohort_eff_n=cohort.eff_n,
                    eff_n_floor=cohort.eff_n_floor,
                    warnings=warns + ["gross E[R] — cost model pending (commit 8)"])


# --------------------------------------------------------------------------- #
# PROVISIONAL stubs — replaced by commits 5 / 6 / 7. Kept minimal + coherent so commit 4 returns a
# valid, testable ZonePlan. Each derives from the two-pass provisional levels already in ctx.
# --------------------------------------------------------------------------- #
def _wick_buffer(buckets, members, delta, direction, H):
    """Measured wick buffer w = q75(overshoot) over cohort members that HELD (close never broke the
    member-frame invalidation). ``delta`` = invalidation distance below (long) / above (short) each
    member's signal. overshoot = how far the WICK poked beyond the level while the CLOSE held — this is
    exactly what a touch-executed stop must sit beyond so a poke-that-held doesn't phantom-stop you (§6)."""
    long = direction == "long"
    over = []
    n = len(buckets)
    for j in members:
        s_j = float(buckets[j].close_price)
        inv_j = (s_j - delta) if long else (s_j + delta)
        end = min(j + H, n - 1)
        rng = range(j + 1, end + 1)
        if not rng:
            continue
        if long:
            ml = min(float(buckets[i].low) for i in rng)
            held = all(float(buckets[i].close_price) > inv_j for i in rng)
            if held and ml < inv_j:                              # held AND the WICK poked the level
                over.append(inv_j - ml)
        else:
            mh = max(float(buckets[i].high) for i in rng)
            held = all(float(buckets[i].close_price) < inv_j for i in rng)
            if held and mh > inv_j:
                over.append(mh - inv_j)
    # condition on poke-and-hold: a held member that never reached the level says nothing about the wick
    # buffer, so it must NOT be averaged in as a 0 (that zeroed q75). Empty set -> 0 (level too deep to poke).
    return _q(over, config.WICK_BUFFER_Q) or 0.0


def _single_tp_er(buckets, members, f, stop, tp1, direction, H):
    """Provisional single-TP E[R] + P_dn at a candidate stop, first-passage (touch). Commit 7 replaces
    this with the full-ladder replay; here it only ranks the stop band (§6 'argmax stop in [wide,tight]')."""
    long = direction == "long"
    risk = abs(f - stop)
    if risk <= 0:
        return -1e9, 1.0
    er = 0.0; pdn = 0
    for j in members:
        p = excursion.first_passage_member(buckets, j, f, stop, [tp1], direction, H, config.STOP_EXEC)
        if 0 in p.reached_lo:
            er += ((tp1 - f) if long else (f - tp1)) / risk
        elif p.stopped:
            er += -1.0; pdn += 1
        elif p.timeout and p.mtm_px is not None:
            er += ((p.mtm_px - f) if long else (f - p.mtm_px)) / risk
    m = max(1, len(members))
    return er / m, pdn / m


def _build_stop(buckets, anchor, cohort, ctx, direction, H):
    """§6 STOP box. Tight edge = q85(winner MAE). Wide edge = close-based structural invalidation
    (OB far edge / absorption plo·0.999) MINUS the measured wick buffer (touch-honest; buffer disabled
    under STOP_EXEC='close' so it's never double-counted). Recommended line = argmax single-TP E[R] over
    [wide, tight] — the EXACT price the evaluator tests under touch (draw-what-you-measure). Commit 7
    re-searches this band jointly with the scale-out weights."""
    long = direction == "long"
    f = ctx["f"]; filled = ctx["filled"]; Df = ctx["Df"]; winners = ctx["winners"]; tp1 = ctx["tp1"]
    p0 = float(anchor.p0)
    Dwin = [Df[j] for j in winners if j in Df] or [Df[j] for j in filled] or [0.0]
    t = _q(Dwin, config.STOP_HEAT_Q) or 0.0
    stop_tight = (f - t) if long else (f + t)

    struct = _entry_struct(ctx["obs"], ctx["marks"], p0, long)
    warns = []; snapped = None; wick = 0.0; inval = None
    if struct is not None:
        inval = (struct["lo"] if long else struct["hi"]) if struct["rank"] == 1 \
            else (struct["lo"] * 0.999 if long else struct["hi"] * 1.001)   # OB far edge / absorption death
        delta = (p0 - inval) if long else (inval - p0)
        wick = _wick_buffer(buckets, filled, delta, direction, H) if config.STOP_EXEC == "touch" else 0.0
        stop_wide = (inval - wick) if long else (inval + wick)
        snapped = struct["id"]
    else:                                                                   # no structural invalidation (A4 common)
        wq = _q(Dwin, config.STOP_WIDE_Q) or t
        stop_wide = (f - wq) if long else (f + wq)
        warns.append("no structural stop anchor")

    lo, hi = sorted((_tick(stop_wide), _tick(stop_tight)))
    # argmax single-TP E[R] over the band (touch); the recommended line == what P_dn is measured against
    N = config.STOP_GRID_N
    cands = [_tick(lo + (hi - lo) * k / (N - 1)) for k in range(N)] if (N > 1 and hi > lo) else [_tick(0.5 * (lo + hi))]
    best_line, best_er, best_pdn = cands[0], -1e18, 1.0
    for stop in cands:
        er, pdn = _single_tp_er(buckets, filled, f, stop, tp1, direction, H)
        if er > best_er:
            best_line, best_er, best_pdn = stop, er, pdn
    line = best_line
    heat_cleared = (sum(1 for dwin in Dwin if dwin < abs(f - line)) / len(Dwin)) if Dwin else 0.0
    conf = 1.0 if struct is not None else config.STAT_ONLY_CONF
    ctx.setdefault("warns", []).extend(warns)
    return ZoneBox(kind="stop", lo=lo, hi=hi, start_epoch=float(anchor.anchor_epoch),
                   end_epoch=float(buckets[min(anchor.anchor_idx + H, len(buckets) - 1)].start_time),
                   span_buckets=H, line=line, prob=best_pdn, exp_r=0.0, snapped_to=snapped,
                   fidelity="envelope", confidence=conf,
                   meta=dict(stop_wide=_tick(stop_wide), stop_tight=_tick(stop_tight), wick_buffer=wick,
                             invalidation=(_tick(inval) if inval is not None else None),
                             risk_ticks=round(abs(f - line) / TICK, 1), risk_pct=round(100 * abs(f - line) / f, 3),
                             clears_winner_heat=heat_cleared, stop_exec=config.STOP_EXEC))


def _build_tps(buckets, anchor, cohort, ctx, stop, direction, H):
    """§7 nested TP boxes. Levels = f +/- q{50,75,90}(favourable excursion | filled); band thickness =
    local quantile width around each q_k. Each level SNAPS to the nearest opposing magnet within SNAP_TOL
    (bearish OB near-edge -> SELL absorption -> liquidation cluster; A4: no HVN) — real resistance beats a
    bare quantile. prob = P(reach level_k before stop) from first-passage (monotone-decreasing in k);
    median_buckets_to_touch from the passage offsets. exp_r filled by the commit-7 optimiser."""
    long = direction == "long"
    f = ctx["f"]; filled = ctx["filled"]; Uf = ctx["Uf"]
    fav = [Uf[j] for j in filled] or [0.0]
    magnets = _tp_magnets(ctx["obs"], ctx["marks"], buckets, anchor.anchor_idx, long)
    snap_tol = config.SNAP_TOL_TICKS * TICK
    dq = config.TP_BW_DQ

    levels = []; boxes_meta = []
    prev = f
    for k, qk in enumerate(config.TP_QUANTILES):
        off = _q(fav, qk) or 0.0
        lvl = (f + off) if long else (f - off)
        # local band width = half the favourable-excursion spread across [qk-dq, qk+dq]
        wlo = _q(fav, max(0.0, qk - dq)) or 0.0
        whi = _q(fav, min(1.0, qk + dq)) or 0.0
        thick = max(0.5 * abs(whi - wlo), TICK)
        # snap to the highest-ranked opposing magnet within SNAP_TOL of the quantile level
        snapped = None
        near = sorted((m for m in magnets if abs(m[1] - lvl) <= snap_tol), key=lambda m: (m[0], abs(m[1] - lvl)))
        if near:
            lvl = near[0][1]; snapped = near[0][2]
        # keep strictly nested away from f (a snap can reorder)
        lvl = _tick(lvl)
        if long:
            lvl = max(lvl, _tick(prev + TICK))
        else:
            lvl = min(lvl, _tick(prev - TICK))
        prev = lvl
        levels.append(lvl); boxes_meta.append((thick, snapped, qk))

    # probabilities + median-to-touch from ONE first-passage over the nested ladder (touch)
    ps = [excursion.first_passage_member(buckets, j, f, stop.line, levels, direction, H, config.STOP_EXEC)
          for j in filled]
    m = max(1, len(ps))
    tps = []
    for k in range(len(levels)):
        prob = sum(1 for p in ps if k in p.reached_lo) / m               # clean point (bracket hairline on 1m)
        offs = sorted(p.touch_off[k] for p in ps if k in (p.touch_off or {}))
        med = offs[len(offs) // 2] if offs else None
        thick, snapped, qk = boxes_meta[k]
        lvl = levels[k]
        lo, hi = _tick(lvl - 0.5 * thick), _tick(lvl + 0.5 * thick)
        tps.append(ZoneBox(kind="tp", lo=lo, hi=hi, start_epoch=float(anchor.anchor_epoch),
                           end_epoch=float(buckets[min(anchor.anchor_idx + H, len(buckets) - 1)].start_time),
                           span_buckets=H, line=lvl, prob=prob, exp_r=0.0, snapped_to=snapped,
                           median_buckets_to_touch=med, fidelity="envelope",
                           confidence=(1.0 if snapped else config.STAT_ONLY_CONF),
                           meta=dict(quantile=qk, thickness=thick, R=abs(lvl - f) / max(TICK, abs(f - stop.line)))))
    return tps


def _er_replay(passages, levels, f, stop, direction, w, assign="lo"):
    """§9 per-member ladder replay -> E[R]. ``assign`` picks which A2 collision assignment to read
    (reached_lo = stop-first / reached_hi = target-first). Scale out w_k at each nested TP reached before
    the stop; the un-taken remainder exits at -1R (stopped) or mark-to-market (timeout)."""
    long = direction == "long"
    risk = abs(f - stop)
    if risk <= 0:
        return -1e18
    K = len(levels); tot = 0.0
    for p in passages:
        reached = set(p.reached_lo if assign == "lo" else p.reached_hi)
        remaining = 1.0; rj = 0.0
        for k in range(K):
            if k in reached:
                rk = ((levels[k] - f) if long else (f - levels[k])) / risk
                rj += w[k] * rk; remaining -= w[k]
            else:
                break
        if remaining > 1e-12:
            if p.stopped:
                rj += remaining * (-1.0)
            elif p.timeout and p.mtm_px is not None:
                rj += remaining * (((p.mtm_px - f) if long else (f - p.mtm_px)) / risk)
        tot += rj
    return tot / max(1, len(passages))


def _weight_grid(K):
    """Coarse simplex grid: w_k >= W_MIN, sum == 1 (K<=3 -> <=2 free weights)."""
    step = config.WEIGHT_STEP; wmin = config.W_MIN
    out = []
    if K == 1:
        return [(1.0,)]
    n = int(round((1.0 - K * wmin) / step))
    if K == 2:
        for i in range(n + 1):
            w0 = wmin + i * step
            out.append((round(w0, 4), round(1.0 - w0, 4)))
    else:  # K == 3
        for i in range(n + 1):
            w0 = wmin + i * step
            for j in range(n - i + 1):
                w1 = wmin + j * step
                w2 = 1.0 - w0 - w1
                if w2 >= wmin - 1e-9:
                    out.append((round(w0, 4), round(w1, 4), round(w2, 4)))
    return out or [tuple(1.0 / K for _ in range(K))]


def _best_weights(passages, levels, f, stop, direction):
    best_w, best_er = None, -1e18
    for w in _weight_grid(len(levels)):
        er = _er_replay(passages, levels, f, stop, direction, w, "lo")
        if er > best_er:
            best_er, best_w = er, w
    return best_w, best_er


def _greedy_bootstrap(rvals, idxs, H, n_boot):
    """GREEDY-SEGMENT bootstrap of E[R] — resamples the SAME non-overlapping units that define eff_n
    (ruling R1), so the band and the g_effn haircut share ONE source of truth. Members sorted by index
    are cut into a new segment whenever a member starts a window disjoint from the current segment's start
    (idx - seg_start >= H); the number of segments == excursion.eff_n_packed. Resample segments with
    replacement (preserving within-segment dependence). Correct for contiguous (-> ceil(n/H) segments),
    scattered (-> isolated-group count), and mixed cohorts alike."""
    import random
    n = len(rvals)
    if n < 2:
        return None
    order = sorted(range(n), key=lambda i: idxs[i])
    r = [rvals[i] for i in order]; ix = [idxs[i] for i in order]
    segs = [[r[0]]]; seg_start = ix[0]
    for k in range(1, n):
        if ix[k] - seg_start >= H:
            segs.append([r[k]]); seg_start = ix[k]                # new disjoint window == a kept eff_n unit
        else:
            segs[-1].append(r[k])
    ns = len(segs)
    rng = random.Random(0xC0FFEE)
    means = []
    for _ in range(n_boot):
        samp = []
        while len(samp) < n:
            samp.extend(segs[rng.randint(0, ns - 1)])
        samp = samp[:n]
        means.append(sum(samp) / len(samp))
    means.sort()

    def pct(p):
        return means[min(n_boot - 1, max(0, int(p * n_boot)))]
    return dict(p16=pct(0.16), p84=pct(0.84), p2_5=pct(0.025), p97_5=pct(0.975),
                n_seg=ns, n_boot=n_boot)


def _optimise_scaleout(buckets, ctx, stop, tps, direction, H):
    """§9 — jointly search the stop over [wide, tight] and the scale-out weights over the simplex to
    maximise the CLEAN-point E[R] (A2). Mutates ``stop`` and ``tps`` in place (line/prob/exp_r), returns
    (scale_out, gross_ER, gross_ci, er_bracket). Bootstrap band is the load-bearing 1m uncertainty."""
    long = direction == "long"
    f = ctx["f"]; filled = ctx["filled"]
    levels = [t.line for t in tps]; K = len(levels)
    lo, hi = stop.lo, stop.hi
    N = config.STOP_GRID_N
    cands = [_tick(lo + (hi - lo) * k / (N - 1)) for k in range(N)] if (N > 1 and hi > lo) else [stop.line]

    best = None  # (stop, w, er_clean, passages_all)
    for st in cands:
        ps_all = [excursion.first_passage_member(buckets, j, f, st, levels, direction, H, config.STOP_EXEC)
                  for j in filled]
        clean = [p for p in ps_all if p.clean] or ps_all
        w, er = _best_weights(clean, levels, f, st, direction)
        if best is None or er > best[2]:
            best = (st, w, er, ps_all)
    best_stop, best_w, gross, ps_all = best
    clean = [p for p in ps_all if p.clean] or ps_all

    # A2 bracket on gross E[R] (hairline on 1m): lo=stop-first over all, hi=target-first over all
    er_lo = _er_replay(ps_all, levels, f, best_stop, direction, best_w, "lo")
    er_hi = _er_replay(ps_all, levels, f, best_stop, direction, best_w, "hi")

    # block-bootstrap band over the clean per-member R at the chosen geometry
    risk = abs(f - best_stop)
    rvals, idxs = [], []
    for p in clean:
        reached = set(p.reached_lo); remaining = 1.0; rj = 0.0
        for k in range(K):
            if k in reached:
                rk = ((levels[k] - f) if long else (f - levels[k])) / max(TICK, risk)
                rj += best_w[k] * rk; remaining -= best_w[k]
            else:
                break
        if remaining > 1e-12:
            if p.stopped:
                rj += remaining * (-1.0)
            elif p.timeout and p.mtm_px is not None:
                rj += remaining * (((p.mtm_px - f) if long else (f - p.mtm_px)) / max(TICK, risk))
        rvals.append(rj); idxs.append(p.idx)
    ci = _greedy_bootstrap(rvals, idxs, H, config.BOOTSTRAP_N)

    # ---- write the chosen geometry back into the boxes ----
    m = max(1, len(ps_all))
    stop.line = best_stop
    stop.prob = sum(1 for p in ps_all if p.stopped and not p.reached_lo) / m       # P_dn at the chosen line
    stop.meta["risk_ticks"] = round(risk / TICK, 1); stop.meta["risk_pct"] = round(100 * risk / f, 3)
    tp_leg_sum = 0.0
    for k, t in enumerate(tps):
        pr = sum(1 for p in ps_all if k in p.reached_lo) / m
        rk = ((levels[k] - f) if long else (f - levels[k])) / max(TICK, risk)
        t.prob = pr; t.line = levels[k]
        t.exp_r = best_w[k] * rk * pr                                              # TP leg contribution
        t.meta["R"] = rk; tp_leg_sum += t.exp_r
    stop.exp_r = gross - tp_leg_sum                                                # stop + timeout legs
    return list(best_w), gross, ci, (round(er_lo, 4), round(er_hi, 4))
