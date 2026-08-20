"""Honesty check on the wide-TP recommendation: the 30m-BUCKET source (recon volume-buckets) may overstate wide-TP
reachability vs the live daemon; the CLOCK sources are Binance-exact. Re-run the TP sweep on CLOCK-ONLY (15c+30c, RR and
RR+wick) so we see whether the wide-TP optimum survives WITHOUT the caveated bucket source. If the clock-only curve also
bottoms at ~0.40-0.50%, the recommendation is robust; if clock-only prefers a tighter TP, the wide-TP gain was leaning on
the optimistic bucket. python study/radarwick_tp_clockonly.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from study.radarwick_tp_sweep import run
from study.radarwick_prop_combined import detect_rr, detect_wick_sdbig


def main():
    print("CLOCK-ONLY TP sweep (15c+30c) | HyroTrader $200k target10/max6-trail/daily4-trail | 20k paths | FIXED-R\n", flush=True)
    rr15c = detect_rr("study/clock_archive", "15m"); rr30c = detect_rr("study/clock_archive", "30m")
    wk15c = detect_wick_sdbig("study/clock_archive", "15m"); wk30c = detect_wick_sdbig("study/clock_archive", "30m")
    run("CLOCK-ONLY LIVE  (RR 15c+30c)", [rr15c, rr30c])
    run("CLOCK-ONLY LIVE + wick (15c+30c)", [rr15c, rr30c, wk15c, wk30c])


if __name__ == "__main__":
    main()
