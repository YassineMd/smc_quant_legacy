"""Vol-scaled 3-TP ladder for the NY range-break. Each TP = k x (2-5pm range), placed for a target HIT probability.
For every break we walk post-break candles (adverse-first vs the wide SL) and record the MAX favourable excursion
reached BEFORE the SL, as a fraction of the range: reach = maxfav / range. The percentiles of `reach` invert directly
into the multiples: k such that P(reach >= k) = target -> k = (1-target) percentile of reach. So TP=k*range is hit
`target` of the time. Reports k for the user's bands (TP1 90-100%, TP2 80-90%, TP3 60-80%). Recon 15m; DATA_ROOT=fwd.
Run: python study/ny_break_tp3.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from collections import defaultdict
import numpy as np
import study.signal_search_lib as L
from app import ny_rangebreak_detect as RB

_DR = os.environ.get("DATA_ROOT", "")
if _DR:
    from study.archive_loader import load_archive
    raws = [b for b in load_archive("15m", root=os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), _DR)))[1] if b.get("start_time")]
    raws.sort(key=lambda b: b["start_time"])
    H = [float(b.get("high", 0) or 0) for b in raws]; Lo = [float(b.get("low", 0) or 0) for b in raws]
    st = [float(b.get("start_time", 0) or 0) for b in raws]; A = raws; n = len(raws)
else:
    F = L.load_features("15m"); A = F["A"]; H = F["h"]; Lo = F["l"]; st = [float(t) for t in F["start"]]; n = F["n"]

days = defaultdict(list)
for i in range(n):
    t = datetime.fromtimestamp(st[i], tz=timezone.utc)
    days[t.date()].append((t.hour, i))
for d in days:
    days[d].sort(key=lambda z: z[1])

SL_PAD = 0.001
reach = []
for r in [r for r in RB.detect(A, hourly_range=True) if r["side"] != 0 and r["break_i"] is not None]:
    bi = int(r["break_i"]); side = int(r["side"]); e = float(r["entry"])
    whi = float(r["whi"]); wlo = float(r["wlo"]); rng = whi - wlo
    if e <= 0 or rng <= 0:
        continue
    sl = wlo * (1 - SL_PAD) if side > 0 else whi * (1 + SL_PAD)
    maxfav = 0.0
    for j in range(bi + 1, min(n, bi + 1 + 192)):             # ~2-day cap (192 x 15m), matching the strategy walk
        hi = float(H[j]); lo = float(Lo[j])
        if (lo <= sl) if side > 0 else (hi >= sl):            # SL hit (adverse-first) -> freeze the reach
            break
        fav = (hi - e) if side > 0 else (e - lo)
        if fav > maxfav:
            maxfav = fav
    reach.append(maxfav / rng)                                # favourable excursion as a fraction of the range

reach = np.array(reach)
print("=" * 92)
print("VOL-SCALED 3-TP LADDER | TP = k x (2-5pm range) | 15m %s | n=%d" % ("DAEMON/fwd" if _DR else "recon", len(reach)))
print("  reach = max favourable excursion before SL, / range.  k for a target hit-rate = (1-target) percentile of reach.")
print("=" * 92)
print("  %-16s %-13s %s" % ("hit-rate", "k x range", "example: TP% if range=3.0%"))
for hitrate in (0.95, 0.90, 0.85, 0.80, 0.70, 0.65, 0.60):
    k = float(np.quantile(reach, 1.0 - hitrate))
    # verify empirically
    emp = 100.0 * np.mean(reach >= k)
    print("  %5.0f%%           %.2f          %.2f%%   (empirical reach %.0f%%)" % (100 * hitrate, k, k * 3.0, emp))
print("-" * 92)
k1 = float(np.quantile(reach, 0.075)); k2 = float(np.quantile(reach, 0.15)); k3 = float(np.quantile(reach, 0.30))
print("  SUGGESTED:  TP1 (~92%%) = %.2f x range   TP2 (~85%%) = %.2f x range   TP3 (~70%%) = %.2f x range" % (k1, k2, k3))
print("              (the current single TP = 0.50 x range sits at ~%.0f%% hit-rate)" % (100 * np.mean(reach >= 0.5)))
print("=" * 92)
