"""Range-break on WEEKENDS (no NY session -> placebo/robustness). Raw 15m BODY range in the 13-16 UTC window, first
15m close beyond it 16-21 UTC, wide SL past wick, ~2-day cap. Reports fixed 1/2-range, adaptive (2x low-vol/0.5x
high-vol at 2.85% range%), and fixed 0.4% TP. WEEKEND=1 keeps ONLY Sat/Sun; WEEKEND=0 = weekdays (reference).
Run: WEEKEND=1 python study/ny_break_weekend.py   /   WEEKEND=0 ...
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from collections import defaultdict
import numpy as np
import study.signal_search_lib as L
import study.mom_absorb_1h as MA

FEE = MA.FEE; CAP = 192; SL_PAD = 0.001; R0, R1, BE = 13, 16, 21; TP_THR = 2.85
WKND = os.environ.get("WEEKEND", "1") == "1"
F = L.load_features("15m"); O = F["o"]; C = F["c"]; H = F["h"]; Lo = F["l"]; st = [float(t) for t in F["start"]]; n = F["n"]

days = defaultdict(list)
for i in range(n):
    t = datetime.fromtimestamp(st[i], tz=timezone.utc)
    if (t.weekday() >= 5) != WKND:                             # WEEKEND=1 keeps Sat/Sun ; else weekdays
        continue
    days[t.date()].append((t.hour, i))
for d in days:
    days[d].sort(key=lambda z: z[1])


def walk(bi, side, e, sl, tp):
    for j in range(bi + 1, min(n, bi + 1 + CAP)):
        hi = float(H[j]); lo = float(Lo[j])
        if (lo <= sl) if side > 0 else (hi >= sl):
            return side * (sl / e - 1) - FEE
        if (hi >= tp) if side > 0 else (lo <= tp):
            return side * (tp / e - 1) - FEE
    ke = min(n - 1, bi + CAP)
    return side * (float(C[ke]) / e - 1) - FEE


half = []; adap = []; f04 = []
for d, lst in days.items():
    win = [(h, i) for (h, i) in lst if R0 <= h < R1]
    if len(win) < 2:
        continue
    idx = [i for _, i in win]
    rhi = max(max(float(O[i]), float(C[i])) for i in idx); rlo = min(min(float(O[i]), float(C[i])) for i in idx)
    whi = max(float(H[i]) for i in idx); wlo = min(float(Lo[i]) for i in idx)
    if not (rhi > rlo) or not (whi > wlo):
        continue
    i1 = idx[-1]; side = 0; bi = None
    for (h, j) in lst:
        if j <= i1 or not (R1 <= h < BE):
            continue
        c = float(C[j])
        if c > rhi:
            side = 1; bi = j; break
        if c < rlo:
            side = -1; bi = j; break
    if bi is None:
        continue
    e = float(C[bi]); sl = wlo * (1 - SL_PAD) if side > 0 else whi * (1 + SL_PAD)
    if (side > 0 and sl >= e) or (side < 0 and sl <= e):
        continue
    y = datetime.fromtimestamp(st[bi], tz=timezone.utc).year
    mult = 2.0 if ((whi - wlo) / e * 100.0) < TP_THR else 0.5
    half.append((walk(bi, side, e, sl, e + side * 0.5 * (whi - wlo)), y))
    adap.append((walk(bi, side, e, sl, e + side * mult * (whi - wlo)), y))
    f04.append((walk(bi, side, e, sl, e * (1 + side * 0.004)), y))


def rep(label, rows):
    nt = np.array([x[0] for x in rows])
    if len(nt) == 0:
        print("  %-16s n=0" % label); return
    tot = (np.prod(1 + nt) - 1) * 100; gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum()
    pf = gg / ll if ll > 0 else float("inf")
    t25 = (np.prod([1 + x for x, y in rows if y == 2025]) - 1) * 100
    t26 = (np.prod([1 + x for x, y in rows if y == 2026]) - 1) * 100
    ns = sum(1 for x, y in [(r[0], 0) for r in rows]) and 0
    print("  %-16s n=%3d  win %4.0f%%  net %+7.1f%%  PF %.2f  mean %+.3f%% | %+.1f%%/%+.1f%%"
          % (label, len(nt), 100 * np.mean(nt > 0), tot, pf, nt.mean() * 100, t25, t26))


nsh = sum(1 for r in adap if False)
print("=" * 92)
print("Range-break on %s | raw 15m body range | 15m recon | days=%d" % ("WEEKENDS (Sat/Sun)" if WKND else "WEEKDAYS", len(half)))
print("=" * 92)
rep("fixed 1/2-range", half)
rep("adaptive TP", adap)
rep("fixed 0.4%", f04)
print("=" * 92)
