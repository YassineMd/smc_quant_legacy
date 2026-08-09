# -*- coding: utf-8 -*-
"""Radar-entry break bet, but EXIT = the next OPPOSING wall (capped at 0.5%); SL = 0.5%. CAUSAL strength entry filter.
Long (broke a resistance): TP = nearest ALIVE resistance above (<=0.5%), else +0.5%. Short: nearest support below.
Does a structural (wall-magnet) target beat the dead ±1% version? Net by year + quarter, fixed params."""
import os, sys, bisect
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

HZ, RT, CAP, SLD, DECAY, STR, MINT = 24, 0.0016, 0.005, 0.005, 0.6, 0.12, 0.0008
_, r15, _ = load_archive("15m", root="study/recon_archive")
A = sorted(r15, key=lambda b: _f(b.get("start_time", 0)))
n = len(A)
O = [_f(b.get("open_price")) for b in A]; C = [_f(b.get("close_price")) for b in A]
H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
DT = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc) for b in A]
walls = AL.detect(A)

# opposing-wall magnets: alive R-walls (long TP) / S-walls (short TP), strength>=0.2, sorted by price
Rw = sorted((w["price"], w["i0"], w["i1"]) for w in walls if w["side"] == "R" and w["strength"] >= 0.2)
Sw = sorted((w["price"], w["i0"], w["i1"]) for w in walls if w["side"] == "S" and w["strength"] >= 0.2)
Rp = [x[0] for x in Rw]; Sp = [x[0] for x in Sw]


def opposing_tp(E, k0, d):
    if d > 0:                                                 # long: nearest ALIVE resistance above, within (MINT, CAP]
        lo = bisect.bisect_left(Rp, E * (1 + MINT)); hi = bisect.bisect_right(Rp, E * (1 + CAP))
        for idx in range(lo, hi):
            _p, i0, i1 = Rw[idx]
            if i0 <= k0 <= i1:
                return _p
        return E * (1 + CAP)
    lo = bisect.bisect_left(Sp, E * (1 - CAP)); hi = bisect.bisect_right(Sp, E * (1 - MINT))
    for idx in range(hi - 1, lo - 1, -1):                     # short: nearest ALIVE support below
        _p, i0, i1 = Sw[idx]
        if i0 <= k0 <= i1:
            return _p
    return E * (1 - CAP)


def trade(k0, d):
    E = C[k0]; tp = opposing_tp(E, k0, d); sl = E * (1 - d * SLD)
    for k in range(k0 + 1, min(n, k0 + 1 + HZ)):
        if d > 0:
            if H[k] >= tp: return (tp - E) / E
            if L[k] <= sl: return (sl - E) / E
        else:
            if L[k] <= tp: return (E - tp) / E
            if H[k] >= sl: return (E - sl) / E
    return d * (C[min(n - 1, k0 + HZ)] - E) / E


T = []
for w in walls:
    hits = w["hits"]; base = w["strength"] / (DECAY ** hits) if hits else w["strength"]
    d = 1 if w["side"] == "R" else -1
    for j, (k0, k1) in enumerate(w["radar_runs"]):
        if base * (DECAY ** j) < STR or k0 + HZ >= n or O[k0] <= 0:   # CAUSAL strength-so-far
            continue
        g = trade(k0, d); dt = DT[k0]
        T.append({"g": g, "yr": dt.year, "q": "%dQ%d" % (dt.year, (dt.month - 1) // 3 + 1)})


def line(name, sel):
    if len(sel) < 20:
        print("   %-14s n=%d (few)" % (name, len(sel))); return
    g = np.array([t["g"] for t in sel])
    print("   %-14s n=%5d  win %4.1f%%  GROSS %+.4f%%  NET %+.4f%%" % (
        name, len(sel), 100 * np.mean(g > 0), 100 * g.mean(), 100 * (g.mean() - RT)))


print("\n=== EXIT = next opposing wall (cap 0.5%%), SL 0.5%%, CAUSAL strength ===")
line("ALL", T)
for y in (2025, 2026):
    line(str(y), [t for t in T if t["yr"] == y])
print("   -- by quarter --")
for q in sorted(set(t["q"] for t in T)):
    line(q, [t for t in T if t["q"] == q])
