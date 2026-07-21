"""MMXSKEW v1.2-Relaxed — CANDIDATE: the frozen v1.1 signal + run_pos <= 4 + mov_mag >= 25 (i.e. v1.2 with the
mov_mag threshold relaxed 39 -> 25). NO da2: a pressure test (2026-07-20) showed the da2 condition in the 25-39
mov_mag tier excluded ZERO in-sample trades — `[mm>=39 | (25<=mm<39 & da2>0)] & run<=4` was byte-identical to
`mm>=25 & run<=4`, so da2 is dropped by Occam (and with it the 1m-data dependency). run_pos + mov_mag come
straight from the 1h buckets, so this needs no 1m archive.

Reuses study.mm_skew_gate_v12.all_signals/walk (identical v1.1 signal + exit machinery). build()/GATE()/taken()
are imported by study/mmxskew_v12r_forward_audit.py. Split-half + bootstrap/permutation Monte Carlo + drop-best-N.
In-sample, one regime, short-heavy — forward tape is the real test.

Run: python study/mm_skew_v12r_validate.py
"""
from __future__ import annotations
import os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import study.mm_skew_feature_matrix as FM
import study.mm_skew_gate_v12 as G

MM_MIN = 25.0
RUN_MAX = 4


def GATE(sg):
    """v1.2-Relaxed gate: run_pos <= 4 AND mov_mag >= 25."""
    return sg["run"] <= RUN_MAX and sg["mm"] >= MM_MIN


def sig_np(b):
    """v1.1 signal MINUS the POC-baseline condition. POC DROPPED 2026-07-21: the ablation across every version
    showed it adds no edge (base-level v1.1 with vs without = 53%/52% win, identical expectancy) — it only culls
    ~17% of signals (mostly shorts). Dropping it buys sample size, the binding constraint on every candidate."""
    if b.get("sk") is None:
        return 0
    if b["up"] and b["sk"] > 0 and b["spread"] >= 35 and b["delta"] < 15:
        return 1
    if b["dn"] and b["sk"] < 0 and b["spread"] <= -35:
        return -1
    return 0


def build():
    A, first, _, _ = FM.build()
    sigs = []; rc = 0; prev = 0
    for i in range(first, len(A) - 1):
        s = sig_np(A[i])
        if s == 0:
            continue
        rc = rc + 1 if s == prev else 1; prev = s          # run_pos over the NO-POC signal sequence
        b = A[i]; ref = b["l"] if b["c"] > b["o"] else (b["h"] if b["c"] < b["o"] else b["o"])
        mm = ((((b["c"] * 100.0) / ref) - 100.0) ** 2) * 100.0 if ref > 0 else 0.0
        sigs.append(dict(i=i, side=s, run=rc, mm=mm, t=float(b.get("start_time", 0))))
    return A, sigs


def taken(A, sigs, rr):
    """One-at-a-time taken trades; net as a FRACTION (fee-in), matching the forward audit's stats()."""
    return [dict(side=t["side"], win=t["win"], net=t["net"] / 100.0, t=t["t"]) for t in G.walk(A, sigs, GATE, rr)]


def _maxdd(path):
    peak = np.maximum.accumulate(path); return np.max((peak - path) / peak)


def main():
    np.random.seed(42); SIMS = 20000
    A, sigs = build()
    print("MMXSKEW v1.2-Relaxed  (v1.1 + run_pos<=%d + mov_mag>=%d, NO da2)  — split-half + Monte Carlo\n"
          % (RUN_MAX, int(MM_MIN)))
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
    print("CAVEAT: in-sample, one regime, short-heavy; MC = sampling luck, not regime risk. Forward tape decides.")


if __name__ == "__main__":
    main()
