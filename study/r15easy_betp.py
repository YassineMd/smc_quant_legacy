"""15mReasy (frozen) — fixed 0.5% SL + "de-risk to breakeven on early weakness" (15m ONLY).

Base : direction + A<=-0.75 + |skew|>=0.4 + prev-same-dir. SL = FIXED 0.5% from entry. TP = RR x SL (=0.75% @1.5).
RULE : if within any of the NEXT 4 BARS price goes AGAINST the position by >= 50% of the stop (i.e. reaches
       0.25% adverse), move the TAKE-PROFIT down to BREAKEVEN (entry). The trade then exits at entry (net -fee)
       on any later return to entry, instead of holding for the full target. SL stays at -0.5%.

INTRA-BAR (conservative, consistent with the BE-stop test): each bar is adverse-first — SL checked, then the
current TP (breakeven if already armed by a PRIOR bar, else original), then the arm check (adverse >=0.25% within
bars 1..4) which takes effect for SUBSEQUENT bars. So the BE-target kicks in the bar AFTER the early dip.

Outcomes: TP (+RR), SL (-1R = -0.5%), BE (0 gross -> -fee net), OPEN. Compared with vs without the rule, per side,
both RRs. 'win' = net>0 (BE is a small loss). Fee 0.08%.

Run: python study/r15easy_betp.py
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
SL_PCT = 0.005          # FIXED 0.5% stop from entry
ADVERSE_FRAC = 0.5      # arm the BE-target when price reaches this fraction of the stop, adverse
ARM_BARS = 4            # ... within this many bars of entry
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
        out.append(dict(i=i, side=s, t=float(A[i].get("start_time", 0))));
    return out


def sim(A, i, side, rr, betp):
    e = A[i]["c"]
    sl = e * (1 - SL_PCT) if side > 0 else e * (1 + SL_PCT)
    slf = SL_PCT
    tp0 = e + rr * (e * SL_PCT) * side
    adverse = e * (1 - ADVERSE_FRAC * SL_PCT) if side > 0 else e * (1 + ADVERSE_FRAC * SL_PCT)
    armed = False
    for j in range(i + 1, len(A)):
        hi, lo = A[j]["h"], A[j]["l"]
        if (lo <= sl) if side > 0 else (hi >= sl):               # SL first (adverse)
            return ("SL", -slf, j)
        cur_tp = e if (betp and armed) else tp0
        if (hi >= cur_tp) if side > 0 else (lo <= cur_tp):
            if betp and armed:
                return ("BE", 0.0, j)
            return ("TP", rr * slf, j)
        if betp and not armed and (j - i) <= ARM_BARS:           # arm for SUBSEQUENT bars
            if (lo <= adverse) if side > 0 else (hi >= adverse):
                armed = True
    return ("OPEN", (A[-1]["c"] - e) / e * side, len(A) - 1)


def run(A, sigs, rr, betp, chain=True):
    last = -1; out = []
    for sg in sigs:
        if chain and sg["i"] <= last:
            continue
        r = sim(A, sg["i"], sg["side"], rr, betp)
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
    print("15mReasy fixed-0.5%%SL + BE-target on early weakness (<=%d bars, >=%.0f%% adverse)  |  signals %d (%dL/%dS)"
          % (ARM_BARS, ADVERSE_FRAC * 100, len(sigs), nl, len(sigs) - nl))
    print("=" * 118)
    for rr in RRS:
        print("RR 1:%.1f" % rr)
        for be, tag in ((False, "0.5%SL only"), (True, "+BE-target ")):
            rows = run(A, sigs, rr, be)
            stat(rows, rr, tag + " ALL")
            stat([r for r in rows if r["side"] > 0], rr, tag + " LONG")
            stat([r for r in rows if r["side"] < 0], rr, tag + " SHORT")
        print()
    print("CAVEAT: intra-bar adverse-first; BE-target arms the bar AFTER the early dip. 15m, one 34-day regime, forward n=0.")


if __name__ == "__main__":
    main()
