"""15m chart: do the 15m-Engulfing (momentum) and Absorption-Candle in-range signals improve the break-side
probability model beyond the range-hour directions? Honest test = leave-one-year-out OOS. Features known by 5pm only
(hour dirs 13/14/15 from 15m clock-hour candles + last in-range signal side, before 16:00 UTC and before the break).
Breaks + hour dirs from the 15m detector (hourly_range). Target = brB.  15m recon.
Run: python study/ny_break_augment_15m.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from collections import defaultdict
import numpy as np
import study.signal_search_lib as L
from app import ny_rangebreak_detect as RB
from app import momentum_detect, engulf1m_detect

F = L.load_features("15m"); A = F["A"]; O = F["o"]; C = F["c"]; st = [float(t) for t in F["start"]]; n = F["n"]


def fired(mod, name):
    out = {}
    try:
        for e in mod.detect(A, skip_last=False):
            out[int(e["i"])] = int(e["side"])
    except Exception as e:
        print("  !! %s.detect failed: %s" % (name, e))
    print("  detector %-9s fired %d times" % (name, len(out)))
    return out


DET = {"momentum": fired(momentum_detect, "momentum"), "absorb": fired(engulf1m_detect, "absorb")}
ranges = [r for r in RB.detect(A, hourly_range=True) if r["side"] != 0 and r["break_i"] is not None]

days = defaultdict(list)
for i in range(n):
    t = datetime.fromtimestamp(st[i], tz=timezone.utc)
    days[t.date()].append((t.hour, i))
for d in days:
    days[d].sort(key=lambda z: z[1])


def hourdir(d, H):
    hh = [i for (h, i) in days[d] if h == H]
    if not hh:
        return 0
    o = float(O[hh[0]]); c = float(C[hh[-1]])
    return 1 if c > o else (-1 if c < o else 0)


rows = []
for r in ranges:
    bi = int(r["break_i"]); up = 1.0 if r["side"] > 0 else 0.0
    d = datetime.fromtimestamp(st[bi], tz=timezone.utc).date(); yr = datetime.fromtimestamp(st[bi], tz=timezone.utc).year
    d13, d14, d15 = hourdir(d, 13), hourdir(d, 14), hourdir(d, 15)
    if 0 in (d13, d14, d15):
        continue
    sig = {}
    for name, fmap in DET.items():
        best = None
        for j, side in fmap.items():
            if j >= bi:
                continue
            tj = datetime.fromtimestamp(st[j], tz=timezone.utc)
            if tj.date() == d and 13 <= tj.hour < 16:
                if best is None or j > best[0]:
                    best = (j, side)
        sig[name] = best[1] if best else 0
    rows.append((d13, d14, d15, sig["momentum"], sig["absorb"], up, yr))

rows = np.array(rows, dtype=float)
yr = rows[:, 6]; Y = rows[:, 5]
FEATS = {
    "base (hour dirs)": [0, 1, 2],
    "base + momentum": [0, 1, 2, 3],
    "base + absorb": [0, 1, 2, 4],
    "base + both": [0, 1, 2, 3, 4],
    "both only": [3, 4],
}


def fit(X, y):
    Xb = np.column_stack([np.ones(len(X)), X]); b = np.zeros(Xb.shape[1])
    for _ in range(60):
        p = 1 / (1 + np.exp(-Xb @ b)); W = p * (1 - p) + 1e-9
        b += np.linalg.solve((Xb * W[:, None]).T @ Xb + 1e-4 * np.eye(Xb.shape[1]), Xb.T @ (y - p))
    return b


def acc(b, X, y):
    p = 1 / (1 + np.exp(-np.column_stack([np.ones(len(X)), X]) @ b))
    return 100.0 * ((p > 0.5) == (y > 0.5)).mean()


print("=" * 92)
print("15m BREAK-SIDE model: hour dirs vs + 15m-Engulfing / Absorption | n=%d | in-sample & LOYO OOS" % len(rows))
print("=" * 92)
print("  %-20s  in-sample   OOS-2025   OOS-2026   OOS-avg" % "feature set")
for name, cols in FEATS.items():
    X = rows[:, cols]
    ins = acc(fit(X, Y), X, Y); oos = {}
    for ty in (2025, 2026):
        tr = yr != ty; te = yr == ty
        if te.sum() < 10 or tr.sum() < 10:
            continue
        oos[ty] = acc(fit(X[tr], Y[tr]), X[te], Y[te])
    oa = np.mean(list(oos.values())) if oos else float("nan")
    print("  %-20s  %6.1f%%    %6.1f%%    %6.1f%%    %6.1f%%"
          % (name, ins, oos.get(2025, float("nan")), oos.get(2026, float("nan")), oa))
print("-" * 92)
labels = ["d13", "d14", "d15", "momentum", "absorb"]
print("  redundancy (corr of signal side with hour dirs):")
for si in (3, 4):
    cors = "  ".join("%s %+.2f" % (labels[hj], (np.corrcoef(rows[rows[:, si] != 0, si], rows[rows[:, si] != 0, hj])[0, 1]
                     if (rows[:, si] != 0).sum() > 5 else 0)) for hj in (0, 1, 2))
    print("    %-9s vs  %s  (fired on %d of %d break days)" % (labels[si], cors, int((rows[:, si] != 0).sum()), len(rows)))
print("=" * 92)
