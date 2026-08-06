"""Range = the 2-5pm VOLUME-PROFILE value area (VAH/VAL) instead of body high/low. Aggregate the 13-16 UTC footprint,
take VAH/VAL (swing_lvn_detect.va_lines_from_profile, same as the terminal's VP zones); break = first 15m close beyond
VAH (long) / VAL (short) 16-21 UTC. Wide SL past the 2-5pm wick, ~2-day cap. Reports fixed 1/2-range, adaptive, and
0.4% TP vs the body-range baseline. Recon 15m; DATA_ROOT=fwd.
Run: python study/ny_break_va.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from collections import defaultdict
import numpy as np
import study.signal_search_lib as L
import study.mom_absorb_1h as MA
from app import swing_lvn_detect as SVL

FEE = MA.FEE; CAP = 192; SL_PAD = 0.001; R0, R1, BE = 13, 16, 21; TP_THR = 2.85
AGG0 = int(os.environ.get("AGG_START", "13"))               # VA aggregation start UTC hour: 13 = 2-5pm only ; 0 = Tokyo+London+2-5pm NY
SESSION_SL = os.environ.get("SESSION_SL", "0") == "1"        # SL at the NY-session high/low up to the break (causal) vs the agg-window wick
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


half = []; adap = []; f04 = []; nbreak = 0
for d, lst in days.items():
    win = [(h, i) for (h, i) in lst if AGG0 <= h < R1]        # aggregate the footprint over [AGG0, 16) UTC
    if len(win) < 2:
        continue
    prof = {}
    for (h, i) in win:
        for ps, vv in (A[i].get("levels") or {}).items():
            try:
                p = float(ps)
            except (TypeError, ValueError):
                continue
            r = prof.get(p)
            if r is None:
                r = {"b": 0.0, "s": 0.0}; prof[p] = r
            r["b"] += float(vv.get("b", 0.0) or 0.0); r["s"] += float(vv.get("s", 0.0) or 0.0)
    if len(prof) < 3:
        continue
    try:
        va = SVL.va_lines_from_profile(prof)
    except Exception:
        continue
    if not va or va.get("vah") is None or va.get("val") is None:
        continue
    rhi = float(va["vah"]); rlo = float(va["val"])
    idx = [i for _, i in win]
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
    nbreak += 1
    e = float(C[bi])
    if SESSION_SL:                                            # SL at the NY-session high/low from 13 UTC up to the break
        sess = [k for (h, k) in lst if h >= R0 and k <= bi]
        shi = max(float(H[k]) for k in sess); slo = min(float(Lo[k]) for k in sess)
        sl = slo * (1 - SL_PAD) if side > 0 else shi * (1 + SL_PAD)
    else:
        sl = wlo * (1 - SL_PAD) if side > 0 else whi * (1 + SL_PAD)
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
    print("  %-16s n=%3d  win %4.0f%%  net %+7.1f%%  PF %.2f  mean %+.3f%% | %+.1f%%/%+.1f%%"
          % (label, len(nt), 100 * np.mean(nt > 0), tot, pf, nt.mean() * 100, t25, t26))


print("=" * 96)
print("VP VAH/VAL breaks | VA agg %d-16UTC (%s) | SL=%s | 2-day cap | 15m %s | breaks=%d"
      % (AGG0, "Tokyo+London+2-5pmNY" if AGG0 == 0 else "2-5pm only",
         "session hi/lo" if SESSION_SL else "agg-window wick", "DAEMON/fwd" if _DR else "recon", nbreak))
print("  (reference body-range: 1/2 TP +107.5%% | adaptive +192.0%% | 0.4%% +8.2%%)")
print("=" * 96)
rep("fixed 1/2-range", half)
rep("adaptive TP", adap)
rep("fixed 0.4%", f04)
print("=" * 96)
