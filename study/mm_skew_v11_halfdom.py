"""MMXSKEW v1.1 (**post BOTH 2026-07-24 freezes: delta + A_h2<0**) + a ½dom ANTI-DOMINANCE filter (user spec):

    LONG  :  ½dom UPPER half  <  55% B     ->  buy_share(upper) < 55
    SHORT :  ½dom LOWER half  <  55% S     ->  sell_share(lower) < 55  ==  buy_share(lower) > 45

½dom is the terminal's per-half COMPOSITION row: the volume profile is split at the price midpoint (high+low)/2
and each half reports its DOMINANT side and that side's SHARE of the half. Unlike Δ↑/Δ↓ (net delta, size-
dependent) this is a pure share, so it flags dominance regardless of how much volume the half holds.

The condition is ANTI-dominance: the half the move is running into must NOT be strongly one-sided — the same
anti-blowoff logic as the frozen `delta <= 15` long cap. Note the asymmetry (LONG reads the UPPER half, SHORT the
LOWER half): each side inspects the half its own aggressor is pushing into.

BASELINE = the CURRENT frozen v1.1: base gate AND the delta freeze AND A_h2 < 0. n is therefore already small
before this filter is applied — reported up front.

Method as in the siblings: taken() non-overlap; filter applied before the slot is claimed; baseline on the SAME
evaluable subset; within-chain partition for the statistics; opposite control; binomial/Fisher/split-half;
per-side win rates.

Run: python study/mm_skew_v11_halfdom.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import absorption as ABS
from study.mm_skew_v11_secondhalf import build, taken, binom_ge, stats
from study.mm_skew_v11_vertdelta import partition, line

RRS = (1.0, 1.5)
DOM_MAX = 55.0


def frozen_v11_sigs(A, first):
    """The CURRENT frozen v1.1: base gate + delta freeze + A_h2 < 0 (fail-closed), mirroring
    app/mmxskew_detect.detect's v11 flag."""
    out = []
    for i in range(first, len(A) - 1):
        b = A[i]
        if b.get("sk") is None:
            continue
        d = b["delta"]
        if b["up"] and b["sk"] > 0 and b["spread"] >= 35 and 0.0 < d <= 15.0:
            s = 1
        elif b["dn"] and b["sk"] < 0 and b["spread"] <= -35 and d < 0.0:
            s = -1
        else:
            continue
        try:
            a2 = ABS.absorption_halves(A, i)[1]
        except Exception:
            a2 = None
        if a2 is None or a2 >= 0.0:            # FAIL-CLOSED, exactly like the live gate
            continue
        out.append(dict(i=i, side=s, t=float(b.get("start_time", 0)), b=b))
    return out


def ok(sg):
    b = sg["b"]
    return (b["dom_up_bs"] is not None) if sg["side"] > 0 else (b["dom_lo_bs"] is not None)


def f_dom(sg):                    # THE PROPOSAL
    b = sg["b"]
    if sg["side"] > 0:
        return b["dom_up_bs"] < DOM_MAX                 # upper half < 55% B
    return (100.0 - b["dom_lo_bs"]) < DOM_MAX           # lower half < 55% S


def f_opp(sg):
    return not f_dom(sg)


def main():
    A, first = build()
    sigs = frozen_v11_sigs(A, first)
    ev = [s for s in sigs if ok(s)]
    L = [s for s in ev if s["side"] > 0]; S = [s for s in ev if s["side"] < 0]
    print("CURRENT frozen v1.1 (base + delta freeze + A_h2<0): %d signals | judgeable for 1/2dom: %d (%dL/%dS)"
          % (len(sigs), len(ev), len(L), len(S)))
    if L:
        print("  LONG  passes upper<55%%B : %3.0f%% (n=%d)" % (100.0 * sum(f_dom(s) for s in L) / len(L),
                                                               sum(f_dom(s) for s in L)))
    if S:
        print("  SHORT passes lower<55%%S : %3.0f%% (n=%d)" % (100.0 * sum(f_dom(s) for s in S) / len(S),
                                                               sum(f_dom(s) for s in S)))
    print()

    for rr in RRS:
        be = 1.0 / (1 + rr)
        print("=" * 100)
        print("RR 1:%.1f    break-even win = %.0f%%" % (rr, be * 100))
        print("=" * 100)
        print("  RE-LINKED chains (what each gate would actually trade):")
        print(line("baseline = current frozen v1.1", taken(A, ev, rr), be))
        print(line(">>> PROPOSAL  1/2dom < 55%", taken(A, ev, rr, f_dom), be))
        print(line("    CONTROL: >= 55% (opposite)", taken(A, ev, rr, f_opp), be))
        print("  PER-SIDE WIN RATE (re-linked):")
        for sd, nm in ((1, "LONG "), (-1, "SHORT")):
            bl = [r for r in taken(A, ev, rr) if r["side"] == sd]
            pr = [r for r in taken(A, ev, rr, f_dom) if r["side"] == sd]
            nb, _, wb, netb = stats(bl); npx, _, wpx, netpx = stats(pr)
            print("    %s: baseline n=%2d win %5.1f%% net %+6.1f%%   ->   1/2dom n=%2d win %5.1f%% net %+6.1f%%"
                  % (nm, nb, wb, netb, npx, wpx, netpx))
        print("  HONEST TEST (same chain, partitioned):")
        partition(A, ev, rr, be, f_dom, "1/2dom < 55%")
        print()


if __name__ == "__main__":
    main()
