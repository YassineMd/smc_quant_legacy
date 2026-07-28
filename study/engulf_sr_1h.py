"""1h ENGULFING reversal at the terminal's SUPPORT/RESISTANCE indicator (app.support_resistance, fractal pivots k=8).

S/R is a ZONE = the pivot candle's whole high-low range (support pivot low candle / resistance pivot high candle).
Causal: a level is usable at bar i only once its pivot is CONFIRMED (i0+K <= i) and it is still UNBROKEN (no close
through it at/before i).

LONG : c1 bearish, c2 bullish, c2 TOUCHES-or-OPENS-at a support zone, c2 close > c1 high, both candles non-doji.
SHORT: c1 bullish, c2 bearish, c2 TOUCHES-or-OPENS-at a resistance zone, c2 close < c1 open, both non-doji.
Entry = c2 close. SL = 0.1% beyond c2 extreme (structural).
TP  -- BOTH variants:
  A) fixed 1:1.2 the stop distance.
  B) closest OPPOSITE S/R level (nearest resistance above for long / support below for short), require >=1:1.2 R:R
     to enter (else skip); SL unchanged.
$200k @ 10% margin x10 = 100% notional, compounded, fee 0.08%. win = net>0.

Run: python study/engulf_sr_1h.py [tf]
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import study.signal_search_lib as L
import study.mom_absorb_1h as MA
from app import support_resistance as SR

SL_PAD = 0.001; RR_FIXED = 1.2; MIN_RR = 1.2
TF = sys.argv[1] if len(sys.argv) > 1 else "1h"
F = L.load_features(TF)
A = F["A"]; d = F["dir"]; o = F["o"]; c = F["c"]; h = F["h"]; l = F["l"]; n = F["n"]; yr = F["year"]
K = SR.SR_PIVOT_K
LEV = SR.detect(A, K)
SUP = [lv for lv in LEV if lv["kind"] == "S"]
RES = [lv for lv in LEV if lv["kind"] == "R"]


def nd(i):
    b = abs(c[i] - o[i]); return b > (h[i] - max(o[i], c[i])) and b > (min(o[i], c[i]) - l[i])


def active(levs, i):
    return [lv for lv in levs if lv["i0"] + K <= i and (lv["i1"] is None or lv["i1"] > i)]


def at_zone(i, lv):
    i0 = lv["i0"]; zlo = min(l[i0], h[i0]); zhi = max(l[i0], h[i0])   # the pivot candle's whole range
    return (l[i] <= zhi and h[i] >= zlo) or (zlo <= o[i] <= zhi)      # candle2 touches, OR opens inside


def gen_signals():
    sigs = []
    for i in range(1, n):
        if not (nd(i) and nd(i - 1)):
            continue
        entry = float(c[i])
        if d[i - 1] == -1 and d[i] == 1 and c[i] > h[i - 1] and any(at_zone(i, lv) for lv in active(SUP, i)):
            opp = min((lv["price"] for lv in active(RES, i) if lv["price"] > entry), default=None)
            sigs.append(dict(i=i, side=1, entry=entry, ext=float(l[i]), opp=opp, yr=int(yr[i])))
        elif d[i - 1] == 1 and d[i] == -1 and c[i] < o[i - 1] and any(at_zone(i, lv) for lv in active(RES, i)):
            opp = max((lv["price"] for lv in active(SUP, i) if lv["price"] < entry), default=None)
            sigs.append(dict(i=i, side=-1, entry=entry, ext=float(h[i]), opp=opp, yr=int(yr[i])))
    return sigs


def sim_fixed(sg):
    e = sg["entry"]; s = sg["side"]; ext = sg["ext"]
    if s > 0:
        sl = ext * (1 - SL_PAD); dist = (e - sl) / e; tp = e * (1 + RR_FIXED * dist)
    else:
        sl = ext * (1 + SL_PAD); dist = (e - sl) / e * -1; tp = e * (1 - RR_FIXED * dist)
    win, ej = MA.walk(A, sg["i"], s, sl, tp, n)
    return (RR_FIXED * dist if win else -dist) - MA.FEE, ej, dist


def sim_opp(sg):
    e = sg["entry"]; s = sg["side"]; ext = sg["ext"]; opp = sg["opp"]
    if opp is None:
        return None
    if s > 0:
        sl = ext * (1 - SL_PAD); dist = (e - sl) / e; tp = opp; tpd = (tp - e) / e
    else:
        sl = ext * (1 + SL_PAD); dist = (sl - e) / e; tp = opp; tpd = (e - tp) / e
    if dist <= 0 or tpd < MIN_RR * dist:                                # min 1:1.2 R:R to enter
        return None
    win, ej = MA.walk(A, sg["i"], s, sl, tp, n)
    return (tpd if win else -dist) - MA.FEE, ej, dist


def run(sim, sigs):
    last = -1; rows = []; skipped = 0
    for sg in sigs:
        if sg["i"] <= last:
            continue
        r = sim(sg)
        if r is None:
            skipped += 1; continue
        net, ej, dist = r; last = ej
        rows.append(dict(net=net, side=sg["side"], yr=sg["yr"], dist=dist))
    return rows, skipped


def report(label, rows):
    n_ = len(rows)
    if n_ == 0:
        print("  %-14s n=0" % label); return
    nt = np.array([r["net"] for r in rows]); w = 100.0 * (nt > 0).sum() / n_
    tot = (np.prod(1 + nt) - 1) * 100
    gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum(); pf = (gg / ll) if ll > 0 else float("inf")
    bal = MA.account(list(nt)); pnl = bal - MA.B0
    print("  %-14s n=%4d  win %5.1f%%  net %+7.1f%%  PF %.2f  avgSL %.2f%%  END $%9.0f  P&L $%+9.0f (%+.1f%%)"
          % (label, n_, w, tot, pf, np.mean([r["dist"] for r in rows]) * 100, bal, pnl, pnl / MA.B0 * 100))


def main():
    sigs = gen_signals()
    nl = sum(1 for s in sigs if s["side"] > 0)
    print("=" * 104)
    print("%s ENGULF at S/R-indicator zones (fractal k=%d, area=pivot candle range)  |  %d %s buckets  |  %d levels (%dS/%dR)"
          % (TF, K, F["n"], TF, len(LEV), len(SUP), len(RES)))
    print("  raw signals %d (%dL/%dS). Entry c2 close, SL 0.1%% beyond c2 extreme. Account $%.0f @ 10%%x10, fee %.2f%%."
          % (len(sigs), nl, len(sigs) - nl, MA.B0, MA.FEE * 100))
    print("=" * 104)
    for name, sim in (("A) FIXED TP 1:1.2", sim_fixed), ("B) TARGET closest opposite S/R (min 1:1.2)", sim_opp)):
        rows, skipped = run(sim, sigs)
        print("--- %s%s ---" % (name, ("   [%d skipped: no opp S/R or <1:1.2]" % skipped) if skipped else ""))
        report("ALL", rows)
        report("LONG", [r for r in rows if r["side"] > 0])
        report("SHORT", [r for r in rows if r["side"] < 0])
        report("2025", [r for r in rows if r["yr"] == 2025])
        report("2026", [r for r in rows if r["yr"] == 2026])
        print()
    print("S/R = app.support_resistance fractal pivots (k=8), zone=pivot candle's high-low. Causal (confirmed+unbroken). 18mo recon.")


if __name__ == "__main__":
    main()
