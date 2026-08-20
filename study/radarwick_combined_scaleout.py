"""RR (normal) + DIAMOND (SD+big-wick) COMBINED, scale-out exit (50%@TP1 0.2net + 50%@TP2 0.4net, BE after TP1), 30c+30bkt,
maker. RR and diamond are DISJOINT (different bars) -> pooled as concurrent sources. Table: RR-only / Diamond-only /
COMBINED -- n, trд/day, both-TP%, net>0%, avg trade, and HyroTrader MC (R0.4 + NOTIONAL). IN-SAMPLE. python study/radarwick_combined_scaleout.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_proptp_alltf_clock import detect, SLBUF
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
                return 0.5 * TN1 - 0.5 * FEE, "tp1_be", off + 1
            if (hi >= tp2) if s > 0 else (lo <= tp2):
                return 0.5 * TN1 + 0.5 * TN2, "both", off + 1
    if hit1:
        return 0.5 * TN1 + 0.5 * (s * (pc[-1] - entry) / entry - FEE), "tp1_end", m
    return s * (pc[-1] - entry) / entry - FEE, "stop", m


def trades_from(sigs, Hi, Lo, C, n):
    tr = []; oc = {"stop": 0, "tp1_be": 0, "tp1_end": 0, "both": 0}; last = -1
    for (k, s, entry, sl, dist, ts) in sigs:
        if k <= last:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H)
        if j0 >= n:
            continue
        net, o, off = sim_scaleout(s, entry, sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1]); oc[o] += 1
        tr.append((float(ts), net, dist)); last = k + int(off)
    return tr, oc


def collect():
    rr = []; dia = []; oc_r = {"stop": 0, "tp1_be": 0, "tp1_end": 0, "both": 0}; oc_d = dict(oc_r)
    for root, tf in SRCS:
        dsigs, Hi, Lo, C, n = detect_wick_sdbig(root, tf)
        A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
        rsigs = detect(A, SLBUF.get(tf, 0.003))[0]
        tr, o = trades_from(rsigs, Hi, Lo, C, n); rr += tr
        for kk in o:
            oc_r[kk] += o[kk]
        tr, o = trades_from(dsigs, Hi, Lo, C, n); dia += tr
        for kk in o:
            oc_d[kk] += o[kk]
    return rr, dia, oc_r, oc_d


def day_blocks(tr, mode):
    by = {}
    for ts, net, dist in tr:
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


def row(name, tr, oc):
    tr = sorted(tr); nets = np.array([t[1] for t in tr]) * 100.0; N = len(tr)
    dbR, nd = day_blocks(tr, "R"); dbN, _ = day_blocks(tr, "N")
    mR = mc(dbR); mN = mc(dbN)
    print("  %-13s %-5d %.2f    %4.1f%%  %4.1f%%  %+.4f%% | %5.1f%%/%3.0f/%4.1f%% | %5.1f%%/%3.0f/%4.1f%%/%4.1f%%"
          % (name, N, N / max(1, nd), 100 * oc["both"] / N, 100 * (nets > 0).mean(), nets.mean(),
             mR["p"], mR["med"], mR["dd99"], mN["p"], mN["med"], mN["dd99"], mN["worst"]), flush=True)


def main():
    print("RR + DIAMOND COMBINED | scale-out (TP1 0.2 / TP2 0.4 net, BE) | 30c+30bkt | maker | HyroTrader $200k | IN-SAMPLE\n", flush=True)
    rr, dia, ocr, ocd = collect()
    print("  config        n     trд/day both%%  net>0%% avg trade | R0.4 pass/med/DD99 | NOTIONAL pass/med/DD99/worst", flush=True)
    row("RR only", rr, ocr)
    row("Diamond only", dia, ocd)
    comb_oc = {kk: ocr[kk] + ocd[kk] for kk in ocr}
    row("COMBINED", rr + dia, comb_oc)


if __name__ == "__main__":
    main()
