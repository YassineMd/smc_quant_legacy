"""15mReasy (frozen) + PRIOR-2-DAY VALUE-AREA-TREND directional bias (15m ONLY, CAUSAL).

Base : direction + A<=-0.75 + |skew|>=0.4 + prev-same-dir. SL 0.1% beyond entry extreme; TP = RR x SL.
BIAS : trading on Day 3. Take the FULL-day 70% Volume-Profile value area of Day 1 (2 days ago) and Day 2 (yesterday):
         if VAH(D2) > VAH(D1) AND VAL(D2) > VAL(D1)  -> value area shifted UP   -> LONG only
         if VAH(D2) < VAH(D1) AND VAL(D2) < VAL(D1)  -> value area shifted DOWN -> SHORT only
         otherwise (mixed / one edge each way)        -> trade BOTH
       Days are UTC calendar days (midnight..23:59). CAUSAL: D1 and D2 are fully CLOSED before any D3 signal, so
       their full-day VA uses no future information. Days without 2 prior days available default to BOTH.

Value area from study.mm_skew_feature_matrix.dayfull (va_poc, 70%). Views: controlled (n constant) + one-at-a-time.

Run: python study/r15easy_vatrend.py
"""
from __future__ import annotations
import os, sys, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from app import absorption as ABS
import study.mm_skew_strategy as S
from study.mm_skew_feature_matrix import va_poc     # 70% value-area (val/vah/D) from a price->volume profile
from study.mm_skew_v11_tf import build              # tf-aware matrix (15m); NOT FM.build (that is 1h-hardcoded)
import study.mm_skew_rr_sweep as RR

FEE = 0.0008
R_EASY = -0.75
SKEW_MIN = 0.4
SL_BUF = 0.001
RRS = (1.0, 1.5)


def daily_va(A):
    """Per-UTC-day 70% value area (val/vah) aggregated from the 15m buckets' level profiles."""
    days = {}
    for b in A:
        d = dt.datetime.utcfromtimestamp(float(b.get("start_time", 0.0) or 0.0)).date()
        days.setdefault(d, []).append(b)
    out = {}
    for d, bs in days.items():
        prof = {}
        for b in bs:
            for pr, v in (b.get("levels") or {}).items():
                try:
                    p = float(pr)
                except (TypeError, ValueError):
                    continue
                prof[p] = prof.get(p, 0.0) + float(v.get("b", 0) or 0) + float(v.get("s", 0) or 0)
        va = va_poc(prof)
        if va:
            out[d] = va
    return out


def bias_for_day(d3, dayfull):
    """LONG-only / SHORT-only / BOTH from the value-area shift of the two prior UTC days."""
    va1 = dayfull.get(d3 - dt.timedelta(days=2))     # Day 1
    va2 = dayfull.get(d3 - dt.timedelta(days=1))     # Day 2
    if not va1 or not va2:
        return "both"
    if va2["vah"] > va1["vah"] and va2["val"] > va1["val"]:
        return "long"
    if va2["vah"] < va1["vah"] and va2["val"] < va1["val"]:
        return "short"
    return "both"


def prep(A, first, dayfull):
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
        d3 = dt.datetime.utcfromtimestamp(float(A[i]["start_time"])).date()
        bias = bias_for_day(d3, dayfull)
        allowed = (bias == "both") or (bias == "long" and s > 0) or (bias == "short" and s < 0)
        out.append(dict(i=i, side=s, t=float(A[i]["start_time"]), bias=bias, allowed=allowed))
    return out


def run(A, sigs, rr, chain, only_allowed=False):
    last = -1; out = []
    for sg in sigs:
        if chain and sg["i"] <= last:
            continue
        if only_allowed and not sg["allowed"]:
            continue
        r = RR.simulate_rr(A, sg["i"], sg["side"], rr, "sl")
        if r is None:
            continue
        out.append(dict(side=sg["side"], win=(r[0] == "TP"), net=r[1] - FEE, gross=r[1])); last = r[2]
    return out


def stat(rows, rr, label):
    n = len(rows)
    if n == 0:
        print("  %-14s n=0" % label); return
    w = sum(1 for r in rows if r["net"] > 0)
    g = np.mean([r["gross"] for r in rows]); nt = np.array([r["net"] for r in rows])
    tot = (np.prod(1 + nt) - 1) * 100
    gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum(); pf = (gg / ll) if ll > 0 else float("inf")
    be = 1.0 / (1 + rr)
    bal = S.BAL0
    for r in rows:
        bal += S.POS_FRAC * bal * S.LEV * r["net"]
    print("  %-14s n=%3d  win %5.1f%% (BE %.0f%%)  net %+6.1f%%  gross/tr %+.4f%%  PF %4.2f  $%s"
          % (label, n, 100.0 * w / n, be * 100, tot, g * 100, pf, f"{bal:,.0f}"))


def main():
    A, first, _floor = build("15m")
    dayfull = daily_va(A)
    sigs = prep(A, first, dayfull)
    # day-bias classification
    days = sorted({dt.datetime.utcfromtimestamp(float(A[s["i"]]["start_time"])).date() for s in sigs})
    dc = {"long": 0, "short": 0, "both": 0}
    for d in days:
        dc[bias_for_day(d, dayfull)] += 1
    nl = sum(1 for s in sigs if s["side"] > 0)
    print("=" * 108)
    print("15mReasy + prior-2-day VA-trend bias  (15m)  |  frozen base  |  signals %d (%dL/%dS)"
          % (len(sigs), nl, len(sigs) - nl))
    print("=" * 108)
    print("  trading days: %d  ->  LONG-bias %d | SHORT-bias %d | BOTH %d" % (len(days), dc["long"], dc["short"], dc["both"]))
    print("  signals allowed by the bias: %d of %d  (dropped %d counter-bias)\n"
          % (sum(1 for s in sigs if s["allowed"]), len(sigs), sum(1 for s in sigs if not s["allowed"])))
    for rr in RRS:
        for chain, vl in ((False, "controlled"), (True, "one-at-a-time")):
            print("RR 1:%.1f  (%s)" % (rr, vl))
            base = run(A, sigs, rr, chain, only_allowed=False)
            filt = run(A, sigs, rr, chain, only_allowed=True)
            stat(base, rr, "baseline ALL")
            stat(filt, rr, ">>>bias ALL")
            stat([r for r in filt if r["side"] > 0], rr, ">>>bias LONG")
            stat([r for r in filt if r["side"] < 0], rr, ">>>bias SHORT")
            print()
    print("CAVEAT: causal (D1/D2 closed before D3). 15m, one 34-day regime, forward n=0. Bias thins the sample.")


if __name__ == "__main__":
    main()
