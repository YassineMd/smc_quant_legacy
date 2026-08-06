"""OPEN-anchored range: from the 2-5pm OPEN, measure up = (highest body close - open) and down = (open - lowest body
close). Two half-ranges -> take the BIGGER (MODE=big) or SMALLER (MODE=small); the range band is symmetric around the
open: rhi = open + R, rlo = open - R. Break = first 15m close beyond the band after 5pm. Wide SL past the 2-5pm wick,
TP = 1/2 the wick range, ~2-day cap. Compares to the raw-body baseline. Recon 15m; DATA_ROOT=fwd.
Run: MODE=big python study/ny_break_openrange.py   /   MODE=small ...
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from collections import defaultdict
import numpy as np
import study.signal_search_lib as L
import study.mom_absorb_1h as MA

FEE = MA.FEE; CAP = 192; SL_PAD = 0.001; R0, R1, BE = 13, 16, 21
MODE = os.environ.get("MODE", "big")
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


def backtest(mode):
    rows = []
    for d, lst in days.items():
        win = [(h, i) for (h, i) in lst if R0 <= h < R1]
        if len(win) < 2:
            continue
        idx = [i for _, i in win]
        o0 = float(O[idx[0]]); hc = max(float(C[i]) for i in idx); lc = min(float(C[i]) for i in idx)
        whi = max(float(H[i]) for i in idx); wlo = min(float(Lo[i]) for i in idx)
        up = hc - o0; dn = o0 - lc
        if mode in ("big", "small"):                             # SYMMETRIC band around the open
            R = max(abs(up), abs(dn)) if mode == "big" else min(abs(up), abs(dn))
            if R <= 0:
                continue
            rhi = o0 + R; rlo = o0 - R
        else:                                                    # ASYMMETRIC: the OPEN is one boundary
            if up <= 0 or dn <= 0:
                continue                                         # need the open strictly inside the close range
            wider_is_up = up >= dn
            take_up = wider_is_up if mode == "abig" else (not wider_is_up)
            rlo, rhi = (o0, hc) if take_up else (lc, o0)         # [open, highest close] or [lowest close, open]
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
        tp = e + side * 0.5 * (whi - wlo)
        rows.append((walk(bi, side, e, sl, tp), datetime.fromtimestamp(st[bi], tz=timezone.utc).year))
    return rows


def rep(label, rows):
    nt = np.array([x[0] for x in rows])
    if len(nt) == 0:
        print("  %-22s n=0" % label); return
    tot = (np.prod(1 + nt) - 1) * 100; gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum()
    pf = gg / ll if ll > 0 else float("inf"); shp = nt.mean() / nt.std() if nt.std() > 0 else 0.0
    t25 = (np.prod([1 + x for x, y in rows if y == 2025]) - 1) * 100
    t26 = (np.prod([1 + x for x, y in rows if y == 2026]) - 1) * 100
    print("  %-22s n=%3d  win %4.0f%%  net %+7.1f%%  PF %.2f  mean %+.3f%%  Sh %+.3f | %+.1f%%/%+.1f%%"
          % (label, len(nt), 100 * np.mean(nt > 0), tot, pf, nt.mean() * 100, shp, t25, t26))


print("=" * 108)
print("OPEN-anchored symmetric range (open +/- R) | wide SL, TP 1/2 wick range, 2-day cap | 15m %s"
      % ("DAEMON/fwd" if _DR else "recon"))
print("=" * 108)
print("  -- SYMMETRIC band (open +/- R) --")
rep("BIGGEST (open +/- max)", backtest("big"))
rep("SMALLEST (open +/- min)", backtest("small"))
print("  -- ASYMMETRIC (open is one boundary): [open,highClose] or [lowClose,open] --")
rep("BIGGEST asym", backtest("abig"))
rep("SMALLEST asym", backtest("asmall"))
print("  (reference: raw-body range fixed-1/2 TP = +107.5%% recon / clock-hourly = +140.9%%)")
print("=" * 108)
