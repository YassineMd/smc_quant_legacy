"""NY-session INTRADAY REVERSAL test. Theory: from the NY open price trends one way until ~4/5pm, then reverses.
Per UTC weekday: r1 = open->pivot return (the 'trend' leg), r2 = pivot->end-of-day return (the 'reversal' leg).
Reversal => r2 opposite-signed to r1: reversal rate > 50%, corr(r1,r2) < 0, and a FADE (short if r1 up / long if
r1 down, held to EoD) is net positive. Sweep the pivot hour (4/5pm maps to ~19-21 UTC). Weekends excluded.
Run: python study/ny_reversal.py
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

F = L.load_features("1h")
n = F["n"]; O = F["o"]; C = F["c"]; start = [float(t) for t in F["start"]]
FEE = MA.FEE
OPEN_H = int(os.environ.get("OPEN_H", "8")); END_H = int(os.environ.get("END_H", "23"))   # trend-leg start / EoD (UTC)

# group buckets by UTC date, weekdays only -> [(hour, idx)] in time order
days = defaultdict(list)
for i in range(n):
    t = datetime.fromtimestamp(start[i], tz=timezone.utc)
    if t.weekday() >= 5:                                      # exclude Sat/Sun
        continue
    days[t.date()].append((t.hour, i))


def legs(pivot_h):
    r1 = []; r2 = []; yr = []
    for d, bks in days.items():
        bks.sort(key=lambda b: b[1])
        ny = [b for b in bks if OPEN_H <= b[0] <= END_H]
        if not ny:
            continue
        piv = [b for b in ny if b[0] <= pivot_h]; end = [b for b in ny if b[0] <= END_H]
        if not piv or not end:
            continue
        i_open = ny[0][1]; i_piv = piv[-1][1]; i_end = end[-1][1]
        if i_piv <= i_open or i_end <= i_piv:                 # need a real trend leg AND a real reversal leg
            continue
        po = O[i_open]; pp = C[i_piv]; pe = C[i_end]
        if po <= 0 or pp <= 0 or pe <= 0:
            continue
        r1.append(pp / po - 1.0); r2.append(pe / pp - 1.0)
        yr.append(datetime.fromtimestamp(start[i_open], tz=timezone.utc).year)
    return np.array(r1), np.array(r2), np.array(yr)


def report(label, r1, r2, min_r1=0.0):
    m = (r1 != 0) & (r2 != 0) & (np.abs(r1) >= min_r1)       # optional: only clear morning moves (|r1| >= min)
    r1 = r1[m]; r2 = r2[m]; k = len(r1)
    if k < 10:
        print("  %-18s n=%d (too few)" % (label, k)); return
    rev = int((np.sign(r1) != np.sign(r2)).sum()); rate = 100.0 * rev / k
    p_rev = _st.binomtest(rev, k, 0.5, alternative="greater").pvalue    # reversal rate > 50% ?
    cc = float(np.corrcoef(r1, r2)[0, 1])
    fade = -np.sign(r1) * r2                                  # short if morning up / long if down, held to EoD
    fade_net = fade - FEE                                     # one entry+exit
    mfade = float(fade_net.mean()) * 100
    tt = _st.ttest_1samp(fade_net, 0.0)
    print("  %-18s n=%4d  reversal %4.1f%% (p=%.3f)  corr(r1,r2) %+.2f  fade %+.3f%%/day (t=%+.2f)  |r1| %.2f%% |r2| %.2f%%"
          % (label, k, rate, p_rev, cc, mfade, float(tt.statistic),
             float(np.abs(r1).mean()) * 100, float(np.abs(r2).mean()) * 100))


print("=" * 120)
print("Intraday REVERSAL | trend leg %dUTC -> pivot, reversal pivot -> EoD %dUTC | weekdays only | 1h recon" % (OPEN_H, END_H))
print("  reversal% = days where the POST-pivot leg OPPOSES the PRE-pivot leg;  fade = trade AGAINST the pre-pivot move into EoD")
print("=" * 120)
for pv in (18, 19, 20, 21):                                  # 2pm ... 5pm ET (EDT)
    r1, r2, yr = legs(pv)
    print("-- pivot %d UTC (~%dpm ET) --" % (pv, pv - 16))
    report("ALL days", r1, r2)
    report("morning |move|>=2%", r1, r2, 0.02)
    report("morning |move|>=3%", r1, r2, 0.03)
print("=" * 120)
