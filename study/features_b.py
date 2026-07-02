"""T2 — B-scope Mode-10 panels over the 16-bucket BASE selection [i-15, i] (entry-legal).

FAITHFUL replication of the terminal's own _refresh_selection_stats math, reusing region_state pure
functions verbatim (absorption_series / eff_agg_series / eff_agg_from_absorption / rolling_share /
trailing_exhaustion / envelope_symmetric) with the exact constants and the badge LOCKED-index rule
(_ali = len-1-LIVE_PANEL_WINDOW//2). Panels computed this tranche: S (stats), P1 ABSORPTION, P2 EFF-AGG,
P3 E/R, P4 EXHAUSTION (core). Phase (P5-7), large/small (P8, needs size_thr), and lean/smoothed (P9/P0)
are deferred to T2b (their terminal state machines) — never guessed.
"""
from __future__ import annotations
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from app import region_state as R, bucket_state as BS, config  # noqa

LW = config.LIVE_PANEL_WINDOW              # 15
AW = config.ABSORP_VOL_WINDOW             # 50
FW = config.EFF_AGG_FORCE_WINDOW          # 50
LOCK = LW // 2                            # 7  -> badge reads len-1-LOCK
PREMAX = LW + AW                          # 65 pre-roll cap
SEL = 16                                  # base selection length (15 priors + entry)


def _slope(ys):
    n = len(ys)
    if n < 2:
        return None
    mx = (n - 1) / 2.0; my = sum(ys) / n
    den = sum((k - mx) ** 2 for k in range(n))
    return sum((k - mx) * (ys[k] - my) for k in range(n)) / den if den else 0.0


def _zero_cross(vals, mid=0.5):
    c = 0
    for k in range(1, len(vals)):
        if (vals[k - 1] - mid) == 0:
            continue
        if ((vals[k] - mid) < 0) != ((vals[k - 1] - mid) < 0):
            c += 1
    return c


def _badge_share(ext_bull, ext_ser_or_bear, pre0, higher_is_strong):
    """Replicate a lean panel's smoothed badge: rolling_share over LW, drop the pre-roll, read the LOCKED
    index. Returns (bull_pct, bear_pct, spread_pct, dominant_is_strong_side)."""
    sh = R.rolling_share(ext_bull, ext_ser_or_bear, LW)[pre0:]
    if not sh:
        return None, None, None, None
    bear = [1.0 - s for s in sh]
    ali = len(sh) - 1 - LOCK
    bi = ali if ali >= 0 else len(sh) - 1
    b, r = sh[bi], bear[bi]
    strong_bull = (b > r) if higher_is_strong else (b < r)
    return b * 100.0, r * 100.0, abs(b - r) * 100.0, strong_bull


