"""ABSORB-EASY DIRECTIONAL — a standalone setup (NOT MMXSKEW v1.1), tested on 15m (1h for contrast).

    LONG  = bullish bucket (close>open) AND absorption label EASY
    SHORT = bearish bucket (close<open) AND absorption label EASY
    SL    = 0.1% beyond the bucket extreme (low*0.999 long / high*1.001 short), TP = RR x SL, SL-first tie.

`A` = app.absorption.absorption()[0], ORIENTED so POSITIVE = the aggressor got ABSORBED. So NEGATIVE = the
aggressor moved price EASILY. The module's descriptive labels (chosen on SD(R)~0.78, not fitted):
    ABSORBED >= +1.5 | heavy >= +0.75 | light <= -0.75 | EASY <= -1.5
So "EASY" is A <= -1.5. Thresholds -0.75 / -1.5 / -2.5 are swept because the label boundary is descriptive.

"in the direction of the candle" is ambiguous, so BOTH readings are reported:
    (a) DIR-ONLY  : candle direction + EASY, whatever side was aggressing
    (b) FLOW-AGREE: candle direction + EASY + the aggressor side matches the candle (delta>0 on a bull bucket)

*** THE CONTROL IS THE POINT ***: "trade EVERY candle in its own direction" at the same bracket. A momentum
setup on constant-volume buckets can look fine simply because the bracket is favourable; the EASY filter only
earns its keep if it BEATS that baseline. Reported first, on the same non-overlap chain.

Run: python study/absorb_easy_direction.py [tf]        (default 15m)
"""
from __future__ import annotations
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from app import absorption as ABS
import study.mm_skew_rr_sweep as RR
import study.mm_skew_strategy as S
from study.mm_skew_v11_tf import build, binom_ge, block

RRS = (1.0, 1.5)
FEE = 0.0008
THRS = (-0.75, -1.5, -2.5)


def scan(A, first):
    """Every directional bucket with its absorption reading attached."""
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
            a, _r, aside = ABS.absorption(A, i)
        except Exception:
            a, aside = None, 0
        out.append(dict(i=i, side=s, t=float(b.get("start_time", 0)), A=a, aside=aside))
    return out


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


def partition(A, sigs, rr, be, pred, lbl):
    last = -1; ps = []; fs = []
    for sg in sigs:
        if sg["i"] <= last:
            continue
        res = RR.simulate_rr(A, sg["i"], sg["side"], rr, "sl")
        if res is None:
            continue
        row = dict(side=sg["side"], win=(res[0] == "TP"), net=res[1] - FEE, t=sg["t"])
        (ps if pred(sg) else fs).append(row); last = res[2]
    wp = sum(r["win"] for r in ps); wf = sum(r["win"] for r in fs)
    print("    [%s] partition of the ALL-CANDLES chain (n=%d = %d pass + %d fail):"
          % (lbl, len(ps) + len(fs), len(ps), len(fs)))
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


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "15m"
    A, first, _ = build(tf)
    sigs = scan(A, first)
    cov = sum(1 for s in sigs if s["A"] is not None)
    print("=" * 108)
    print("ABSORB-EASY DIRECTIONAL on %s   (SL 0.1%% beyond the extreme, TP = RR x SL, fee 0.08%%)" % tf.upper())
    print("=" * 108)
    print("  directional buckets: %d   |  absorption computable on %d (%.0f%%)"
          % (len(sigs), cov, 100.0 * cov / max(1, len(sigs))))
    for thr in THRS:
        a = sum(1 for s in sigs if s["A"] is not None and s["A"] <= thr)
        b = sum(1 for s in sigs if s["A"] is not None and s["A"] <= thr and s["aside"] == s["side"])
        print("    A <= %5.2f : dir-only %4d   flow-agree %4d" % (thr, a, b))
    print()

    for rr in RRS:
        be = 1.0 / (1 + rr)
        print("-" * 108)
        print("RR 1:%.1f   (break-even %.0f%%)" % (rr, be * 100))
        print("-" * 108)
        base = taken(A, sigs, rr)
        block(base, be, "CONTROL all candles")
        print("       account $%s -> $%s" % (f"{S.BAL0:,.0f}", f"{acct(base):,.0f}"))
        for thr in THRS:
            T = taken(A, sigs, rr, lambda sg, t=thr: sg["A"] is not None and sg["A"] <= t)
            block(T, be, "EASY A<=%.2f dir" % thr)
            print("       account $%s -> $%s" % (f"{S.BAL0:,.0f}", f"{acct(T):,.0f}"))
        for thr in THRS:
            T = taken(A, sigs, rr,
                      lambda sg, t=thr: sg["A"] is not None and sg["A"] <= t and sg["aside"] == sg["side"])
            block(T, be, "  +flow-agree %.2f" % thr)
        print("  HONEST TEST (does EASY beat 'every candle'?):")
        partition(A, sigs, rr, be, lambda sg: sg["A"] is not None and sg["A"] <= -1.5, "A <= -1.5 (EASY)")
        print("  PER-SIDE (EASY A<=-1.5, dir-only):")
        T = taken(A, sigs, rr, lambda sg: sg["A"] is not None and sg["A"] <= -1.5)
        for sd, nm in ((1, "LONG "), (-1, "SHORT")):
            block([r for r in T if r["side"] == sd], be, "    " + nm)
        print()


if __name__ == "__main__":
    main()
