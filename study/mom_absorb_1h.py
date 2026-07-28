"""1h MOMENTUM entry: two same-direction, heavily-absorbed candles with expanding move-magnitude.

LONG  (SHORT mirrors): candle i-1 and candle i BOTH up (down), BOTH heavily absorbed (absorption A >= 0.75 =
the terminal's 'heavy' shade, mirror of the 15mReasy R-easy A <= -0.75), and movmag(i) > movmag(i-1).
Entry = candle 2 (bar i) close.
Exit  = structural SL 0.1% beyond candle 2's low (long) / high (short); TP at 1:1 that stop distance.
Non-overlap taken(), 1h-bar fill (adverse-SL first). $200k @ 10% margin x10 = 100% notional, compounded, fee 0.08% RT.

Run: python study/mom_absorb_1h.py
"""
from __future__ import annotations
import os, sys, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import study.signal_search_lib as L

ABS_HEAVY = 0.75          # 'heavy absorb': absorption A >= this (both candles)
SL_PAD = 0.001            # stop 0.1% beyond candle 2's extreme
RR = 1.0                  # TP : SL = 1:1
FEE = 0.0008
B0 = 200_000.0; MARGIN_FRAC = 0.10; LEVERAGE = 10.0


def gen_signals(F):
    A = F["A"]; d = F["dir"]; a = F["absA"]; mm = F["movmag"]; n = F["n"]
    o = F["o"]; c = F["c"]; h = F["h"]; l = F["l"]
    sigs = []
    for i in range(1, n):
        if a[i] < ABS_HEAVY or a[i - 1] < ABS_HEAVY:            # both candles heavily absorbed
            continue
        if mm[i] <= mm[i - 1]:                                  # candle 2 move-magnitude must expand
            continue
        if d[i] == 1 and d[i - 1] == 1:
            side = 1
        elif d[i] == -1 and d[i - 1] == -1:
            side = -1
        else:
            continue                                            # need two SAME-direction candles
        body = abs(c[i] - o[i]); top = h[i] - max(o[i], c[i]); bot = min(o[i], c[i]) - l[i]
        if not (body > top and body > bot):                     # candle 2 NOT a doji: body dominates both wicks
            continue
        sigs.append(dict(i=i, side=side, entry=float(A[i]["c"]),
                         ext=float(A[i]["l"]) if side > 0 else float(A[i]["h"]),
                         yr=int(F["year"][i])))
    return sigs


def bracket(entry, side, ext):
    if side > 0:
        sl = ext * (1 - SL_PAD); dist = (entry - sl) / entry
        return sl, entry * (1 + RR * dist), dist
    sl = ext * (1 + SL_PAD); dist = (sl - entry) / entry
    return sl, entry * (1 - RR * dist), dist


def walk(A, i, side, sl, tp, n):
    for j in range(i + 1, n):
        hi = float(A[j]["h"]); lo = float(A[j]["l"])
        if (lo <= sl) if side > 0 else (hi >= sl):
            return False, j                                     # SL adverse-first
        if (hi >= tp) if side > 0 else (lo <= tp):
            return True, j
    return False, n - 1                                         # unresolved -> non-win


def taken(F, sigs):
    A = F["A"]; n = F["n"]; last = -1; rows = []
    for sg in sigs:
        i = sg["i"]
        if i <= last:
            continue                                            # non-overlap
        sl, tp, dist = bracket(sg["entry"], sg["side"], sg["ext"])
        win, ej = walk(A, i, sg["side"], sl, tp, n); last = ej
        net = (RR * dist if win else -dist) - FEE
        rows.append(dict(net=net, side=sg["side"], yr=sg["yr"], dist=dist))
    return rows


def account(nets):
    bal = B0
    for x in nets:
        bal *= (1.0 + x)
        if bal <= 0:
            return 0.0
    return bal


def report(label, rows):
    n = len(rows)
    if n == 0:
        print("  %-14s n=0" % label); return
    nt = np.array([r["net"] for r in rows])
    w = 100.0 * (nt > 0).sum() / n
    tot = (np.prod(1 + nt) - 1) * 100
    gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum(); pf = (gg / ll) if ll > 0 else float("inf")
    dist = np.mean([r["dist"] for r in rows]) * 100
    bal = account(list(nt)); pnl = bal - B0
    print("  %-14s n=%4d  win %5.1f%%  net %+6.1f%%  PF %.2f  avgSL %.2f%%  END $%9.0f  P&L $%+9.0f (%+.1f%%)"
          % (label, n, w, tot, pf, dist, bal, pnl, pnl / B0 * 100))


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "1h"
    F = L.load_features(tf)
    sigs = gen_signals(F)
    nl = sum(1 for s in sigs if s["side"] > 0)
    rows = taken(F, sigs)
    print("=" * 104)
    print("%s MOMENTUM (2x heavy-absorb same-dir + expanding movmag + candle2 non-doji)  |  %d %s buckets, %s -> %s"
          % (tf, F["n"], tf, L._dtu(F["start"][0]).strftime("%Y-%m-%d"), L._dtu(F["start"][-1]).strftime("%Y-%m-%d")))
    print("Entry candle-2 close. SL 0.1%% beyond candle-2 extreme, TP 1:1. Account $%.0f @ 10%%x10, fee %.2f%%. absorb>=%.2f."
          % (B0, FEE * 100, ABS_HEAVY))
    print("  raw signals %d (%dL/%dS) -> %d taken (non-overlap)" % (len(sigs), nl, len(sigs) - nl, len(rows)))
    print("=" * 104)
    report("ALL", rows)
    report("LONG", [r for r in rows if r["side"] > 0])
    report("SHORT", [r for r in rows if r["side"] < 0])
    report("2025", [r for r in rows if r["yr"] == 2025])
    report("2026", [r for r in rows if r["yr"] == 2026])
    print("-" * 104)
    print("'heavy absorb R' = absorption A >= %.2f (both candles). 1h-bar fill, adverse-first. Reconstruction (18mo)." % ABS_HEAVY)


if __name__ == "__main__":
    main()
