"""MMXSKEW v1.1 (post 2026-07-24 delta freeze) + ABSORPTION R2 < 0  (user spec, 2026-07-24, BOTH sides).

    LONG and SHORT :  A_h2 < 0

A_h2 is the 2nd leg of the terminal's "Absorb R ( A1 / A2 )" row (app/absorption.absorption_halves), oriented so
POSITIVE = that half's AGGRESSOR GOT ABSORBED. So **A2 < 0 = the second half moved EASILY** — the aggressor was
NOT absorbed into the close, i.e. no wall in the way on the leg you're entering on.

Note this is the ABSOLUTE version of the Skew-Divergence "R2-vacuum" filter (which used the RELATIVE dA = A2-A1
<= 0). Both are logged here so the two can be compared directly.

DATA: A_h2 needs price_h1+delta_h1 on the bucket AND >= MIN_OBS(20) baselined priors in the trailing WINDOW(30),
and price_h1 is 1m-RECONSTRUCTED on most buckets (accuracy UNVERIFIED) -> coverage is reported up front.

Method as in the sibling studies: taken() non-overlap, filter applied before the slot is claimed, baseline on the
SAME evaluable subset, within-chain partition for the statistics, opposite control, binomial/Fisher/split-half,
per-side win rates.

Run: python study/mm_skew_v11_absorbr2.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import absorption as ABS
from study.mm_skew_v11_secondhalf import build, v11_sigs, taken, binom_ge, stats
from study.mm_skew_v11_vertdelta import partition, line

RRS = (1.0, 1.5)


def attach_absorption(A, sigs):
    """Compute (A_h1, A_h2) for each signal bucket against the real trailing window."""
    for sg in sigs:
        try:
            a1, a2 = ABS.absorption_halves(A, sg["i"])
        except Exception:
            a1 = a2 = None
        sg["A1"] = a1; sg["A2"] = a2


def ok(sg):
    return sg.get("A2") is not None


def f_r2(sg):                      # THE PROPOSAL — second half NOT absorbed, both sides
    return sg["A2"] < 0


def f_vac(sg):                     # comparison: the RELATIVE Skew-Div style vacuum (dA = A2 - A1 <= 0)
    return sg.get("A1") is not None and (sg["A2"] - sg["A1"]) <= 0


def f_opp(sg):
    return not f_r2(sg)


def main():
    A, first = build()
    sigs = v11_sigs(A, first)
    attach_absorption(A, sigs)
    ev = [s for s in sigs if ok(s)]
    print("FROZEN v1.1 signals: %d | judgeable (A_h2 computable): %d (%.0f%%)"
          % (len(sigs), len(ev), 100.0 * len(ev) / max(1, len(sigs))))
    print("  (A_h2 needs price_h1 on the bucket + >=20 baselined priors; price_h1 is 1m-RECONSTRUCTED -> UNVERIFIED)\n")
    for sd, nm in ((1, "LONG "), (-1, "SHORT")):
        ss = [s for s in ev if s["side"] == sd]
        if ss:
            print("  %s judgeable n=%3d -> passes A2<0 %3.0f%% (n=%d)   [rel. vacuum dA<=0: %3.0f%%]"
                  % (nm, len(ss), 100.0 * sum(f_r2(s) for s in ss) / len(ss), sum(f_r2(s) for s in ss),
                     100.0 * sum(f_vac(s) for s in ss) / len(ss)))
    print()

    for rr in RRS:
        be = 1.0 / (1 + rr)
        print("=" * 100)
        print("RR 1:%.1f    break-even win = %.0f%%" % (rr, be * 100))
        print("=" * 100)
        print("  RE-LINKED chains (what each gate would actually trade):")
        print(line("baseline (no filter)", taken(A, ev, rr), be))
        print(line(">>> PROPOSAL  A2 < 0", taken(A, ev, rr, f_r2), be))
        print(line("    (compare) rel. vacuum dA<=0", taken(A, ev, rr, f_vac), be))
        print(line("    CONTROL: A2 >= 0 (opposite)", taken(A, ev, rr, f_opp), be))
        print("  PER-SIDE WIN RATE (re-linked):")
        for sd, nm in ((1, "LONG "), (-1, "SHORT")):
            bl = [r for r in taken(A, ev, rr) if r["side"] == sd]
            pr = [r for r in taken(A, ev, rr, f_r2) if r["side"] == sd]
            nb, _, wb, netb = stats(bl); npx, _, wpx, netpx = stats(pr)
            print("    %s: baseline n=%2d win %5.1f%% net %+6.1f%%   ->   A2<0 n=%2d win %5.1f%% net %+6.1f%%"
                  % (nm, nb, wb, netb, npx, wpx, netpx))
        print("  HONEST TEST (same chain, partitioned):")
        partition(A, ev, rr, be, f_r2, "A2 < 0")
        print()


if __name__ == "__main__":
    main()
