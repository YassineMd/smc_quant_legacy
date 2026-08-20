"""DIAMOND (SD+big-wick) STANDALONE on 30m-clock + 30m-bucket only, maker fees 0.04%%RT (limit). Sweep net TP {0.2/0.3/0.4%%}
(gross = net + 0.04%%), candle-capped SL. Report n, trades/day, win%%, exp%%, IS/OOS, and HyroTrader $200k MC under BOTH
sizings (R0.4 risk-capped + NOTIONAL 10x10). The diamond is low-frequency -> watch days-to-pass. IN-SAMPLE.
python study/radarwick_diamond_30only.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_winrate_dd import sim
from study.radarwick_prop_combined import detect_wick_sdbig
FEE, H = 0.0004, 200                      # maker 0.04% RT, no slip
TARGET, MAXDD, NPATH, MAXD = 10.0, 6.0, 20000, 400
SRCS = [("study/clock_archive", "30m"), ("study/recon_archive", "30m")]
NETTPS = [0.002, 0.003, 0.004]


def build(dets, gtp):
    tr = []
    for det in dets:
        sigs, Hi, Lo, C, n = det; last = -1
        for (k, s, entry, sl, dist, ts) in sigs:
            if k <= last:
                continue
            j0 = k + 1; j1 = min(n, k + 1 + H)
            if j0 >= n:
                continue
            outc, gross, off = sim(s, entry, entry * (1 + s * gtp), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
            net = gross - FEE; last = k + int(off)
            yr = datetime.fromtimestamp(float(ts), tz=timezone.utc).year
            tr.append((float(ts), net, dist, yr))
    tr.sort(); return tr


def day_blocks(tr, mode):
    by = {}
    for ts, net, dist, yr in tr:
        v = (0.4 * (net / dist)) if mode == "R" else (net * 100.0)
        by.setdefault(datetime.fromtimestamp(ts, tz=timezone.utc).date(), []).append(v)
    if not by:
        return []
    d0, d1 = min(by), max(by); out = []; d = d0
    while d <= d1:
        out.append(by.get(d, [])); d += timedelta(days=1)
    return out, (d1 - d0).days + 1


def mc(dayb):
    days = dayb[0]
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
    print("DIAMOND (SD+big-wick) standalone | 30m-clock + 30m-bucket | maker 0.04%%RT | HyroTrader $200k daily4%% | IN-SAMPLE\n", flush=True)
    dets = [detect_wick_sdbig(root, tf) for root, tf in SRCS]
    nev = sum(len(d[0]) for d in dets)
    print("  diamond events: 30c=%d  30bkt=%d  (pooled %d)\n" % (len(dets[0][0]), len(dets[1][0]), nev), flush=True)
    print("  netTP  n     trд/day win%%   exp%%      IS win/exp        OOS win/exp       | R0.4 pass/med/DD99 | NOTIONAL pass/med/DD99/worst", flush=True)
    for ntp in NETTPS:
        tr = build(dets, ntp + FEE)
        nets = np.array([t[1] for t in tr]) * 100.0
        dbR = day_blocks(tr, "R"); dbN = day_blocks(tr, "N"); ndays = dbR[1]
        mR = mc(dbR); mN = mc(dbN)
        def yst(yr):
            a = np.array([t[1] for t in tr if t[3] == yr]) * 100.0
            return ("%4.1f%%/%+.3f%%" % (100 * (a > 0).mean(), a.mean())) if len(a) else "n/a"
        print("  %.1f%%   %-5d %.2f    %5.1f%% %+.4f%%  %-16s  %-16s | %5.1f%%/%3.0f/%4.1f%% | %5.1f%%/%3.0f/%4.1f%%/%4.1f%%"
              % (ntp * 100, len(tr), len(tr) / max(1, ndays), 100 * (nets > 0).mean(), nets.mean(),
                 yst(2025), yst(2026), mR["p"], mR["med"], mR["dd99"], mN["p"], mN["med"], mN["dd99"], mN["worst"]), flush=True)


if __name__ == "__main__":
    main()
