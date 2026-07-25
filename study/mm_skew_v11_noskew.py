"""MMXSKEW v1.1 (1h) — ABLATION of the SKEW gate (skew>0 long / skew<0 short).

Current frozen v1.1:  close>open & **skew>0** & spread>=+35 & 0<delta<=15 & momentum & A_h2<0  (SHORT = mirror).
This drops the skew condition and asks whether it earns its keep — the same treatment the spread gate got
(study/mm_skew_v11_nospread.py), where the answer was an emphatic YES.

skew = app.footprint_panel.profile_skewness(levels): the volume profile's skew, >0 = mass leaning HIGH.
DIRECTIONAL SKEW `dsk = skew * side` normalises both sides, so the frozen gate is exactly `dsk > 0`.

Reported: (1) with vs without; (2) DISJOINT BANDS of dsk (a cumulative ladder is nested and fakes a gradient);
(3) WITHIN-CHAIN partition of the no-skew chain by dsk>0 + Fisher + split-half; (4) per-side win rates.
Everything on taken() non-overlap; spread + delta + momentum + A_h2<0 held FIXED throughout.

Run: python study/mm_skew_v11_noskew.py
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


def sigs(A, first, use_skew: bool):
    """v1.1 with the SKEW gate switchable; spread/delta/momentum/A_h2 always applied (as frozen)."""
    out = []; noskew_field = 0
    for i in range(max(first, 1), len(A) - 1):
        b = A[i]
        sk = b.get("sk")
        if use_skew and sk is None:
            continue
        if b["up"] and b["spread"] >= 35 and (not use_skew or sk > 0):
            s = 1
        elif b["dn"] and b["spread"] <= -35 and (not use_skew or sk < 0):
            s = -1
        else:
            continue
        d = b["delta"]
        if not ((0.0 < d <= 15.0) if s > 0 else (d < 0.0)):
            continue
        if not ((b["spread"] - A[i - 1]["spread"]) * s > 0.0):
            continue
        try:
            a2 = ABS.absorption_halves(A, i)[1]
        except Exception:
            a2 = None
        if a2 is None or a2 >= 0.0:
            continue
        if sk is None:
            noskew_field += 1
        out.append(dict(i=i, side=s, t=float(b.get("start_time", 0)),
                        dsk=(sk * s) if sk is not None else None))
    return out, noskew_field


def fisher(a, b, c, d):
    n = a + b + c + d
    if n == 0 or (a + b) == 0 or (c + d) == 0:
        return float("nan")
    r1, c1 = a + b, a + c
    hp = lambda x: (math.comb(c1, x) * math.comb(n - c1, r1 - x)) / math.comb(n, r1)
    po = hp(a)
    return min(1.0, sum(hp(x) for x in range(max(0, r1 - (n - c1)), min(r1, c1) + 1) if hp(x) <= po + 1e-12))


def partition(A, sigs_, rr, be, pred, lbl):
    last = -1; ps = []; fs = []
    for sg in sigs_:
        if sg["i"] <= last:
            continue
        res = RR.simulate_rr(A, sg["i"], sg["side"], rr, "sl")
        if res is None:
            continue
        row = dict(side=sg["side"], win=(res[0] == "TP"), net=res[1] - FEE, t=sg["t"])
        (ps if pred(sg) else fs).append(row); last = res[2]
    np_, wp = len(ps), sum(r["win"] for r in ps)
    nf, wf = len(fs), sum(r["win"] for r in fs)
    print("    [%s] within-chain partition (n=%d = %d pass + %d fail):" % (lbl, np_ + nf, np_, nf))
    block(ps, be, "      pass"); block(fs, be, "      fail")
    print("      Fisher pass-vs-fail: p=%.3f" % fisher(wp, np_ - wp, wf, nf - wf))
    if np_ >= 6:
        mid = np_ // 2
        block(ps[:mid], be, "      pass H1"); block(ps[mid:], be, "      pass H2")


def main():
    A, first, _ = build("1h")
    with_sk, _ = sigs(A, first, True)
    no_sk, nofield = sigs(A, first, False)
    wl = sum(1 for s in with_sk if s["side"] > 0); nl = sum(1 for s in no_sk if s["side"] > 0)
    print("=" * 108)
    print("MMXSKEW v1.1 (1h) — SKEW GATE ABLATION   [spread + delta + momentum + A_h2<0 held fixed]")
    print("=" * 108)
    print("  WITH skew (FROZEN) : %3d signals (%dL/%dS)" % (len(with_sk), wl, len(with_sk) - wl))
    print("  WITHOUT skew       : %3d signals (%dL/%dS)   [%d of them have NO skew field at all]"
          % (len(no_sk), nl, len(no_sk) - nl, nofield))
    print("  -> the skew gate keeps %.0f%% of the no-skew population\n"
          % (100.0 * len(with_sk) / max(1, len(no_sk))))

    for rr in RRS:
        be = 1.0 / (1 + rr)
        print("-" * 108)
        print("RR 1:%.1f   (break-even %.0f%%)" % (rr, be * 100))
        print("-" * 108)
        print("  1) WITH vs WITHOUT:")
        block(taken(A, with_sk, rr), be, "WITH skew")
        block(taken(A, no_sk, rr), be, "WITHOUT skew")

        print("  2) DISJOINT BANDS of directional skew (dsk = skew * side):")
        banded = [s for s in no_sk if s["dsk"] is not None]
        vals = sorted(s["dsk"] for s in banded)
        qs = [vals[int(q * (len(vals) - 1))] for q in (0.25, 0.5, 0.75)] if vals else [0, 0, 0]
        for lbl, pred in (("dsk < 0  (gate REJECTS)", lambda v: v < 0),
                          ("0 <= dsk < %.2f" % qs[1], lambda v: 0 <= v < qs[1]),
                          ("dsk >= %.2f" % qs[1], lambda v: v >= qs[1])):
            print(line_for(A, [s for s in banded if pred(s["dsk"])], rr, be, lbl))

        print("  3) HONEST TEST — one chain (no skew gate), partitioned by dsk>0:")
        partition(A, banded, rr, be, lambda sg: sg["dsk"] > 0, "dsk > 0")

        print("  4) PER-SIDE:")
        for sd, nm in ((1, "LONG "), (-1, "SHORT")):
            a = [r for r in taken(A, with_sk, rr) if r["side"] == sd]
            b_ = [r for r in taken(A, no_sk, rr) if r["side"] == sd]
            block(a, be, "    %s WITH" % nm); block(b_, be, "    %s WITHOUT" % nm)
        print()


def line_for(A, sub, rr, be, lbl):
    T = taken(A, sub, rr)
    if not T:
        return "    %-26s n=0" % lbl
    w = sum(1 for r in T if r["win"]); net = np.array([r["net"] for r in T])
    tot = (np.prod(1 + net) - 1) * 100
    return "    %-26s n=%3d  win %5.1f%%  net %+7.1f%%  P=%.3f" % (
        lbl, len(T), 100.0 * w / len(T), tot, binom_ge(w, len(T), be))


if __name__ == "__main__":
    main()
