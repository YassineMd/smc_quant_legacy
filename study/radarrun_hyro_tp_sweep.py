"""Is a tighter TP (0.2% / 0.25%) better than 0.3% for the OPTIMAL rule? Sweep TP on the recommended combined config
(15m clock + 30m clock + 30m bucket) under HyroTrader 1-Step $200k real rules, OPTIMAL sizing (risk 0.4% = $800/trade,
fixed-R, loss capped). Reports per TP: win%, avg win/loss (acct%), pass%, days-to-pass p10/median/mean/p90, trailing-DD
med/p90/p99 among passing runs. Also the SIMPLE rule (5% margin fixed) for contrast. python study/radarrun_hyro_tp_sweep.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.radarrun_hyro_prop import detect_src, day_blocks, SRCS
from study.radarrun_hyro_two_rules import mc
from study.radarrun_proptp_alltf_clock import eval_tp

TPS = [0.002, 0.0025, 0.003, 0.0035, 0.004]


def main():
    det = {n: detect_src(root, tf) for n, root, tf in SRCS}
    print("HyroTrader 1-Step $200k | target 10%%, max 6%% trailing, daily 3%% (=4%%, non-binding at safe size)\n", flush=True)
    print("OPTIMAL rule (risk $800/trade, fixed-R, loss capped):", flush=True)
    print("  TP     | n    | win%% | avgW/avgL acct%% | pass%% (fails) | days p10/MED/mean/p90 | DD med/p90/p99", flush=True)
    print("  " + "-" * 104, flush=True)
    rows = {}
    for tp in TPS:
        pooled = []
        for n, *_ in SRCS:
            pooled.extend(eval_tp(*det[n], tp))
        pooled.sort(key=lambda t: t[0])
        days = day_blocks(pooled)
        nets = np.array([t[1] for t in pooled]); win = 100.0 * (nets > 0).mean()
        acct = np.array([0.4 * t[2] for t in pooled])           # OPTIMAL: per-trade acct move = Rp(0.4%) * r
        aw = acct[acct > 0].mean(); al = acct[acct < 0].mean()
        m = mc(days, 0.4, "R", 3.0)
        rows[tp] = m
        print("  %.2f%%  | %-4d | %4.1f | +%.2f / %.2f     | %5.2f%% (%3d)  |  %3.0f / %3.0f / %4.1f / %3.0f  |  %.1f/%.1f/%.1f%%"
              % (tp * 100, len(pooled), win, aw, al, m["p"], m["fails"],
                 m["d10"], m["d50"], m["dmean"], m["d90"], m["dd50"], m["dd90"], m["dd99"]), flush=True)

    print("\nSIMPLE rule (5%% margin x10 fixed) for contrast:", flush=True)
    print("  TP     | pass%% (fails) | days p10/MED/mean/p90 | DD med/p90/p99", flush=True)
    print("  " + "-" * 70, flush=True)
    for tp in TPS:
        pooled = []
        for n, *_ in SRCS:
            pooled.extend(eval_tp(*det[n], tp))
        pooled.sort(key=lambda t: t[0])
        days = day_blocks(pooled)
        m = mc(days, 0.5, "N", 3.0)
        print("  %.2f%%  | %5.2f%% (%3d)  |  %3.0f / %3.0f / %4.1f / %3.0f  |  %.1f/%.1f/%.1f%%"
              % (tp * 100, m["p"], m["fails"], m["d10"], m["d50"], m["dmean"], m["d90"],
                 m["dd50"], m["dd90"], m["dd99"]), flush=True)


if __name__ == "__main__":
    main()
