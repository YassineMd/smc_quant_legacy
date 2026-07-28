"""15mReasy (frozen) + FULL directional bias = VA-trend + Day-2 range breakout (15m ONLY, CAUSAL).

Base : direction + A<=-0.75 (R-easy) + |skew|>=0.4 + prev-same-dir. SL 0.1% beyond entry extreme; TP = RR x SL.

BIAS (two layers, evaluated on Day 3; UTC calendar days midnight..23:59):
  L1  VA-TREND (structural, slow): full-day 70% value area of D1 (2 days ago) vs D2 (yesterday).
        VAH(D2)>VAH(D1) AND VAL(D2)>VAL(D1) -> value shifted UP   -> long-only
        VAH(D2)<VAH(D1) AND VAL(D2)<VAL(D1) -> value shifted DOWN -> short-only
        else                                                        -> both
  L2  BREAKOUT (live Day-3 event, OVERRIDES L1): running extreme of Day 3 from midnight UP TO AND INCLUDING the
        signal bar (entry is at that bar's close, so its H/L are known -> causal, no look-ahead):
        run_low(D3, <=i)  < low(D2)  -> short-only
        run_high(D3, <=i) > high(D2) -> long-only
      exactly-one-side breach gives a clean bias and OVERRIDES L1 (confirmation > prediction); both/neither -> fall
      back to L1. Days lacking two prior days, or D2 without VA / hi-lo, default to L1 / both respectively.

Reports: day + signal classification, controlled (n const) + one-at-a-time, per side, and the HONEST partition
(aligned-with-bias vs counter-bias) with a Fisher exact p. Also how often L2 overrode / contradicted L1.

Run: python study/r15easy_biasfull.py
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
RRS = (1.0, 1.5)


def _day(ts):
    return dt.datetime.utcfromtimestamp(float(ts)).date()


def daily_va(A):
    """Per-UTC-day 70% value area (val/vah) from the 15m level profiles."""
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


def daily_hilo(A):
    """Per-UTC-day full-day (high, low) from the 15m bars."""
    out = {}
    for b in A:
        d = _day(b.get("start_time", 0.0) or 0.0)
        hi, lo = float(b["h"]), float(b["l"])
        if d not in out:
            out[d] = [hi, lo]
        else:
            if hi > out[d][0]:
                out[d][0] = hi
            if lo < out[d][1]:
                out[d][1] = lo
    return {d: (v[0], v[1]) for d, v in out.items()}


def running_extremes(A):
    """run_hi[i]/run_lo[i] = max-high / min-low from the start of bar i's UTC day through bar i (causal)."""
    n = len(A)
    rh = [0.0] * n; rl = [0.0] * n
    cur = None; ch = -1e30; cl = 1e30
    for i, b in enumerate(A):
        d = _day(b.get("start_time", 0.0) or 0.0)
        h, l = float(b["h"]), float(b["l"])
        if d != cur:
            cur = d; ch = h; cl = l
        else:
            ch = max(ch, h); cl = min(cl, l)
        rh[i] = ch; rl[i] = cl
    return rh, rl


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


def breakout_bias(d3, i, rh, rl, dayhl):
    """L2: exactly-one-side breach of D2's range up to bar i -> long/short; else None (no clean signal)."""
    hl2 = dayhl.get(d3 - dt.timedelta(days=1))
    if not hl2:
        return None
    up = rh[i] > hl2[0]
    dn = rl[i] < hl2[1]
    if up and not dn:
        return "long"
    if dn and not up:
        return "short"
    return None                                                # both or neither breached


def prep(A, first, dayva, dayhl, rh, rl):
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
        d3 = _day(A[i]["start_time"])
        vb = va_bias(d3, dayva)
        bo = breakout_bias(d3, i, rh, rl, dayhl)
        bias = bo if bo is not None else vb                    # L2 overrides L1
        allowed = (bias == "both") or (bias == "long" and s > 0) or (bias == "short" and s < 0)
        out.append(dict(i=i, side=s, t=float(A[i]["start_time"]), va=vb, bo=bo, bias=bias, allowed=allowed))
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


_INV = {"long": "short", "short": "long", "both": "both"}


