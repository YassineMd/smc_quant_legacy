# -*- coding: utf-8 -*-
"""PRICE-PATH DEVELOPMENT into the wall (kinematics only, NO order flow). Over the approach [j0,k0-1] (STRICTLY
before radar entry; k0/resolving bar excluded), oriented toward the target wall: path efficiency (impulse vs grind),
approach velocity, acceleration (late third vs early), deepest pullback, counter-bar fraction, final run length,
fresh-extreme arrival, range expansion. AUC for RESIST, both years. Descriptive."""
import os, sys, bisect
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f, auc_p
from app import absorption_level_detect as AL

LF, DECAY, STR, MAXLEG, EPS = 24, 0.6, 0.12, 96, 1e-9
print("loading 15m + detect ...", flush=True)
_, r15, _ = load_archive("15m", root="study/recon_archive")
A = sorted(r15, key=lambda b: _f(b.get("start_time", 0)))
for b in A:
    b["open"] = _f(b.get("open_price")); b["close"] = _f(b.get("close_price"))
n = len(A)
C = np.array([b["close"] for b in A]); H = np.array([_f(b.get("high")) for b in A]); L = np.array([_f(b.get("low")) for b in A])
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]

walls = AL.detect(A)
Sanch = set(); Ranch = set()
for w in walls:
    (Sanch if w["side"] == "S" else Ranch).add(w["i0"])
    for r in w["radar_runs"]:
        (Sanch if w["side"] == "S" else Ranch).add(r[0])
Sanch = sorted(Sanch); Ranch = sorted(Ranch)


def last_anchor(anchors, k0):
    i = bisect.bisect_left(anchors, k0) - 1
    return anchors[i] if (i >= 0 and 6 <= k0 - anchors[i] <= MAXLEG) else None


V = []
for w in walls:
    hits = w["hits"]; base = w["strength"] / (DECAY ** hits) if hits else w["strength"]
    P = w["price"]; band = w["band"]; side = w["side"]; r_lo = P - 3.0 * band; r_hi = P + 3.0 * band
    for j, (k0, k1, *_) in enumerate(w["radar_runs"]):
        if base * (DECAY ** j) < STR:
            continue
        oc = None
        for k in range(k0, min(n, k0 + LF)):
            r = (0 if C[k] > r_hi else (1 if C[k] < r_lo else None)) if side == "R" else \
                (0 if C[k] < r_lo else (1 if C[k] > r_hi else None))
            if r is not None:
                oc = r; break
        if oc is None:
            continue
        j0 = last_anchor(Sanch if side == "R" else Ranch, k0)
        if j0 is None:
            continue
        d = 1.0 if side == "R" else -1.0
        if (C[k0] - C[j0]) * d <= 0:
            continue
        m = k0 - j0                                   # approach = indices j0 .. k0-1 (EXCLUDES k0)
        if m < 6:
            continue
        pc = C[j0:k0]; hh = H[j0:k0]; ll = L[j0:k0]
        p0 = pc[0]
        diffs = np.diff(pc)
        path = float(np.sum(np.abs(diffs))) + EPS
        net = (pc[-1] - pc[0]) * d                    # net displacement toward the wall (>0 by construction)
        er = net / path                               # path efficiency: 1=straight impulse, ~0=grind
        dist = net / p0 * 100.0                        # total approach distance %
        vel = dist / m                                 # avg %/bar toward wall
        t = m // 3
        early_v = (pc[t] - pc[0]) * d / max(1, t) / p0 * 100.0
        late_v = (pc[-1] - pc[-1 - t]) * d / max(1, t) / p0 * 100.0
        accel = late_v - early_v                       # >0 = accelerating into the wall
        if d > 0:
            run = np.maximum.accumulate(pc); dd = float(np.max((run - pc) / pc)) * 100.0   # deepest dip below running high
            fresh = 1.0 if pc[-1] >= pc.max() - EPS else 0.0
        else:
            run = np.minimum.accumulate(pc); dd = float(np.max((pc - run) / pc)) * 100.0
            fresh = 1.0 if pc[-1] <= pc.min() + EPS else 0.0
        npull = float(np.mean((diffs * d) < 0))        # fraction of bars stepping AWAY from the wall (grind-iness)
        rl = 0
        for i in range(m - 1, 0, -1):
            if (pc[i] - pc[i - 1]) * d > 0:
                rl += 1
            else:
                break
        rng = (hh - ll) / p0 * 100.0
        rng_dev = float(np.mean(rng[-t:]) - np.mean(rng[:t]))    # range expansion into the wall
        V.append({"resist": oc, "yr": YR[k0], "er": er, "dist": dist, "vel": vel, "accel": accel,
                  "maxret": dd, "npull": npull, "runlen": float(rl), "fresh": fresh, "rng_dev": rng_dev,
                  "leglen": float(m)})

