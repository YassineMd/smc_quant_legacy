# -*- coding: utf-8 -*-
"""HOLDOUT for the radar-entry break edge. FIXED params (no tuning): break bet, +/-1.0% target, HZ 24, str>=0.12.
Fixes the one look-ahead: CAUSAL strength-so-far = base * DECAY^(prior visits of this wall), not the final strength.
Reports net/tr by YEAR and by QUARTER so no single period can carry it; + a dev(2025)/holdout(2026) split."""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

HZ, RT, D, DECAY, STR = 24, 0.0016, 0.010, 0.6, 0.12
_, r15, _ = load_archive("15m", root="study/recon_archive")
A = sorted(r15, key=lambda b: _f(b.get("start_time", 0)))
n = len(A)
O = [_f(b.get("open_price")) for b in A]; C = [_f(b.get("close_price")) for b in A]
H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
DT = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc) for b in A]
walls = AL.detect(A)


def trade(k0, d):
    P0 = C[k0]; tgt = P0 * (1 + d * D); stp = P0 * (1 - d * D)
    for k in range(k0 + 1, min(n, k0 + 1 + HZ)):
        if d > 0:
            if H[k] >= tgt: return D
            if L[k] <= stp: return -D
        else:
            if L[k] <= tgt: return D
            if H[k] >= stp: return -D
    return d * (C[min(n - 1, k0 + HZ)] - P0) / P0


T = []
for w in walls:
    hits = w["hits"]; base = w["strength"] / (DECAY ** hits) if hits else w["strength"]
    d = 1 if w["side"] == "R" else -1
    for j, (k0, k1) in enumerate(w["radar_runs"]):
        cstr = base * (DECAY ** j)                            # CAUSAL strength-so-far (only prior visits)
        if cstr < STR or k0 + HZ >= n or O[k0] <= 0:
            continue
        g = trade(k0, d); dt = DT[k0]
        T.append({"g": g, "net": g - RT, "yr": dt.year, "q": "%dQ%d" % (dt.year, (dt.month - 1) // 3 + 1)})


def line(name, sel):
    if len(sel) < 20:
        print("   %-14s n=%d (few)" % (name, len(sel))); return
    g = np.array([t["g"] for t in sel])
    print("   %-14s n=%5d  win %4.1f%%  GROSS %+.4f%%  NET %+.4f%%" % (
        name, len(sel), 100 * np.mean(g > 0), 100 * g.mean(), 100 * (g.mean() - RT)))


print("\n=== HOLDOUT (CAUSAL strength, fixed params: break bet, +/-1.0%%, HZ24, str>=0.12) ===")
line("ALL", T)
print("   -- by year --")
for y in (2025, 2026):
    line(str(y), [t for t in T if t["yr"] == y])
print("   -- by quarter (fixed params, every period is out-of-sample) --")
for q in sorted(set(t["q"] for t in T)):
    line(q, [t for t in T if t["q"] == q])
print("   -- dev / holdout split --")
line("dev 2025", [t for t in T if t["yr"] == 2025])
line("holdout 2026", [t for t in T if t["yr"] == 2026])
