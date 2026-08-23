"""TRUE OUT-OF-SAMPLE check of the bucket-1h DELTA>=P80 x RR candidate on the DAEMON's own 1h buckets (live since
2026-06-20 — never part of the recon archive the candidate was found on). Same honest machinery: union-persist replay
(incremental, W=2000, stride 1), the pane-identical delta rank filter, single RR TPs + the fixed nets, resolution at the
daemon's 1m buckets (first-touch, conservative ties), non-overlap. Reports the candidate rows + the ALL control.
Short window (~2 months) -> small n; the point is SIGN + magnitude vs in-sample (+0.10%/trade), not prop stats.
python study/radarrun_bkt1h_deltapct_oos_daemon.py"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.radarrun_honest_deltapct_tp import delta_rank, fmt, FEE, SLIP, PCT_STRONG
from study.radarrun_bkt1h_deltapct_confirm import eval_1m

TF, W, SLBUF = "1h", 2000, 0.002          # terminal uses sl_buf 0.002 on 1h
CONFIGS = [("RR 1:1", "rr", 1.0), ("RR 1:1.2", "rr", 1.2), ("RR 1:1.5", "rr", 1.5), ("RR 1:2", "rr", 2.0),
           ("0.4% net", "fix", 0.0044), ("0.5% net", "fix", 0.0054)]
_A = None


def _init():
    global _A
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    _A = sorted(load_archive(TF, drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))


def _work(rng):
    k0, k1 = rng
    from app import config, radar_breakout_detect as RB
    from study.candle_bias_1h import _f
    seen = {}
    for k in range(k0, k1):
        lo = max(0, k - W); sub = _A[lo:k + 1]
        for g in RB.detect(sub, skip_last=False, sl_buf=SLBUF, tp_frac=config.RR_TP_FRAC):
            b = lo + int(g["i"]); key = (b, g["side"])
            if key not in seen:
                seen[key] = (b, _f(_A[b].get("end_time")), g["side"], g["entry"], g["sl_trade"], k)
    return list(seen.values())


def main():
    import multiprocessing as mp
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    A = sorted(load_archive(TF, drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    if n < 200:
        print("daemon 1h archive too small: %d buckets" % n); return
    print("DAEMON 1h buckets: %d  (%s .. %s)  — OUT-OF-SAMPLE vs the recon-based candidate" % (
        n, datetime.fromtimestamp(_f(A[0].get("start_time")), tz=timezone.utc).date(),
        datetime.fromtimestamp(_f(A[-1].get("end_time")), tz=timezone.utc).date()), flush=True)
    t0 = time.time(); best = {}
    chunks = [(a, min(a + 300, n)) for a in range(1, n, 300)]
    with mp.Pool(4, initializer=_init) as pool:
        for res in pool.imap(_work, chunks):
            for (b, et, s, e, sl, fk) in res:
                cur = best.get((b, s))
                if cur is None or fk < cur[5]:
                    best[(b, s)] = (b, et, s, e, sl, fk)
    byet = {}
    for rec in sorted(best.values(), key=lambda r: (r[5], r[0])):
        if rec[1] not in byet:
            byet[rec[1]] = rec
    fires = sorted([(b, et, s, e, sl) for (b, et, s, e, sl, fk) in byet.values()])
    print("union badges: %d  (%.0fs)" % (len(fires), time.time() - t0), flush=True)
    D = np.abs(np.array([_f(b.get("buy_vol")) - _f(b.get("sell_vol")) for b in A]))
    ranks = np.array([delta_rank(D, f[0]) for f in fires])
    lit = [f for f, r in zip(fires, ranks) if r >= PCT_STRONG]
    print("DELTA>=P80 filtered: %d (%.0f%%)\n" % (len(lit), 100 * len(lit) / max(1, len(fires))), flush=True)
    A1 = sorted(load_archive("1m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    print("daemon 1m buckets for resolution: %d  (%s .. %s)" % (len(A1), datetime.fromtimestamp(T1[0], tz=timezone.utc).date(),
          datetime.fromtimestamp(T1[-1], tz=timezone.utc).date()), flush=True)
    del A1
    print("\n== OOS results (daemon-1m first-touch, conservative ties, non-overlap) ==", flush=True)
    for name, kind, val in CONFIGS:
        d, unres = eval_1m(lit, kind, val, T1, H1, L1)
        print("  %-9s DELTA>=P80 : %s  (unresolved %d)" % (name, fmt(d), unres), flush=True)
    for name, kind, val in (("RR 1:1", "rr", 1.0), ("RR 1:1.2", "rr", 1.2)):
        d, unres = eval_1m(fires, kind, val, T1, H1, L1)
        print("  %-9s ALL control: %s  (unresolved %d)" % (name, fmt(d), unres), flush=True)


if __name__ == "__main__":
    main()
