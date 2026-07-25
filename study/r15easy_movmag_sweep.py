"""15mReasy — mov_mag SWEEP (absolute + volatility-normalized RATIO), 15m ONLY.

Base config : A(i) <= -0.75 (R-easy) + skew agrees + candle i-1 same direction. SL 0.1% beyond extreme, plain TP.
mov_mag(i)  = ((close*100/ref - 100)^2)*100, ref = low(up)/high(down) — squared % travel from the reversed extreme.
mov_ratio(i)= mov_mag(i) / trailing-EMA50(mov_mag), EMA EXCLUDES the current bucket (causal) — the SAME
              volatility-normalized measure as MMXSKEW v1.2-Dynamic, so "small vs the recent norm" instead of a
              fixed magnitude.

Reported per side (LONG / SHORT), controlled (n constant, the honest view) unless noted:
  1. ABSOLUTE cap sweep  (mov_mag <= thr)         2. ABSOLUTE disjoint bands
  3. RATIO cap sweep     (mov_ratio <= thr)        4. RATIO disjoint bands
Each row: n, win%, gross/tr, net/tr, fee-adjusted BE*, and gap = win - BE*. Gross is the binding number on 15m.

Run: python study/r15easy_movmag_sweep.py
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
RRS = (1.0, 1.5)


def mov_mag(b):
    o, c, h, l = b["o"], b["c"], b["h"], b["l"]
    ref = l if c > o else (h if c < o else o)
    return ((((c * 100.0) / ref) - 100.0) ** 2) * 100.0 if ref > 0 else 0.0


def prep(A, first):
    n = len(A)
    mm = [mov_mag(A[i]) for i in range(n)]
    ratio = [1.0] * n; ema = None                     # trailing EMA-50 of mov_mag, EXCLUDES current (causal)
    for i in range(n):
        ratio[i] = (mm[i] / ema) if (ema and ema > 0) else 1.0
        ema = mm[i] if ema is None else mm[i] * (2 / 51) + ema * (1 - 2 / 51)
    Aval = [None] * n; dirn = [0] * n
    for i in range(n):
        try:
            Aval[i] = ABS.absorption(A, i)[0]
        except Exception:
            Aval[i] = None
        dirn[i] = 1 if A[i]["up"] else (-1 if A[i]["dn"] else 0)

    def skew_ok(i, s):
        sk = A[i].get("sk")
        return sk is not None and ((sk > 0) if s > 0 else (sk < 0))

    base = []
    for i in range(max(first, 1), n - 1):
        s = dirn[i]
        if s == 0 or Aval[i] is None or Aval[i] > R_EASY or not skew_ok(i, s) or dirn[i - 1] != s:
            continue
        base.append(dict(i=i, side=s, t=float(A[i].get("start_time", 0)), mm=mm[i], ratio=ratio[i]))
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


def cell(A, sigs, rr):
    last = -1; rows = []
    for sg in sigs:
        r = sim(A, sg["i"], sg["side"], rr)          # controlled: every signal, independent
        if r is None:
            continue
        rows.append(dict(win=(r[0] == "TP"), gross=r[1], net=r[1] - FEE, slf=r[3]))
    n = len(rows)
    if n == 0:
        return "n=0"
    w = sum(1 for r in rows if r["win"])
    g = np.mean([r["gross"] for r in rows]); nt = np.mean([r["net"] for r in rows])
    s = float(np.median([r["slf"] for r in rows]))
    be = (FEE + s) / (s * (1 + rr)) * 100
    wr = 100.0 * w / n
    return "n=%3d win %5.1f%% gross %+.4f%% net %+.4f%% BE* %.1f%% gap %+.1f" % (n, wr, g * 100, nt * 100, be, wr - be)


def sweep(A, base, key, caps, bands, title):
    for sd, nm in ((1, "LONG"), (-1, "SHORT")):
        ss = [s for s in base if s["side"] == sd]
        print("  -- %s %s (n_signals=%d) --" % (nm, title, len(ss)))
        for rr in RRS:
            print("    RR 1:%.1f  caps:" % rr)
            for thr in caps:
                print("      %-12s %s" % ("%s<=%.2f" % (key, thr), cell(A, [s for s in ss if s[key] <= thr], rr)))
            print("    RR 1:%.1f  bands:" % rr)
            for lo, hi in bands:
                lbl = "%s in (%.1f,%.1f]" % (key, lo, hi) if hi < 1e8 else "%s > %.1f" % (key, lo)
                print("      %-16s %s" % (lbl, cell(A, [s for s in ss if lo < s[key] <= hi], rr)))
        print()


def main():
    A, first, _ = build("15m")
    base = prep(A, first)
    nl = sum(1 for s in base if s["side"] > 0)
    print("=" * 104)
    print("15mReasy mov_mag SWEEP (15m)  |  A<=-0.75 + skew + prev-same-dir base = %d (%dL/%dS)  |  controlled view"
          % (len(base), nl, len(base) - nl))
    print("=" * 104)
    print()
    print("ABSOLUTE mov_mag:")
    sweep(A, base, "mm", (5, 7.5, 10, 12.5, 15, 20, 25),
          [(0, 5), (5, 10), (10, 15), (15, 25), (25, 1e9)], "abs")
    print("RATIO mov_mag (= mov_mag / trailing-EMA50, causal):")
    sweep(A, base, "ratio", (0.4, 0.6, 0.8, 1.0, 1.3),
          [(0, 0.4), (0.4, 0.7), (0.7, 1.0), (1.0, 1.5), (1.5, 1e9)], "ratio")


if __name__ == "__main__":
    main()
