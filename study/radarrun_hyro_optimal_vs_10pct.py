"""Head-to-head at the CHOSEN TP 0.25%: OPTIMAL (risk $800 fixed-R, flex size, loss capped) vs the ORIGINAL plan
(10% margin x10 lev = 100% notional fixed). HyroTrader 1-Step $200k real rules (target10 / max6% / daily3% TRAILING),
manual signal-capture {100/70/50%}. Same signals -> same win rate; only the money management differs.
Reports per rule: per-trade avg win / avg loss / WORST single loss (acct%), then per capture: pass%, fails,
days median/p90, trailing-DD p99. python study/radarrun_hyro_optimal_vs_10pct.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.radarrun_hyro_prop import detect_src, day_blocks, SRCS, TARGET, MAXDD, MAXD
from study.radarrun_proptp_alltf_clock import eval_tp

NPATH = 20000
TP = 0.0025
CAPS = [1.0, 0.7, 0.5]
RULES = [("OPTIMAL  risk $800 (flex)", 0.4, "R"), ("10% margin x10 (fixed)", 1.0, "N")]


def mc(days, mult, mode, cap, daily_lim=3.0):
    random.seed(7); passes = 0; fails = 0; dtp = []; ddr = []; kt = 0; dt = 0
    for _ in range(NPATH):
        eq = peak = 0.0; mdd = 0.0; passed = failed = False
        for dnum in range(1, MAXD + 1):
            day = days[random.randrange(len(days))]; ipeak = eq; dt += 1
            for net, r in day:
                if cap < 1.0 and random.random() > cap:
                    continue
                kt += 1
                eq += (mult * net * 100.0) if mode == "N" else (mult * r)
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
    return dict(p=100.0 * passes / NPATH, fails=fails, med=dq[0], p90=dq[1],
                dd99=(np.percentile(ddr, 99) if ddr else 0.0), spd=kt / max(1, dt))


def main():
    det = {n: detect_src(root, tf) for n, root, tf in SRCS}
    pooled = []
    for n, *_ in SRCS:
        pooled.extend(eval_tp(*det[n], TP))
    pooled.sort(key=lambda t: t[0])
    days = day_blocks(pooled)
    nets = np.array([t[1] for t in pooled]); rs = np.array([t[2] for t in pooled])
    win = 100.0 * (nets > 0).mean()
    print("Head-to-head @ TP 0.25%% | HyroTrader 1-Step $200k (target10 / max6%% / daily3%%, TRAILING) | n=%d | WIN %.1f%%\n"
          % (len(pooled), win), flush=True)
    for name, mult, mode in RULES:
        acct = (nets * 100.0 * mult) if mode == "N" else (mult * rs)
        aw = acct[acct > 0].mean(); al = acct[acct < 0].mean(); worst = acct.min()
        print("=" * 92, flush=True)
        print("%-26s | per-trade acct: avg win +%.2f%%  avg loss %.2f%%  WORST single %.2f%%"
              % (name, aw, al, worst), flush=True)
        print("  capture | eff trd/day | pass%% (fails/20k) | days med / p90 | DD p99", flush=True)
        for c in CAPS:
            m = mc(days, mult, mode, c)
            print("   %3.0f%%   |   %5.2f      | %6.2f%% (%4d)    |  %3.0f / %3.0f     | %.1f%%"
                  % (c * 100, m["spd"], m["p"], m["fails"], m["med"], m["p90"], m["dd99"]), flush=True)
        print("", flush=True)


if __name__ == "__main__":
    main()
