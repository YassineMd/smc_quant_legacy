"""Days-to-pass DISTRIBUTION for the recommended config: 15m clock + 30m clock + 30m bucket @ 0.3% TP.
Reports p10/p25/median/p75/p90 (trading days ~ calendar days on crypto) + pass% at R0.5/0.75/1.0.
python study/radarrun_drop5m_days.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.radarrun_combined_optimize import detect_source, day_blocks
from study.radarrun_proptp_alltf_clock import eval_tp, TARGET, MAXDD, DAILY, MAXD

SRCS = [("15m clock", "study/clock_archive", "15m", False), ("30m clock", "study/clock_archive", "30m", False),
        ("30m bucket", "study/recon_archive", "30m", False)]
TP = 0.003; NPATH = 20000


def mc_dist(days, Rp):
    random.seed(7); passes = 0; dtp = []
    for _ in range(NPATH):
        eq = peak = 0.0; passed = failed = False
        for dd in range(1, MAXD + 1):
            day = days[random.randrange(len(days))]; dstart = eq; dlow = eq
            for r in day:
                eq += Rp * r; dlow = min(dlow, eq); peak = max(peak, eq)
                if peak - eq >= MAXDD:
                    failed = True; break
                if eq >= TARGET:
                    passed = True; break
            if failed or (dstart - dlow) >= DAILY:
                failed = True
            if passed or failed:
                if passed:
                    passes += 1; dtp.append(dd)
                break
    return 100.0 * passes / NPATH, dtp


def main():
    pooled = []
    for name, root, tf, filt in SRCS:
        pooled.extend(eval_tp(*detect_source(root, tf, filt), TP))
    pooled.sort(key=lambda t: t[0])
    days = day_blocks(pooled)
    print("15m clock + 30m clock + 30m bucket @ 0.3%% TP  (n=%d trades, %.1f/day)\n"
          % (len(pooled), sum(len(d) for d in days) / max(1, len(days))), flush=True)
    print("  risk  | pass%% | days-to-pass  p10 / p25 / MEDIAN / p75 / p90", flush=True)
    print("  " + "-" * 60, flush=True)
    for Rp in (0.5, 0.75, 1.0):
        p, dtp = mc_dist(days, Rp)
        q = np.percentile(dtp, [10, 25, 50, 75, 90]) if dtp else [0] * 5
        print("  R%.2f%% | %4.0f%% | %3d / %3d / %3d / %3d / %3d"
              % (Rp, p, q[0], q[1], q[2], q[3], q[4]), flush=True)


if __name__ == "__main__":
    main()
