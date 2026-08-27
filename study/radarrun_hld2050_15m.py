"""DUAL EMA-HL DELTA (20 + 50) x RADAR RUNNER, 15m bucket — HONEST test (user 2026-08-27: the
radarrun_hld2050_30m rule on the 15m bucket — LONG only if BOTH the EMA20 and EMA50 HL deltas are positive,
SHORT only if both negative). Feature definitions, cells and harness IMPORTED UNCHANGED from
radarrun_hld2050_30m (pre-registered there); only the fire sets / archives switch to 15m (both cached:
recon rr_union_bucket_15m_s1 / daemon rr_union_b15m_daemon_m30).
python study/radarrun_hld2050_15m.py"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np

from study.radarrun_hld2050_30m import features, report

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "study", "out")


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    from study.radarrun_honest_deltapct_tp import load_fires
    print("DUAL EMA-HL DELTA (20+50) x RADAR RUNNER 15m BUCKET — long: d20>0 AND d50>0 / short mirror | pre-registered\n", flush=True)
    t0 = time.time()
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    del A1
    print("=" * 120, flush=True)
    print("RECON 15m BUCKET 2025-01 .. 2026-06 (per-year split in rows)", flush=True)
    A = sorted(load_archive("15m", root="study/recon_archive", drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    report(features(load_fires("bucket", "15m"), A), T1, H1, L1)
    del A, T1, H1, L1
    Ad1 = sorted(load_archive("1m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    Td = np.array([_f(b.get("start_time")) for b in Ad1]); Hd = np.array([_f(b.get("high")) for b in Ad1]); Ld = np.array([_f(b.get("low")) for b in Ad1])
    del Ad1
    print("=" * 120, flush=True)
    print("DAEMON 15m (TRUE OOS, 2026-06-20 ..)", flush=True)
    Ad = sorted(load_archive("15m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    report(features(json.load(open(os.path.join(OUT, "rr_union_b15m_daemon_m30.json"))), Ad), Td, Hd, Ld)
    print("\ndone in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
