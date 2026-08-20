"""DIAMOND ONLY (the cyan-diamond wick-breakout = SD+big-wick), tested STANDALONE — NOT mixed with the regular Radar
Runner triangles. Its own prop MC + TP-ceiling sweep: can the diamond signal pass the HyroTrader challenge on its own, and
what TP is best for IT? Pooled diamond line (15c+30c+30bkt) and each source alone. Same bracket/fees/rules as the RR MC.
python study/radarwick_diamond_standalone.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from study.radarwick_tp_sweep import run
from study.radarwick_prop_combined import detect_wick_sdbig


def main():
    print("DIAMOND (SD+big-wick) STANDALONE | HyroTrader $200k target10/max6-trail/daily4-trail | 20k paths | FIXED-R\n", flush=True)
    d15c = detect_wick_sdbig("study/clock_archive", "15m")
    d30c = detect_wick_sdbig("study/clock_archive", "30m")
    d30b = detect_wick_sdbig("study/recon_archive", "30m")
    d1c = detect_wick_sdbig("study/clock_archive", "1h")
    print("  (diamond raw events: 15c=%d  30c=%d  30bkt=%d  1hc=%d)\n"
          % (len(d15c[0]), len(d30c[0]), len(d30b[0]), len(d1c[0])), flush=True)
    run("DIAMOND pooled (15c+30c+30bkt)", [d15c, d30c, d30b])
    run("DIAMOND 15m-clock only", [d15c])
    run("DIAMOND 30m-clock only", [d30c])
    run("DIAMOND 30m-bucket only", [d30b])


if __name__ == "__main__":
    main()
