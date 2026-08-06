"""Compare the 15m RANGE definition: CLOCK-HOURLY (current overlay, hourly_range=True) vs RAW 15m BODY range
(hourly_range=False -> box hugs the actual 15m candle bodies). Both on 15m data, wide SL, ~2-day cap. For each,
report the fixed 1/2-range TP and the shipped volatility-adaptive TP. Recon 15m; DATA_ROOT=fwd.
Run: python study/ny_break_rangedef.py
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


def rep(label, rows):
    nt = np.array([x[0] for x in rows])
    if len(nt) == 0:
        print("  %-30s n=0" % label); return
    tot = (np.prod(1 + nt) - 1) * 100; gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum()
    pf = gg / ll if ll > 0 else float("inf"); shp = nt.mean() / nt.std() if nt.std() > 0 else 0.0
    t25 = (np.prod([1 + x for x, y in rows if y == 2025]) - 1) * 100
    t26 = (np.prod([1 + x for x, y in rows if y == 2026]) - 1) * 100
    print("  %-30s n=%3d  win %4.0f%%  net %+7.1f%%  PF %.2f  mean %+.3f%%  Sh %+.3f | %+.1f%%/%+.1f%%"
          % (label, len(nt), 100 * np.mean(nt > 0), tot, pf, nt.mean() * 100, shp, t25, t26))


print("=" * 116)
print("15m RANGE DEFINITION: clock-hourly (current) vs RAW 15m body | wide SL, 2-day cap | 15m %s"
      % ("DAEMON/fwd" if _DR else "recon"))
print("=" * 116)
for hourly, name in ((True, "clock-hourly (CURRENT)"), (False, "RAW 15m body (box hugs bodies)")):
    brks = [r for r in RB.detect(A, hourly_range=hourly) if r["side"] != 0 and r["break_i"] is not None]
    medrng = np.median([(float(r["whi"]) - float(r["wlo"])) / float(r["entry"]) * 100 for r in brks if r["entry"] > 0])
    fixed = []; adapt = []
    for r in brks:
        bi = int(r["break_i"]); side = int(r["side"]); e = float(r["entry"]); whi = float(r["whi"]); wlo = float(r["wlo"])
        rng = whi - wlo
        if e <= 0 or rng <= 0:
            continue
        sl = wlo * (1 - SL_PAD) if side > 0 else whi * (1 + SL_PAD)
        y = datetime.fromtimestamp(st[bi], tz=timezone.utc).year
        fixed.append((walk(bi, side, e, sl, e + side * 0.5 * rng), y))
        adapt.append((walk(bi, side, e, sl, float(r["tp"])), y))          # detector's shipped adaptive TP
    print("-- %s | breaks=%d  median range %.2f%% --" % (name, len(brks), medrng))
    rep("  fixed 1/2-range TP", fixed)
    rep("  adaptive TP (shipped)", adapt)
print("=" * 116)
