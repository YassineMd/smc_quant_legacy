"""STACK-FLIP BIAS + EMA100-POSITION x RADAR RUNNER, 30m bucket, CANDLE SL 0.1% — HONEST test (user
2026-08-27: the radarrun_stack100_1h_sl01 rule on the 30m bucket — LONG only if the last stack flip is
GREEN AND close > EMA100 AND the candle midpoint (>=50%% of its range) is above the EMA100; SHORT mirror;
SL 0.1%% beyond the signal candle). Feature definitions, cells and harness IMPORTED UNCHANGED from
radarrun_stack100_1h_sl01 (pre-registered there); only the fire sets / archives switch to 30m (both cached).
EXITS: 0.2%% net, 0.4%% net, 0.5%% net, RR 1:1, RR 1:2.
python study/radarrun_stack100_30m_sl01.py"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np

from study.radarrun_stack100_1h_sl01 import features, report

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "study", "out")


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    from study.radarrun_honest_deltapct_tp import load_fires
    print("STACK-FLIP BIAS + EMA100-POSITION x RADAR RUNNER 30m — SL 0.1%% beyond the candle | 5 exits | pre-registered\n", flush=True)
    t0 = time.time()
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    del A1
    print("=" * 120, flush=True)
    print("RECON 30m BUCKET 2025-01 .. 2026-06 (per-year split in rows)", flush=True)
    A = sorted(load_archive("30m", root="study/recon_archive", drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    report(features(load_fires("bucket", "30m"), A), T1, H1, L1)
    del A, T1, H1, L1
    Ad1 = sorted(load_archive("1m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    Td = np.array([_f(b.get("start_time")) for b in Ad1]); Hd = np.array([_f(b.get("high")) for b in Ad1]); Ld = np.array([_f(b.get("low")) for b in Ad1])
    del Ad1
    print("=" * 120, flush=True)
    print("DAEMON 30m (TRUE OOS, 2026-06-20 ..)", flush=True)
    Ad = sorted(load_archive("30m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    report(features(json.load(open(os.path.join(OUT, "rr_union_b30m_daemon_m30.json"))), Ad), Td, Hd, Ld)
    print("\ndone in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
