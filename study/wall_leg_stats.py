# -*- coding: utf-8 -*-
"""FULL stats-box order-flow development over the leg (last opposing wall -> radar). Mean of every build_stats
parameter over [j0,k0], oriented to the leg direction, tested vs resist/break. Both years. (Follow-up: the first
leg study only used 2 of the 33 stats.)"""
import os, sys, bisect
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import build_stats, auc_p, _f

LF, DECAY, STR, MAXLEG = 24, 0.6, 0.12, 96
from app import absorption_level_detect as AL
print("loading + build_stats + detect ...", flush=True)
_, r15, _ = load_archive("15m", root="study/recon_archive")
A = sorted(r15, key=lambda b: _f(b.get("start_time", 0)))
for b in A:
    b["open"] = _f(b.get("open_price")); b["close"] = _f(b.get("close_price"))
n = len(A)
C = [b["close"] for b in A]; H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
S, _O, _C = build_stats(A)
Sarr = {k: np.array([(_f(v) if v == v else np.nan) for v in S[k]]) for k in S}
walls = AL.detect(A)

Sanch = set(); Ranch = set()
for w in walls:
    (Sanch if w["side"] == "S" else Ranch).add(w["i0"])
    for r in w["radar_runs"]:
        (Sanch if w["side"] == "S" else Ranch).add(r[0])
Sanch = sorted(Sanch); Ranch = sorted(Ranch)


def last_anchor(anchors, k0):
    i = bisect.bisect_left(anchors, k0) - 1
    return anchors[i] if (i >= 0 and 3 <= k0 - anchors[i] <= MAXLEG) else None


DIR = ("body_pct", "delta_pct", "da2", "dP_t", "dP_1", "dP_2", "delta_up", "delta_dn", "skew", "mmxskew", "effagg_sp")
CTR = {"close_in_range": 0.5, "rB": 50.0, "halfdom_up": 50.0, "halfdom_dn": 50.0}
MAG = ("range_pct", "mov_mag", "body_frac", "upper_wick", "lower_wick", "absorb_R", "absorb_h1", "absorb_h2",
       "vw", "cost_tick_ratio", "vel_ratio", "tape_ratio", "ker_buy", "ker_sell", "ber", "ser", "ber30", "ser30")

V = []
for w in walls:
    hits = w["hits"]; base = w["strength"] / (DECAY ** hits) if hits else w["strength"]
    P = w["price"]; band = w["band"]; side = w["side"]; r_lo = P - 3.0 * band; r_hi = P + 3.0 * band
    for j, (k0, k1, *pr) in enumerate(w["radar_runs"]):
        if base * (DECAY ** j) < STR:
            continue
        ko = oc = None
        for k in range(k0, min(n, k0 + LF)):
            r = (0 if C[k] > r_hi else (1 if C[k] < r_lo else None)) if side == "R" else \
                (0 if C[k] < r_lo else (1 if C[k] > r_hi else None))
            if r is not None:
                ko, oc = k, r; break
        if oc is None or ko <= k0:
            continue
        j0 = last_anchor(Sanch if side == "R" else Ranch, k0)
        if j0 is None:
            continue
        d = 1.0 if side == "R" else -1.0
        if (C[k0] - C[j0]) * d <= 0:
            continue
        sl = slice(j0, k0 + 1)
        f = {"resist": oc, "yr": YR[k0]}
        for k in DIR:
            f[k] = float(np.nanmean(Sarr[k][sl])) * d
        for k, c in CTR.items():
            f[k + "_dir"] = (float(np.nanmean(Sarr[k][sl])) - c) * d
        for k in MAG:
            f[k] = float(np.nanmean(Sarr[k][sl]))
        V.append(f)

base_r = sum(v["resist"] for v in V) / len(V)
print("\n=== FULL leg stats: %d legs | base RESIST %.1f%% ===" % (len(V), 100 * base_r), flush=True)
feats = list(DIR) + [k + "_dir" for k in CTR] + list(MAG)
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
print("   [ranked by |AUC-0.5|; >0.5 -> higher -> RESIST]", flush=True)
for _, feat, g, a25, a26 in rows:
    flag = "  <-- both-yr" if (g - 0.5) * (a25 - 0.5) > 0 and (g - 0.5) * (a26 - 0.5) > 0 and abs(g - 0.5) >= 0.04 else ""
    print("   %-16s %.3f (25:%.2f 26:%.2f)%s" % (feat, g, a25, a26, flag), flush=True)
