# -*- coding: utf-8 -*-
"""VOLUME x MOMENTUM combination for the wall-break trade. Does adding "entry candle punching toward the break"
(body% oriented) on top of "heavy wall-level volume" lift the break trade's net P&L beyond volume alone?
Bet the break direction (R=up / S=down), symmetric +/-1.0% target, first-passage. GROSS + NET, both years."""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

HZ, FEE, RT, D = 24, 0.0008, 0.0016, 0.010
print("loading 15m + detecting walls...", flush=True)
_, r15, _ = load_archive("15m", root="study/recon_archive")
A = sorted(r15, key=lambda b: _f(b.get("start_time", 0)))
n = len(A)
O = [_f(b.get("open_price")) for b in A]; C = [_f(b.get("close_price")) for b in A]
H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
CV = np.array([_f(b.get("curr_vol")) for b in A])
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
walls = AL.detect(A)


def box_vol(bucket, r_lo, r_hi):
    v = 0.0
    for ps, vv in (bucket.get("levels") or {}).items():
        try:
            p = float(ps)
        except (TypeError, ValueError):
            continue
        if r_lo <= p <= r_hi:
            v += _f(vv.get("b")) + _f(vv.get("s"))
    return v


E = []
for w in walls:
    if w["strength"] < 0.12:
        continue
    P = w["price"]; band = w["band"]; side = w["side"]; r_lo = P - 3.0 * band; r_hi = P + 3.0 * band
    for (k0, k1) in w["radar_runs"]:
        if k0 + HZ >= n or O[k0] <= 0:
            continue
        bv = box_vol(A[k0], r_lo, r_hi)
        if bv <= 0:
            continue
        rm = float(np.median(CV[max(0, k0 - 200):k0])) if k0 > 5 else CV[k0]
        bd = 1.0 if side == "R" else -1.0
        E.append({"k0": k0, "side": side, "vr": bv / rm if rm > 0 else 0.0,
                  "mom": (C[k0] - O[k0]) / O[k0] * 100.0 * bd, "yr": YR[k0]})

vrs = sorted(e["vr"] for e in E); vt2 = vrs[2 * len(vrs) // 3]
moms = sorted(e["mom"] for e in E); mt2 = moms[2 * len(moms) // 3]
for e in E:
    e["hv"] = e["vr"] >= vt2; e["hm"] = e["mom"] >= mt2


def trade(e):
    k0 = e["k0"]; P0 = C[k0]; d = 1 if e["side"] == "R" else -1
    tgt = P0 * (1 + d * D); stp = P0 * (1 - d * D)
    for k in range(k0 + 1, min(n, k0 + 1 + HZ)):
        if d > 0:
            if H[k] >= tgt: return D
            if L[k] <= stp: return -D
        else:
            if L[k] <= tgt: return D
            if H[k] >= stp: return -D
    return d * (C[min(n - 1, k0 + HZ)] - P0) / P0


def report(name, sel):
    if len(sel) < 20:
        print("   %-30s n=%d (few)" % (name, len(sel))); return
    g = np.array([trade(e) for e in sel]); net = g - RT
    y25 = [i for i, e in enumerate(sel) if e["yr"] == 2025]; y26 = [i for i, e in enumerate(sel) if e["yr"] == 2026]
    print("   %-30s n=%4d win %4.1f%% GROSS %+.4f%% NET %+.4f%% (net25:%+.3f/net26:%+.3f)" % (
        name, len(sel), 100 * np.mean(g > 0), 100 * g.mean(), 100 * net.mean(),
        100 * (g[y25].mean() - RT if y25 else 0), 100 * (g[y26].mean() - RT if y26 else 0)))


print("\n=== VOLUME x MOMENTUM, break bet, +/-1.0%% target === entries %d ===" % len(E), flush=True)
report("ALL (break bet)", E)
report("HI-vol (baseline edge)", [e for e in E if e["hv"]])
report("HI-mom only", [e for e in E if e["hm"]])
report("HI-vol & mom>0", [e for e in E if e["hv"] and e["mom"] > 0])
report("HI-vol & HI-mom (stacked)", [e for e in E if e["hv"] and e["hm"]])
report("HI-vol OR HI-mom (broad)", [e for e in E if e["hv"] or e["hm"]])
report("LO-vol & LO-mom (anti)", [e for e in E if (not e["hv"]) and e["mom"] < moms[len(moms) // 3]])
