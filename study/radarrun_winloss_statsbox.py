"""RADAR RUNNER — what separates WINNERS from LOSERS? (user 2026-08-23). Honest union badge sets (cached), bucket+clock x
15m/30m/1h, non-overlap taken(). WINNER = reaches the 0.5% NET TP (0.54% gross) before the candle SL; LOSER = SL first;
EOD dropped. Bar-level SL-first (pessimistic on winners). Features = every hamburger>Candles>Stats-Box parameter at the
BREAKOUT bar (causal; study/nowick_wall_winloss.feat_at), directional ones SIGNED by trade side, + candle geometry + the
stop distance sld% (flagged: a wider stop mechanically raises P(reach 0.5%) — geometry, not edge) + side.
Gates: AUC per feature split 2025/2026 (CONSISTENT only if same side of 0.5 both years, |AUC-.5|>=.03); disjoint
terciles per year with win% AND avg net; cross-combo tally (a feature must agree on >=4/6 combos to be believed).
python study/radarrun_winloss_statsbox.py"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.nowick_wall_winloss import feat_at, DIR, auc
from study.radarrun_honest_deltapct_tp import load_fires, resolve, ROOTS, FEE, SLIP

COMBOS = [("bucket", "15m"), ("clock", "15m"), ("bucket", "30m"), ("clock", "30m"), ("bucket", "1h"), ("clock", "1h")]
TP_G = 0.0054                                   # 0.5% net -> 0.54% gross
GEO = ["sld%", "body", "rng%", "close_pos", "wick_against", "side_long"]
MIN_AUC = 0.03


def rows_for(src, tf):
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    fires = load_fires(src, tf)
    A = sorted(load_archive(tf, root=ROOTS[src], drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A]); O = np.array([_f(b.get("open", b.get("open_price"))) for b in A])
    out = []; busy = -1; eod = 0
    for (b, t, s, e, sl) in fires:
        if b < busy:
            continue
        sld = abs(e - sl) / e
        if sld <= 0 or b < 30:
            continue
        net, xk = resolve(s, e, sl, TP_G, b, Hi, Lo, C)
        busy = xk
        if net > 0:
            win = 1
        elif abs(net - (-(sld) - FEE - 2 * SLIP)) < 1e-9:
            win = 0
        else:
            eod += 1; continue                                        # EOD close-out: neither
        d = feat_at(A, b)
        for k in list(d):
            if k in DIR and d[k] == d[k]:
                d[k] = d[k] * s
        rng = max(Hi[b] - Lo[b], 1e-9)
        d["sld%"] = sld * 100.0
        d["body"] = abs(C[b] - O[b]) / rng
        d["rng%"] = rng / C[b] * 100.0
        d["close_pos"] = ((C[b] - Lo[b]) / rng) if s > 0 else ((Hi[b] - C[b]) / rng)     # closed in breakout dir (1 = strong)
        d["wick_against"] = ((Hi[b] - max(O[b], C[b])) / rng) if s > 0 else ((min(O[b], C[b]) - Lo[b]) / rng)
        d["side_long"] = 1.0 if s > 0 else 0.0
        d["_win"] = win; d["_net"] = net * 100.0; d["_y"] = datetime.fromtimestamp(t, tz=timezone.utc).year
        out.append(d)
    return out, eod


def main():
    print("RADAR RUNNER winner/loser autopsy | WIN = reaches 0.5%% net TP before candle SL | stats-box features at breakout bar, signed by side\n", flush=True)
    tally = {}                                                        # feature -> list of (combo, direction)
    for src, tf in COMBOS:
        t0 = time.time()
        rows, eod = rows_for(src, tf)
        feats = [k for k in rows[0] if not k.startswith("_")]
        n25 = [r for r in rows if r["_y"] == 2025]; n26 = [r for r in rows if r["_y"] == 2026]
        print("=" * 118, flush=True)
        print("%s %s | taken n=%d (EOD dropped %d) | win(0.5%% TP) %.1f%% | 2025 n=%d win %.1f%% | 2026 n=%d win %.1f%%  (%.0fs)"
              % (src.upper(), tf, len(rows), eod, 100 * np.mean([r["_win"] for r in rows]),
                 len(n25), 100 * np.mean([r["_win"] for r in n25]) if n25 else 0,
                 len(n26), 100 * np.mean([r["_win"] for r in n26]) if n26 else 0, time.time() - t0), flush=True)
        res = []
        for f in feats:
            a25, _ = auc(np.array([r[f] for r in n25], float), np.array([r["_win"] for r in n25]))
            a26, _ = auc(np.array([r[f] for r in n26], float), np.array([r["_win"] for r in n26]))
            if a25 != a25 or a26 != a26:
                continue
            cons = (a25 - 0.5) * (a26 - 0.5) > 0 and min(abs(a25 - 0.5), abs(a26 - 0.5)) >= MIN_AUC
            res.append((f, a25, a26, cons))
            if cons:
                tally.setdefault(f, []).append(("%s %s" % (src, tf), "+" if a25 > 0.5 else "-"))
        res.sort(key=lambda z: -abs((z[1] + z[2]) / 2 - 0.5))
        print("  %-26s %7s %7s  %s" % ("feature (signed by side)", "AUC25", "AUC26", "consistent?"), flush=True)
        for f, a25, a26, cons in res[:14]:
            print("  %-26s %7.3f %7.3f  %s" % (f, a25, a26, "<-- CONSISTENT" if cons else ""), flush=True)
        # disjoint terciles for the consistent ones (win% + avg net, per year)
        for f, a25, a26, cons in res:
            if not cons:
                continue
            xs = np.array([r[f] for r in rows], float); m = ~np.isnan(xs)
            q = np.quantile(xs[m], [1 / 3, 2 / 3])
            print("    %s terciles (q=%.3f / %.3f):" % (f, q[0], q[1]), flush=True)
            for Y, yr in ((2025, n25), (2026, n26)):
                parts = []
                for lab, sel in (("LO", lambda v: v < q[0]), ("MID", lambda v: q[0] <= v < q[1]), ("HI", lambda v: v >= q[1])):
                    g = [r for r in yr if r[f] == r[f] and sel(r[f])]
                    if g:
                        parts.append("%s %.0f%%/%+.3f%%(n%d)" % (lab, 100 * np.mean([r["_win"] for r in g]), np.mean([r["_net"] for r in g]), len(g)))
                print("       %d: %s" % (Y, "   ".join(parts)), flush=True)
    print("\n" + "=" * 118, flush=True)
    print("CROSS-COMBO TALLY — features CONSISTENT (both years) on >= 4 of 6 combos, with direction (+ = higher value -> more winners):", flush=True)
    hits = sorted(tally.items(), key=lambda kv: -len(kv[1]))
    any_ = False
    for f, lst in hits:
        if len(lst) >= 4:
            any_ = True
            print("  %-26s %d/6  %s" % (f, len(lst), "  ".join("%s(%s)" % (c, d) for c, d in lst)), flush=True)
    if not any_:
        print("  NONE — no stats-box parameter separates winners from losers consistently across timeframes.", flush=True)
    print("\n  (features consistent on 2-3 combos:)", flush=True)
    for f, lst in hits:
        if 2 <= len(lst) < 4:
            print("  %-26s %d/6  %s" % (f, len(lst), "  ".join("%s(%s)" % (c, d) for c, d in lst)), flush=True)


if __name__ == "__main__":
    main()
