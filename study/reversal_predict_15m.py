# -*- coding: utf-8 -*-
"""PREDICTIVE reversal study + detector calibration on 15m (fires ON candle3). Same framing as 1h:
candidate = fresh LB-bar extreme; outcome (score only) = holds + reverses R within LF bars; features = candles 1,2,3."""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f, auc_p

_, rows, _ = load_archive("15m", root="study/recon_archive")
A = sorted(rows, key=lambda b: _f(b.get("start_time", 0)))
n = len(A)
O = [_f(b.get("open_price")) for b in A]; C = [_f(b.get("close_price")) for b in A]
H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
DP = [0.0] * n
for i in range(n):
    cv = _f(A[i].get("curr_vol")); DP[i] = (_f(A[i].get("buy_vol")) - _f(A[i].get("sell_vol"))) / cv * 100.0 if cv > 0 else 0.0

LB = 6; LF = 6; R = 0.004      # 15m: 0.4% reverse within 6 bars

def rev_lo(i): return min(L[i + 1:i + 1 + LF]) >= L[i] and (max(H[i + 1:i + 1 + LF]) - L[i]) / L[i] >= R
def rev_hi(i): return max(H[i + 1:i + 1 + LF]) <= H[i] and (H[i] - min(L[i + 1:i + 1 + LF])) / H[i] >= R

freshlo = [i for i in range(LB, n - LF - 1) if L[i] <= min(L[i - LB:i]) and H[i] > L[i]]
base = sum(rev_lo(i) for i in freshlo) / len(freshlo)
print("15m: %d buckets  fresh-low candidates %d  base reversal %.1f%% (R=%.1f%%/%d bars)" % (n, len(freshlo), 100 * base, R * 100, LF))

# AUC of candle-3 features (bottom) among fresh-low candidates
def feat(i):
    rng = H[i] - L[i]
    return {"c3_cir": (C[i] - L[i]) / rng, "c3_lwick": (min(O[i], C[i]) - L[i]) / rng,
            "c3_bull": 1.0 if C[i] > O[i] else 0.0, "c3_delta": DP[i],
            "delta_shift": DP[i] - (DP[i - 2] + DP[i - 1]) / 2.0, "c3_range": rng / O[i] * 100.0}
rows_ = []
for st in ("c3_cir", "c3_lwick", "c3_bull", "c3_delta", "delta_shift", "c3_range"):
    rv = [feat(i)[st] for i in freshlo if rev_lo(i)]; cv = [feat(i)[st] for i in freshlo if not rev_lo(i)]
    auc = auc_p(rv, cv)[0]
    a25 = auc_p([feat(i)[st] for i in freshlo if rev_lo(i) and YR[i] == 2025], [feat(i)[st] for i in freshlo if not rev_lo(i) and YR[i] == 2025])[0]
    a26 = auc_p([feat(i)[st] for i in freshlo if rev_lo(i) and YR[i] == 2026], [feat(i)[st] for i in freshlo if not rev_lo(i) and YR[i] == 2026])[0]
    rows_.append((abs(auc - 0.5), st, auc, a25, a26))
print("  predictive AUC (bottom):  " + "  ".join("%s %.3f(%.2f/%.2f)" % (st, auc, a25, a26) for _, st, auc, a25, a26 in sorted(rows_, reverse=True)))

print("\n  detector calibration (CIR, LWICK, bull, DS):  flags  precision(25/26)  recall")
totrev = sum(rev_lo(i) for i in freshlo)
def cal(CIR, LWICK, bull, DS):
    fl = []
    for i in freshlo:
        rng = H[i] - L[i]
        cir = (C[i] - L[i]) / rng; lw = (min(O[i], C[i]) - L[i]) / rng; ds = DP[i] - (DP[i - 2] + DP[i - 1]) / 2
        if cir >= CIR and lw >= LWICK and (not bull or C[i] > O[i]) and ds >= DS:
            fl.append(i)
    if not fl: print("    CIR%.2f LW%.2f b%d DS%d  0" % (CIR, LWICK, bull, DS)); return
    hit = sum(rev_lo(i) for i in fl)
    h25 = sum(rev_lo(i) for i in fl if YR[i] == 2025); f25 = sum(1 for i in fl if YR[i] == 2025)
    h26 = sum(rev_lo(i) for i in fl if YR[i] == 2026); f26 = sum(1 for i in fl if YR[i] == 2026)
    print("    CIR%.2f LW%.2f b%d DS%-2d  %5d   %5.1f%% (%.0f/%.0f)   %.0f%%" % (
        CIR, LWICK, bull, DS, len(fl), 100 * hit / len(fl), 100 * h25 / max(1, f25), 100 * h26 / max(1, f26), 100 * hit / totrev))
for cfg in ((0.50, 0.20, 0, 0), (0.55, 0.25, 1, 3), (0.60, 0.30, 1, 3), (0.60, 0.30, 1, 5), (0.65, 0.35, 1, 8)):
    cal(*cfg)
