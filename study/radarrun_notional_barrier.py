"""CLEAN re-run of the notional stop-cap question — resolve on each source's OWN higher-TF candles (the bar's low/high is
the authoritative 'was the stop touched' test; avoids the 1m-bucket basis mismatch that corrupted radarrun_notional_capsweep
at tight stops). Stop-first same-bar (pessimistic). Flat stop-distance cap {candle/0.8/0.7/0.6/0.5/0.4%} = min(candle,cap),
TP 0.25%, NOTIONAL f=1.0 (10%x10) sizing. HyroTrader MC target10/max6-trail/daily 3&4-trail. Pooled 15c+30c+30bkt,
time-based non-overlap. python study/radarrun_notional_barrier.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_proptp_alltf_clock import detect, SLBUF

FEE, SLIP, TP, H = 0.0004, 0.0003, 0.0025, 200
TARGET, MAXDD, NPATH, MAXD = 10.0, 6.0, 20000, 400
SRCS = [("study/clock_archive", "15m"), ("study/clock_archive", "30m"), ("study/recon_archive", "30m")]
CAPS = [None, 0.008, 0.007, 0.006, 0.005, 0.004]


def sim(s, entry, tp, sl, ph, pl, pc):
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if s > 0 else (hi >= sl):
            return "sl", s * (sl - entry) / entry, off + 1
        if (hi >= tp) if s > 0 else (lo <= tp):
            return "tp", s * (tp - entry) / entry, off + 1
    return "end", (s * (pc[-1] - entry) / entry if len(pc) else 0.0), len(ph)


def load_dets():
    dets = []
    for root, tf in SRCS:
        A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
        n = len(A)
        Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
        C = np.array([_f(b.get("close", b.get("close_price"))) for b in A]); ST = np.array([_f(b.get("start_time")) for b in A])
        sigs = detect(A, SLBUF.get(tf, 0.003))[0]
        dets.append((sigs, Hi, Lo, C, ST, n))
    return dets


def eval_cap(dets, cap):
    tr = []
    for (sigs, Hi, Lo, C, ST, n) in dets:
        last = -1
        for (k, s, entry, csl, dist, ts) in sigs:
            if k <= last:
                continue
            cdist = abs(entry - csl) / entry
            eff = cdist if cap is None else min(cdist, cap)
            if eff <= 0:
                continue
            sl = entry * (1 - s * eff)
            j0 = k + 1; j1 = min(n, k + 1 + H)
            outc, gross, off = sim(s, entry, entry * (1 + s * TP), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
            net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
            tr.append((float(ST[k]), net, eff)); last = k + int(off)
    tr.sort(); return tr


def day_blocks(tr):
    by = {}
    for ts, net, _d in tr:
        by.setdefault(datetime.fromtimestamp(ts, tz=timezone.utc).date(), []).append(net)
    if not by:
        return []
    d0, d1 = min(by), max(by); out = []; d = d0
    while d <= d1:
        out.append(by.get(d, [])); d += timedelta(days=1)
    return out


def mc_notional(days, daily_lim):
    random.seed(7); passes = 0; dtp = []; mdds = []
    for _ in range(NPATH):
        eq = peak = 0.0; mdd = 0.0; passed = failed = False
        for _dn in range(1, MAXD + 1):
            day = days[random.randrange(len(days))]; ipeak = eq
            for net in day:
                eq += net * 100.0; peak = max(peak, eq); ipeak = max(ipeak, eq); mdd = max(mdd, peak - eq)
                if peak - eq >= MAXDD:
                    failed = True; break
                if ipeak - eq >= daily_lim:
                    failed = True; break
                if eq >= TARGET:
                    passed = True; break
            if passed or failed:
                break
        mdds.append(mdd)
        if passed:
            passes += 1; dtp.append(_dn)
    return dict(p=100.0 * passes / NPATH, med=(np.percentile(dtp, 50) if dtp else 0),
                dd99=np.percentile(mdds, 99), worst=max(mdds))


def main():
    print("RadarRun NOTIONAL 10%%x10 stop-cap sweep | HIGHER-TF barrier (authoritative) | TP0.25%% | pooled 15c+30c+30bkt\n", flush=True)
    dets = load_dets()
    print("  stop-cap   n     win%   avgLoss   exp/tr%   avgSL%  | notional pass/DDp99/worst @daily3   @daily4", flush=True)
    for cap in CAPS:
        tr = eval_cap(dets, cap)
        nets = np.array([t[1] for t in tr]) * 100.0; dists = np.array([t[2] for t in tr]) * 100.0
        l = nets[nets <= 0]; days = day_blocks(tr)
        m3 = mc_notional(days, 3.0); m4 = mc_notional(days, 4.0)
        print("  %-8s  %-5d %5.1f%%  %+.3f%%  %+.4f%%  %.3f%% | %5.1f%%/%.1f%%/%.1f%%   %5.1f%%/%.1f%%/%.1f%%"
              % (("candle" if cap is None else "%.1f%%" % (cap * 100)), len(tr), 100.0 * (nets > 0).mean(),
                 l.mean() if len(l) else 0.0, nets.mean(), dists.mean(),
                 m3["p"], m3["dd99"], m3["worst"], m4["p"], m4["dd99"], m4["worst"]), flush=True)


if __name__ == "__main__":
    main()
