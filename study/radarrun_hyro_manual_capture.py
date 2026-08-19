"""MANUAL-trading reality: you won't catch all ~8.5 signals/day by hand. Sweep TP {0.20/0.25/0.30%} x signal-capture
rate {100/70/50%} under the OPTIMAL rule (risk $800, fixed-R, loss capped), HyroTrader 1-Step $200k real rules.
Capture = each signal independently kept with prob c (random-miss model). Reports win% (constant per TP), effective
trades/day, pass%, days-to-pass median/p90, trailing-DD p99 among passers. Goal: highest win + lowest DD, must clear
inside ~60 days (1-2 months) even on a slow (p90) run. python study/radarrun_hyro_manual_capture.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.radarrun_hyro_prop import detect_src, day_blocks, SRCS, TARGET, MAXDD, MAXD
from study.radarrun_proptp_alltf_clock import eval_tp

NPATH = 20000
TPS = [0.002, 0.0025, 0.003]
CAPS = [1.0, 0.7, 0.5]
RISK = 0.4   # OPTIMAL: 0.4% of account = $800 per trade (fixed-R)


def mc_cap(days, daily_lim, cap):
    random.seed(7); passes = 0; fails = 0; dtp = []; ddr = []; kept_tot = 0; day_tot = 0
    for _ in range(NPATH):
        eq = peak = 0.0; mdd = 0.0; passed = failed = False
        for dnum in range(1, MAXD + 1):
            day = days[random.randrange(len(days))]; ipeak = eq; day_tot += 1
            for net, r in day:
                if cap < 1.0 and random.random() > cap:
                    continue                                     # missed this signal (trading by hand)
                kept_tot += 1
                eq += RISK * r
                peak = max(peak, eq); ipeak = max(ipeak, eq); mdd = max(mdd, peak - eq)
                if peak - eq >= MAXDD or ipeak - eq >= daily_lim:
                    failed = True; break
                if eq >= TARGET:
                    passed = True; break
            if passed or failed:
                break
        if passed:
            passes += 1; dtp.append(dnum); ddr.append(mdd)
        elif failed:
            fails += 1
    dq = np.percentile(dtp, [50, 90]) if dtp else [0, 0]
    dd99 = np.percentile(ddr, 99) if ddr else 0.0
    return dict(p=100.0 * passes / NPATH, fails=fails, med=dq[0], p90=dq[1], dd99=dd99,
                spd=kept_tot / max(1, day_tot))


def main():
    det = {n: detect_src(root, tf) for n, root, tf in SRCS}
    print("OPTIMAL rule (risk $800) | HyroTrader 1-Step $200k | daily 3%% | MANUAL signal-capture sweep\n", flush=True)
    print("  Target: highest win + lowest DD, clearing inside ~60 days (1-2 months) even at p90.\n", flush=True)
    for tp in TPS:
        pooled = []
        for n, *_ in SRCS:
            pooled.extend(eval_tp(*det[n], tp))
        pooled.sort(key=lambda t: t[0])
        days = day_blocks(pooled)
        win = 100.0 * (np.array([t[1] for t in pooled]) > 0).mean()
        print("==== TP %.2f%%  (win rate %.1f%%) ====" % (tp * 100, win), flush=True)
        print("  capture | eff trd/day | pass%% (fails) | days MED / p90 | DD p99 | inside 2mo?", flush=True)
        for c in CAPS:
            m = mc_cap(days, 3.0, c)
            ok = "YES" if m["p90"] <= 60 else ("p90=%d>60" % m["p90"])
            print("   %3.0f%%   |   %5.2f      | %5.2f%% (%3d)  |  %3.0f / %3.0f     | %.1f%%  | %s"
                  % (c * 100, m["spd"], m["p"], m["fails"], m["med"], m["p90"], m["dd99"], ok), flush=True)
        print("", flush=True)


if __name__ == "__main__":
    main()
