"""ABSORB->ENGULF at S/R with a market-structure BIAS filter — 15m (NEW variant).

EXIT: TP 1:1.2, SL 0.1% below c1 LOW (long) / above c1 HIGH (short).  VA+SR confluence -> TP 1:2.
BIAS (causal state machine over the S/R indicator's events; formation known K bars after the pivot):
  resistance MITIGATED -> long-regime ; support MITIGATED -> short-regime (also "STOP longs")
  last 2 formations both SUPPORT -> long ; both RESISTANCE -> short ; no regime set -> take nothing.
  Only same-direction signals fire in the current regime.
GUARDS (S/R indicator): skip if a support zone overlaps a resistance zone; skip a long c2 touching resistance /
  a short c2 touching support.
LONG  (SHORT mirrors): c1 = (bullish & A>=0.75) OR KerB-vacuum(kb==9999) OR (bearish doji).
  c2 = bullish ENGULFING marubozu (non-doji, body>=BODY_FRAC*range, engulfs c1) & A<=AB2 & close>c1.high & skew>0,
  touching support (prev-day VAL or S/R support zone c2 closes ABOVE).
SHORT c2 close rule is user-literal "close < c1 HIGH" by default (asymmetric); the structural mirror "close < c1 LOW"
  is included as a labeled comparison. SHORT skew<0.
Entry c2 close. Account $200k @10x, fee 0.08%.

CLI: python study/absorb_engulf_bias_15m.py
"""
from __future__ import annotations
import os, sys
from math import comb
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import study.engulf_va_sr_1h as E
import study.mom_absorb_1h as MA
import study.ker_continuation as KC

AB1 = 0.75; SL_PAD = 0.001; RR = 1.2; BODY_FRAC = 0.70
_CACHE = {}


def _prep(tf):
    if tf in _CACHE:
        return _CACHE[tf]
    X = E.ctx(tf); A = X["A"]; n = X["n"]
    KB = np.array([KC.ker(b)[0] for b in A]); KS = np.array([KC.ker(b)[1] for b in A])
    Kp = E.K
    # ---- causal market-structure bias array
    LEV = X["SUP"] + X["RES"]
    events = []                                   # (t_known, order, 'mit'|'form', kind)
    for x in LEV:
        events.append((x["i0"] + Kp, 1, "form", x["kind"]))     # formation confirmed K bars later
        if x["i1"] is not None:
            events.append((x["i1"], 0, "mit", x["kind"]))        # mitigation known when broken (order 0 = before form at same t)
    events.sort(key=lambda e: (e[0], e[1]))
    bias = np.zeros(n, int); state = 0; forms = []; ei = 0
    for i in range(n):
        while ei < len(events) and events[ei][0] <= i:
            _, _, typ, kind = events[ei]
            if typ == "mit":
                state = 1 if kind == "R" else -1                 # R mitigated->long, S mitigated->short
            else:
                forms.append(kind)
                if len(forms) >= 2 and forms[-1] == forms[-2]:
                    state = 1 if forms[-1] == "S" else -1        # 2 supports formed->long, 2 resistances->short
            ei += 1
        bias[i] = state
    _CACHE[tf] = (X, KB, KS, bias)
    return _CACHE[tf]


def gen(tf, ab2=0.75, engulf="body", use_bias=True, short_close="high"):
    X, KB, KS, bias = _prep(tf)
    d = X["d"]; c = X["c"]; h = X["h"]; l = X["l"]; o = X["o"]; n = X["n"]; absA = X["absA"]; sk = X["sk"]
    sigs = []
    for i in range(1, n):
        rng = h[i] - l[i]
        if rng <= 0:
            continue
        if not E._nd(X, i) or E._sr_overlap(X, i) or absA[i] > ab2:              # c2 non-doji + no overlap + c2 EASY
            continue
        if abs(c[i] - o[i]) < BODY_FRAC * rng:                                   # c2 big body / small wicks
            continue
        vah, val = E._va_ref(X, i)
        doji1 = not E._nd(X, i - 1)
        # LONG
        if use_bias and bias[i] != 1:
            pass
        elif d[i] == 1 and c[i] > h[i - 1] and sk[i] > 0:
            eng = (o[i] <= min(o[i - 1], c[i - 1])) if engulf == "body" else (o[i] <= l[i - 1])
            c1ok = (d[i - 1] == 1 and absA[i - 1] >= AB1) or KB[i - 1] == 9999.0 or (doji1 and c[i - 1] < o[i - 1])
            if eng and c1ok and not E._touches_sr(X, i, X["RES"]):
                va = E._at_va(X, i, val); sr = E._at_sr(X, i, X["SUP"], True) is not None
                if va or sr:
                    sigs.append(dict(i=i, side=1, entry=float(c[i]), ext=float(l[i - 1]),
                                     src=("VA" if va else "") + ("SR" if sr else "")))
                    continue
        # SHORT
        if use_bias and bias[i] != -1:
            continue
        closeok = (c[i] < h[i - 1]) if short_close == "high" else (c[i] < l[i - 1])
        if d[i] == -1 and closeok and sk[i] < 0:
            eng = (o[i] >= max(o[i - 1], c[i - 1])) if engulf == "body" else (o[i] >= h[i - 1])
            c1ok = (d[i - 1] == -1 and absA[i - 1] >= AB1) or KS[i - 1] == 9999.0 or (doji1 and c[i - 1] > o[i - 1])
            if eng and c1ok and not E._touches_sr(X, i, X["SUP"]):
                va = E._at_va(X, i, vah); sr = E._at_sr(X, i, X["RES"], False) is not None
                if va or sr:
                    sigs.append(dict(i=i, side=-1, entry=float(c[i]), ext=float(h[i - 1]),
                                     src=("VA" if va else "") + ("SR" if sr else "")))
    return sigs


