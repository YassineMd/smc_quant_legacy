# -*- coding: utf-8 -*-
"""Is there ANY GROSS edge in fading the bubble-heavy breakout? Report GROSS (no fee) + NET across stop/target
structures. If gross ~ 0 for every structure, no trade can save it (the 73% was a loose-reference mirage)."""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f

LB, CIR, FEE, RT = 6, 0.55, 0.0008, 0.0016


def spikeshare(b):
    lv = b.get("levels") or {}; tot = mx = 0.0
    for vv in lv.values():
        t = _f(vv.get("b")) + _f(vv.get("s")); tot += t
        if t > mx: mx = t
    return (mx / tot * 100.0) if tot > 0 else 0.0


def run_test(tf, HZ=8):
    _, rows, _ = load_archive(tf, root="study/recon_archive")
    A = sorted(rows, key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    O = [_f(b.get("open_price")) for b in A]; C = [_f(b.get("close_price")) for b in A]
    H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
    YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]

    def sim(i, up, stop, tgt):
        """first-touch; returns GROSS % (fade dir). stop/tgt are prices."""
        E = C[i]
        if up:  # SHORT
            for k in range(i + 1, min(n, i + 1 + HZ)):
                if H[k] >= stop: return -(stop - E) / E
                if L[k] <= tgt: return (E - tgt) / E
            return (E - C[min(n - 1, i + HZ)]) / E
        else:   # LONG
            for k in range(i + 1, min(n, i + 1 + HZ)):
                if L[k] <= stop: return -(E - stop) / E
                if H[k] >= tgt: return (tgt - E) / E
            return (C[min(n - 1, i + HZ)] - E) / E

    # config -> (stop_price, tgt_price) given i, up
    def cfg(name, i, up):
        E = C[i]
        if name == "A struct(stop=extreme,tgt=opp)":
            return (H[i], L[i]) if up else (L[i], H[i])
        if name == "B loose(stop=ext*1.004)":
            return (H[i] * 1.004, L[i]) if up else (L[i] * 0.996, H[i])
        if name == "C fixed 1:1 (0.4/0.4)":
            return (E * 1.004, E * 0.996) if up else (E * 0.996, E * 1.004)
        if name == "D fixed 0.6stop/0.4tgt":
            return (E * 1.006, E * 0.996) if up else (E * 0.994, E * 1.004)
        return (E, E)

    cand = []
    for i in range(LB, n - HZ - 1):
        rng = H[i] - L[i]
        if rng <= 0 or O[i] <= 0 or not A[i].get("levels"): continue
        up = None
        if H[i] >= max(H[i - LB:i]) and C[i] > O[i] and (C[i] - L[i]) / rng >= CIR: up = True
        elif L[i] <= min(L[i - LB:i]) and C[i] < O[i] and (H[i] - C[i]) / rng >= CIR: up = False
        if up is None: continue
        cand.append({"i": i, "up": up, "yr": YR[i], "spike": spikeshare(A[i])})

    def report(subname, sel, name):
        t = []; last = -10**9
        for c in sel:
            if c["i"] > last + HZ: t.append(c); last = c["i"]
        if not t: print("     %-32s %-30s n=0" % (name, subname)); return
        gs = []; ns = []
        for c in t:
            st, tg = cfg(name, c["i"], c["up"]); g = sim(c["i"], c["up"], st, tg)
            gs.append(g); ns.append(g - RT)
        g25 = np.mean([x for c, x in zip(t, gs) if c["yr"] == 2025]) if any(c["yr"] == 2025 for c in t) else 0
        g26 = np.mean([x for c, x in zip(t, gs) if c["yr"] == 2026]) if any(c["yr"] == 2026 for c in t) else 0
        print("     %-30s n=%4d  GROSS/tr %+.4f%%  NET/tr %+.4f%%  (gross 25:%+.3f%%/26:%+.3f%%)" % (
            name, len(t), 100 * np.mean(gs), 100 * np.mean(ns), 100 * g25, 100 * g26))

    print("\n=== %s ===  breakout candidates %d | HZ=%d | fade short-up/long-down" % (tf, len(cand), HZ))
    for subname, sel in (("ALL breakouts", cand), ("spike>=25 (bubble-heavy)", [c for c in cand if c["spike"] >= 25])):
        print("   -- %s --" % subname)
        for name in ("A struct(stop=extreme,tgt=opp)", "B loose(stop=ext*1.004)", "C fixed 1:1 (0.4/0.4)", "D fixed 0.6stop/0.4tgt"):
            report(subname, sel, name)


for tf in ("15m", "1h"):
    run_test(tf)
