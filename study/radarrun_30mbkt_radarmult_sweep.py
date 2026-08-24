"""RADAR_MULT sweep on the 30m-bucket Radar Runner (user 2026-08-24). Pre-registered grid {1.5, 2.0, 3.0, 4.0} — no
iteration beyond it. mult moves three things at once: the SL cap (radar edge; at 3.0 it almost never binds -> ~0.95%
stops), the breakout trigger (close beyond P +/- mult*band), and wall lifetime (body-close beyond the radar retires it).
Per mult: fresh union-persist replay (stride 1, W=2000 — detection changes with mult; the mult=3 control reuses the
legacy cache), then 1-MINUTE first-touch evaluation (non-overlap, fees+slip) at 0.5% net and RR 1:1, per year, plus the
TRUE OOS on daemon 30m buckets. Reports badge counts + mean stop distance per mult (did the cap bind?).
python study/radarrun_30mbkt_radarmult_sweep.py"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "study", "out")
MULTS = [1.5, 2.0, 3.0, 4.0]
W = 2000
_A = None; _M = 3.0


def _init(root, mult):
    global _A, _M
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    from app import radar_breakout_detect as RB
    if root is None:
        _A = sorted(load_archive("30m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    else:
        _A = sorted(load_archive("30m", root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    _M = float(mult)
    RB.RADAR_MULT = _M                                   # the breakout box + SL cap use this module global


def _work(rng):
    k0, k1 = rng
    from app import config, radar_breakout_detect as RB, absorption_level_detect as AL
    from study.candle_bias_1h import _f
    seen = {}
    for k in range(k0, k1):
        lo = max(0, k - W); sub = _A[lo:k + 1]
        try:
            walls = AL.detect(sub, skip_last=False, radar_mult=_M)   # walls + runs + lifetime at the SWEEP mult
        except Exception:
            walls = []
        for g in RB.detect(sub, walls=walls, skip_last=False, sl_buf=0.003, tp_frac=config.RR_TP_FRAC):
            b = lo + int(g["i"]); key = (b, g["side"])
            if key not in seen:
                seen[key] = (b, _f(_A[b].get("end_time")), g["side"], g["entry"], g["sl_trade"], k)
    return list(seen.values())


def union(root, mult, tag):
    import multiprocessing as mp
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    path = os.path.join(OUT, "rr_union_b30m_%s_m%02d.json" % (tag, int(mult * 10)))
    legacy = os.path.join(OUT, "rr30mbkt_live_fires_union.json")
    if abs(mult - 3.0) < 1e-9 and tag == "recon" and os.path.exists(legacy):
        return json.load(open(legacy))
    if os.path.exists(path):
        return json.load(open(path))
    if root is None:
        n = len(load_archive("30m", drop_degenerate=True)[1])
    else:
        n = len(load_archive("30m", root=root, drop_degenerate=False)[1])
    best = {}
    chunks = [(a, min(a + 600, n)) for a in range(1, n, 600)]
    with mp.Pool(6, initializer=_init, initargs=(root, mult)) as pool:
        for i, res in enumerate(pool.imap(_work, chunks), 1):
            for (b, et, s, e, sl, fk) in res:
                cur = best.get((b, s))
                if cur is None or fk < cur[5]:
                    best[(b, s)] = (b, et, s, e, sl, fk)
            if i % 10 == 0 or i == len(chunks):
                print("    m%.1f %s chunk %d/%d badges %d" % (mult, tag, i, len(chunks), len(best)), flush=True)
    byet = {}
    for rec in sorted(best.values(), key=lambda r: (r[5], r[0])):
        if rec[1] not in byet:
            byet[rec[1]] = rec
    fires = sorted([(b, et, s, e, sl) for (b, et, s, e, sl, fk) in byet.values()])
    json.dump(fires, open(path, "w"))
    return fires


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    from study.radarrun_bkt1h_deltapct_confirm import eval_1m
    from study.radarrun_honest_deltapct_tp import fmt
    print("RADAR_MULT sweep — 30m bucket | pre-registered {1.5, 2.0, 3.0, 4.0} | union replay per mult | 1m first-touch | non-overlap\n", flush=True)
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    del A1
    Ad1 = sorted(load_archive("1m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    Td = np.array([_f(b.get("start_time")) for b in Ad1]); Hd = np.array([_f(b.get("high")) for b in Ad1]); Ld = np.array([_f(b.get("low")) for b in Ad1])
    del Ad1
    for mult in MULTS:
        t0 = time.time()
        fr = union("study/recon_archive", mult, "recon")
        sld = np.array([abs(e - sl) / e for (b, t, s, e, sl) in fr if e > 0])
        print("=" * 118, flush=True)
        print("RADAR_MULT %.1f | RECON badges %d | mean stop %.3f%% (p25 %.3f / p75 %.3f)  (%.0fs)"
              % (mult, len(fr), 100 * sld.mean(), 100 * np.percentile(sld, 25), 100 * np.percentile(sld, 75), time.time() - t0), flush=True)
        for cname, kind, val in (("0.5% net", "fix", 0.0054), ("RR 1:1", "rr", 1.0)):
            d, _ = eval_1m(fr, kind, val, T1, H1, L1)
            print("  recon  %-8s %s" % (cname, fmt(d)), flush=True)
        frd = union(None, mult, "daemon")
        sldd = np.array([abs(e - sl) / e for (b, t, s, e, sl) in frd if e > 0])
        print("  DAEMON badges %d | mean stop %.3f%%" % (len(frd), 100 * sldd.mean() if len(sldd) else 0), flush=True)
        for cname, kind, val in (("0.5% net", "fix", 0.0054), ("RR 1:1", "rr", 1.0)):
            d, _ = eval_1m(frd, kind, val, Td, Hd, Ld)
            print("  daemon %-8s %s" % (cname, fmt(d)), flush=True)


if __name__ == "__main__":
    main()
