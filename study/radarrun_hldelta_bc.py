"""EMA-HL DELTA + BREAKOUT-CLOSE x RADAR RUNNER, 30m + 15m bucket — HONEST test (user 2026-08-27: LONG only
if the EMA HL delta is positive AND the signal bar closes above the previous bar's high; SHORT only if the
delta is negative AND it closes below the previous bar's low). Canonical harness: cached union fire sets,
1-MINUTE first-touch, non-overlap taken(), fees 0.04% RT + 0.03% slip/leg, prop MC; recon per-year + DAEMON
OOS decisive.

PRE-REGISTERED (frozen; no iteration): DELTA exactly as radarrun_hldelta.py (shipped ema_ext readout at the
fire close: 20-bar window incl. the fire bar, window high/low each measured vertically to EMA20 AT its own
bar, delta = signed net); BC exactly as radarrun_brkclose_30m.py (long iff C > prev high / short iff C <
prev low; b=0 or degenerate -> false). RULE = both conditions on the fire's side. CELLS per tf: ALL /
WITH (the rule) / REST (complement) / WITH-LONG / WITH-SHORT. EXITS: 0.2% net, 0.4% net, RR 1:0.5, RR 1:1.
python study/radarrun_hldelta_bc.py"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np

from study.radarrun_hldelta import ema20, P

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "study", "out")
TPS = [("0.2% net", "fix", 0.0024), ("0.4% net", "fix", 0.0044), ("RR 1:0.5", "rr", 0.5), ("RR 1:1", "rr", 1.0)]


def features(fires, A):
    from study.candle_bias_1h import _f
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    H = np.array([_f(b.get("high")) for b in A])
    L = np.array([_f(b.get("low")) for b in A])
    E = ema20(C)
    recs = []
    for f in fires:
        b = int(f[0]); s = int(f[2])
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
        recs.append(dict(f=tuple(f), s=s, w=dlt_ok and bc_ok))
    return recs


CELLS = [
    ("ALL", lambda r: True),
    ("WITH", lambda r: r["w"]),
    ("REST", lambda r: not r["w"]),
    ("WITH-LONG", lambda r: r["w"] and r["s"] > 0),
    ("WITH-SHORT", lambda r: r["w"] and r["s"] < 0),
]


def report(recs, T1, H1, L1):
    from study.radarrun_honest_deltapct_tp import fmt
    from study.radarrun_bkt1h_deltapct_confirm import eval_1m
    for name, keep in CELLS:
        fs = [r["f"] for r in recs if keep(r)]
        for cname, kind, val in TPS:
            d, _ = eval_1m(fs, kind, val, T1, H1, L1)
            print("  %-10s %-9s %s" % (name, cname, fmt(d)), flush=True)


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    from study.radarrun_honest_deltapct_tp import load_fires
    print("EMA-HL DELTA + BREAKOUT-CLOSE x RADAR RUNNER — 30m + 15m bucket | long: delta>0 AND C>prev high / short mirror | pre-registered\n", flush=True)
    t0 = time.time()
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    del A1
    for tf in ("30m", "15m"):
        print("=" * 120, flush=True)
        print("RECON %s BUCKET 2025-01 .. 2026-06 (per-year split in rows)" % tf, flush=True)
        A = sorted(load_archive(tf, root="study/recon_archive", drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
        report(features(load_fires("bucket", tf), A), T1, H1, L1)
        del A
    del T1, H1, L1
    Ad1 = sorted(load_archive("1m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    Td = np.array([_f(b.get("start_time")) for b in Ad1]); Hd = np.array([_f(b.get("high")) for b in Ad1]); Ld = np.array([_f(b.get("low")) for b in Ad1])
    del Ad1
    for tf, fn in (("30m", "rr_union_b30m_daemon_m30.json"), ("15m", "rr_union_b15m_daemon_m30.json")):
        print("=" * 120, flush=True)
        print("DAEMON %s (TRUE OOS, 2026-06-20 ..)" % tf, flush=True)
        Ad = sorted(load_archive(tf, drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
        report(features(json.load(open(os.path.join(OUT, fn))), Ad), Td, Hd, Ld)
    print("\ndone in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
