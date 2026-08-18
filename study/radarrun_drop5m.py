"""Combined WITHOUT 5m: 15m clock + 30m clock + 30m bucket. Does dropping the DD-liability source lower the combined
drawdown (and by how much / how much slower)? Reuses the same detect/eval/MC. python study/radarrun_drop5m.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from study.radarrun_combined_optimize import detect_source, summarize, SOURCES
from study.radarrun_proptp_alltf_clock import eval_tp

SRCS = [s for s in SOURCES if s[0] != "5m clock"]          # 15m clock, 30m clock, 30m bucket


def main():
    det = {name: detect_source(root, tf, filt) for name, root, tf, filt in SRCS}

    def pool(tpmap):
        p = []
        for name, *_ in SRCS:
            p.extend(eval_tp(*det[name], tpmap[name]))
        p.sort(key=lambda t: t[0]); return p

    print("COMBINED WITHOUT 5m  (15m clock + 30m clock + 30m bucket) -- pass%%/median-days @R0.5/0.75/1.0\n", flush=True)
    for tp in (0.002, 0.003, 0.004, 0.005):
        summarize("uniform @%.1f%%" % (tp * 100), pool({n: tp for n, *_ in SRCS}))
    summarize("mix 15m.3/30mc.2/30mb.3", pool({"15m clock": 0.003, "30m clock": 0.002, "30m bucket": 0.003}))
    summarize("mix 15m.5/30mc.3/30mb.5", pool({"15m clock": 0.005, "30m clock": 0.003, "30m bucket": 0.005}))


if __name__ == "__main__":
    main()
