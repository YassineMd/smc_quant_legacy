"""On LOW-volatility days (bottom-third 2-5pm range%), does a DIFFERENT take-profit rescue the ~breakeven result?
Baseline single 1/2-range TP is ~flat on low-vol days. Sweep TP variants on that subset only (wide SL unchanged,
~2-day cap): TP = k x range for several k, and fixed-% TPs. Report net / mean / PF / win% / 2025 / 2026 per variant.
Recon 15m.  LOWQ env = the vol quantile cutoff (default 0.33 = bottom third).
Run: python study/ny_break_lowvol_tp.py
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
LOWQ = float(os.environ.get("LOWQ", "0.33"))
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


brks = []
for r in [r for r in RB.detect(A, hourly_range=True) if r["side"] != 0 and r["break_i"] is not None]:
    bi = int(r["break_i"]); side = int(r["side"]); e = float(r["entry"]); whi = float(r["whi"]); wlo = float(r["wlo"])
    rng = whi - wlo
    if e <= 0 or rng <= 0:
        continue
    brks.append((bi, side, e, whi, wlo, rng, rng / e * 100.0, datetime.fromtimestamp(st[bi], tz=timezone.utc).year))

thr = np.quantile([b[6] for b in brks], LOWQ)
low = [b for b in brks if b[6] <= thr]                                    # low-vol subset
print("=" * 100)
print("LOW-VOL days (2-5pm range%% <= %.2f%%, bottom %.0f%%) | vary TP, wide SL | 15m recon | n=%d"
      % (thr, LOWQ * 100, len(low)))
print("  %-18s  net       mean/tr   PF     win%%   2025 / 2026" % "take-profit")
print("=" * 100)


def rep(label, tp_fn):
    nt = []; yr = []
    for (bi, side, e, whi, wlo, rng, rp, y) in low:
        sl = wlo * (1 - SL_PAD) if side > 0 else whi * (1 + SL_PAD)
        tp = tp_fn(e, side, rng)
        nt.append(walk(bi, side, e, sl, tp)); yr.append(y)
    nt = np.array(nt)
    tot = (np.prod(1 + nt) - 1) * 100; gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum()
    pf = gg / ll if ll > 0 else float("inf")
    t25 = (np.prod([1 + x for x, yy in zip(nt, yr) if yy == 2025]) - 1) * 100
    t26 = (np.prod([1 + x for x, yy in zip(nt, yr) if yy == 2026]) - 1) * 100
    print("  %-18s  %+6.1f%%  %+.3f%%  %.2f  %4.0f%%   %+.1f%% / %+.1f%%"
          % (label, tot, nt.mean() * 100, pf, 100 * np.mean(nt > 0), t25, t26))


for k in (0.3, 0.5, 0.7, 1.0, 1.5, 2.0):
    rep("%.1f x range" % k, (lambda kk: (lambda e, side, rng: e + side * kk * rng))(k))
for pct in (0.3, 0.5, 0.8, 1.2, 1.6):
    rep("fixed %.1f%%" % pct, (lambda pp: (lambda e, side, rng: e * (1 + side * pp / 100.0)))(pct))
print("=" * 100)
