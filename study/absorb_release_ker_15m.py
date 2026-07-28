"""ABSORB->RELEASE breakout at S/R — 15m, with candle-1 = absorbed OR KER-vacuum (NEW variant).

Same as study/absorb_release_sr.py EXCEPT candle 1's "absorbed" test gets an OR branch:
  LONG  c1 = bullish AND (absA[i-1] >= AB1  OR  KerB[i-1] is infinite/vacuum, kb==9999.0)
  SHORT c1 = bearish AND (absA[i-1] >= AB1  OR  KerS[i-1] is infinite/vacuum, ks==9999.0)
KER vacuum (kb==9999) = price rose on net-SELLING volume (F_bull==0, W_bull>0) — sellers absorbed on an up bar;
the mirror ks==9999 = price fell on net-BUYING volume. It's a second absorption signature, OR-ed with heavy A.
c2..6 identical: c2 same-dir + EASY (A<=AB2), breaks c1 extreme, at support/resistance (VA or S/R zone, close
beyond the S/R zone), non-doji, skew>0 (long) / skew<0 (short, MIRROR — user paste said >0, read as typo).
EXIT: SL 0.1% beyond c2 extreme; TP 1:1.2 (VA+SR confluence -> 1:2). Entry c2 close. Acct $200k @10x, fee 0.08%.

CLI: python study/absorb_release_ker_15m.py   (15m; sweeps AB2; KER-off baseline vs KER-on; confluence-strip + binom p)
"""
from __future__ import annotations
import os, sys
from math import comb
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import study.engulf_va_sr_1h as E
import study.mom_absorb_1h as MA
import study.ker_continuation as KC

AB1 = 0.75; SL_PAD = 0.001; RR = 1.2
_KER = {}


def _ker_arrays(tf, A):
    if tf not in _KER:
        kb = np.array([KC.ker(b)[0] for b in A]); ks = np.array([KC.ker(b)[1] for b in A])
        _KER[tf] = (kb, ks)
    return _KER[tf]


def gen(tf, ab2=-0.75, use_ker=True, short_skew=-1):
    X = E.ctx(tf); d = X["d"]; c = X["c"]; h = X["h"]; l = X["l"]; n = X["n"]
    absA = X["absA"]; sk = X["sk"]; KB, KS = _ker_arrays(tf, X["A"]); sigs = []
    for i in range(1, n):
        if not E._nd(X, i) or E._sr_overlap(X, i) or absA[i] > ab2:          # c2 non-doji, no S/R overlap, c2 EASY
            continue
        vah, val = E._va_ref(X, i)
        c1_abs = absA[i - 1] >= AB1
        # LONG
        if d[i] == 1 and d[i - 1] == 1 and c[i] > h[i - 1] and sk[i] > 0:
            if (c1_abs or (use_ker and KB[i - 1] == 9999.0)) and not E._touches_sr(X, i, X["RES"]):
                va = E._at_va(X, i, val); sr = E._at_sr(X, i, X["SUP"], True) is not None
                if va or sr:
                    sigs.append(dict(i=i, side=1, entry=float(c[i]), ext=float(l[i]),
                                     src=("VA" if va else "") + ("SR" if sr else ""),
                                     ktrig=(not c1_abs)))
        # SHORT
        elif d[i] == -1 and d[i - 1] == -1 and c[i] < l[i - 1] and (sk[i] < 0 if short_skew < 0 else sk[i] > 0):
            if (c1_abs or (use_ker and KS[i - 1] == 9999.0)) and not E._touches_sr(X, i, X["SUP"]):
                va = E._at_va(X, i, vah); sr = E._at_sr(X, i, X["RES"], False) is not None
                if va or sr:
                    sigs.append(dict(i=i, side=-1, entry=float(c[i]), ext=float(h[i]),
                                     src=("VA" if va else "") + ("SR" if sr else ""),
                                     ktrig=(not c1_abs)))
    return sigs


def analyze(tf, ab2=-0.75, conf2=False, use_ker=True):
    X = E.ctx(tf); A = X["A"]; n = X["n"]; yr = X["yr"]; last = -1; rows = []
    for sg in gen(tf, ab2, use_ker):
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
        rows.append(dict(net=(rr * dist if win else -dist) - MA.FEE, side=s, yr=int(yr[i]),
                         src=sg["src"], dist=dist, ktrig=sg["ktrig"]))
    return rows


def bp(k, n, p):
    return sum(comb(n, j) * p ** j * (1 - p) ** (n - j) for j in range(k, n + 1)) if n else float("nan")


def line(lab, rows, be):
    m = len(rows)
    if m == 0:
        print("    %-18s n=0" % lab); return
    nt = np.array([r["net"] for r in rows]); w = int((nt > 0).sum())
    tot = (np.prod(1 + nt) - 1) * 100
    print("    %-18s n=%3d  win %5.1f%%  net %+7.1f%%  avgSL %.2f%%  p(vs BE)=%.3f"
          % (lab, m, 100 * w / m, tot, np.mean([r["dist"] for r in rows]) * 100, bp(w, m, be)))


def main():
    tf = "15m"; X = E.ctx(tf); be = (1 + MA.FEE) / (1 + RR)
    print("=" * 100)
    print("%s ABSORB->RELEASE at S/R, c1 = heavy-A OR KER-vacuum  |  %d buckets, %d S/R levels" % (tf, X["n"], X["nlev"]))
    print("  Exit fixed 1:1.2, SL 0.1%% beyond c2. Break-even win = %.1f%%. Acct $%.0f, fee %.2f%%. (short skew<0 = mirror)"
          % (be * 100, MA.B0, MA.FEE * 100))
    print("=" * 100)
    for use_ker in (False, True):
        for ab2 in (-0.75, 0.0, 0.75):
            rows = analyze(tf, ab2, False, use_ker)
            nk = sum(1 for r in rows if r["ktrig"])
            print("\n--- KER=%s  c2 A<=%+.2f  |  %d taken (%d KER-vacuum-triggered c1) ---"
                  % ("ON " if use_ker else "OFF", ab2, len(rows), nk))
            for lab, f in (("ALL", lambda r: True), ("LONG", lambda r: r["side"] > 0), ("SHORT", lambda r: r["side"] < 0),
                           ("confluence VASR", lambda r: r["src"] == "VASR"),
                           ("NON-confluence", lambda r: r["src"] != "VASR"),
                           ("KER-triggered only", lambda r: r["ktrig"])):
                line(lab, [r for r in rows if f(r)], be)


if __name__ == "__main__":
    main()
