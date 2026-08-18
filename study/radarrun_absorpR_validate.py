"""VALIDATE the one lead from the RR winner/loser sweep: does high ABSORPTION-R at the breakout bar lift NET RETURN
(thesis: an absorbed wall ejects price further -> bigger runners)? 5m clock candles, both years.

Four gates, each designed to kill a plausible false-positive:
  1. MONOTONICITY  — disjoint absorpR quintiles vs avg net-return, PER YEAR (a real effect is monotone, not one lucky bin).
  2. OOS HOLDOUT   — fit the threshold on 2025, test UNSEEN on 2026-H1 (does the high-absorpR subset beat baseline OOS?).
  3. PERMUTATION   — shuffle absorpR across trades 3000x; how often does chance reproduce the observed Q5-Q1 spread? (p).
  4. SCHEME CHECK  — measured under 1x / 3x / trail: the thesis predicts the lift GROWS toward runner-capturing schemes.
Non-overlap is NOT enforced here (we compare per-trade returns within the same event set, so overlap is common to all
subsets); fee 0.04% RT. DESCRIPTIVE. Usage: python study/radarrun_absorpR_validate.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.wall_breakout_backtest import sim_scale, sim_trail
from study.radarrun_filter_5m_clock import detect_events
from app import absorption as ABS, config

FEE = 0.0004; H = 200; MAXT = 5; TPP = 0.002; SLBUF = 0.003; SLIP = 0.0003   # prop exit: 0.2% TP + candle-capped SL


def build(tf="5m"):
    A = sorted(load_archive(tf, root="study/clock_archive", drop_degenerate=False)[1],
               key=lambda b: float(b.get("start_time", 0) or 0))
    n = len(A)
    O = np.array([float(b.get("open", 0)) for b in A]); C = np.array([float(b.get("close", 0)) for b in A])
    Hi = np.array([float(b.get("high", 0)) for b in A]); Lo = np.array([float(b.get("low", 0)) for b in A])
    yr = np.array([datetime.fromtimestamp(float(b.get("start_time", 0)), tz=timezone.utc).year for b in A])
    ev = detect_events(A, O, C, Hi, Lo)
    R = {"1x": [], "3x": [], "trail": [], "prop0.2": []}; aR = []; years = []; bpct = []
    for (k, side) in sorted(ev):
        if k + 1 >= n:
            continue
        rlo, rhi, band = ev[(k, side)]; s = 1 if side == "S" else -1
        entry = C[k]; L = rhi - rlo
        brk = rhi if side == "S" else rlo; sl = rlo if side == "S" else rhi
        tiers = [brk + s * N * L for N in range(1, MAXT + 1)]
        j0 = k + 1; j1 = min(n, k + 1 + H); ph = Hi[j0:j1]; pl = Lo[j0:j1]; pc = C[j0:j1]
        if len(ph) < 1:
            continue
        try:
            a = ABS.absorption(A, k)[0]
        except Exception:
            a = None
        if a is None or not np.isfinite(a):
            continue
        r1, _ = sim_scale(s, entry, sl, [tiers[0]], [1.0], ph, pl, pc, False)
        r3, _ = sim_scale(s, entry, sl, [tiers[2]], [1.0], ph, pl, pc, False)
        rt, _ = sim_trail(s, entry, sl, tiers, ph, pl, pc)
        # PROP exit: fixed 0.2% TP + candle-capped SL (max(candleLow*(1-buf), rlo) long)
        slp = max(Lo[k] * (1 - SLBUF), rlo) if s > 0 else min(Hi[k] * (1 + SLBUF), rhi)
        tpp = entry * (1 + s * TPP); rp = s * (pc[-1] - entry) / entry - FEE
        for off in range(len(ph)):
            if (pl[off] <= slp) if s > 0 else (ph[off] >= slp):
                rp = s * (slp - entry) / entry - FEE - SLIP; break
            if (ph[off] >= tpp) if s > 0 else (pl[off] <= tpp):
                rp = s * (tpp - entry) / entry - FEE; break
        R["1x"].append(r1 - FEE); R["3x"].append(r3 - FEE); R["trail"].append(rt - FEE); R["prop0.2"].append(rp)
        aR.append(float(a)); years.append(int(yr[k]))
        bpct.append(abs(entry - slp) / entry if entry > 0 else np.nan)   # prop-exit SL distance (the fixed-TP geometry)
    return {k: np.array(v) for k, v in R.items()}, np.array(aR), np.array(years), np.array(bpct)


def quintile_spread(x, r):
    """avg-return of the top absorpR quintile minus the bottom quintile (%)."""
    q = np.quantile(x, [0.2, 0.8])
    lo = r[x <= q[0]]; hi = r[x >= q[1]]
    return (hi.mean() - lo.mean()) * 100.0 if len(lo) and len(hi) else np.nan


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "5m"
    R, aR, years, bpct = build(tf)
    N = len(aR)
    print("%s clock RR trades with absorpR: %d  (2025=%d, 2026=%d)\n" % (
        tf, N, (years == 2025).sum(), (years == 2026).sum()), flush=True)

    for sch in ("prop0.2", "1x", "3x", "trail"):
        r = R[sch]
        print("======== SCHEME %s  (baseline avg net-return: 2025 %+.4f%% / 2026 %+.4f%%) ========"
              % (sch, 100 * r[years == 2025].mean(), 100 * r[years == 2026].mean()), flush=True)
        # 1. MONOTONICITY — disjoint absorpR quintiles per year
        for Y in (2025, 2026):
            m = years == Y; x = aR[m]; rr = r[m]
            qs = np.quantile(x, [0.2, 0.4, 0.6, 0.8])
            bins = np.digitize(x, qs)
            avg = [100 * rr[bins == b].mean() if (bins == b).any() else np.nan for b in range(5)]
            print("   %d absorpR Q1..Q5 avg-ret%%: %s" % (Y, "  ".join("%+.3f" % a for a in avg)), flush=True)
        # 2. OOS HOLDOUT — threshold = 2025 top-tercile cut, applied to 2026
        thr = np.quantile(aR[years == 2025], 2 / 3)
        te = years == 2026; hi = R[sch][te & (aR >= thr)]; base = R[sch][te]
        print("   OOS(2026): threshold absorpR>=%.3f (from 2025)  ->  high-set %+.4f%% (n=%d)  vs baseline %+.4f%% (n=%d)"
              % (thr, 100 * hi.mean(), len(hi), 100 * base.mean(), len(base)), flush=True)
        # 3. PERMUTATION NULL — shuffle absorpR, how often chance beats the observed pooled Q5-Q1 spread
        obs = quintile_spread(aR, r)
        rng = np.random.default_rng(7); ge = 0; NP = 3000
        for _ in range(NP):
            ge += quintile_spread(rng.permutation(aR), r) >= obs
        print("   pooled Q5-Q1 return spread = %+.4f%%   permutation p(>=obs) = %.3f (n_perm=%d)\n"
              % (obs, ge / NP, NP), flush=True)

    # ---- IS THE PROP-EXIT EFFECT GENUINE OR GEOMETRIC? control for SL distance (R:R, since TP is fixed 0.2%) ----
    r1 = R["prop0.2"]; fin = np.isfinite(bpct)
    cc = np.corrcoef(aR[fin], bpct[fin])[0, 1]
    print("======== PROP 0.2%% EFFECT: geometric control ========")
    print("   corr(absorpR, SL-distance) = %+.3f  (if strongly -, high absorpR = tighter SL = mechanically better R:R)"
          % cc, flush=True)
    bq = np.quantile(bpct[fin], [1 / 3, 2 / 3])
    bband = np.digitize(bpct, bq)
    print("   prop absorpR Q5-Q1 return spread WITHIN each SL-distance tercile (survives control if still clearly +):")
    for band in range(3):
        m = (bband == band) & np.isfinite(aR)
        if m.sum() < 120:
            print("     SLdist band %d: n<120" % band); continue
        print("     SLdist band %d (n=%d): %+.4f%%" % (band, m.sum(), quintile_spread(aR[m], r1[m])), flush=True)
    # OOS 2026 bootstrap CI on the high-absorpR prop lift vs baseline
    thr = np.quantile(aR[years == 2025], 2 / 3); te = years == 2026
    hi = r1[te & (aR >= thr)]; base = r1[te]
    rng = np.random.default_rng(11); diffs = []
    for _ in range(3000):
        diffs.append(rng.choice(hi, len(hi)).mean() - rng.choice(base, len(base)).mean())
    lo95, hi95 = np.percentile(diffs, [2.5, 97.5])
    print("   OOS(2026) 1x lift high-vs-baseline = %+.4f%%  bootstrap 95%% CI [%+.4f, %+.4f]%%  (crosses 0? %s)"
          % (100 * (hi.mean() - base.mean()), 100 * lo95, 100 * hi95, "YES" if lo95 < 0 < hi95 else "no"), flush=True)


if __name__ == "__main__":
    main()
