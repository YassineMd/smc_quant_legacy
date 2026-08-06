"""NY range-break with a VOLATILITY-ADAPTIVE take-profit, as one combined strategy.
Rule: TP = HIGH_MULT x range when the 2-5pm range% is HIGH, LOW_MULT x range when it is LOW (below the threshold).
Threshold THR is an absolute range% ; THR=0 -> use the median of the window's ranges (relative/robust). Wide SL,
~2-day cap. Compares the adaptive TP to the fixed 1/2-range baseline. Recon 15m; DATA_ROOT=fwd.
Run: python study/ny_break_adaptive.py     (THR=0 LOW_MULT=2.0 HIGH_MULT=0.5)
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
THR = float(os.environ.get("THR", "0")); LOW_MULT = float(os.environ.get("LOW_MULT", "2.0")); HIGH_MULT = float(os.environ.get("HIGH_MULT", "0.5"))
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


brks = []
for r in [r for r in RB.detect(A, hourly_range=True) if r["side"] != 0 and r["break_i"] is not None]:
    bi = int(r["break_i"]); side = int(r["side"]); e = float(r["entry"]); whi = float(r["whi"]); wlo = float(r["wlo"])
    rng = whi - wlo
    if e <= 0 or rng <= 0:
        continue
    brks.append((bi, side, e, whi, wlo, rng, rng / e * 100.0, datetime.fromtimestamp(st[bi], tz=timezone.utc).year))

thr = float(np.median([b[6] for b in brks])) if THR == 0 else THR


def run(mult_fn):
    nt = []; yr = []
    for (bi, side, e, whi, wlo, rng, rp, y) in brks:
        sl = wlo * (1 - SL_PAD) if side > 0 else whi * (1 + SL_PAD)
        nt.append(walk(bi, side, e, sl, e + side * mult_fn(rp) * rng)); yr.append(y)
    return np.array(nt), yr


def rep(label, nt, yr):
    tot = (np.prod(1 + nt) - 1) * 100; gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum()
    pf = gg / ll if ll > 0 else float("inf"); shp = nt.mean() / nt.std() if nt.std() > 0 else 0.0
    t25 = (np.prod([1 + x for x, y in zip(nt, yr) if y == 2025]) - 1) * 100
    t26 = (np.prod([1 + x for x, y in zip(nt, yr) if y == 2026]) - 1) * 100
    print("  %-24s net %+7.1f%%  win %4.0f%%  PF %.2f  mean %+.3f%%  Sharpe %+.3f  | 2025 %+.1f%% 2026 %+.1f%%"
          % (label, tot, 100 * np.mean(nt > 0), pf, nt.mean() * 100, shp, t25, t26))


print("=" * 112)
print("NY RANGE-BREAK adaptive TP | low-vol (range%% < %.2f%%) -> %.1fx range, else %.1fx | 15m %s | n=%d"
      % (thr, LOW_MULT, HIGH_MULT, "DAEMON/fwd" if _DR else "recon", len(brks)))
print("=" * 112)
nb, yb = run(lambda rp: 0.5)
rep("FIXED 0.5x range", nb, yb)
na, ya = run(lambda rp: LOW_MULT if rp < thr else HIGH_MULT)
rep("ADAPTIVE %.1f/%.1f" % (LOW_MULT, HIGH_MULT), na, ya)
if not _DR:
    mm = np.random.default_rng(20260806)
    boot = np.array([mm.choice(na, size=len(na), replace=True).mean() for _ in range(10000)]) * 100
    lo, hi = np.percentile(boot, [2.5, 97.5])
    print("  adaptive bootstrap mean/trade %+.4f%%  95%% CI [%+.4f%%, %+.4f%%]" % (na.mean() * 100, lo, hi))
print("=" * 112)
