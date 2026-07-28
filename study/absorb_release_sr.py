"""ABSORB->RELEASE breakout at Support/Resistance (NEW 15m candidate).

Pattern (LONG; SHORT mirrors) — a CONTINUATION/release, not the engulf reversal:
  1. candle 1 BULLISH and heavily ABSORBED   (absA[i-1] >= AB1, default 0.75 = "heavy")
  2. candle 2 BULLISH and EASY               (absA[i]   <= AB2, swept: -0.75 light / 0.0 / 0.75)
  3. candle 2 touches/opens at SUPPORT = prev-day VAL OR an S/R-indicator support ZONE that c2 closes ABOVE
  4. candle 2 close > candle 1 HIGH
  5. candle 2 non-doji
  6. candle 2 skew > 0
GUARDS (S/R-indicator only): skip if any active support zone overlaps a resistance zone; skip a LONG whose c2
  touches a resistance zone / a SHORT whose c2 touches a support zone.
EXIT: SL 0.1% beyond c2 extreme; TP 1:1.2 the stop; VA+SR confluence -> TP 1:2. Entry = c2 close.
Account $200k @ 10% margin x10 = 100% notional, compounded, fee 0.08%.

Reuses the S/R + VA + bias machinery from study/engulf_va_sr_1h (ctx + bool helpers).
CLI: python study/absorb_release_sr.py [tf]   (default 15m; sweeps AB2 and both exit modes)
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import study.engulf_va_sr_1h as E          # ctx, _nd, _va_ref, _at_va, _at_sr, _touches_sr, _sr_overlap, _bias, _ok
import study.mom_absorb_1h as MA           # walk, account, FEE, B0

AB1 = 0.75          # candle 1 "heavy" absorption floor
SL_PAD = 0.001      # SL 0.1% beyond candle 2 extreme
RR = 1.2


def gen(tf, ab1=AB1, ab2=-0.75, use_bias=False):
    X = E.ctx(tf); d = X["d"]; c = X["c"]; h = X["h"]; l = X["l"]; n = X["n"]
    absA = X["absA"]; sk = X["sk"]; sigs = []
    for i in range(1, n):
        if not E._nd(X, i):                                   # c2 non-doji (rule 5)
            continue
        if E._sr_overlap(X, i):                               # guard: S & R overlap
            continue
        if absA[i] > ab2:                                     # c2 EASY (rule 2)
            continue
        vah, val = E._va_ref(X, i); bi = E._bias(X, i) if use_bias else "both"
        # LONG
        if (d[i] == 1 and d[i - 1] == 1 and absA[i - 1] >= ab1                     # rules 1(dir+absorb) + 2(dir)
                and c[i] > h[i - 1] and sk[i] > 0 and E._ok(bi, 1)):               # rules 4 + 6 + bias
            if not E._touches_sr(X, i, X["RES"]):                                  # guard: not into resistance
                va = E._at_va(X, i, val); sr = E._at_sr(X, i, X["SUP"], True) is not None   # rule 3
                if va or sr:
                    sigs.append(dict(i=i, side=1, entry=float(c[i]), ext=float(l[i]),
                                     src=("VA" if va else "") + ("SR" if sr else "")))
        # SHORT
        elif (d[i] == -1 and d[i - 1] == -1 and absA[i - 1] >= ab1
                and c[i] < l[i - 1] and sk[i] < 0 and E._ok(bi, -1)):
            if not E._touches_sr(X, i, X["SUP"]):                                  # guard: not into support
                va = E._at_va(X, i, vah); sr = E._at_sr(X, i, X["RES"], False) is not None
                if va or sr:
                    sigs.append(dict(i=i, side=-1, entry=float(c[i]), ext=float(h[i]),
                                     src=("VA" if va else "") + ("SR" if sr else "")))
    return sigs


def analyze(tf, ab1=AB1, ab2=-0.75, conf2=False, use_bias=False):
    X = E.ctx(tf); A = X["A"]; n = X["n"]; yr = X["yr"]; last = -1; rows = []
    for sg in gen(tf, ab1, ab2, use_bias):
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
                         src=sg["src"], dist=dist))
    return rows


def report(label, rows):
    m = len(rows)
    if m == 0:
        print("  %-16s n=0" % label); return
    nt = np.array([r["net"] for r in rows]); w = 100.0 * (nt > 0).sum() / m
    tot = (np.prod(1 + nt) - 1) * 100
    gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum(); pf = (gg / ll) if ll > 0 else float("inf")
    bal = MA.account(list(nt))
    print("  %-16s n=%4d  win %5.1f%%  net %+7.1f%%  PF %.2f  avgSL %.2f%%  END $%9.0f  P&L $%+8.0f"
          % (label, m, w, tot, pf, np.mean([r["dist"] for r in rows]) * 100, bal, bal - MA.B0))


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "15m"
    X = E.ctx(tf)
    # fee-adjusted break-even win rate for RR 1:1.2 : w such that w*1.2 - (1-w) = fee  -> w=(1+fee)/2.2
    be = (1 + MA.FEE) / (1 + RR) * 100
    print("=" * 104)
    print("%s ABSORB->RELEASE breakout at (VA OR S/R zone)  |  %d %s buckets  |  %d S/R levels" % (tf, X["n"], tf, X["nlev"]))
    print("  c1 bull+absorbed(A>=%.2f) -> c2 bull+easy(A<=AB2), close>c1.high, skew>0, non-doji, at support. Mirror short." % AB1)
    print("  Entry c2 close, SL 0.1%% beyond c2 extreme. RR 1:%.1f (VA+SR->1:2). Break-even win @1:1.2 = %.1f%%. Acct $%.0f, fee %.2f%%."
          % (RR, be, MA.B0, MA.FEE * 100))
    print("=" * 104)
    for ab2 in (-0.75, 0.0, 0.75):
        tag = {-0.75: "AB2<=-0.75 (light/R-easy, symmetric intent)", 0.0: "AB2<=0.00 (proportional-or-easy)",
               0.75: "AB2<=0.75 (literal: not-heavy)"}[ab2]
        for conf2, lbl in ((False, "fixed 1:1.2"), (True, "VA+SR->1:2")):
            rows = analyze(tf, AB1, ab2, conf2)
            print("\n--- c2 %s   |   exit %s   |   %d taken ---" % (tag, lbl, len(rows)))
            for lab, f in (("ALL", lambda r: True), ("LONG", lambda r: r["side"] > 0), ("SHORT", lambda r: r["side"] < 0),
                           ("2025", lambda r: r["yr"] == 2025), ("2026", lambda r: r["yr"] == 2026),
                           ("  both VA+SR", lambda r: r["src"] == "VASR")):
                report(lab, [r for r in rows if f(r)])


if __name__ == "__main__":
    main()
