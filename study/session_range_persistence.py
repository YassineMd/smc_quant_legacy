"""Does YESTERDAY's session RANGE% predict TODAY's? Known for NY (autocorr ~0.33, OOS beats climatology). Extend to
Tokyo, London, and the whole day (AllSess = union of the three windows).

Range% = (sessionHigh - sessionLow)/sessionOpen*100 over the session's 1h volume buckets, per (UTC weekday, session).
Sessions (UTC, canonical): Tokyo [0,8)  London [8,16)  New York [13,21).  AllSess [0,21) = Tokyo..NY-close span.
Pairs = consecutive TRADING days (yesterday -> today); a pair's YEAR = today's year. Weekends excluded.

Descriptive reliability only: Pearson r (pooled + per year) + Spearman (fat-tail-robust) + permutation-null p +
OOS skill = 1 - MAE_model/MAE_climatology (fit one year, predict the other; BOTH directions, since 2026 is a half
year). A durable predictor: r positive BOTH years AND OOS skill > 0 BOTH fit directions.
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
import study.signal_search_lib as L

F = L.load_features("1h")
st = F["start"]; H = F["h"]; Lo = F["l"]; O = F["o"]
dts = [datetime.fromtimestamp(float(t), tz=timezone.utc) for t in st]
hour = np.array([d.hour for d in dts]); dow = np.array([d.weekday() for d in dts])
dord = np.array([d.toordinal() for d in dts])
wkday = dow < 5
order = np.argsort(st)

WINDOWS = {"Tokyo": (0, 8), "London": (8, 16), "NewYork": (13, 21), "AllSess": (0, 21)}
NAMES = list(WINDOWS)


def day_ranges(h0, h1):
    sel = wkday & (hour >= h0) & (hour < h1)
    days = {}
    for i in order:
        if sel[i]:
            days.setdefault(int(dord[i]), []).append(i)
    out = {}
    for d, idxs in days.items():
        hh = max(H[k] for k in idxs); ll = min(Lo[k] for k in idxs); oo = O[idxs[0]]
        if oo > 0 and hh > ll:
            out[d] = (hh - ll) / oo * 100.0
    return out


R = {name: day_ranges(*w) for name, w in WINDOWS.items()}
alld = sorted(set.intersection(*[set(R[n].keys()) for n in NAMES]))
year_of = {d: datetime.fromordinal(d).year for d in alld}
print("trading days with all 4 windows present: %d  (2025=%d, 2026=%d)"
      % (len(alld), sum(year_of[d] == 2025 for d in alld), sum(year_of[d] == 2026 for d in alld)), flush=True)


def pairs(pred, targ):
    X, Y, YR = [], [], []
    for k in range(1, len(alld)):
        p, c = alld[k - 1], alld[k]
        X.append(R[pred][p]); Y.append(R[targ][c]); YR.append(year_of[c])
    return np.array(X), np.array(Y), np.array(YR)


def pear(x, y):
    return float(np.corrcoef(x, y)[0, 1]) if len(x) > 2 and x.std() > 0 and y.std() > 0 else float('nan')


def rank(a):
    o = np.asarray(a, float).argsort(); r = np.empty(len(a)); r[o] = np.arange(len(a)); return r


def spear(x, y):
    return pear(rank(x), rank(y))


def perm_p(x, y, B=3000, seed=7):
    rng = np.random.default_rng(seed); r0 = abs(pear(x, y)); c = 0
    for _ in range(B):
        c += abs(pear(x, rng.permutation(y))) >= r0
    return (c + 1) / (B + 1)


def oos(x, y, yrarr, fit, test):
    tr = yrarr == fit; te = yrarr == test
    if tr.sum() < 10 or te.sum() < 10:
        return None
    b, a = np.polyfit(x[tr], y[tr], 1)
    mae_m = np.mean(np.abs(y[te] - (a + b * x[te])))
    mae_b = np.mean(np.abs(y[te] - y[tr].mean()))
    return 1 - mae_m / mae_b


print("\n[1] SAME-SESSION day-over-day RANGE%% persistence  (yesterday -> today):", flush=True)
print("  %-9s n=%-4s r(pool)  r2025  r2026  spearman  permP    OOSskill(25->26 / 26->25)" % ("session", ""), flush=True)
for s in NAMES:
    x, y, yv = pairs(s, s)
    r25 = pear(x[yv == 2025], y[yv == 2025]); r26 = pear(x[yv == 2026], y[yv == 2026])
    s1 = oos(x, y, yv, 2025, 2026); s2 = oos(x, y, yv, 2026, 2025)
    print("  %-9s n=%-4d %+.3f   %+.3f  %+.3f   %+.3f    %.4f   %s / %s"
          % (s, len(x), pear(x, y), r25, r26, spear(x, y), perm_p(x, y),
             ("%+.3f" % s1 if s1 is not None else "  -- "), ("%+.3f" % s2 if s2 is not None else "  -- ")), flush=True)

print("\n[2] FULL yesterday->today Pearson r matrix  (rows = YESTERDAY, cols = TODAY):", flush=True)
print("  yest\\today   " + "  ".join("%9s" % t for t in NAMES), flush=True)
for p in NAMES:
    row = []
    for t in NAMES:
        x, y, _ = pairs(p, t); row.append("%+9.3f" % pear(x, y))
    print("  %-11s " % p + "  ".join(row), flush=True)

print("\n[3] Best predictor of TODAY's NY range  (which yesterday-measure leads NY today):", flush=True)
for p in NAMES:
    x, y, yv = pairs(p, "NewYork")
    s1 = oos(x, y, yv, 2025, 2026); s2 = oos(x, y, yv, 2026, 2025)
    print("  %-9s -> NY_today   r=%+.3f  r2025=%+.3f  r2026=%+.3f  OOSskill %s / %s"
          % (p, pear(x, y), pear(x[yv == 2025], y[yv == 2025]), pear(x[yv == 2026], y[yv == 2026]),
             ("%+.3f" % s1 if s1 is not None else " -- "), ("%+.3f" % s2 if s2 is not None else " -- ")), flush=True)
