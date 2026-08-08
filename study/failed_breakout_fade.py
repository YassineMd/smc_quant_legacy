# -*- coding: utf-8 -*-
"""FAILED-BREAKOUT FADE — is the 73% real, and is it tradeable?

The continuation study found: a breakout candle with a dominant single-level bubble (spike>=25) FAILS ~73%.
Two things must survive before we trust it:
  (1) THINNESS control: spike is a SHARE (hottest level / candle total) -> high on thin candles. Re-test with an
      ABSOLUTE bubble (hottest level vol / rolling-median candle vol) and by requiring a THICK candle.
  (2) TRADEABILITY: 73% "fail" used a loose reference. Simulate the actual FADE (short up-breaks / long down-breaks):
      entry = candle close, structural stop = candle extreme, target = candle opposite end. Net of FEE=0.0008/side.

Both years, first-touch fill (stop wins ties = conservative), horizon HZ bars.
"""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f

LB, CIR = 6, 0.55
FEE = 0.0008
RT = 2 * FEE            # round-trip fee


def maxlevel(b):
    lv = b.get("levels") or {}
    mx = 0.0
    for vv in lv.values():
        t = _f(vv.get("b")) + _f(vv.get("s"))
        if t > mx: mx = t
    return mx


def spikeshare(b):
    lv = b.get("levels") or {}
    tot = 0.0; mx = 0.0
    for vv in lv.values():
        t = _f(vv.get("b")) + _f(vv.get("s")); tot += t
        if t > mx: mx = t
    return (mx / tot * 100.0) if tot > 0 else 0.0


def run_test(tf, R, HZ=8):
    _, rows, _ = load_archive(tf, root="study/recon_archive")
    A = sorted(rows, key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    O = [_f(b.get("open_price")) for b in A]; C = [_f(b.get("close_price")) for b in A]
    H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
    CV = np.array([_f(b.get("curr_vol")) for b in A])
    YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]

    def fade(i, up):
        """Structural fade: up-break -> SHORT at C[i], stop=H[i], target=L[i]. Returns net% (fees in) or None."""
        E = C[i]
        if up:
            stop, tgt = H[i], L[i]
            for k in range(i + 1, min(n, i + 1 + HZ)):
                if H[k] >= stop: return -(stop - E) / E - RT       # stopped (loss)
                if L[k] <= tgt: return (E - tgt) / E - RT          # target (win)
            return (E - C[min(n - 1, i + HZ)]) / E - RT            # time exit
        else:
            stop, tgt = L[i], H[i]
            for k in range(i + 1, min(n, i + 1 + HZ)):
                if L[k] <= stop: return -(E - stop) / E - RT
                if H[k] >= tgt: return (tgt - E) / E - RT
            return (C[min(n - 1, i + HZ)] - E) / E - RT

    cand = []
    for i in range(LB, n - HZ - 1):
        rng = H[i] - L[i]
        if rng <= 0 or O[i] <= 0: continue
        up = None
        if H[i] >= max(H[i - LB:i]) and C[i] > O[i] and (C[i] - L[i]) / rng >= CIR: up = True
        elif L[i] <= min(L[i - LB:i]) and C[i] < O[i] and (H[i] - C[i]) / rng >= CIR: up = False
        if up is None: continue
        if not (A[i].get("levels")): continue
        volmed = np.median(CV[max(0, i - 200):i]) if i > 5 else CV[i]
        cand.append({"i": i, "up": up, "yr": YR[i], "spike": spikeshare(A[i]),
                     "volr": (CV[i] / volmed) if volmed > 0 else 1.0,
                     "bigbub": (maxlevel(A[i]) / volmed) if volmed > 0 else 0.0,
                     "pnl": fade(i, up)})

    def report(name, sel):
        sel = [c for c in sel if c["pnl"] is not None]
        t = []; last = -10**9
        for c in sel:                                    # non-overlap
            if c["i"] > last + HZ: t.append(c); last = c["i"]
        if not t: print("   %-30s n=0" % name); return
        wins = sum(1 for c in t if c["pnl"] > 0); net = sum(c["pnl"] for c in t)
        n25 = [c for c in t if c["yr"] == 2025]; n26 = [c for c in t if c["yr"] == 2026]
        w25 = (sum(1 for c in n25 if c["pnl"] > 0) / len(n25) * 100) if n25 else 0
        w26 = (sum(1 for c in n26 if c["pnl"] > 0) / len(n26) * 100) if n26 else 0
        print("   %-30s n=%4d  win %4.1f%%  net/tr %+.3f%%  tot %+6.1f%%  (25:%4.1f%%/26:%4.1f%%)" % (
            name, len(t), 100 * wins / len(t), 100 * net / len(t), 100 * net, w25, w26))

    print("\n=== %s ===  breakout candidates(with footprint) %d  | HZ=%d bars, fee %.2f%% RT | fade = short up / long down" % (
        tf, len(cand), HZ, RT * 100))
    print("   [structural: entry=close, stop=candle extreme, target=candle opposite end]")
    report("ALL breakouts (baseline fade)", cand)
    report("spike>=25 (share, uncontrolled)", [c for c in cand if c["spike"] >= 25])
    report("spike>=25 & THICK (volr>=1)", [c for c in cand if c["spike"] >= 25 and c["volr"] >= 1.0])
    report("spike>=25 & THIN (volr<1)", [c for c in cand if c["spike"] >= 25 and c["volr"] < 1.0])
    report("bigbub>=0.5 (abs big node)", [c for c in cand if c["bigbub"] >= 0.5])
    report("bigbub>=0.8 (abs BIG node)", [c for c in cand if c["bigbub"] >= 0.8])
    report("bigbub>=0.8 & THICK", [c for c in cand if c["bigbub"] >= 0.8 and c["volr"] >= 1.0])


for tf, R in (("15m", 0.004), ("1h", 0.006)):
    run_test(tf, R)