base_r = sum(v["resist"] for v in V) / len(V)
print("\n=== PRICE-PATH DEVELOPMENT: %d approaches | base RESIST %.1f%% ===" % (len(V), 100 * base_r), flush=True)
print("   [AUC>0.5 -> higher feature -> RESIST (wall holds)]\n", flush=True)
lab = {"er": "path efficiency (impulse>grind)", "dist": "approach distance %", "vel": "velocity %/bar",
       "accel": "acceleration into wall", "maxret": "deepest pullback %", "npull": "counter-bar fraction (grind)",
       "runlen": "final run length (bars)", "fresh": "arrives on FRESH extreme", "rng_dev": "range expansion into wall",
       "leglen": "approach length (bars)"}
rows = []
for feat in lab:
    a = [v[feat] for v in V if v["resist"] and v[feat] == v[feat]]; b = [v[feat] for v in V if not v["resist"] and v[feat] == v[feat]]
    if len(a) < 30 or len(b) < 30:
        continue
    g = auc_p(a, b)[0]
    a25 = auc_p([v[feat] for v in V if v["resist"] and v["yr"] == 2025 and v[feat] == v[feat]], [v[feat] for v in V if not v["resist"] and v["yr"] == 2025 and v[feat] == v[feat]])[0]
    a26 = auc_p([v[feat] for v in V if v["resist"] and v["yr"] == 2026 and v[feat] == v[feat]], [v[feat] for v in V if not v["resist"] and v["yr"] == 2026 and v[feat] == v[feat]])[0]
    rows.append((abs(g - 0.5), feat, g, a25, a26))
rows.sort(reverse=True)
for _, feat, g, a25, a26 in rows:
    flag = "  <-- both-yr" if (g - 0.5) * (a25 - 0.5) > 0 and (g - 0.5) * (a26 - 0.5) > 0 and abs(g - 0.5) >= 0.04 else ""
    print("   %-8s %.3f (25:%.2f 26:%.2f)  %-32s%s" % (feat, g, a25, a26, lab[feat], flag), flush=True)

# clean rate splits for the strongest few (tertiles), both years
print("\n   -- rate splits (bottom third / top third), both yr --", flush=True)
for feat in [r[1] for r in rows[:4]]:
    xs = sorted(v[feat] for v in V if v[feat] == v[feat])
    lo_t = xs[len(xs) // 3]; hi_t = xs[2 * len(xs) // 3]
    lo = [v for v in V if v[feat] <= lo_t]; hi = [v for v in V if v[feat] >= hi_t]
    for nm, g in (("bottom3rd", lo), ("top3rd", hi)):
        s25 = [v for v in g if v["yr"] == 2025]; s26 = [v for v in g if v["yr"] == 2026]
        print("   %-8s %-9s n=%4d RESIST %.1f%% (25:%.1f 26:%.1f)" % (
            feat, nm, len(g), 100 * sum(v["resist"] for v in g) / max(1, len(g)),
            100 * sum(v["resist"] for v in s25) / max(1, len(s25)),
            100 * sum(v["resist"] for v in s26) / max(1, len(s26))), flush=True)
