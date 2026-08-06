"""Verify the NY Range-break detector on 15m buckets (the overlay's 15m-native mode) still holds the edge.
Range/break/SL/TP come straight from app/ny_rangebreak_detect on the 15m recon set (range = 15m closes 13-16 UTC,
break = first 15m close beyond in 16-21 UTC, SL 0.1% past the 15m wick, TP = 1/2 the wick range). Walk 15m to EoD,
adverse-first. One trade/day. fee 0.08%.
Run: python study/ny_rb15_check.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
import study.signal_search_lib as L
import study.mom_absorb_1h as MA
from app import ny_rangebreak_detect as RB

rng = np.random.default_rng(20260806)
F = L.load_features("15m"); FEE = MA.FEE
A = F["A"]; H = F["h"]; Lo = F["l"]; C = F["c"]; st = [float(t) for t in F["start"]]; n = F["n"]

ranges = [r for r in RB.detect(A) if r["side"] != 0 and r["break_i"] is not None]
rows = []
for r in ranges:
    bi = int(r["break_i"]); side = int(r["side"]); e = float(r["entry"]); sl = float(r["sl"]); tp = float(r["tp"])
    if e <= 0:
        continue
    d0 = datetime.fromtimestamp(st[bi], tz=timezone.utc).date(); yr = datetime.fromtimestamp(st[bi], tz=timezone.utc).year
    net = None; jlast = None
    for j in range(bi + 1, n):
        if datetime.fromtimestamp(st[j], tz=timezone.utc).date() != d0:    # exit at end of the UTC day
            break
        jlast = j
        hi = float(H[j]); lo = float(Lo[j])
        if (lo <= sl) if side > 0 else (hi >= sl):                          # adverse-first
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
print("NY RANGE-BREAK on 15m buckets (15m-native range + 15m break) | wide SL past wick | TP 1/2 range | n=%d" % len(rows))
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
