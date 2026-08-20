"""Two-target scale-out on RadarRun (30m-clock + 30m-bucket, notional 10%x10, maker 0.04%RT no-slip, candle stop):
50% off at TP1 = 0.2% NET (gross 0.24%), 50% off at TP2 = 0.4% NET (gross 0.44%). Full return = 0.5*trancheA + 0.5*trancheB.
Two stop-management variants: (A) stop stays at the candle stop for the runner; (B) stop -> BREAKEVEN after TP1 fills.
Reports outcome mix (full-stop / TP1-then-stop / both-TP), win%, expectancy, and the notional HyroTrader MC. IN-SAMPLE.
python study/radarrun_scaleout.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_proptp_alltf_clock import detect, SLBUF
FEE, H = 0.0004, 200                       # maker 0.04% RT per tranche; no slippage
TP1N, TP2N = 0.0020, 0.0040                # NET targets (fees already removed)
G1, G2 = 0.0024, 0.0044                    # gross price levels that net the above
TARGET, MAXDD, NPATH, MAXD = 10.0, 6.0, 20000, 400
SRCS = [("study/clock_archive", "30m"), ("study/recon_archive", "30m")]


def sim_scaleout(s, entry, sl, ph, pl, pc, be):
    """returns (full_net_return, outcome, exit_off) with stop-first same-bar. outcome in {stop, tp1_stop, tp1_end, both}."""
    tp1 = entry * (1 + s * G1); tp2 = entry * (1 + s * G2)
    sl_dist = abs(entry - sl) / entry
    hit1 = False; slp = sl; m = len(ph)
    for off in range(m):
        hi = ph[off]; lo = pl[off]
        if not hit1:
            if (lo <= sl) if s > 0 else (hi >= sl):                  # full position stopped before TP1
                return -sl_dist - FEE, "stop", off + 1
            if (hi >= tp1) if s > 0 else (lo <= tp1):                # TP1 -> bank tranche A (+0.2% net on 50%)
                hit1 = True
                if be:
                    slp = entry
                if (hi >= tp2) if s > 0 else (lo <= tp2):            # same bar also clears TP2
                    return 0.5 * TP1N + 0.5 * TP2N, "both", off + 1
        else:
            sld = abs(entry - slp) / entry
            if (lo <= slp) if s > 0 else (hi >= slp):                # tranche B stopped (candle stop or BE)
                return 0.5 * TP1N + 0.5 * (-sld - FEE), "tp1_stop", off + 1
            if (hi >= tp2) if s > 0 else (lo <= tp2):
                return 0.5 * TP1N + 0.5 * TP2N, "both", off + 1
    if hit1:                                                          # horizon end, tranche B exits at close
        netB = s * (pc[-1] - entry) / entry - FEE
        return 0.5 * TP1N + 0.5 * netB, "tp1_end", m
    return s * (pc[-1] - entry) / entry - FEE, "stop", m             # never hit TP1 nor stop -> flat exit (rare)


def load_dets():
    dets = []
    for root, tf in SRCS:
        A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
        n = len(A); Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
        C = np.array([_f(b.get("close", b.get("close_price"))) for b in A]); ST = np.array([_f(b.get("start_time")) for b in A])
        dets.append((detect(A, SLBUF.get(tf, 0.003))[0], Hi, Lo, C, ST, n))
    return dets


def eval_cfg(dets, be):
    tr = []; oc = {"stop": 0, "tp1_stop": 0, "tp1_end": 0, "both": 0}
    for (sigs, Hi, Lo, C, ST, n) in dets:
        last = -1
        for (k, s, entry, csl, dist, ts) in sigs:
            if k <= last:
                continue
            j0 = k + 1; j1 = min(n, k + 1 + H)
            net, o, off = sim_scaleout(s, entry, csl, Hi[j0:j1], Lo[j0:j1], C[j0:j1], be)
            oc[o] += 1
            tr.append((float(ST[k]), net)); last = k + int(off)      # non-overlap: advance past the runner's exit bar
    tr.sort(); return tr, oc


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


def report(title, tr, oc):
    nets = np.array([t[1] for t in tr]) * 100.0; N = len(tr)
    days = day_blocks(tr); spd = N / max(1, len(days))
    print("=" * 94, flush=True)
    print(title, flush=True)
    print("  outcomes: both-TP %.1f%%  |  TP1-then-stop %.1f%%  |  full-stop %.1f%%  |  TP1-then-flat %.1f%%"
          % (100 * oc["both"] / N, 100 * oc["tp1_stop"] / N, 100 * oc["stop"] / N, 100 * oc["tp1_end"] / N), flush=True)
    print("  net>0 %.1f%%  | avg trade %+.4f%%  worst-trade %+.3f%%  | %d trades  %.2f/day"
          % (100 * (nets > 0).mean(), nets.mean(), nets.min(), N, spd), flush=True)
    print("  daily |  pass%  | days p10/med/p90 | trades p10/med/p90 | DD p99 | worst-path", flush=True)
    for dl in (3.0, 4.0):
        m = mc(days, dl)
        print("   %.0f%%  |  %5.1f%% |   %3.0f / %3.0f / %3.0f  |   %3.0f / %3.0f / %3.0f  | %5.1f%% | %5.1f%%"
              % (dl, m["p"], m["d10"], m["d50"], m["d90"], m["t10"], m["t50"], m["t90"], m["dd99"], m["worst"]), flush=True)
    print("", flush=True)


def main():
    print("RadarRun SCALE-OUT | 30c+30bkt | 50%%@TP1(0.2%%net) + 50%%@TP2(0.4%%net) | candle stop | notional 10%%x10 | IN-SAMPLE\n", flush=True)
    dets = load_dets()
    trA, ocA = eval_cfg(dets, be=False)
    trB, ocB = eval_cfg(dets, be=True)
    report("(A) stop stays at CANDLE stop for the runner", trA, ocA)
    report("(B) stop -> BREAKEVEN after TP1 fills", trB, ocB)


if __name__ == "__main__":
    main()
