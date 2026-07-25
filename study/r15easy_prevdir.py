"""15mReasy + PREVIOUS-CANDLE-SAME-DIRECTION (continuation filter, 15m ONLY).

Setup  : enter on candle i when
           - A(i) <= -0.75  (R-easy, entry candle only — the PREVIOUS candle need NOT be easy)
           - skew(i) agrees with the side (skew>0 long / skew<0 short)
           - candle i-1 is the SAME direction as candle i  (NEW: a continuation / don't-fade-a-turn filter)
Exit   : SL 0.1% beyond candle i's extreme; TP = RR x SL (RR 1:1.0 and 1:1.5). Plain brackets.

Contrast with study/r15easy_2candle.py, which ALSO required the previous candle to be R-easy (only 44 signals).
Here the prior candle just has to point the same way, so far more signals survive.

Baseline = single candle (dir + A<=-0.75 + skew). Views: CHAIN (tradeable) + CONTROLLED (n constant). Gross is
the number that matters (fees bind on 15m); BE* = fee-adjusted break-even from each row's own median stop.

Run: python study/r15easy_prevdir.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.mm_skew_v11_tf import build
from app import absorption as ABS

FEE = 0.0008
R_EASY = -0.75
SL_BUF = 0.001
RRS = (1.0, 1.5)


def prep(A, first):
    Aval = [None] * len(A); dirn = [0] * len(A)
    for i in range(len(A)):
        try:
            Aval[i] = ABS.absorption(A, i)[0]
        except Exception:
            Aval[i] = None
        dirn[i] = 1 if A[i]["up"] else (-1 if A[i]["dn"] else 0)

    def skew_ok(i, s):
        sk = A[i].get("sk")
        return sk is not None and ((sk > 0) if s > 0 else (sk < 0))

    single = []; prevdir = []
    for i in range(max(first, 1), len(A) - 1):
        s = dirn[i]
        if s == 0 or Aval[i] is None or Aval[i] > R_EASY or not skew_ok(i, s):
            continue
        sg = dict(i=i, side=s, t=float(A[i].get("start_time", 0)))
        single.append(sg)
        if dirn[i - 1] == s:                      # NEW filter: previous candle same direction (any absorption)
            prevdir.append(sg)
    return single, prevdir


def sim(A, i, side, rr):
    e = A[i]["c"]
    sl = A[i]["l"] * (1 - SL_BUF) if side > 0 else A[i]["h"] * (1 + SL_BUF)
    sld = (e - sl) if side > 0 else (sl - e)
    if sld <= 0:
        return None
    slf = sld / e
    tp = e + rr * sld * side
    for j in range(i + 1, len(A)):
        hi, lo = A[j]["h"], A[j]["l"]
        if (lo <= sl) if side > 0 else (hi >= sl):
            return ("SL", -slf, j, slf)
        if (hi >= tp) if side > 0 else (lo <= tp):
            return ("TP", rr * slf, j, slf)
    return ("OPEN", (A[-1]["c"] - e) / e * side, len(A) - 1, slf)


def run(A, sigs, rr, chain):
    last = -1; out = []
    for sg in sigs:
        if chain and sg["i"] <= last:
            continue
        r = sim(A, sg["i"], sg["side"], rr)
        if r is None:
            continue
        out.append(dict(side=sg["side"], out=r[0], gross=r[1], net=r[1] - FEE, slf=r[3]))
        last = r[2]
    return out


def table(rows, rr, label):
    print("    %s" % label)
    print("      %-7s %5s %5s %5s | %7s %8s %10s %10s %8s"
          % ("side", "n", "TP", "SL", "win%", "net%", "gross/tr", "net/tr", "BE*"))
    for sd, nm in ((None, "ALL"), (1, "LONG"), (-1, "SHORT")):
        rs = [r for r in rows if sd is None or r["side"] == sd]
        if not rs:
            print("      %-7s n=0" % nm); continue
        n = len(rs); tp = sum(1 for r in rs if r["out"] == "TP"); sl = sum(1 for r in rs if r["out"] == "SL")
        w = sum(1 for r in rs if r["net"] > 0)
        g = np.array([r["gross"] for r in rs]); nt = np.array([r["net"] for r in rs])
        s = float(np.median([r["slf"] for r in rs]))
        be = (FEE + s) / (s * (1 + rr)) * 100
        print("      %-7s %5d %5d %5d | %6.1f%% %+7.1f%% %+9.4f%% %+9.4f%% %7.1f%%"
              % (nm, n, tp, sl, 100.0 * w / n, (np.prod(1 + nt) - 1) * 100, g.mean() * 100, nt.mean() * 100, be))


def main():
    A, first, _ = build("15m")
    single, prevdir = prep(A, first)
    sl_ = sum(1 for s in single if s["side"] > 0); pl = sum(1 for s in prevdir if s["side"] > 0)
    print("=" * 106)
    print("15mReasy + PREV-CANDLE-SAME-DIR  (15m)  |  A<=-0.75 + skew + prev candle same direction  |  plain TP")
    print("=" * 106)
    print("  single-candle (dir + A<=-0.75 + skew) : %4d (%dL/%dS)" % (len(single), sl_, len(single) - sl_))
    print("  + prev candle same direction          : %4d (%dL/%dS)   = %.0f%% of single\n"
          % (len(prevdir), pl, len(prevdir) - pl, 100.0 * len(prevdir) / max(1, len(single))))

    for rr in RRS:
        print("-" * 106)
        print("RR 1:%.1f    (BE* = fee-adjusted break-even from that row's own median stop)" % rr)
        print("-" * 106)
        for chain, vl in ((True, "CHAIN (tradeable)"), (False, "CONTROLLED (n constant)")):
            print("  %s" % vl)
            table(run(A, single, rr, chain), rr, "baseline  single candle:")
            table(run(A, prevdir, rr, chain), rr, ">>> + prev candle same direction:")
        print()


if __name__ == "__main__":
    main()
