"""Monte Carlo on the MM×Skew v1.1 strategy (study/MMXSKEW.md: + long delta<15 filter).
Per-trade net returns (notional=balance, so each trade multiplies equity by 1+retf-fee) are the input.
  (A) BOOTSTRAP (resample n trades WITH replacement, 20k sims) -> distribution of the 28-day return,
      P(profitable), percentiles, and a bootstrap test of positive per-trade expectancy.
  (B) PERMUTATION (shuffle the actual trade ORDER, 20k sims) -> max-drawdown distribution (sequence risk).
CAVEAT: MC treats the ~80 observed trades as representative -> it quantifies SEQUENCE + SAMPLING luck ONLY,
NOT regime/overfitting risk. One 28-day sample; the true forward edge could sit outside this cloud.
Run:  python study/mm_skew_montecarlo.py
"""
from __future__ import annotations
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import study.mm_skew_poc as P
import study.mm_skew_rr_sweep as RR

FEE = 0.0008
np.random.seed(42)


def build():
    M, span = P.build()
    for i in range(len(M)):
        b = M[i]; cv = float(b.get("curr_vol", 0)) or 1.0
        b["delta"] = (float(b.get("buy_vol", 0)) - float(b.get("sell_vol", 0))) / cv * 100
    return M, span


def sigf(b):                       # v1.1: frozen rule + long delta<15
    s = P.sig(b)
    if s == 1 and b["delta"] >= 15:
        return 0
    return s


def taken(M, rr):
    i = 0; rets = []
    while i < len(M) - 1:
        s = sigf(M[i])
        if s == 0:
            i += 1; continue
        res = RR.simulate_rr(M, i, s, rr, "sl")
        if res is None:
            i += 1; continue
        rets.append(res[1] - FEE)
        i = res[2] + 1
    return np.array(rets)


def maxdd(path):                   # path = equity multiples (starts at 1)
    peak = np.maximum.accumulate(path, axis=-1)
    return np.max((peak - path) / peak, axis=-1)


def main():
    M, span = build()
    SIMS = 20000
    for rr in (1.0, 1.5):
        r = taken(M, rr); n = len(r)
        win = 100 * np.mean(r > 0); mean = r.mean(); actual = np.prod(1 + r) - 1
        act_dd = maxdd(np.cumprod(1 + r)) * 100
        print("=" * 92)
        print(f"RR 1:{rr}   n={n} trades  win={win:.1f}%  mean/trade={mean*100:+.3f}%  "
              f"ACTUAL 28d return={actual*100:+.1f}%  maxDD={act_dd:.1f}%")
        print("=" * 92)

        # (A) bootstrap with replacement
        idx = np.random.randint(0, n, size=(SIMS, n))
        samp = r[idx]
        finals = (np.prod(1 + samp, axis=1) - 1) * 100
        means = samp.mean(axis=1)
        pct = np.percentile(finals, [5, 25, 50, 75, 95])
        print("  (A) BOOTSTRAP  28-day return distribution (%):")
        print(f"      p5 {pct[0]:+.1f} | p25 {pct[1]:+.1f} | median {pct[2]:+.1f} | p75 {pct[3]:+.1f} | p95 {pct[4]:+.1f}")
        print(f"      P(profit) = {100*np.mean(finals>0):.1f}%   P(>+5%) = {100*np.mean(finals>5):.1f}%   "
              f"P(<-5%) = {100*np.mean(finals<-5):.1f}%")
        print(f"      bootstrap P(mean per-trade > 0) = {100*np.mean(means>0):.1f}%  "
              f"(95% CI mean/trade [{np.percentile(means,2.5)*100:+.3f}, {np.percentile(means,97.5)*100:+.3f}]%)")

        # (B) permutation of trade order -> drawdown risk
        perm = np.array([np.random.permutation(r) for _ in range(SIMS)])
        dd = maxdd(np.cumprod(1 + perm, axis=1)) * 100
        ddp = np.percentile(dd, [50, 75, 95, 99])
        print("  (B) PERMUTATION  max-drawdown distribution (%):")
        print(f"      median {ddp[0]:.1f} | p75 {ddp[1]:.1f} | p95 {ddp[2]:.1f} | p99 {ddp[3]:.1f}  "
              f"(actual-order DD was {act_dd:.1f}%)")
        print()


if __name__ == "__main__":
    main()
