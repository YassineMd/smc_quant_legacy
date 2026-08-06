"""Does 2-5pm VOLATILITY predict WIN vs LOSS (not just move size)?
For each NY range-break, take the actual single 1/2-range-TP trade (wide SL, ~2-day cap) and split trades by the
2-5pm volatility (range% and realized-vol) into terciles -> win rate, net, mean/trade per tercile. Also corr(vol, net).
If win rate / mean rises with vol -> volatility helps call a winning day.  Recon 15m; DATA_ROOT=fwd.
Run: python study/ny_break_volwin.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from collections import defaultdict
import numpy as np
from scipy import stats as _st
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

days = defaultdict(list)
for i in range(n):
    t = datetime.fromtimestamp(st[i], tz=timezone.utc)
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


rows = []
for r in [r for r in RB.detect(A, hourly_range=True) if r["side"] != 0 and r["break_i"] is not None]:
    bi = int(r["break_i"]); side = int(r["side"]); e = float(r["entry"]); whi = float(r["whi"]); wlo = float(r["wlo"])
    rng = whi - wlo
    if e <= 0 or rng <= 0:
        continue
    d = datetime.fromtimestamp(st[bi], tz=timezone.utc).date()
    win15 = [i for (h, i) in days[d] if 13 <= h < 16]
    cl = [float(C[i]) for i in win15]
    rets = [cl[k] / cl[k - 1] - 1.0 for k in range(1, len(cl)) if cl[k - 1] > 0]
    rvol = (np.std(rets) * 100.0) if rets else 0.0
    sl = wlo * (1 - SL_PAD) if side > 0 else whi * (1 + SL_PAD)
    net = walk(bi, side, e, sl, e + side * 0.5 * rng)
    rows.append((rng / e * 100.0, rvol, net))

rows = np.array(rows)
rngp, rvolv, net = rows[:, 0], rows[:, 1], rows[:, 2]
print("=" * 96)
print("Does 2-5pm VOLATILITY predict WIN/LOSS? | single 1/2-range TP | 15m %s | n=%d" % ("DAEMON/fwd" if _DR else "recon", len(rows)))
print("=" * 96)
for nm, v in (("range%", rngp), ("realized-vol", rvolv)):
    rho, p = _st.spearmanr(v, net)
    pb = _st.pointbiserialr((net > 0).astype(int), v)[0]
    print("  corr(%s, net): Spearman %+.3f (p=%.2f)   corr(%s, WIN): %+.3f" % (nm, rho, p, nm, pb))
print("  -- range%% terciles -> win rate / net / mean --")
q = np.quantile(rngp, [0, 1 / 3, 2 / 3, 1.0])
for lo, hi, lab in ((q[0], q[1], "low  vol"), (q[1], q[2], "mid  vol"), (q[2], q[3], "high vol")):
    m = (rngp >= lo) & (rngp <= hi); nt = net[m]
    tot = (np.prod(1 + nt) - 1) * 100
    print("    %-8s range %.2f-%.2f%%  n=%3d  win %5.1f%%  net %+7.1f%%  mean %+.3f%%"
          % (lab, lo, hi, m.sum(), 100 * np.mean(nt > 0), tot, nt.mean() * 100))
print("=" * 96)
