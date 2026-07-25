"""MMXSKEW v1.1 — replace the eff-agg LEVEL gate with an eff-agg MOMENTUM gate.

Frozen v1.1 uses a LEVEL condition:   LONG spread(i) >= +35   /  SHORT spread(i) <= -35.
User proposal — a CHANGE condition:   LONG spread(i) > spread(i-1)  /  SHORT spread(i) < spread(i-1),
i.e. the effective-aggressor reading moved in the trade's direction across the two candles. This is the same
`dE` quantity the Flow Flip overlay grades on (dE = effagg(c2) - effagg(c1)).

Normalised for both sides:  dspr = spread(i)*side (LEVEL) and  dE = (spread(i) - spread(i-1))*side (MOMENTUM);
the frozen gate is `dspr >= 35`, the proposal is `dE > 0`.

Tested as a 2x2 so "replace" is answered honestly — a replacement is only justified if MOMENTUM alone >= LEVEL
alone, and the BOTH cell shows whether they are complementary or redundant:
    A  neither      B  LEVEL only (FROZEN)      C  MOMENTUM only (PROPOSAL)      D  BOTH
delta freeze + A_h2<0 (fail-closed) are held FIXED in every cell. taken() non-overlap throughout.

Run: python study/mm_skew_v11_effmom.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import absorption as ABS
from study.mm_skew_v11_secondhalf import build, taken, binom_ge, fisher, stats
from study.mm_skew_v11_vertdelta import partition, line

RRS = (1.0, 1.5)
LEVEL_MIN = 35.0


def sigs(A, first):
    """Every v1.1 signal with NO eff-agg condition (delta freeze + A_h2<0 still applied), carrying both the
    LEVEL (dspr) and MOMENTUM (dE) readings so the cells below are pure subsets of one population."""
    out = []
    for i in range(max(first, 1), len(A) - 1):
        b = A[i]
        if b.get("sk") is None:
            continue
        d = b["delta"]
        if b["up"] and b["sk"] > 0 and 0.0 < d <= 15.0:
            s = 1
        elif b["dn"] and b["sk"] < 0 and d < 0.0:
            s = -1
        else:
            continue
        try:
            a2 = ABS.absorption_halves(A, i)[1]
        except Exception:
            a2 = None
        if a2 is None or a2 >= 0.0:
            continue
        out.append(dict(i=i, side=s, t=float(b.get("start_time", 0)), b=b,
                        dspr=b["spread"] * s,
                        dE=(b["spread"] - A[i - 1]["spread"]) * s))
    return out


f_level = lambda sg: sg["dspr"] >= LEVEL_MIN
f_mom = lambda sg: sg["dE"] > 0.0
f_both = lambda sg: f_level(sg) and f_mom(sg)

CELLS = (("A  neither (no eff-agg)", None),
         ("B  LEVEL only   <- FROZEN", f_level),
         ("C  MOMENTUM only  <- PROPOSAL", f_mom),
         ("D  BOTH", f_both))


def main():
    A, first = build()
    all_sigs = sigs(A, first)
    n = len(all_sigs)
    print("v1.1 population with NO eff-agg condition (delta freeze + A_h2<0 fixed): %d signals" % n)
    for lbl, pred in CELLS:
        sub = all_sigs if pred is None else [s for s in all_sigs if pred(s)]
        nl = sum(1 for s in sub if s["side"] > 0)
        print("  %-30s %3d signals (%dL/%dS)" % (lbl, len(sub), nl, len(sub) - nl))
    ov_l = sum(1 for s in all_sigs if f_level(s)); ov_m = sum(1 for s in all_sigs if f_mom(s))
    ov_b = sum(1 for s in all_sigs if f_both(s))
    print("  overlap: LEVEL %d | MOMENTUM %d | BOTH %d -> momentum keeps %.0f%% of the LEVEL set\n"
          % (ov_l, ov_m, ov_b, 100.0 * ov_b / max(1, ov_l)))

    for rr in RRS:
        be = 1.0 / (1 + rr)
        print("=" * 100)
        print("RR 1:%.1f    break-even win = %.0f%%" % (rr, be * 100))
        print("=" * 100)
        print("  THE 2x2 (each cell = its own non-overlap chain):")
        for lbl, pred in CELLS:
            sub = all_sigs if pred is None else [s for s in all_sigs if pred(s)]
            print(line(lbl, taken(A, sub, rr), be))

        print("  DISJOINT BANDS of eff-agg MOMENTUM dE (does the sign carry anything?):")
        for lbl, pred in (("dE < -20", lambda v: v < -20), ("-20 <= dE < 0", lambda v: -20 <= v < 0),
                          ("0 <= dE < 20", lambda v: 0 <= v < 20), ("dE >= 20", lambda v: v >= 20)):
            sub = [s for s in all_sigs if pred(s["dE"])]
            print(line(lbl, taken(A, sub, rr), be))

        print("  HONEST TESTS (one chain, partitioned):")
        partition(A, all_sigs, rr, be, f_mom, "MOMENTUM dE>0 (on ALL signals)")
        lvl = [s for s in all_sigs if f_level(s)]
        partition(A, lvl, rr, be, f_mom, "MOMENTUM on top of LEVEL", show_half=False)

        print("  PER-SIDE WIN RATE:")
        for sd, nm in ((1, "LONG "), (-1, "SHORT")):
            row = []
            for lbl, pred in CELLS:
                sub = all_sigs if pred is None else [s for s in all_sigs if pred(s)]
                rs = [r for r in taken(A, sub, rr) if r["side"] == sd]
                k, _, w, net = stats(rs)
                row.append("%s n=%2d %5.1f%% %+6.1f%%" % (lbl.split()[0], k, w, net))
            print("    %s: %s" % (nm, "  |  ".join(row)))
        print()


if __name__ == "__main__":
    main()
