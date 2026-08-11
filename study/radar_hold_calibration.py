"""Is the wall's stated P(resist)% (the hover 'holds N%') CALIBRATED? Bin every 5m radar visit by the model's stated
P_resist and compare to the ACTUAL hold rate in that bin, both recon years. Well-calibrated => stated ~= actual on
the diagonal. Overconfident => actual < stated (esp. at the high end)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

A = sorted(load_archive("5m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
n = len(A)
print("5m bars=%d detecting walls..." % n, flush=True)
walls = AL.detect(A)

rows = []   # (year, pr, hold)
for w in walls:
    broken = bool(w.get("broken")); i1 = int(w.get("i1", n - 1))
    for (rk0, rk1, pr) in w.get("radar_runs", ()):
        rk0 = int(rk0); rk1 = int(rk1)
        if rk1 >= n - 1:
            continue
        hold = 0 if (broken and rk0 <= i1 <= rk1 + 2) else 1
        yr = datetime.fromtimestamp(_f(A[rk0].get("start_time")), tz=timezone.utc).year
        rows.append((yr, float(pr), hold))

pr = np.array([r[1] for r in rows]); hold = np.array([r[2] for r in rows]); yr = np.array([r[0] for r in rows])
print("visits=%d  overall stated mean=%.1f%%  actual hold=%.1f%%\n" % (len(rows), pr.mean(), 100 * hold.mean()), flush=True)

EDGES = [0, 55, 60, 65, 70, 75, 80, 85, 90, 101]
for tag, mask in (("BOTH", np.ones(len(rows), bool)), ("2025", yr == 2025), ("2026", yr == 2026)):
    print("[%s]  stated-bin      n     mean-stated   ACTUAL-hold   gap(actual-stated)" % tag, flush=True)
    for lo, hi in zip(EDGES, EDGES[1:]):
        m = mask & (pr >= lo) & (pr < hi)
        N = int(m.sum())
        if N < 30:
            continue
        ms = pr[m].mean(); ah = 100 * hold[m].mean()
        print("   %3d-%-3d %%      %5d     %5.1f%%       %5.1f%%       %+5.1f" % (lo, hi, N, ms, ah, ah - ms), flush=True)
    print("", flush=True)
