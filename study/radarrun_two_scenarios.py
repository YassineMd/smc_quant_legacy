"""Two scenarios side by side (RR-only, pooled 15c+30c+30bkt, TP 0.25%, higher-TF barrier, HyroTrader $200k
target10/max6-trail/daily-trail, 20k-path day-block MC). Reports win%, DD, days-to-pass, trades-to-pass, pass%.
  OPTIMAL  = candle stop (~1%), RISK-CAPPED R0.4 ($800/trade flex sizing)  [the locked plan]
  CAPPED   = SL = min(candle, 0.5%), NOTIONAL 10%x10 (f=1.0)                [the notional alternative]
Both reported at daily 3%% and 4%%. ALL IN-SAMPLE. python study/radarrun_two_scenarios.py"""
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
        dets.append((detect(A, SLBUF.get(tf, 0.003))[0], Hi, Lo, C, ST, n))
    return dets


def eval_cfg(dets, cap):
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
            tr.append((float(ST[k]), net, net / eff)); last = k + int(off)
    tr.sort(); return tr


def day_blocks(tr):
    by = {}
    for ts, net, r in tr:
        by.setdefault(datetime.fromtimestamp(ts, tz=timezone.utc).date(), []).append((net, r))
    if not by:
        return []
    d0, d1 = min(by), max(by); out = []; d = d0
    while d <= d1:
        out.append(by.get(d, [])); d += timedelta(days=1)
    return out


def mc(days, mode, Rp, daily_lim):
    random.seed(7); passes = 0; dtp = []; ttp = []; mdds = []
    for _ in range(NPATH):
        eq = peak = 0.0; mdd = 0.0; passed = failed = False; ntr = 0
        for dnum in range(1, MAXD + 1):
            day = days[random.randrange(len(days))]; ipeak = eq
            for (net, r) in day:
                ntr += 1
                eq += (Rp * r) if mode == "R" else (net * 100.0)
                peak = max(peak, eq); ipeak = max(ipeak, eq); mdd = max(mdd, peak - eq)
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
            passes += 1; dtp.append(dnum); ttp.append(ntr)
    dd = np.percentile(dtp, [10, 50, 90]) if dtp else [0, 0, 0]
    tq = np.percentile(ttp, [10, 50, 90]) if ttp else [0, 0, 0]
    return dict(p=100.0 * passes / NPATH, d50=dd[1], d10=dd[0], d90=dd[2],
                t50=tq[1], t10=tq[0], t90=tq[2], dd99=np.percentile(mdds, 99), worst=max(mdds))


def tstats(tr):
    nets = np.array([t[1] for t in tr]) * 100.0
    w = nets[nets > 0]; l = nets[nets <= 0]
    return len(nets), 100.0 * (nets > 0).mean(), (w.mean() if len(w) else 0), (l.mean() if len(l) else 0), nets.mean()


def table(title, tr, mode, Rp):
    n, win, aw, al, exp = tstats(tr)
    days = day_blocks(tr)
    print("=" * 92, flush=True)
    print("%s" % title, flush=True)
    print("  win rate %.1f%%  |  avg win %+.3f%%  avg loss %+.3f%%  |  expectancy %+.4f%%/trade  |  %d signals" % (win, aw, al, exp, n), flush=True)
    print("  daily |  pass%  | days-to-pass p10/med/p90 | trades-to-pass p10/med/p90 | DD p99 | worst-path", flush=True)
    print("  " + "-" * 88, flush=True)
    for dl in (3.0, 4.0):
        m = mc(days, mode, Rp, dl)
        print("   %.0f%%  |  %5.1f%% |     %3.0f / %3.0f / %3.0f      |      %3.0f / %3.0f / %3.0f       | %5.1f%% |  %5.1f%%"
              % (dl, m["p"], m["d10"], m["d50"], m["d90"], m["t10"], m["t50"], m["t90"], m["dd99"], m["worst"]), flush=True)
    print("", flush=True)


def main():
    print("RadarRun — OPTIMAL vs CAPPED-0.5%% | RR-only 15c+30c+30bkt | TP 0.25%% | HyroTrader $200k | 20k paths | IN-SAMPLE\n", flush=True)
    dets = load_dets()
    tr_opt = eval_cfg(dets, None)          # candle stop
    tr_cap = eval_cfg(dets, 0.005)         # SL cap 0.5%
    table("OPTIMAL  — candle stop (~1%), RISK-CAPPED R0.4 ($800/trade)  [locked plan]", tr_opt, "R", 0.4)
    table("CAPPED   — SL min(candle, 0.5%), NOTIONAL 10%x10 (f=1.0)     [notional alt]", tr_cap, "N", None)


if __name__ == "__main__":
    main()
