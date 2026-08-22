"""RADAR RUNNER — HONEST SCREEN across ALL clock + bucket timeframes (excl 1m), per study/HONEST_TEST_PROMPT.md.
Gates: (1) union persist semantics via incremental replay (terminal.py ~7783), stride-validated vs the exact 30m-bucket
set; 5m CLOCK applies the terminal's absorpR >= RR_ABSORPR_MIN gate; (2) full period Jan-2025 -> archive end, both years
split, month density printed; (3) BAR-LEVEL resolution for the SCREEN (conservative: SL-first within a bar) -- 1m
confirmation deferred until a combo survives; calibrated against the 30m-bucket 1m truth in --validate; (4) non-overlap
taken(); (6) n / W / L / win% / avg net / avg R / hist DD @0.4% / prop FIRST-attempt MC. As-badge scale-out bracket.
python study/radarrun_honest_alltf.py --validate | python study/radarrun_honest_alltf.py [clock:5m bucket:1h ...]"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "study", "out")
ROOTS = {"clock": "study/clock_archive", "bucket": "study/recon_archive"}
COMBOS = [("bucket", "30m"), ("clock", "4h"), ("bucket", "4h"), ("clock", "1h"), ("bucket", "1h"), ("clock", "30m"), ("clock", "15m"), ("bucket", "15m"), ("clock", "5m"), ("bucket", "5m")]   # cheapest first
W, STRIDE = 2000, 1          # stride MUST be 1: ~40% of union badges are single-frame (stride-2 recall 70.7%, stride-3 58.9%)
FEE, SLIP, G1, G2 = 0.0004, 0.0003, 0.0024, 0.0044
HOLD = 400
_A = None


def slbuf(tf):
    return 0.002 if tf == "1h" else 0.003


def _init(root, tf):
    global _A
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    _A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))


def _work(args):
    k0, k1, stride, sb = args
    from app import config, radar_breakout_detect as RB
    from study.candle_bias_1h import _f
    seen = {}
    for k in range(k0, k1, stride):
        lo = max(0, k - W); sub = _A[lo:k + 1]
        for g in RB.detect(sub, skip_last=False, sl_buf=sb, tp_frac=config.RR_TP_FRAC):
            b = lo + int(g["i"]); key = (b, g["side"])
            if key not in seen:
                seen[key] = (b, _f(_A[b].get("end_time")), g["side"], g["entry"], g["sl_trade"], k)
    return list(seen.values())


def union_fires(src, tf, stride=STRIDE, k_range=None, cache=True, nproc=6):
    """Union badge set for (src, tf). Cached unless k_range given (validation slices)."""
    import multiprocessing as mp
    root = ROOTS[src]
    path = os.path.join(OUT, "rr_union_%s_%s_s%d.json" % (src, tf, stride))
    legacy = os.path.join(OUT, "rr30mbkt_live_fires_union.json")           # the exact stride-1 30m bucket set
    if cache and k_range is None:
        if src == "bucket" and tf == "30m" and os.path.exists(legacy):
            return json.load(open(legacy))
        if os.path.exists(path):
            return json.load(open(path))
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    n = len(load_archive(tf, root=root, drop_degenerate=False)[1])
    k0, k1 = (1, n) if k_range is None else k_range
    chunks = [(a, min(a + 600, k1), stride, slbuf(tf)) for a in range(k0, k1, 600)]
    best = {}
    with mp.Pool(nproc, initializer=_init, initargs=(root, tf)) as pool:
        for i, res in enumerate(pool.imap(_work, chunks), 1):
            for (b, et, s, e, sl, fk) in res:
                cur = best.get((b, s))
                if cur is None or fk < cur[5]:
                    best[(b, s)] = (b, et, s, e, sl, fk)
            if i % 10 == 0 or i == len(chunks):
                print("    %s %s chunk %d/%d  badges %d" % (src, tf, i, len(chunks), len(best)), flush=True)
    byet = {}
    for rec in sorted(best.values(), key=lambda r: (r[5], r[0])):
        if rec[1] not in byet:
            byet[rec[1]] = rec
    fires = sorted([(b, et, s, e, sl) for (b, et, s, e, sl, fk) in byet.values()])
    if src == "clock" and tf == "5m":                                       # terminal's 5m TIME absorpR gate
        from app import absorption as ABS, config
        A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
        kept = []
        for f in fires:
            try:
                aR = ABS.absorption(A, f[0])[0]
            except Exception:
                aR = None
            if aR is None or aR >= config.RR_ABSORPR_MIN:
                kept.append(f)
        print("    5m clock absorpR gate: %d -> %d" % (len(fires), len(kept)), flush=True)
        fires = kept
    if cache and k_range is None:
        json.dump(fires, open(path, "w"))
    return fires


def resolve_bar(s, e, sl, k, Hi, Lo, C):
    """Bar-level scale-out resolution (SCREEN). SL checked FIRST within a bar (conservative). Returns (net, outcome, exit_k)."""
    n = len(Hi); tp1 = e * (1 + s * G1); tp2 = e * (1 + s * G2); hit1 = False
    for j in range(k + 1, min(n, k + 1 + HOLD)):
        hi = Hi[j]; lo = Lo[j]
        if not hit1:
            if (lo <= sl) if s > 0 else (hi >= sl):
                return s * (sl - e) / e - FEE - 2 * SLIP, "SL", j
            if (hi >= tp1) if s > 0 else (lo <= tp1):
                hit1 = True
                if (hi >= tp2) if s > 0 else (lo <= tp2):
                    return 0.5 * (G1 - FEE - SLIP) + 0.5 * (G2 - FEE - SLIP), "TP1_TP2", j
                continue
        else:
            if (lo <= e) if s > 0 else (hi >= e):
                return 0.5 * (G1 - FEE - SLIP) + 0.5 * (0.0 - FEE - 2 * SLIP), "TP1_BE", j
            if (hi >= tp2) if s > 0 else (lo <= tp2):
                return 0.5 * (G1 - FEE - SLIP) + 0.5 * (G2 - FEE - SLIP), "TP1_TP2", j
    j = min(n - 1, k + HOLD)
    if hit1:
        return 0.5 * (G1 - FEE - SLIP) + 0.5 * (s * (C[j] - e) / e - FEE - 2 * SLIP), "EOD", j
    return s * (C[j] - e) / e - FEE - 2 * SLIP, "EOD", j


def evaluate(src, tf, fires, A):
    from study.candle_bias_1h import _f
    from study.radarrun_hyro_prop import mc, day_blocks
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    taken = []; busy = -1
    for (b, t, s, e, sl) in fires:
        if b < busy:
            continue
        sld = abs(e - sl) / e
        if sld <= 0:
            continue
        net, outc, xk = resolve_bar(s, e, sl, b, Hi, Lo, C)
        taken.append(dict(t=t, net=net, r=net / sld, outc=outc, y=datetime.fromtimestamp(t, tz=timezone.utc).year,
                          m=datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m")))
        busy = xk
    if len(taken) < 20:
        return dict(src=src, tf=tf, raw=len(fires), n=len(taken))
    nets = np.array([x["net"] for x in taken]); rs = np.array([x["r"] for x in taken])
    eq = np.cumsum(0.4 * rs); dd = float((np.maximum.accumulate(eq) - eq).max())
    days = day_blocks([(x["t"], x["net"], x["r"]) for x in taken])
    m4 = mc(days, 0.4, 4.0, "R")
    mix = {o: 100 * sum(1 for x in taken if x["outc"] == o) / len(taken) for o in ("TP1_TP2", "TP1_BE", "SL", "EOD")}
    md = {}
    for x in taken:
        md[x["m"]] = md.get(x["m"], 0) + 1
    yr = {}
    for Y in (2025, 2026):
        ny = np.array([x["net"] for x in taken if x["y"] == Y])
        yr[Y] = (len(ny), 100 * (ny > 0).mean() if len(ny) else 0, ny.mean() * 100 if len(ny) else 0)
    return dict(src=src, tf=tf, raw=len(fires), n=len(taken), W=int((nets > 0).sum()), L=int((nets < 0).sum()),
                win=100 * (nets > 0).mean(), avg=nets.mean() * 100, avgR=rs.mean(), dd=dd, prop=m4["p"], propd=m4["d50"],
                mix=mix, spd=len(taken) / max(1, len(days)), yr=yr, months=len(md), mmin=min(md.values()), mmax=max(md.values()))


def persisted_check(src, tf, fires):
    """The terminal's own persisted fires for this tf (clock vs bucket by end-time alignment) must be a subset."""
    from app import config
    TFM = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}
    try:
        pers = json.load(open(os.path.join(config.DATA_DIR, "radarrun_fired.json"))).get(tf, {})
    except Exception:
        return "n/a"
    ets = np.array([f[1] for f in fires]) if fires else np.array([0.0])
    ok = tot = 0
    for kk in pers:
        t = float(kk); dt = datetime.fromtimestamp(t, tz=timezone.utc)
        clk = abs(t - round(t)) < 0.02 and dt.second == 0 and dt.minute % TFM[tf] == 0
        if (clk != (src == "clock")) or t > ets[-1] + 1:
            continue
        tot += 1
        if abs(ets[int(np.argmin(np.abs(ets - t)))] - t) < 2.0:
            ok += 1
    return "%d/%d" % (ok, tot) if tot else "none"


