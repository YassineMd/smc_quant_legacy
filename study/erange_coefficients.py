"""Derive Expected-Range regression coefficients per session: today_rangeFrac = A + B*yest_rangeFrac (winsorized OLS),
plus MEAN (fallback) and CLIP=(P01,P99). rangeFrac = (sessHigh-sessLow)/sessOpen over 1h volume buckets, weekdays,
consecutive trading days. Windows match study/session_range_persistence.py; WholeDay = full UTC day [0,24)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
import study.signal_search_lib as L

F = L.load_features("1h")
st = F["start"]; H = F["h"]; Lo = F["l"]; O = F["o"]
dts = [datetime.fromtimestamp(float(t), tz=timezone.utc) for t in st]
hour = np.array([d.hour for d in dts]); dow = np.array([d.weekday() for d in dts])
dord = np.array([d.toordinal() for d in dts]); wkday = dow < 5
order = np.argsort(st)

WINDOWS = {"Tokyo": (0, 8), "London": (8, 16), "NewYork": (13, 21), "WholeDay": (0, 24)}


def day_frac(h0, h1):
    sel = wkday & (hour >= h0) & (hour < h1)
    days = {}
    for i in order:
        if sel[i]:
            days.setdefault(int(dord[i]), []).append(i)
    out = {}
    for d, idxs in days.items():
        hh = max(H[k] for k in idxs); ll = min(Lo[k] for k in idxs); oo = O[idxs[0]]
        if oo > 0 and hh > ll:
            out[d] = (hh - ll) / oo
    return out


def wins(a, lo=2.5, hi=97.5):
    a = np.asarray(a, float); loq, hiq = np.percentile(a, [lo, hi]); return np.clip(a, loq, hiq)


print("session     A         B        MEAN      CLIP(P01,P99)      n     r")
for name, (h0, h1) in WINDOWS.items():
    R = day_frac(h0, h1); days = sorted(R)
    X = np.array([R[days[k - 1]] for k in range(1, len(days))])
    Y = np.array([R[days[k]] for k in range(1, len(days))])
    xw, yw = wins(X), wins(Y)
    B, A = np.polyfit(xw, yw, 1)
    mean = float(np.mean(Y)); p01, p99 = np.percentile(list(R.values()), [1, 99])
    r = float(np.corrcoef(X, Y)[0, 1])
    print("%-9s  %.5f  %.4f   %.5f   (%.4f, %.4f)   %4d   %+.3f"
          % (name, A, B, mean, p01, p99, len(X), r))
