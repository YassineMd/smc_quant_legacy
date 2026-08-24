"""RADAR_MULT sweep addendum (user 2026-08-24): 0.2% net (+0.4% net) TP win rates per mult — the main sweep only
ran 0.5% net + RR 1:1. Reuses the CACHED per-mult union fire sets (no replay) + the canonical 1m first-touch
eval (non-overlap, fees+slip). python study/radarrun_radarmult_tp02.py"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "study", "out")
MULTS = [1.5, 2.0, 3.0, 4.0]
TPS = [("0.2% net", "fix", 0.0024), ("0.4% net", "fix", 0.0044)]


def load_union(tag, mult):
    path = os.path.join(OUT, "rr_union_b30m_%s_m%02d.json" % (tag, int(mult * 10)))
    legacy = os.path.join(OUT, "rr30mbkt_live_fires_union.json")
    if abs(mult - 3.0) < 1e-9 and tag == "recon" and os.path.exists(legacy):
        return json.load(open(legacy))
    return json.load(open(path))


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    from study.radarrun_bkt1h_deltapct_confirm import eval_1m
    from study.radarrun_honest_deltapct_tp import fmt
    print("RADAR_MULT x 0.2%/0.4% net TP — 30m bucket | cached unions | 1m first-touch | non-overlap\n", flush=True)
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    del A1
    Ad1 = sorted(load_archive("1m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    Td = np.array([_f(b.get("start_time")) for b in Ad1]); Hd = np.array([_f(b.get("high")) for b in Ad1]); Ld = np.array([_f(b.get("low")) for b in Ad1])
    del Ad1
    for mult in MULTS:
        fr = load_union("recon", mult)
        frd = load_union("daemon", mult)
        print("=" * 110, flush=True)
        print("RADAR_MULT %.1f | recon badges %d | daemon badges %d" % (mult, len(fr), len(frd)), flush=True)
        for cname, kind, val in TPS:
            d, _ = eval_1m(fr, kind, val, T1, H1, L1)
            print("  recon  %-8s %s" % (cname, fmt(d)), flush=True)
        for cname, kind, val in TPS:
            d, _ = eval_1m(frd, kind, val, Td, Hd, Ld)
            print("  daemon %-8s %s" % (cname, fmt(d)), flush=True)


if __name__ == "__main__":
    main()
