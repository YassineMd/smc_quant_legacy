"""15mReasy — FINAL config stats (15m ONLY).

  LONG  = bullish  + A<=-0.75 + skew>0 + prev candle bullish
  SHORT = bearish  + A<=-0.75 + skew<0 + prev candle bearish + mov_mag<=10   (mov_mag cut on SHORT ONLY)
  EXIT  = SL 0.1% beyond entry extreme; TP = RR x SL  (RR 1:1.0 and 1:1.5).

Full stats: per side + combined, CHAIN (tradeable, longs/shorts interleaved one-position-at-a-time) and
CONTROLLED (every signal, n constant), win/TP/SL counts, gross & net per trade, fee-adjusted BE*, PF, maxDD,
split-half, and the $200k/10%-margin/x10 account sim.

Run: python study/r15easy_final.py
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

    sigs = []
    for i in range(max(first, 1), len(A) - 1):
        s = dirn[i]
        if s == 0 or Aval[i] is None or Aval[i] > R_EASY or not skew_ok(i, s) or dirn[i - 1] != s:
            continue
        if s < 0 and mov_mag(A[i]) > MM_MAX:           # mov_mag<=10 on SHORT only
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


def stat(rows, rr, label):
    n = len(rows)
    if n == 0:
        print("  %-8s n=0" % label); return
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
    print("  %-8s n=%3d  W/L %3d/%-3d  win %5.1f%% (BE* %.1f%%)  net %+6.1f%%  gross/tr %+.4f%%  net/tr %+.4f%%  PF %4.2f  DD %4.1f%%  $%s"
          % (label, n, w, n - w, 100.0 * w / n, be, tot, g.mean() * 100, nt.mean() * 100, pf, dd, f"{bal:,.0f}"))


def main():
    A, first, _ = build("15m")
    sigs = prep(A, first)
    nl = sum(1 for s in sigs if s["side"] > 0)
    print("=" * 118)
    print("15mReasy FINAL  |  LONG: A<=-0.75+skew+prev-dir   SHORT: +mov_mag<=10   |  SL 0.1%%, TP 1:1 / 1:1.5")
    print("=" * 118)
    print("  signals: %d  (%dL / %dS)\n" % (len(sigs), nl, len(sigs) - nl))
    for rr in RRS:
        for chain, vl in ((True, "CHAIN (tradeable, one position at a time)"),
                          (False, "CONTROLLED (every signal, n constant)")):
            rows = run(A, sigs, rr, chain)
            print("RR 1:%.1f  %s" % (rr, vl))
            stat(rows, rr, "ALL")
            stat([r for r in rows if r["side"] > 0], rr, "LONG")
            stat([r for r in rows if r["side"] < 0], rr, "SHORT")
            if chain and len(rows) >= 4:
                m = len(rows) // 2
                stat(rows[:m], rr, "  H1")
                stat(rows[m:], rr, "  H2")
            print()
    print("CAVEAT: 15m, one ~34-day regime, forward n=0, small n per side, deep filter search (multiplicity).")


if __name__ == "__main__":
    main()
