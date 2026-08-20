"""Push the TP ceiling: is 0.25% really optimal for the 15c+30c+30bkt line, or does a HIGHER TP pass FASTER? A bigger TP =
bigger wins = reach the 10% target in fewer trades, at the cost of a lower win% + higher DD. The prop MC captures BOTH, so
it finds the TP that passes fastest while staying under the 6% max. Sweep TP 0.25->0.80% x R 0.3/0.4/0.5, LIVE (RR only)
vs LIVE+wick, on the exact Hyro rules (target10/max6-trail/daily4-trail, 20k paths). Also prints win% + avg-R + trд/day so
the speed-vs-DD tradeoff is legible. python study/radarwick_tp_sweep.py

CAVEAT (report to user): the CLOCK archive is Binance-exact clock OHLC (wider-TP reachability is live-faithful), but the
30m-BUCKET source is recon volume-buckets that can run smaller than live daemon buckets -> wider TP may fill LESS often
live than here. Treat the bucket contribution to wide-TP speed as optimistic."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.radarrun_hyro_prop import day_blocks, mc
from study.radarwick_prop_combined import detect_rr, detect_wick_sdbig, pooled_trades

TPS = [0.0025, 0.003, 0.0035, 0.004, 0.005, 0.006, 0.007, 0.008]
RPS = [0.3, 0.4, 0.5]


def run(name, sources):
    print("################  %s  ################" % name, flush=True)
    print("  %-6s %-8s %-7s | %s" % ("TP", "trd/day", "win%", "  ".join("R%.1f: pass / med-days / DDp99 / fail-max" % r for r in RPS)), flush=True)
    for tp in TPS:
        pooled = pooled_trades(sources, tp)
        days = day_blocks(pooled)
        nets = np.array([t[1] for t in pooled]); rs = np.array([t[2] for t in pooled])
        win = 100.0 * (nets > 0).mean() if len(nets) else 0.0
        avgR = rs.mean() if len(rs) else 0.0
        spd = sum(len(d) for d in days) / max(1, len(days))
        cells = []
        for Rp in RPS:
            m = mc(days, Rp, 4.0, "R")
            cells.append("%5.1f%% /%4.0fd /%4.1f%% /%3.1f%%" % (m["p"], m["d50"], m["dd99"], m["fmax"]))
        print("  %.2f%%  %5.2f    %4.1f%% (avgR %+.2f) | %s" % (tp * 100, spd, win, avgR, "   ".join(cells)), flush=True)
    print("", flush=True)


def main():
    print("TP-CEILING sweep | 15c+30c+30bkt | HyroTrader $200k target10/max6-trail/daily4-trail | 20k paths | FIXED-R\n", flush=True)
    rr15c = detect_rr("study/clock_archive", "15m"); rr30c = detect_rr("study/clock_archive", "30m")
    rr30b = detect_rr("study/recon_archive", "30m")
    wk15c = detect_wick_sdbig("study/clock_archive", "15m"); wk30c = detect_wick_sdbig("study/clock_archive", "30m")
    wk30b = detect_wick_sdbig("study/recon_archive", "30m")
    live = [rr15c, rr30c, rr30b]
    livew = live + [wk15c, wk30c, wk30b]
    run("LIVE  (RR 15c+30c+30bkt)", live)
    run("LIVE + wick (SD+big on all 3)", livew)


if __name__ == "__main__":
    main()