def analyze(tf, ab2=0.75, engulf="body", use_bias=True, short_close="high", conf2=False):
    X, _, _, _ = _prep(tf); A = X["A"]; n = X["n"]; yr = X["yr"]; last = -1; rows = []
    for sg in gen(tf, ab2, engulf, use_bias, short_close):
        i = sg["i"]
        if i <= last:
            continue
        e = sg["entry"]; s = sg["side"]; ext = sg["ext"]
        rr = 2.0 if (conf2 and sg["src"] == "VASR") else RR
        if s > 0:
            sl = ext * (1 - SL_PAD); dist = (e - sl) / e; tp = e * (1 + rr * dist)
        else:
            sl = ext * (1 + SL_PAD); dist = (sl - e) / e; tp = e * (1 - rr * dist)
        if dist <= 0:
            continue
        win, ej = MA.walk(A, i, s, sl, tp, n); last = ej
        rows.append(dict(net=(rr * dist if win else -dist) - MA.FEE, side=s, yr=int(yr[i]), src=sg["src"], dist=dist))
    return rows


_PRE = {}


def precompute(tf):
    """One O(n*L) pass computing every config-INDEPENDENT per-bar gate, so config sweeps are O(n)."""
    if tf in _PRE:
        return _PRE[tf]
    X, KB, KS, bias = _prep(tf)
    d = X["d"]; c = X["c"]; h = X["h"]; l = X["l"]; o = X["o"]; n = X["n"]; absA = X["absA"]; sk = X["sk"]; yr = X["yr"]
    recs = []
    for i in range(1, n):
        rng = h[i] - l[i]
        if rng <= 0:
            continue
        base = E._nd(X, i) and not E._sr_overlap(X, i)
        if not base:
            continue
        vah, val = E._va_ref(X, i); doji1 = not E._nd(X, i - 1)
        recs.append(dict(
            i=i, yr=int(yr[i]), d=int(d[i]), c=float(c[i]), sk=float(sk[i]), absA=float(absA[i]),
            body_ratio=abs(c[i] - o[i]) / rng, hprev=float(h[i - 1]), lprev=float(l[i - 1]), bias=int(bias[i]),
            eng_body_l=(o[i] <= min(o[i - 1], c[i - 1])), eng_range_l=(o[i] <= l[i - 1]),
            eng_body_s=(o[i] >= max(o[i - 1], c[i - 1])), eng_range_s=(o[i] >= h[i - 1]),
            c1ok_l=((d[i - 1] == 1 and absA[i - 1] >= AB1) or KB[i - 1] == 9999.0 or (doji1 and c[i - 1] < o[i - 1])),
            c1ok_s=((d[i - 1] == -1 and absA[i - 1] >= AB1) or KS[i - 1] == 9999.0 or (doji1 and c[i - 1] > o[i - 1])),
            va_l=bool(E._at_va(X, i, val)), sr_l=(E._at_sr(X, i, X["SUP"], True) is not None), t_res=E._touches_sr(X, i, X["RES"]),
            va_s=bool(E._at_va(X, i, vah)), sr_s=(E._at_sr(X, i, X["RES"], False) is not None), t_sup=E._touches_sr(X, i, X["SUP"]),
        ))
    _PRE[tf] = (X, recs)
    return _PRE[tf]


