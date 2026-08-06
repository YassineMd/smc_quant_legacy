"""Volatility filter for the NY range-break: only take breaks whose 2-5pm range% >= threshold.
Single 1/2-range TP, wide SL, ~2-day cap. Sweep the min-range threshold -> per-threshold n, % of trades kept,
compounded net, mean/trade (the quality metric), PF, win%, Sharpe/tr, 2025/2026. A good filter RAISES mean/tr & PF
while keeping most of the profit with far fewer trades.  Recon 15m; DATA_ROOT=fwd.
Run: python study/ny_break_volfilter.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
import study.signal_search_lib as L
import study.mom_absorb_1h as MA
from app import ny_rangebreak_detect as RB

FEE = MA.FEE; CAP = 192; SL_PAD = 0.001
_DR = os.environ.get("DATA_ROOT", "")
if _DR:
    from study.archive_loader import load_archive
    A = [b for b in load_archive("15m", root=os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), _DR)))[1] if b.get("start_time")]
    A.sort(key=lambda b: b["start_time"])
    C = [float(b.get("close_price", 0) or 0) for b in A]; H = [float(b.get("high", 0) or 0) for b in A]
    Lo = [float(b.get("low", 0) or 0) for b in A]; st = [float(b.get("start_time", 0) or 0) for b in A]; n = len(A)
else:
    F = L.load_features("15m"); A = F["A"]; C = F["c"]; H = F["h"]; Lo = F["l"]; st = [float(t) for t in F["start"]]; n = F["n"]


def walk(bi, side, e, sl, tp):
    for j in range(bi + 1, min(n, bi + 1 + CAP)):
        hi = float(H[j]); lo = float(Lo[j])
        if (lo <= sl) if side > 0 else (hi >= sl):
            return side * (sl / e - 1) - FEE
        if (hi >= tp) if side > 0 else (lo <= tp):
            return side * (tp / e - 1) - FEE
    ke = min(n - 1, bi + CAP)
    return side * (float(C[ke]) / e - 1) - FEE


rows = []
for r in [r for r in RB.detect(A, hourly_range=True) if r["side"] != 0 and r["break_i"] is not None]:
    bi = int(r["break_i"]); side = int(r["side"]); e = float(r["entry"]); whi = float(r["whi"]); wlo = float(r["wlo"])
    rng = whi - wlo
    if e <= 0 or rng <= 0:
        continue
    sl = wlo * (1 - SL_PAD) if side > 0 else whi * (1 + SL_PAD)
    net = walk(bi, side, e, sl, e + side * 0.5 * rng)
    rows.append((rng / e * 100.0, net, datetime.fromtimestamp(st[bi], tz=timezone.utc).year))

rows = np.array(rows)
tot0 = len(rows)
print("=" * 108)
print("NY RANGE-BREAK volatility filter (min 2-5pm range%%) | single 1/2-range TP | 15m %s | n=%d"
      % ("DAEMON/fwd" if _DR else "recon", tot0))
print("  %-9s %-11s %-9s %-9s %-6s %-6s %-9s  %s" % ("min-range", "trades", "net", "mean/tr", "PF", "win%", "Sharpe/tr", "2025 / 2026"))
print("=" * 108)
for thr in (0.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0):
    m = rows[:, 0] >= thr; nt = rows[m, 2 - 1]; yr = rows[m, 2]
    if len(nt) < 5:
        print("  >= %.1f%%   n=%d (too few)" % (thr, len(nt))); continue
    tot = (np.prod(1 + nt) - 1) * 100; gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum()
    pf = gg / ll if ll > 0 else float("inf"); shp = nt.mean() / nt.std() if nt.std() > 0 else 0.0
    t25 = (np.prod([1 + x for x, y in zip(nt, yr) if y == 2025]) - 1) * 100
    t26 = (np.prod([1 + x for x, y in zip(nt, yr) if y == 2026]) - 1) * 100
    print("  >= %.1f%%   %4d (%3.0f%%)  %+7.1f%%  %+.3f%%  %.2f  %4.0f%%  %+.3f     %+.1f%% / %+.1f%%"
          % (thr, len(nt), 100 * len(nt) / tot0, tot, nt.mean() * 100, pf, 100 * np.mean(nt > 0), shp, t25, t26))
print("=" * 108)
