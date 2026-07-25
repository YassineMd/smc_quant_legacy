"""15mReasy — a standalone 15m setup (NOT MMXSKEW).

    LONG  = bullish bucket  AND  A <= R_EASY  AND  eff-agg spread >= +EFF_MIN
    SHORT = bearish bucket  AND  A <= R_EASY  AND  eff-agg spread <= -EFF_MIN
    SL = 0.1% beyond the bucket extreme, TP = RR x SL, SL-first on a same-bar tie.
    defaults: R_EASY = -0.75, EFF_MIN = 25.

`A` = app.absorption.absorption()[0], oriented POSITIVE = the aggressor was ABSORBED, so A <= -0.75 is the
module's "light/EASY" side — the aggressor moved price with little resistance. Needs only a trailing window
(NO price_h1 / no 1m reconstruction), so coverage is ~100%.

*** THE BAR IS THE FEE-ADJUSTED BREAK-EVEN, NOT 50%/40%. *** 15m stops are small (~0.35%), so an 0.08%
round-trip is ~23% of the trade: the REAL break-even is ~61.4% @1:1.0 and ~49.1% @1:1.5. Both are printed and
every verdict is taken against them; the nominal figure is shown only for reference.

Reported: funnel; the 2x2 (neither / R-easy only / eff-agg only / BOTH) so it is clear which leg does the work;
an eff-agg threshold sweep (is 25 on anything?); within-chain partitions + Fisher; split-half; per side; account.

Run: python study/r15easy.py [tf] [rr ...]        (default 15m, RR 1.0 and 1.5)
"""
from __future__ import annotations
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from app import absorption as ABS
import study.mm_skew_rr_sweep as RR
import study.mm_skew_strategy as S
from study.mm_skew_v11_tf import build, binom_ge, block

FEE = 0.0008
R_EASY = -0.75
EFF_MIN = 25.0


def scan(A, first):
    """Every directional bucket + its absorption reading + its DIRECTIONAL eff-agg spread."""
    out = []
    for i in range(max(first, 1), len(A) - 1):
        b = A[i]
        if b["up"]:
            s = 1
        elif b["dn"]:
            s = -1
        else:
            continue
        try:
            a = ABS.absorption(A, i)[0]
        except Exception:
            a = None
        out.append(dict(i=i, side=s, t=float(b.get("start_time", 0)), A=a, dspr=b["spread"] * s))
    return out


f_easy = lambda sg: sg["A"] is not None and sg["A"] <= R_EASY
f_eff = lambda sg: sg["dspr"] >= EFF_MIN
f_both = lambda sg: f_easy(sg) and f_eff(sg)

CELLS = (("control  all candles", None),
         ("A  R-easy only", f_easy),
         ("B  eff-agg only", f_eff),
         ("C  BOTH = 15mReasy", f_both))


def taken(A, sigs, rr, pred=None):
    last = -1; out = []
    for sg in sigs:
        if sg["i"] <= last:
            continue
        if pred is not None and not pred(sg):
            continue
        res = RR.simulate_rr(A, sg["i"], sg["side"], rr, "sl")
        if res is None:
            continue
        out.append(dict(side=sg["side"], win=(res[0] == "TP"), net=res[1] - FEE, t=sg["t"]))
        last = res[2]
    return sorted(out, key=lambda z: z["t"])


def fisher(a, b, c, d):
    n = a + b + c + d
    if n == 0 or (a + b) == 0 or (c + d) == 0:
        return float("nan")
    r1, c1 = a + b, a + c
    hp = lambda x: (math.comb(c1, x) * math.comb(n - c1, r1 - x)) / math.comb(n, r1)
    po = hp(a)
    return min(1.0, sum(hp(x) for x in range(max(0, r1 - (n - c1)), min(r1, c1) + 1) if hp(x) <= po + 1e-12))


def partition(A, sigs, rr, be, pred, lbl, base=None):
    last = -1; ps = []; fs = []
    pool = sigs if base is None else [s for s in sigs if base(s)]
    for sg in pool:
        if sg["i"] <= last:
            continue
        res = RR.simulate_rr(A, sg["i"], sg["side"], rr, "sl")
        if res is None:
            continue
        row = dict(side=sg["side"], win=(res[0] == "TP"), net=res[1] - FEE, t=sg["t"])
        (ps if pred(sg) else fs).append(row); last = res[2]
    wp = sum(r["win"] for r in ps); wf = sum(r["win"] for r in fs)
    print("    [%s] (n=%d = %d pass + %d fail):" % (lbl, len(ps) + len(fs), len(ps), len(fs)))
    block(ps, be, "      pass"); block(fs, be, "      fail")
    print("      Fisher pass-vs-fail: p=%.3f" % fisher(wp, len(ps) - wp, wf, len(fs) - wf))
    if len(ps) >= 6:
        m = len(ps) // 2
        block(ps[:m], be, "      pass H1"); block(ps[m:], be, "      pass H2")


