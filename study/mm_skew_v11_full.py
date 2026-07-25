"""MMXSKEW v1.1 (post 2026-07-24 delta freeze) + FULL AGREEMENT + ACCELERATION (user spec, 2026-07-24):

    LONG  :  d-UP > 0  AND  d-LO > 0   AND  p1 > 0  AND  p2 > 0  AND  p2 > p1
    SHORT :  d-UP < 0  AND  d-LO < 0   AND  p1 < 0  AND  p2 < 0  AND  p2 < p1

i.e. BOTH price-range halves agree on flow, BOTH volume halves agree on price, AND the move ACCELERATED into
the close (p2 vs p1 is exactly the terminal's "ΔP" headline = dP_acc = p2 - p1).

  d-UP / d-LO : net delta of the upper / lower half of the PRICE range (split at (high+low)/2), % of curr_vol
                -> the "Δ↑ / Δ↓" row. From the stored `levels` profile (REAL data).
  p1 / p2     : (price_h1-open)/open and (close-price_h1)/price_h1, in % -> the "ΔP ( p1 / p2 )" row, split at
                the 50%-VOLUME mark. Needs price_h1, which is 1m-RECONSTRUCTED on most buckets (UNVERIFIED).

Method as in the sibling studies: taken() non-overlap; filter applied before the slot is claimed; baseline on the
SAME evaluable subset; within-chain partition for stats; component legs + opposite control; binomial/Fisher/
split-half; per-side win rates.

Run: python study/mm_skew_v11_full.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from study.mm_skew_v11_secondhalf import build, v11_sigs, taken, binom_ge, stats
from study.mm_skew_v11_vertdelta import partition, line

RRS = (1.0, 1.5)


def ok(sg):
    b = sg["b"]
    return b["dup"] is not None and b["p1"] is not None and b["p2"] is not None


def f_vert(sg):                    # both price-range halves agree
    b = sg["b"]
    return (b["dup"] > 0 and b["dlo"] > 0) if sg["side"] > 0 else (b["dup"] < 0 and b["dlo"] < 0)


def f_pboth(sg):                   # both volume halves agree on price
    b = sg["b"]
    return (b["p1"] > 0 and b["p2"] > 0) if sg["side"] > 0 else (b["p1"] < 0 and b["p2"] < 0)


def f_accel(sg):                   # the move ACCELERATED (p2 vs p1)
    b = sg["b"]
    return (b["p2"] > b["p1"]) if sg["side"] > 0 else (b["p2"] < b["p1"])


def f_full(sg):                    # THE PROPOSAL — all three
    return f_vert(sg) and f_pboth(sg) and f_accel(sg)


def f_opp(sg):
    return not f_full(sg)


def main():
    A, first = build()
    sigs = v11_sigs(A, first)
    ev = [s for s in sigs if ok(s)]
    print("FROZEN v1.1 signals: %d | judgeable (levels AND price_h1): %d (%.0f%%)"
          % (len(sigs), len(ev), 100.0 * len(ev) / max(1, len(sigs))))
    print("  WARNING: price_h1 is 1m-RECONSTRUCTED on most buckets, accuracy UNVERIFIED\n")
    print("  FUNNEL (share of judgeable signals surviving each leg):")
    for sd, nm in ((1, "LONG "), (-1, "SHORT")):
        ss = [s for s in ev if s["side"] == sd]
        if not ss:
            continue
        n = len(ss)
        print("    %s n=%3d -> vert(dUP&dLO) %3.0f%% | p1&p2 %3.0f%% | accel %3.0f%% | ALL THREE %3.0f%% (n=%d)"
              % (nm, n, 100.0 * sum(f_vert(s) for s in ss) / n, 100.0 * sum(f_pboth(s) for s in ss) / n,
                 100.0 * sum(f_accel(s) for s in ss) / n, 100.0 * sum(f_full(s) for s in ss) / n,
                 sum(f_full(s) for s in ss)))
    print()

    for rr in RRS:
        be = 1.0 / (1 + rr)
        print("=" * 100)
        print("RR 1:%.1f    break-even win = %.0f%%" % (rr, be * 100))
        print("=" * 100)
        print("  RE-LINKED chains (what each gate would actually trade):")
        print(line("baseline (no filter)", taken(A, ev, rr), be))
        print(line(">>> FULL  vert & p1p2 & accel", taken(A, ev, rr, f_full), be))
        print(line("    vert only", taken(A, ev, rr, f_vert), be))
        print(line("    p1&p2 only", taken(A, ev, rr, f_pboth), be))
        print(line("    accel only", taken(A, ev, rr, f_accel), be))
        print(line("    CONTROL: opposite", taken(A, ev, rr, f_opp), be))
        print("  PER-SIDE WIN RATE (re-linked):")
        for sd, nm in ((1, "LONG "), (-1, "SHORT")):
            bl = [r for r in taken(A, ev, rr) if r["side"] == sd]
            pr = [r for r in taken(A, ev, rr, f_full) if r["side"] == sd]
            nb, _, wb, netb = stats(bl); npx, _, wpx, netpx = stats(pr)
            print("    %s: baseline n=%2d win %5.1f%% net %+6.1f%%   ->   FULL n=%2d win %5.1f%% net %+6.1f%%"
                  % (nm, nb, wb, netb, npx, wpx, netpx))
        print("  HONEST TEST (same chain, partitioned):")
        partition(A, ev, rr, be, f_full, "FULL")
        partition(A, ev, rr, be, f_accel, "accel alone", show_half=False)
        print()


if __name__ == "__main__":
    main()
