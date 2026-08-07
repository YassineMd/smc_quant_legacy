"""How good are LATE breaks — ones that fire AFTER the NY close (current cutoff = 5h past the 5pm range end = 21 UTC)?
Raw-body 2-5pm range, adaptive TP, wide SL, ~2-day cap. Search the first 15m close beyond the range up to MAXH hours
after 16:00 UTC (cross-day capable). Bucket each break by how late it fired and report each band + cumulative, so we
can see if extending past the NY close adds edge or noise.  Recon 15m; DATA_ROOT=fwd.
Run: python study/ny_break_latebreak.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from collections import defaultdict
import numpy as np
import study.signal_search_lib as L
import study.mom_absorb_1h as MA

FEE = MA.FEE; CAP = 192; SL_PAD = 0.001; R0, R1 = 13, 16; TP_THR = 2.85; MAXH = float(os.environ.get("MAXH", "24"))
_DR = os.environ.get("DATA_ROOT", "")
if _DR:
    from study.archive_loader import load_archive
    A = [b for b in load_archive("15m", root=os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), _DR)))[1] if b.get("start_time")]
    A.sort(key=lambda b: b["start_time"])
    O = [float(b.get("open_price", 0) or 0) for b in A]; C = [float(b.get("close_price", 0) or 0) for b in A]
    H = [float(b.get("high", 0) or 0) for b in A]; Lo = [float(b.get("low", 0) or 0) for b in A]
    st = [float(b.get("start_time", 0) or 0) for b in A]; n = len(A)
else:
    F = L.load_features("15m"); A = F["A"]; O = F["o"]; C = F["c"]; H = F["h"]; Lo = F["l"]; st = [float(t) for t in F["start"]]; n = F["n"]

days = defaultdict(list)
for i in range(n):
    t = datetime.fromtimestamp(st[i], tz=timezone.utc)
    if t.weekday() >= 5:
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


rows = []                                                        # (net, hours_late, year)
for d, lst in days.items():
    win = [(h, i) for (h, i) in lst if R0 <= h < R1]
    if len(win) < 2:
        continue
    idx = [i for _, i in win]
    rhi = max(max(float(O[i]), float(C[i])) for i in idx); rlo = min(min(float(O[i]), float(C[i])) for i in idx)
    whi = max(float(H[i]) for i in idx); wlo = min(float(Lo[i]) for i in idx)
    if not (rhi > rlo) or not (whi > wlo):
        continue
    i1 = idx[-1]; t16 = datetime(d.year, d.month, d.day, R1, tzinfo=timezone.utc).timestamp(); tend = t16 + MAXH * 3600.0
    side = 0; bi = None
    for j in range(i1 + 1, n):                                   # first close beyond the range, up to MAXH hours after 5pm
        if st[j] >= tend:
            break
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
    mult = 2.0 if ((whi - wlo) / e * 100.0) < TP_THR else 0.5
    net = walk(bi, side, e, sl, e + side * mult * (whi - wlo))
    rows.append((net, (st[bi] - t16) / 3600.0, datetime.fromtimestamp(st[bi], tz=timezone.utc).year))


def rep(label, rs):
    if not rs:
        print("  %-26s n=0" % label); return
    nt = np.array([x[0] for x in rs]); tot = (np.prod(1 + nt) - 1) * 100
    gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum(); pf = gg / ll if ll > 0 else float("inf")
    t25 = (np.prod([1 + x for x, h, y in rs if y == 2025]) - 1) * 100
    t26 = (np.prod([1 + x for x, h, y in rs if y == 2026]) - 1) * 100
    print("  %-26s n=%3d  win %4.0f%%  net %+7.1f%%  PF %.2f  mean %+.3f%% | %+.1f%%/%+.1f%%"
          % (label, len(nt), 100 * np.mean(nt > 0), tot, pf, nt.mean() * 100, t25, t26))


print("=" * 100)
print("LATE breaks after the NY close | raw-body range, adaptive TP | 15m %s | total breaks<=%dh = %d"
      % ("DAEMON/fwd" if _DR else "recon", int(MAXH), len(rows)))
print("  (hours-late = time of break after 16:00 UTC / 5pm.  current strategy = <5h band only)")
print("=" * 100)
bands = [(0, 5, "0-5h  (<=9pm, CURRENT)"), (5, 8, "5-8h  (9pm-midnight)"), (8, 14, "8-14h (Asia overnight)"), (14, 24, "14-24h (next day)")]
for lo, hi, lab in bands:
    rep(lab, [r for r in rows if lo <= r[1] < hi])
print("-" * 100)
for cut in (5, 8, 14, 24):
    rep("CUMULATIVE <=%dh" % cut, [r for r in rows if r[1] < cut])
print("=" * 100)
