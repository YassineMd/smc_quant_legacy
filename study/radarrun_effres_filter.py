"""Does the Eff/Res (effort->result) metric separate Radar Runner WINNERS from LOSERS, and does it add value BEYOND candle
geometry? (user 2026-08-23). Honest union badge sets (cached), bucket+clock x 15m/30m/1h, non-overlap taken(), WIN = 0.5%
net TP before the candle SL (bar-level SL-first), EOD dropped. Feature = app.effort_result.compute at the BREAKOUT bar:
eff (result/expected ticks), retention (kept share of the excursion), label ABSORBED/normal/EASY/n-a.
Layers: (1) raw AUC per year + disjoint bands (win%, avg net); (2) size-STRATIFIED grid (sld% tercile x eff band) — if the
eff effect vanishes inside size buckets it is geometry, not information; (3) as a FILTER: drop ABSORBED / keep EASY vs ALL,
per year. Cross-combo tally at the end. python study/radarrun_effres_filter.py"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from app import effort_result as ER
from study.nowick_wall_winloss import auc
from study.radarrun_honest_deltapct_tp import load_fires, resolve, ROOTS, FEE, SLIP

COMBOS = [("bucket", "15m"), ("clock", "15m"), ("bucket", "30m"), ("clock", "30m"), ("bucket", "1h"), ("clock", "1h")]
TP_G = 0.0054
BANDS = [("ABSORBED", lambda e: e is not None and e <= ER.ABSORBED_MAX),
         ("normal", lambda e: e is not None and ER.ABSORBED_MAX < e < ER.EASY_MIN),
         ("EASY", lambda e: e is not None and e >= ER.EASY_MIN),
         ("n/a", lambda e: e is None)]


def rows_for(src, tf):
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    fires = load_fires(src, tf)
    A = sorted(load_archive(tf, root=ROOTS[src], drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    out = []; busy = -1
    for (b, t, s, e, sl) in fires:
        if b < busy:
            continue
        sld = abs(e - sl) / e
        if sld <= 0 or b < ER.WIN + 1:
            continue
        net, xk = resolve(s, e, sl, TP_G, b, Hi, Lo, C); busy = xk
        if net > 0:
            win = 1
        elif abs(net - (-(sld) - FEE - 2 * SLIP)) < 1e-9:
            win = 0
        else:
            continue
        r = ER.compute(A, b)
        eff = r["eff"] if r else None; ret = r["retention"] if r else None
        # sign-aware: the breakout SIDE vs the bar's delta side — eff is signed by the DELTA side; for a breakout we want
        # efficiency IN THE BREAKOUT direction, so flip when the bar's net delta opposed the break.
        if r is not None and eff is not None and np.sign(r["delta"]) != s:
            eff = -eff
        out.append(dict(win=win, net=net * 100.0, y=datetime.fromtimestamp(t, tz=timezone.utc).year, eff=eff, ret=ret,
                        sld=sld * 100.0, lab=(r["label"] if r else "n/a")))
    return out


def wl(g):
    if not g:
        return "n=0"
    return "n=%-4d win %4.1f%% avg %+.3f%%" % (len(g), 100 * np.mean([x["win"] for x in g]), np.mean([x["net"] for x in g]))


def main():
    print("Eff/Res as a Radar Runner winner/loser separator | WIN = 0.5%% net TP before candle SL | eff signed toward the BREAKOUT side\n", flush=True)
    tally = {}
    for src, tf in COMBOS:
        t0 = time.time(); rows = rows_for(src, tf)
        n25 = [r for r in rows if r["y"] == 2025]; n26 = [r for r in rows if r["y"] == 2026]
        print("=" * 120, flush=True)
        print("%s %s | n=%d | base win %.1f%% | 2025 %s | 2026 %s  (%.0fs)" % (src.upper(), tf, len(rows), 100 * np.mean([r["win"] for r in rows]),
              wl(n25), wl(n26), time.time() - t0), flush=True)
        # (1) raw AUC per year
        for f in ("eff", "ret"):
            a25, _ = auc(np.array([r[f] if r[f] is not None else np.nan for r in n25], float), np.array([r["win"] for r in n25]))
            a26, _ = auc(np.array([r[f] if r[f] is not None else np.nan for r in n26], float), np.array([r["win"] for r in n26]))
            cons = (a25 - .5) * (a26 - .5) > 0 and min(abs(a25 - .5), abs(a26 - .5)) >= 0.03
            print("  AUC %-4s 2025 %.3f  2026 %.3f  %s" % (f, a25, a26, "<-- CONSISTENT" if cons else ""), flush=True)
            if cons:
                tally.setdefault(f, []).append(("%s %s" % (src, tf), "+" if a25 > .5 else "-"))
        # disjoint bands
        print("  bands (eff toward the break):", flush=True)
        for name, sel in BANDS:
            g25 = [r for r in n25 if sel(r["eff"])]; g26 = [r for r in n26 if sel(r["eff"])]
            print("    %-9s 2025 %s | 2026 %s" % (name, wl(g25), wl(g26)), flush=True)
        # (2) size-stratified grid
        sv = np.array([r["sld"] for r in rows]); q = np.quantile(sv, [1 / 3, 2 / 3])
        print("  size-STRATIFIED (sld%% tercile x eff band), pooled years — win%% (n):", flush=True)
        print("    %-10s %-20s %-20s %-20s" % ("sld tercile", "ABSORBED", "normal", "EASY"), flush=True)
        for lab, lo, hi in (("LO", -1, q[0]), ("MID", q[0], q[1]), ("HI", q[1], 1e9)):
            cells = []
            for name, sel in BANDS[:3]:
                g = [r for r in rows if lo <= r["sld"] < hi and sel(r["eff"])]
                cells.append("%5.1f%% (n%d)" % (100 * np.mean([x["win"] for x in g]), len(g)) if len(g) >= 15 else "   -- (n%d)" % len(g))
            print("    %-10s %-20s %-20s %-20s" % (lab, *cells), flush=True)
        # (3) as a filter
        print("  as a FILTER (vs ALL):", flush=True)
        for name, keep in (("ALL", lambda r: True), ("drop ABSORBED", lambda r: not (r["eff"] is not None and r["eff"] <= ER.ABSORBED_MAX)),
                           ("EASY only", lambda r: r["eff"] is not None and r["eff"] >= ER.EASY_MIN),
                           ("eff>=1 only", lambda r: r["eff"] is not None and r["eff"] >= 1.0)):
            print("    %-14s 2025 %s | 2026 %s" % (name, wl([r for r in n25 if keep(r)]), wl([r for r in n26 if keep(r)])), flush=True)
    print("\n" + "=" * 120, flush=True)
    print("CROSS-COMBO TALLY (consistent both years):", flush=True)
    for f, lst in sorted(tally.items(), key=lambda kv: -len(kv[1])):
        print("  %-4s %d/6  %s" % (f, len(lst), "  ".join("%s(%s)" % (c, d) for c, d in lst)), flush=True)
    if not tally:
        print("  NONE", flush=True)


if __name__ == "__main__":
    main()
