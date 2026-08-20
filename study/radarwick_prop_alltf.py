"""Prop MC of the SD+big-wick add across ALL of 15m/30m/1h x clock/bucket (user asked for the full grid; the earlier run
only did 30m-clock). Per cell: RR-alone vs RR + SD+big-wick (isolation), + a portfolio block (LIVE = RR[15c+30c+30bkt]
vs LIVE + all three wick sources). HyroTrader 1-Step $200k (target10/max6-trail/daily4-trail), 20k paths, FIXED-R, same
candle-SL bracket. python study/radarwick_prop_alltf.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.radarrun_hyro_prop import day_blocks, mc
from study.radarwick_prop_combined import detect_rr, detect_wick_sdbig, pooled_trades

CELLS = [("clock", "study/clock_archive", "15m"), ("clock", "study/clock_archive", "30m"),
         ("clock", "study/clock_archive", "1h"), ("bucket", "study/recon_archive", "15m"),
         ("bucket", "study/recon_archive", "30m"), ("bucket", "study/recon_archive", "1h")]
TPS = [0.0025, 0.003, 0.004]
RPS = [0.4, 0.5]


def line(tag, pooled):
    days = day_blocks(pooled)
    nets = np.array([t[1] for t in pooled]); win = 100.0 * (nets > 0).mean() if len(nets) else 0.0
    spd = sum(len(d) for d in days) / max(1, len(days))
    cells = []
    for Rp in RPS:
        m = mc(days, Rp, 4.0, "R")
        cells.append("R%.1f pass%5.1f%% med%3.0fd DDp99 %4.1f%%" % (Rp, m["p"], m["d50"], m["dd99"]))
    print("      %-10s n=%-4d win%4.1f%% %.2f/day | %s" % (tag, len(pooled), win, spd, "  |  ".join(cells)), flush=True)


def main():
    print("SD+big-wick prop MC — FULL GRID 15m/30m/1h x clock/bucket | HyroTrader $200k | 20k paths | FIXED-R daily4%%\n", flush=True)
    rr = {}; wk = {}
    for ds, root, tf in CELLS:
        rr[(ds, tf)] = detect_rr(root, tf); wk[(ds, tf)] = detect_wick_sdbig(root, tf)
    for ds, root, tf in CELLS:
        print("================ %s %s  (RR=%d events | SD+big-wick=%d events) ================"
              % (ds.upper(), tf, len(rr[(ds, tf)][0]), len(wk[(ds, tf)][0])), flush=True)
        for tp in TPS:
            print("  TP %.2f%%" % (tp * 100), flush=True)
            line("RR", pooled_trades([rr[(ds, tf)]], tp))
            line("RR+wick", pooled_trades([rr[(ds, tf)], wk[(ds, tf)]], tp))
        print("", flush=True)
    # portfolio: LIVE (RR 15c+30c+30bkt) vs LIVE + all 3 matching wick sources
    live = [rr[("clock", "15m")], rr[("clock", "30m")], rr[("bucket", "30m")]]
    livew = live + [wk[("clock", "15m")], wk[("clock", "30m")], wk[("bucket", "30m")]]
    print("================ PORTFOLIO  LIVE = RR[15c+30c+30bkt]  vs  LIVE + wick[15c+30c+30bkt] ================", flush=True)
    for tp in TPS:
        print("  TP %.2f%%" % (tp * 100), flush=True)
        line("LIVE", pooled_trades(live, tp))
        line("LIVE+wick", pooled_trades(livew, tp))
    print("", flush=True)


if __name__ == "__main__":
    main()
