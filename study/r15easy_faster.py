"""15mReasy + FASTER-CANDLE filter (15m ONLY).

Base   : A(i)<=-0.75 (R-easy) + skew(i) agrees + candle i-1 same direction.
ADD    : duration(i) < duration(i-1)  — the entry candle's ELAPSED TIME is SHORTER than the previous candle's.
         Constant-VOLUME buckets fill in variable time, so a shorter duration = the SAME volume traded FASTER =
         pace/velocity ACCELERATING into the entry. duration = end_time - start_time.
EXIT   : SL 0.1% beyond entry extreme; TP = RR x SL (RR 1:1.0 and 1:1.5). Plain brackets.

Baseline = the prev-dir config WITHOUT the speed cut, so the marginal effect is visible. Also the WITHIN-CHAIN
partition (faster vs slower) + Fisher + split-half, and disjoint duration-RATIO bands. Views: CHAIN (tradeable)
+ CONTROLLED (n constant). Gross is the binding number on 15m; BE* = fee-adjusted break-even.

Run: python study/r15easy_faster.py
"""
from __future__ import annotations
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from app import absorption as ABS
from study.mm_skew_v11_tf import build

FEE = 0.0008
R_EASY = -0.75
SL_BUF = 0.001
RRS = (1.0, 1.5)


def dur(b):
    return float(b.get("end_time", 0.0) or 0.0) - float(b.get("start_time", 0.0) or 0.0)


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

    base = []
    for i in range(max(first, 1), len(A) - 1):
        s = dirn[i]
        if s == 0 or Aval[i] is None or Aval[i] > R_EASY or not skew_ok(i, s) or dirn[i - 1] != s:
            continue
        d0 = dur(A[i]); d1 = dur(A[i - 1])
        ratio = (d0 / d1) if d1 > 0 else None                 # <1 => entry filled FASTER than prev
        base.append(dict(i=i, side=s, t=float(A[i].get("start_time", 0)), faster=(d1 > 0 and d0 < d1), ratio=ratio))
    return base


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


def run(A, sigs, rr, chain, pred=None):
    last = -1; out = []
    for sg in sigs:
        if chain and sg["i"] <= last:
            continue
        if pred is not None and not pred(sg):
            continue
        r = sim(A, sg["i"], sg["side"], rr)
        if r is None:
            continue
        out.append(dict(side=sg["side"], out=r[0], gross=r[1], net=r[1] - FEE, slf=r[3])); last = r[2]
    return out


def fisher(a, b, c, d):
    n = a + b + c + d
    if n == 0 or (a + b) == 0 or (c + d) == 0:
        return float("nan")
    r1, c1 = a + b, a + c
    hp = lambda x: (math.comb(c1, x) * math.comb(n - c1, r1 - x)) / math.comb(n, r1)
    po = hp(a)
    return min(1.0, sum(hp(x) for x in range(max(0, r1 - (n - c1)), min(r1, c1) + 1) if hp(x) <= po + 1e-12))


def one(rows, rr, lbl):
    n = len(rows)
    if n == 0:
        print("    %-28s n=0" % lbl); return
    w = sum(1 for r in rows if r["net"] > 0)
    g = np.array([r["gross"] for r in rows]); nt = np.array([r["net"] for r in rows])
    s = float(np.median([r["slf"] for r in rows]))
    be = (FEE + s) / (s * (1 + rr)) * 100
    print("    %-28s n=%3d  win %5.1f%%  gross/tr %+.4f%%  net/tr %+.4f%%  BE* %.1f%%  gap %+.1f"
          % (lbl, n, 100.0 * w / n, g.mean() * 100, nt.mean() * 100, be, 100.0 * w / n - be))


def side_tab(A, sigs, rr, chain, pred, tag):
    for sd, nm in ((None, "ALL"), (1, "LONG"), (-1, "SHORT")):
        rows = [r for r in run(A, sigs, rr, chain, pred) if sd is None or r["side"] == sd]
        one(rows, rr, "%s %s" % (tag, nm))


def main():
    A, first, _ = build("15m")
    base = prep(A, first)
    keep = [s for s in base if s["faster"]]
    bl = sum(1 for s in base if s["side"] > 0); kl = sum(1 for s in keep if s["side"] > 0)
    print("=" * 104)
    print("15mReasy + FASTER candle (dur(i)<dur(i-1))  |  A<=-0.75 + skew + prev-dir  |  15m")
    print("=" * 104)
    print("  prev-dir base : %d (%dL/%dS)" % (len(base), bl, len(base) - bl))
    print("  + faster      : %d (%dL/%dS)   = %.0f%%\n" % (len(keep), kl, len(keep) - kl, 100.0 * len(keep) / max(1, len(base))))

    for rr in RRS:
        print("-" * 104); print("RR 1:%.1f" % rr); print("-" * 104)
        print("  CONTROLLED (n constant):")
        side_tab(A, base, rr, False, None, "baseline")
        side_tab(A, base, rr, False, lambda s: s["faster"], ">>>faster")
        print("  CHAIN (tradeable):")
        side_tab(A, keep, rr, True, None, ">>>faster")
        print("  HONEST TEST (one chain, partitioned faster vs slower):")
        last = -1; ps = []; fs = []
        for sg in base:
            if sg["i"] <= last:
                continue
            r = sim(A, sg["i"], sg["side"], rr)
            if r is None:
                continue
            (ps if sg["faster"] else fs).append(dict(win=(r[0] == "TP"), net=r[1] - FEE)); last = r[2]
        wp = sum(1 for r in ps if r["win"]); wf = sum(1 for r in fs if r["win"])
        for lbl, rr_ in (("faster (dur<prev)", ps), ("slower (dur>=prev)", fs)):
            n = len(rr_); w = sum(1 for r in rr_ if r["net"] > 0)
            g = np.mean([r["net"] + FEE for r in rr_]) if rr_ else 0
            print("    %-20s n=%3d win %5.1f%% gross/tr %+.4f%%" % (lbl, n, 100.0 * w / n if n else 0, g * 100))
        print("    Fisher faster-vs-slower: p=%.3f" % fisher(wp, len(ps) - wp, wf, len(fs) - wf))
        if len(ps) >= 6:
            m = len(ps) // 2
            for hl, sub in (("H1", ps[:m]), ("H2", ps[m:])):
                w = sum(1 for r in sub if r["win"]); print("    faster %s: n=%d win %.1f%%" % (hl, len(sub), 100.0 * w / len(sub)))
        print("  DISJOINT duration-RATIO bands (controlled, dur(i)/dur(i-1)):")
        for lo, hi in ((0, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 1e9)):
            sub = [s for s in base if s["ratio"] is not None and lo <= s["ratio"] < hi]
            one(run(A, sub, rr, False), rr, "ratio [%.1f,%.1f)" % (lo, hi))
        print()


if __name__ == "__main__":
    main()
