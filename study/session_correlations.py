"""SESSION CORRELATIONS / PATTERNS — beyond direction (which was NULL, see session_leader.py). Weekends excluded.
3 non-overlapping UTC blocks: Tokyo [00,08)  London [08,13)  New York [13,21).  Per block per weekday day:
  ret% = (close-open)/open*100 ,  range% = (high-low)/open*100 (open=first bucket open, close=last, hi/lo=max/min).

Tests (Fisher-z p for correlations; exploratory -> multiple-comparison caveat applies):
  [1] RETURN correlation (Pearson + Spearman) between sessions -> momentum vs mean-reversion?
  [2] RANGE (volatility) correlation (Spearman) -> does volatility CLUSTER across sessions?
  [3] Where does the day's HIGH / LOW form? (% of days by session) + where the day's EXTREME move sits
  [4] VOL SPILLOVER: Tokyo range quartile -> mean rest-of-day (London+NY) range
  [5] year split for the headline finding
Run: python study/session_correlations.py
"""
from __future__ import annotations
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from collections import defaultdict
import numpy as np
import study.signal_search_lib as L

F = L.load_features("1h")
st = F["start"]; O = F["o"]; C = F["c"]; H = F["h"]; Lo = F["l"]
dts = [datetime.fromtimestamp(float(t), tz=timezone.utc) for t in st]
hour = np.array([d.hour for d in dts]); dow = np.array([d.weekday() for d in dts])
dord = np.array([d.toordinal() for d in dts]); yr = np.array([d.year for d in dts])
BLOCKS = (("Tokyo", 0, 8), ("London", 8, 13), ("New York", 13, 21)); NAMES = [b[0] for b in BLOCKS]
order = np.argsort(st)


def build():
    byday = defaultdict(list)
    for i in order:
        if dow[i] < 5:
            byday[dord[i]].append(i)
    days = []
    for d, idxs in byday.items():
        rec = {"yr": int(yr[idxs[0]])}; ok = True
        for name, h0, h1 in BLOCKS:
            blk = [i for i in idxs if h0 <= hour[i] < h1]
            if not blk:
                ok = False; break
            op = O[blk[0]]; cl = C[blk[-1]]; hi = max(H[k] for k in blk); lo = min(Lo[k] for k in blk)
            rec[name] = dict(o=op, c=cl, h=hi, l=lo, ret=(cl - op) / op * 100, rng=(hi - lo) / op * 100)
        if ok:
            days.append(rec)
    return days


def fisher_p(r, n):
    if n < 5 or abs(r) >= 1:
        return float("nan")
    z = math.atanh(r) * math.sqrt(n - 3)
    return math.erfc(abs(z) / math.sqrt(2.0))


def corr(a, b, spearman=False):
    a = np.asarray(a, float); b = np.asarray(b, float)
    if spearman:
        a = a.argsort().argsort().astype(float); b = b.argsort().argsort().astype(float)
    r = np.corrcoef(a, b)[0, 1]
    return r, fisher_p(r, len(a))


days = build()
n = len(days)
R = {nm: np.array([d[nm]["ret"] for d in days]) for nm in NAMES}      # returns
G = {nm: np.array([d[nm]["rng"] for d in days]) for nm in NAMES}      # ranges

print("=" * 100)
print("SESSION CORRELATIONS on 18-mo recon 1h (weekends excluded)  |  %d weekday days" % n)
print("  Tokyo [00,08)  London [08,13)  New York [13,21) UTC (non-overlapping).  EXPLORATORY (multi-comparison).")
print("=" * 100)

print("\n[1] RETURN correlation between sessions (momentum + / mean-reversion -):")
print("  %-20s  Pearson r   p       Spearman r   p" % "pair")
for i, a in enumerate(NAMES):
    for b in NAMES[i + 1:]:
        rp, pp = corr(R[a], R[b]); rs, ps = corr(R[a], R[b], spearman=True)
        print("  %-20s  %+.3f     %.4f   %+.3f      %.4f" % ("%s ~ %s" % (a, b), rp, pp, rs, ps))
# Tokyo vs rest-of-day return (reversion of the Asian move?)
rest = R["London"] + R["New York"]
rr, pr = corr(R["Tokyo"], rest)
print("  Tokyo ret ~ (London+NY) ret:  Pearson %+.3f  p%.4f  -> %s"
      % (rr, pr, "mean-reversion" if (rr < 0 and pr < 0.05) else ("momentum" if (rr > 0 and pr < 0.05) else "~ none")))

print("\n[2] RANGE (volatility) correlation — does volatility CLUSTER across sessions? (Spearman):")
for i, a in enumerate(NAMES):
    for b in NAMES[i + 1:]:
        rs, ps = corr(G[a], G[b], spearman=True)
        print("  %-20s  Spearman %+.3f   p %.4f   -> %s"
              % ("%s ~ %s" % (a, b), rs, ps, "CLUSTERS" if (rs > 0 and ps < 0.05) else "~ none"))

print("\n[3] Where does the day's HIGH / LOW / EXTREME move form? (%% of days by session):")
hi_c = {nm: 0 for nm in NAMES}; lo_c = {nm: 0 for nm in NAMES}; ex_c = {nm: 0 for nm in NAMES}
for d in days:
    dh = max(d[nm]["h"] for nm in NAMES); dl = min(d[nm]["l"] for nm in NAMES); dopen = d["Tokyo"]["o"]
    for nm in NAMES:
        if d[nm]["h"] == dh:
            hi_c[nm] += 1; break
    for nm in NAMES:
        if d[nm]["l"] == dl:
            lo_c[nm] += 1; break
    ext_nm = max(NAMES, key=lambda nm: max(abs(d[nm]["h"] - dopen), abs(d[nm]["l"] - dopen)))  # furthest from day open
    ex_c[ext_nm] += 1
for nm in NAMES:
    print("  %-9s  day-high %4.1f%%   day-low %4.1f%%   day-extreme %4.1f%%"
          % (nm, 100 * hi_c[nm] / n, 100 * lo_c[nm] / n, 100 * ex_c[nm] / n))

print("\n[4] VOL SPILLOVER: Tokyo range quartile -> mean (London+NY) range:")
tq = G["Tokyo"]; rest_rng = G["London"] + G["New York"]
qs = np.quantile(tq, [0.25, 0.5, 0.75])
labels = ["Q1 (calm Tokyo)", "Q2", "Q3", "Q4 (wild Tokyo)"]
bins = np.digitize(tq, qs)
for q in range(4):
    m = bins == q
    print("  %-16s  n=%3d   Tokyo range %5.2f%%   -> rest-of-day range %5.2f%%"
          % (labels[q], int(m.sum()), tq[m].mean(), rest_rng[m].mean()))
sr, sp = corr(G["Tokyo"], rest_rng, spearman=True)
print("  corr(Tokyo range, rest-of-day range) Spearman %+.3f  p%.4f" % (sr, sp))

print("\n[5] BY YEAR — range-cluster Spearman (Tokyo~NY) + return corr (Tokyo~NY):")
for y in (2025, 2026):
    idx = [k for k, d in enumerate(days) if d["yr"] == y]
    if len(idx) < 20:
        continue
    gT = G["Tokyo"][idx]; gN = G["New York"][idx]; rT = R["Tokyo"][idx]; rN = R["New York"][idx]
    grc, gpc = corr(gT, gN, spearman=True); rrc, rpc = corr(rT, rN)
    print("  %d (n=%d):  range Tokyo~NY %+.3f p%.4f    return Tokyo~NY %+.3f p%.4f"
          % (y, len(idx), grc, gpc, rrc, rpc))
print("=" * 100)
