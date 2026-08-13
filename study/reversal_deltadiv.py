# -*- coding: utf-8 -*-
"""Are DELTA-DIVERGENT candles "very strong" reversal candles?  Test the user's claim.

Divergence = price makes a fresh extreme but ORDER FLOW does NOT confirm it. Take the SHIPPED detector flags
(fresh LB-extreme + hammer CIR/WICK/bull + delta-shift DS) and overlay several divergence definitions, measuring
incremental precision vs the plain hammer AND head-to-head vs the new engulf strong tier.

  dd_prev : candle-delta divergence  - c3 makes a lower low than the prior swing low in the window, but c3's delta%
                                       is HIGHER than the delta at that prior low (selling waning at the new low). Mirror top.
  dd_cvd  : CVD divergence           - cumulative delta is HIGHER at the new price low than at the prior price low. Mirror.
  dd_sign : delta sign-flip          - c3's delta is outright favourable at the extreme (>0 at a low / <0 at a high).
  dd_strong: dd_prev AND dd_sign     - strict: diverges vs prior low AND delta flips positive.

NB the detector already gates ds>=3 (a LOCAL divergence vs the 2 approach candles) — this tests whether a TRUER
swing/CVD/sign divergence adds on top. Taken (non-overlap) basis + exact binomial p vs base, split by year, both sides.
"""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f, auc_p
try:
    from scipy.stats import binomtest
    def pval(k, n, p): return binomtest(k, n, p, alternative="greater").pvalue if n else 1.0
except Exception:
    from math import comb
    def pval(k, n, p): return sum(comb(n, j) * p**j * (1 - p)**(n - j) for j in range(k, n + 1)) if n else 1.0

LB, CIR, WICK, DS = 6, 0.55, 0.25, 3.0


def run_test(tf, R, LF=6):
    _, rows, _ = load_archive(tf, root="study/recon_archive")
    A = sorted(rows, key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    O = [_f(b.get("open_price")) for b in A]; C = [_f(b.get("close_price")) for b in A]
    H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
    YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
    DP = [0.0] * n; CD = [0.0] * n; run = 0.0
    for i in range(n):
        bv = _f(A[i].get("buy_vol")); sv = _f(A[i].get("sell_vol")); cv = _f(A[i].get("curr_vol"))
        DP[i] = (bv - sv) / cv * 100.0 if cv > 0 else 0.0
        run += (bv - sv); CD[i] = run
    def eng(i, down):
        if down: return C[i - 1] < O[i - 1] and O[i] <= C[i - 1] and C[i] >= O[i - 1]
        return C[i - 1] > O[i - 1] and O[i] >= C[i - 1] and C[i] <= O[i - 1]
    def rev(i, down):
        if down: return min(L[i + 1:i + 1 + LF]) >= L[i] and (max(H[i + 1:i + 1 + LF]) - L[i]) / L[i] >= R
        return max(H[i + 1:i + 1 + LF]) <= H[i] and (H[i] - min(L[i + 1:i + 1 + LF])) / H[i] >= R

    fresh = []
    for i in range(LB, n - LF - 1):
        if H[i] - L[i] <= 0 or O[i] <= 0: continue
        if L[i] <= min(L[i - LB:i]): fresh.append((i, True))
        if H[i] >= max(H[i - LB:i]): fresh.append((i, False))
    base = sum(rev(i, d) for i, d in fresh) / len(fresh)

    flags = []
    for i in range(LB, n - LF - 1):
        rng = H[i] - L[i]
        if rng <= 0 or O[i] <= 0: continue
        ds = DP[i] - (DP[i - 2] + DP[i - 1]) / 2.0
        cir = (C[i] - L[i]) / rng; lw = (min(O[i], C[i]) - L[i]) / rng
        cir_t = (H[i] - C[i]) / rng; uw = (H[i] - max(O[i], C[i])) / rng
        down = None
        if L[i] <= min(L[i - LB:i]) and cir >= CIR and lw >= WICK and C[i] > O[i] and ds >= DS: down = True
        elif H[i] >= max(H[i - LB:i]) and cir_t >= CIR and uw >= WICK and C[i] < O[i] and ds <= -DS: down = False
        if down is None: continue
        win = range(i - LB, i)
        if down:
            pe = min(win, key=lambda k: L[k])                    # prior swing-low bar
            dd_prev = DP[i] > DP[pe]; dd_cvd = CD[i] > CD[pe]; dd_sign = DP[i] > 0
        else:
            pe = max(win, key=lambda k: H[k])                    # prior swing-high bar
            dd_prev = DP[i] < DP[pe]; dd_cvd = CD[i] < CD[pe]; dd_sign = DP[i] < 0
        flags.append({"i": i, "down": down, "hit": rev(i, down), "yr": YR[i], "eng": eng(i, down),
                      "dd_prev": dd_prev, "dd_cvd": dd_cvd, "dd_sign": dd_sign,
                      "dd_strong": dd_prev and dd_sign})

    def line(name, sel):
        taken = []; last = -10**9
        for f in sel:
            if f["i"] > last + LF: taken.append(f); last = f["i"]
        nt = len(taken); ht = sum(f["hit"] for f in taken)
        h25 = sum(f["hit"] for f in taken if f["yr"] == 2025); n25 = sum(1 for f in taken if f["yr"] == 2025)
        h26 = sum(f["hit"] for f in taken if f["yr"] == 2026); n26 = sum(1 for f in taken if f["yr"] == 2026)
        print("   %-26s raw %4d/%2.0f%%  | taken n=%3d %5.1f%% (25:%2.0f%% 26:%2.0f%%)  p=%.4f" % (
            name, len(sel), 100 * sum(f["hit"] for f in sel) / max(1, len(sel)), nt,
            100 * ht / max(1, nt), 100 * h25 / max(1, n25), 100 * h26 / max(1, n26), pval(ht, nt, base)))

    print("\n=== %s ===  %d buckets | base %.1f%% | detector flags %d  (share diverging: prev %.0f%% cvd %.0f%% sign %.0f%%)" % (
        tf, n, 100 * base, len(flags),
        100 * sum(f["dd_prev"] for f in flags) / max(1, len(flags)),
        100 * sum(f["dd_cvd"] for f in flags) / max(1, len(flags)),
        100 * sum(f["dd_sign"] for f in flags) / max(1, len(flags))))
    line("HAMMER (shipped core)", flags)
    line("+ dd_prev (vs prior low)", [f for f in flags if f["dd_prev"]])
    line("+ dd_cvd (CVD div)", [f for f in flags if f["dd_cvd"]])
    line("+ dd_sign (delta flips)", [f for f in flags if f["dd_sign"]])
    line("+ dd_strong (prev & sign)", [f for f in flags if f["dd_strong"]])
    line("+ engulf (current strong)", [f for f in flags if f["eng"]])
    line("+ engulf & dd_prev", [f for f in flags if f["eng"] and f["dd_prev"]])


for tf, R in (("15m", 0.004), ("1h", 0.006), ("4h", 0.010)):
    run_test(tf, R)
