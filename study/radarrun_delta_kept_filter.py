"""Radar Runner breakouts with STRONG delta IN FAVOUR of the position AND a HIGH/VERY-HIGH KEPT share (user 2026-08-23).
Honest union badge sets, bucket+clock x 15m/30m/1h, non-overlap taken(), WIN = 0.5% net TP before candle SL (bar-level
SL-first), + RR 1:1 net. Definitions (all at the breakout bar, causal):
  strong-in-favour  = pane delta rank >= P80 (trailing-50 |delta| percentile) AND sign(delta) == breakout side
  kept              = share of the bar's excursion in the break direction retained at the close:
                      (close-open)/(high-open) for longs, (open-close)/(open-low) for shorts  (= the Eff/Res 'kept %')
Layers: (1) strong-in-favour subset vs ALL; (2) DISJOINT kept bands inside it (<.5 / .5-.7 / .7-.9 / >=.9) per year;
(3) FILTERS per year: S&kept>=.7, S&kept>=.9, and kept>=.9 WITHOUT delta (which ingredient works?); (4) size-stratified
inside S (sld tercile x kept>=.7); (5) AUC(kept) inside S per year. Cross-combo tally of both-year-positive cells.
python study/radarrun_delta_kept_filter.py"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.nowick_wall_winloss import auc
from study.radarrun_honest_deltapct_tp import load_fires, resolve, delta_rank, ROOTS, FEE, SLIP, PCT_STRONG

COMBOS = [("bucket", "15m"), ("clock", "15m"), ("bucket", "30m"), ("clock", "30m"), ("bucket", "1h"), ("clock", "1h")]
TP_G = 0.0054
KB = [("<50%", 0.0, 0.5), ("50-70%", 0.5, 0.7), ("70-90%", 0.7, 0.9), (">=90%", 0.9, 9.9)]


def rows_for(src, tf):
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    fires = load_fires(src, tf)
    A = sorted(load_archive(tf, root=ROOTS[src], drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A]); C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    Dl = np.array([_f(b.get("buy_vol")) - _f(b.get("sell_vol")) for b in A]); D = np.abs(Dl)
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
        exc = (Hi[b] - O[b]) if s > 0 else (O[b] - Lo[b])
        kept = (((C[b] - O[b]) if s > 0 else (O[b] - C[b])) / exc) if exc > 0 else float("nan")
        out.append(dict(win=win, net=net * 100.0, rr1=rr1 * 100.0, y=datetime.fromtimestamp(t, tz=timezone.utc).year,
                        dr=delta_rank(D, b), al=(Dl[b] > 0) == (s > 0), kept=kept, sld=sld * 100.0))
    return out


def wl(g):
    if not g:
        return "n=0"
    return "n=%-4d win %4.1f%% tp.5 %+.3f%% rr1 %+.3f%%" % (len(g), 100 * np.mean([x["win"] for x in g]),
                                                         np.mean([x["net"] for x in g]), np.mean([x["rr1"] for x in g]))


def main():
    print("Radar Runner: STRONG delta IN FAVOUR x KEPT share | WIN = 0.5%% net TP before candle SL\n", flush=True)
    cands = []
    for src, tf in COMBOS:
        t0 = time.time(); rows = rows_for(src, tf)
        S = [r for r in rows if r["dr"] >= PCT_STRONG and r["al"] and r["kept"] == r["kept"]]
        y = lambda g, Y: [r for r in g if r["y"] == Y]
        print("=" * 120, flush=True)
        print("%s %s | ALL n=%d win %.1f%% | STRONG-in-favour n=%d (%.0f%%) win %.1f%%  (%.0fs)" % (src.upper(), tf, len(rows),
              100 * np.mean([r["win"] for r in rows]), len(S), 100 * len(S) / max(1, len(rows)),
              100 * np.mean([r["win"] for r in S]) if S else 0, time.time() - t0), flush=True)
        a25, _ = auc(np.array([r["kept"] for r in y(S, 2025)], float), np.array([r["win"] for r in y(S, 2025)]))
        a26, _ = auc(np.array([r["kept"] for r in y(S, 2026)], float), np.array([r["win"] for r in y(S, 2026)]))
        print("  AUC(kept) inside S: 2025 %.3f  2026 %.3f%s" % (a25, a26,
              "  <-- CONSISTENT" if (a25 == a25 and a26 == a26 and (a25 - .5) * (a26 - .5) > 0 and min(abs(a25 - .5), abs(a26 - .5)) >= .03) else ""), flush=True)
        print("  DISJOINT kept bands inside S:", flush=True)
        for name, lo, hi in KB:
            g25 = [r for r in y(S, 2025) if lo <= r["kept"] < hi]; g26 = [r for r in y(S, 2026) if lo <= r["kept"] < hi]
            print("    kept %-7s 2025 %s | 2026 %s" % (name, wl(g25), wl(g26)), flush=True)
            if len(g25) >= 50 and len(g26) >= 50 and np.mean([r["net"] for r in g25]) > 0 and np.mean([r["net"] for r in g26]) > 0:
                cands.append(("%s %s kept %s (tp.5)" % (src, tf, name), len(g25) + len(g26)))
            if len(g25) >= 50 and len(g26) >= 50 and np.mean([r["rr1"] for r in g25]) > 0 and np.mean([r["rr1"] for r in g26]) > 0:
                cands.append(("%s %s kept %s (rr1)" % (src, tf, name), len(g25) + len(g26)))
        print("  FILTERS:", flush=True)
        F = [("ALL", lambda r: True), ("S (strong-in-favour)", lambda r: r["dr"] >= PCT_STRONG and r["al"]),
             ("S & kept>=70%", lambda r: r["dr"] >= PCT_STRONG and r["al"] and r["kept"] >= 0.7),
             ("S & kept>=90%", lambda r: r["dr"] >= PCT_STRONG and r["al"] and r["kept"] >= 0.9),
             ("kept>=90% (no delta)", lambda r: r["kept"] >= 0.9),
             ("kept>=70% (no delta)", lambda r: r["kept"] >= 0.7)]
        for name, keep in F:
            print("    %-22s 2025 %s | 2026 %s" % (name, wl([r for r in y(rows, 2025) if keep(r)]), wl([r for r in y(rows, 2026) if keep(r)])), flush=True)
        if S:
            sv = np.array([r["sld"] for r in S]); q = np.quantile(sv, [1 / 3, 2 / 3])
            print("  size-stratified inside S (sld tercile x kept), pooled — win%% (n):   kept<70%%   |   kept>=70%%", flush=True)
            for lab, lo, hi in (("LO", -1, q[0]), ("MID", q[0], q[1]), ("HI", q[1], 1e9)):
                g1 = [r for r in S if lo <= r["sld"] < hi and r["kept"] < 0.7]; g2 = [r for r in S if lo <= r["sld"] < hi and r["kept"] >= 0.7]
                f = lambda g: ("%5.1f%% (n%d)" % (100 * np.mean([x["win"] for x in g]), len(g))) if len(g) >= 15 else ("   -- (n%d)" % len(g))
                print("    %-4s %-18s %-18s" % (lab, f(g1), f(g2)), flush=True)
    print("\n" + "=" * 120, flush=True)
    print("CELLS positive in BOTH years with n>=50/yr (screen is bar-level; a survivor still needs 1m + daemon OOS):", flush=True)
    for c, n in cands:
        print("  %s  n=%d" % (c, n), flush=True)
    if not cands:
        print("  NONE", flush=True)


if __name__ == "__main__":
    main()
