"""MMXSKEW v1.1 (POST delta-freeze 2026-07-24) + VERTICAL Δ↑/Δ↓ AGREEMENT.

The terminal's "Δ↑ / Δ↓" row: the volume profile is split at the PRICE MIDPOINT (high+low)/2 and each half's NET
delta (buy-sell) is shown as % of curr_vol, so Δ↑ + Δ↓ = the whole-bucket delta%.

Hypothesis (user): BOTH halves must agree with the trade direction —
    LONG  : Δ↑ > 0 AND Δ↓ > 0      (net buying in the upper AND the lower half of the range)
    SHORT : Δ↑ < 0 AND Δ↓ < 0      (net selling in both)

This is a REAL additional constraint: the base gate only fixes the TOTAL delta sign (LONG 0<delta<=15), which a
bucket can satisfy with one half negative. It needs only the stored `levels` profile — NOT the 1m-reconstructed
delta_h1/price_h1 — so it is judgeable on far more signals than the volume-split test in mm_skew_v11_secondhalf.py.

METHOD: taken() non-overlap only [canonical-taken-basis]; filter applied BEFORE the slot is claimed; baseline
restricted to the SAME evaluable subset (like-for-like); WITHIN-CHAIN partition for the statistics (separately
re-linked chains are not a partition); disjoint legs + opposite-filter control; exact binomial + Fisher +
split-half [split-half-gate-is-near-vacuous: split-half is a fragility check, not a significance test].

Run: python study/mm_skew_v11_vertdelta.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import study.mm_skew_rr_sweep as RR
from study.mm_skew_v11_secondhalf import build, v11_sigs, taken, binom_ge, fisher, stats, FEE

RRS = (1.0, 1.5)


def ok(sg):                      # judgeable: the bucket has a levels profile
    return sg["b"]["dup"] is not None


def f_both(sg):                  # THE PROPOSAL — both halves agree with the side
    b = sg["b"]; s = sg["side"]
    return (b["dup"] > 0 and b["dlo"] > 0) if s > 0 else (b["dup"] < 0 and b["dlo"] < 0)


def f_up(sg):                    # upper half only
    b = sg["b"]; s = sg["side"]
    return b["dup"] > 0 if s > 0 else b["dup"] < 0


def f_lo(sg):                    # lower half only
    b = sg["b"]; s = sg["side"]
    return b["dlo"] > 0 if s > 0 else b["dlo"] < 0


def f_opp(sg):                   # CONTROL: complement of the proposal
    return not f_both(sg)


def line(lbl, rows, be):
    n, w, wr, net = stats(rows)
    p = binom_ge(w, n, be) if n else float("nan")
    return "    %-32s n=%3d win %5.1f%% net %+7.1f%%  P(>=win|BE)=%s" % (
        lbl, n, wr, net, ("%.3f" % p) if n else "--")


def partition(A, ev, rr, be, pred, lbl, show_half=True):
    """ONE chain, split by the predicate -> a true partition (n's sum). This is the honest test."""
    last = -1; ps = []; fs = []
    for sg in ev:
        if sg["i"] <= last:
            continue
        res = RR.simulate_rr(A, sg["i"], sg["side"], rr, "sl")
        if res is None:
            continue
        row = dict(side=sg["side"], win=(res[0] == "TP"), net=res[1] - FEE, t=sg["t"])
        (ps if pred(sg) else fs).append(row); last = res[2]
    np_, wp, wrp, netp = stats(ps); nf, wf, wrf, netf = stats(fs)
    print("    [%s] within-chain partition (n=%d = %d pass + %d fail):" % (lbl, np_ + nf, np_, nf))
    print("      pass: n=%3d win %5.1f%% net %+7.1f%%  P(>=win|BE)=%s"
          % (np_, wrp, netp, ("%.3f" % binom_ge(wp, np_, be)) if np_ else "--"))
    print("      fail: n=%3d win %5.1f%% net %+7.1f%%  P(>=win|BE)=%s"
          % (nf, wrf, netf, ("%.3f" % binom_ge(wf, nf, be)) if nf else "--"))
    print("      Fisher pass-vs-fail: p=%.3f" % fisher(wp, np_ - wp, wf, nf - wf))
    if show_half and np_ >= 6:
        mid = np_ // 2
        h = []
        for hl, sub in (("H1", ps[:mid]), ("H2", ps[mid:])):
            n2, _, wr2, net2 = stats(sub); h.append("%s n=%d win %.1f%% net %+.1f%%" % (hl, n2, wr2, net2))
        print("      pass split-half: %s | %s" % (h[0], h[1]))
    return ps, fs


def main():
    A, first = build()
    sigs = v11_sigs(A, first)
    ev = [s for s in sigs if ok(s)]
    print("FROZEN v1.1 signals (with the 2026-07-24 delta freeze): %d" % len(sigs))
    print("  judgeable (bucket has a `levels` profile): %d (%.0f%%)  <- real stored data, no reconstruction"
          % (len(ev), 100.0 * len(ev) / max(1, len(sigs))))
    for sd, nm in ((1, "LONG "), (-1, "SHORT")):
        ss = [s for s in ev if s["side"] == sd]
        if ss:
            print("  %s judgeable n=%3d -> passes dUP %3.0f%% | passes dLO %3.0f%% | passes BOTH %3.0f%%"
                  % (nm, len(ss), 100.0 * sum(f_up(s) for s in ss) / len(ss),
                     100.0 * sum(f_lo(s) for s in ss) / len(ss), 100.0 * sum(f_both(s) for s in ss) / len(ss)))
    print()

    for rr in RRS:
        be = 1.0 / (1 + rr)
        print("=" * 100)
        print("RR 1:%.1f    break-even win = %.0f%%" % (rr, be * 100))
        print("=" * 100)
        print("  RE-LINKED chains (what each gate would actually trade):")
        print(line("baseline (no dUP/dLO filter)", taken(A, ev, rr), be))
        print(line(">>> PROPOSAL  dUP & dLO agree", taken(A, ev, rr, f_both), be))
        print(line("    dUP only", taken(A, ev, rr, f_up), be))
        print(line("    dLO only", taken(A, ev, rr, f_lo), be))
        print(line("    CONTROL: opposite", taken(A, ev, rr, f_opp), be))
        print("  HONEST TEST (same chain, partitioned):")
        partition(A, ev, rr, be, f_both, "PROPOSAL dUP&dLO")
        partition(A, ev, rr, be, f_up, "dUP alone", show_half=False)
        partition(A, ev, rr, be, f_lo, "dLO alone", show_half=False)
        print("  PER SIDE (re-linked):")
        for sd, nm in ((1, "LONG "), (-1, "SHORT")):
            bl = [r for r in taken(A, ev, rr) if r["side"] == sd]
            pr = [r for r in taken(A, ev, rr, f_both) if r["side"] == sd]
            nb, _, wb, netb = stats(bl); npx, _, wpx, netpx = stats(pr)
            print("    %s: baseline n=%2d win %5.1f%% net %+6.1f%%  ->  proposal n=%2d win %5.1f%% net %+6.1f%%"
                  % (nm, nb, wb, netb, npx, wpx, netpx))
        print()


if __name__ == "__main__":
    main()
