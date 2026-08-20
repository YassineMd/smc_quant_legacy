"""Is the ONE live thread from the strength split — BIG rejection wick (wick>=0.5) — robust across TPs, or a 0.3%-only
mirage? Compare ALL wick-breakouts vs the big-wick subset across TP {0.2/0.3/0.4/0.5}%, OOS-split, decent-n cells only.
Same shipped bracket (candle-SL), taken(), fees. python study/radarwick_bigwick.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_winrate_dd import sim
from app import radar_breakout_detect as RB
from study.radarwick_strength import load, build

H = 200; FEE = 0.0004; SLIP = 0.0003
TPS = [0.002, 0.003, 0.004, 0.005]
SLBUF = {"15m": 0.003, "30m": 0.003, "1h": 0.002}
CELLS = [("bucket", "study/recon_archive", "1h"), ("bucket", "study/recon_archive", "30m"),
         ("bucket", "study/recon_archive", "15m"), ("clock", "study/clock_archive", "30m"),
         ("clock", "study/clock_archive", "15m")]
WICK_BIG = 0.5


def ev_eval(events, C, Hi, Lo, YR, n, buf, tp):
    by = {}; last = -1
    for (k, s, rlo, rhi, pen, wick) in events:
        if k <= last or k + 1 >= n:
            continue
        entry = C[k]; fsl = max(Lo[k] * (1 - buf), rlo) if s > 0 else min(Hi[k] * (1 + buf), rhi)
        dist = abs(entry - fsl) / entry
        if dist <= 0:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(s, entry, entry * (1 + s * tp), fsl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        by.setdefault(int(YR[k]), []).append((net, dist)); last = k + int(off)
    res = {}
    for y, arr in by.items():
        nets = np.array([a[0] for a in arr]); dists = np.array([a[1] for a in arr])
        res[y] = (len(nets), 100.0 * (nets > 0).mean(), nets.mean() * 100.0, (nets / dists).mean())
    return res


def c(res, y):
    if y not in res:
        return "n=0"
    nn, w, net, e = res[y]; return "n=%-4d win%4.1f%% net%+.3f%% expR%+.2f" % (nn, w, net, e)


def main():
    print("BIG-WICK (wick>=%.1f) vs ALL wick-breakouts, across TPs | OOS-split | expR is the fair metric\n" % WICK_BIG, flush=True)
    for dsname, root, tf in CELLS:
        A, C, Hi, Lo, YR, n = load(tf, root); buf = SLBUF[tf]
        ev = build(A); big = [e for e in ev if e[5] >= WICK_BIG]
        print("================ %s %s  (ALL=%d  BIG-wick=%d) ================" % (dsname.upper(), tf, len(ev), len(big)), flush=True)
        for tp in TPS:
            ar = ev_eval(ev, C, Hi, Lo, YR, n, buf, tp); br = ev_eval(big, C, Hi, Lo, YR, n, buf, tp)
            print("  TP %.2f%%  ALL  IS %-33s OOS %s" % (tp * 100, c(ar, 2025), c(ar, 2026)), flush=True)
            print("           BIG  IS %-33s OOS %s" % (c(br, 2025), c(br, 2026)), flush=True)
        print("", flush=True)


if __name__ == "__main__":
    main()
