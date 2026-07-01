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
    tps = _build_tps(buckets, anchor, cohort, ctx, direction, H)               # commit 6
    scale_out, gross = _optimise_scaleout(buckets, ctx, stop, tps, direction, H)   # commit 7

    warns = list(ctx.get("warns", []))
    conf = min(entry.confidence, stop.confidence, *(t.confidence for t in tps)) if tps else entry.confidence
    conf *= cohort.eff_n / (cohort.eff_n + config.N0_EFFN)                      # g_effn (§10)
    return ZonePlan(direction=direction, entry=entry, stop=stop, tps=tps,
                    scale_out=scale_out, gross_exp_r=gross, net_exp_r=gross,    # net==gross until cost (commit 8)
                    cohort_n=cohort.n_used, eff_n=cohort.eff_n, fidelity="envelope",
                    confidence=conf, warnings=warns + ["gross E[R] — cost model pending (commit 8)"])


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


def _build_tps(buckets, anchor, cohort, ctx, direction, H):    # commit 6 replaces
    long = direction == "long"
    f = ctx["f"]
    fav = [ctx["Uf"][j] for j in ctx["filled"]] or [0.0]
    lvl = _tick(f + (_q(fav, config.TP_QUANTILES[0]) or 0.0) * (1 if long else -1))
    return [ZoneBox(kind="tp", lo=lvl, hi=lvl, start_epoch=float(anchor.anchor_epoch),
                    end_epoch=float(anchor.anchor_epoch), span_buckets=H, line=lvl, prob=0.0, exp_r=0.0,
                    fidelity="envelope", confidence=1.0, meta=dict(provisional=True))]


def _optimise_scaleout(buckets, ctx, stop, tps, direction, H):  # commit 7 replaces
    return [1.0 for _ in tps], 0.0
