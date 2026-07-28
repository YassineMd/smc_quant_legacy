"""15mReasy variant: DROP the prev-candle-same-dir filter + prior-2-day VA-trend bias (15m ONLY, CAUSAL).

Base : direction + A<=-0.75 (R-easy) + |skew|>=0.4  [NO prev-candle-same-direction requirement].
       SL 0.1% beyond entry extreme; TP = RR x SL.
BIAS : L1 VA-trend ONLY. Full-day 70% value area of D1 (2 days ago) vs D2 (yesterday):
         VAH(D2)>VAH(D1) AND VAL(D2)>VAL(D1) -> long-only ; both < -> short-only ; else both.
       UTC calendar days (midnight..23:59). CAUSAL: D1,D2 fully closed before any D3 signal.

Isolates the effect of REMOVING filter-3 (prev-same-dir): prints a with-prev vs without-prev baseline so the cost/
benefit of the drop is explicit, then the +VA-trend-bias set (controlled + one-at-a-time, per side) and the HONEST
aligned-vs-counter partition with a Fisher exact p, on the no-prev base.

Run: python study/r15easy_vatrend_noprev.py
"""
from __future__ import annotations
import os, sys, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from app import absorption as ABS
import study.mm_skew_strategy as S
from study.mm_skew_feature_matrix import va_poc
from study.mm_skew_v11_tf import build
import study.mm_skew_rr_sweep as RR
try:
    from scipy.stats import fisher_exact
except Exception:                                              # pragma: no cover
    fisher_exact = None

FEE = 0.0008
R_EASY = -0.75
SKEW_MIN = 0.4                                                 # FROZEN skew threshold
RRS = (1.0, 1.5)


def _day(ts):
    return dt.datetime.utcfromtimestamp(float(ts)).date()


def daily_va(A):
    days = {}
    for b in A:
        days.setdefault(_day(b.get("start_time", 0.0) or 0.0), []).append(b)
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


def va_bias(d3, dayva):
    va1 = dayva.get(d3 - dt.timedelta(days=2))
    va2 = dayva.get(d3 - dt.timedelta(days=1))
    if not va1 or not va2:
        return "both"
    if va2["vah"] > va1["vah"] and va2["val"] > va1["val"]:
        return "long"
    if va2["vah"] < va1["vah"] and va2["val"] < va1["val"]:
        return "short"
    return "both"


def prep(A, first, dayva, require_prev):
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
        if s == 0 or Aval[i] is None or Aval[i] > R_EASY:
            continue
        if require_prev and dirn[i - 1] != s:                 # filter-3 (optional)
            continue
        sk = A[i].get("sk")
        if sk is None or (sk < SKEW_MIN if s > 0 else sk > -SKEW_MIN):   # |skew|>=0.4
            continue
        d3 = _day(A[i]["start_time"])
        bias = va_bias(d3, dayva)
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
        print("  %-16s n=0" % label); return
    w = sum(1 for r in rows if r["net"] > 0)
    g = np.mean([r["gross"] for r in rows]); nt = np.array([r["net"] for r in rows])
    tot = (np.prod(1 + nt) - 1) * 100
    gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum(); pf = (gg / ll) if ll > 0 else float("inf")
    be = 1.0 / (1 + rr)
    bal = S.BAL0
    for r in rows:
        bal += S.POS_FRAC * bal * S.LEV * r["net"]
    print("  %-16s n=%3d  win %5.1f%% (BE %.0f%%)  net %+6.1f%%  gross/tr %+.4f%%  PF %4.2f  $%s"
          % (label, n, 100.0 * w / n, be * 100, tot, g * 100, pf, f"{bal:,.0f}"))


def honest(A, sigs, rr):
    al = []; ct = []; last = -1
    for sg in sigs:
        if sg["i"] <= last:
            continue
        r = RR.simulate_rr(A, sg["i"], sg["side"], rr, "sl")
        if r is None:
            continue
        last = r[2]
        if sg["bias"] not in ("long", "short"):
            continue
        (al if sg["allowed"] else ct).append(r[0] == "TP")
    aw, an = sum(al), len(al); cw, cn = sum(ct), len(ct)
    p = None
    if fisher_exact is not None and an and cn:
        p = fisher_exact([[aw, an - aw], [cw, cn - cw]])[1]
    print("  aligned n=%3d win %5.1f%%   counter n=%3d win %5.1f%%   Fisher p=%s"
          % (an, 100.0 * aw / an if an else 0.0, cn, 100.0 * cw / cn if cn else 0.0,
             ("%.3f" % p) if p is not None else "n/a"))


def main():
    A, first, _floor = build("15m")
    dayva = daily_va(A)
    sig_prev = prep(A, first, dayva, require_prev=True)        # frozen base (filter-3 ON)
    sigs = prep(A, first, dayva, require_prev=False)           # this spec: filter-3 OFF
    nl = sum(1 for s in sigs if s["side"] > 0)
    days = sorted({_day(A[s["i"]]["start_time"]) for s in sigs})
    dc = {"long": 0, "short": 0, "both": 0}
    for d in days:
        dc[va_bias(d, dayva)] += 1
    print("=" * 112)
    print("15mReasy  NO prev-dir filter  + VA-trend bias  (15m)  |  |skew|>=0.4  |  signals %d (%dL/%dS)"
          % (len(sigs), nl, len(sigs) - nl))
    print("=" * 112)
    print("  filter-3 isolation:  with prev-dir -> %d signals   |   without prev-dir -> %d signals (+%d)"
          % (len(sig_prev), len(sigs), len(sigs) - len(sig_prev)))
    print("  trading days %d  ->  LONG-bias %d | SHORT-bias %d | BOTH %d" % (len(days), dc["long"], dc["short"], dc["both"]))
    print("  signals allowed by VA-trend bias: %d of %d  (dropped %d counter-bias)\n"
          % (sum(1 for s in sigs if s["allowed"]), len(sigs), sum(1 for s in sigs if not s["allowed"])))
    for rr in RRS:
        print("RR 1:%.1f  (one-at-a-time)" % rr)
        stat(run(A, sig_prev, rr, True, False), rr, "base +prev-dir")     # frozen base, for reference
        stat(run(A, sigs, rr, True, False), rr, "base NO-prev")           # this spec's base
        filt = run(A, sigs, rr, True, True)
        stat(filt, rr, ">>>NO-prev +bias")
        stat([r for r in filt if r["side"] > 0], rr, ">>>bias LONG")
        stat([r for r in filt if r["side"] < 0], rr, ">>>bias SHORT")
        print("  HONEST (aligned vs counter-bias, on the NO-prev base):")
        honest(A, sigs, rr)
        print()
    print("CAVEAT: filter-3 removed (looser base). |skew|>=0.4 kept. VA-trend causal. 15m, one regime, forward n=0.")


if __name__ == "__main__":
    main()
