"""Eff/Res x DELTA>=P80 on Radar Runner breakouts (user 2026-08-23): does efficiency separate winners from losers WITHIN
the strong-delta badges ("big effort that bought price" vs "big effort absorbed")? Honest union sets, bucket+clock x
15m/30m/1h, non-overlap taken(), WIN = 0.5% net TP before candle SL (bar-level SL-first); also the RR 1:1 net per badge.
eff = app.effort_result at the breakout bar, signed toward the BREAK side; delta rank = the pane's trailing-50 |delta|
percentile (DELTA>=P80 = 'strong').
Layers: (1) AUC(eff) per year INSIDE the strong-delta subset + eff bands; (2) delta-band x eff-band grid (win%);
(3) as FILTERS vs ALL per year (win%, avg 0.5%-TP net, avg RR1:1 net); (4) size-stratified inside strong-delta.
python study/radarrun_effres_delta_filter.py"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from app import effort_result as ER
from study.nowick_wall_winloss import auc
from study.radarrun_honest_deltapct_tp import load_fires, resolve, delta_rank, ROOTS, FEE, SLIP, PCT_STRONG

COMBOS = [("bucket", "15m"), ("clock", "15m"), ("bucket", "30m"), ("clock", "30m"), ("bucket", "1h"), ("clock", "1h")]
TP_G = 0.0054
EB = [("ABSORBED", lambda e: e is not None and e <= ER.ABSORBED_MAX),
      ("normal", lambda e: e is not None and ER.ABSORBED_MAX < e < ER.EASY_MIN),
      ("EASY", lambda e: e is not None and e >= ER.EASY_MIN)]
DB = [("weak<P20", lambda d: d < 0.2), ("mid", lambda d: 0.2 <= d < PCT_STRONG), ("STRONG>=P80", lambda d: d >= PCT_STRONG)]


def rows_for(src, tf):
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    fires = load_fires(src, tf)
    A = sorted(load_archive(tf, root=ROOTS[src], drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    D = np.abs(np.array([_f(b.get("buy_vol")) - _f(b.get("sell_vol")) for b in A]))
    out = []; busy = -1
    for (b, t, s, e, sl) in fires:
        if b < busy:
            continue
        sld = abs(e - sl) / e
        if sld <= 0 or b < 51:
            continue
        net, xk = resolve(s, e, sl, TP_G, b, Hi, Lo, C); busy = xk
        if net > 0:
            win = 1
        elif abs(net - (-(sld) - FEE - 2 * SLIP)) < 1e-9:
            win = 0
        else:
            continue
        rr1, _ = resolve(s, e, sl, 1.0 * sld, b, Hi, Lo, C)
        r = ER.compute(A, b); eff = r["eff"] if r else None
        if r is not None and eff is not None and np.sign(r["delta"]) != s:
            eff = -eff
        out.append(dict(win=win, net=net * 100.0, rr1=rr1 * 100.0, y=datetime.fromtimestamp(t, tz=timezone.utc).year,
                        eff=eff, sld=sld * 100.0, dr=delta_rank(D, b)))
    return out


def wl(g):
    if not g:
        return "n=0"
    return "n=%-4d win %4.1f%% tp.5 %+.3f%% rr1 %+.3f%%" % (len(g), 100 * np.mean([x["win"] for x in g]),
                                                         np.mean([x["net"] for x in g]), np.mean([x["rr1"] for x in g]))


def main():
    print("Eff/Res x DELTA>=P80 on Radar Runner | WIN = 0.5%% net TP before candle SL | eff toward the break\n", flush=True)
    tally = {}
    for src, tf in COMBOS:
        t0 = time.time(); rows = rows_for(src, tf)
        strong = [r for r in rows if r["dr"] >= PCT_STRONG]
        s25 = [r for r in strong if r["y"] == 2025]; s26 = [r for r in strong if r["y"] == 2026]
        print("=" * 120, flush=True)
        print("%s %s | all n=%d win %.1f%% | STRONG-delta n=%d (%.0f%%) win %.1f%%  (%.0fs)" % (src.upper(), tf, len(rows),
              100 * np.mean([r["win"] for r in rows]), len(strong), 100 * len(strong) / max(1, len(rows)),
              100 * np.mean([r["win"] for r in strong]) if strong else 0, time.time() - t0), flush=True)
        a25, _ = auc(np.array([r["eff"] if r["eff"] is not None else np.nan for r in s25], float), np.array([r["win"] for r in s25]))
        a26, _ = auc(np.array([r["eff"] if r["eff"] is not None else np.nan for r in s26], float), np.array([r["win"] for r in s26]))
        cons = a25 == a25 and a26 == a26 and (a25 - .5) * (a26 - .5) > 0 and min(abs(a25 - .5), abs(a26 - .5)) >= 0.03
        print("  (1) AUC(eff) INSIDE strong-delta: 2025 %.3f  2026 %.3f  %s" % (a25, a26, "<-- CONSISTENT" if cons else ""), flush=True)
        if cons:
            tally.setdefault("eff|strong", []).append(("%s %s" % (src, tf), "+" if a25 > .5 else "-"))
        print("      eff bands inside strong-delta:", flush=True)
        for name, sel in EB:
            print("        %-9s 2025 %s | 2026 %s" % (name, wl([r for r in s25 if sel(r["eff"])]), wl([r for r in s26 if sel(r["eff"])])), flush=True)
        print("  (2) delta-band x eff-band grid, pooled — win%% (n):", flush=True)
        print("      %-12s %-18s %-18s %-18s" % ("delta \\ eff", "ABSORBED", "normal", "EASY"), flush=True)
        for dn, dsel in DB:
            cells = []
            for en, esel in EB:
                g = [r for r in rows if dsel(r["dr"]) and esel(r["eff"])]
                cells.append("%5.1f%% (n%d)" % (100 * np.mean([x["win"] for x in g]), len(g)) if len(g) >= 15 else "   -- (n%d)" % len(g))
            print("      %-12s %-18s %-18s %-18s" % (dn, *cells), flush=True)
        print("  (3) FILTERS vs ALL:", flush=True)
        F = [("ALL", lambda r: True), ("DELTA>=P80", lambda r: r["dr"] >= PCT_STRONG),
             ("D>=P80 & eff>=1", lambda r: r["dr"] >= PCT_STRONG and r["eff"] is not None and r["eff"] >= 1.0),
             ("D>=P80 & EASY", lambda r: r["dr"] >= PCT_STRONG and r["eff"] is not None and r["eff"] >= ER.EASY_MIN),
             ("D>=P80 & !ABS", lambda r: r["dr"] >= PCT_STRONG and not (r["eff"] is not None and r["eff"] <= ER.ABSORBED_MAX)),
             ("D>=P80 & ABS", lambda r: r["dr"] >= PCT_STRONG and r["eff"] is not None and r["eff"] <= ER.ABSORBED_MAX)]
        for name, keep in F:
            print("      %-16s 2025 %s | 2026 %s" % (name, wl([r for r in rows if r["y"] == 2025 and keep(r)]),
                                                       wl([r for r in rows if r["y"] == 2026 and keep(r)])), flush=True)
        sv = np.array([r["sld"] for r in strong]) if strong else np.array([0.0]); q = np.quantile(sv, [1 / 3, 2 / 3])
        print("  (4) size-stratified INSIDE strong-delta (sld tercile x eff band), pooled — win%% (n):", flush=True)
        for lab, lo, hi in (("LO", -1, q[0]), ("MID", q[0], q[1]), ("HI", q[1], 1e9)):
            cells = []
            for en, esel in EB:
                g = [r for r in strong if lo <= r["sld"] < hi and esel(r["eff"])]
                cells.append("%5.1f%% (n%d)" % (100 * np.mean([x["win"] for x in g]), len(g)) if len(g) >= 15 else "   -- (n%d)" % len(g))
            print("      %-12s %-18s %-18s %-18s" % (lab, *cells), flush=True)
    print("\n" + "=" * 120, flush=True)
    print("CROSS-COMBO TALLY (AUC(eff) inside strong-delta, consistent both years):", flush=True)
    for f, lst in tally.items():
        print("  %s %d/6  %s" % (f, len(lst), "  ".join("%s(%s)" % (c, d) for c, d in lst)), flush=True)
    if not tally:
        print("  NONE", flush=True)


if __name__ == "__main__":
    main()
