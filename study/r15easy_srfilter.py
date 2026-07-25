"""15mReasy (frozen) + DON'T-FADE-INTO-A-LEVEL filter (15m ONLY).

Base   : direction + A<=-0.75 (R-easy) + |skew|>=0.4 + prev candle same direction. SL 0.1%, TP RR x SL.
ADD    : skip a LONG whose entry is VERY CLOSE (BELOW) an active RESISTANCE, and a SHORT whose entry is very
         close (ABOVE) an active SUPPORT — i.e. don't enter straight into the level the move must break.

Levels : app.support_resistance.detect (fractal pivot highs=R / lows=S, k=8). CAUSAL use — a level at pivot p is
         only known once confirmed (p + k <= i), and only counts while still active (not broken: i1 is None or
         i1 > i). "Adverse" = R above the entry for a long / S below the entry for a short (the level in the path
         to TP). "close" = the nearest adverse level within TOL% of the entry price. Sweep TOL to see if it matters.

Views  : controlled (n constant) + one-at-a-time. Gross is the binding number on 15m; BE* = fee-adjusted BE.

Run: python study/r15easy_srfilter.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from app import absorption as ABS
from app import support_resistance as SR
from study.mm_skew_v11_tf import build

FEE = 0.0008
R_EASY = -0.75
SKEW_MIN = 0.4
SL_BUF = 0.001
K = SR.SR_PIVOT_K
RRS = (1.0, 1.5)
TOLS = (0.0010, 0.0020, 0.0030, 0.0050)     # "very close" = nearest adverse level within this % of entry


def prep(A, first):
    Aval = [None] * len(A); dirn = [0] * len(A)
    for i in range(len(A)):
        try:
            Aval[i] = ABS.absorption(A, i)[0]
        except Exception:
            Aval[i] = None
        dirn[i] = 1 if A[i]["up"] else (-1 if A[i]["dn"] else 0)
    out = []
    for i in range(max(first, 1), len(A) - 1):
        s = dirn[i]
        if s == 0 or Aval[i] is None or Aval[i] > R_EASY or dirn[i - 1] != s:
            continue
        sk = A[i].get("sk")
        if sk is None or (sk < SKEW_MIN if s > 0 else sk > -SKEW_MIN):
            continue
        out.append(dict(i=i, side=s, t=float(A[i].get("start_time", 0))))
    return out


def adverse_dist(levels, i, side, e):
    """Distance (fraction of entry) to the NEAREST active ADVERSE level, or None if none in the path.
    LONG -> nearest RESISTANCE ABOVE e; SHORT -> nearest SUPPORT BELOW e. Causal: level confirmed (i0+K<=i)
    and not yet broken (i1 is None or i1 > i)."""
    best = None
    for lv in levels:
        if lv["i0"] + K > i:                 # pivot not confirmable by bar i yet -> unknown (causal)
            continue
        if lv["i1"] is not None and lv["i1"] <= i:
            continue                         # already broken by bar i
        p = lv["price"]
        if side > 0 and lv["kind"] == "R" and p > e:
            d = (p - e) / e
        elif side < 0 and lv["kind"] == "S" and p < e:
            d = (e - p) / e
        else:
            continue
        if best is None or d < best:
            best = d
    return best


def sim(A, i, side, rr):
    e = A[i]["c"]
    sl = A[i]["l"] * (1 - SL_BUF) if side > 0 else A[i]["h"] * (1 + SL_BUF)
    sld = (e - sl) if side > 0 else (sl - e)
    if sld <= 0:
        return None
    slf = sld / e; tp = e + rr * sld * side
    for j in range(i + 1, len(A)):
        hi, lo = A[j]["h"], A[j]["l"]
        if (lo <= sl) if side > 0 else (hi >= sl):
            return ("SL", -slf, j, slf)
        if (hi >= tp) if side > 0 else (lo <= tp):
            return ("TP", rr * slf, j, slf)
    return ("OPEN", (A[-1]["c"] - e) / e * side, len(A) - 1, slf)


def run(A, sigs, rr, chain, keep=None):
    last = -1; out = []
    for sg in sigs:
        if chain and sg["i"] <= last:
            continue
        if keep is not None and not keep(sg):
            continue
        r = sim(A, sg["i"], sg["side"], rr)
        if r is None:
            continue
        out.append(dict(side=sg["side"], win=(r[0] == "TP"), net=r[1] - FEE, gross=r[1], slf=r[3])); last = r[2]
    return out


def line(rows, rr, lbl):
    n = len(rows)
    if n == 0:
        print("    %-22s n=0" % lbl); return
    w = sum(1 for r in rows if r["net"] > 0)
    g = np.mean([r["gross"] for r in rows]); nt = np.mean([r["net"] for r in rows])
    tot = (np.prod(1 + np.array([r["net"] for r in rows])) - 1) * 100
    s = float(np.median([r["slf"] for r in rows])); be = (FEE + s) / (s * (1 + rr)) * 100
    print("    %-22s n=%3d win %5.1f%% gross %+.4f%% net %+.4f%% tot %+.1f%% BE* %.1f%% gap %+.1f"
          % (lbl, n, 100.0 * w / n, g * 100, nt * 100, tot, be, 100.0 * w / n - be))


def main():
    A, first, _ = build("15m")
    levels = SR.detect(A, k=K)
    sigs = prep(A, first)
    for sg in sigs:
        sg["adv"] = adverse_dist(levels, sg["i"], sg["side"], A[sg["i"]]["c"])
    near = sum(1 for s in sigs if s["adv"] is not None)
    print("=" * 100)
    print("15mReasy + don't-fade-into-a-level  (15m)  |  frozen base + S/R proximity  |  %d S/R levels" % len(levels))
    print("=" * 100)
    print("  signals: %d  (%d have an adverse level somewhere in the path, %d have none)\n"
          % (len(sigs), near, len(sigs) - near))

    for rr in RRS:
        print("-" * 100); print("RR 1:%.1f  (controlled)" % rr); print("-" * 100)
        line(run(A, sigs, rr, False), rr, "baseline (no S/R)")
        for tol in TOLS:
            keep = (lambda sg, t=tol: (sg["adv"] is None) or (sg["adv"] >= t))   # skip if adverse level within tol
            rows = run(A, sigs, rr, False, keep)
            dropped = len(sigs) - len(run(A, sigs, rr, False, keep))
            line(rows, rr, ">>> skip <%.2f%%" % (tol * 100))
        # per side at a mid tolerance (0.3%)
        keep3 = lambda sg: (sg["adv"] is None) or (sg["adv"] >= 0.003)
        print("  per side @ skip<0.30%:")
        for sd, nm in ((1, "LONG"), (-1, "SHORT")):
            line([r for r in run(A, sigs, rr, False, keep3) if r["side"] == sd], rr, "  " + nm)
        print()


if __name__ == "__main__":
    main()