def _bias_scheme(sg, scheme):
    """Directional bias for a signal under a given combination scheme."""
    va, bo = sg["va"], sg["bo"]
    if scheme == "L1only":
        return va
    if scheme == "L2only":
        return bo if bo is not None else "both"
    if scheme == "L2over":                                     # breakout overrides VA-trend (the spec)
        return bo if bo is not None else va
    if scheme == "L1over":                                     # VA-trend wins conflicts
        return va if va in ("long", "short") else (bo if bo is not None else "both")
    if scheme == "invL2":                                      # DIAGNOSTIC: breakout direction flipped
        return _INV[bo] if bo is not None else "both"
    raise ValueError(scheme)


def honest(A, sigs, rr, scheme):
    """Aligned-with-bias vs counter-bias, one-at-a-time chain, Fisher exact on win/loss."""
    def allowed(sg):
        b = _bias_scheme(sg, scheme)
        return b, (b == "both") or (b == "long" and sg["side"] > 0) or (b == "short" and sg["side"] < 0)
    al = []; ct = []
    last = -1
    for sg in sigs:
        if sg["i"] <= last:
            continue
        r = RR.simulate_rr(A, sg["i"], sg["side"], rr, "sl")
        if r is None:
            continue
        last = r[2]
        b, ok = allowed(sg)
        if b not in ("long", "short"):
            continue
        (al if ok else ct).append(r[0] == "TP")
    aw, an = sum(al), len(al); cw, cn = sum(ct), len(ct)
    p = None
    if fisher_exact is not None and an and cn:
        p = fisher_exact([[aw, an - aw], [cw, cn - cw]])[1]
    print("  %-8s aligned n=%3d win %5.1f%%   counter n=%3d win %5.1f%%   Fisher p=%s"
          % (scheme, an, 100.0 * aw / an if an else 0.0, cn, 100.0 * cw / cn if cn else 0.0,
             ("%.3f" % p) if p is not None else "n/a"))


def main():
    A, first, _floor = build("15m")
    dayva = daily_va(A); dayhl = daily_hilo(A); rh, rl = running_extremes(A)
    sigs = prep(A, first, dayva, dayhl, rh, rl)
    nl = sum(1 for s in sigs if s["side"] > 0)
    # override / conflict accounting
    ov = sum(1 for s in sigs if s["bo"] is not None)
    conflict = sum(1 for s in sigs if s["bo"] is not None and s["va"] in ("long", "short") and s["bo"] != s["va"])
    print("=" * 112)
    print("15mReasy + VA-trend + Day-2 breakout bias  (15m)  |  frozen base  |  signals %d (%dL/%dS)"
          % (len(sigs), nl, len(sigs) - nl))
    print("=" * 112)
    print("  L2 breakout fired on %d of %d signals (overrides L1)   |   L2 CONTRADICTED L1 on %d"
          % (ov, len(sigs), conflict))
    print("  signals allowed by the combined bias: %d of %d  (dropped %d counter-bias)\n"
          % (sum(1 for s in sigs if s["allowed"]), len(sigs), sum(1 for s in sigs if not s["allowed"])))
    for rr in RRS:
        for chain, vl in ((False, "controlled"), (True, "one-at-a-time")):
            print("RR 1:%.1f  (%s)" % (rr, vl))
            stat(run(A, sigs, rr, chain, False), rr, "baseline ALL")
            filt = run(A, sigs, rr, chain, True)
            stat(filt, rr, ">>>bias ALL")
            stat([r for r in filt if r["side"] > 0], rr, ">>>bias LONG")
            stat([r for r in filt if r["side"] < 0], rr, ">>>bias SHORT")
            print()
        print("HONEST decomposition (aligned vs counter-bias, chain):")
        for sc in ("L1only", "L2only", "L2over", "L1over", "invL2"):
            honest(A, sigs, rr, sc)
        print()
    print("CAVEAT: causal (D1/D2 closed; Day-3 breach uses run-extreme up to the entry bar). L2 overrides L1.")
    print("        UTC calendar days. 15m, one 34-day regime, forward n=0. Bias thins the sample.")


if __name__ == "__main__":
    main()
