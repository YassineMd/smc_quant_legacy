"""MMXSKEW v1.1 — ABLATION of the eff-agg SPREAD gate (>=+35 long / <=-35 short).

Current frozen v1.1:  close>open & skew>0 & **spread>=+35** & 0<delta<=15 & A_h2<0   (SHORT = mirror, delta<0)
This drops the spread condition and asks whether it earns its keep.

DIRECTIONAL SPREAD (`dspr = spread * side`) normalises both sides: the frozen gate is exactly `dspr >= 35` for
long AND short, so the threshold can be swept/banded once instead of twice.

Reported:
  1. THRESHOLD SWEEP — the gate at dspr >= {-inf, 0, 15, 25, 35, 45}: does 35 sit on anything real?
  2. DISJOINT BANDS — dspr in (-inf,0) / [0,15) / [15,35) / [35,inf): a cumulative ladder is nested and fakes a
     gradient [disjoint-bands-not-cumulative-ladders], so the bands are what actually carry information.
  3. WITHIN-CHAIN partition of the no-spread chain by (dspr>=35), + Fisher, + split-half.
Everything on taken() non-overlap [canonical-taken-basis], delta + A_h2<0 held FIXED throughout.

Run: python study/mm_skew_v11_nospread.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import absorption as ABS
from study.mm_skew_v11_secondhalf import build, taken, binom_ge, fisher, stats
from study.mm_skew_v11_vertdelta import partition, line

RRS = (1.0, 1.5)


def sigs(A, first, spread_min=None):
    """Frozen v1.1 with the SPREAD gate parameterised. spread_min=None -> no spread condition at all.
    delta freeze + A_h2<0 (fail-closed) always applied, exactly like the live gate."""
    out = []
    for i in range(first, len(A) - 1):
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
        dspr = b["spread"] * s                       # directional: frozen gate == dspr >= 35 on both sides
        if spread_min is not None and dspr < spread_min:
            continue
        try:
            a2 = ABS.absorption_halves(A, i)[1]
        except Exception:
            a2 = None
        if a2 is None or a2 >= 0.0:                  # FAIL-CLOSED, as live
            continue
        out.append(dict(i=i, side=s, t=float(b.get("start_time", 0)), b=b, dspr=dspr))
    return out


def main():
    A, first = build()
    full = sigs(A, first, None)                      # NO spread gate
    froz = sigs(A, first, 35.0)                      # current frozen
    nl = sum(1 for s in full if s["side"] > 0); ns = len(full) - nl
    fl = sum(1 for s in froz if s["side"] > 0); fs = len(froz) - fl
    print("v1.1 (delta freeze + A_h2<0 held fixed):")
    print("  WITHOUT spread gate : %3d signals (%dL/%dS)" % (len(full), nl, ns))
    print("  WITH spread>=35     : %3d signals (%dL/%dS)   <- the current frozen gate keeps %.0f%%\n"
          % (len(froz), fl, fs, 100.0 * len(froz) / max(1, len(full))))

    for rr in RRS:
        be = 1.0 / (1 + rr)
        print("=" * 100)
        print("RR 1:%.1f    break-even win = %.0f%%" % (rr, be * 100))
        print("=" * 100)

        print("  1) THRESHOLD SWEEP (cumulative gates — nested, read with care):")
        for thr in (None, 0.0, 15.0, 25.0, 35.0, 45.0):
            ch = taken(A, sigs(A, first, thr), rr)
            lbl = "no spread gate" if thr is None else ("dspr >= %g" % thr)
            print(line(lbl + ("   <- FROZEN" if thr == 35.0 else ""), ch, be))

        print("  2) DISJOINT BANDS of directional spread (the informative view):")
        bands = [("dspr < 0", lambda v: v < 0), ("0 <= dspr < 15", lambda v: 0 <= v < 15),
                 ("15 <= dspr < 35", lambda v: 15 <= v < 35), ("dspr >= 35  <- FROZEN", lambda v: v >= 35)]
        for lbl, pred in bands:
            sub = [s for s in full if pred(s["dspr"])]
            ch = taken(A, sub, rr)
            print(line(lbl, ch, be))

        print("  3) HONEST TEST — one chain (no spread gate), partitioned by dspr>=35:")
        partition(A, full, rr, be, lambda sg: sg["dspr"] >= 35.0, "dspr >= 35")

        print("  PER-SIDE WIN RATE:")
        for sd, nm in ((1, "LONG "), (-1, "SHORT")):
            a = [r for r in taken(A, full, rr) if r["side"] == sd]
            b_ = [r for r in taken(A, froz, rr) if r["side"] == sd]
            na, _, wa, neta = stats(a); nb, _, wb, netb = stats(b_)
            print("    %s: NO spread n=%2d win %5.1f%% net %+6.1f%%   |   spread>=35 n=%2d win %5.1f%% net %+6.1f%%"
                  % (nm, na, wa, neta, nb, wb, netb))
        print()


if __name__ == "__main__":
    main()
