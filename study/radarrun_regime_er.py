"""PRE-REGISTERED regime test (user 2026-08-24): does a trend/chop NOWCAST condition Radar Runner outcomes?
ONE metric, chosen a priori, no sweeps: trailing 5-DAY Kaufman Efficiency Ratio of the substrate's own closes
(ER = |C_end - C_start| / sum|dC| over every bar ending in [t-5d, t)). High = trending, low = chop. Causal.
THE TRAP (named in advance): any regime metric trivially separates recon-2025 (trend, RR+) from the daemon era
(chop, RR-) — that would be circular. The claim stands ONLY if HIGH-ER beats LOW-ER **WITHIN each era**:
2025-recon, 2026-recon, and the daemon era separately. Disjoint in-era terciles, one non-overlap taken() sequence
per combo tagged by ER (not per-subset re-taking), 1m first-touch, RR 1:1 + 0.5% net.
python study/radarrun_regime_er.py"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.radarrun_honest_deltapct_tp import load_fires, ROOTS, FEE, SLIP
from study.radarrun_bkt1h_deltapct_confirm import resolve_1m
import study.radarrun_30m_delta_kept_eff as D30

COMBOS = [("bucket", "15m"), ("clock", "15m"), ("bucket", "30m"), ("clock", "30m"), ("bucket", "1h")]
ER_SEC = 5 * 86400


def er_at(ET, CL, t):
    """Kaufman ER of closes of bars ended in [t-5d, t). None if < 20 bars."""
    j1 = int(np.searchsorted(ET, t - 1)); j0 = int(np.searchsorted(ET, t - ER_SEC))
    if j1 - j0 < 20:
        return None
    c = CL[j0:j1]
    denom = float(np.abs(np.diff(c)).sum())
    return (abs(float(c[-1] - c[0])) / denom) if denom > 0 else 0.0


def taken_records(fires, A, T1, H1, L1):
    """ONE non-overlap taken() pass over ALL badges; each taken trade tagged (t, net05, rr1, er)."""
    from study.candle_bias_1h import _f
    ET = np.array([_f(b.get("end_time")) for b in A]); CL = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    out = []; busy = -1.0
    for (b, t, s, e, sl) in fires:
        if t < busy:
            continue
        sld = abs(e - sl) / e
        if sld <= 0:
            continue
        n05, tx = resolve_1m(s, e, sl, 0.0054, t, T1, H1, L1)
        if n05 is None:
            continue
        nr1, _ = resolve_1m(s, e, sl, 1.0 * sld, t, T1, H1, L1)
        er = er_at(ET, CL, t)
        out.append(dict(t=t, n05=n05 * 100, nr1=(nr1 * 100) if nr1 is not None else None, er=er,
                        y=datetime.fromtimestamp(t, tz=timezone.utc).year))
        busy = tx
    return [r for r in out if r["er"] is not None and r["nr1"] is not None]


def era_table(recs, label):
    if len(recs) < 60:
        print("    %-9s n=%d — too few" % (label, len(recs)), flush=True); return None
    ers = np.array([r["er"] for r in recs]); q = np.quantile(ers, [1 / 3, 2 / 3])
    print("    %-9s n=%d  ER terciles q=%.3f / %.3f:" % (label, len(recs), q[0], q[1]), flush=True)
    res = {}
    for lab, lo, hi in (("LO(chop)", -1, q[0]), ("MID", q[0], q[1]), ("HI(trend)", q[1], 9)):
        g = [r for r in recs if lo <= r["er"] < hi]
        r1 = np.mean([x["nr1"] for x in g]); w1 = 100 * np.mean([x["nr1"] > 0 for x in g])
        n5 = np.mean([x["n05"] for x in g]); w5 = 100 * np.mean([x["n05"] > 0 for x in g])
        res[lab] = r1
        print("      %-9s n=%-4d | RR1:1 win %4.1f%% avg %+.3f%% | 0.5%%net win %4.1f%% avg %+.3f%%"
              % (lab, len(g), w1, r1, w5, n5), flush=True)
    d = res["HI(trend)"] - res["LO(chop)"]
    print("      HI-LO (RR1:1): %+.3f%%  %s" % (d, "<-- trend better" if d > 0 else "<-- CHOP better (inverts)"), flush=True)
    return d


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    print("PRE-REGISTERED: RadarRun x trailing 5-day Kaufman ER | in-era disjoint terciles | 1m first-touch | one taken() sequence\n", flush=True)
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    del A1
    diffs = []
    for src, tf in COMBOS:
        t0 = time.time()
        A = sorted(load_archive(tf, root=ROOTS[src], drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
        recs = taken_records(load_fires(src, tf), A, T1, H1, L1)
        print("=" * 110, flush=True)
        print("%s %s | taken n=%d  (%.0fs)" % (src.upper(), tf, len(recs), time.time() - t0), flush=True)
        for era, sel in (("2025", lambda r: r["y"] == 2025), ("2026rec", lambda r: r["y"] == 2026)):
            d = era_table([r for r in recs if sel(r)], era)
            if d is not None:
                diffs.append((src, tf, era, d))
    # daemon era
    print("=" * 110, flush=True)
    print("DAEMON 30m era (true OOS window)", flush=True)
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
    Ad1 = sorted(load_archive("1m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    Td = np.array([_f(b.get("start_time")) for b in Ad1]); Hd = np.array([_f(b.get("high")) for b in Ad1]); Ld = np.array([_f(b.get("low")) for b in Ad1])
    del Ad1
    recs_d = taken_records(fires_d, Ad, Td, Hd, Ld)
    d = era_table(recs_d, "daemon")
    if d is not None:
        diffs.append(("bucket", "30m", "daemon", d))
    print("\n" + "=" * 110, flush=True)
    print("PRE-REGISTERED VERDICT — HI-LO (RR1:1) per combo x era (claim holds only if positive across eras incl. daemon):", flush=True)
    for src, tf, era, d in diffs:
        print("  %-7s %-4s %-8s %+.3f%%" % (src, tf, era, d), flush=True)
    pos = sum(1 for *_x, d in diffs if d > 0)
    print("  positive %d / %d" % (pos, len(diffs)), flush=True)


if __name__ == "__main__":
    main()
