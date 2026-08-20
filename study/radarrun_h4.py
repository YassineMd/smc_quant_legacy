"""H4 — daily stop-out after 2 losses cuts the drawdown tail? Re-run the 20k(->50k) day-bootstrap MC with a rule: after
2 losing trades in a resampled UTC day, stop trading that day. Baseline = no rule. RR-only 15c+30c+30bkt, TP 0.25%,
candle stop, Hyro rules (target10 / max6 trail / daily4 trail). Report pass, median days, p99 + p99.9 + worst-path DD,
with and without the rule, at R0.3 and R0.4. Falsified if p99 DD doesn't fall, or median days rises past the ~2-month
(60d) budget. IN-SAMPLE. python study/radarrun_h4.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_proptp_alltf_clock import detect, eval_tp, SLBUF
from study.radarrun_hyro_prop import day_blocks

TARGET, MAXDD = 10.0, 6.0
NPATH, MAXD, DAILY = 50000, 400, 4.0
SRCS = [("study/clock_archive", "15m"), ("study/clock_archive", "30m"), ("study/recon_archive", "30m")]


def mc_h4(days, Rp, max_losses):
    random.seed(7); passes = 0; dtp = []; mdds = []
    for _ in range(NPATH):
        eq = peak = 0.0; mdd = 0.0; passed = failed = False
        for dnum in range(1, MAXD + 1):
            day = days[random.randrange(len(days))]; ipeak = eq; losses = 0
            for (net, r) in day:
                eq += Rp * r; peak = max(peak, eq); ipeak = max(ipeak, eq); mdd = max(mdd, peak - eq)
                if peak - eq >= MAXDD:
                    failed = True; break
                if ipeak - eq >= DAILY:
                    failed = True; break
                if eq >= TARGET:
                    passed = True; break
                if net < 0:
                    losses += 1
                    if max_losses is not None and losses >= max_losses:
                        break                       # stop trading this day after 2 losses
            if passed or failed:
                break
        mdds.append(mdd)
        if passed:
            passes += 1; dtp.append(dnum)
    md = np.percentile(dtp, 50) if dtp else 0
    ddq = np.percentile(mdds, [99, 99.9]); worst = max(mdds)
    return dict(p=100.0 * passes / NPATH, med=md, dd99=ddq[0], dd999=ddq[1], worst=worst)


def main():
    dets = [detect(sorted(load_archive(tf, root=root, drop_degenerate=False)[1],
                          key=lambda b: _f(b.get("start_time", 0))), SLBUF.get(tf, 0.003)) for root, tf in SRCS]
    pooled = []
    for d in dets:
        pooled.extend(eval_tp(*d, 0.0025))
    pooled.sort(key=lambda t: t[0])
    days = day_blocks(pooled)
    nloss_days = sum(1 for d in days for i, (_, r) in enumerate(d))  # not used; placeholder
    print("H4 — daily 2-loss stop-out | RR 15c+30c+30bkt TP0.25%% | Hyro target10/max6-trail/daily4-trail | %dk paths | IN-SAMPLE\n"
          % (NPATH // 1000), flush=True)
    print("  %-22s  pass%%    med-days   DD p99 / p99.9 / worst-path" % "config", flush=True)
    for Rp in (0.3, 0.4):
        for ml, tag in ((None, "baseline (no rule)"), (2, "stop after 2 losses/day")):
            m = mc_h4(days, Rp, ml)
            print("  R%.1f  %-16s  %5.1f%%   %5.0f     %4.1f%% / %4.1f%% / %4.1f%%"
                  % (Rp, tag, m["p"], m["med"], m["dd99"], m["dd999"], m["worst"]), flush=True)
        print("", flush=True)


if __name__ == "__main__":
    main()
