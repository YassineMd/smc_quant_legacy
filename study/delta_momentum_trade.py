# -*- coding: utf-8 -*-
"""Is the confirm-vs-oppose 88% a TRADEABLE delta-momentum edge, or geometry + fee-bound?

The session-level "confirming bubble -> 88% reject" was (a) NOT level-specific (placebo = same) and (b) geometry-
aided (the net-sell candle already closed low, so the reject target sat closer). Stripped of the level it is just:
"a strong net-delta candle -> price continues in the delta direction." Test THAT directly and tradeably:

  signal  : |candle net-delta%| >= T  (sweep T)
  trade   : enter at close in the delta direction; SYMMETRIC first-passage TP/SL at +/-R (0.4%), horizon HZ.
  outcome : win% + GROSS/tr + NET/tr (fee 0.08%/side). Non-overlap (>=HZ apart). Both years.

If GROSS/tr >> fee -> real edge. If GROSS ~ 0 -> the 88% was geometry; momentum is fee-bound.
Also report the ASYMMETRIC (level-style: TP near / SL far) to show the high win% there nets ~0 too.
"""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f

R, HZ, FEE, RT = 0.004, 8, 0.0008, 0.0016


def run_test(tf, R=R):
    _, rows, _ = load_archive(tf, root="study/recon_archive")
    A = sorted(rows, key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    C = [_f(b.get("close_price")) for b in A]; H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
    YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
    DP = [0.0] * n
    for i in range(n):
        cv = _f(A[i].get("curr_vol")); DP[i] = (_f(A[i].get("buy_vol")) - _f(A[i].get("sell_vol"))) / cv * 100.0 if cv > 0 else 0.0

    def sym(i, d):
        """symmetric first-passage from close, dir d (+1 long/-1 short). returns gross %."""
        E = C[i]
        tp = E * (1 + R) if d > 0 else E * (1 - R); sl = E * (1 - R) if d > 0 else E * (1 + R)
        for k in range(i + 1, min(n, i + 1 + HZ)):
            if d > 0:
                if H[k] >= tp: return R
                if L[k] <= sl: return -R
            else:
                if L[k] <= tp: return R
                if H[k] >= sl: return -R
        return d * (C[min(n - 1, i + HZ)] - E) / E

    def asym(i, d):
        """level-style: enter at close, TP is the NEAR side (0.2R), SL the FAR side (1.8R) — mimics geometry."""
        E = C[i]; near, far = 0.2 * R, 1.8 * R
        tp = E * (1 + near) if d > 0 else E * (1 - near); sl = E * (1 - far) if d > 0 else E * (1 + far)
        for k in range(i + 1, min(n, i + 1 + HZ)):
            if d > 0:
                if H[k] >= tp: return near
                if L[k] <= sl: return -far
            else:
                if L[k] <= tp: return near
                if H[k] >= sl: return -far
        return d * (C[min(n - 1, i + HZ)] - E) / E

    print("\n=== %s ===  delta-momentum trade | R=%.1f%% HZ=%d fee %.2f%% RT" % (tf, R * 100, HZ, RT * 100))
    print("   [SYMMETRIC TP/SL from entry = the clean tradeability test]")
    for T in (10, 15, 25, 40, 60):
        sig = [i for i in range(6, n - HZ - 1) if abs(DP[i]) >= T]
        taken = []; last = -10**9
        for i in sig:
            if i > last + HZ: taken.append(i); last = i
        if len(taken) < 20: print("   |dP|>=%2d  n=%d (few)" % (T, len(taken))); continue
        g = np.array([sym(i, 1 if DP[i] > 0 else -1) for i in taken])
        net = g - RT
        w = np.mean(g > 0) * 100
        y25 = [k for k, i in enumerate(taken) if YR[i] == 2025]; y26 = [k for k, i in enumerate(taken) if YR[i] == 2026]
        g25 = np.mean(g[y25]) if y25 else 0; g26 = np.mean(g[y26]) if y26 else 0
        print("   |dP|>=%2d  n=%5d  win %4.1f%%  GROSS/tr %+.4f%%  NET/tr %+.4f%%  (gross 25:%+.3f/26:%+.3f)" % (
            T, len(taken), w, 100 * g.mean(), 100 * net.mean(), 100 * g25, 100 * g26))
    print("   [ASYMMETRIC near-TP/far-SL (mimics the level geometry -> high win%%, check it still nets ~0)]")
    for T in (15, 40):
        sig = [i for i in range(6, n - HZ - 1) if abs(DP[i]) >= T]
        taken = []; last = -10**9
        for i in sig:
            if i > last + HZ: taken.append(i); last = i
        g = np.array([asym(i, 1 if DP[i] > 0 else -1) for i in taken])
        print("   |dP|>=%2d  n=%5d  win %4.1f%%  GROSS/tr %+.4f%%  NET/tr %+.4f%%" % (
            T, len(taken), np.mean(g > 0) * 100, 100 * g.mean(), 100 * (g.mean() - RT)))


for tf in ("15m", "1h"):
    run_test(tf, R=0.004 if tf == "15m" else 0.006)
