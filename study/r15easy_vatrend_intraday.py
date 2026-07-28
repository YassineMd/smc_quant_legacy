"""15mReasy (frozen) + TWO-PHASE VA-trend bias with a 3PM intraday switch (15m ONLY, CAUSAL).

Base : direction + A<=-0.75 (R-easy) + |skew|>=0.4 + prev-same-dir. SL 0.1% beyond entry; TP = RR x SL.

BIAS (Day 3 = the day we trade; UTC calendar days midnight..23:59):
  PHASE A  (00:00 -> 15:00 UTC)  = filter #4:  full-day VA of D1 (2 days ago) vs D2 (yesterday).
             VAH(D2)>VAH(D1) AND VAL(D2)>VAL(D1) -> long ; both < -> short ; else both.
  PHASE B  (>= 15:00 UTC, "3PM") = NEW rule:  full-day VA of D2 (closed) vs D3-SO-FAR (midnight..signal bar,
             CAUSAL partial profile).  VAH(D3)>VAH(D2) AND VAL(D3)>VAL(D2) -> long ; both < -> short ; else both.
  The reference FLIPS at 3PM: once Day 3's own developing profile is mature it replaces the D1/D2 comparison.
  Causal throughout — D1/D2 closed; D3 uses only buckets up to and including the entry bar (we enter at its close).

Compares baseline (no bias) / filter#4-only (D1-D2 all day) / filter#4 + 3PM-switch, controlled + one-at-a-time,
per side, plus the HONEST aligned-vs-counter partition (Fisher p) for BOTH bias variants so the 3PM switch's
marginal value is explicit.

Run: python study/r15easy_vatrend_intraday.py
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
SKEW_MIN = 0.4
CUTOFF_HOUR = 15                                               # "3PM" = 15:00 UTC (day = UTC midnight..23:59)
RRS = (1.0, 1.5)


def _dtu(ts):
    return dt.datetime.utcfromtimestamp(float(ts))


def daily_va(A):
    """Full-day 70% value area (val/vah) per UTC day + first bucket index per UTC day."""
    days = {}; first = {}
    for i, b in enumerate(A):
        d = _dtu(b.get("start_time", 0.0) or 0.0).date()
        days.setdefault(d, []).append(b)
        if d not in first:
            first[d] = i
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
    return out, first


def partial_va(A, i, i0):
    """CAUSAL 70% value area of Day-3 buckets [i0 .. i] (midnight of the signal's day up to the entry bar)."""
    prof = {}
    for k in range(i0, i + 1):
        for pr, v in (A[k].get("levels") or {}).items():
            try:
                p = float(pr)
            except (TypeError, ValueError):
                continue
            prof[p] = prof.get(p, 0.0) + float(v.get("b", 0) or 0) + float(v.get("s", 0) or 0)
    return va_poc(prof)


def _trend(a, b):
    """b's value area vs a's: both edges up -> long ; both down -> short ; else both."""
    if not a or not b:
        return "both"
    if b["vah"] > a["vah"] and b["val"] > a["val"]:
        return "long"
    if b["vah"] < a["vah"] and b["val"] < a["val"]:
        return "short"
    return "both"


def bias_f4(t, dayva):
    """Filter #4: D1 vs D2 (always, all day)."""
    d3 = t.date()
    return _trend(dayva.get(d3 - dt.timedelta(days=2)), dayva.get(d3 - dt.timedelta(days=1)))


def bias_switch(A, i, t, dayva, first):
    """Two-phase: before 3PM -> filter #4 (D1 vs D2); from 3PM -> D2 vs D3-so-far (causal partial)."""
    if t.hour < CUTOFF_HOUR:
        return bias_f4(t, dayva)
    d3 = t.date()
    va2 = dayva.get(d3 - dt.timedelta(days=1))
    va3 = partial_va(A, i, first.get(d3, i))
    return _trend(va2, va3)


def prep(A, afirst, dayva, first):
    Aval = [None] * len(A); dirn = [0] * len(A)
    for i in range(len(A)):
        try:
            Aval[i] = ABS.absorption(A, i)[0]
        except Exception:
            Aval[i] = None
        dirn[i] = 1 if A[i]["up"] else (-1 if A[i]["dn"] else 0)
    out = []
    for i in range(max(afirst, 1), len(A) - 1):
        s = dirn[i]
        if s == 0 or Aval[i] is None or Aval[i] > R_EASY or dirn[i - 1] != s:
            continue
        sk = A[i].get("sk")
        if sk is None or (sk < SKEW_MIN if s > 0 else sk > -SKEW_MIN):
            continue
        t = _dtu(A[i]["start_time"])
        b4 = bias_f4(t, dayva)
        bsw = bias_switch(A, i, t, dayva, first)
        out.append(dict(i=i, side=s, t=float(A[i]["start_time"]), hour=t.hour,
                        phaseB=(t.hour >= CUTOFF_HOUR), bias4=b4, biassw=bsw))
    return out


def _allowed(bias, s):
    return (bias == "both") or (bias == "long" and s > 0) or (bias == "short" and s < 0)


def run(A, sigs, rr, chain, biaskey=None):
    last = -1; out = []
    for sg in sigs:
        if chain and sg["i"] <= last:
            continue
        if biaskey is not None and not _allowed(sg[biaskey], sg["side"]):
            continue
        r = RR.simulate_rr(A, sg["i"], sg["side"], rr, "sl")
        if r is None:
            continue
        out.append(dict(side=sg["side"], win=(r[0] == "TP"), net=r[1] - FEE, gross=r[1])); last = r[2]
    return out


def stat(rows, rr, label):
    n = len(rows)
    if n == 0:
        print("  %-18s n=0" % label); return
    w = sum(1 for r in rows if r["net"] > 0)
    g = np.mean([r["gross"] for r in rows]); nt = np.array([r["net"] for r in rows])
    tot = (np.prod(1 + nt) - 1) * 100
    gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum(); pf = (gg / ll) if ll > 0 else float("inf")
    be = 1.0 / (1 + rr)
    print("  %-18s n=%3d  win %5.1f%% (BE %.0f%%)  net %+6.1f%%  gross/tr %+.4f%%  PF %4.2f"
          % (label, n, 100.0 * w / n, be * 100, tot, g * 100, pf))


def honest(A, sigs, rr, biaskey, label):
    al = []; ct = []; last = -1
    for sg in sigs:
        if sg["i"] <= last:
            continue
        r = RR.simulate_rr(A, sg["i"], sg["side"], rr, "sl")
        if r is None:
            continue
        last = r[2]
        b = sg[biaskey]
        if b not in ("long", "short"):
            continue
        (al if _allowed(b, sg["side"]) else ct).append(r[0] == "TP")
    aw, an = sum(al), len(al); cw, cn = sum(ct), len(ct)
    p = None
    if fisher_exact is not None and an and cn:
        p = fisher_exact([[aw, an - aw], [cw, cn - cw]])[1]
    print("  %-16s aligned n=%3d win %5.1f%%   counter n=%3d win %5.1f%%   Fisher p=%s"
          % (label, an, 100.0 * aw / an if an else 0.0, cn, 100.0 * cw / cn if cn else 0.0,
             ("%.3f" % p) if p is not None else "n/a"))


def main():
    A, afirst, _floor = build("15m")
    dayva, first = daily_va(A)
    sigs = prep(A, afirst, dayva, first)
    nl = sum(1 for s in sigs if s["side"] > 0)
    nB = sum(1 for s in sigs if s["phaseB"])
    # how often the 3PM switch actually CHANGES the bias vs filter#4 alone (only possible in phase B)
    changed = sum(1 for s in sigs if s["phaseB"] and s["bias4"] != s["biassw"])
    print("=" * 112)
    print("15mReasy + VA-trend with a 3PM (15:00 UTC) intraday switch  (15m)  |  signals %d (%dL/%dS)"
          % (len(sigs), nl, len(sigs) - nl))
    print("=" * 112)
    print("  phase split: %d before 3PM (use D1/D2)  |  %d from 3PM (use D2/D3-so-far)" % (len(sigs) - nB, nB))
    print("  the 3PM switch changed the bias vs filter#4 on %d of the %d afternoon signals\n" % (changed, nB))
    for rr in RRS:
        print("RR 1:%.1f  (one-at-a-time)" % rr)
        stat(run(A, sigs, rr, True, None), rr, "baseline (no bias)")
        stat(run(A, sigs, rr, True, "bias4"), rr, "filter#4 only")
        stat(run(A, sigs, rr, True, "biassw"), rr, "filter#4 + 3PM")
        stat([r for r in run(A, sigs, rr, True, "biassw") if r["side"] > 0], rr, "  3PM-switch LONG")
        stat([r for r in run(A, sigs, rr, True, "biassw") if r["side"] < 0], rr, "  3PM-switch SHORT")
        print("  HONEST (aligned vs counter-bias):")
        honest(A, sigs, rr, "bias4", "filter#4 only")
        honest(A, sigs, rr, "biassw", "filter#4 + 3PM")
        print()
    print("CAVEAT: 3PM = 15:00 UTC (day = UTC midnight..23:59). D3 profile CAUSAL (midnight..entry bar).")
    print("        15m, one 34-day regime, forward n=0. The switch only acts on afternoon signals.")


if __name__ == "__main__":
    main()
