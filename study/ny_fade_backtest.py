"""NY-morning FADE backtest. Theory: a STRONG NY-open trend reverses in the afternoon.
Each UTC weekday: morning move r1 = NY open (OPEN_H) -> PIVOT hour (close-to-close). If |r1| >= MIN, FADE at the
pivot (SHORT if the morning went up / LONG if down). Exit: SL/TP walked on the 1h buckets pivot->EoD, else the
EoD close. SL/TP=0 -> just HOLD to the EoD close. Non-overlap (one trade/day). fee 0.08%/rt.
Run: MIN=2 PIVOT=18 SL=1.5 TP=1.5 python study/ny_fade_backtest.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from collections import defaultdict
import numpy as np
import study.signal_search_lib as L
import study.mom_absorb_1h as MA

rng = np.random.default_rng(20260806)
F = L.load_features("1h")
n = F["n"]; O = F["o"]; C = F["c"]; H = F["h"]; Lo = F["l"]; start = [float(t) for t in F["start"]]
FEE = MA.FEE
OPEN_H = int(os.environ.get("OPEN_H", "13")); PIVOT = int(os.environ.get("PIVOT", "18")); END_H = int(os.environ.get("END_H", "23"))
MIN = float(os.environ.get("MIN", "2")) / 100.0
SL = float(os.environ.get("SL", "0")) / 100.0; TP = float(os.environ.get("TP", "0")) / 100.0

days = defaultdict(list)
for i in range(n):
    t = datetime.fromtimestamp(start[i], tz=timezone.utc)
    if t.weekday() >= 5:
        continue
    days[t.date()].append((t.hour, i))

rows = []
for d, bks in days.items():
    bks.sort(key=lambda b: b[1])
    ny = [b for b in bks if OPEN_H <= b[0] <= END_H]
    if not ny:
        continue
    piv = [b for b in ny if b[0] <= PIVOT]; end = [b for b in ny if b[0] <= END_H]
    if not piv or not end:
        continue
    i_open = ny[0][1]; i_piv = piv[-1][1]; i_end = end[-1][1]
    if i_piv <= i_open or i_end <= i_piv:
        continue
    po = O[i_open]; e = C[i_piv]
    if po <= 0 or e <= 0:
        continue
    r1 = e / po - 1.0
    if abs(r1) < MIN:                                        # only clear morning trends
        continue
    side = -1 if r1 > 0 else 1                               # FADE the morning move
    yr = datetime.fromtimestamp(start[i_open], tz=timezone.utc).year
    if SL > 0 and TP > 0:
        tp = e * (1 + side * TP); sl = e * (1 - side * SL)   # short: SL above / TP below; long: mirror
        net = None
        for j in range(i_piv + 1, i_end + 1):
            hi = float(H[j]); lo = float(Lo[j])
            sl_hit = (hi >= sl) if side < 0 else (lo <= sl)  # SL adverse-first
            tp_hit = (lo <= tp) if side < 0 else (hi >= tp)
            if sl_hit:
                net = -SL - FEE; break
            if tp_hit:
                net = TP - FEE; break
        if net is None:
            net = side * (C[i_end] / e - 1.0) - FEE          # unresolved by EoD -> exit at the close
    else:
        net = side * (C[i_end] / e - 1.0) - FEE              # HOLD to the EoD close
    rows.append(dict(net=net, side=side, yr=yr, win=net > 0, r1=r1))


def rep(label, rs):
    k = len(rs)
    if k == 0:
        print("  %-10s n=0" % label); return
    nt = np.array([r["net"] for r in rs]); w = 100.0 * sum(r["win"] for r in rs) / k
    tot = (np.prod(1 + nt) - 1) * 100; gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum()
    pf = (gg / ll) if ll > 0 else float("inf"); bal = MA.account(list(nt))
    print("  %-10s n=%4d  win %5.1f%%  net %+7.1f%%  PF %.2f  mean %+.3f%%  END $%9.0f (%+.1f%%)"
          % (label, k, w, tot, pf, nt.mean() * 100, bal, (bal - MA.B0) / MA.B0 * 100))


exit_desc = ("HOLD to EoD" if not (SL > 0 and TP > 0) else "SL %.1f%% / TP %.1f%%" % (SL * 100, TP * 100))
print("=" * 104)
print("NY-MORNING FADE | open %dUTC -> pivot %dUTC (fade if |move|>=%.0f%%) -> exit %s -> EoD %dUTC | weekdays | n=%d"
      % (OPEN_H, PIVOT, MIN * 100, exit_desc, END_H, len(rows)))
print("=" * 104)
rep("ALL", rows); rep("LONG", [r for r in rows if r["side"] > 0]); rep("SHORT", [r for r in rows if r["side"] < 0])
rep("2025", [r for r in rows if r["yr"] == 2025]); rep("2026", [r for r in rows if r["yr"] == 2026])
if rows:
    nt = np.array([r["net"] for r in rows])
    mm = np.array([rng.choice(nt, size=len(nt), replace=True).mean() for _ in range(10000)]) * 100
    lo, hi = np.percentile(mm, [2.5, 97.5])
    print("  bootstrap mean net/trade %+.4f%%  95%% CI [%+.4f%%, %+.4f%%]  -> %s  | avg morning move %.2f%%"
          % (nt.mean() * 100, lo, hi, "clears 0" if lo > 0 else ("sig NEGATIVE" if hi < 0 else "includes 0"),
             float(np.mean([abs(r["r1"]) for r in rows])) * 100))
print("=" * 104)
