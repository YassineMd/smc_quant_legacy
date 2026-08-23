"""CONFIRM the one screen survivor of the strong-delta-in-favour x KEPT study: BUCKET 1h, S & kept>=90%
(screen: tp.5 +0.083/+0.202%, rr1 +0.166/+0.326%, win 79/84%, n 143/80). Two gates:
  A. 1-MINUTE first-touch (clock_archive/1m) on the recon set, for 0.5% net and RR 1:1, with S and ALL controls.
  B. TRUE OOS on the daemon's own 1h buckets (2026-06-21 ->), same filter, resolved on daemon 1m — S & kept>=90,
     S & kept>=70, S, ALL. (The parent cell S = DELTA>=P80 already FAILED this OOS at -0.221%.)
python study/radarrun_bkt1h_kept_confirm.py"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.radarrun_honest_deltapct_tp import load_fires, delta_rank, fmt, ROOTS, PCT_STRONG
from study.radarrun_bkt1h_deltapct_confirm import eval_1m
import study.radarrun_bkt1h_deltapct_oos_daemon as OOS


def with_kept(fires, A):
    from study.candle_bias_1h import _f
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A]); C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    Dl = np.array([_f(b.get("buy_vol")) - _f(b.get("sell_vol")) for b in A]); D = np.abs(Dl)
    out = []
    for f in fires:
        b, t, s, e, sl = f
        if b < 51:
            continue
        exc = (Hi[b] - O[b]) if s > 0 else (O[b] - Lo[b])
        kept = (((C[b] - O[b]) if s > 0 else (O[b] - C[b])) / exc) if exc > 0 else float("nan")
        strong = delta_rank(D, b) >= PCT_STRONG and ((Dl[b] > 0) == (s > 0))
        out.append((f, strong, kept))
    return out


def report(tag, recs, T1, H1, L1):
    sets = [("ALL", [r[0] for r in recs]), ("S strong-in-favour", [r[0] for r in recs if r[1]]),
            ("S & kept>=70%", [r[0] for r in recs if r[1] and r[2] >= 0.7]), ("S & kept>=90%", [r[0] for r in recs if r[1] and r[2] >= 0.9]),
            ("kept>=90% (no delta)", [r[0] for r in recs if r[2] >= 0.9])]
    for name, fs in sets:
        for cname, kind, val in (("0.5% net", "fix", 0.0054), ("RR 1:1", "rr", 1.0)):
            d, unres = eval_1m(fs, kind, val, T1, H1, L1)
            print("  %-22s %-8s %s  (unresolved %d)" % (name, cname, fmt(d), unres), flush=True)


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    # ---- A. 1m on the recon set ----
    print("== A. BUCKET 1h (recon), 1-MINUTE first-touch, non-overlap on 1m exits ==", flush=True)
    A = sorted(load_archive("1h", root=ROOTS["bucket"], drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    recs = with_kept(load_fires("bucket", "1h"), A)
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    del A1
    report("recon", recs, T1, H1, L1)
    # ---- B. daemon OOS ----
    print("\n== B. DAEMON 1h OUT-OF-SAMPLE (union replay, daemon-1m first-touch) ==", flush=True)
    import multiprocessing as mp
    Ad = sorted(load_archive("1h", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(Ad); best = {}
    chunks = [(a, min(a + 300, n)) for a in range(1, n, 300)]
    with mp.Pool(4, initializer=OOS._init) as pool:
        for res in pool.imap(OOS._work, chunks):
            for (b, et, s, e, sl, fk) in res:
                cur = best.get((b, s))
                if cur is None or fk < cur[5]:
                    best[(b, s)] = (b, et, s, e, sl, fk)
    byet = {}
    for rec in sorted(best.values(), key=lambda r: (r[5], r[0])):
        if rec[1] not in byet:
            byet[rec[1]] = rec
    fires_d = sorted([(b, et, s, e, sl) for (b, et, s, e, sl, fk) in byet.values()])
    print("daemon 1h buckets %d (%s .. %s), union badges %d" % (n, datetime.fromtimestamp(_f(Ad[0].get("start_time")), tz=timezone.utc).date(),
          datetime.fromtimestamp(_f(Ad[-1].get("end_time")), tz=timezone.utc).date(), len(fires_d)), flush=True)
    recs_d = with_kept(fires_d, Ad)
    Ad1 = sorted(load_archive("1m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    Td = np.array([_f(b.get("start_time")) for b in Ad1]); Hd = np.array([_f(b.get("high")) for b in Ad1]); Ld = np.array([_f(b.get("low")) for b in Ad1])
    del Ad1
    report("daemon", recs_d, Td, Hd, Ld)


if __name__ == "__main__":
    main()