def gen_fast(tf, ab2=0.75, engulf="body", use_bias=True, short_close="high", body_frac=BODY_FRAC, bias_arr=None):
    _, recs = precompute(tf); out = []
    for r in recs:
        if r["body_ratio"] < body_frac or r["absA"] > ab2:
            continue
        bz = r["bias"] if bias_arr is None else bias_arr[r["i"]]
        if r["d"] == 1 and r["c"] > r["hprev"] and r["sk"] > 0 and (not use_bias or bz == 1):
            eng = r["eng_body_l"] if engulf == "body" else r["eng_range_l"]
            if eng and r["c1ok_l"] and not r["t_res"] and (r["va_l"] or r["sr_l"]):
                out.append(dict(i=r["i"], side=1, entry=r["c"], ext=r["lprev"], yr=r["yr"],
                                src=("VA" if r["va_l"] else "") + ("SR" if r["sr_l"] else ""))); continue
        cok = (r["c"] < r["hprev"]) if short_close == "high" else (r["c"] < r["lprev"])
        if r["d"] == -1 and cok and r["sk"] < 0 and (not use_bias or bz == -1):
            eng = r["eng_body_s"] if engulf == "body" else r["eng_range_s"]
            if eng and r["c1ok_s"] and not r["t_sup"] and (r["va_s"] or r["sr_s"]):
                out.append(dict(i=r["i"], side=-1, entry=r["c"], ext=r["hprev"], yr=r["yr"],
                                src=("VA" if r["va_s"] else "") + ("SR" if r["sr_s"] else "")))
    return out


def analyze_fast(tf, ab2=0.75, engulf="body", use_bias=True, short_close="high", body_frac=BODY_FRAC, conf2=False, bias_arr=None):
    X, _ = precompute(tf); A = X["A"]; n = X["n"]; last = -1; rows = []
    for sg in gen_fast(tf, ab2, engulf, use_bias, short_close, body_frac, bias_arr):
        i = sg["i"]
        if i <= last:
            continue
        e = sg["entry"]; s = sg["side"]; ext = sg["ext"]
        rr = 2.0 if (conf2 and sg["src"] == "VASR") else RR
        if s > 0:
            sl = ext * (1 - SL_PAD); dist = (e - sl) / e; tp = e * (1 + rr * dist)
        else:
            sl = ext * (1 + SL_PAD); dist = (sl - e) / e; tp = e * (1 - rr * dist)
        if dist <= 0:
            continue
        win, ej = MA.walk(A, i, s, sl, tp, n); last = ej
        rows.append(dict(net=(rr * dist if win else -dist) - MA.FEE, side=s, yr=sg["yr"], src=sg["src"], dist=dist))
    return rows


def bp(k, m, p):
    if m == 0:
        return float("nan")
    if m <= 300:
        return sum(comb(m, j) * p ** j * (1 - p) ** (m - j) for j in range(k, m + 1))
    import math
    mu = m * p; sd = math.sqrt(m * p * (1 - p)); return 0.5 * math.erfc((k - 0.5 - mu) / (sd * math.sqrt(2.0)))


def line(lab, rows, be):
    m = len(rows)
    if m == 0:
        print("    %-18s n=0" % lab); return
    nt = np.array([r["net"] for r in rows]); w = int((nt > 0).sum()); tot = (np.prod(1 + nt) - 1) * 100
    print("    %-18s n=%3d  win %5.1f%%  net %+7.1f%%  avgSL %.2f%%  p(vs BE)=%.3f"
          % (lab, m, 100 * w / m, tot, np.mean([r["dist"] for r in rows]) * 100, bp(w, m, be)))


def block(tf, ab2, engulf, use_bias, short_close):
    be = (1 + MA.FEE) / (1 + RR)
    rows = analyze(tf, ab2, engulf, use_bias, short_close, conf2=False)
    print("\n--- bias=%s engulf=%-5s c2 A<=%+.2f short_close<c1.%s  |  %d taken ---"
          % ("ON " if use_bias else "OFF", engulf, ab2, short_close, len(rows)))
    for lab, f in (("ALL", lambda r: True), ("LONG", lambda r: r["side"] > 0), ("SHORT", lambda r: r["side"] < 0),
                   ("confluence VASR", lambda r: r["src"] == "VASR"), ("NON-confluence", lambda r: r["src"] != "VASR")):
        line(lab, [r for r in rows if f(r)], be)


def main():
    tf = "15m"; X, _, _, bias = _prep(tf); be = (1 + MA.FEE) / (1 + RR)
    nlong = int((bias == 1).sum()); nshort = int((bias == -1).sum()); nnone = int((bias == 0).sum())
    print("=" * 100)
    print("%s ABSORB->ENGULF at S/R + structure-BIAS  |  %d buckets, %d S/R levels" % (tf, X["n"], X["nlev"]))
    print("  Break-even win = %.1f%%. SL 0.1%% off c1. body>=%.0f%% range. bias regime: %d long / %d short / %d none bars."
          % (be * 100, BODY_FRAC * 100, nlong, nshort, nnone))
    print("=" * 100)
    block(tf, 0.75, "body", True, "high")     # primary (literal c2<=0.75, literal short close<c1.high)
    block(tf, -0.75, "body", True, "high")    # R-easy c2
    block(tf, 0.75, "body", False, "high")    # bias OFF -> isolate the structure filter's effect
    block(tf, 0.75, "range", True, "high")    # stricter range-engulf
    block(tf, 0.75, "body", True, "low")      # SHORT structural mirror (close<c1.low)


if __name__ == "__main__":
    main()