def validate():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    print("== (a) BAR-level resolver vs 1m TRUTH on 30m bucket (exact union set) ==", flush=True)
    A = sorted(load_archive("30m", root=ROOTS["bucket"], drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    fires = union_fires("bucket", "30m")
    r = evaluate("bucket", "30m", fires, A)
    print("   bar-level: n=%d win %.1f%%  avg %+.3f%%  mix bothTP %.1f / TP1BE %.1f / SL %.1f   prop %.1f%%"
          % (r["n"], r["win"], r["avg"], r["mix"]["TP1_TP2"], r["mix"]["TP1_BE"], r["mix"]["SL"], r["prop"]), flush=True)
    print("   1m TRUTH : n=5119 win 82.7%  avg -0.038%  mix bothTP 48.8 / TP1BE 33.9 / SL 17.3   prop 0.0%", flush=True)
    print("== (b) STRIDE recall vs exact (stride-1) union on 30m bucket, bars 20000-24000 ==", flush=True)
    exact = {(f[0], f[2]) for f in fires if 20100 <= f[0] <= 23800}
    for st in (2, 3, 4):
        t0 = time.time()
        fs = union_fires("bucket", "30m", stride=st, k_range=(20000, 24000), cache=False)
        got = {(f[0], f[2]) for f in fs if 20100 <= f[0] <= 23800}
        print("   stride %d: recall %d/%d = %.1f%%  extra %d   (%.0fs)" % (st, len(exact & got), len(exact),
              100 * len(exact & got) / max(1, len(exact)), len(got - exact), time.time() - t0), flush=True)
    print("== (c) per-detect cost on 5m clock (W=%d) ==" % W, flush=True)
    from app import config, radar_breakout_detect as RB
    A5 = sorted(load_archive("5m", root=ROOTS["clock"], drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    t0 = time.time()
    for k in range(60000, 60020):
        RB.detect(A5[k - W:k + 1], skip_last=False, sl_buf=0.003, tp_frac=config.RR_TP_FRAC)
    print("   5m clock: %.3fs/detect  (n bars=%d)" % ((time.time() - t0) / 20, len(A5)), flush=True)


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    if "--validate" in sys.argv:
        validate(); return
    want = [a for a in sys.argv[1:] if ":" in a]
    combos = [tuple(a.split(":")) for a in want] if want else COMBOS
    rows = []
    for src, tf in combos:
        t0 = time.time()
        print("=" * 100, flush=True); print("%s %s" % (src.upper(), tf), flush=True)
        fires = union_fires(src, tf)
        A = sorted(load_archive(tf, root=ROOTS[src], drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
        r = evaluate(src, tf, fires, A); r["pers"] = persisted_check(src, tf, fires); rows.append(r)
        if "win" in r:
            print("  raw %d | taken n=%d W=%d L=%d | win %.1f%% | avg %+.3f%% R %+.3f | bothTP %.1f%% TP1BE %.1f%% SL %.1f%% | "
                  "histDD@0.4 %.1f%% | prop1st %.1f%% (med %.0fd) | %.2f/day | months %d (%d..%d/mo) | persisted %s | %.0fs"
                  % (r["raw"], r["n"], r["W"], r["L"], r["win"], r["avg"], r["avgR"], r["mix"]["TP1_TP2"], r["mix"]["TP1_BE"],
                     r["mix"]["SL"], r["dd"], r["prop"], r["propd"], r["spd"], r["months"], r["mmin"], r["mmax"], r["pers"], time.time() - t0), flush=True)
            for Y in (2025, 2026):
                print("     %d: n=%-5d win %.1f%%  avg %+.3f%%" % (Y, *r["yr"][Y]), flush=True)
        else:
            print("  raw %d taken %d -- too few" % (r["raw"], r["n"]), flush=True)
    print("\n" + "=" * 100, flush=True)
    print("SUMMARY (bar-level screen, as-badge scale-out, non-overlap, fees+slip)", flush=True)
    print("  %-7s %-4s | %5s %5s %5s %5s | %6s | %8s %7s | %6s %6s %6s | %8s %7s | %8s %8s | %s"
          % ("src", "tf", "raw", "n", "W", "L", "win%", "avg%", "avgR", "2TP%", "BE%", "SL%", "DD@0.4", "prop1st", "2025avg", "2026avg", "persisted"), flush=True)
    for r in rows:
        if "win" not in r:
            print("  %-7s %-4s | %5d %5d   too few" % (r["src"], r["tf"], r["raw"], r["n"]), flush=True); continue
        print("  %-7s %-4s | %5d %5d %5d %5d | %5.1f%% | %+7.3f%% %+7.3f | %5.1f%% %5.1f%% %5.1f%% | %7.1f%% %6.1f%% | %+7.3f%% %+7.3f%% | %s"
              % (r["src"], r["tf"], r["raw"], r["n"], r["W"], r["L"], r["win"], r["avg"], r["avgR"], r["mix"]["TP1_TP2"], r["mix"]["TP1_BE"],
                 r["mix"]["SL"], r["dd"], r["prop"], r["yr"][2025][2], r["yr"][2026][2], r["pers"]), flush=True)


if __name__ == "__main__":
    main()
