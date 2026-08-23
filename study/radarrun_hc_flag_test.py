"""HONEST test of the Radar Runner badge FLAGS on the union sets (user 2026-08-24): the gold-ring HC flag
(_rr_conviction: breakout-side Strength effort_z >= STR_EFFORT_HI AND trailing-50 reward/eff share > 50 toward the
break) and the ABSORBED flag (_rr_absorbed: absorption A >= 0 at the breakout bar). Both computed with the SAME app
modules the terminal freezes at fire time. Old claims ("hc lifts win% on 1h", "absorbed robust on 5m/15m") came from
the discredited batch pipeline — this is the re-test under the gates. Union badge sets, bucket+clock x 15m/30m/1h,
non-overlap on 1m exits, 1-MINUTE first-touch, 0.5% net + RR 1:1, per year; sets ALL / HC / not-HC / ABSORBED /
HC&ABSORBED; then TRUE OOS on daemon 30m buckets. python study/radarrun_hc_flag_test.py"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.radarrun_honest_deltapct_tp import load_fires, fmt, ROOTS
from study.radarrun_bkt1h_deltapct_confirm import eval_1m
import study.radarrun_30m_delta_kept_eff as D30

COMBOS = [("bucket", "15m"), ("clock", "15m"), ("bucket", "30m"), ("clock", "30m"), ("bucket", "1h"), ("clock", "1h")]


def flags(fires, A):
    """(hc, absorbed) per badge — the terminal's exact fire-time computation (reward_eff + absorption modules)."""
    from app import reward_eff, absorption
    out = []
    for f in fires:
        b, t, s, e, sl = f
        if b < 51:
            continue
        hc = False
        try:
            up = s > 0
            base = reward_eff.strength_baseline(A, b); bo = 0.0
            if base and base.get("vol"):
                st = reward_eff.strength(A, b, b, base=base)
                if st.get("ok"):
                    bo = st["buy" if up else "sell"]["effort_z"]
            sh, ok = reward_eff.share(A, b - 49, b)
            rf = (sh if up else 100.0 - sh) if ok else 50.0
            hc = bool(bo >= reward_eff.STR_EFFORT_HI and rf > 50.0)
        except Exception:
            pass
        try:
            a = absorption.absorption(A, b)[0]
            ab = bool(a is not None and a >= 0.0)
        except Exception:
            ab = False
        out.append(dict(f=f, hc=hc, ab=ab))
    return out


SETS = [("ALL", lambda r: True), ("HC (gold ring)", lambda r: r["hc"]), ("not-HC", lambda r: not r["hc"]),
        ("ABSORBED", lambda r: r["ab"]), ("HC & ABSORBED", lambda r: r["hc"] and r["ab"])]


def report(recs, T1, H1, L1):
    for name, keep in SETS:
        fs = [r["f"] for r in recs if keep(r)]
        for cname, kind, val in (("0.5% net", "fix", 0.0054), ("RR 1:1", "rr", 1.0)):
            d, _ = eval_1m(fs, kind, val, T1, H1, L1)
            print("    %-15s %-8s %s" % (name, cname, fmt(d)), flush=True)


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    print("Radar Runner HC (gold ring) + ABSORBED flags — honest re-test | 1-MINUTE first-touch | non-overlap | fees+slip\n", flush=True)
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    del A1
    for src, tf in COMBOS:
        t0 = time.time()
        A = sorted(load_archive(tf, root=ROOTS[src], drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
        recs = flags(load_fires(src, tf), A)
        print("=" * 120, flush=True)
        print("%s %s | badges %d | HC %d (%.0f%%) | ABSORBED %d (%.0f%%)  (%.0fs)" % (src.upper(), tf, len(recs),
              sum(r["hc"] for r in recs), 100 * np.mean([r["hc"] for r in recs]),
              sum(r["ab"] for r in recs), 100 * np.mean([r["ab"] for r in recs]), time.time() - t0), flush=True)
        report(recs, T1, H1, L1)
    print("\n" + "=" * 120, flush=True)
    print("BUCKET 30m — TRUE OUT-OF-SAMPLE (daemon 30m buckets, daemon-1m first-touch)", flush=True)
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
    recs_d = flags(fires_d, Ad)
    print("  daemon 30m buckets %d, union badges %d | HC %d | ABSORBED %d" % (n, len(recs_d),
          sum(r["hc"] for r in recs_d), sum(r["ab"] for r in recs_d)), flush=True)
    Ad1 = sorted(load_archive("1m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    Td = np.array([_f(b.get("start_time")) for b in Ad1]); Hd = np.array([_f(b.get("high")) for b in Ad1]); Ld = np.array([_f(b.get("low")) for b in Ad1])
    del Ad1
    report(recs_d, Td, Hd, Ld)


if __name__ == "__main__":
    main()
