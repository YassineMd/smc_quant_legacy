"""DECISIVE test: does adding SD+big-wick 30m-clock to the Radar Runner raise throughput WITHOUT hurting the HyroTrader
prop pass? Reuses the exact Hyro MC (target 10%, max 6% trailing, daily 3/4% trailing, day-block resample, 20k paths) and
the SAME candle-SL bracket. The wick source is added as another concurrent pool member (identical to how the live 3-source
setup already pools 15m/30m-clock + 30m-bucket). Reports, per config: trades/day, win%, prop PASS%, median days-to-pass,
trailing-DD med/p90/p99 — at the recommended FIXED-R sizing (loss capped at Rp% of the account).

Configs:
  A  RR 30m-clock (isolation baseline)
  B  RR 30m-clock  +  SD+big-wick 30m-clock            <- the pure incremental effect
  C  LIVE = RR[15m-clock + 30m-clock + 30m-bucket]     (the current locked setup)
  D  LIVE  +  SD+big-wick 30m-clock                     <- real-portfolio impact
python study/radarwick_prop_combined.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_proptp_alltf_clock import detect, eval_tp, SLBUF
from study.radarrun_hyro_prop import day_blocks, mc
from app import radar_breakout_detect as RB

WICK_BIG = 0.5; RM = 3.0


def load_arrays(root, tf):
    A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    return A


def detect_rr(root, tf):
    return detect(load_arrays(root, tf), SLBUF.get(tf, 0.003))


def detect_wick_sdbig(root, tf):
    """SD+big-wick events in the SAME (sigs, Hi, Lo, C, n) shape as detect(), same candle-SL bracket. same_dir + wick>=0.5."""
    A = load_arrays(root, tf); n = len(A); buf = SLBUF.get(tf, 0.003)
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    ST = np.array([_f(b.get("start_time")) for b in A])
    ev = {}; c0 = 0; step = 6000
    while c0 < n:
        c1 = min(n, c0 + step); S = A[c0:c1]
        try:
            dets = RB.detect_wick(S, skip_last=False, same_dir=True)
        except Exception:
            dets = []
        for e in dets:
            if float(e["wick"]) < WICK_BIG:
                continue
            k = int(e["i"]) + c0; s = int(e["side"]); side = "S" if s > 0 else "R"
            if (k, side) not in ev:
                ev[(k, side)] = (s, float(e["radar_lo"]), float(e["radar_hi"]))
        if c1 >= n:
            break
        c0 += step - 1000
    sigs = []
    for (k, side) in sorted(ev):
        if k + 1 >= n:
            continue
        s, rlo, rhi = ev[(k, side)]; entry = C[k]
        sl = max(Lo[k] * (1 - buf), rlo) if s > 0 else min(Hi[k] * (1 + buf), rhi)
        dist = abs(entry - sl) / entry
        if dist > 0:
            sigs.append((k, s, entry, sl, dist, float(ST[k])))
    return sigs, Hi, Lo, C, n


def pooled_trades(sources, tp):
    pooled = []
    for det in sources:
        pooled.extend(eval_tp(*det, tp))
    pooled.sort(key=lambda t: t[0])
    return pooled


def report(name, pooled):
    days = day_blocks(pooled)
    nets = np.array([t[1] for t in pooled]); win = 100.0 * (nets > 0).mean() if len(nets) else 0.0
    spd = sum(len(d) for d in days) / max(1, len(days))
    print("  %-34s n=%-4d  win %4.1f%%  %.2f trd/day" % (name, len(pooled), win, spd), flush=True)
    for Rp in (0.3, 0.4, 0.5):
        m = mc(days, Rp, 4.0, "R")
        print("      FIXED-R %.1f%% (daily4%%)  pass %5.1f%%  days p10/med/p90 %3.0f/%3.0f/%3.0f  DD med/p90/p99 %4.1f/%4.1f/%4.1f  fail d/m %.1f/%.1f"
              % (Rp, m["p"], m["d10"], m["d50"], m["d90"], m["dd50"], m["dd90"], m["dd99"], m["fdaily"], m["fmax"]), flush=True)


def main():
    print("HyroTrader 1-Step $200k | target 10%% max 6%%(trail) daily 4%%(trail) | RR vs RR+SD-big-wick-30m | 20k paths\n", flush=True)
    rr30c = detect_rr("study/clock_archive", "30m")
    wk30c = detect_wick_sdbig("study/clock_archive", "30m")
    rr15c = detect_rr("study/clock_archive", "15m")
    rr30b = detect_rr("study/recon_archive", "30m")
    print("  (wick-source SD+big 30m-clock raw events: %d)\n" % len(wk30c[0]), flush=True)
    for tp in (0.0025, 0.003, 0.004):
        print("=" * 104, flush=True)
        print("TP %.2f%%" % (tp * 100), flush=True)
        print("-" * 104, flush=True)
        report("A  RR 30m-clock", pooled_trades([rr30c], tp))
        report("B  RR 30m-clock + SDbig-wick30c", pooled_trades([rr30c, wk30c], tp))
        report("C  LIVE (15c+30c+30bucket RR)", pooled_trades([rr15c, rr30c, rr30b], tp))
        report("D  LIVE + SDbig-wick30c", pooled_trades([rr15c, rr30c, rr30b, wk30c], tp))
        print("", flush=True)


if __name__ == "__main__":
    main()
