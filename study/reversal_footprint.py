# -*- coding: utf-8 -*-
"""Do FOOTPRINT (per-price-level) features predict a reversal AT candle N, beyond the hammer? Predictive framing
(fresh low + holds/reverses). Test: delta by half, buy-share at the low third, sell concentration at the low,
POC position. AUC among fresh lows AND — the key test — AUC AMONG THE HAMMER FLAGS (incremental). 1h + 15m, both yrs."""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from datetime import datetime, timezone
from study.candle_bias_1h import load as load1h, _f, auc_p
from study.archive_loader import load_archive


def load15():
    _, rows, _ = load_archive("15m", root="study/recon_archive")
    A = sorted(rows, key=lambda b: _f(b.get("start_time", 0)))
    for b in A:
        b["open"] = _f(b.get("open_price")); b["close"] = _f(b.get("close_price"))
    return A


def fp_bottom(b):
    """Footprint features for a BOTTOM candle (from the per-level `levels` map). None if no footprint."""
    lv = b.get("levels") or {}
    o = _f(b.get("open", b.get("open_price"))); c = _f(b.get("close", b.get("close_price")))
    h = _f(b.get("high")); l = _f(b.get("low")); rng = h - l
    if not lv or rng <= 0:
        return None
    mid = (h + l) / 2.0; lo3 = l + rng / 3.0
    b_lo = s_lo = b_hi = s_hi = b_l3 = s_l3 = tot = 0.0; poc_p = l; poc_v = -1.0
    for ps, vv in lv.items():
        try:
            p = float(ps)
        except (TypeError, ValueError):
            continue
        bb = _f(vv.get("b")); ss = _f(vv.get("s")); v = bb + ss; tot += v
        if p < mid: b_lo += bb; s_lo += ss
        else: b_hi += bb; s_hi += ss
        if p <= lo3: b_l3 += bb; s_l3 += ss
        if v > poc_v: poc_v = v; poc_p = p
    if tot <= 0:
        return None
    s_all = s_lo + s_hi
    return {
        "delta_lowhalf": (b_lo - s_lo) / tot * 100.0,           # net flow in the LOWER half (absorption if not very negative)
        "delta_uphalf": (b_hi - s_hi) / tot * 100.0,
        "buyshare_low3": (b_l3 / (b_l3 + s_l3) * 100.0) if (b_l3 + s_l3) > 0 else 50.0,   # buyers at the low third
        "sellconc_low3": (s_l3 / s_all * 100.0) if s_all > 0 else 0.0,   # fraction of selling dumped at the low (capitulation)
        "poc_pos": (poc_p - l) / rng,                           # 0 = volume node AT the low, 1 = at the high
    }


def run(A, tf, R, LF):
    n = len(A)
    O = [_f(b.get("open", b.get("open_price"))) for b in A]; C = [_f(b.get("close", b.get("close_price"))) for b in A]
    H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
    YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
    DP = [0.0] * n
    for i in range(n):
        cv = _f(A[i].get("curr_vol")); DP[i] = (_f(A[i].get("buy_vol")) - _f(A[i].get("sell_vol"))) / cv * 100.0 if cv > 0 else 0.0
    LB = 6
    def rev(i): return min(L[i + 1:i + 1 + LF]) >= L[i] and (max(H[i + 1:i + 1 + LF]) - L[i]) / L[i] >= R
    freshlo = [i for i in range(LB, n - LF - 1) if L[i] <= min(L[i - LB:i]) and H[i] > L[i] and (A[i].get("levels"))]
    def hammer(i):
        rng = H[i] - L[i]
        if rng <= 0: return False
        ci = (C[i] - L[i]) / rng; lw = (min(O[i], C[i]) - L[i]) / rng; ds = DP[i] - (DP[i - 2] + DP[i - 1]) / 2
        return ci >= 0.55 and lw >= 0.25 and C[i] > O[i] and ds >= 3
    FP = {i: fp_bottom(A[i]) for i in freshlo}
    freshlo = [i for i in freshlo if FP[i]]
    hf = [i for i in freshlo if hammer(i)]
    base_all = sum(rev(i) for i in freshlo) / len(freshlo)
    base_h = sum(rev(i) for i in hf) / len(hf)
    print("\n%s: %d fresh-low(w/footprint)  base rev %.1f%%  |  hammer %d flags, precision %.1f%%" % (
        tf, len(freshlo), 100 * base_all, len(hf), 100 * base_h))
    feats = ["delta_lowhalf", "delta_uphalf", "buyshare_low3", "sellconc_low3", "poc_pos"]
    def aucset(idxs, fn):
        rv = [FP[i][fn] for i in idxs if rev(i)]; cv = [FP[i][fn] for i in idxs if not rev(i)]
        a = auc_p(rv, cv)[0]
        a25 = auc_p([FP[i][fn] for i in idxs if rev(i) and YR[i] == 2025], [FP[i][fn] for i in idxs if not rev(i) and YR[i] == 2025])[0]
        a26 = auc_p([FP[i][fn] for i in idxs if rev(i) and YR[i] == 2026], [FP[i][fn] for i in idxs if not rev(i) and YR[i] == 2026])[0]
        return a, a25, a26
    print("  footprint feature   AUC(all fresh)      AUC(AMONG HAMMERS = incremental)")
    for fn in feats:
        a, _, _ = aucset(freshlo, fn); ah, ah25, ah26 = aucset(hf, fn)
        inc = " <== incremental" if (abs(ah - 0.5) >= 0.04 and (ah25 - 0.5) * (ah26 - 0.5) > 0) else ""
        print("  %-15s     %.3f              %.3f (%.2f/%.2f)%s" % (fn, a, ah, ah25, ah26, inc))


run(load1h(), "1h ", 0.006, 6)
run(load15(), "15m", 0.004, 6)