def acct(T):
    bal = S.BAL0
    for r in T:
        bal += S.POS_FRAC * bal * S.LEV * r["net"]
    return bal


def real_be(A, sigs, rr):
    """Fee-adjusted break-even win rate from the MEDIAN stop distance of the actual chain."""
    last = -1; slfs = []
    for sg in sigs:
        if sg["i"] <= last:
            continue
        res = RR.simulate_rr(A, sg["i"], sg["side"], rr, "sl")
        if res is None:
            continue
        e = A[sg["i"]]["c"]
        sl = A[sg["i"]]["l"] * 0.999 if sg["side"] > 0 else A[sg["i"]]["h"] * 1.001
        slfs.append(abs(e - sl) / e); last = res[2]
    s = float(np.median(slfs)) if slfs else 0.0
    return ((FEE + s) / (s * (1 + rr)) if s > 0 else float("nan")), s


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "15m"
    rrs = [float(x) for x in sys.argv[2:]] or [1.0, 1.5]
    A, first, _ = build(tf)
    sigs = scan(A, first)
    print("=" * 110)
    print("15mReasy  on %s   |  LONG bull + A<=%.2f + eff-agg>=+%.0f   (SHORT mirror)" % (tf.upper(), R_EASY, EFF_MIN))
    print("=" * 110)
    print("  directional buckets %d" % len(sigs))
    for lbl, pred in CELLS:
        sub = sigs if pred is None else [s for s in sigs if pred(s)]
        nl = sum(1 for s in sub if s["side"] > 0)
        print("    %-24s %4d signals (%dL/%dS)" % (lbl, len(sub), nl, len(sub) - nl))
    print()

    for rr in rrs:
        nom = 1.0 / (1 + rr)
        rbe, sl = real_be(A, [s for s in sigs if f_both(s)], rr)
        print("-" * 110)
        print("RR 1:%.1f   nominal BE %.0f%%   |   median stop %.3f%%  ->  ***FEE-ADJUSTED BE %.1f%%***"
              % (rr, nom * 100, sl * 100, rbe * 100))
        print("-" * 110)
        for lbl, pred in CELLS:
            sub = sigs if pred is None else [s for s in sigs if pred(s)]
            T = taken(A, sub, rr)
            block(T, nom, lbl)
            if T:
                w = 100.0 * sum(1 for r in T if r["win"]) / len(T)
                print("         account $%s -> $%s   |  vs REAL BE %.1f%%: %s"
                      % (f"{S.BAL0:,.0f}", f"{acct(T):,.0f}", rbe * 100,
                         "CLEARS by %.1fpp" % (w - rbe * 100) if w >= rbe * 100 else "SHORT by %.1fpp" % (rbe * 100 - w)))
        print("  eff-agg threshold sweep (on the R-easy set):")
        for thr in (0, 15, 25, 35, 45):
            sub = [s for s in sigs if f_easy(s) and s["dspr"] >= thr]
            T = taken(A, sub, rr)
            if T:
                w = 100.0 * sum(1 for r in T if r["win"]) / len(T)
                net = (np.prod(1 + np.array([r["net"] for r in T])) - 1) * 100
                print("      dspr >= %2d : n=%4d  win %5.1f%%  net %+7.1f%%   %s"
                      % (thr, len(T), w, net, "<- proposed" if thr == 25 else ""))
        print("  HONEST TESTS:")
        partition(A, sigs, rr, nom, f_both, "15mReasy vs all candles")
        partition(A, sigs, rr, nom, f_eff, "does eff-agg add ON TOP of R-easy?", base=f_easy)
        print("  PER SIDE (15mReasy):")
        T = taken(A, [s for s in sigs if f_both(s)], rr)
        for sd, nm in ((1, "LONG "), (-1, "SHORT")):
            block([r for r in T if r["side"] == sd], nom, "    " + nm)
        print()


if __name__ == "__main__":
    main()
