# -*- coding: utf-8 -*-
"""WALLS from aggression AND absorption — do EITHER predict S/R beyond a random line?

A wall = where strong one-sided aggression (|delta|>=T) meets its limit. Two flavors:
  ABSORPTION (tiny body, aggressor failed at the extreme): buy-absorbed HIGH=resistance / sell-absorbed LOW=support
  AGGRESSION (big body, aggressor moved FROM a base):      sell-agg    HIGH=resistance / buy-agg      LOW=support
Test each source's discovered levels (+ the two combined) as causal S/R (revisit >=MIN_AGE after birth, life L_LIFE),
first-passage REJECT vs BREAK, against ANTI (dir-shuffle) + PLACEBO (random in-range). Both years.
"""
import os, sys, random
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
random.seed(42)
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
try:
    from scipy.stats import binomtest
    def pv(k, n, p): return binomtest(k, n, p, alternative="two-sided").pvalue if n else 1.0
except Exception:
    def pv(k, n, p): return 1.0

T, SMALL, BIG, EPS, R, LF, L_LIFE, MIN_AGE = 20.0, 0.35, 0.60, 0.0015, 0.004, 8, 96, 3


def run_test(tf, R=R):
    _, rows, _ = load_archive(tf, root="study/recon_archive")
    A = sorted(rows, key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    O = [_f(b.get("open_price")) for b in A]; C = [_f(b.get("close_price")) for b in A]
    H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
    YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
    DP = [0.0] * n
    for i in range(n):
        cv = _f(A[i].get("curr_vol")); DP[i] = (_f(A[i].get("buy_vol")) - _f(A[i].get("sell_vol"))) / cv * 100.0 if cv > 0 else 0.0

    def passage(i, lvl, rd):
        rj = lvl * (1 - R) if rd else lvl * (1 + R); bk = lvl * (1 + R) if rd else lvl * (1 - R)
        for k in range(i + 1, min(n, i + 1 + LF)):
            if rd:
                if L[k] <= rj: return 1
                if H[k] >= bk: return -1
            else:
                if H[k] >= rj: return 1
                if L[k] <= bk: return -1
        return 0

    res = {g: [] for g in ("abs", "agg", "both", "ANTI", "PLAC")}
    active = []                                              # {price, side, src, born, last_test}

    for i in range(1, n - LF - 1):
        pc = C[i - 1]
        active = [lv for lv in active if i - lv["born"] <= L_LIFE]
        w0 = max(0, i - L_LIFE); rlo = min(L[w0:i]); rhi = max(H[w0:i])
        # revisit tests: aligned by source, plus a dir-shuffled ANTI over all levels
        for ctrl in ("AL", "AN"):
            best = None; bd = 1e18
            for lv in active:
                if i - lv["born"] < MIN_AGE or i - lv["last_test"] < LF:
                    continue
                side = lv["side"] if ctrl == "AL" else ("S" if lv["side"] == "R" else "R")
                p = lv["price"]; eps = p * EPS
                if side == "R" and p > pc and H[i] >= p - eps:
                    d = abs(H[i] - p)
                    if d < bd: best = (lv, p, True); bd = d
                elif side == "S" and p < pc and L[i] <= p + eps:
                    d = abs(L[i] - p)
                    if d < bd: best = (lv, p, False); bd = d
            if best:
                lv, p, rd = best; oc = passage(i, p, rd)
                if oc != 0:
                    r = 1 if oc == 1 else 0
                    if ctrl == "AL":
                        lv["last_test"] = i
                        res[lv["src"]].append((r, YR[i])); res["both"].append((r, YR[i]))
                    else:
                        res["ANTI"].append((r, YR[i]))
        for p in (random.uniform(rlo, rhi), random.uniform(rlo, rhi)):
            eps = p * EPS
            oc = passage(i, p, True) if (p > pc and H[i] >= p - eps) else (passage(i, p, False) if (p < pc and L[i] <= p + eps) else None)
            if oc is not None and oc != 0:
                res["PLAC"].append((1 if oc == 1 else 0, YR[i]))
        # add levels from candle i
        rng = H[i] - L[i]
        if rng <= 0:
            continue
        body = abs(C[i] - O[i]) / rng
        new = None
        if abs(DP[i]) >= T and body <= SMALL:                       # ABSORPTION
            new = (H[i], "R", "abs") if DP[i] > 0 else (L[i], "S", "abs")
        elif abs(DP[i]) >= T and body >= BIG and ((DP[i] > 0) == (C[i] > O[i])):   # AGGRESSION (dir matches)
            new = (L[i], "S", "agg") if DP[i] > 0 else (H[i], "R", "agg")
        if new:
            price, side, src = new; merged = False
            for lv in active:
                if lv["side"] == side and abs(lv["price"] - price) <= price * EPS * 2 and i - lv["born"] <= L_LIFE:
                    lv["last_test"] = lv["last_test"]; merged = True; break
            if not merged:
                active.append({"price": price, "side": side, "src": src, "born": i, "last_test": -999})

    def line(tag, sel):
        if not sel: print("   %-22s n=0" % tag); return
        nn = len(sel); rj = sum(r for r, _ in sel)
        j25 = [r for r, y in sel if y == 2025]; j26 = [r for r, y in sel if y == 2026]
        r25 = (sum(j25) / len(j25) * 100) if j25 else 0; r26 = (sum(j26) / len(j26) * 100) if j26 else 0
        p = res["PLAC"]; rp = (sum(r for r, _ in p) / len(p)) if p else 0.5
        print("   %-22s n=%5d  REJ %4.1f%%  (25:%4.1f/26:%4.1f)  vs-placebo p=%.3f" % (
            tag, nn, 100 * rj / nn, r25, r26, pv(rj, nn, rp)))

    print("\n=== %s === walls: aggression + absorption | T=%.0f small<=%.2f big>=%.2f R %.1f%% LF %d" % (tf, T, SMALL, BIG, R * 100, LF))
    line("ABSORPTION aligned", res["abs"])
    line("AGGRESSION aligned", res["agg"])
    line("BOTH aligned", res["both"])
    line("ANTI (shuffled)", res["ANTI"])
    line("PLACEBO (random)", res["PLAC"])


for tf in ("15m",):
    run_test(tf, R=0.004)
