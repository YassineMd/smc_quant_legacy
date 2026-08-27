"""EMA20 x RADAR RUNNER, 30m bucket — HONEST test (user 2026-08-26: long only when the fire candle CLOSES
above the 20 EMA, short only below). Canonical harness: cached union fire sets, 1-MINUTE first-touch,
non-overlap taken(), fees 0.04% RT + 0.03% slip/leg, prop MC; recon per-year + DAEMON OOS decisive.

PRE-REGISTERED (frozen; no iteration): EMA(20) of 30m closes, adjust=False recursion seeded at the first
close — the value the shipped 20-EMA line shows AT the fire bar's close (causal, uses data <= the bar).
RULE = LONG iff close > EMA / SHORT iff close < EMA (equality excluded). CELLS: ALL control / WITH-EMA (the
rule) / AGAINST-EMA / WITH-LONG / WITH-SHORT. EXITS: 0.2% net, 0.4% net, RR 1:0.5, RR 1:1.
python study/radarrun_ema20_30m.py"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "study", "out")
TPS = [("0.2% net", "fix", 0.0024), ("0.4% net", "fix", 0.0044), ("RR 1:0.5", "rr", 0.5), ("RR 1:1", "rr", 1.0)]


def ema20(closes):
    a = 2.0 / 21.0
    y = np.empty(len(closes))
    y[0] = closes[0]
    for i in range(1, len(closes)):
        c = closes[i] if closes[i] > 0 else y[i - 1]
        y[i] = a * c + (1.0 - a) * y[i - 1]
    return y


def features(fires, A):
    from study.candle_bias_1h import _f
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    E = ema20(C)
    recs = []
    for f in fires:
        b = int(f[0]); s = int(f[2])
        above = C[b] > E[b]; below = C[b] < E[b]
        recs.append(dict(f=tuple(f), s=s,
                         with_ema=(s > 0 and above) or (s < 0 and below),
                         against=(s > 0 and below) or (s < 0 and above)))
    return recs


CELLS = [
    ("ALL", lambda r: True),
    ("WITH-EMA", lambda r: r["with_ema"]),
    ("AGAINST-EMA", lambda r: r["against"]),
    ("WITH-LONG", lambda r: r["with_ema"] and r["s"] > 0),
    ("WITH-SHORT", lambda r: r["with_ema"] and r["s"] < 0),
]


def report(recs, T1, H1, L1):
    from study.radarrun_honest_deltapct_tp import fmt
    from study.radarrun_bkt1h_deltapct_confirm import eval_1m
    for name, keep in CELLS:
        fs = [r["f"] for r in recs if keep(r)]
        for cname, kind, val in TPS:
            d, _ = eval_1m(fs, kind, val, T1, H1, L1)
            print("  %-12s %-9s %s" % (name, cname, fmt(d)), flush=True)


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    from study.radarrun_honest_deltapct_tp import load_fires
    print("EMA20 x RADAR RUNNER 30m BUCKET — canonical harness | long>EMA / short<EMA at the fire close | pre-registered\n", flush=True)
    t0 = time.time()
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    del A1
    print("=" * 120, flush=True)
    print("RECON 2025-01 .. 2026-06 (per-year split in rows)", flush=True)
    A = sorted(load_archive("30m", root="study/recon_archive", drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    report(features(load_fires("bucket", "30m"), A), T1, H1, L1)
    del A, T1, H1, L1
    print("=" * 120, flush=True)
    print("DAEMON 30m (TRUE OOS, 2026-06-20 ..)", flush=True)
    Ad1 = sorted(load_archive("1m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    Td = np.array([_f(b.get("start_time")) for b in Ad1]); Hd = np.array([_f(b.get("high")) for b in Ad1]); Ld = np.array([_f(b.get("low")) for b in Ad1])
    del Ad1
    Ad = sorted(load_archive("30m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    frd = json.load(open(os.path.join(OUT, "rr_union_b30m_daemon_m30.json")))
    report(features(frd, Ad), Td, Hd, Ld)
    print("\ndone in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
