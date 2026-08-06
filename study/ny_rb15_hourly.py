"""NY Range-break on 15m with a CLOCK-HOURLY-CLOSE range (self-contained faithful approx of the 1h-close range).
Range: among 15m buckets in 13-16 UTC, take each clock hour's (13/14/15) LAST close -> rhi/rlo = max/min of those
hourly closes (narrow, like the validated 1h-close range); whi/wlo = max/min high/low over the window (tf-invariant).
Break = first 15m close beyond in 16-21 UTC. SL 0.1% past the wick, TP = 1/2 the wick range. Walk 15m to EoD.
One trade/day. fee 0.08%.
Run: python study/ny_rb15_hourly.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from collections import defaultdict
import numpy as np
import study.signal_search_lib as L
import study.mom_absorb_1h as MA

rng = np.random.default_rng(20260806)
F = L.load_features("15m"); FEE = MA.FEE
A = F["A"]; H = F["h"]; Lo = F["l"]; C = F["c"]; O = F["o"]; st = [float(t) for t in F["start"]]; n = F["n"]
R0, R1, BRK_END = 13, 16, 21
SL_PAD = 0.001; TP_RANGE = 0.5

days = defaultdict(list)
for i in range(n):
    t = datetime.fromtimestamp(st[i], tz=timezone.utc)
    if t.weekday() >= 5:
        continue
    days[t.date()].append((t.hour, i))

rows = []
for d, lst in sorted(days.items()):
    lst.sort(key=lambda z: z[1])
    win = [(h, i) for (h, i) in lst if R0 <= h < R1]
    if len(win) < 2:
        continue
    hourly_close = {}                                          # clock hour -> last 15m close in it (mimics a 1h close)
    for h, i in win:
        hourly_close[h] = float(C[i])
    if len(hourly_close) < 2:
        continue
    rhi = max(hourly_close.values()); rlo = min(hourly_close.values())
    whi = max(float(H[i]) for _, i in win); wlo = min(float(Lo[i]) for _, i in win)
    if not (rhi > rlo) or not (whi > wlo):
        continue
    i1 = win[-1][1]
    side = 0; bi = None
    for h, j in lst:
        if j <= i1 or not (R1 <= h < BRK_END):
            continue
        c = float(C[j])
        if c > rhi:
            side = 1; bi = j; break
        if c < rlo:
            side = -1; bi = j; break
    if bi is None:
        continue
    e = float(C[bi]); sl = wlo * (1 - SL_PAD) if side > 0 else whi * (1 + SL_PAD)
    tp = e + side * TP_RANGE * (whi - wlo); yr = datetime.fromtimestamp(st[bi], tz=timezone.utc).year
    net = None; jlast = None
    for j in range(bi + 1, n):
        if datetime.fromtimestamp(st[j], tz=timezone.utc).date() != d:
            break
        jlast = j
        hi = float(H[j]); lo = float(Lo[j])
        if (lo <= sl) if side > 0 else (hi >= sl):
            net = side * (sl / e - 1.0) - FEE; break
        if (hi >= tp) if side > 0 else (lo <= tp):
            net = side * (tp / e - 1.0) - FEE; break
    if net is None:
        px = float(C[jlast]) if jlast is not None else e
        net = side * (px / e - 1.0) - FEE
    rows.append(dict(net=net, side=side, yr=yr, win=net > 0))


def rep(label, rs):
    k = len(rs)
    if k == 0:
        print("  %-10s n=0" % label); return
    nt = np.array([r["net"] for r in rs]); w = 100.0 * sum(r["win"] for r in rs) / k
    tot = (np.prod(1 + nt) - 1) * 100; gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum()
    pf = (gg / ll) if ll > 0 else float("inf"); bal = MA.account(list(nt))
    print("  %-10s n=%4d  win %5.1f%%  net %+8.1f%%  PF %.2f  mean %+.3f%%  END $%10.0f (%+.1f%%)"
          % (label, k, w, tot, pf, nt.mean() * 100, bal, (bal - MA.B0) / MA.B0 * 100))


print("=" * 104)
print("NY RANGE-BREAK 15m + CLOCK-HOURLY-CLOSE range | wide SL past wick | TP 1/2 range | n=%d" % len(rows))
print("=" * 104)
rep("ALL", rows); rep("LONG", [r for r in rows if r["side"] > 0]); rep("SHORT", [r for r in rows if r["side"] < 0])
rep("2025", [r for r in rows if r["yr"] == 2025]); rep("2026", [r for r in rows if r["yr"] == 2026])
if rows:
    nt = np.array([r["net"] for r in rows])
    mm = np.array([rng.choice(nt, size=len(nt), replace=True).mean() for _ in range(10000)]) * 100
    lo, hi = np.percentile(mm, [2.5, 97.5])
    print("  bootstrap mean net/trade %+.4f%%  95%% CI [%+.4f%%, %+.4f%%]  -> %s"
          % (nt.mean() * 100, lo, hi, "clears 0" if lo > 0 else ("sig NEGATIVE" if hi < 0 else "includes 0")))
print("=" * 104)
