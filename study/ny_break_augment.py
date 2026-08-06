"""Does adding the in-range STRATEGY signals (1h Easy / Engulf S/R / Reversal) improve the break-side probability
model beyond the range-hour directions? Honest test = OUT-OF-SAMPLE (leave-one-year-out) accuracy, since adding
features always lifts in-sample. Features known by 5pm only (hour dirs 13/14/15 + last in-range signal side per
strategy, all before 16:00 UTC and before the break). Target = brB.  1h recon.
Run: python study/ny_break_augment.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from collections import defaultdict
import numpy as np
import study.signal_search_lib as L
from app import ny_rangebreak_detect as RB
from app import easy1h_detect, engulf_sr_detect, reversal_detect, engulf1m_detect

F = L.load_features("1h"); A = F["A"]; O = F["o"]; C = F["c"]; st = [float(t) for t in F["start"]]; n = F["n"]


def fired(mod):
    out = {}
    try:
        for e in mod.detect(A, skip_last=False):
            out[int(e["i"])] = int(e["side"])
    except Exception as e:
        print("  !! detect failed:", e)
    return out


DET = {"easy1h": fired(easy1h_detect), "engulfsr": fired(engulf_sr_detect), "reversal": fired(reversal_detect),
       "absorb": fired(engulf1m_detect)}                     # Absorption Candle indicator (all-tf; descriptive)
ranges = [r for r in RB.detect(A) if r["side"] != 0 and r["break_i"] is not None]

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
    strat = {}
    for name, fmap in DET.items():                            # last in-range signal side (13-16 UTC, before break)
        best = None
        for j, side in fmap.items():
            if j >= bi:
                continue
            tj = datetime.fromtimestamp(st[j], tz=timezone.utc)
            if d13 is not None and tj.date() == d and 13 <= tj.hour < 16:
                if best is None or j > best[0]:
                    best = (j, side)
        strat[name] = best[1] if best else 0
    rows.append((d13, d14, d15, strat["easy1h"], strat["engulfsr"], strat["reversal"], strat["absorb"], up, yr))

rows = np.array(rows, dtype=float)
yr = rows[:, 8]
Y = rows[:, 7]
FEATS = {
    "base (hour dirs)": [0, 1, 2],
    "base + 3 strategies": [0, 1, 2, 3, 4, 5],
    "base + 3 + absorb": [0, 1, 2, 3, 4, 5, 6],
    "base + absorb only": [0, 1, 2, 6],
    "absorb only": [6],
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
print("BREAK-SIDE model: hour dirs vs + in-range strategy signals | n=%d | in-sample & leave-one-year-out OOS" % len(rows))
print("=" * 92)
print("  %-22s  in-sample   OOS-2025   OOS-2026   OOS-avg" % "feature set")
for name, cols in FEATS.items():
    X = rows[:, cols]
    b_all = fit(X, Y); ins = acc(b_all, X, Y)
    oos = {}
    for test_yr in (2025, 2026):
        tr = yr != test_yr; te = yr == test_yr
        if te.sum() < 10 or tr.sum() < 10:
            continue
        b = fit(X[tr], Y[tr]); oos[test_yr] = acc(b, X[te], Y[te])
    oa = np.mean(list(oos.values())) if oos else float("nan")
    print("  %-22s  %6.1f%%    %6.1f%%    %6.1f%%    %6.1f%%"
          % (name, ins, oos.get(2025, float("nan")), oos.get(2026, float("nan")), oa))
print("-" * 92)
# redundancy: correlation of each strategy side with the range-hour directions
print("  redundancy (corr of strategy side with hour dirs / with each other):")
labels = ["d13", "d14", "d15", "easy1h", "engulfsr", "reversal", "absorb"]
for si in (3, 4, 5, 6):
    cors = []
    for hj in (0, 1, 2):
        m = (rows[:, si] != 0)
        cors.append("%s %+.2f" % (labels[hj], np.corrcoef(rows[m, si], rows[m, hj])[0, 1] if m.sum() > 5 else 0))
    print("    %-9s vs  %s" % (labels[si], "  ".join(cors)))
print("=" * 92)
