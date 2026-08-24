"""Radar Runner x WALL-REGIME BIAS (user 2026-08-24). A-priori definition, no sweeps: at each badge's breakout bar count
the ACTIVE Order-Flow Walls (absorption_level_detect marks, active iff i0 <= b <= i1) per side — more SUPPORT walls ->
LONG-bias regime, more RESISTANCE walls -> SHORT-bias, equal -> NEUTRAL. FILTER = badge side agrees with the bias
(ALIGNED); AGAINST + NEUTRAL reported as controls. Union badge sets, 6 combos (no 5m/1m), 1-MINUTE first-touch,
non-overlap per set, TPs: 0.2% / 0.4% / 0.5% NET + RR 1:1, per year; then TRUE OOS on daemon 30m.
Claim holds only if ALIGNED beats AGAINST within eras INCLUDING the daemon leg. python study/radarrun_wall_regime_bias.py"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.radarrun_honest_deltapct_tp import load_fires, fmt, ROOTS
from study.radarrun_bkt1h_deltapct_confirm import eval_1m
import study.radarrun_30m_delta_kept_eff as D30

COMBOS = [("bucket", "15m"), ("clock", "15m"), ("bucket", "30m"), ("clock", "30m"), ("bucket", "1h"), ("clock", "1h")]
TPS = [("0.2% net", "fix", 0.0024), ("0.4% net", "fix", 0.0044), ("0.5% net", "fix", 0.0054), ("RR 1:1", "rr", 1.0)]
CHUNK, OVER = 6000, 1000


def wall_counts(A):
    """(nS[bar], nR[bar]) active-wall counts per bar — chunked AL.detect (dedup by (i0,side,price)), diff-array prefix sum."""
    from app import absorption_level_detect as AL
    from study.candle_bias_1h import _f
    n = len(A); seen = set(); dS = np.zeros(n + 1); dR = np.zeros(n + 1)
    c0 = 0
    while c0 < n:
        c1 = min(n, c0 + CHUNK)
        for w in AL.detect(A[c0:c1], skip_last=False):
            side = w.get("side"); i0 = int(w.get("i0", 0)) + c0; i1 = int(w.get("i1", 0)) + c0
            key = (i0, side, round(_f(w.get("price")), 4))
            if side not in ("S", "R") or key in seen or i1 < i0:
                continue
            seen.add(key)
            (dS if side == "S" else dR)[i0] += 1
            (dS if side == "S" else dR)[min(i1 + 1, n)] -= 1
        if c1 >= n:
            break
        c0 += CHUNK - OVER
    return np.cumsum(dS[:n]), np.cumsum(dR[:n])


def tag(fires, nS, nR):
    out = []
    for f in fires:
        b = f[0]; s = f[2]
        bias = 1 if nS[b] > nR[b] else (-1 if nR[b] > nS[b] else 0)
        out.append(dict(f=f, al=(bias != 0 and bias == (1 if s > 0 else -1)), ag=(bias != 0 and bias != (1 if s > 0 else -1)),
                        neu=(bias == 0)))
    return out


SETS = [("ALL", lambda r: True), ("ALIGNED", lambda r: r["al"]), ("AGAINST", lambda r: r["ag"]), ("NEUTRAL", lambda r: r["neu"])]


def report(recs, T1, H1, L1):
    for name, kind, val in TPS:
        for sname, keep in SETS:
            fs = [r["f"] for r in recs if keep(r)]
            d, _ = eval_1m(fs, kind, val, T1, H1, L1)
            print("    %-9s %-8s %s" % (name, sname, fmt(d)), flush=True)
        print("", flush=True)


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    print("Radar Runner x WALL-REGIME bias (active S vs R wall count) | 1m first-touch | non-overlap | fees+slip\n", flush=True)
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    del A1
    for src, tf in COMBOS:
        t0 = time.time()
        A = sorted(load_archive(tf, root=ROOTS[src], drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
        nS, nR = wall_counts(A)
        recs = tag(load_fires(src, tf), nS, nR)
        print("=" * 118, flush=True)
        print("%s %s | badges %d | ALIGNED %d (%.0f%%) | AGAINST %d (%.0f%%) | NEUTRAL %d  (%.0fs)" % (src.upper(), tf, len(recs),
              sum(r["al"] for r in recs), 100 * np.mean([r["al"] for r in recs]),
              sum(r["ag"] for r in recs), 100 * np.mean([r["ag"] for r in recs]), sum(r["neu"] for r in recs), time.time() - t0), flush=True)
        report(recs, T1, H1, L1)
    print("=" * 118, flush=True)
    print("BUCKET 30m — TRUE OUT-OF-SAMPLE (daemon 30m, daemon-1m first-touch)", flush=True)
    import multiprocessing as mp
    Ad = sorted(load_archive("30m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(Ad); best = {}
    with mp.Pool(4, initializer=D30._init) as pool:
        for res in pool.imap(D30._work, [(a, min(a + 300, n)) for a in range(1, n, 300)]):
            for (b, et, s, e, sl, fk) in res:
                cur = best.get((b, s))
                if cur is None or fk < cur[5]:
                    best[(b, s)] = (b, et, s, e, sl, fk)
    byet = {}
    for rec in sorted(best.values(), key=lambda r: (r[5], r[0])):
        if rec[1] not in byet:
            byet[rec[1]] = rec
    fires_d = sorted([(b, et, s, e, sl) for (b, et, s, e, sl, fk) in byet.values()])
    nSd, nRd = wall_counts(Ad)
    recs_d = tag(fires_d, nSd, nRd)
    print("  daemon 30m badges %d | ALIGNED %d | AGAINST %d | NEUTRAL %d" % (len(recs_d),
          sum(r["al"] for r in recs_d), sum(r["ag"] for r in recs_d), sum(r["neu"] for r in recs_d)), flush=True)
    Ad1 = sorted(load_archive("1m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    Td = np.array([_f(b.get("start_time")) for b in Ad1]); Hd = np.array([_f(b.get("high")) for b in Ad1]); Ld = np.array([_f(b.get("low")) for b in Ad1])
    del Ad1
    report(recs_d, Td, Hd, Ld)


if __name__ == "__main__":
    main()
