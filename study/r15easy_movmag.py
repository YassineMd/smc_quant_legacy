"""15mReasy + MOV_MAG <= 10 (15m ONLY).

ENTRY : candle i, when  A(i) <= -0.75 (R-easy)  AND  skew(i) agrees  AND  candle i-1 same direction
        AND  mov_mag(i) <= 10   (NEW).
STOP  : 0.1% beyond candle i's extreme.  TP = RR x SL  (RR 1:1.0 and 1:1.5). Plain brackets.

mov_mag = ((close*100/ref - 100)^2)*100, ref = low (up candle) / high (down) — the terminal's squared % move
from the REVERSED extreme. mov_mag <= 10  <=>  |close/ref - 1| <= ~0.316%, i.e. the close sits within ~0.32%
of the candle's low (up) / high (down): a SMALL / weak directional candle (little travel from the extreme).

Baseline = the prev-dir config WITHOUT the mov_mag cut, restricted to nothing (same population), so the marginal
effect of mov_mag<=10 is visible. Also: disjoint mov_mag bands, so it's clear whether 10 is a cliff or a slope.
Views: CHAIN (tradeable) + CONTROLLED (n constant). Gross reported (fees bind); BE* = fee-adjusted break-even.

Run: python study/r15easy_movmag.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from app import absorption as ABS
from study.mm_skew_v11_tf import build

FEE = 0.0008
R_EASY = -0.75
SL_BUF = 0.001
MM_MAX = 10.0
RRS = (1.0, 1.5)


def mov_mag(b):
    o, c, h, l = b["o"], b["c"], b["h"], b["l"]
    ref = l if c > o else (h if c < o else o)
    return ((((c * 100.0) / ref) - 100.0) ** 2) * 100.0 if ref > 0 else 0.0


def prep(A, first):
    Aval = [None] * len(A); dirn = [0] * len(A)
    for i in range(len(A)):
        try:
            Aval[i] = ABS.absorption(A, i)[0]
        except Exception:
            Aval[i] = None
        dirn[i] = 1 if A[i]["up"] else (-1 if A[i]["dn"] else 0)

    def skew_ok(i, s):
        sk = A[i].get("sk")
        return sk is not None and ((sk > 0) if s > 0 else (sk < 0))

    base = []
    for i in range(max(first, 1), len(A) - 1):
        s = dirn[i]
        if s == 0 or Aval[i] is None or Aval[i] > R_EASY or not skew_ok(i, s) or dirn[i - 1] != s:
            continue
        base.append(dict(i=i, side=s, t=float(A[i].get("start_time", 0)), mm=mov_mag(A[i])))
    return base


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


def run(A, sigs, rr, chain):
    last = -1; out = []
    for sg in sigs:
        if chain and sg["i"] <= last:
            continue
        r = sim(A, sg["i"], sg["side"], rr)
        if r is None:
            continue
        out.append(dict(side=sg["side"], out=r[0], gross=r[1], net=r[1] - FEE, slf=r[3])); last = r[2]
    return out


def line(rows, rr, lbl):
    n = len(rows)
    if n == 0:
        print("      %-30s n=0" % lbl); return
    w = sum(1 for r in rows if r["net"] > 0)
    g = np.array([r["gross"] for r in rows]); nt = np.array([r["net"] for r in rows])
    s = float(np.median([r["slf"] for r in rows]))
    be = (FEE + s) / (s * (1 + rr)) * 100
    print("      %-30s n=%4d  win %5.1f%%  net %+7.1f%%  gross/tr %+.4f%%  net/tr %+.4f%%  BE* %.1f%%"
          % (lbl, n, 100.0 * w / n, (np.prod(1 + nt) - 1) * 100, g.mean() * 100, nt.mean() * 100, be))


def table(rows, rr, label):
    print("    %s" % label)
    for sd, nm in ((None, "ALL"), (1, "LONG"), (-1, "SHORT")):
        line([r for r in rows if sd is None or r["side"] == sd], rr, nm)


def main():
    A, first, _ = build("15m")
    base = prep(A, first)
    keep = [s for s in base if s["mm"] <= MM_MAX]
    bl = sum(1 for s in base if s["side"] > 0); kl = sum(1 for s in keep if s["side"] > 0)
    print("=" * 108)
    print("15mReasy + mov_mag<=%.0f  (15m)  |  A<=-0.75 + skew + prev-same-dir + mov_mag<=%.0f  |  plain TP"
          % (MM_MAX, MM_MAX))
    print("=" * 108)
    print("  prev-dir config          : %4d (%dL/%dS)" % (len(base), bl, len(base) - bl))
    print("  + mov_mag <= %.0f          : %4d (%dL/%dS)   = %.0f%%\n"
          % (MM_MAX, len(keep), kl, len(keep) - kl, 100.0 * len(keep) / max(1, len(base))))

    for rr in RRS:
        print("-" * 108)
        print("RR 1:%.1f" % rr)
        print("-" * 108)
        for chain, vl in ((True, "CHAIN (tradeable)"), (False, "CONTROLLED (n constant)")):
            print("  %s" % vl)
            table(run(A, base, rr, chain), rr, "baseline (no mov_mag cut):")
            table(run(A, keep, rr, chain), rr, ">>> + mov_mag <= 10:")
        print("  DISJOINT mov_mag bands (controlled — is 10 a cliff or a slope?):")
        for lbl, lo, hi in (("mm <= 5", 0, 5), ("5 < mm <= 10", 5, 10), ("10 < mm <= 25", 10, 25), ("mm > 25", 25, 1e9)):
            sub = [s for s in base if lo < s["mm"] <= hi] if lo > 0 else [s for s in base if s["mm"] <= hi]
            line(run(A, sub, rr, False), rr, lbl)
        print()


if __name__ == "__main__":
    main()
