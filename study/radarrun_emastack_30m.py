"""EMA STACK x RADAR RUNNER, 30m bucket — HONEST test (user 2026-08-27: LONG only when the fire candle
CLOSES above the 20 EMA AND 20>50 AND 50>100; SHORT only when close < 20 EMA AND 20<50 AND 50<100).
Canonical harness: cached union fire sets, 1-MINUTE first-touch, non-overlap taken(), fees 0.04% RT +
0.03% slip/leg, prop MC; recon per-year + DAEMON OOS decisive.

PRE-REGISTERED (frozen; no iteration): EMA(20/50/100) of 30m closes, adjust=False recursion seeded at the
first close — the values the shipped EMA lines show AT the fire bar's close (causal, uses data <= the bar).
RULE = LONG iff C>E20 and E20>E50 and E50>E100 / SHORT iff C<E20 and E20<E50 and E50<E100 (strict).
CELLS: ALL control / STACK (the rule) / AGAINST-STACK (fire against a full opposite stack) / STACK-LONG /
STACK-SHORT. EXITS: 0.2% net, 0.4% net, RR 1:0.5, RR 1:1.
python study/radarrun_emastack_30m.py"""
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
    E20, E50, E100 = ema(C, 20), ema(C, 50), ema(C, 100)
    recs = []
    for f in fires:
        b = int(f[0]); s = int(f[2])
        bull = C[b] > E20[b] and E20[b] > E50[b] and E50[b] > E100[b]
        bear = C[b] < E20[b] and E20[b] < E50[b] and E50[b] < E100[b]
        recs.append(dict(f=tuple(f), s=s,
                         stack=(s > 0 and bull) or (s < 0 and bear),
                         against=(s > 0 and bear) or (s < 0 and bull)))
    return recs


CELLS = [
    ("ALL", lambda r: True),
    ("STACK", lambda r: r["stack"]),
    ("AGAINST-STK", lambda r: r["against"]),
    ("STACK-LONG", lambda r: r["stack"] and r["s"] > 0),
    ("STACK-SHORT", lambda r: r["stack"] and r["s"] < 0),
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
    print("EMA STACK x RADAR RUNNER 30m BUCKET — canonical harness | long: C>E20>E50>E100 / short mirror | pre-registered\n", flush=True)
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
