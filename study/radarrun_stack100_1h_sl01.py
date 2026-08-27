"""STACK-FLIP BIAS + EMA100-POSITION x RADAR RUNNER, 1h bucket, CANDLE SL 0.1% — HONEST test (user
2026-08-27: on the 1h bucket, SL 0.1% beyond the signal candle's extreme; LONG only if the LAST stack flip
is GREEN (20>50>100 completed most recently) AND close > EMA100 AND at least 50% of the candle's range is
above the EMA100; SHORT mirror with a RED flip and below). Canonical harness: union fire sets (recon cached
rr_union_bucket_1h_s1.json; DAEMON union built here once — stride-1 W=2000 replay, sl_buf 0.002 = the 1h
convention of the cached recon set — cached as rr_union_b1h_daemon_m30.json), 1-MINUTE first-touch,
non-overlap taken(), fees 0.04% RT + 0.03% slip/leg, prop MC; recon per-year + DAEMON OOS decisive.

PRE-REGISTERED (frozen; no iteration): EMAs adjust=False seeded at the first close. STACK FLIPS exactly as
the shipped ema_stack sub-widget: bull = E20>E50>E100 / bear = E100>E50>E20 on closed bars, transitions
only, first flips from bar 100; BIAS at fire b = colour of the most recent flip <= b (no flip -> excluded
from FULL, falls in REST). EMA100 POSITION: long iff C[b] > E100[b] AND (H[b]+L[b])/2 > E100[b] ("50% of
the candle above" = midpoint above); short mirror. FULL = bias-aligned AND position condition. SL REBUILT =
low x (1-0.001) for LONG / high x (1+0.001) for SHORT; entry = the badge's recorded entry.
CELLS: ALL (custom-SL control) / FULL / REST / FULL-LONG / FULL-SHORT.
EXITS: 0.2% net, 0.4% net, 0.5% net, RR 1:1, RR 1:2.
python study/radarrun_stack100_1h_sl01.py"""
import os, sys, json, time, bisect
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np

from study.radarrun_hld2050_30m import ema

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "study", "out")
TPS = [("0.2% net", "fix", 0.0024), ("0.4% net", "fix", 0.0044), ("0.5% net", "fix", 0.0054),
       ("RR 1:1", "rr", 1.0), ("RR 1:2", "rr", 2.0)]
SLB = 0.001
W = 2000
_A = None


def _init():
    global _A
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    _A = sorted(load_archive("1h", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))


def _work(rng):
    k0, k1 = rng
    from app import config, radar_breakout_detect as RB
    from study.candle_bias_1h import _f
    seen = {}
    for k in range(k0, k1):
        lo = max(0, k - W); sub = _A[lo:k + 1]
        for g in RB.detect(sub, skip_last=False, sl_buf=0.002, tp_frac=config.RR_TP_FRAC):
            b = lo + int(g["i"]); key = (b, g["side"])
            if key not in seen:
                seen[key] = (b, _f(_A[b].get("end_time")), g["side"], g["entry"], g["sl_trade"], k)
    return list(seen.values())


def daemon_union():
    path = os.path.join(OUT, "rr_union_b1h_daemon_m30.json")
    if os.path.exists(path):
        return json.load(open(path))
    import multiprocessing as mp
    from study.archive_loader import load_archive
    n = len(load_archive("1h", drop_degenerate=True)[1])
    chunks = [(a, min(a + 300, n)) for a in range(1, n, 300)]
    best = {}
    with mp.Pool(6, initializer=_init) as pool:
        for i, res in enumerate(pool.imap(_work, chunks), 1):
            for (b, et, s, e, sl, fk) in res:
                cur = best.get((b, s))
                if cur is None or fk < cur[5]:
                    best[(b, s)] = (b, et, s, e, sl, fk)
            print("    daemon 1h chunk %d/%d  badges %d" % (i, len(chunks), len(best)), flush=True)
    byet = {}
    for rec in sorted(best.values(), key=lambda r: (r[5], r[0])):
        if rec[1] not in byet:
            byet[rec[1]] = rec
    fires = sorted([(b, et, s, e, sl) for (b, et, s, e, sl, fk) in byet.values()])
    json.dump(fires, open(path, "w"))
    return fires


def features(fires, A):
    from study.candle_bias_1h import _f
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    H = np.array([_f(b.get("high")) for b in A])
    L = np.array([_f(b.get("low")) for b in A])
    E20, E50, E100 = ema(C, 20), ema(C, 50), ema(C, 100)
    bull = (E20 > E50) & (E50 > E100)
    bear = (E100 > E50) & (E50 > E20)
    flips = []                                                # (bar, colour) transitions, from bar 100 on
    for i in range(100, len(A)):
        if bull[i] and not bull[i - 1]:
            flips.append((i, "g"))
        if bear[i] and not bear[i - 1]:
            flips.append((i, "r"))
    fbars = [f[0] for f in flips]
    recs = []
    nnb = 0
    for f in fires:
        b = int(f[0]); s = int(f[2]); e = float(f[3])
        sl = L[b] * (1.0 - SLB) if s > 0 else H[b] * (1.0 + SLB)
        if e <= 0 or sl <= 0 or (s > 0 and sl >= e) or (s < 0 and sl <= e):
            continue
        j = bisect.bisect_right(fbars, b) - 1
        bias = flips[j][1] if j >= 0 else None
        if bias is None:
            nnb += 1
        mid = 0.5 * (H[b] + L[b])
        pos_ok = (s > 0 and C[b] > E100[b] and mid > E100[b]) or (s < 0 and C[b] < E100[b] and mid < E100[b])
        w_ok = pos_ok and ((s > 0 and bias == "g") or (s < 0 and bias == "r"))
        recs.append(dict(f=(b, float(f[1]), s, e, float(sl)), s=s, w=w_ok))
    if nnb:
        print("  (no stack flip yet at %d fires -> REST)" % nnb, flush=True)
    return recs


CELLS = [
    ("ALL", lambda r: True),
    ("FULL", lambda r: r["w"]),
    ("REST", lambda r: not r["w"]),
    ("FULL-LONG", lambda r: r["w"] and r["s"] > 0),
    ("FULL-SHORT", lambda r: r["w"] and r["s"] < 0),
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
    print("STACK-FLIP BIAS + EMA100-POSITION x RADAR RUNNER 1h — SL 0.1%% beyond the candle | 5 exits | pre-registered\n", flush=True)
    t0 = time.time()
    frd = daemon_union()
    print("  daemon 1h union: %d fires  (%.0fs)" % (len(frd), time.time() - t0), flush=True)
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    del A1
    print("=" * 120, flush=True)
    print("RECON 1h BUCKET 2025-01 .. 2026-06 (per-year split in rows)", flush=True)
    A = sorted(load_archive("1h", root="study/recon_archive", drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    report(features(load_fires("bucket", "1h"), A), T1, H1, L1)
    del A, T1, H1, L1
    Ad1 = sorted(load_archive("1m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    Td = np.array([_f(b.get("start_time")) for b in Ad1]); Hd = np.array([_f(b.get("high")) for b in Ad1]); Ld = np.array([_f(b.get("low")) for b in Ad1])
    del Ad1
    print("=" * 120, flush=True)
    print("DAEMON 1h (TRUE OOS, 2026-06-20 ..)", flush=True)
    Ad = sorted(load_archive("1h", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    report(features(frd, Ad), Td, Hd, Ld)
    print("\ndone in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
