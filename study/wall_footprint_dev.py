# -*- coding: utf-8 -*-
"""FOOTPRINT DEVELOPMENT into the wall — two-sided aggression & absorption, and how they EVOLVE along the approach.
Previous leg studies took the MEAN over the leg (kills the trajectory) and oriented to ONE net-delta number (hides the
two sides). Here: split the leg [j0,k0] into EARLY/LATE thirds; from the per-bar footprint (buy_vol/sell_vol + body)
derive, on BOTH sides, aggressor AGGRESSION (delta w/ follow-through), aggressor ABSORPTION (delta w/o progress),
and DEFENDER pressure (opposite side stepping in). Test the LATE-approach level AND the DEVELOPMENT (late-early).
Causal, both years."""
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
O = np.array([b["open"] for b in A]); C = np.array([b["close"] for b in A])
H = np.array([_f(b.get("high")) for b in A]); L = np.array([_f(b.get("low")) for b in A])
BV = np.array([_f(b.get("buy_vol")) for b in A]); SV = np.array([_f(b.get("sell_vol")) for b in A])
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]

vol = BV + SV
rng = np.maximum(H - L, EPS)
dp = (BV - SV) / np.maximum(vol, EPS)          # delta share (footprint two sides), [-1,1]
bfrac = np.abs(C - O) / rng                     # body capture of range
prog = (C - O) / rng                            # signed price progress within the bar

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


def seg_foot(sl, d):
    """two-sided footprint metrics over a slice, oriented so d = toward the target wall."""
    push = dp[sl] * d                            # aggressor net delta (toward wall)
    pr = prog[sl] * d                            # aggressor price progress
    bf = bfrac[sl]
    aggr = np.mean(np.clip(push, 0, None) * np.clip(pr, 0, None))       # winning aggression (delta + follow-through)
    absb = np.mean(np.clip(push, 0, None) * (1.0 - bf))                 # absorbed: delta present, body small (no progress)
    defend = np.mean(np.clip(-push, 0, None) * np.clip(-pr, 0, None))   # defender: opposite delta w/ reverse progress
    return {"push": float(np.mean(push)), "aggr": float(aggr), "absb": float(absb),
            "defend": float(defend), "vol": float(np.mean(vol[sl])), "bfrac": float(np.mean(bf))}


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
        # footprint measured STRICTLY BEFORE radar entry [j0, k0-1] — k0 (entry/possible resolving bar) EXCLUDED
        m = k0 - j0                                          # approach spans indices j0..k0-1
        if m < 6:
            continue
        t = m // 3
        early = seg_foot(slice(j0, j0 + t), d)              # first third of the approach
        late = seg_foot(slice(k0 - t, k0), d)               # last third of the approach (up to but NOT incl k0)
        f = {"resist": oc, "yr": YR[k0]}
        for key in ("push", "aggr", "absb", "defend", "vol", "bfrac"):
            f["l_" + key] = late[key]
            f["dev_" + key] = late[key] - early[key]        # DEVELOPMENT along the approach
        # relative volume development (climax?) normalised to bar's own median
        f["l_volr"] = late["vol"] / (float(np.median(vol[max(0, k0 - 200):k0])) + EPS)
        V.append(f)

base_r = sum(v["resist"] for v in V) / len(V)
print("\n=== FOOTPRINT DEVELOPMENT: %d legs | base RESIST %.1f%% ===" % (len(V), 100 * base_r), flush=True)
print("   l_* = level in the LATE third (approach) | dev_* = development (late - early)", flush=True)
print("   [AUC>0.5 -> higher feature -> RESIST]\n", flush=True)
feats = ["l_push", "dev_push", "l_aggr", "dev_aggr", "l_absb", "dev_absb",
         "l_defend", "dev_defend", "l_vol", "dev_vol", "l_volr", "l_bfrac", "dev_bfrac"]
rows = []
for feat in feats:
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
    print("   %-12s %.3f (25:%.2f 26:%.2f)%s" % (feat, g, a25, a26, flag), flush=True)

# the two flagship footprint hypotheses, as clean rate splits (both years)
print("\n   -- flagship splits (both yr) --", flush=True)
for feat, lab in (("dev_push", "aggressor pressure FADING into wall (dev_push<0)"),
                  ("l_absb", "high ABSORPTION at approach (l_absb top third)"),
                  ("l_defend", "DEFENDER stepping in at approach (l_defend top third)")):
    xs = sorted(v[feat] for v in V)
    if feat == "dev_push":
        lo = [v for v in V if v[feat] < 0]; hi = [v for v in V if v[feat] >= 0]
        labs = ("fading(<0)", "building(>=0)")
    else:
        thr = xs[int(len(xs) * 2 / 3)]
        lo = [v for v in V if v[feat] < thr]; hi = [v for v in V if v[feat] >= thr]
        labs = ("bottom2/3", "top1/3")
    for grp, lb in ((lo, labs[0]), (hi, labs[1])):
        s25 = [v for v in grp if v["yr"] == 2025]; s26 = [v for v in grp if v["yr"] == 2026]
        print("   %-46s %-12s n=%4d RESIST %.1f%% (25:%.1f 26:%.1f)" % (
            lab, lb, len(grp), 100 * sum(v["resist"] for v in grp) / max(1, len(grp)),
            100 * sum(v["resist"] for v in s25) / max(1, len(s25)),
            100 * sum(v["resist"] for v in s26) / max(1, len(s26))), flush=True)
