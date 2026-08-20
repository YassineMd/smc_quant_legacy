"""Drop the 15m-clock source (the loss factory) -> keep ONLY 30m-clock + 30m-bucket. net TP 0.2% (gross 0.27%), CANDLE
stop, NOTIONAL 10%x10 (f=1.0), standard breakout entry. Report win%, pass%, days-to-pass, trades-to-pass, DD. Also shows
the 0.5%-CAP variant for contrast (survivable-notional). HyroTrader $200k target10/max6-trail, 20k paths. IN-SAMPLE.
python study/radarrun_30only_notional.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_proptp_alltf_clock import detect, SLBUF
FEE, SLIP, TP, H = 0.0004, 0.0000, 0.0024, 200   # LIMIT/maker: 0.02%+0.02%=0.04% RT, NO slippage; gross 0.24% -> net 0.20%
TARGET, MAXDD, NPATH, MAXD = 10.0, 6.0, 20000, 400
SRCS = [("study/clock_archive", "30m"), ("study/recon_archive", "30m")]   # 30c + 30bkt only


def sim(s, entry, tp, sl, ph, pl, pc):
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if s > 0 else (hi >= sl):
            return "sl", s * (sl - entry) / entry, off + 1
        if (hi >= tp) if s > 0 else (lo <= tp):
            return "tp", s * (tp - entry) / entry, off + 1
    return "end", (s * (pc[-1] - entry) / entry if len(pc) else 0), len(ph)


def load_dets():
    dets = []
    for root, tf in SRCS:
        A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
        n = len(A); Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
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
            tr.append((float(ST[k]), net)); last = k + int(off)
    tr.sort(); return tr


def day_blocks(tr):
    by = {}
    for ts, net in tr:
        by.setdefault(datetime.fromtimestamp(ts, tz=timezone.utc).date(), []).append(net)
    if not by:
        return []
    d0, d1 = min(by), max(by); out = []; d = d0
    while d <= d1:
        out.append(by.get(d, [])); d += timedelta(days=1)
    return out


def mc(days, daily_lim):
    random.seed(7); passes = 0; dtp = []; ttp = []; mdds = []
    for _ in range(NPATH):
        eq = peak = 0.0; mdd = 0.0; passed = failed = False; ntr = 0
        for dnum in range(1, MAXD + 1):
            day = days[random.randrange(len(days))]; ipeak = eq
            for net in day:
                ntr += 1; eq += net * 100.0
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
    return dict(p=100.0 * passes / NPATH, d10=dd[0], d50=dd[1], d90=dd[2], t10=tq[0], t50=tq[1], t90=tq[2],
                dd99=np.percentile(mdds, 99), worst=max(mdds))


def report(title, tr):
    nets = np.array([t[1] for t in tr]) * 100.0
    w = nets[nets > 0]; l = nets[nets <= 0]
    days = day_blocks(tr); ndays = len(days); spd = len(tr) / max(1, ndays)
    print("=" * 92, flush=True)
    print(title, flush=True)
    print("  win %.1f%%  | avgWin %+.3f%%  avgLoss %+.3f%%  worst-trade %+.3f%%  | exp %+.4f%%  | %d trades  %.2f/day" %
          (100.0 * (nets > 0).mean(), w.mean(), l.mean(), nets.min(), nets.mean(), len(tr), spd), flush=True)
    print("  daily |  pass%  | days p10/med/p90 | trades p10/med/p90 | DD p99 | worst-path", flush=True)
    for dl in (3.0, 4.0):
        m = mc(days, dl)
        print("   %.0f%%  |  %5.1f%% |   %3.0f / %3.0f / %3.0f  |   %3.0f / %3.0f / %3.0f  | %5.1f%% | %5.1f%%" %
              (dl, m["p"], m["d10"], m["d50"], m["d90"], m["t10"], m["t50"], m["t90"], m["dd99"], m["worst"]), flush=True)
    print("", flush=True)


def main():
    print("RadarRun 30m-clock + 30m-bucket ONLY | net TP0.2%% (gross 0.24%%, maker 0.04%%RT no-slip) | NOTIONAL 10%%x10 | LIMIT-ENTRY assumes-fill | HyroTrader $200k | IN-SAMPLE\n", flush=True)
    dets = load_dets()
    report("CANDLE stop (~1%) + notional  [as asked]", eval_cfg(dets, None))
    report("0.5%-CAP stop + notional  [survivable-notional variant, for contrast]", eval_cfg(dets, 0.005))


if __name__ == "__main__":
    main()
