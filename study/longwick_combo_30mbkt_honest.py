"""LONG WICK COMBO — HONEST test, 30m BUCKET (user 2026-08-25). Same pre-registration family as the Long Wick
runs: signals = the SHIPPED app/longwick_detect.detect_combo (2-bar failed-push pair, NO wall binding — pure
causal bar geometry, so no per-bar wall replay is needed); entry = the wick bar's CLOSE; SL 0.1% beyond the
wick bar's extreme (high for shorts / low for longs); TP 0.2/0.25/0.3/0.4/0.5% NET + RR 1:1; 1-MINUTE
first-touch, SL-first ties, fees 0.04% RT + 0.03% slip/leg, NON-OVERLAP taken(), prop MC (canonical eval_1m);
recon per-year + DAEMON OOS; ALL/LONG/SHORT. NO iteration. python study/longwick_combo_30mbkt_honest.py"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np

SLB = 0.001
TPS = [("0.2% net", "fix", 0.0024), ("0.25% net", "fix", 0.0029), ("0.3% net", "fix", 0.0034),
       ("0.4% net", "fix", 0.0044), ("0.5% net", "fix", 0.0054), ("RR 1:1", "rr", 1.0)]


def signals(daemon):
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    from app import longwick_detect as LW
    if daemon:
        A = sorted(load_archive("30m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    else:
        A = sorted(load_archive("30m", root="study/recon_archive", drop_degenerate=False)[1],
                   key=lambda b: _f(b.get("start_time", 0)))
    marks = LW.detect_combo(A, skip_last=False)
    fires = []
    for e in marks:
        i = int(e["i"]); side = int(e["side"])
        b = A[i]
        e_ = _f(b.get("close", b.get("close_price")))
        sl = _f(b.get("high")) * (1 + SLB) if side < 0 else _f(b.get("low")) * (1 - SLB)
        fires.append((i, _f(b.get("end_time")), side, e_, sl))
    print("  signals: %d / %d bars" % (len(fires), len(A)), flush=True)
    return sorted(fires)


def report(fires, T1, H1, L1):
    from study.radarrun_honest_deltapct_tp import fmt
    from study.radarrun_bkt1h_deltapct_confirm import eval_1m
    for label, keep in (("ALL", lambda s: True), ("LONG", lambda s: s > 0), ("SHORT", lambda s: s < 0)):
        fs = [f for f in fires if keep(f[2])]
        for cname, kind, val in TPS:
            d, _ = eval_1m(fs, kind, val, T1, H1, L1)
            print("  %-5s %-9s %s" % (label, cname, fmt(d)), flush=True)


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    print("LONG WICK COMBO — 30m BUCKET HONEST TEST | shipped detect_combo (wall-free, causal) | SL 0.1% beyond"
          " extreme | 1m first-touch | non-overlap | fees+slip\n", flush=True)
    t0 = time.time()
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    del A1
    print("=" * 120, flush=True)
    print("RECON 2025-01 .. 2026-06 (per-year split in rows)", flush=True)
    report(signals(daemon=False), T1, H1, L1)
    del T1, H1, L1
    print("=" * 120, flush=True)
    print("DAEMON 30m (TRUE OOS, 2026-06-20 ..)", flush=True)
    Ad1 = sorted(load_archive("1m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    Td = np.array([_f(b.get("start_time")) for b in Ad1]); Hd = np.array([_f(b.get("high")) for b in Ad1]); Ld = np.array([_f(b.get("low")) for b in Ad1])
    del Ad1
    report(signals(daemon=True), Td, Hd, Ld)
    print("\ndone in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
