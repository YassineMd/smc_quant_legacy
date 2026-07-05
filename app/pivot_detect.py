"""PIVOT INDICATOR detector — the FROZEN S5j-r5 confluence + entry, ported to run LIVE on the terminal's
buckets. Self-contained: reuses ONLY app.region_state + app.config (the same panel math the study calls),
so it is import-safe (no study side-effects) and bar-for-bar identical to study/s5j_final.py.

Rule (S5j-r5), per bar b, LONG (short mirrors):
  leg 1'' : on the 100-bar selection [b-99, b], the two most-recent LOCKED P0 crosses are both up AND the
            newest is the +50 EXTREME cross (settling dots never count).
  leg 2   : locked eff-agg badge spread (2*e_sh-1)*100 >= 65.
  leg 3   : phase dominant == START/DURING on the OWN table AND != START/DURING on the opposite (two-sided).
  leg 4   : locked P6 phase spread up-dn >= 15.
  leg 5   : EXISTS-form range context — close < max open over the zone [b-99, b-59] (N in 60..100).
  fire    : all five AND-ed.
Entry (WAIT-baseline-touch): MKT if the fire bar already sits at/through the moving POC baseline with the
  right colour; else the first later bar whose wick touches the baseline (low<=line long / high>=line short)
  and closes the right colour. Entry price = that bar's close.

detect_pivots(buckets) -> list of {det_i, entry_i (or None), side} over the whole window (no blackout — the
selection is the scope). Feed a window that includes >= ~FIRST bars of lookback before the region of interest
so legs 1/5 and the phase EMA have their context.
"""
from __future__ import annotations
import math
import numpy as np

from . import region_state as R, config

LW = config.LIVE_PANEL_WINDOW          # 15
LOCK = LW // 2                         # 7
W_SEL = 64                             # phase-trajectory trailing selection
SELW = 100                             # leg 1'' selection width
FIRST = 100                            # leg 5 needs the full 100-bar zone
PHASE_NAMES = ("BEFORE", "STARTDUR", "END")


# --- global series (setups_S3.p9_global, verbatim) -------------------------------------------------
def _p9_global(snaps):
    n = len(snaps)
    ab, ar, sval = R.absorption_series(snaps, 0, n - 1, config.ABSORP_VOL_WINDOW)
    a_sh = np.array(R.rolling_share(ab, ar, LW), float)
    eb, er_, _ = R.eff_agg_from_absorption(snaps, 0, n - 1, config.EFF_AGG_FORCE_WINDOW, sval)
    e_sh = np.array(R.rolling_share(eb, er_, LW), float)
    rb = [s.get("buyer_er", 0.0) for s in snaps]; rs_ = [s.get("seller_er", 0.0) for s in snaps]
    r_sh = np.array(R.rolling_share(rb, rs_, LW), float)
    lean = (1 - 2 * a_sh) * 100 + (2 * e_sh - 1) * 100 + (2 * r_sh - 1) * 100
    ex = R.trailing_exhaustion(snaps, 0, n - 1, LW, config.EXH_MEASURE, config.EXH_SEL_MIN_WINDOW)
    s4 = np.empty(n); hold = 0.0
    for k, (b4, s4_) in enumerate(ex):
        inst = (s4_ - b4) * 100.0
        if abs(inst) > 1e-9:
            hold = inst
        s4[k] = hold
    bull = (lean + s4) / 4.0; bear = (lean - s4) / 4.0
    idx0 = np.maximum(np.arange(n) - LOCK, 0)
    sum0 = (bull + bull[idx0]) / 2.0 + (bear + bear[idx0]) / 2.0
    return a_sh, e_sh, r_sh, sum0


# --- phase trajectory (setups_S3.phase_traj, verbatim) ---------------------------------------------
def _phase_traj(a_sh, e_sh, r_sh, i):
    lo = max(0, i - W_SEL + 1); pre = min(lo, LW); a0 = lo - pre
    lam = config.PHASE_EMA_LAMBDA
    res = {}
    for key, sgn in (("up", 1.0), ("down", -1.0)):
        op = None; traj = []
        for k in range(a0, i + 1):
            vals = (sgn * (a_sh[k] - 0.5) * 200.0, sgn * (r_sh[k] - 0.5) * 200.0, sgn * (e_sh[k] - 0.5) * 200.0)
            ll = []
            for _n, *g in config.PHASE_STATS[key]:
                s = 0.0
                for x, (m, sd) in zip(vals, g):
                    sd = max(8.0, sd); s += -0.5 * ((x - m) / sd) ** 2 - math.log(sd)
                ll.append(s)
            mx = max(ll); exp_ = [math.exp(l - mx) for l in ll]; t = sum(exp_) or 1.0
            p = [e / t for e in exp_]
            conf = [p[0] * 100, (p[1] + p[2]) * 100, p[3] * 100]
            op = conf[:] if op is None else [lam * op[j] + (1 - lam) * conf[j] for j in range(3)]
            traj.append(op[:])
        traj = traj[pre:]
        ti = len(traj) - 1 - LOCK
        res[key] = traj[ti if ti >= 0 else -1]
    return res["up"], res["down"]


