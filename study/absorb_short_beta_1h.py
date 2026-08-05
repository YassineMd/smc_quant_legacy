"""BETA CHECK for the short side of: absorption badge + vw>=3 + absR<=-0.5 + SWING-aligned + swing-A/A4<=0,
enter candle side, TP 0.5% / SL 0.1% beyond the candle extreme [reading B]. The short side netted +9.6% in-sample.
Is that ALPHA or just short-BETA (SOL fell over the window, so any short wins)?

Baselines, all with the IDENTICAL short bracket (entry=close, SL=high*(1+0.1%), TP=close*(1-0.5%)):
  [1] market drift over the window (buy&hold %) -> the beta tailwind.
  [2] RANDOM-timed shorts: same COUNT as the strategy, random 1h bars, S resamples -> distribution + p(random>=strat).
  [3] NAIVE short of EVERY absorption bear-candle (no vw/absR/swing/swingA filter) -> the candle-type baseline.
Run: python study/absorb_short_beta_1h.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import study.signal_search_lib as L
import study.mom_absorb_1h as MA
from app import engulf1m_detect as E, structure, swing_lvn_detect as SW

rng = np.random.default_rng(20260805)
F = L.load_features("1h")
A = F["A"]; n = F["n"]; absA = F["absA"]; O = F["o"]; C = F["c"]; Hh = F["h"]; Ll = F["l"]
FEE = MA.FEE; TP = 0.005; SL_PAD = 0.001


def short_net(i):
    """Net of ONE short at bar i with the strategy bracket. Returns (net, exit_bar) or None if degenerate."""
    e = C[i]; sl = Hh[i] * (1 + SL_PAD); tp = e * (1 - TP)
    if sl <= e:
        return None
    win, ej = MA.walk(A, i, -1, sl, tp, n)
    dist = (sl - e) / e
    return (TP if win else -dist) - FEE, ej


# ---- swing direction (causal) ----
Harr = [float(b.get("high", 0.0) or 0.0) for b in A]; Larr = [float(b.get("low", 0.0) or 0.0) for b in A]
Carr = [float(b.get("close", b.get("close_price", 0.0)) or 0.0) for b in A]
_thr = SW._adaptive_thr(Harr, Larr, Carr, window=len(Carr))
_piv = sorted(structure._zigzag_confirmed(Harr, Larr, _thr), key=lambda p: p[3])
swing_dir = [0] * n; _pi = 0; _cur = 0
for _i in range(n):
    while _pi < len(_piv) and _piv[_pi][3] <= _i:
        _cur = -1 if _piv[_pi][2] else 1; _pi += 1
    swing_dir[_i] = _cur

marks = E.detect(A, skip_last=True, absorp=list(absA))
bear_marks = [m for m in marks if m["side"] < 0]                 # every absorption BEAR candle (badge)


def vw_ok(i):
    ut = float(A[i].get("up_ticks", 0.0) or 0.0); dt = float(A[i].get("dn_ticks", 0.0) or 0.0)
    mn = min(ut, dt); return mn > 0 and (max(ut, dt) / mn - 1.0) * 100.0 >= 3.0


def taken_net(idxs):
    """Non-overlap compounded net for a sorted list of short entry bars."""
    nets = []; last = -1
    for i in sorted(idxs):
        if i <= last:
            continue
        r = short_net(i)
        if r is None:
            continue
        net, ej = r; last = ej; nets.append(net)
    return np.array(nets)


# ---- STRATEGY shorts (all filters incl. swing-A/A4) ----
strat = []
for m in bear_marks:
    i = m["i"]
    if not vw_ok(i) or not (absA[i] <= -0.5) or swing_dir[i] != -1:
        continue
    _legs = SW.swing_lines(A[:i + 1])
    _dev = next((lg for lg in reversed(_legs) if lg.get("developing")), None)
    if _dev is not None:
        _a = _dev.get("A"); _a4 = _dev.get("A4")
        if (_a is not None and _a > 0) or (_a4 is not None and _a4 > 0):
            continue
    strat.append(i)
strat_nets = taken_net(strat)
strat_tot = (np.prod(1 + strat_nets) - 1) * 100


def tot(nets):
    return (np.prod(1 + nets) - 1) * 100 if len(nets) else 0.0


# ---- baselines ----
drift = (C[-1] / C[0] - 1.0) * 100.0
naive_nets = taken_net([m["i"] for m in bear_marks])
lo_i, hi_i = 30, n - 5
Ntr = len(strat_nets)
rand_tots = []
for _ in range(3000):
    idx = rng.integers(lo_i, hi_i, size=Ntr)
    nets = []
    for i in idx:                                               # independent random shorts (no non-overlap) — pure timing null
        r = short_net(int(i))
        if r is not None:
            nets.append(r[0])
    rand_tots.append(tot(np.array(nets)))
rand_tots = np.array(rand_tots)
p_ge = float((rand_tots >= strat_tot).mean())

print("=" * 100)
print("SHORT-SIDE BETA CHECK  (bracket: entry=close, SL=high+0.1%%, TP=0.5%%, fee %.2f%%/rt)" % (FEE * 100))
print("=" * 100)
print("[1] Market drift over the recon window (buy&hold):  %+.1f%%   -> a short tailwind of ~%+.1f%%"
      % (drift, -drift))
print("[3] NAIVE short of EVERY absorption bear-candle (no filters):  n=%d  net %+.1f%%"
      % (len(naive_nets), tot(naive_nets)))
print("    STRATEGY filtered short (vw+absR+swing+swingA):            n=%d  net %+.1f%%" % (Ntr, strat_tot))
print("[2] RANDOM-timed shorts, same count (n=%d), same bracket, 3000 resamples:" % Ntr)
print("    random net: mean %+.1f%%  median %+.1f%%  5th %+.1f%%  95th %+.1f%%"
      % (rand_tots.mean(), np.median(rand_tots), np.percentile(rand_tots, 5), np.percentile(rand_tots, 95)))
print("    P(random short >= strategy short %+.1f%%) = %.3f  ->  %s"
      % (strat_tot, p_ge, "ALPHA (beats random timing)" if p_ge < 0.05 else "NOT distinguishable from random-timed short = BETA"))
print("=" * 100)
