"""EMA20 + BREAKOUT-CLOSE x RADAR RUNNER, 15m bucket — HONEST test (user 2026-08-27: on the 15m bucket,
LONG only if the signal bar closes above the 20 EMA AND above the previous bar's high; SHORT only if it
closes below the 20 EMA AND below the previous bar's low). Canonical harness: union fire sets (recon cached
rr_union_bucket_15m_s1.json; DAEMON union built here once with the same stride-1 W=2000 replay and cached as
rr_union_b15m_daemon_m30.json), 1-MINUTE first-touch, non-overlap taken(), fees 0.04% RT + 0.03% slip/leg,
prop MC; recon per-year + DAEMON OOS decisive.

PRE-REGISTERED (frozen; no iteration): EMA(20) of 15m closes, adjust=False seeded at the first close
(chart-identical, causal). EMA rule = long iff C>E20 / short iff C<E20; BC rule = long iff C > prev high /
short iff C < prev low (b=0 or degenerate prev -> BC false). CELLS: ALL control / EMA-ONLY / BC-ONLY /
EMA+BC (the requested rule) / E+B-LONG / E+B-SHORT. EXITS: 0.2% net, 0.4% net, RR 1:0.5, RR 1:1.
python study/radarrun_ema20bc_15m.py"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "study", "out")
TPS = [("0.2% net", "fix", 0.0024), ("0.4% net", "fix", 0.0044), ("RR 1:0.5", "rr", 0.5), ("RR 1:1", "rr", 1.0)]
W = 2000
_A = None


def _init():
    global _A
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    _A = sorted(load_archive("15m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))


def _work(rng):
    k0, k1 = rng
    from app import config, radar_breakout_detect as RB
    from study.candle_bias_1h import _f
    seen = {}
    for k in range(k0, k1):
        lo = max(0, k - W); sub = _A[lo:k + 1]
        for g in RB.detect(sub, skip_last=False, sl_buf=0.003, tp_frac=config.RR_TP_FRAC):
            b = lo + int(g["i"]); key = (b, g["side"])
            if key not in seen:
                seen[key] = (b, _f(_A[b].get("end_time")), g["side"], g["entry"], g["sl_trade"], k)
    return list(seen.values())


def daemon_union():
    """Stride-1 union-persist replay of the DAEMON 15m archive (same semantics as the cached recon set +
    the 30m daemon set: W=2000, sl_buf 0.003, default radar_mult 3.0, first-frame badge kept, one per end-time)."""
    path = os.path.join(OUT, "rr_union_b15m_daemon_m30.json")
    if os.path.exists(path):
        return json.load(open(path))
    import multiprocessing as mp
    from study.archive_loader import load_archive
    n = len(load_archive("15m", drop_degenerate=True)[1])
    chunks = [(a, min(a + 600, n)) for a in range(1, n, 600)]
    best = {}
    with mp.Pool(6, initializer=_init) as pool:
        for i, res in enumerate(pool.imap(_work, chunks), 1):
            for (b, et, s, e, sl, fk) in res:
                cur = best.get((b, s))
                if cur is None or fk < cur[5]:
                    best[(b, s)] = (b, et, s, e, sl, fk)
            print("    daemon 15m chunk %d/%d  badges %d" % (i, len(chunks), len(best)), flush=True)
    byet = {}
    for rec in sorted(best.values(), key=lambda r: (r[5], r[0])):
        if rec[1] not in byet:
            byet[rec[1]] = rec
    fires = sorted([(b, et, s, e, sl) for (b, et, s, e, sl, fk) in byet.values()])
    json.dump(fires, open(path, "w"))
    return fires


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
    H = np.array([_f(b.get("high")) for b in A])
    L = np.array([_f(b.get("low")) for b in A])
    E = ema20(C)
    recs = []
    for f in fires:
        b = int(f[0]); s = int(f[2])
        ema_ok = (s > 0 and C[b] > E[b]) or (s < 0 and C[b] < E[b])
        bc_ok = (b >= 1 and H[b - 1] > 0 and L[b - 1] > 0 and C[b] > 0
                 and ((s > 0 and C[b] > H[b - 1]) or (s < 0 and C[b] < L[b - 1])))
        recs.append(dict(f=tuple(f), s=s, ema=ema_ok, bc=bc_ok))
    return recs


CELLS = [
    ("ALL", lambda r: True),
    ("EMA-ONLY", lambda r: r["ema"]),
    ("BC-ONLY", lambda r: r["bc"]),
    ("EMA+BC", lambda r: r["ema"] and r["bc"]),
    ("E+B-LONG", lambda r: r["ema"] and r["bc"] and r["s"] > 0),
    ("E+B-SHORT", lambda r: r["ema"] and r["bc"] and r["s"] < 0),
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
    print("EMA20 + BREAKOUT-CLOSE x RADAR RUNNER 15m BUCKET — canonical harness | long: C>E20 AND C>prev high / short mirror | pre-registered\n", flush=True)
    t0 = time.time()
    frd = daemon_union()                                      # build/cache the daemon 15m union FIRST (slow part)
    print("  daemon 15m union: %d fires  (%.0fs)" % (len(frd), time.time() - t0), flush=True)
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
    report(features(frd, Ad), Td, Hd, Ld)
    print("\ndone in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
