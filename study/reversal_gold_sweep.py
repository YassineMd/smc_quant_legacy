# -*- coding: utf-8 -*-
"""Threshold sweep for the GOLD tier = engulf & win>=T (defenders' aggressive share at the extreme third).
Pick a robust T (not a knife-edge): precision + n + year split across T in {30..55}, both tf."""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
try:
    from scipy.stats import binomtest
    def pval(k, n, p): return binomtest(k, n, p, alternative="greater").pvalue if n else 1.0
except Exception:
    def pval(k, n, p): return 1.0

LB, CIR, WICK, DS = 6, 0.55, 0.25, 3.0


def winshare(b, hi, lo, down):
    lv = b.get("levels") or {}; rng = hi - lo
    if not lv or rng <= 0: return 0.0
    thr = (lo + rng / 3.0) if down else (hi - rng / 3.0)
    ext = tot = 0.0
    for ps, vv in lv.items():
        try: p = float(ps)
        except (TypeError, ValueError): continue
        w = _f(vv.get("b")) if down else _f(vv.get("s")); tot += w
        if (p <= thr) if down else (p >= thr): ext += w
    return (ext / tot * 100.0) if tot > 0 else 0.0


def run(tf, R, LF=6):
    _, rows, _ = load_archive(tf, root="study/recon_archive")
    A = sorted(rows, key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    O = [_f(b.get("open_price")) for b in A]; C = [_f(b.get("close_price")) for b in A]
    H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
    YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
    DP = [0.0] * n
    for i in range(n):
        cv = _f(A[i].get("curr_vol")); DP[i] = (_f(A[i].get("buy_vol")) - _f(A[i].get("sell_vol"))) / cv * 100.0 if cv > 0 else 0.0
    def eng(i, d):
        if d: return C[i - 1] < O[i - 1] and O[i] <= C[i - 1] and C[i] >= O[i - 1]
        return C[i - 1] > O[i - 1] and O[i] >= C[i - 1] and C[i] <= O[i - 1]
    def rev(i, d):
        if d: return min(L[i + 1:i + 1 + LF]) >= L[i] and (max(H[i + 1:i + 1 + LF]) - L[i]) / L[i] >= R
        return max(H[i + 1:i + 1 + LF]) <= H[i] and (H[i] - min(L[i + 1:i + 1 + LF])) / H[i] >= R
    base_n = base_h = 0
    engf = []
    for i in range(LB, n - LF - 1):
        rng = H[i] - L[i]
        if rng <= 0 or O[i] <= 0: continue
        if L[i] <= min(L[i - LB:i]) or H[i] >= max(H[i - LB:i]): base_n += 1; base_h += 0
        ds = DP[i] - (DP[i - 2] + DP[i - 1]) / 2.0
        cir = (C[i] - L[i]) / rng; lw = (min(O[i], C[i]) - L[i]) / rng
        cir_t = (H[i] - C[i]) / rng; uw = (H[i] - max(O[i], C[i])) / rng
        d = None
        if L[i] <= min(L[i - LB:i]) and cir >= CIR and lw >= WICK and C[i] > O[i] and ds >= DS: d = True
        elif H[i] >= max(H[i - LB:i]) and cir_t >= CIR and uw >= WICK and C[i] < O[i] and ds <= -DS: d = False
        if d is None or not eng(i, d): continue
        engf.append({"i": i, "hit": rev(i, d), "yr": YR[i], "win": winshare(A[i], H[i], L[i], d)})
    # base rate over fresh candidates
    fresh = []
    for i in range(LB, n - LF - 1):
        if H[i] - L[i] <= 0 or O[i] <= 0: continue
        for d in ((True,) if L[i] <= min(L[i - LB:i]) else ()) + ((False,) if H[i] >= max(H[i - LB:i]) else ()):
            fresh.append(rev(i, d))
    base = sum(fresh) / len(fresh)
    def taken(sel):
        t = []; last = -10**9
        for f in sel:
            if f["i"] > last + LF: t.append(f); last = f["i"]
        return t
    et = taken(engf)
    print("\n=== %s === base %.1f%% | engulf(all): taken n=%d %.1f%%" % (
        tf, 100 * base, len(et), 100 * sum(f["hit"] for f in et) / max(1, len(et))))
    for T in (30, 35, 40, 45, 50, 55):
        sel = taken([f for f in engf if f["win"] >= T])
        nt = len(sel); ht = sum(f["hit"] for f in sel)
        h25 = sum(f["hit"] for f in sel if f["yr"] == 2025); n25 = sum(1 for f in sel if f["yr"] == 2025)
        h26 = sum(f["hit"] for f in sel if f["yr"] == 2026); n26 = sum(1 for f in sel if f["yr"] == 2026)
        print("   win>=%2d: n=%3d  %5.1f%%  (25:%2.0f%% n%d / 26:%2.0f%% n%d)  p=%.4f" % (
            T, nt, 100 * ht / max(1, nt), 100 * h25 / max(1, n25), n25, 100 * h26 / max(1, n26), n26, pval(ht, nt, base)))


for tf, R in (("15m", 0.004), ("1h", 0.006)):
    run(tf, R)
