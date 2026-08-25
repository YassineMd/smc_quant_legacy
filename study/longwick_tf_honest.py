"""LONG WICK @ WALL — HONEST test on 15m + 1h BUCKET (user 2026-08-25; identical pre-registration to the 30m
run in longwick_30mbkt_honest.py): shipped app/longwick_detect per bar close on TRAILING W=2000 walls
(repaint-proof), entry = signal close, SL 0.1% beyond the candle extreme, TP 0.2/0.25/0.3/0.4/0.5% NET +
RR 1:1, 1-MINUTE first-touch, non-overlap taken(), fees 0.04% RT + 0.03% slip/leg, prop MC; recon per-year +
DAEMON OOS; ALL/LONG/SHORT. NO iteration. python study/longwick_tf_honest.py"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np

W = 2000
SLB = 0.001
TPS = [("0.2% net", "fix", 0.0024), ("0.25% net", "fix", 0.0029), ("0.3% net", "fix", 0.0034),
       ("0.4% net", "fix", 0.0044), ("0.5% net", "fix", 0.0054), ("RR 1:1", "rr", 1.0)]
_A = None
_TF = "15m"


def _init(daemon, tf):
    global _A, _TF
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    _TF = tf
    if daemon:
        _A = sorted(load_archive(tf, drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    else:
        _A = sorted(load_archive(tf, root="study/recon_archive", drop_degenerate=False)[1],
                    key=lambda b: _f(b.get("start_time", 0)))


def _geom_candidates(A):
    from study.candle_bias_1h import _f
    out = []
    for i, b in enumerate(A):
        o = _f(b.get("open", b.get("open_price"))); c = _f(b.get("close", b.get("close_price")))
        h = _f(b.get("high")); l = _f(b.get("low"))
        if o <= 0 or c <= 0 or h <= l:
            continue
        body = abs(c - o); uw = h - max(o, c); lw = min(o, c) - l
        if body <= 0:
            continue
        if c < o and uw > body and uw > lw and lw < body:
            out.append((i, -1))
        elif c > o and lw > body and lw > uw and uw < body:
            out.append((i, 1))
    return out


def _work(chunk):
    from app import absorption_level_detect as AL
    from app import longwick_detect as LW
    from study.candle_bias_1h import _f
    out = []
    for (i, side) in chunk:
        lo = max(0, i - W)
        sub = _A[lo:i + 1]
        try:
            walls = AL.detect(sub, skip_last=False)
            marks = LW.detect(sub, walls, skip_last=False)
        except Exception:
            continue
        if any(int(e["i"]) == len(sub) - 1 and int(e["side"]) == side for e in marks):
            b = _A[i]
            e_ = _f(b.get("close", b.get("close_price")))
            sl = _f(b.get("high")) * (1 + SLB) if side < 0 else _f(b.get("low")) * (1 - SLB)
            out.append((i, _f(b.get("end_time")), side, e_, sl))
    return out


def signals(daemon, tf):
    import multiprocessing as mp
    _init(daemon, tf)
    cands = _geom_candidates(_A)
    print("  geometry candidates: %d / %d bars" % (len(cands), len(_A)), flush=True)
    chunks = [cands[k:k + 200] for k in range(0, len(cands), 200)]
    fires = []
    with mp.Pool(6, initializer=_init, initargs=(daemon, tf)) as pool:
        for j, res in enumerate(pool.imap(_work, chunks), 1):
            fires += res
            if j % 10 == 0 or j == len(chunks):
                print("  wall-confirm chunk %d/%d fires %d" % (j, len(chunks), len(fires)), flush=True)
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
    print("LONG WICK @ WALL — 15m + 1h BUCKET HONEST TEST | same pre-registration as the 30m run\n", flush=True)
    t0 = time.time()
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    del A1
    Ad1 = sorted(load_archive("1m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    Td = np.array([_f(b.get("start_time")) for b in Ad1]); Hd = np.array([_f(b.get("high")) for b in Ad1]); Ld = np.array([_f(b.get("low")) for b in Ad1])
    del Ad1
    for tf in ("15m", "1h"):
        print("=" * 120, flush=True)
        print("BUCKET %s — RECON 2025-01 .. 2026-06 (per-year split in rows)" % tf, flush=True)
        fr = signals(daemon=False, tf=tf)
        report(fr, T1, H1, L1)
        print("-" * 120, flush=True)
        print("BUCKET %s — DAEMON (TRUE OOS, 2026-06-20 ..)" % tf, flush=True)
        frd = signals(daemon=True, tf=tf)
        report(frd, Td, Hd, Ld)
    print("\ndone in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
