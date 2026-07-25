"""15mReasy (frozen) + BREAKEVEN-STOP rule (15m ONLY).

Base : direction + A<=-0.75 + |skew|>=0.4 + prev-same-dir. SL 0.1% beyond entry extreme; TP = RR x SL.
RULE : once price has reached +0.5R (halfway to a 1.5R target / at the 1.0R target if RR=1.0... it is 0.5x the
       STOP distance in profit), move the stop from its original level to the ENTRY (breakeven). A later reversal
       then exits at breakeven (net = -fee) instead of a full -1R stop.

INTRA-BAR MODELLING (conservative, so the rule's benefit is NOT overstated): each bar is processed adverse-move-
FIRST — the (current) stop is checked before the arm level, and arming only takes effect for SUBSEQUENT bars.
So a single-bar round-trip (+0.5R then -1R in the same bar) counts as a FULL stop, and the breakeven protection
only helps when the favourable move and the reversal fall in different bars (realistic).

Outcomes: TP (+RR), SL (-1R), BE (0 gross -> -fee net, after arming), OPEN. Compared with vs without the rule,
per side, both RRs. 'win' = net>0 (so a BE exit is a small loss, not a win). Fee 0.08%.

Run: python study/r15easy_bestop.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from app import absorption as ABS
import study.mm_skew_strategy as S
from study.mm_skew_v11_tf import build

FEE = 0.0008
R_EASY = -0.75
SKEW_MIN = 0.4
SL_BUF = 0.001
ARM_R = 0.5                # move to breakeven once +ARM_R (in stop-distance units) profit is reached
RRS = (1.0, 1.5)


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


def sim(A, i, side, rr, breakeven):
    e = A[i]["c"]
    sl0 = A[i]["l"] * (1 - SL_BUF) if side > 0 else A[i]["h"] * (1 + SL_BUF)
    sld = (e - sl0) if side > 0 else (sl0 - e)
    if sld <= 0:
        return None
    slf = sld / e
    tp = e + rr * sld * side
    arm = e + ARM_R * sld * side
    armed = False
    for j in range(i + 1, len(A)):
        hi, lo = A[j]["h"], A[j]["l"]
        cur_sl = e if (breakeven and armed) else sl0
        if (lo <= cur_sl) if side > 0 else (hi >= cur_sl):        # adverse first
            if breakeven and armed:
                return ("BE", 0.0, j)                            # exit at entry -> gross 0
            return ("SL", -slf, j)
        if (hi >= tp) if side > 0 else (lo <= tp):
            return ("TP", rr * slf, j)
        if breakeven and not armed:                              # arm for SUBSEQUENT bars
            if (hi >= arm) if side > 0 else (lo <= arm):
                armed = True
    return ("OPEN", (A[-1]["c"] - e) / e * side, len(A) - 1)


def run(A, sigs, rr, breakeven, chain=True):
    last = -1; out = []
    for sg in sigs:
        if chain and sg["i"] <= last:
            continue
        r = sim(A, sg["i"], sg["side"], rr, breakeven)
        if r is None:
            continue
        out.append(dict(side=sg["side"], out=r[0], gross=r[1], net=r[1] - FEE)); last = r[2]
    return out


def stat(rows, rr, label):
    n = len(rows)
    if n == 0:
        print("  %-12s n=0" % label); return
    tp = sum(1 for r in rows if r["out"] == "TP"); sl = sum(1 for r in rows if r["out"] == "SL")
    be = sum(1 for r in rows if r["out"] == "BE")
    w = sum(1 for r in rows if r["net"] > 0)
    nt = np.array([r["net"] for r in rows])
    tot = (np.prod(1 + nt) - 1) * 100
    gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum(); pf = (gg / ll) if ll > 0 else float("inf")
    path = np.cumprod(1 + nt); peak = np.maximum.accumulate(path); dd = float(np.max((peak - path) / peak)) * 100
    bal = S.BAL0
    for r in rows:
        bal += S.POS_FRAC * bal * S.LEV * r["net"]
    print("  %-12s n=%3d  TP %3d SL %3d BE %3d  win %5.1f%%  net %+6.1f%%  net/tr %+.4f%%  PF %4.2f  DD %4.1f%%  $%s"
          % (label, n, tp, sl, be, 100.0 * w / n, tot, nt.mean() * 100, pf, dd, f"{bal:,.0f}"))


def main():
    A, first, _ = build("15m")
    sigs = prep(A, first)
    nl = sum(1 for s in sigs if s["side"] > 0)
    print("=" * 118)
    print("15mReasy + BREAKEVEN stop @ +0.5R  (15m)  |  frozen base  |  one-at-a-time  |  signals %d (%dL/%dS)"
          % (len(sigs), nl, len(sigs) - nl))
    print("=" * 118)
    for rr in RRS:
        print("RR 1:%.1f" % rr)
        for be, tag in ((False, "flat SL "), (True, "+BE@0.5R")):
            rows = run(A, sigs, rr, be)
            stat(rows, rr, tag + " ALL")
            stat([r for r in rows if r["side"] > 0], rr, tag + " LONG")
            stat([r for r in rows if r["side"] < 0], rr, tag + " SHORT")
        print()
    print("CAVEAT: intra-bar modelled adverse-first (BE benefit not overstated). 15m, one 34-day regime, forward n=0.")


if __name__ == "__main__":
    main()
