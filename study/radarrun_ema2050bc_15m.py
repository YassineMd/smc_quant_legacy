"""EMA20 + EMA50-ALIGN + BREAKOUT-CLOSE x RADAR RUNNER, 15m bucket — HONEST test (user 2026-08-27: LONG only
if the signal bar closes above the 20 EMA AND above the previous bar's high AND EMA20 > EMA50; SHORT mirror).
Canonical harness: union fire sets (recon cached rr_union_bucket_15m_s1.json; daemon cached
rr_union_b15m_daemon_m30.json from radarrun_ema20bc_15m), 1-MINUTE first-touch, non-overlap taken(), fees
0.04% RT + 0.03% slip/leg, prop MC; recon per-year + DAEMON OOS decisive.

PRE-REGISTERED (frozen; no iteration): EMA(20/50) of 15m closes, adjust=False seeded at the first close
(chart-identical, causal). FULL rule = long iff C>E20 AND C>prev high AND E20>E50 / short mirror (strict).
CELLS: ALL control / EMA+BC (the previous two-condition rule, reference) / STK-ONLY (E20 vs E50 alignment
alone) / FULL (the requested rule) / FULL-LONG / FULL-SHORT. EXITS: 0.2% net, 0.4% net, RR 1:0.5, RR 1:1.
python study/radarrun_ema2050bc_15m.py"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "study", "out")
TPS = [("0.2% net", "fix", 0.0024), ("0.4% net", "fix", 0.0044), ("RR 1:0.5", "rr", 0.5), ("RR 1:1", "rr", 1.0)]


def ema(closes, period):
    a = 2.0 / (period + 1.0)
    y = np.empty(len(closes))
    y[0] = closes[0]
    for i in range(1, len(closes)):
        c = closes[i] if closes[i] > 0 else y[i - 1]
        y[i] = a * c + (1.0 - a) * y[i - 1]
    return y


def features(fires, A):
    from study.candle_bias_1h import _f
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    H = np.array([_f(b.get("high")) for b in A])
    L = np.array([_f(b.get("low")) for b in A])
    E20, E50 = ema(C, 20), ema(C, 50)
    recs = []
    for f in fires:
        b = int(f[0]); s = int(f[2])
        ema_ok = (s > 0 and C[b] > E20[b]) or (s < 0 and C[b] < E20[b])
        bc_ok = (b >= 1 and H[b - 1] > 0 and L[b - 1] > 0 and C[b] > 0
                 and ((s > 0 and C[b] > H[b - 1]) or (s < 0 and C[b] < L[b - 1])))
        stk_ok = (s > 0 and E20[b] > E50[b]) or (s < 0 and E20[b] < E50[b])
        recs.append(dict(f=tuple(f), s=s, ema=ema_ok, bc=bc_ok, stk=stk_ok))
    return recs


CELLS = [
    ("ALL", lambda r: True),
    ("EMA+BC", lambda r: r["ema"] and r["bc"]),
    ("STK-ONLY", lambda r: r["stk"]),
    ("FULL", lambda r: r["ema"] and r["bc"] and r["stk"]),
    ("FULL-LONG", lambda r: r["ema"] and r["bc"] and r["stk"] and r["s"] > 0),
    ("FULL-SHORT", lambda r: r["ema"] and r["bc"] and r["stk"] and r["s"] < 0),
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
    print("EMA20 + EMA50-ALIGN + BREAKOUT-CLOSE x RADAR RUNNER 15m BUCKET — canonical harness | long: C>E20, C>prev high, E20>E50 / short mirror | pre-registered\n", flush=True)
    t0 = time.time()
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    del A1
    print("=" * 120, flush=True)
    print("RECON 2025-01 .. 2026-06 (per-year split in rows)", flush=True)
    A = sorted(load_archive("15m", root="study/recon_archive", drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    report(features(load_fires("bucket", "15m"), A), T1, H1, L1)
    del A, T1, H1, L1
    print("=" * 120, flush=True)
    print("DAEMON 15m (TRUE OOS, 2026-06-20 ..)", flush=True)
    Ad1 = sorted(load_archive("1m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    Td = np.array([_f(b.get("start_time")) for b in Ad1]); Hd = np.array([_f(b.get("high")) for b in Ad1]); Ld = np.array([_f(b.get("low")) for b in Ad1])
    del Ad1
    Ad = sorted(load_archive("15m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    frd = json.load(open(os.path.join(OUT, "rr_union_b15m_daemon_m30.json")))
    report(features(frd, Ad), Td, Hd, Ld)
    print("\ndone in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