# --- P0 confirmed-cross markers (m10_sweep_s5b._last_cross / sel_markers, verbatim) -----------------
def _last_cross(vals, ex, lo_k, hi_k, L, up_c, dn_c, confirm_end):
    last = None
    for k in range(lo_k, hi_k):
        a = float(vals[k - 1]) - L; b = float(vals[k]) - L
        if a == 0.0 or b == 0.0 or (a < 0) == (b < 0):
            continue
        newpos = b > 0
        if all((float(vals[j]) - L > 0) == newpos for j in range(k, min(confirm_end, k + 2))):
            frac = a / (a - b)
            last = (ex[k - 1] + frac * (ex[k] - ex[k - 1]), L, up_c if newpos else dn_c)
    return last


def _sel_markers_w(sum0, b):
    """Locked P0 markers on the [b-99, b] selection (one most-recent per level; dots excluded); newest first."""
    vals = sum0[b - (SELW - 1):b + 1]; ex = list(range(SELW)); end = SELW - LOCK
    out = []
    for L in (50.0, 0.0, -50.0):
        m = _last_cross(vals, ex, 1, end, L, 1, -1, end)
        if m is not None:
            out.append(m)
    out.sort(key=lambda m: -m[0])
    return out


def _leg1(sum0, b, side):
    if b < SELW - 1:
        return False
    mk = _sel_markers_w(sum0, b)
    if len(mk) < 2:
        return False
    if side == "long":
        return mk[0][2] > 0 and mk[1][2] > 0 and mk[0][1] == 50.0
    return mk[0][2] < 0 and mk[1][2] < 0 and mk[0][1] == -50.0


# --- the full per-bar detection -------------------------------------------------------------------
def detect_pivots(buckets):
    """Scan ``buckets`` (list of wire/full_snapshot dicts) and return every S5j-r5 fire as
    {det_i, entry_i, side}. det_i/entry_i are indices into ``buckets`` (entry_i None = CANCELLED)."""
    n = len(buckets)
    if n < FIRST + 1:
        return []
    op = np.array([float(b.get("open", b.get("open_price", 0.0))) for b in buckets])
    cl = np.array([float(b.get("close", b.get("close_price", 0.0))) for b in buckets])
    hi = np.array([float(b.get("high", 0.0)) for b in buckets])
    lo_ = np.array([float(b.get("low", 0.0)) for b in buckets])
    poc = np.array([float(b.get("poc_price", 0.0)) for b in buckets])
    a_sh, e_sh, r_sh, sum0 = _p9_global(buckets)

    # moving POC baseline (5% EMA), verbatim
    base = np.empty(n); base[0] = poc[0]
    for k in range(1, n):
        base[k] = poc[k] * 0.05 + base[k - 1] * 0.95
    rop = np.round(op * 100).astype(np.int64); rcl = np.round(cl * 100).astype(np.int64)
    rbase = np.round(base * 100).astype(np.int64)

    # leg 5 zone: max/min open over [b-99, b-59] (N 60..100), EXISTS form
    zmax = np.full(n, np.inf); zmin = np.full(n, -np.inf)
    for b in range(FIRST, n):
        z = rop[b - 99:b - 58]                      # 41 opens = N 60..100
        if len(z):
            zmax[b] = z.max(); zmin[b] = z.min()

    out = []
    for b in range(FIRST, n):
        upv, dnv = _phase_traj(a_sh, e_sh, r_sh, b)
        dom_u = PHASE_NAMES[int(np.argmax(upv))]; dom_d = PHASE_NAMES[int(np.argmax(dnv))]
        sh = e_sh[max(0, b - LOCK)]; spr2 = (2.0 * sh - 1.0) * 100.0
        for side in ("long", "short"):
            if side == "long":
                l2 = spr2 >= 65.0
                l3 = dom_u == "STARTDUR" and dom_d != "STARTDUR"
                l4 = (upv[1] - dnv[1]) >= 15.0
                l5 = rcl[b] < zmax[b]
            else:
                l2 = -spr2 >= 65.0
                l3 = dom_d == "STARTDUR" and dom_u != "STARTDUR"
                l4 = (dnv[1] - upv[1]) >= 15.0
                l5 = rcl[b] > zmin[b]
            if not (l2 and l3 and l4 and l5):
                continue
            if not _leg1(sum0, b, side):
                continue
            out.append({"det_i": b, "side": side, "entry_i": _entry_after(rcl, rop, rbase, lo_, hi, base, b, side, n)})
    return out


def _entry_after(rcl, rop, rbase, lo_, hi, base, b, side, n):
    """S5j-r5 WAIT-baseline-touch entry. Returns the entry bucket index, or None (no touch found ahead)."""
    long = side == "long"
    mkt_ok = ((rcl[b] <= rbase[b]) and (rcl[b] > rop[b])) if long else \
             ((rcl[b] >= rbase[b]) and (rcl[b] < rop[b]))
    if mkt_ok:
        return b
    for j in range(b + 1, n):
        if long:
            if lo_[j] <= base[j] and rcl[j] > rop[j]:
                return j
        else:
            if hi[j] >= base[j] and rcl[j] < rop[j]:
                return j
    return None
