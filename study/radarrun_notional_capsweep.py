"""How tight can the RadarRun stop go under NOTIONAL 10%x10 (f=1.0) sizing before the win-rate damage outweighs the
smaller-loss benefit? The 1m-wall stop (0.22%) was too tight (win 4.4%); a floor makes it ~a flat cap (wall always tighter
than any sane floor). So sweep a FLAT stop-distance cap {candle / 0.8 / 0.7 / 0.6 / 0.5 / 0.4%} = min(candle_dist, cap),
TP 0.25%, resolved on the 1m-BUCKET PATH (honest for a tight stop). Report win%, avgLoss, expectancy, and NOTIONAL f=1.0
HyroTrader pass / DDp99 / worst-path at daily 3%% and 4%%. Pooled 15c+30c+30bkt. (Risk-capped R0.4 already passes 100%%;
this is the notional question.) python study/radarrun_notional_capsweep.py"""
import os, sys, bisect, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_proptp_alltf_clock import detect, SLBUF

FEE, SLIP, TP = 0.0004, 0.0003, 0.0025
MAXBARS_1M = 6000
TARGET, MAXDD = 10.0, 6.0
NPATH, MAXD = 20000, 400
SRCS = [("study/clock_archive", "15m"), ("study/clock_archive", "30m"), ("study/recon_archive", "30m")]
CAPS = [None, 0.008, 0.007, 0.006, 0.005, 0.004]


def load_1m_arrays():
    A = sorted(load_archive("1m", root="study/recon_archive", drop_degenerate=False)[1],
               key=lambda b: _f(b.get("start_time", 0)))
    ST = np.array([_f(b.get("start_time")) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    return ST, Hi, Lo, C


def resolve_1m(E, entry, tp, sl, s, ST1, H1, L1, C1):
    j0 = bisect.bisect_left(ST1, E); n = len(ST1)
    for j in range(j0, min(n, j0 + MAXBARS_1M)):
        hi = H1[j]; lo = L1[j]
        if (lo <= sl) if s > 0 else (hi >= sl):
            return "sl", s * (sl - entry) / entry, float(ST1[j])
        if (hi >= tp) if s > 0 else (lo <= tp):
            return "tp", s * (tp - entry) / entry, float(ST1[j])
    j = min(n - 1, j0 + MAXBARS_1M)
    return "end", s * (C1[j] - entry) / entry, float(ST1[j])


def build_signals(tmax):
    sigs = []
    for root, tf in SRCS:
        A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
        for (k, s, entry, csl, dist, ts) in detect(A, SLBUF.get(tf, 0.003))[0]:
            if float(ts) <= tmax:
                sigs.append((float(ts), s, float(entry), float(csl)))
    sigs.sort(); return sigs


def eval_cap(sigs, cap, ST1, H1, L1, C1):
    tr = []; last_t = -1.0
    for (ts, s, entry, csl) in sigs:
        if ts <= last_t:
            continue
        cdist = abs(entry - csl) / entry
        eff = cdist if cap is None else min(cdist, cap)
        sl = entry * (1 - s * eff)
        if eff <= 0:
            continue
        outc, gross, xt = resolve_1m(ts, entry, entry * (1 + s * TP), sl, s, ST1, H1, L1, C1)
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        tr.append((ts, net, eff)); last_t = xt
    return tr


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
    print("RadarRun NOTIONAL 10%%x10 (f1.0) stop-CAP sweep | TP0.25%% | 1m-path resolved | pooled 15c+30c+30bkt\n", flush=True)
    ST1, H1, L1, C1 = load_1m_arrays()
    sigs = build_signals(float(ST1[-1]))
    print("  1m bars=%d  pooled signals=%d (within 1m span, ..%s)\n"
          % (len(ST1), len(sigs), datetime.fromtimestamp(ST1[-1], tz=timezone.utc).date()), flush=True)
    print("  stop-cap   n     win%   avgLoss   exp/tr%   avgSL%   | notional pass/DDp99/worst @daily3  @daily4", flush=True)
    for cap in CAPS:
        tr = eval_cap(sigs, cap, ST1, H1, L1, C1)
        nets = np.array([t[1] for t in tr]) * 100.0; dists = np.array([t[2] for t in tr]) * 100.0
        l = nets[nets <= 0]
        days = day_blocks(tr)
        m3 = mc_notional(days, 3.0); m4 = mc_notional(days, 4.0)
        print("  %-8s  %-5d %5.1f%%  %+.3f%%  %+.4f%%  %.3f%%  | %5.1f%%/%.1f%%/%.1f%%   %5.1f%%/%.1f%%/%.1f%%"
              % (("candle" if cap is None else "%.1f%%" % (cap * 100)), len(tr), 100.0 * (nets > 0).mean(),
                 l.mean() if len(l) else 0.0, nets.mean(), dists.mean(),
                 m3["p"], m3["dd99"], m3["worst"], m4["p"], m4["dd99"], m4["worst"]), flush=True)


if __name__ == "__main__":
    main()
