"""15mReasy (15m ONLY) — plain brackets + R-easy + skew, then ADD r2<0.

EXIT   : SL 0.1% beyond the entry bucket's extreme; TP = RR x SL (RR 1:1.0 and 1:1.5). NO early exit.
FILTERS: (1) A <= -0.75 (R-easy)   (2) skew>0 long / skew<0 short
ADD    : (3) A_h2 < 0  — the 2nd leg of absorption_halves (50%-volume split), oriented POSITIVE = absorbed, so
         <0 = the second half moved easily. NEEDS price_h1 (+ >=20 baselined priors) -> coverage is reported and
         the filter is FAIL-CLOSED (uncomputable = rejected), matching the live v1.1 convention.

Because r2 is only computable on part of the sample, the baseline is ALSO shown restricted to the r2-judgeable
subset — otherwise "filter works" is confounded with "the judgeable subset differs".

Views: CHAIN (tradeable, one at a time) and CONTROLLED (every signal, n constant). Gross is reported alongside
net because the 0.08% fee is the binding constraint on 15m, so gross says whether the SIGNAL improved.

Run: python study/r15easy_r2.py
"""
from __future__ import annotations
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from app import absorption as ABS
import study.mm_skew_strategy as S
from study.mm_skew_v11_tf import build

FEE = 0.0008
R_EASY = -0.75
SL_BUF = 0.001
RRS = (1.0, 1.5)


def prep(A, first):
    sigs = []
    for i in range(max(first, 1), len(A) - 1):
        b = A[i]
        s = 1 if b["up"] else (-1 if b["dn"] else 0)
        if s == 0:
            continue
        try:
            a = ABS.absorption(A, i)[0]
        except Exception:
            a = None
        if a is None or a > R_EASY:
            continue
        sk = b.get("sk")
        if sk is None or not ((sk > 0) if s > 0 else (sk < 0)):
            continue
        try:
            a2 = ABS.absorption_halves(A, i)[1]
        except Exception:
            a2 = None
        sigs.append(dict(i=i, side=s, t=float(b.get("start_time", 0)), A2=a2))
    return sigs


def sim(A, i, side, rr):
    e = A[i]["c"]
    sl = A[i]["l"] * (1 - SL_BUF) if side > 0 else A[i]["h"] * (1 + SL_BUF)
    sld = (e - sl) if side > 0 else (sl - e)
    if sld <= 0:
        return None
    slf = sld / e
    tp = e + rr * sld * side
    for j in range(i + 1, len(A)):
        hi, lo = A[j]["h"], A[j]["l"]
        if (lo <= sl) if side > 0 else (hi >= sl):
            return ("SL", -slf, j, slf)
        if (hi >= tp) if side > 0 else (lo <= tp):
            return ("TP", rr * slf, j, slf)
    return ("OPEN", (A[-1]["c"] - e) / e * side, len(A) - 1, slf)


def run(A, sigs, rr, chain, pred=None):
    last = -1; out = []
    for sg in sigs:
        if chain and sg["i"] <= last:
            continue
        if pred is not None and not pred(sg):
            continue
        r = sim(A, sg["i"], sg["side"], rr)
        if r is None:
            continue
        out.append(dict(side=sg["side"], out=r[0], gross=r[1], net=r[1] - FEE, slf=r[3]))
        last = r[2]
    return out


def table(rows, rr, label):
    print("    %s" % label)
    print("      %-7s %5s %5s %5s | %7s %8s %10s %10s %8s"
          % ("side", "n", "TP", "SL", "win%", "net%", "gross/tr", "net/tr", "BE*"))
    for sd, nm in ((None, "ALL"), (1, "LONG"), (-1, "SHORT")):
        rs = [r for r in rows if sd is None or r["side"] == sd]
        if not rs:
            continue
        n = len(rs); tp = sum(1 for r in rs if r["out"] == "TP"); sl = sum(1 for r in rs if r["out"] == "SL")
        w = sum(1 for r in rs if r["net"] > 0)
        g = np.array([r["gross"] for r in rs]); nt = np.array([r["net"] for r in rs])
        s = float(np.median([r["slf"] for r in rs]))
        be = (FEE + s) / (s * (1 + rr)) * 100
        print("      %-7s %5d %5d %5d | %6.1f%% %+7.1f%% %+9.4f%% %+9.4f%% %7.1f%%"
              % (nm, n, tp, sl, 100.0 * w / n, (np.prod(1 + nt) - 1) * 100, g.mean() * 100, nt.mean() * 100, be))


def main():
    A, first, _ = build("15m")
    sigs = prep(A, first)
    jud = [s for s in sigs if s["A2"] is not None]
    keep = [s for s in jud if s["A2"] < 0]
    nl = sum(1 for s in sigs if s["side"] > 0)
    print("=" * 106)
    print("15mReasy (15m)  |  dir + A<=-0.75 + skew  |  SL 0.1%%, plain TP  |  then + r2<0")
    print("=" * 106)
    print("  R-easy + skew signals : %4d (%dL/%dS)" % (len(sigs), nl, len(sigs) - nl))
    print("  r2 COMPUTABLE on      : %4d (%.0f%%)   <- needs price_h1 + >=20 baselined priors" %
          (len(jud), 100.0 * len(jud) / max(1, len(sigs))))
    kl = sum(1 for s in keep if s["side"] > 0)
    print("  ... of those, r2<0    : %4d (%dL/%dS)  = %.0f%% of judgeable\n"
          % (len(keep), kl, len(keep) - kl, 100.0 * len(keep) / max(1, len(jud))))

    for rr in RRS:
        print("-" * 106)
        print("RR 1:%.1f    (BE* = fee-adjusted break-even from that row's own median stop)" % rr)
        print("-" * 106)
        for chain, vl in ((True, "CHAIN (tradeable)"), (False, "CONTROLLED (n constant)")):
            print("  %s" % vl)
            table(run(A, sigs, rr, chain), rr, "baseline  R-easy + skew (all signals):")
            table(run(A, jud, rr, chain), rr, "baseline  restricted to r2-JUDGEABLE (like-for-like):")
            table(run(A, keep, rr, chain), rr, ">>> + r2 < 0:")
        print()


if __name__ == "__main__":
    main()
