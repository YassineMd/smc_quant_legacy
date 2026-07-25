"""15mReasy — ONE-TRADE-AT-A-TIME stats (15m ONLY), matching the live terminal detector.

RULE  : a single position at a time. Signals (L or S) that print while a prior trade is still open (not yet at
        TP or SL) are SKIPPED. A trade ends at TP or SL; the NEXT eligible signal is the first whose bucket index
        is strictly after the prior trade's EXIT bar. Longs and shorts share the one slot (a live long blocks a
        short signal and vice-versa) — the realistic single-account rule.
CONFIG: LONG = bullish + A<=-0.75 + skew>0 + prev bullish ; SHORT = mirror. (No mov_mag — the terminal spec.)
EXIT  : SL 0.1% beyond entry extreme; TP = RR x SL (RR 1:1.0 and 1:1.5).

LONG / SHORT rows are subsets of the SAME interleaved chain, so their n sum to ALL. Account = $200k, 10% margin
x10 lev, compounding, one position. BE* = fee-adjusted break-even from the chain's own median stop.

Run: python study/r15easy_1trade.py
"""
from __future__ import annotations
import os, sys
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

    sigs = []
    for i in range(max(first, 1), len(A) - 1):
        s = dirn[i]
        if s == 0 or Aval[i] is None or Aval[i] > R_EASY or not skew_ok(i, s) or dirn[i - 1] != s:
            continue
        sigs.append(dict(i=i, side=s, t=float(A[i].get("start_time", 0))))
    return sigs


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


def chain(A, sigs, rr):
    """One position at a time: skip any signal on/before the prior trade's exit bar."""
    last = -1; out = []
    for sg in sigs:
        if sg["i"] <= last:
            continue                                   # a prior trade is still in play -> DO NOT take
        r = sim(A, sg["i"], sg["side"], rr)
        if r is None:
            continue
        out.append(dict(side=sg["side"], out=r[0], gross=r[1], net=r[1] - FEE, slf=r[3])); last = r[2]
    return out


def stat(rows, rr, label):
    n = len(rows)
    if n == 0:
        print("  %-10s n=0" % label); return
    tp = sum(1 for r in rows if r["out"] == "TP"); sl = sum(1 for r in rows if r["out"] == "SL")
    w = sum(1 for r in rows if r["net"] > 0)
    g = np.array([r["gross"] for r in rows]); nt = np.array([r["net"] for r in rows])
    tot = (np.prod(1 + nt) - 1) * 100
    gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum()
    pf = (gg / ll) if ll > 0 else float("inf")
    path = np.cumprod(1 + nt); peak = np.maximum.accumulate(path)
    dd = float(np.max((peak - path) / peak)) * 100
    s = float(np.median([r["slf"] for r in rows]))
    be = (FEE + s) / (s * (1 + rr)) * 100
    bal = S.BAL0
    for r in rows:
        bal += S.POS_FRAC * bal * S.LEV * r["net"]
    print("  %-10s n=%3d  W/L %3d/%-3d (TP %3d, SL %3d)  win %5.1f%% (BE* %.1f%%)  net %+6.1f%%  gross/tr %+.4f%%  net/tr %+.4f%%  PF %4.2f  DD %4.1f%%  $%s"
          % (label, n, w, n - w, tp, sl, 100.0 * w / n, be, tot, g.mean() * 100, nt.mean() * 100, pf, dd, f"{bal:,.0f}"))


def main():
    A, first, _ = build("15m")
    sigs = prep(A, first)
    nl = sum(1 for s in sigs if s["side"] > 0)
    print("=" * 130)
    print("15mReasy ONE-TRADE-AT-A-TIME  |  A<=-0.75 + skew + prev-same-dir  |  SL 0.1%%, TP 1:1 / 1:1.5  (15m)")
    print("=" * 130)
    print("  raw signals: %d (%dL/%dS)  — many are skipped because a prior trade is still open\n" % (len(sigs), nl, len(sigs) - nl))
    for rr in RRS:
        rows = chain(A, sigs, rr)
        print("RR 1:%.1f" % rr)
        stat(rows, rr, "ALL")
        stat([r for r in rows if r["side"] > 0], rr, "LONG")
        stat([r for r in rows if r["side"] < 0], rr, "SHORT")
        if len(rows) >= 4:
            m = len(rows) // 2
            stat(rows[:m], rr, "  H1")
            stat(rows[m:], rr, "  H2")
        print("  (%d of %d signals actually TAKEN — the rest fell inside an open trade)\n" % (len(rows), len(sigs)))
    print("CAVEAT: 15m, one ~34-day regime, forward n=0. Fee (0.08%%) is the binding constraint at these small stops.")


if __name__ == "__main__":
    main()
