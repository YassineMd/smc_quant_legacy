"""15mReasy + SKEW filter (15m ONLY).

Setup so far : LONG = bullish bucket AND A <= -0.75 ; SHORT = bearish bucket AND A <= -0.75
               SL 0.1% beyond the entry bucket's extreme; TP 1.5R OR early exit when ADVERSE heavy absorption
               prints (A >= +0.75 on OUR aggressor) — the best exit found in study/r15easy_heavyexit.py.
NEW filter   : LONG requires skew > 0, SHORT requires skew < 0   (skew = footprint_panel.profile_skewness,
               >0 = the volume profile's mass leans HIGH).

Reported: with vs without; per side (n / TP / SL / early / win / loss); the WITHIN-CHAIN partition + Fisher so
the filter is judged on the same trades; gross vs net per trade (the fee is the binding constraint here, so
gross is the number that says whether the SIGNAL improved); and the fee-adjusted break-even.

Both views as usual: CHAIN (tradeable, one position at a time) and CONTROLLED (every signal, n constant, so
only the filter varies).

Run: python study/r15easy_skew.py
"""
from __future__ import annotations
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import study.r15easy_heavyexit as H
import study.mm_skew_strategy as S

FEE = H.FEE
TF = "15m"


def fisher(a, b, c, d):
    n = a + b + c + d
    if n == 0 or (a + b) == 0 or (c + d) == 0:
        return float("nan")
    r1, c1 = a + b, a + c
    hp = lambda x: (math.comb(c1, x) * math.comb(n - c1, r1 - x)) / math.comb(n, r1)
    po = hp(a)
    return min(1.0, sum(hp(x) for x in range(max(0, r1 - (n - c1)), min(r1, c1) + 1) if hp(x) <= po + 1e-12))


def f_skew(A, sg):
    sk = A[sg["i"]].get("sk")
    return sk is not None and ((sk > 0) if sg["side"] > 0 else (sk < 0))


def run(A, Av, As, sigs, chain, pred=None):
    last = -1; out = []
    for sg in sigs:
        if chain and sg["i"] <= last:
            continue
        if pred is not None and not pred(sg):
            continue
        r = H.sim(A, Av, As, sg["i"], sg["side"], H.RR, "adverse", 0.75)
        if r is None:
            continue
        e = A[sg["i"]]["c"]
        sl = A[sg["i"]]["l"] * (1 - H.SL_BUF) if sg["side"] > 0 else A[sg["i"]]["h"] * (1 + H.SL_BUF)
        out.append(dict(side=sg["side"], out=r[0], gross=r[1], net=r[1] - FEE, slf=abs(e - sl) / e))
        last = r[2]
    return out


def table(rows, label):
    print("  %s" % label)
    print("    %-7s %5s %5s %5s %6s | %6s %6s | %7s %8s %10s %10s"
          % ("side", "n", "TP", "SL", "early", "WIN", "LOSS", "win%", "net%", "gross/tr", "net/tr"))
    for sd, nm in ((None, "ALL"), (1, "LONG"), (-1, "SHORT")):
        rs = [r for r in rows if sd is None or r["side"] == sd]
        if not rs:
            continue
        n = len(rs)
        tp = sum(1 for r in rs if r["out"] == "TP"); sl = sum(1 for r in rs if r["out"] == "SL")
        hv = sum(1 for r in rs if r["out"] == "HEAVY")
        w = sum(1 for r in rs if r["net"] > 0)
        g = np.array([r["gross"] for r in rs]); nt = np.array([r["net"] for r in rs])
        net = (np.prod(1 + nt) - 1) * 100
        print("    %-7s %5d %5d %5d %6d | %6d %6d | %6.1f%% %+7.1f%% %+9.4f%% %+9.4f%%"
              % (nm, n, tp, sl, hv, w, n - w, 100.0 * w / n, net, g.mean() * 100, nt.mean() * 100))


def main():
    A, first, _ = H.build(TF)
    sigs, Av, As = H.prep(A, first)
    keep = [s for s in sigs if f_skew(A, s)]
    nl = sum(1 for s in sigs if s["side"] > 0); kl = sum(1 for s in keep if s["side"] > 0)
    print("=" * 112)
    print("15mReasy + SKEW  (15m only)  |  dir + A<=-0.75 + skew agrees  |  SL 0.1%%, TP 1.5R or adverse-heavy")
    print("=" * 112)
    print("  raw signals %d (%dL/%dS)  ->  skew-agreeing %d (%dL/%dS)   [keeps %.0f%%]\n"
          % (len(sigs), nl, len(sigs) - nl, len(keep), kl, len(keep) - kl, 100.0 * len(keep) / len(sigs)))

    for chain, lbl in ((True, "CHAIN (tradeable, one position at a time)"),
                       (False, "CONTROLLED (every signal, n constant — only the filter varies)")):
        print("-" * 112); print(lbl); print("-" * 112)
        base = run(A, Av, As, sigs, chain)
        filt = run(A, Av, As, sigs, chain, lambda sg: f_skew(A, sg))
        table(base, "WITHOUT skew:")
        table(filt, "WITH skew:")
        s = float(np.median([r["slf"] for r in filt])) if filt else 0.0
        be = (FEE + s) / (s * (1 + H.RR)) * 100 if s > 0 else float("nan")
        w = 100.0 * sum(1 for r in filt if r["net"] > 0) / len(filt) if filt else float("nan")
        print("    median stop %.3f%%  ->  fee-adjusted BE %.1f%%   |  WITH-skew win %.1f%%  ->  gap %+.1fpp\n"
              % (s * 100, be, w, w - be))

    # honest test: same chain, split by the skew filter
    print("-" * 112); print("HONEST TEST — one chain, partitioned by the skew filter"); print("-" * 112)
    last = -1; ps = []; fs = []
    for sg in sigs:
        if sg["i"] <= last:
            continue
        r = H.sim(A, Av, As, sg["i"], sg["side"], H.RR, "adverse", 0.75)
        if r is None:
            continue
        row = dict(side=sg["side"], net=r[1] - FEE, gross=r[1])
        (ps if f_skew(A, sg) else fs).append(row); last = r[2]
    for nm, rs in (("pass (skew agrees)", ps), ("fail (skew opposes)", fs)):
        n = len(rs); w = sum(1 for r in rs if r["net"] > 0)
        g = np.array([r["gross"] for r in rs]); nt = np.array([r["net"] for r in rs])
        print("  %-22s n=%4d  win %5.1f%%  net %+7.1f%%  gross/tr %+.4f%%  net/tr %+.4f%%"
              % (nm, n, 100.0 * w / n, (np.prod(1 + nt) - 1) * 100, g.mean() * 100, nt.mean() * 100))
    wp = sum(1 for r in ps if r["net"] > 0); wf = sum(1 for r in fs if r["net"] > 0)
    print("  Fisher pass-vs-fail: p=%.3f" % fisher(wp, len(ps) - wp, wf, len(fs) - wf))
    if len(ps) >= 6:
        m = len(ps) // 2
        for hl, sub in (("H1", ps[:m]), ("H2", ps[m:])):
            nt = np.array([r["net"] for r in sub]); w = sum(1 for r in sub if r["net"] > 0)
            print("  pass %s: n=%3d win %5.1f%% net %+6.1f%%" % (hl, len(sub), 100.0 * w / len(sub),
                                                                 (np.prod(1 + nt) - 1) * 100))


if __name__ == "__main__":
    main()
