"""Should the Radar Runner SKIP trades whose candle-SL distance is wider than a threshold? 30c+30bkt, TP 0.25%, barrier.
Sweep max-SL {0.5/0.75/1.0/1.25/1.5%/none}: report n, %%kept, win%, exp%% (net), expR (net/SL-dist), avg SL, IS/OOS win,
and the HyroTrader MC under BOTH sizings — R0.4 (risk-capped: expR is what matters) and NOTIONAL 10x10 (exp%% + tail matter).
A wide SL = high win rate but big/rare loss; effect differs by sizing. IN-SAMPLE. python study/radarrun_sl_threshold.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_proptp_alltf_clock import detect, SLBUF
from study.radarrun_winrate_dd import sim
FEE, SLIP, TP, H = 0.0004, 0.0003, 0.0025, 200
TARGET, MAXDD, NPATH, MAXD = 10.0, 6.0, 20000, 400
SRCS = [("study/clock_archive", "30m"), ("study/recon_archive", "30m")]
THRESH = [0.005, 0.0075, 0.010, 0.0125, 0.015, None]


def build():
    tr = []
    for root, tf in SRCS:
        A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
        n = len(A); Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
        C = np.array([_f(b.get("close", b.get("close_price"))) for b in A]); ST = np.array([_f(b.get("start_time")) for b in A])
        last = -1
        for (k, s, entry, sl, dist, ts) in detect(A, SLBUF.get(tf, 0.003))[0]:
            if k <= last:
                continue
            j0 = k + 1; j1 = min(n, k + 1 + H)
            if j0 >= n:
                continue
            outc, gross, off = sim(s, entry, entry * (1 + s * TP), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
            net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0); last = k + int(off)
            yr = datetime.fromtimestamp(float(ST[k]), tz=timezone.utc).year
            tr.append((float(ST[k]), net, dist, yr))
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
    return out


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
    print("RR max-SL filter | 30c+30bkt TP0.25%% | HyroTrader $200k daily4%% | IN-SAMPLE\n", flush=True)
    trall = build(); ntot = len(trall)
    print("  maxSL   n     %kept win%   exp%      expR   avgSL  | R0.4 pass/med/DD99 | NOTIONAL pass/med/DD99/worst", flush=True)
    for th in THRESH:
        tr = [t for t in trall if (th is None or t[2] <= th)]
        nets = np.array([t[1] for t in tr]) * 100.0; dists = np.array([t[2] for t in tr]) * 100.0
        rs = np.array([t[1] / t[2] for t in tr])
        win = 100.0 * (nets > 0).mean()
        mR = mc(day_blocks(tr, "R")); mN = mc(day_blocks(tr, "N"))
        print("  %-6s  %-5d %4.0f%% %5.1f%% %+.4f%% %+.3f  %.3f%% | %5.1f%%/%3.0f/%4.1f%% | %5.1f%%/%3.0f/%4.1f%%/%4.1f%%"
              % (("%.2f%%" % (th * 100)) if th else "none", len(tr), 100.0 * len(tr) / ntot, win, nets.mean(),
                 rs.mean(), dists.mean(), mR["p"], mR["med"], mR["dd99"], mN["p"], mN["med"], mN["dd99"], mN["worst"]), flush=True)
    # IS/OOS win + exp by threshold (robustness)
    print("\n  IS/OOS check (win%% | exp%%):", flush=True)
    for th in THRESH:
        tr = [t for t in trall if (th is None or t[2] <= th)]
        def s(yr):
            a = np.array([t[1] for t in tr if t[3] == yr]) * 100.0
            return ("%4.1f%%/%+.4f%%" % (100 * (a > 0).mean(), a.mean())) if len(a) else "n/a"
        print("    maxSL %-6s  IS %s | OOS %s" % (("%.2f%%" % (th * 100)) if th else "none", s(2025), s(2026)), flush=True)


if __name__ == "__main__":
    main()
