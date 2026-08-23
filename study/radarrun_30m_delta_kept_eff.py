"""Radar Runner on 30m (bucket + clock), + bucket 1h for reference: STRONG delta IN FAVOUR x KEPT share x Eff/Res >= 1.0x
(user 2026-08-23). Honest union badge sets, non-overlap on 1m exits, 1-MINUTE first-touch resolution (clock_archive/1m),
0.5% net single TP and RR 1:1, per year. Sets show what each ingredient adds: ALL -> S -> S&kept -> S&eff>=1 ->
S&kept&eff>=1, + eff>=1 alone. Disjoint kept bands inside S&eff>=1 (RR 1:1). Then TRUE OOS for bucket 30m on the
daemon's own 30m buckets (union replay, daemon-1m resolution). python study/radarrun_30m_delta_kept_eff.py"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from app import effort_result as ER
from study.radarrun_honest_deltapct_tp import load_fires, delta_rank, fmt, ROOTS, PCT_STRONG
from study.radarrun_bkt1h_deltapct_confirm import eval_1m

COMBOS = [("bucket", "30m"), ("clock", "30m"), ("bucket", "1h")]
W, SLBUF30 = 2000, 0.003
_A = None


def feats(fires, A):
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
        r = ER.compute(A, b); eff = r["eff"] if r else None
        if r is not None and eff is not None and np.sign(r["delta"]) != s:
            eff = -eff
        out.append(dict(f=f, S=strong, kept=kept, eff=eff))
    return out


SETS = [("ALL", lambda r: True), ("S strong-in-favour", lambda r: r["S"]),
        ("S & kept>=70%", lambda r: r["S"] and r["kept"] >= 0.7), ("S & kept>=90%", lambda r: r["S"] and r["kept"] >= 0.9),
        ("S & eff>=1", lambda r: r["S"] and r["eff"] is not None and r["eff"] >= 1.0),
        ("S & kept>=70 & eff>=1", lambda r: r["S"] and r["kept"] >= 0.7 and r["eff"] is not None and r["eff"] >= 1.0),
        ("S & kept>=90 & eff>=1", lambda r: r["S"] and r["kept"] >= 0.9 and r["eff"] is not None and r["eff"] >= 1.0),
        ("eff>=1 alone", lambda r: r["eff"] is not None and r["eff"] >= 1.0)]
KB = [("<50%", 0.0, 0.5), ("50-70%", 0.5, 0.7), ("70-90%", 0.7, 0.9), (">=90%", 0.9, 9.9)]


def report(recs, T1, H1, L1):
    for name, keep in SETS:
        fs = [r["f"] for r in recs if keep(r)]
        for cname, kind, val in (("0.5% net", "fix", 0.0054), ("RR 1:1", "rr", 1.0)):
            d, unres = eval_1m(fs, kind, val, T1, H1, L1)
            print("    %-24s %-8s %s" % (name, cname, fmt(d)), flush=True)
    print("    disjoint kept bands inside S & eff>=1 (RR 1:1):", flush=True)
    for name, lo, hi in KB:
        fs = [r["f"] for r in recs if r["S"] and r["eff"] is not None and r["eff"] >= 1.0 and lo <= r["kept"] < hi]
        d, _ = eval_1m(fs, "rr", 1.0, T1, H1, L1)
        print("      kept %-7s %s" % (name, fmt(d)), flush=True)


def _init():
    global _A
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    _A = sorted(load_archive("30m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))


def _work(rng):
    k0, k1 = rng
    from app import config, radar_breakout_detect as RB
    from study.candle_bias_1h import _f
    seen = {}
    for k in range(k0, k1):
        lo = max(0, k - W); sub = _A[lo:k + 1]
        for g in RB.detect(sub, skip_last=False, sl_buf=SLBUF30, tp_frac=config.RR_TP_FRAC):
            b = lo + int(g["i"]); key = (b, g["side"])
            if key not in seen:
                seen[key] = (b, _f(_A[b].get("end_time")), g["side"], g["entry"], g["sl_trade"], k)
    return list(seen.values())


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    print("Radar Runner 30m: STRONG delta IN FAVOUR x KEPT x Eff/Res>=1 | 1-MINUTE first-touch | non-overlap | fees+slip\n", flush=True)
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    del A1
    for src, tf in COMBOS:
        t0 = time.time()
        A = sorted(load_archive(tf, root=ROOTS[src], drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
        recs = feats(load_fires(src, tf), A)
        print("=" * 130, flush=True)
        print("%s %s | badges %d | S %d | S&kept>=90 %d | S&eff>=1 %d | S&kept>=90&eff>=1 %d  (%.0fs)" % (src.upper(), tf, len(recs),
              sum(r["S"] for r in recs), sum(1 for r in recs if r["S"] and r["kept"] >= 0.9),
              sum(1 for r in recs if r["S"] and r["eff"] is not None and r["eff"] >= 1), sum(1 for r in recs if SETS[6][1](r)), time.time() - t0), flush=True)
        report(recs, T1, H1, L1)
    # ---- OOS: daemon 30m ----
    print("\n" + "=" * 130, flush=True)
    print("BUCKET 30m — TRUE OUT-OF-SAMPLE on the daemon's own 30m buckets (union replay, daemon-1m first-touch)", flush=True)
    import multiprocessing as mp
    Ad = sorted(load_archive("30m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(Ad); best = {}
    with mp.Pool(4, initializer=_init) as pool:
        for res in pool.imap(_work, [(a, min(a + 300, n)) for a in range(1, n, 300)]):
            for (b, et, s, e, sl, fk) in res:
                cur = best.get((b, s))
                if cur is None or fk < cur[5]:
                    best[(b, s)] = (b, et, s, e, sl, fk)
    byet = {}
    for rec in sorted(best.values(), key=lambda r: (r[5], r[0])):
        if rec[1] not in byet:
            byet[rec[1]] = rec
    fires_d = sorted([(b, et, s, e, sl) for (b, et, s, e, sl, fk) in byet.values()])
    print("  daemon 30m buckets %d (%s .. %s), union badges %d" % (n, datetime.fromtimestamp(_f(Ad[0].get("start_time")), tz=timezone.utc).date(),
          datetime.fromtimestamp(_f(Ad[-1].get("end_time")), tz=timezone.utc).date(), len(fires_d)), flush=True)
    recs_d = feats(fires_d, Ad)
    Ad1 = sorted(load_archive("1m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    Td = np.array([_f(b.get("start_time")) for b in Ad1]); Hd = np.array([_f(b.get("high")) for b in Ad1]); Ld = np.array([_f(b.get("low")) for b in Ad1])
    del Ad1
    report(recs_d, Td, Hd, Ld)


if __name__ == "__main__":
    main()
