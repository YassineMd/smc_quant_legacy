"""MMXSKEW v1.2-Dynamic — CANDIDATE: v1.1 + run_pos<=4 + mov_mag_ratio>=1.25.

mov_mag_ratio = mov_mag / trailing-EMA50(mov_mag) — a VOLATILITY-NORMALIZED expansion measure that replaces the
static `mov_mag>=25` with a regime-adaptive ratio (a bucket qualifies when its move is >=1.25x the recent norm,
so it neither blacks out in quiet regimes nor over-fires in loud ones). T=1.25 was chosen from an in-sample fine
sweep (study/mm_skew_sprint.py): T in [1.25,1.40] is a FLAT PLATEAU (n=16, 69/73% win, identical metrics — a
robust boundary, not a knife-edge peak), so 1.25 = the low / most-trades edge of the plateau.

NO 1m dependency — mov_mag + its EMA come straight from the 1h buckets. build()/GATE()/taken() are imported by
study/mmxskew_v12d_forward_audit.py. CURVE-FIT RISK: T was tuned in-sample from 19 candidates — the MAGNITUDE will
regress; the forward audit is the real test. Split-half + bootstrap/permutation Monte Carlo + drop-best-N.

Run: python study/mm_skew_v12d_validate.py
"""
from __future__ import annotations
import os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import study.mm_skew_feature_matrix as FM
import study.mm_skew_rr_sweep as RR

T_OPT = 1.25
RUN_MAX = 4


def _mm(b):
    ref = b["l"] if b["c"] > b["o"] else (b["h"] if b["c"] < b["o"] else b["o"])
    return ((((b["c"] * 100) / ref) - 100) ** 2) * 100 if ref > 0 else 0.0


def GATE(sg):
    """v1.2-Dynamic gate: run_pos <= 4 AND mov_mag_ratio >= T_OPT."""
    return sg["run"] <= RUN_MAX and sg["ratio"] >= T_OPT


def sig_np(b):
    """v1.1 signal MINUS the POC-baseline condition. POC DROPPED 2026-07-21 (ablation: no edge, only culls ~17%
    of signals — mostly shorts). Dropping it buys sample size, the binding constraint on every candidate."""
    if b.get("sk") is None:
        return 0
    if b["up"] and b["sk"] > 0 and b["spread"] >= 35 and b["delta"] < 15:
        return 1
    if b["dn"] and b["sk"] < 0 and b["spread"] <= -35:
        return -1
    return 0


def build():
    A, first, _, _ = FM.build()
    mm_all = [_mm(b) for b in A]; ratio = [1.0] * len(A); ema = None
    for k in range(len(A)):                          # trailing EMA-50 of mov_mag, EXCLUDES current bucket
        ratio[k] = (mm_all[k] / ema) if (ema and ema > 0) else 1.0
        ema = mm_all[k] if ema is None else mm_all[k] * (2 / 51) + ema * (1 - 2 / 51)
    sigs = []; rc = 0; prev = 0
    for i in range(first, len(A) - 1):
        s = sig_np(A[i])                             # v1.1 minus POC (run_pos over the NO-POC sequence)
        if s == 0:
            continue
        rc = rc + 1 if s == prev else 1; prev = s
        sigs.append(dict(i=i, side=s, t=float(A[i].get("start_time", 0)), run=rc, ratio=ratio[i]))
    return A, sigs


def taken(A, sigs, rr):
    """One-at-a-time taken trades; net as a FRACTION (fee-in), matching the audit's stats()."""
    last = -1; out = []
    for sg in sigs:
        if sg["i"] <= last or not GATE(sg):
            continue
        res = RR.simulate_rr(A, sg["i"], sg["side"], rr, "sl")
        if res is None:
            continue
        out.append(dict(side=sg["side"], win=(res[0] == "TP"), net=res[1] - 0.0008, t=sg["t"])); last = res[2]
    return sorted(out, key=lambda z: z["t"])


def _maxdd(path):
    peak = np.maximum.accumulate(path); return np.max((peak - path) / peak)


def main():
    np.random.seed(42); SIMS = 20000
    A, sigs = build()
    print("MMXSKEW v1.2-Dynamic  (v1.1 + run_pos<=%d + mov_mag_ratio>=%.2f)  — split-half + Monte Carlo\n"
          % (RUN_MAX, T_OPT))
    for rr in (1.0, 1.5):
        T = sorted(taken(A, sigs, rr), key=lambda z: z["t"]); n = len(T)
        if n == 0:
            print("RR 1:%s  n=0" % rr); continue
        r = np.array([t["net"] for t in T]); w = sum(t["win"] for t in T)
        L = [t for t in T if t["side"] > 0]; Sh = [t for t in T if t["side"] < 0]
        tot = (np.prod(1 + r) - 1) * 100; dd = _maxdd(np.cumprod(1 + r)) * 100
        print("=" * 88); print("RR 1:%s   n=%d (%dL/%dS)" % (rr, n, len(L), len(Sh))); print("=" * 88)
        print("  ACTUAL: win %.0f%% (L %.0f%% / S %.0f%%)  net %+.1f%%  mean/tr %+.3f%%  maxDD %.1f%%"
              % (100 * w / n, 100 * np.mean([t["win"] for t in L]) if L else 0,
                 100 * np.mean([t["win"] for t in Sh]) if Sh else 0, tot, r.mean() * 100, dd))
        mid = n // 2

        def hh(x):
            return "win %.0f%% net %+.1f%% (n%d)" % (100 * np.mean([1 if v > 0 else 0 for v in x]),
                                                     (np.prod(1 + x) - 1) * 100, len(x))
        print("  SPLIT-HALF: H1 %s | H2 %s" % (hh(r[:mid]), hh(r[mid:])))
        for k in (1, 2, 3):
            rem = np.sort(r)[:n - k]
            print("  drop-best-%d: net %+.1f%%  exp %+.3f%%  win %.0f%%"
                  % (k, (np.prod(1 + rem) - 1) * 100, rem.mean() * 100, 100 * np.mean([1 if x > 0 else 0 for x in rem])))
        samp = r[np.random.randint(0, n, size=(SIMS, n))]; fin = (np.prod(1 + samp, axis=1) - 1) * 100
        means = samp.mean(axis=1) * 100
        print("  MC: P(profit)=%.1f%%  edge/tr 95%%CI [%+.3f, %+.3f]%%  total p5/50/95 %+.0f/%+.0f/%+.0f%%"
              % (100 * np.mean(fin > 0), np.percentile(means, 2.5), np.percentile(means, 97.5),
                 np.percentile(fin, 5), np.percentile(fin, 50), np.percentile(fin, 95)))
        perm = np.array([np.random.permutation(r) for _ in range(SIMS)])
        pdd = np.array([_maxdd(np.cumprod(1 + p)) for p in perm]) * 100
        print("      perm maxDD p50 %.1f%% | p95 %.1f%%\n" % (np.percentile(pdd, 50), np.percentile(pdd, 95)))
    print("CAVEAT: T tuned in-sample (curve-fit) — magnitude WILL regress. One regime, small n. Forward tape decides.")


if __name__ == "__main__":
    main()
