# -*- coding: utf-8 -*-
"""15m: does a LONGER approach window (5/7/10 candles) add predictive power beyond candle-N's own hammer?
Predictive framing (fire on candle N): candidate = fresh 6-bar low; outcome = holds + reverses 0.4% in 6 bars.
Test approach features (decline, deceleration, delta divergence, repeated wicks, down-run) at W=5,7,10, + an
INCREMENTAL test: does adding the best approach filter to the candle-N hammer baseline improve precision?"""
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
DP = [0.0] * n; LWF = [0.0] * n
for i in range(n):
    cv = _f(A[i].get("curr_vol")); DP[i] = (_f(A[i].get("buy_vol")) - _f(A[i].get("sell_vol"))) / cv * 100.0 if cv > 0 else 0.0
    rng = H[i] - L[i]; LWF[i] = (min(O[i], C[i]) - L[i]) / rng if rng > 0 else 0.0

LB = 6; LF = 6; R = 0.004
def rev_lo(i): return min(L[i + 1:i + 1 + LF]) >= L[i] and (max(H[i + 1:i + 1 + LF]) - L[i]) / L[i] >= R
freshlo = [i for i in range(11, n - LF - 1) if L[i] <= min(L[i - LB:i]) and H[i] > L[i]]
base = sum(rev_lo(i) for i in freshlo) / len(freshlo)
print("15m fresh-low candidates %d  base reversal %.1f%%" % (len(freshlo), 100 * base))

def approach_feats(i, W):
    seg = range(i - W + 1, i + 1); h = W // 2; mid = i - h
    dec_W = (max(H[k] for k in seg) - L[i]) / L[i] * 100.0                 # total decline into the low
    older = C[i - W + 1] - C[mid]; recent = C[mid] - C[i]                  # drop older half vs recent half
    decel = older - recent                                                # >0 = decline SLOWING into the low
    d_recent = sum(DP[k] for k in range(mid + 1, i + 1)); d_old = sum(DP[k] for k in range(i - W + 1, mid + 1))
    delta_div = d_recent - d_old                                          # buying recently vs selling earlier (divergence)
    nwick = sum(1 for k in seg if LWF[k] >= 0.30)                         # repeated lower-wick rejection
    ndown = sum(1 for k in seg if C[k] < O[k])
    run = 0
    for k in range(i - 1, i - W, -1):
        if C[k] < O[k]: run += 1
        else: break
    return {"decline": dec_W, "decel": decel, "delta_div": delta_div, "nwick": float(nwick), "ndown": float(ndown), "run_down": float(run)}

def cir(i):
    rng = H[i] - L[i]; return (C[i] - L[i]) / rng if rng > 0 else 0.0

print("\n  AUC among fresh lows (reversal vs continuation), by window W:")
print("  %-11s " % "feature" + "  ".join("W=%d" % w for w in (5, 7, 10)) + "     (candle-N cir ref = %.3f)" % auc_p([cir(i) for i in freshlo if rev_lo(i)], [cir(i) for i in freshlo if not rev_lo(i)])[0])
for fn in ("decline", "decel", "delta_div", "nwick", "ndown", "run_down"):
    line = "  %-11s " % fn
    for W in (5, 7, 10):
        rv = [approach_feats(i, W)[fn] for i in freshlo if rev_lo(i)]; cv = [approach_feats(i, W)[fn] for i in freshlo if not rev_lo(i)]
        a = auc_p(rv, cv)[0]
        a25 = auc_p([approach_feats(i, W)[fn] for i in freshlo if rev_lo(i) and YR[i] == 2025], [approach_feats(i, W)[fn] for i in freshlo if not rev_lo(i) and YR[i] == 2025])[0]
        a26 = auc_p([approach_feats(i, W)[fn] for i in freshlo if rev_lo(i) and YR[i] == 2026], [approach_feats(i, W)[fn] for i in freshlo if not rev_lo(i) and YR[i] == 2026])[0]
        line += " %.3f(%.2f/%.2f)" % (a, a25, a26)
    print(line)

# INCREMENTAL: does adding an approach filter to the candle-N hammer baseline lift precision?
def hammer(i):
    rng = H[i] - L[i]
    if rng <= 0: return False
    ci = (C[i] - L[i]) / rng; lw = (min(O[i], C[i]) - L[i]) / rng; ds = DP[i] - (DP[i - 2] + DP[i - 1]) / 2
    return ci >= 0.55 and lw >= 0.25 and C[i] > O[i] and ds >= 3
baseflags = [i for i in freshlo if hammer(i)]
bp = sum(rev_lo(i) for i in baseflags) / len(baseflags)
print("\n  baseline candle-N hammer: %d flags, precision %.1f%%" % (len(baseflags), 100 * bp))
print("  + approach filter (W=7):")
for fn, op, thr in (("decel", ">=", 0.0), ("delta_div", ">=", 5.0), ("delta_div", ">=", 15.0),
                    ("nwick", ">=", 2.0), ("run_down", "<=", 2.0), ("run_down", ">=", 3.0), ("decline", ">=", 1.5)):
    sub = [i for i in baseflags if (approach_feats(i, 7)[fn] >= thr if op == ">=" else approach_feats(i, 7)[fn] <= thr)]
    if len(sub) < 30:
        print("    %-10s %s %-4g   n=%d (few)" % (fn, op, thr, len(sub))); continue
    p = sum(rev_lo(i) for i in sub) / len(sub)
    p25 = sum(rev_lo(i) for i in sub if YR[i] == 2025) / max(1, sum(1 for i in sub if YR[i] == 2025))
    p26 = sum(rev_lo(i) for i in sub if YR[i] == 2026) / max(1, sum(1 for i in sub if YR[i] == 2026))
    print("    %-10s %s %-5g  n=%4d  precision %.1f%% (%.0f/%.0f)  [base %.1f%%]" % (fn, op, thr, len(sub), 100 * p, 100 * p25, 100 * p26, 100 * bp))
