"""Does the 2-5pm (range-window) VOLATILITY predict how far price MOVES after the break?
For each NY range-break (15m), measure the range-window volatility three ways and the post-break excursion:
  vol dims (13-16 UTC): range% = (whi-wlo)/entry ; rvol = std of 15m close-returns ; path = sum |15m hi-lo|/entry
  post-break MOVE: MFE% = max FAVOURABLE excursion after the break to end of the UTC day (the run you could capture)
Report Spearman corr(vol, MFE), a range%-tercile table (median MFE per tercile), and the MFE/range ratio (is the
1/2-range TP well-sized?).  Also whether MFE reaches the 1/2-range TP.  Recon 15m; DATA_ROOT=study/archive_data for fwd.
Run: python study/ny_break_volmove.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from collections import defaultdict
import numpy as np
from scipy import stats as _st
import study.signal_search_lib as L
from app import ny_rangebreak_detect as RB

_DR = os.environ.get("DATA_ROOT", "")
if _DR:
    from study.archive_loader import load_archive
    raws = [b for b in load_archive("15m", root=os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), _DR)))[1] if b.get("start_time")]
    raws.sort(key=lambda b: b["start_time"])
    O = [float(b.get("open_price", 0) or 0) for b in raws]; C = [float(b.get("close_price", 0) or 0) for b in raws]
    H = [float(b.get("high", 0) or 0) for b in raws]; Lo = [float(b.get("low", 0) or 0) for b in raws]
    st = [float(b.get("start_time", 0) or 0) for b in raws]; A = raws; n = len(raws)
else:
    F = L.load_features("15m"); A = F["A"]; O = F["o"]; C = F["c"]; H = F["h"]; Lo = F["l"]
    st = [float(t) for t in F["start"]]; n = F["n"]

days = defaultdict(list)
for i in range(n):
    t = datetime.fromtimestamp(st[i], tz=timezone.utc)
    days[t.date()].append((t.hour, i))
for d in days:
    days[d].sort(key=lambda z: z[1])

rows = []
for r in [r for r in RB.detect(A, hourly_range=True) if r["side"] != 0 and r["break_i"] is not None]:
    bi = int(r["break_i"]); side = int(r["side"]); e = float(r["entry"])
    if e <= 0:
        continue
    d = datetime.fromtimestamp(st[bi], tz=timezone.utc).date()
    win = [i for (h, i) in days[d] if 13 <= h < 16]                       # 2-5pm candles
    if len(win) < 2:
        continue
    rngp = (float(r["whi"]) - float(r["wlo"])) / e * 100.0                # range % of entry
    cl = [float(C[i]) for i in win]
    rets = [cl[k] / cl[k - 1] - 1.0 for k in range(1, len(cl)) if cl[k - 1] > 0]
    rvol = (np.std(rets) * 100.0) if rets else 0.0                        # realized vol per 15m bar
    path = sum((float(H[i]) - float(Lo[i])) for i in win) / e * 100.0     # total traversal %
    aft = [i for (h, i) in days[d] if i > bi]                             # post-break, same UTC day
    if not aft:
        continue
    mfe = (max(float(H[i]) for i in aft) - e) / e * 100.0 if side > 0 else (e - min(float(Lo[i]) for i in aft)) / e * 100.0
    rows.append((rngp, rvol, path, max(0.0, mfe), 0.5 * rngp))            # +half-range TP target

rows = np.array(rows)
rng_, rv_, pa_, mfe_, tp_ = rows[:, 0], rows[:, 1], rows[:, 2], rows[:, 3], rows[:, 4]
print("=" * 96)
print("NY 2-5pm VOLATILITY -> post-break MOVE (MFE to EoD) | 15m %s | n=%d"
      % ("DAEMON/fwd" if _DR else "recon", len(rows)))
print("=" * 96)
print("  Spearman corr with post-break MFE:")
for nm, v in (("range%", rng_), ("realized-vol", rv_), ("path(sum|hi-lo|)", pa_)):
    rho, p = _st.spearmanr(v, mfe_)
    print("    %-18s rho %+.2f  (p=%.1e)%s" % (nm, rho, p, "   << predictive" if abs(rho) > 0.2 and p < 0.01 else ""))
print("  -- range%% terciles -> median post-break MFE --")
q = np.quantile(rng_, [0, 1 / 3, 2 / 3, 1.0])
for lo, hi, lab in ((q[0], q[1], "low  "), (q[1], q[2], "mid  "), (q[2], q[3], "high ")):
    m = (rng_ >= lo) & (rng_ <= hi)
    print("    range%% %-4s [%.2f-%.2f]  n=%3d  median MFE %.2f%%  (median range %.2f%%)"
          % (lab, lo, hi, m.sum(), np.median(mfe_[m]), np.median(rng_[m])))
ratio = mfe_ / np.where(rng_ > 0, rng_, 1e-9)
print("  -- MFE / range ratio (how far the break runs vs the range size) --")
print("    median %.2f  |  mean %.2f  |  P(MFE >= 1/2 range = TP reached) %.0f%%"
      % (np.median(ratio), ratio.mean(), 100.0 * np.mean(mfe_ >= tp_)))
print("=" * 96)
