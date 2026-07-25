"""Frozen 15mReasy config on ANY timeframe — parity-checked against the 15m baseline.

Gate (mirrors app/r15easy_detect.py): direction + A<=-0.75 (R-easy) + |skew|>=0.4 + prev candle same direction.
SL 0.1% beyond the entry extreme; TP = RR x SL (RR 1:1.0 and 1:1.5). One-at-a-time (chain) + controlled.

`python study/r15easy_tf.py 15m` PARITY-CHECKS against the frozen numbers (103 signals; TP1:1.0 ~66% win).
Only trust the 5m output if 15m reproduces. Maturity floor scales: target_vol[tf] floor = 100000*tf_secs/3600.
Reports the fee-adjusted break-even per row (smaller tf => smaller stop => HIGHER BE, so the 5m bar is brutal).

Run: python study/r15easy_tf.py 5m
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
SKEW_MIN = 0.4
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
    out = []
    for i in range(max(first, 1), len(A) - 1):
        s = dirn[i]
        if s == 0 or Aval[i] is None or Aval[i] > R_EASY or dirn[i - 1] != s:
            continue
        sk = A[i].get("sk")
        if sk is None or (sk < SKEW_MIN if s > 0 else sk > -SKEW_MIN):
            continue
        out.append(dict(i=i, side=s, t=float(A[i].get("start_time", 0))))
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


def run(A, sigs, rr, chain):
    last = -1; out = []
    for sg in sigs:
        if chain and sg["i"] <= last:
            continue
        r = sim(A, sg["i"], sg["side"], rr)
        if r is None:
            continue
        out.append(dict(side=sg["side"], win=(r[0] == "TP"), net=r[1] - FEE, gross=r[1], slf=r[3])); last = r[2]
    return out


def stat(rows, rr, label):
    n = len(rows)
    if n == 0:
        print("  %-8s n=0" % label); return
    w = sum(1 for r in rows if r["net"] > 0)
    g = np.array([r["gross"] for r in rows]); nt = np.array([r["net"] for r in rows])
    tot = (np.prod(1 + nt) - 1) * 100
    gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum(); pf = (gg / ll) if ll > 0 else float("inf")
    s = float(np.median([r["slf"] for r in rows])); be = (FEE + s) / (s * (1 + rr)) * 100
    bal = S.BAL0
    for r in rows:
        bal += S.POS_FRAC * bal * S.LEV * r["net"]
    print("  %-8s n=%3d  win %5.1f%% (BE* %.1f%%)  net %+6.1f%%  gross/tr %+.4f%%  stop %.3f%%  PF %4.2f  $%s"
          % (label, n, 100.0 * w / n, be, tot, g.mean() * 100, s * 100, pf, f"{bal:,.0f}"))


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "5m"
    A, first, floor = build(tf)
    sigs = prep(A, first)
    nl = sum(1 for s in sigs if s["side"] > 0)
    span = (A[-1]["start_time"] - A[first]["start_time"]) / 86400.0 if first < len(A) else 0.0
    print("=" * 108)
    print("15mReasy FROZEN config on %s   |  A<=-0.75 + |skew|>=0.4 + prev-dir  |  SL 0.1%%, TP 1:1 / 1:1.5"
          % tf.upper())
    print("=" * 108)
    print("  %d mature %s buckets, %.1f days  |  signals: %d (%dL/%dS)  [maturity floor tv>=%.0f]\n"
          % (len(A) - first, tf, span, len(sigs), nl, len(sigs) - nl, floor))
    for rr in RRS:
        for chain, vl in ((True, "one-at-a-time"), (False, "controlled")):
            rows = run(A, sigs, rr, chain)
            print("RR 1:%.1f  (%s)" % (rr, vl))
            stat(rows, rr, "ALL")
            stat([r for r in rows if r["side"] > 0], rr, "LONG")
            stat([r for r in rows if r["side"] < 0], rr, "SHORT")
        print()
    if tf == "15m":
        n10 = len(run(A, sigs, 1.0, False))
        print("PARITY vs frozen: expect ~103 controlled signals, TP1:1.0 win ~66%%.  got n=%d" % n10)


if __name__ == "__main__":
    main()
