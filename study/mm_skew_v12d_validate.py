"""MMXSKEW v1.2-Dynamic — CANDIDATE: v1.1 + run_pos<=4 + mov_mag_ratio>=1.30.

mov_mag_ratio = mov_mag / trailing-EMA50(mov_mag) — a VOLATILITY-NORMALIZED expansion measure that replaces the
static `mov_mag>=25` with a regime-adaptive ratio (a bucket qualifies when its move is >=1.30x the recent norm,
so it neither blacks out in quiet regimes nor over-fires in loud ones).

REGISTERED BASELINE (this module, no-POC — the numbers the freeze holds and the forward audit compares against):
    RR 1:1.0  n=23  win 69.6%  exp +0.434%      RR 1:1.5  n=21  win 66.7%  exp +0.753%
from 174 base signals -> 29 passing the gate -> 23/21 taken after the non-overlap filter in taken(). (n differs
between RRs because the exit bar differs, so a different set of signals falls inside a still-open trade. The
gated count 29 is NOT a trade count: 29 = 23 + 6 overlap-skipped @1.0 = 21 + 8 @1.5.)

THRESHOLD PROVENANCE — RE-SWEPT 2026-07-21 on the no-POC population. Read this before trusting T at all.
T was originally taken from a sweep in study/mm_skew_sprint.py that ran on the OLD **with-POC** population
(149 signals) and reported T in [1.25,1.40] as a "FLAT PLATEAU ... a robust boundary, not a knife-edge peak".
That justification is RETIRED — it was never valid for this population and the plateau reading is wrong anyway:

 1. NO T IS STATISTICALLY SEPARABLE. Nesting-aware permutation over 10 T levels (41 nested comparisons per RR;
    a two-sample test is invalid because higher-T sets are strict SUBSETS): min p = 0.080 @1:1.0 / 0.086 @1:1.5,
    both for the widest span (1.10 vs 1.45). Zero comparisons reach nominal p<0.05. The whole T-curve is noise.
 2. THE "PLATEAU" IS A MECHANICAL ARTIFACT, NOT EVIDENCE. taken() is bit-identical for T in (1.24716, 1.35539]
    at BOTH RRs — but only because the sole gated signals in that band (i=3263 r=1.29990 run=3; i=2852
    r=1.34367 run=2) are ALREADY overlap-skipped, and a skipped signal never updates `last`, so dropping it is
    a strict no-op. Zero degrees of freedom, zero information about T. Remove the overlap filter and the shape
    inverts to monotone-declining with T=1.25 an outright local PEAK (+0.830 -> +0.815 -> +0.772 @1:1.5).
    Overlap-skip is stratified by run_pos (run=1: 0/13 skipped; run>=2: most), so such "plateaus" appear
    wherever the marginal band happens to hold continuation signals.
 3. T=1.25 SITS ~ON THE LOWER CLIFF, not mid-shelf: 0.00284 above the 1.24716 breakpoint (0.23%) vs 8.4% below
    the upper one. Cost if that signal enters: n 21->22, exp +0.753% -> +0.699% (-0.054pp) — mild, no cliff.
 4. T=1.45/1.50 look better (+0.826/+0.817) and are REJECTED: the lift is exactly one excluded loser (i=2680,
    ratio 1.44855, net -1.008%) swapped against an excluded winner; nesting-aware p = 0.510/0.524, and the null
    p95 for a random 2-trade drop (+0.125/+0.176) EXCEEDS the observed lift. H1/H2 also front-loads.

RESOLVED 2026-07-21 — T MOVED 1.25 -> 1.30, and RE-FROZEN. This is a ROBUSTNESS edit, not a performance one:
the taken set is trade-for-trade IDENTICAL (23/21, same nets, same win%), so nothing was optimised and no
in-sample number improved. What it buys: under mov_mag jitter the T=1.25 taken set changed in 35.9% of draws
at sd 0.5% and 52.4% at sd 1.0%, while T=1.30 changed in 0.0% of both; across 9 EMA periods T=1.30 is never
worse than 1.25 and strictly better at three (EMA 30/35/80), 1.25 better at none; and
sqrt(1.24716*1.35539) = 1.300149 is almost exactly the geometric midpoint of the two MATERIAL cliffs, so it maximises
headroom on both sides instead of perching 0.23% above the lower one. Done while forward n=0, so NO
out-of-sample data was discarded. app/mmxskew_detect.RATIO_MIN was moved in lockstep so the live badge still
equals the frozen gate (parity re-verified: 0/174 disagreements). Per the never-re-tune rule this is the LAST
discretionary edit to this gate — from here the forward tape decides. See study/MMXSKEW_NOPOC.md.

RE-ENTRY CONVENTION — AUDITED 2026-07-21, RESOLVED: convention A stays HARD-LOCKED. `sg["i"] <= last` (skip a
signal firing on the bucket in which the prior trade exited) is a DECLARED EXECUTION RULE, not an implementation
detail. Flipping it to `< last` moves this line's RR1.5 baseline to n=23 exp +0.561%, but the audit rejected the
change: effect sizes are 0.22-0.74 SE (5% needs ~2.0), the sign flips with RR in all three candidates (+ at
1:1.0, - at 1:1.5 — RR is downstream of entry, so a real entry-convention effect cannot reverse with TP
distance), and the delta is a RE-CHAINING not "extra trades" (B also displaces 7 trades downstream; v1.3's
apparent gain is 2 dropped cascade losers, not the marginal trades). Full record: study/MMXSKEW_NOPOC.md
"Execution contract".

NO 1m dependency — mov_mag + its EMA come straight from the 1h buckets. build()/GATE()/taken() are imported by
study/mmxskew_v12d_forward_audit.py. CURVE-FIT RISK: the whole T-curve is in-sample noise (see 1. above), so the
MAGNITUDE will regress regardless of which T is used — the forward audit is the real test. Split-half +
bootstrap/permutation Monte Carlo + drop-best-N.

Run: python study/mm_skew_v12d_validate.py
"""
from __future__ import annotations
import os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import study.mm_skew_feature_matrix as FM
import study.mm_skew_rr_sweep as RR

T_OPT = 1.30      # 1.25 -> 1.30 on 2026-07-21 (robustness, not performance — see THRESHOLD PROVENANCE above)
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
