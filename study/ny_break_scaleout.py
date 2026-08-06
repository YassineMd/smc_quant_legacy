"""NY range-break 3-TP SCALE-OUT backtest vs the single 1/2-range TP baseline.
Entry = first 15m close beyond the 2-5pm body range. Wide SL past the range wick. TPs (x range) default 0.10/0.20/0.50,
1/3 closed at each. BE=1 -> SL to breakeven after TP1 (runner is risk-free); BE=0 -> keep the wide stop throughout.
Walk 15m adverse-first, ~2-day cap. fee 0.08% round-trip (once per trade). One trade/day.  Recon 15m; DATA_ROOT=fwd.
Run: python study/ny_break_scaleout.py     (TPS=0.1,0.2,0.5  BE=1)
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
TPS = [float(x) for x in os.environ.get("TPS", "0.1,0.2,0.5").split(",")]
BE = os.environ.get("BE", "1") == "1"
_DR = os.environ.get("DATA_ROOT", "")
if _DR:
    from study.archive_loader import load_archive
    A = [b for b in load_archive("15m", root=os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), _DR)))[1] if b.get("start_time")]
    A.sort(key=lambda b: b["start_time"])
    C = [float(b.get("close_price", 0) or 0) for b in A]; H = [float(b.get("high", 0) or 0) for b in A]
    Lo = [float(b.get("low", 0) or 0) for b in A]; startt = [float(b.get("start_time", 0) or 0) for b in A]; n = len(A)
else:
    F = L.load_features("15m"); A = F["A"]; C = F["c"]; H = F["h"]; Lo = F["l"]; startt = [float(t) for t in F["start"]]; n = F["n"]


def walk_single(bi, side, e, sl, tp):
    for j in range(bi + 1, min(n, bi + 1 + CAP)):
        hi = float(H[j]); lo = float(Lo[j])
        if (lo <= sl) if side > 0 else (hi >= sl):
            return side * (sl / e - 1) - FEE
        if (hi >= tp) if side > 0 else (lo <= tp):
            return side * (tp / e - 1) - FEE
    ke = min(n - 1, bi + CAP)
    return side * (float(C[ke]) / e - 1) - FEE


def walk_scaleout(bi, side, e, sl0, tps):
    remaining = 1.0; realized = 0.0; sl = sl0; leg = 0; fr = 1.0 / 3.0; hits = [0, 0, 0]; last = bi
    for j in range(bi + 1, min(n, bi + 1 + CAP)):
        last = j; hi = float(H[j]); lo = float(Lo[j])
        if (lo <= sl) if side > 0 else (hi >= sl):            # adverse-first: SL / BE hit -> close remaining
            realized += remaining * (side * (sl / e - 1)); remaining = 0.0; break
        while leg < 3 and ((hi >= tps[leg]) if side > 0 else (lo <= tps[leg])):
            realized += fr * (side * (tps[leg] / e - 1)); remaining -= fr; hits[leg] = 1; leg += 1
            if leg == 1 and BE:
                sl = e                                        # SL -> breakeven after TP1
        if leg >= 3 or remaining <= 1e-9:
            remaining = 0.0; break
    if remaining > 1e-9:
        realized += remaining * (side * (float(C[last]) / e - 1))
    return realized - FEE, hits


base = []; scal = []; allhits = []
for r in [r for r in RB.detect(A, hourly_range=True) if r["side"] != 0 and r["break_i"] is not None]:
    bi = int(r["break_i"]); side = int(r["side"]); e = float(r["entry"]); whi = float(r["whi"]); wlo = float(r["wlo"])
    rng = whi - wlo
    if e <= 0 or rng <= 0:
        continue
    sl0 = wlo * (1 - SL_PAD) if side > 0 else whi * (1 + SL_PAD)
    yr = datetime.fromtimestamp(startt[bi], tz=timezone.utc).year
    base.append((walk_single(bi, side, e, sl0, e + side * 0.5 * rng), yr))
    net, hits = walk_scaleout(bi, side, e, sl0, [e + side * k * rng for k in TPS])
    scal.append((net, yr)); allhits.append(hits)


def rep(label, rows):
    nt = np.array([x[0] for x in rows]); k = len(nt)
    tot = (np.prod(1 + nt) - 1) * 100; gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum()
    pf = gg / ll if ll > 0 else float("inf"); bal = MA.account(list(nt))
    t25 = (np.prod([1 + x for x, y in rows if y == 2025]) - 1) * 100
    t26 = (np.prod([1 + x for x, y in rows if y == 2026]) - 1) * 100
    shp = nt.mean() / nt.std() if nt.std() > 0 else 0.0
    print("  %-18s n=%d  win %5.1f%%  net %+7.1f%%  PF %.2f  mean %+.3f%%  Sharpe/tr %+.3f  | 2025 %+.1f%% 2026 %+.1f%%"
          % (label, k, 100 * np.mean(nt > 0), tot, pf, nt.mean() * 100, shp, t25, t26))


hits = np.array(allhits)
print("=" * 112)
print("NY RANGE-BREAK SCALE-OUT | TPs %s x range (1/3 each) | SL->BE after TP1: %s | 15m %s | n=%d"
      % (TPS, BE, "DAEMON/fwd" if _DR else "recon", len(scal)))
print("  TP hit-rates: TP1 %.0f%%  TP2 %.0f%%  TP3 %.0f%%" % tuple(100 * hits.mean(axis=0)))
print("=" * 112)
rep("SINGLE 1/2-range", base)
rep("3-TP SCALE-OUT", scal)
if scal:
    nt = np.array([x[0] for x in scal])
    rng_ = np.random.default_rng(20260806)
    mm = np.array([rng_.choice(nt, size=len(nt), replace=True).mean() for _ in range(10000)]) * 100
    lo, hi = np.percentile(mm, [2.5, 97.5])
    print("  scale-out bootstrap mean/trade %+.4f%%  95%% CI [%+.4f%%, %+.4f%%]" % (nt.mean() * 100, lo, hi))
print("=" * 112)
