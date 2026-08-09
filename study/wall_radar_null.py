# -*- coding: utf-8 -*-
"""Is the "break bet at radar entry -> +0.11%" a WALL edge, or just generic 15m momentum / a first-passage artifact?
Nulls (same +/-1.0% first-passage, HZ 24):
  A radar entries, RANDOM direction  -> should be ~0 (confirms the construction isn't biased)
  B ALL bars, bet sign of last body (momentum continuation) -> generic momentum baseline
  C ALL bars, RANDOM direction        -> pure null (~0)
If B ~= the radar-break +0.11%, the edge is generic momentum and walls are irrelevant."""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import random
import numpy as np
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

random.seed(42)
HZ, RT, D = 24, 0.0016, 0.010
_, r15, _ = load_archive("15m", root="study/recon_archive")
A = sorted(r15, key=lambda b: _f(b.get("start_time", 0)))
n = len(A)
O = [_f(b.get("open_price")) for b in A]; C = [_f(b.get("close_price")) for b in A]
H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]


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


def report(name, ks, dirfn):
    ks = [k for k in ks if k + HZ < n and O[k] > 0]
    g = np.array([trade(k, dirfn(k)) for k in ks]); net = g - RT
    y25 = [i for i, k in enumerate(ks) if YR[k] == 2025]; y26 = [i for i, k in enumerate(ks) if YR[k] == 2026]
    print("   %-34s n=%5d win %4.1f%% GROSS %+.4f%% NET %+.4f%% (net25:%+.3f/net26:%+.3f)" % (
        name, len(ks), 100 * np.mean(g > 0), 100 * g.mean(), 100 * net.mean(),
        100 * (g[y25].mean() - RT if y25 else 0), 100 * (g[y26].mean() - RT if y26 else 0)), flush=True)


# radar-entry bars + their break direction
walls = AL.detect(A)
rad = []
for w in walls:
    if w["strength"] < 0.12:
        continue
    bd = 1 if w["side"] == "R" else -1
    for (k0, k1) in w["radar_runs"]:
        rad.append((k0, bd))
radk = [k for k, _ in rad]; radd = {k: d for k, d in rad}

allbars = list(range(200, n - HZ - 1))
samp = allbars if len(allbars) < 40000 else random.sample(allbars, 40000)

print("\n=== NULLS (break bet was +0.113%% NET on radar entries) ===", flush=True)
report("radar entries, BREAK dir (repro)", radk, lambda k: radd[k])
report("A radar entries, RANDOM dir", radk, lambda k: random.choice((1, -1)))
report("B ALL bars, body-sign momentum", samp, lambda k: 1 if C[k] >= O[k] else -1)
report("B2 ALL bars, 3-bar drift momentum", samp, lambda k: 1 if C[k] >= C[k - 3] else -1)
report("C ALL bars, RANDOM dir", samp, lambda k: random.choice((1, -1)))