def compute_bscope(snaps, rs, i, sel_len=SEL):
    """B-scope panel values for entry bucket i (selection [i-sel_len+1, i]). rs = precomputed repo series.
    ``sel_len`` defaults to the study's frozen 16 (T1/T2 reproducibility); the frame-sweep passes 1..15."""
    out = {}
    lo, hi = i - sel_len + 1, i
    sel = snaps[lo:hi + 1]
    n = len(snaps)
    pre0 = min(lo, PREMAX)
    extp = snaps[lo - pre0:hi + 1]
    Lp = len(extp)

    cv = [float(b.get("curr_vol", 0.0)) for b in sel]
    bv = [float(b.get("buy_vol", 0.0)) for b in sel]
    sv = [float(b.get("sell_vol", 0.0)) for b in sel]
    sumcv = sum(cv) or 1.0

    # ---- P1 ABSORPTION (totals from the full-history per-bucket series = rs; badge from pre-roll) ----
    absb_sl = rs["bull"][lo:hi + 1]; absr_sl = rs["bear"][lo:hi + 1]; s_sl = rs["sval"][lo:hi + 1]
    abs_bull, abs_bear = sum(absb_sl), sum(absr_sl)
    eb, er, esv = R.absorption_series(extp, 0, Lp - 1, AW)
    b1, r1, sp1, strong1 = _badge_share(eb, er, pre0, higher_is_strong=False)   # absorption: LOWER share = strong
    out["P1.01"], out["P1.02"], out["P1.03"] = b1, r1, sp1
    out["P1.04"] = ("bull" if strong1 else "bear") if strong1 is not None else None
    out["P1.05"], out["P1.06"] = abs_bull, abs_bear
    out["P1.07"] = (abs_bull + abs_bear) / sumcv
    sh1 = R.rolling_share(eb, er, LW)[pre0:]
    out["P1.08"] = _slope(sh1)
    tot_abs = [absb_sl[k] + absr_sl[k] for k in range(sel_len)]
    out["P1.09"] = max(range(sel_len), key=lambda k: tot_abs[k])
    out["P1.10"] = sum(1 for s in s_sl if s is not None and s >= 0.60)
    _t = absb_sl[-1] + absr_sl[-1]
    out["P1.11"] = (absb_sl[-1] / _t * 100.0) if _t > 0 else 50.0

    # ---- P2 EFF-AGG ----
    eff_bull_sl = rs["effb"][lo:hi + 1]; eff_bear_sl = rs["effr"][lo:hi + 1]
    eff_bull, eff_bear = sum(eff_bull_sl), sum(eff_bear_sl)
    e2b, e2r, _f = R.eff_agg_series(extp, 0, Lp - 1, AW, FW)
    b2, r2, sp2, strong2 = _badge_share(e2b, e2r, pre0, higher_is_strong=True)   # eff-agg: HIGHER share = strong
    out["P2.01"], out["P2.02"], out["P2.03"] = b2, r2, sp2
    out["P2.04"] = ("bull" if strong2 else "bear") if strong2 is not None else None
    out["P2.05"], out["P2.06"] = eff_bull, eff_bear
    out["P2.07"] = (eff_bull + eff_bear) / sumcv
    sh2 = R.rolling_share(e2b, e2r, LW)[pre0:]
    out["P2.08"] = _slope(sh2)
    tot_eff = [eff_bull_sl[k] + eff_bear_sl[k] for k in range(sel_len)]
    out["P2.09"] = max(range(sel_len), key=lambda k: tot_eff[k])
    out["P2.10"] = _zero_cross(sh2, 0.5)
    _te = eff_bull_sl[-1] + eff_bear_sl[-1]
    out["P2.11"] = (eff_bull_sl[-1] / _te * 100.0) if _te > 0 else 50.0

    # ---- P3 E/R ----
    ber = [float(b.get("buyer_er", 0.0)) for b in extp]
    ser = [float(b.get("seller_er", 0.0)) for b in extp]
    b3, r3, sp3, strong3 = _badge_share(ber, ser, pre0, higher_is_strong=True)
    out["P3.01"], out["P3.02"], out["P3.03"] = b3, r3, sp3
    out["P3.04"] = ("buyer" if strong3 else "seller") if strong3 is not None else None
    bmul = rs["bmult"][lo:hi + 1]; smul = rs["smult"][lo:hi + 1]
    out["P3.05"] = sum(bmul) / sel_len
    out["P3.06"] = sum(smul) / sel_len
    out["P3.07"] = sum(1 for k in range(sel_len) if max(bmul[k], smul[k]) >= 1.5)
    out["P3.08"] = sum(1 for k in range(sel_len) if bmul[k] >= 1.5 and smul[k] >= 1.5)
    out["P3.09"] = max(range(sel_len), key=lambda k: max(bmul[k], smul[k]))
    _tb = ber[-1] + ser[-1]
    out["P3.10"] = (ber[-1] / _tb * 100.0) if _tb > 0 else 50.0

    # ---- P4 EXHAUSTION (core: trailing_exhaustion -> symmetric envelope, exact crossover count) ----
    sel_exh = R.trailing_exhaustion(extp, pre0, Lp - 1, LW, config.EXH_MEASURE, config.EXH_SEL_MIN_WINDOW)
    exb = [e[0] for e in sel_exh]; exr = [e[1] for e in sel_exh]
    sb = R.envelope_symmetric(exb, config.EXH_RELEASE); sr = R.envelope_symmetric(exr, config.EXH_RELEASE)
    e1, e2 = sb[-1], sr[-1]
    out["P4.01"] = max(e1, e2) * 100.0
    out["P4.02"] = "bull" if e1 > e2 else "bear"
    out["P4.03"] = max(e1, e2) * 100.0
    out["P4.04"] = min(e1, e2) * 100.0
    out["P4.05"] = abs(e1 - e2) * 100.0
    peak_i = max(range(len(sb)), key=lambda k: max(sb[k], sr[k]))
    out["P4.09"] = max(sb[peak_i], sr[peak_i]) * 100.0        # peak gated score (position in P4.09b via peak_i)
    d = [sb[k] - sr[k] for k in range(len(sb))]
    P = config.EXH_CROSS_PERSIST; crosses = 0
    for k in range(1, len(d)):
        if d[k - 1] == 0 or d[k] == 0 or (d[k - 1] < 0) == (d[k] < 0):
            continue
        newneg = d[k] < 0
        if all((d[j] < 0) == newneg for j in range(k, min(len(d), k + P))):
            crosses += 1
    out["P4.10"] = crosses
    # P4.06/07 (fire counts), P4.08 (buckets since fire), P4.11 (weakest gating leg) -> deferred (T2b)

    # ---- S stats box (pure selection aggregates) ----
    opL = sum(float(b.get("opL", 0.0)) for b in sel); opS = sum(float(b.get("opS", 0.0)) for b in sel)
    clL = sum(float(b.get("clL", 0.0)) for b in sel); clS = sum(float(b.get("clS", 0.0)) for b in sel)
    sumbuy, sumsell = sum(bv), sum(sv)
    delta = sumbuy - sumsell
    doi = (opL + opS) - (clL + clS)
    dur = float(sel[-1].get("end_time", 0.0)) - float(sel[0].get("start_time", 0.0))
    hi_p = max(float(b.get("high", 0.0)) for b in sel); lo_p = min(float(b.get("low", 0.0)) for b in sel)
    c0 = float(sel[0].get("open", 0.0)); c1 = float(sel[-1].get("close", 0.0))
    out["S.01"] = sel_len
    out["S.02"] = dur
    out["S.03"] = sumcv
    out["S.04"] = "%.1f | %.1f" % (sumsell, sumbuy)
    out["S.05"] = delta
    out["S.06"] = doi
    out["S.07"] = opL; out["S.08"] = opS; out["S.09"] = clL; out["S.10"] = clS
    out["S.11"] = sum(bmul) / sel_len                    # aggregate B-E/R multiple (mean exhaustion buyer z-mult)
    out["S.12"] = sum(smul) / sel_len
    vels = [(float(sel[k].get("curr_vol", 0.0)) /
             max(1.0, float(sel[k].get("end_time", 0.0)) - float(sel[k].get("start_time", 0.0))))
            for k in range(sel_len)]
    out["S.13"] = sum(vels) / sel_len
    out["S.14"] = (c1 - c0) / c0 * 100.0 if c0 else None    # net % change over the selection

    return {"B-" + k: v for k, v in out.items()}
