"""Does a CAUSAL velocity filter improve the Radar Runner? Skip a breakout when the recent tape is dead (mean |ret|/bar
over the last 14 bars, excluding the breakout bar). Shipped spec: entry + candle-SL + fixed 0.5% TP. Canonical
NON-OVERLAP basis. Rigorous, three ways so a lucky threshold can't pass:
  1) DOSE-RESPONSE  - disjoint velocity QUARTILES -> exp-R per quartile. A real effect is a monotone gradient.
  2) BOTH YEARS + FORWARD  - recon 2025 / recon 2026 / live forward, each separately (regime-robust, not a 1-yr fit).
  3) PERMUTATION PLACEBO  - shuffle velocity vs outcome 3000x; p = P(shuffled high-vs-low gap >= real). Filter is only
     'real' if the gradient holds in BOTH recon years AND p is small.
Also reports the practical filter: keep vel>=p33/p50 -> retained n / win / exp-R vs the unfiltered baseline.
1h + 30m. python study/radarrun_velocity_filter.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.radarrun_tp_velocity import get_buckets, signals, FEE, SLIP, H

np.random.seed(12345)
TP_FRAC = 0.005


def rows_with_vel(A, slbuf):
    """(year, net, expR, vel) per NON-OVERLAPPING signal, fix-0.5% TP + candle-SL."""
    sigs, Hi, Lo, C = signals(A, slbuf)
    n = len(C); out = []; last = -1
    for g in sorted(sigs, key=lambda z: z["k"]):
        k = g["k"]
        if k <= last:
            continue
        s = g["s"]; entry = g["entry"]; sl = g["sl"]; dist = g["dist"]; tp = entry * (1 + s * TP_FRAC)
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc = "end"; gross = (s * (C[j1 - 1] - entry) / entry if j1 > j0 else 0.0); off = max(1, j1 - k - 1)
        for o in range(j0, j1):
            if (Lo[o] <= sl) if s > 0 else (Hi[o] >= sl):
                outc = "sl"; gross = s * (sl - entry) / entry; off = o - k; break
            if (Hi[o] >= tp) if s > 0 else (Lo[o] <= tp):
                outc = "tp"; gross = s * (tp - entry) / entry; off = o - k; break
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        out.append((g["y"], net, net / dist, g["vel"])); last = k + off
    return out


def _stat(sub):
    a = np.array([r[1] for r in sub]); rr = np.array([r[2] for r in sub])
    return len(a), 100 * (a > 0).mean(), a.mean() * 100, rr.mean()


def perm_p(rows, N=3000):
    rr = np.array([r[2] for r in rows]); vel = np.array([r[3] for r in rows])
    med = np.median(vel); hi = vel >= med
    if hi.sum() < 8 or (~hi).sum() < 8:
        return None, None
    real = rr[hi].mean() - rr[~hi].mean()
    ge = 0
    for _ in range(N):
        sh = np.random.permutation(rr)
        if sh[hi].mean() - sh[~hi].mean() >= real:
            ge += 1
    return real, ge / N


def analyze(rows, label):
    if len(rows) < 20:
        print("  %-32s n=%d (<20, skip)" % (label, len(rows))); return
    n, w, av, er = _stat(rows)
    print("\n  %s   baseline n=%d win=%.0f%% avg=%+.3f%% expR=%+.3f" % (label, n, w, av, er), flush=True)
    vel = np.array([r[3] for r in rows]); qs = np.quantile(vel, [0.25, 0.5, 0.75])
    print("    dose-response by velocity quartile (Q1 slowest -> Q4 fastest):", flush=True)
    for qi, (lo, hi) in enumerate([(-1e9, qs[0]), (qs[0], qs[1]), (qs[1], qs[2]), (qs[2], 1e9)]):
        g = [r for r in rows if lo <= r[3] < hi] if qi < 3 else [r for r in rows if r[3] >= qs[2]]
        if len(g) >= 8:
            gn, gw, ga, ge = _stat(g)
            print("      Q%d vel[%.3f]  n=%-4d win=%2.0f%% avg=%+.3f%% expR=%+.3f" % (
                qi + 1, np.mean([r[3] for r in g]) * 100, gn, gw, ga, ge), flush=True)
    for p in (0.33, 0.50):
        thr = np.quantile(vel, p); keep = [r for r in rows if r[3] >= thr]
        kn, kw, ka, ke = _stat(keep)
        print("    FILTER keep vel>=p%d (%.3f%%/bar): n=%-4d (%.0f%% kept) win=%2.0f%% avg=%+.3f%% expR=%+.3f  (base %+.3f)" % (
            int(p * 100), thr * 100, kn, 100 * kn / len(rows), kw, ka, ke, er), flush=True)
    real, p = perm_p(rows)
    if p is not None:
        print("    permutation: high-vs-low expR gap=%+.3f  p=%.3f %s" % (
            real, p, "<-- significant" if p < 0.05 else "(not sig)"), flush=True)


def main():
    for tf in ("1h", "30m"):
        slb = 0.002 if tf == "1h" else 0.003
        print("\n################  TF = %s  ################" % tf, flush=True)
        rec = rows_with_vel(get_buckets(tf, {"root": "study/recon_archive"}), slb)
        analyze([r for r in rec if r[0] == 2025], "RECON 2025 ")
        analyze([r for r in rec if r[0] == 2026], "RECON 2026 ")
        try:
            fwd = rows_with_vel(get_buckets(tf, {}), slb)
            analyze(fwd, "FORWARD    ")
        except Exception as e:
            print("  (forward %s skipped: %s)" % (tf, e), flush=True)


if __name__ == "__main__":
    main()
