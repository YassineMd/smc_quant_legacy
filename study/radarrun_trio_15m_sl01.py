"""EMA-SIDE + HL-DELTA + BREAKOUT-CLOSE x RADAR RUNNER, 15m bucket, CANDLE SL 0.1% — HONEST test (user
2026-08-27: on 15m, SL 0.1% beyond the signal candle's extreme, TP RR 1:1 and RR 1:2; LONG only if the bar
closes above the 20 EMA AND the EMA HL delta is positive AND the close is above the previous bar's high;
SHORT mirror). Canonical harness: cached union fire sets (recon rr_union_bucket_15m_s1 / daemon
rr_union_b15m_daemon_m30), 1-MINUTE first-touch, non-overlap taken(), fees 0.04% RT + 0.03% slip/leg,
prop MC; recon per-year + DAEMON OOS decisive.

PRE-REGISTERED (frozen; no iteration): entry = the badge's recorded entry; SL REBUILT = signal bar low x
(1 - 0.001) for LONG / high x (1 + 0.001) for SHORT (the Long Wick harness convention). EMA side / HL delta
/ breakout-close exactly as radarrun_hldelta.py + radarrun_brkclose_30m.py (EMA20 seeded first close; delta
= signed net of window extremes each measured to the EMA at its own bar, 20-bar window incl. the fire bar).
FULL = all three on the fire's side. CELLS: ALL (custom-SL control) / FULL / REST / FULL-LONG / FULL-SHORT.
EXITS: RR 1:1, RR 1:2.
python study/radarrun_trio_15m_sl01.py"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np

from study.radarrun_hldelta import ema20, P

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "study", "out")
TPS = [("RR 1:1", "rr", 1.0), ("RR 1:2", "rr", 2.0)]
SLB = 0.001


def features(fires, A):
    from study.candle_bias_1h import _f
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    H = np.array([_f(b.get("high")) for b in A])
    L = np.array([_f(b.get("low")) for b in A])
    E = ema20(C)
    recs = []
    for f in fires:
        b = int(f[0]); s = int(f[2]); e = float(f[3])
        sl = L[b] * (1.0 - SLB) if s > 0 else H[b] * (1.0 + SLB)   # SL 0.1% beyond the signal candle's extreme
        if e <= 0 or sl <= 0 or (s > 0 and sl >= e) or (s < 0 and sl <= e):
            continue
        ema_ok = (s > 0 and C[b] > E[b]) or (s < 0 and C[b] < E[b])
        hi_p = hi_i = lo_p = lo_i = None
        for i in range(max(0, b - P + 1), b + 1):
            if H[i] > 0 and (hi_p is None or H[i] >= hi_p):
                hi_p, hi_i = H[i], i
            if L[i] > 0 and (lo_p is None or L[i] <= lo_p):
                lo_p, lo_i = L[i], i
        dlt_ok = False
        if hi_p is not None and lo_p is not None and E[hi_i] > 0 and E[lo_i] > 0:
            delta = (hi_p - E[hi_i]) / E[hi_i] + (lo_p - E[lo_i]) / E[lo_i]
            dlt_ok = (s > 0 and delta > 0) or (s < 0 and delta < 0)
        bc_ok = (b >= 1 and H[b - 1] > 0 and L[b - 1] > 0 and C[b] > 0
                 and ((s > 0 and C[b] > H[b - 1]) or (s < 0 and C[b] < L[b - 1])))
        recs.append(dict(f=(b, float(f[1]), s, e, float(sl)), s=s, w=ema_ok and dlt_ok and bc_ok))
    return recs


CELLS = [
    ("ALL", lambda r: True),
    ("FULL", lambda r: r["w"]),
    ("REST", lambda r: not r["w"]),
    ("FULL-LONG", lambda r: r["w"] and r["s"] > 0),
    ("FULL-SHORT", lambda r: r["w"] and r["s"] < 0),
]


def report(recs, T1, H1, L1):
    from study.radarrun_honest_deltapct_tp import fmt
    from study.radarrun_bkt1h_deltapct_confirm import eval_1m
    for name, keep in CELLS:
        fs = [r["f"] for r in recs if keep(r)]
        for cname, kind, val in TPS:
            d, _ = eval_1m(fs, kind, val, T1, H1, L1)
            print("  %-10s %-8s %s" % (name, cname, fmt(d)), flush=True)


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    from study.radarrun_honest_deltapct_tp import load_fires
    print("EMA-SIDE + HL-DELTA + BREAKOUT-CLOSE x RADAR RUNNER 15m — SL 0.1%% beyond the candle | RR 1:1 + 1:2 | pre-registered\n", flush=True)
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
