"""When (time of day) is the break-side forecast most reliable BEFORE the break?
For each break day we walk UTC checkpoints T (15-min steps). The 'lean' = sign(price_at_T - range_open_price)
(cumulative direction since the 2pm/13:00 open). Accuracy = P(lean == break side), counted ONLY on days whose break
is still AFTER T (a genuine pre-break read). As T -> break, accuracy rises but coverage (days not yet broken) falls;
the sweet spot maximises accuracy while the break usually hasn't happened. 15m recon; breaks from the 15m detector.
Run: python study/ny_break_timing.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from collections import defaultdict
from scipy import stats as _st
import study.signal_search_lib as L
from app import ny_rangebreak_detect as RB

F = L.load_features("15m"); A = F["A"]; O = F["o"]; C = F["c"]; st = [float(t) for t in F["start"]]; n = F["n"]
ranges = [r for r in RB.detect(A, hourly_range=True) if r["side"] != 0 and r["break_i"] is not None]

days = defaultdict(list)
for i in range(n):
    t = datetime.fromtimestamp(st[i], tz=timezone.utc)
    days[t.date()].append((t.hour, t.minute, i))
for d in days:
    days[d].sort(key=lambda z: z[2])

# per break day: (date, open_price, break_time, break_side)
recs = []
for r in ranges:
    bi = int(r["break_i"]); bside = int(r["side"])
    d = datetime.fromtimestamp(st[bi], tz=timezone.utc).date()
    ny = [z for z in days[d] if z[0] >= 13]
    if not ny:
        continue
    op = float(O[ny[0][2]])
    if op <= 0:
        continue
    recs.append((d, op, st[bi], bside, days[d]))

CHECKS = [(h, m) for h in range(14, 18) for m in (0, 15, 30, 45)]    # 14:00 .. 17:45 UTC (= 3:00pm .. 6:45pm Morocco)
nb = len(recs)
print("=" * 96)
print("BREAK-SIDE forecast reliability vs time-of-day (lean = price move since 2pm open) | 15m recon | break days=%d" % nb)
print("  T (UTC / Morocco)   acc    n   cov     p        (accuracy among days whose break is still AFTER T)")
print("=" * 96)
for (h, m) in CHECKS:
    correct = tot = 0
    for (d, op, btime, bside, daybars) in recs:
        tabs = datetime(d.year, d.month, d.day, h, m, tzinfo=timezone.utc).timestamp()
        if btime <= tabs:                                   # break already happened -> not a pre-break read
            continue
        prior = [C[i] for (hh, mm, i) in daybars if 13 <= hh and st[i] < tabs]
        if not prior:
            continue
        lean = 1 if prior[-1] > op else (-1 if prior[-1] < op else 0)
        if lean == 0:
            continue
        tot += 1
        if lean == bside:
            correct += 1
    if tot < 10:
        print("  %02d:%02d / %2dpm%02d      n=%d (too few)" % (h, m, (h + 1) % 24 if (h + 1) % 24 <= 12 else (h + 1) % 12, m, tot))
        continue
    acc = 100.0 * correct / tot; cov = 100.0 * tot / nb
    p = _st.binomtest(correct, tot, 0.5, alternative="greater").pvalue
    bar = "#" * int(round(acc / 3))
    print("  %02d:%02d / %2d:%02dpm    %5.1f%% %4d  %4.0f%%  p=%.3f  %s" % (h, m, (h + 1) % 24, m, acc, tot, cov, p, bar))
print("=" * 96)
