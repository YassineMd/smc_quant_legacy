"""Pin down the REPAINT cause: is it the WINDOW's LEFT edge (how much history precedes the signal)? Fix the right edge at
the full window (E=N) and vary the LEFT edge L (= scan-start). For each recent signal at absolute bar k, record the set
of warmups (k-L) at which the detector still SHOWS it. If a signal needs >= W bars of history before it and the terminal
reloads with a shorter window, it DROPS -> that is the repaint. Reports, per recent signal, the MIN warmup it needs; any
signal needing more warmup than a reload provides is the bug. python study/radarrun_repaint_leftedge.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.radarrun_tp_velocity import get_buckets
from app import radar_breakout_detect as RB

SLBUF = 0.003; TPF = 0.005


def abs_sig(B, L, E):
    """Absolute signal-bar indices for the sliced window B[L:E] (sliced i -> absolute i+L)."""
    return {int(s["i"]) + L for s in RB.detect(B[L:E], skip_last=True, sl_buf=SLBUF, tp_frac=TPF)}


def main():
    B = get_buckets("30m", {}); N = len(B)
    full = sorted(int(s["i"]) for s in RB.detect(B, skip_last=True, sl_buf=SLBUF, tp_frac=TPF))
    targets = [k for k in full if k >= N - 260][-18:]        # the most recent ~18 signals (near the live edge)
    if not targets:
        print("no recent signals"); return
    kmin = min(targets)
    Ls = list(range(0, kmin - 4, 4))                          # sweep the left edge from full history up to near the signal
    print("DAEMON 30m N=%d. Testing %d recent signals; sweeping left edge L=0..%d.\n" % (N, len(targets), kmin - 5), flush=True)
    presence = {k: [] for k in targets}                       # k -> list of warmups (k-L) where SHOWN
    for L in Ls:
        s = abs_sig(B, L, N)
        for k in targets:
            if k in s:
                presence[k].append(k - L)
    print("  %-8s %10s %10s %12s" % ("signal k", "shows?", "minWarmup", "maxWarmup(=k-0)"), flush=True)
    need = []
    for k in targets:
        w = presence[k]
        if not w:
            print("  %-8d %10s" % (k, "NEVER"), flush=True); continue
        # a signal is UNSTABLE if it disappears at low warmup (needs a minimum history before it)
        minw = min(w); maxw = max(w)
        # find the warmup THRESHOLD below which it vanishes: the largest warmup that is ABSENT
        allw = [k - L for L in Ls]
        absent = [ww for ww in allw if ww not in w and ww >= 0]
        thr = (max(absent) + 1) if absent else 0              # needs at least `thr` bars of history before it
        need.append(thr)
        flag = "  <-- needs %d bars warmup" % thr if thr > 0 else ""
        print("  %-8d %10s %10d %12d%s" % (k, "yes", minw, maxw, flag), flush=True)
    if need:
        print("\n  warmup needed before a signal is stable: median=%d  p90=%d  max=%d bars" % (
            int(np.median(need)), int(np.percentile(need, 90)), int(max(need))), flush=True)
        print("  => any reload whose window starts LESS than 'max' bars before a fresh signal can DROP it.", flush=True)


if __name__ == "__main__":
    main()
