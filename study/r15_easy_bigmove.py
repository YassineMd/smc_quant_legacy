"""15m — EASY-only (A <= -1.5, NOT the light band) + mov_mag >= 20  (a big-move / momentum variant).

ENTRY  : direction from the candle (bull->long, bear->short) AND skew agrees (skew>0 long / skew<0 short).
ADD    : A <= -1.5  (the module's "EASY" label — NOT "light" which is -1.5 < A <= -0.75)  AND  mov_mag >= 20.
EXIT   : SL 0.1% beyond the entry candle's extreme; TP = RR x SL (RR 1:1.0 and 1:1.5). Plain brackets.

Absorption labels (app/absorption.label): ABSORBED>=+1.5 | heavy>=+0.75 | light<=-0.75 | EASY<=-1.5.
"EASY only (not light)" = A <= -1.5. mov_mag = ((close*100/ref-100)^2)*100, ref=low(up)/high(down).

This is the OPPOSITE flavour to the small-candle short edge (mov_mag<=10): a STRONG easy move on a BIG candle.
Reported: the 2x2 of {light vs EASY} x {mov_mag<20 vs >=20} so the specific cell is isolated, plus per side and
the fee-adjusted break-even (bigger candles => bigger stops => lower BE, so the fee math is friendlier here).

Run: python study/r15_easy_bigmove.py
"""
from __future__ import annotations
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from app import absorption as ABS
from study.mm_skew_v11_tf import build

FEE = 0.0008
SL_BUF = 0.001
RRS = (1.0, 1.5)


def mov_mag(b):
    o, c, h, l = b["o"], b["c"], b["h"], b["l"]
    ref = l if c > o else (h if c < o else o)
    return ((((c * 100.0) / ref) - 100.0) ** 2) * 100.0 if ref > 0 else 0.0


def scan(A, first):
    Aval = [None] * len(A)
    for i in range(len(A)):
        try:
            Aval[i] = ABS.absorption(A, i)[0]
        except Exception:
            Aval[i] = None

    def skew_ok(i, s):
        sk = A[i].get("sk")
        return sk is not None and ((sk > 0) if s > 0 else (sk < 0))

    out = []
    for i in range(max(first, 1), len(A) - 1):
        s = 1 if A[i]["up"] else (-1 if A[i]["dn"] else 0)
        if s == 0 or not skew_ok(i, s) or Aval[i] is None:
            continue
        out.append(dict(i=i, side=s, t=float(A[i].get("start_time", 0)), A=Aval[i], mm=mov_mag(A[i])))
    return out


def sim(A, i, side, rr):
    e = A[i]["c"]
    sl = A[i]["l"] * (1 - SL_BUF) if side > 0 else A[i]["h"] * (1 + SL_BUF)
    sld = (e - sl) if side > 0 else (sl - e)
    if sld <= 0:
        return None
    slf = sld / e; tp = e + rr * sld * side
    for j in range(i + 1, len(A)):
        hi, lo = A[j]["h"], A[j]["l"]
        if (lo <= sl) if side > 0 else (hi >= sl):
            return ("SL", -slf, j, slf)
        if (hi >= tp) if side > 0 else (lo <= tp):
            return ("TP", rr * slf, j, slf)
    return ("OPEN", (A[-1]["c"] - e) / e * side, len(A) - 1, slf)


def cell(A, sigs, rr, controlled=True):
    last = -1; rows = []
    for sg in sigs:
        if not controlled and sg["i"] <= last:
            continue
        r = sim(A, sg["i"], sg["side"], rr)
        if r is None:
            continue
        rows.append(dict(side=sg["side"], win=(r[0] == "TP"), gross=r[1], net=r[1] - FEE, slf=r[3])); last = r[2]
    return rows


def show(rows, rr, lbl):
    n = len(rows)
    if n == 0:
        print("    %-26s n=0" % lbl); return
    w = sum(1 for r in rows if r["net"] > 0)
    g = np.array([r["gross"] for r in rows]); nt = np.array([r["net"] for r in rows])
    s = float(np.median([r["slf"] for r in rows]))
    be = (FEE + s) / (s * (1 + rr)) * 100
    print("    %-26s n=%3d  win %5.1f%%  gross/tr %+.4f%%  net/tr %+.4f%%  stop %.3f%%  BE* %.1f%%  gap %+.1f"
          % (lbl, n, 100.0 * w / n, g.mean() * 100, nt.mean() * 100, s * 100, be, 100.0 * w / n - be))


EASY = lambda s: s["A"] <= -1.5
LIGHT = lambda s: -1.5 < s["A"] <= -0.75
BIG = lambda s: s["mm"] >= 20.0


def main():
    A, first, _ = build("15m")
    sigs = scan(A, first)
    ne = sum(1 for s in sigs if EASY(s)); nl = sum(1 for s in sigs if LIGHT(s))
    prop = [s for s in sigs if EASY(s) and BIG(s)]
    pl = sum(1 for s in prop if s["side"] > 0)
    print("=" * 108)
    print("15m EASY-only (A<=-1.5) + mov_mag>=20  |  base = direction + skew agrees  |  SL 0.1%%, plain TP")
    print("=" * 108)
    print("  skew-agreeing directional signals: %d  (EASY A<=-1.5: %d | light -1.5..-0.75: %d)" % (len(sigs), ne, nl))
    print("  >>> EASY AND mov_mag>=20 (PROPOSAL): %d  (%dL/%dS)\n" % (len(prop), pl, len(prop) - pl))

    for rr in RRS:
        print("-" * 108); print("RR 1:%.1f" % rr); print("-" * 108)
        print("  2x2  {light | EASY}  x  {mov_mag<20 | >=20}  (controlled):")
        for lab, pred in (("light  & mm<20", lambda s: LIGHT(s) and not BIG(s)),
                          ("light  & mm>=20", lambda s: LIGHT(s) and BIG(s)),
                          ("EASY   & mm<20", lambda s: EASY(s) and not BIG(s)),
                          ("EASY   & mm>=20  <<<", lambda s: EASY(s) and BIG(s))):
            show(cell(A, [s for s in sigs if pred(s)], rr), rr, lab)
        print("  PROPOSAL per side (EASY & mm>=20):")
        for sd, nm in ((1, "LONG"), (-1, "SHORT")):
            show(cell(A, [s for s in prop if s["side"] == sd], rr), rr, "  " + nm + " controlled")
            show(cell(A, [s for s in prop if s["side"] == sd], rr, controlled=False), rr, "  " + nm + " chain")
        print()


if __name__ == "__main__":
    main()
