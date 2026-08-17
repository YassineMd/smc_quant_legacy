"""HONEST win rate + DD for 30m @ 0.30% TP, one-position-at-a-time, via MONTE-CARLO over trade SELECTION (not the single
deterministic 'always take the first' path). Each path walks time and, whenever flat, catches the next available signal
with prob `catch` -> a RANDOM one-at-a-time subset. Over many paths we get the sampling distribution of win% and max-DD.
catch=1.0 = diligent (take every free signal = the sequential path); catch<1 = you miss some at random. If win% is
stable across paths/catch rates, it is NOT a selection artifact -> it's honest. Also a bootstrap 95% CI on the win rate
(the live sample is small). Reports mean [p5..p95] win%, DD% median/p95. python study/radarrun_30m_honest_mc.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.candle_bias_1h import _f
from study.radarrun_tp_velocity import get_buckets
from study.radarrun_winrate_dd import sim
from study.radarrun_30m_bestsetup import detect

random.seed(7); H = 200; FEE = 0.0004; SLIP = 0.0003; RP = 0.5; N = 6000


def all_signals(A, tp):
    sigs, Hi, Lo, C, n = detect(A); out = []
    for (k, s, entry, sl, dist) in sigs:                    # every detected signal, evaluated independently
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(s, entry, entry * (1 + s * tp), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        out.append((k, k + int(off), net / dist))           # (entry bar, exit bar, R)
    return out                                              # chronological (detect returns sorted by k)


def path(sigs, catch):
    busy = -1; rs = []
    for (k, ex, R) in sigs:
        if k > busy and random.random() < catch:
            rs.append(R); busy = ex
    if not rs:
        return None
    eq = peak = dd = 0.0
    for r in rs:
        eq += RP * r; peak = max(peak, eq); dd = max(dd, peak - eq)
    return 100.0 * np.mean([r > 0 for r in rs]), dd, len(rs)


def main():
    for ds, root in (("RECON", {"root": "study/recon_archive"}), ("DAEMON", {})):
        A = get_buckets("30m", root)
        for tp in (0.002, 0.003):
            sigs = all_signals(A, tp)
            print("\n====  30m %.2f%%-TP  %s  (%d signals total)  ====" % (tp * 100, ds, len(sigs)), flush=True)
            print("  selection MC (one-at-a-time, R=0.5%%):", flush=True)
            print("  %-9s %5s %-22s %-20s" % ("catch", "~n", "win%  mean[p5..p95]", "maxDD%  med / p95"), flush=True)
            for catch in (1.0, 0.8, 0.6, 0.4):
                W = []; D = []; NN = []
                for _ in range(N):
                    r = path(sigs, catch)
                    if r:
                        W.append(r[0]); D.append(r[1]); NN.append(r[2])
                W = np.array(W); D = np.array(D)
                print("  %-9.1f %5d  %4.1f [%4.1f..%4.1f]        %5.1f / %5.1f" % (
                    catch, int(np.mean(NN)), W.mean(), np.percentile(W, 5), np.percentile(W, 95),
                    np.median(D), np.percentile(D, 95)), flush=True)
            # bootstrap 95% CI on the per-trade win rate (resample the diligent one-at-a-time trades)
            seq = []; busy = -1
            for (k, ex, R) in sigs:
                if k > busy:
                    seq.append(1 if R > 0 else 0); busy = ex
            seq = np.array(seq)
            boot = [np.mean(seq[np.random.randint(0, len(seq), len(seq))]) for _ in range(5000)]
            print("  diligent win rate = %.0f%%  (bootstrap 95%% CI %.0f-%.0f%%, n=%d one-at-a-time trades)" % (
                100 * seq.mean(), 100 * np.percentile(boot, 2.5), 100 * np.percentile(boot, 97.5), len(seq)), flush=True)


if __name__ == "__main__":
    main()
