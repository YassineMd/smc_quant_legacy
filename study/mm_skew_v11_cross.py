"""MMXSKEW v1.1 (post 2026-07-24 delta freeze) + the CROSS filter (user spec, 2026-07-24):

    LONG  :  d-UP > 0   AND  dP_h2 > 0
    SHORT :  d-LO < 0   AND  dP_h2 < 0

NOTE the ASYMMETRY: it takes the UPPER-half delta for longs and the LOWER-half delta for shorts (i.e. each side
looks at the half its own aggressor is pushing into), unlike mm_skew_v11_vertdelta.py which required BOTH halves.

  d-UP / d-LO = net delta of the upper / lower half of the PRICE range (split at (high+low)/2), % of curr_vol
                -> the terminal's "Δ↑ / Δ↓" row. From the stored `levels` profile (real data).
  dP_h2       = (close - price_h1)/price_h1*100, the 2nd leg of the "ΔP ( p1 / p2 )" row, split at the
                50%-VOLUME mark -> needs price_h1, which is 1m-RECONSTRUCTED on most buckets (unvalidated).

Same method as the sibling studies: taken() non-overlap; filter applied before the slot is claimed; baseline on
the SAME evaluable subset; within-chain partition for the statistics; legs + opposite control; binomial/Fisher/
split-half.

Run: python study/mm_skew_v11_cross.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from study.mm_skew_v11_secondhalf import build, v11_sigs, taken, binom_ge, stats
from study.mm_skew_v11_vertdelta import partition, line

RRS = (1.0, 1.5)


def ok(sg):                       # needs the vertical legs AND price_h1 (for p2)
    b = sg["b"]
    return b["dup"] is not None and b["p2"] is not None


def f_vert_leg(sg):               # LONG -> d-UP > 0 ;  SHORT -> d-LO < 0   (the asymmetric leg)
    b = sg["b"]
    return b["dup"] > 0 if sg["side"] > 0 else b["dlo"] < 0


def f_p2(sg):                     # LONG -> dP_h2 > 0 ; SHORT -> dP_h2 < 0
    b = sg["b"]
    return b["p2"] > 0 if sg["side"] > 0 else b["p2"] < 0


def f_cross(sg):                  # THE PROPOSAL
    return f_vert_leg(sg) and f_p2(sg)


def f_opp(sg):
    return not f_cross(sg)


def main():
    A, first = build()
    sigs = v11_sigs(A, first)
    ev = [s for s in sigs if ok(s)]
    print("FROZEN v1.1 signals: %d | judgeable (levels AND price_h1): %d (%.0f%%)"
          % (len(sigs), len(ev), 100.0 * len(ev) / max(1, len(sigs))))
    print("  WARNING: price_h1 is 1m-RECONSTRUCTED on most buckets and its accuracy is UNVERIFIED")
    for sd, nm in ((1, "LONG "), (-1, "SHORT")):
        ss = [s for s in ev if s["side"] == sd]
        if ss:
            print("  %s n=%3d -> passes vert-leg %3.0f%% | passes p2 %3.0f%% | passes BOTH %3.0f%%"
                  % (nm, len(ss), 100.0 * sum(f_vert_leg(s) for s in ss) / len(ss),
                     100.0 * sum(f_p2(s) for s in ss) / len(ss), 100.0 * sum(f_cross(s) for s in ss) / len(ss)))
    print()

    for rr in RRS:
        be = 1.0 / (1 + rr)
        print("=" * 100)
        print("RR 1:%.1f    break-even win = %.0f%%" % (rr, be * 100))
        print("=" * 100)
        print("  RE-LINKED chains (what each gate would actually trade):")
        print(line("baseline (no filter)", taken(A, ev, rr), be))
        print(line(">>> CROSS  vert-leg & p2", taken(A, ev, rr, f_cross), be))
        print(line("    vert-leg only", taken(A, ev, rr, f_vert_leg), be))
        print(line("    p2 only", taken(A, ev, rr, f_p2), be))
        print(line("    CONTROL: opposite", taken(A, ev, rr, f_opp), be))
        print("  HONEST TEST (same chain, partitioned):")
        partition(A, ev, rr, be, f_cross, "CROSS")
        partition(A, ev, rr, be, f_vert_leg, "vert-leg alone", show_half=False)
        partition(A, ev, rr, be, f_p2, "p2 alone", show_half=False)
        print("  PER SIDE (re-linked):")
        for sd, nm in ((1, "LONG "), (-1, "SHORT")):
            bl = [r for r in taken(A, ev, rr) if r["side"] == sd]
            pr = [r for r in taken(A, ev, rr, f_cross) if r["side"] == sd]
            nb, _, wb, netb = stats(bl); npx, _, wpx, netpx = stats(pr)
            print("    %s: baseline n=%2d win %5.1f%% net %+6.1f%%  ->  CROSS n=%2d win %5.1f%% net %+6.1f%%"
                  % (nm, nb, wb, netb, npx, wpx, netpx))
        print()


if __name__ == "__main__":
    main()
