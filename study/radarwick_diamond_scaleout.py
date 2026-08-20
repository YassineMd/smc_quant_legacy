"""DIAMOND (SD+big-wick) standalone on 30m-clock + 30m-bucket with the SCALE-OUT exit: 50% at TP1 (0.2% net / 0.24% gross)
+ 50% at TP2 (0.4% net / 0.44% gross), stop -> BE after TP1. Candle-capped SL, maker 0.04%RT. Reports outcome mix, win%,
avg trade, IS/OOS, and HyroTrader $200k MC under BOTH sizings (R0.4 + NOTIONAL 10x10). IN-SAMPLE. python study/radarwick_diamond_scaleout.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.radarwick_prop_combined import detect_wick_sdbig
FEE, H = 0.0004, 200
G1, G2 = 0.0024, 0.0044; TN1 = G1 - FEE; TN2 = G2 - FEE
TARGET, MAXDD, NPATH, MAXD = 10.0, 6.0, 20000, 400
SRCS = [("study/clock_archive", "30m"), ("study/recon_archive", "30m")]


def sim_scaleout(s, entry, sl, ph, pl, pc):
    tp1 = entry * (1 + s * G1); tp2 = entry * (1 + s * G2); sl_dist = abs(entry - sl) / entry
    hit1 = False; slp = sl; m = len(ph)
    for off in range(m):
        hi = ph[off]; lo = pl[off]
        if not hit1:
            if (lo <= sl) if s > 0 else (hi >= sl):
                return -sl_dist - FEE, "stop", off + 1
            if (hi >= tp1) if s > 0 else (lo <= tp1):
                hit1 = True; slp = entry
                if (hi >= tp2) if s > 0 else (lo <= tp2):
                    return 0.5 * TN1 + 0.5 * TN2, "both", off + 1
        else:
            if (lo <= slp) if s > 0 else (hi >= slp):
                return 0.5 * TN1 + 0.5 * (-FEE), "tp1_be", off + 1
            if (hi >= tp2) if s > 0 else (lo <= tp2):
                return 0.5 * TN1 + 0.5 * TN2, "both", off + 1
    if hit1:
        return 0.5 * TN1 + 0.5 * (s * (pc[-1] - entry) / entry - FEE), "tp1_end", m
    return s * (pc[-1] - entry) / entry - FEE, "stop", m


def build(dets):
    tr = []; oc = {"stop": 0, "tp1_be": 0, "tp1_end": 0, "both": 0}
    for (sigs, Hi, Lo, C, n) in dets:
        last = -1
        for (k, s, entry, sl, dist, ts) in sigs:
            if k <= last:
                continue
            j0 = k + 1; j1 = min(n, k + 1 + H)
            if j0 >= n:
                continue
            net, o, off = sim_scaleout(s, entry, sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1]); oc[o] += 1
            yr = datetime.fromtimestamp(float(ts), tz=timezone.utc).year
            tr.append((float(ts), net, dist, yr)); last = k + int(off)
    tr.sort(); return tr, oc


def day_blocks(tr, mode):
    by = {}
    for ts, net, dist, yr in tr:
        v = (0.4 * (net / dist)) if mode == "R" else (net * 100.0)
        by.setdefault(datetime.fromtimestamp(ts, tz=timezone.utc).date(), []).append(v)
    if not by:
        return [], 0
    d0, d1 = min(by), max(by); out = []; d = d0
    while d <= d1:
        out.append(by.get(d, [])); d += timedelta(days=1)
    return out, (d1 - d0).days + 1


def mc(days):
    random.seed(7); passes = 0; dtp = []; mdds = []
    for _ in range(NPATH):
        eq = peak = 0.0; mdd = 0.0; passed = failed = False
        for dnum in range(1, MAXD + 1):
            day = days[random.randrange(len(days))]; ipeak = eq
            for v in day:
                eq += v; peak = max(peak, eq); ipeak = max(ipeak, eq); mdd = max(mdd, peak - eq)
                if peak - eq >= MAXDD:
                    failed = True; break
                if ipeak - eq >= 4.0:
                    failed = True; break
                if eq >= TARGET:
                    passed = True; break
            if passed or failed:
                break
        mdds.append(mdd)
        if passed:
            passes += 1; dtp.append(dnum)
    return dict(p=100.0 * passes / NPATH, med=(np.percentile(dtp, 50) if dtp else 0),
                dd99=np.percentile(mdds, 99), worst=max(mdds))


def main():
    print("DIAMOND (SD+big-wick) + SCALE-OUT | 30c+30bkt | 50%@TP1(0.2net)+50%@TP2(0.4net) BE | maker | HyroTrader $200k | IN-SAMPLE\n", flush=True)
    dets = [detect_wick_sdbig(root, tf) for root, tf in SRCS]
    tr, oc = build(dets); N = len(tr)
    nets = np.array([t[1] for t in tr]) * 100.0
    dbR, ndays = day_blocks(tr, "R"); dbN, _ = day_blocks(tr, "N")
    mR = mc(dbR); mN = mc(dbN)
    print("  n=%d  %.2f trд/day  | both-TP %.1f%% | TP1->BE %.1f%% | full-stop %.1f%% | net>0 %.1f%% | avg trade %+.4f%% | worst %+.3f%%"
          % (N, N / max(1, ndays), 100 * oc["both"] / N, 100 * oc["tp1_be"] / N, 100 * oc["stop"] / N,
             100 * (nets > 0).mean(), nets.mean(), nets.min()), flush=True)
    for yr in (2025, 2026):
        a = np.array([t[1] for t in tr if t[3] == yr]) * 100.0
        print("    %d: n=%-4d net>0 %.1f%%  exp %+.4f%%" % (yr, len(a), 100 * (a > 0).mean(), a.mean()), flush=True)
    print("  R0.4       pass %.1f%%  med %.0fd  DDp99 %.1f%%  worst-path %.1f%%" % (mR["p"], mR["med"], mR["dd99"], mR["worst"]), flush=True)
    print("  NOTIONAL   pass %.1f%%  med %.0fd  DDp99 %.1f%%  worst-path %.1f%%" % (mN["p"], mN["med"], mN["dd99"], mN["worst"]), flush=True)


if __name__ == "__main__":
    main()
