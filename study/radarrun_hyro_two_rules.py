"""Clean side-by-side of the SIMPLE rule vs the OPTIMAL rule on the recommended combined config
(15m clock + 30m clock + 30m bucket, TP 0.3%), HyroTrader 1-Step $200k real rules.

  SIMPLE  = fixed 5% margin x10lev = 50% notional every trade  (mode N, mult 0.5)
  OPTIMAL = risk a fixed 0.4% ($800) per trade, size flexes    (mode R, mult 0.4)

Reports (per rule x daily cap 3%/4%): win% (same for both, it's the same trades), avg win / avg loss / worst single
loss in account %, pass%, days-to-pass p10/median/mean/p90, trailing-DD median/p90/worst. 20k-path day-block MC.
python study/radarrun_hyro_two_rules.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.radarrun_hyro_prop import detect_src, day_blocks, SRCS, TARGET, MAXDD, MAXD
from study.radarrun_proptp_alltf_clock import eval_tp

TP = 0.003; NPATH = 20000


def mc(days, mult, mode, daily_lim):
    random.seed(7); passes = 0; fails = 0; dtp = []; ddr_pass = []
    for _ in range(NPATH):
        eq = peak = 0.0; mdd = 0.0; passed = failed = False
        for dnum in range(1, MAXD + 1):
            day = days[random.randrange(len(days))]; ipeak = eq
            for net, r in day:
                eq += (mult * net * 100.0) if mode == "N" else (mult * r)
                peak = max(peak, eq); ipeak = max(ipeak, eq); mdd = max(mdd, peak - eq)
                if peak - eq >= MAXDD or ipeak - eq >= daily_lim:
                    failed = True; break
                if eq >= TARGET:
                    passed = True; break
            if passed or failed:
                break
        if passed:
            passes += 1; dtp.append(dnum); ddr_pass.append(mdd)   # DD only among PASSING runs
        elif failed:
            fails += 1
    dq = np.percentile(dtp, [10, 50, 90]) if dtp else [0, 0, 0]
    ddq = np.percentile(ddr_pass, [50, 90, 99]) if ddr_pass else [0, 0, 0]
    return dict(p=100.0 * passes / NPATH, fails=fails, d10=dq[0], d50=dq[1],
                dmean=(np.mean(dtp) if dtp else 0), d90=dq[2], dd50=ddq[0], dd90=ddq[1], dd99=ddq[2])


def main():
    det = {n: detect_src(root, tf) for n, root, tf in SRCS}
    pooled = []
    for n, *_ in SRCS:
        pooled.extend(eval_tp(*det[n], TP))
    pooled.sort(key=lambda t: t[0])
    days = day_blocks(pooled)
    nets = np.array([t[1] for t in pooled]); win = 100.0 * (nets > 0).mean()
    spd = sum(len(d) for d in days) / max(1, len(days))
    print("HyroTrader 1-Step $200k | target 10%%, max 6%% trailing, daily cap 3%%/4%% trailing | TP 0.3%%", flush=True)
    print("Signals: 15m clock + 30m clock + 30m bucket | n=%d | WIN RATE %.1f%% (identical for both rules) | %.1f trd/day\n"
          % (len(pooled), win, spd), flush=True)

    RULES = [("SIMPLE  (5%% margin x10, fixed)", 0.5, "N"), ("OPTIMAL (risk $800/trade, flex)", 0.4, "R")]
    for name, mult, mode in RULES:
        # per-trade account move for this rule
        if mode == "N":
            acct = nets * 100.0 * mult
        else:
            acct = np.array([mult * t[2] for t in pooled])
        aw = acct[acct > 0].mean(); al = acct[acct < 0].mean(); worst = acct.min()
        print("=" * 90, flush=True)
        print("%s | per-trade acct: avg win +%.2f%%  avg loss %.2f%%  worst single %.2f%%"
              % (name, aw, al, worst), flush=True)
        print("  daily cap | pass%% (fails/20k) | days p10 / MEDIAN / mean / p90 | DD(passing) med / p90 / p99", flush=True)
        for dl in (3.0, 4.0):
            m = mc(days, mult, mode, dl)
            print("    %.0f%%     | %5.2f%% (%3d)     |   %3.0f / %3.0f / %4.1f / %3.0f       |   %4.1f%% / %4.1f%% / %4.1f%%"
                  % (dl, m["p"], m["fails"], m["d10"], m["d50"], m["dmean"], m["d90"],
                     m["dd50"], m["dd90"], m["dd99"]), flush=True)
        print("", flush=True)


if __name__ == "__main__":
    main()
