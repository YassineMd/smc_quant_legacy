"""NY 3-PHASE double-reversal test (Moroccan time = UTC+1).
User model:  leg1 2-5pm (13-16 UTC) = the MOVE  ->  leg2 5-7pm (16-18 UTC) reverses leg1
             ->  leg3 7-10pm (18-21 UTC) reverses leg2 (i.e. swings BACK toward leg1).
Per UTC weekday we take three contiguous, close-to-close price legs and ask:
  rev1 = sign(leg2) != sign(leg1)   (leg2 opposes leg1)
  rev2 = sign(leg3) != sign(leg2)   (leg3 opposes leg2)
  dbl  = rev1 AND rev2              (full zig-zag: leg3 swings back toward leg1)
Under a random walk consecutive non-overlapping legs are independent -> every rate ~50%, dbl ~25%.
A real pattern needs rev1/rev2 significantly > 50% (binom) and corr(legi,legj) < 0.  Weekends excluded.
Run: python study/ny_3phase.py       (MINLEG1=2 -> condition on a >=2% opening move)
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

# leg windows in UTC (Moroccan = UTC+1):  2-5pm -> 13-16 | 5-7pm -> 16-18 | 7-10pm -> 18-21
W1 = (13, 16); W2 = (16, 18); W3 = (18, 21)

days = defaultdict(list)
for i in range(n):
    t = datetime.fromtimestamp(start[i], tz=timezone.utc)
    if t.weekday() >= 5:                                       # weekdays only
        continue
    days[t.date()].append((t.hour, i))

leg1 = []; leg2 = []; leg3 = []; yrs = []
for d, bks in days.items():
    bks.sort(key=lambda b: b[1])
    w1 = [b for b in bks if W1[0] <= b[0] < W1[1]]
    w2 = [b for b in bks if W2[0] <= b[0] < W2[1]]
    w3 = [b for b in bks if W3[0] <= b[0] < W3[1]]
    if not w1 or not w2 or not w3:                            # need price in all three windows
        continue
    p0 = O[w1[0][1]]                                          # 2pm  (open of first leg-1 bucket)
    pA = C[w1[-1][1]]                                         # 5pm  (close of last leg-1 bucket)
    pB = C[w2[-1][1]]                                         # 7pm
    pC = C[w3[-1][1]]                                         # 10pm
    if min(p0, pA, pB, pC) <= 0:
        continue
    leg1.append(pA / p0 - 1.0); leg2.append(pB / pA - 1.0); leg3.append(pC / pB - 1.0)
    yrs.append(datetime.fromtimestamp(start[w1[0][1]], tz=timezone.utc).year)

leg1 = np.array(leg1); leg2 = np.array(leg2); leg3 = np.array(leg3); yrs = np.array(yrs)


def block(label, m):
    l1 = leg1[m]; l2 = leg2[m]; l3 = leg3[m]
    k = len(l1)
    if k < 12:
        print("  %-22s n=%d (too few)" % (label, k)); return
    rev1 = (np.sign(l1) != np.sign(l2)) & (l1 != 0) & (l2 != 0)
    rev2 = (np.sign(l2) != np.sign(l3)) & (l2 != 0) & (l3 != 0)
    dbl = rev1 & rev2
    back = (np.sign(l3) == np.sign(l1)) & (l1 != 0) & (l3 != 0)   # leg3 back toward leg1
    r1 = 100.0 * rev1.sum() / k; r2 = 100.0 * rev2.sum() / k
    rd = 100.0 * dbl.sum() / k; rb = 100.0 * back.sum() / k
    p1 = _st.binomtest(int(rev1.sum()), k, 0.5, alternative="greater").pvalue
    p2 = _st.binomtest(int(rev2.sum()), k, 0.5, alternative="greater").pvalue
    pd = _st.binomtest(int(dbl.sum()), k, 0.25, alternative="greater").pvalue   # vs 25% chance
    c12 = float(np.corrcoef(l1, l2)[0, 1]); c23 = float(np.corrcoef(l2, l3)[0, 1]); c13 = float(np.corrcoef(l1, l3)[0, 1])
    print("  %-22s n=%4d | rev1 %4.1f%%(p=%.3f) rev2 %4.1f%%(p=%.3f) | BOTH %4.1f%%(p=%.3f vs25) | leg3~leg1 %4.1f%%"
          % (label, k, r1, p1, r2, p2, rd, pd, rb))
    print("  %-22s        corr l1,l2 %+.2f  l2,l3 %+.2f  l1,l3 %+.2f | |l1| %.2f%% |l2| %.2f%% |l3| %.2f%%"
          % ("", c12, c23, c13, np.abs(l1).mean() * 100, np.abs(l2).mean() * 100, np.abs(l3).mean() * 100))


print("=" * 122)
print("NY 3-PHASE double-reversal | leg1 2-5pm  leg2 5-7pm  leg3 7-10pm (Moroccan) | weekdays | 1h recon | n=%d" % len(leg1))
print("  rev1=leg2 opposes leg1 | rev2=leg3 opposes leg2 | BOTH=full zig-zag | leg3~leg1=leg3 same dir as leg1")
print("  null: random walk -> rev1/rev2 ~50%, BOTH ~25%")
print("=" * 122)
block("ALL days", np.ones(len(leg1), bool))
block("leg1 |move|>=1%", np.abs(leg1) >= 0.01)
block("leg1 |move|>=2%", np.abs(leg1) >= 0.02)
block("leg1 |move|>=3%", np.abs(leg1) >= 0.03)
print("-" * 122)
block("2025", yrs == 2025)
block("2026", yrs == 2026)
block("2025 |leg1|>=2%", (yrs == 2025) & (np.abs(leg1) >= 0.02))
block("2026 |leg1|>=2%", (yrs == 2026) & (np.abs(leg1) >= 0.02))
print("=" * 122)
