# -*- coding: utf-8 -*-
"""PREDICTIVE reversal study (1h): at candle3's CLOSE, using ONLY candles 1,2,3, is candle3 a reversal?
Candidate = candle3 makes a FRESH local extreme (lowest low / highest high of the last LB). Among candidates,
LABEL (forward, for scoring ONLY) = it HOLDS and reverses: no new extreme in the next LF bars AND price travels
>=R the other way. Features from candles [i-2,i-1,i] only. AUC = P(feature higher at reversal vs continuation)."""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from datetime import datetime, timezone
from study.candle_bias_1h import load, _f, auc_p

A = load(); n = len(A)
O = [b["open"] for b in A]; C = [b["close"] for b in A]
H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
DP = [0.0] * n
for i in range(n):
    cv = _f(A[i].get("curr_vol"))
    DP[i] = (_f(A[i].get("buy_vol")) - _f(A[i].get("sell_vol"))) / cv * 100.0 if cv > 0 else 0.0

LB = 6          # candle3 must be the extreme over the last LB (fresh extreme)
LF = 6          # forward window for the outcome label
R = 0.006       # bounce/drop the other way that counts as a reversal (0.6% on 1h)

def feats_bottom(i):
    """3-candle features known at candle3=i (all causal). Oriented for a BOTTOM (candle3 = fresh low)."""
    rng3 = H[i] - L[i]
    if rng3 <= 0 or O[i] <= 0:
        return None
    a = i - 2
    mx3 = max(H[a], H[i - 1], H[i]); mn3 = min(L[a], L[i - 1], L[i]); wr = mx3 - mn3
    return {
        "c3_lwick": (min(O[i], C[i]) - L[i]) / rng3,                 # candle3 lower wick (rejection of its own low)
        "c3_cir": (C[i] - L[i]) / rng3,                             # candle3 close position (closed off its low)
        "c3_range": rng3 / O[i] * 100.0,
        "c3_bull": 1.0 if C[i] > O[i] else 0.0,                     # candle3 turned up within itself
        "c3_delta": DP[i],                                          # candle3 net flow (buying INTO the low = absorption)
        "absorb": DP[i] if C[i] < O[i] else 0.0,                    # down candle but positive delta = absorption
        "delta_shift": DP[i] - (DP[a] + DP[i - 1]) / 2.0,          # flow shift at candle3 vs the 2 approach candles
        "newlow_depth": (min(L[a], L[i - 1]) - L[i]) / L[i] * 100.0,  # how far candle3 extends below the prior 2 lows
        "approach": (max(H[a], H[i - 1]) - L[i]) / L[i] * 100.0,    # decline into the low (swing being exhausted)
        "wrange3": wr / O[a] * 100.0 if O[a] > 0 else 0.0,
    }

def feats_top(i):
    rng3 = H[i] - L[i]
    if rng3 <= 0 or O[i] <= 0:
        return None
    a = i - 2
    mx3 = max(H[a], H[i - 1], H[i]); mn3 = min(L[a], L[i - 1], L[i]); wr = mx3 - mn3
    return {
        "c3_uwick": (H[i] - max(O[i], C[i])) / rng3,
        "c3_cir_inv": (H[i] - C[i]) / rng3,                        # closed off its high
        "c3_range": rng3 / O[i] * 100.0,
        "c3_bear": 1.0 if C[i] < O[i] else 0.0,
        "c3_delta_neg": -DP[i],
        "absorb": -DP[i] if C[i] > O[i] else 0.0,                  # up candle but negative delta = absorption
        "delta_shift": (DP[a] + DP[i - 1]) / 2.0 - DP[i],
        "newhigh_depth": (H[i] - max(H[a], H[i - 1])) / H[i] * 100.0,
        "approach": (H[i] - min(L[a], L[i - 1])) / H[i] * 100.0,
        "wrange3": wr / O[a] * 100.0 if O[a] > 0 else 0.0,
    }

def run(is_bottom):
    ff = feats_bottom if is_bottom else feats_top
    cand = []                                                       # (i, features, is_reversal)
    for i in range(LB, n - LF - 1):
        if is_bottom:
            if L[i] > min(L[i - LB:i]):                            # not a fresh LOW -> skip
                continue
            fut_lo = min(L[i + 1:i + 1 + LF]); fut_hi = max(H[i + 1:i + 1 + LF])
            rev = (fut_lo >= L[i]) and ((fut_hi - L[i]) / L[i] >= R)   # held + bounced R
        else:
            if H[i] < max(H[i - LB:i]):
                continue
            fut_hi = max(H[i + 1:i + 1 + LF]); fut_lo = min(L[i + 1:i + 1 + LF])
            rev = (fut_hi <= H[i]) and ((H[i] - fut_lo) / H[i] >= R)
        fe = ff(i)
        if fe is None:
            continue
        cand.append((i, fe, rev))
    nrev = sum(1 for _, _, r in cand if r)
    tag = "BOTTOM (fresh low)" if is_bottom else "TOP (fresh high)"
    print("\n=== %s : %d candidates, %d reverse (%.1f%% base) ===" % (tag, len(cand), nrev, 100 * nrev / max(1, len(cand))))
    names = list(cand[0][1].keys())
    rows = []
    for st in names:
        rv = [fe[st] for _, fe, r in cand if r and fe[st] == fe[st]]
        cv = [fe[st] for _, fe, r in cand if not r and fe[st] == fe[st]]
        if len(rv) < 20 or len(cv) < 20:
            continue
        auc, p, _, _ = auc_p(rv, cv)
        r25 = [fe[st] for i, fe, r in cand if r and YR[i] == 2025]; c25 = [fe[st] for i, fe, r in cand if not r and YR[i] == 2025]
        r26 = [fe[st] for i, fe, r in cand if r and YR[i] == 2026]; c26 = [fe[st] for i, fe, r in cand if not r and YR[i] == 2026]
        a25 = auc_p(r25, c25)[0] if r25 and c25 else float("nan")
        a26 = auc_p(r26, c26)[0] if r26 and c26 else float("nan")
        rows.append((abs(auc - 0.5), st, auc, p, sum(rv) / len(rv), sum(cv) / len(cv), a25, a26))
    rows.sort(reverse=True)
    print("%-13s  %6s %8s  %10s %10s  %5s/%5s" % ("feature", "AUC", "p", "mean@rev", "mean@cont", "25", "26"))
    for _, st, auc, p, mr, mc, a25, a26 in rows:
        star = " *" if (abs(auc - 0.5) >= 0.04 and p < 0.01 and (a25 - 0.5) * (a26 - 0.5) > 0) else ""
        print("%-13s  %6.3f %8.4f  %10.4f %10.4f  %5.2f/%5.2f%s" % (st, auc, p, mr, mc, a25, a26, star))

print("recon 1h: %d  |  candidate=fresh %d-bar extreme, outcome=hold + %.1f%% reverse within %d bars" % (n, LB, R * 100, LF))
run(True)
run(False)
