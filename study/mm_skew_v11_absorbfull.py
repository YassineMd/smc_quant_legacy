"""MMXSKEW v1.1 (1h) — swap the frozen A_h2<0 for the BUCKET-LEVEL A<0.

FROZEN today:  A_h2 < 0   (2nd half of the 50%-volume split not absorbed)  -- needs `price_h1`, which is
               1m-RECONSTRUCTED and UNVERIFIED on most of the backtest, and needs >=20 baselined priors.
PROPOSAL:      A    < 0   (WHOLE bucket not absorbed, app.absorption.absorption()[0]) -- needs only the
               trailing window; NO price_h1, so NO reconstruction dependency and far better coverage.

Both are oriented so POSITIVE = the aggressor got ABSORBED; <0 = price moved easily for the aggressor.

Cells (spread + skew + delta + momentum held FIXED, so only the absorption leg varies):
    A  no absorption filter        B  A_h2<0 (FROZEN)        C  A<0 (PROPOSAL)        D  both
Plus: coverage comparison, within-chain partitions + Fisher + split-half, per-side, account sim.

Run: python study/mm_skew_v11_absorbfull.py
"""
from __future__ import annotations
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from app import absorption as ABS
import study.mm_skew_rr_sweep as RR
import study.mm_skew_strategy as S
from study.mm_skew_v11_tf import build, taken, binom_ge, block

RRS = (1.0, 1.5)
FEE = 0.0008


def sigs(A, first):
    """All v1.1 signals through spread+skew+delta+momentum, each carrying BOTH absorption readings."""
    out = []
    for i in range(max(first, 1), len(A) - 1):
        b = A[i]
        sk = b.get("sk")
        if sk is None:
            continue
        if b["up"] and sk > 0 and b["spread"] >= 35:
            s = 1
        elif b["dn"] and sk < 0 and b["spread"] <= -35:
            s = -1
        else:
            continue
        d = b["delta"]
        if not ((0.0 < d <= 15.0) if s > 0 else (d < 0.0)):
            continue
        if not ((b["spread"] - A[i - 1]["spread"]) * s > 0.0):
            continue
        try:
            a_full = ABS.absorption(A, i)[0]
        except Exception:
            a_full = None
        try:
            a2 = ABS.absorption_halves(A, i)[1]
        except Exception:
            a2 = None
        out.append(dict(i=i, side=s, t=float(b.get("start_time", 0)), A=a_full, A2=a2))
    return out


f_h2 = lambda sg: sg["A2"] is not None and sg["A2"] < 0.0            # FROZEN
f_full = lambda sg: sg["A"] is not None and sg["A"] < 0.0            # PROPOSAL
f_both = lambda sg: f_h2(sg) and f_full(sg)

CELLS = (("A  no absorption filter", None),
         ("B  A_h2 < 0   <- FROZEN", f_h2),
         ("C  A < 0      <- PROPOSAL", f_full),
         ("D  both", f_both))


def fisher(a, b, c, d):
    n = a + b + c + d
    if n == 0 or (a + b) == 0 or (c + d) == 0:
        return float("nan")
    r1, c1 = a + b, a + c
    hp = lambda x: (math.comb(c1, x) * math.comb(n - c1, r1 - x)) / math.comb(n, r1)
    po = hp(a)
    return min(1.0, sum(hp(x) for x in range(max(0, r1 - (n - c1)), min(r1, c1) + 1) if hp(x) <= po + 1e-12))


def partition(A, S_, rr, be, pred, lbl):
    last = -1; ps = []; fs = []
    for sg in S_:
        if sg["i"] <= last:
            continue
        res = RR.simulate_rr(A, sg["i"], sg["side"], rr, "sl")
        if res is None:
            continue
        row = dict(side=sg["side"], win=(res[0] == "TP"), net=res[1] - FEE, t=sg["t"])
        (ps if pred(sg) else fs).append(row); last = res[2]
    wp = sum(r["win"] for r in ps); wf = sum(r["win"] for r in fs)
    print("    [%s] partition (n=%d = %d pass + %d fail):" % (lbl, len(ps) + len(fs), len(ps), len(fs)))
    block(ps, be, "      pass"); block(fs, be, "      fail")
    print("      Fisher pass-vs-fail: p=%.3f" % fisher(wp, len(ps) - wp, wf, len(fs) - wf))
    if len(ps) >= 6:
        m = len(ps) // 2
        block(ps[:m], be, "      pass H1"); block(ps[m:], be, "      pass H2")


def main():
    A, first, _ = build("1h")
    Sg = sigs(A, first)
    covA = sum(1 for s in Sg if s["A"] is not None); cov2 = sum(1 for s in Sg if s["A2"] is not None)
    print("=" * 108)
    print("MMXSKEW v1.1 (1h) — BUCKET-LEVEL A<0  vs  FROZEN A_h2<0   [spread+skew+delta+momentum fixed]")
    print("=" * 108)
    print("  signals reaching the absorption leg: %d" % len(Sg))
    print("  COVERAGE — A computable on %d (%.0f%%)  |  A_h2 computable on %d (%.0f%%)   <- A needs NO price_h1"
          % (covA, 100.0 * covA / len(Sg), cov2, 100.0 * cov2 / len(Sg)))
    for lbl, pred in CELLS:
        sub = Sg if pred is None else [s for s in Sg if pred(s)]
        nl = sum(1 for s in sub if s["side"] > 0)
        print("  %-28s %3d signals (%dL/%dS)" % (lbl, len(sub), nl, len(sub) - nl))
    print()

    for rr in RRS:
        be = 1.0 / (1 + rr)
        print("-" * 108)
        print("RR 1:%.1f   (break-even %.0f%%)" % (rr, be * 100))
        print("-" * 108)
        for lbl, pred in CELLS:
            sub = Sg if pred is None else [s for s in Sg if pred(s)]
            T = taken(A, sub, rr)
            block(T, be, lbl)
            bal = S.BAL0
            for r in T:
                bal += S.POS_FRAC * bal * S.LEV * r["net"]
            print("       account $%s -> $%s (%+.1f%%)" % (f"{S.BAL0:,.0f}", f"{bal:,.0f}", (bal / S.BAL0 - 1) * 100))
        print("  HONEST TESTS (one chain = all signals, partitioned):")
        partition(A, Sg, rr, be, f_full, "A < 0  (PROPOSAL)")
        partition(A, Sg, rr, be, f_h2, "A_h2 < 0  (FROZEN)")
        print("  PER-SIDE:")
        for sd, nm in ((1, "LONG "), (-1, "SHORT")):
            for lbl, pred in (("A_h2<0", f_h2), ("A<0   ", f_full)):
                sub = [s for s in Sg if pred(s)]
                block([r for r in taken(A, sub, rr) if r["side"] == sd], be, "    %s %s" % (nm, lbl))
        print()


if __name__ == "__main__":
    main()
