"""RADAR RUNNER — HONEST TP sweep on DELTA-STRONG breakouts (user 2026-08-23). Same union badge sets as the all-tf screen
(cached study/out/rr_union_*.json = exactly what the terminal persists), same candle-anchored SL, SINGLE TP (no scale-out,
no BE move), 7 TP configs: 0.2% / 0.4% / 0.5% NET (terminal convention: net = gross - 0.04% maker RT) and RR 1:1 / 1:1.2 /
1:1.5 / 1:2 (TP distance = k x SL distance).

FILTER = the Volume pane's Delta-mode 'strong' tier, computed exactly as the pane draws it (causal): |buy_vol - sell_vol|
of the breakout bar ranked >= VOL_PCT_STRONG (0.80) against its previous VOL_PCT_WIN-1 (49) bars. Three rows per TP:
  LITERAL  = |delta| >= P80 (magnitude only, as the pane grades it)
  ALIGNED  = |delta| >= P80 AND sign(delta) == breakout side
  ALL      = unfiltered control (is the filter adding anything?)
Bar-level screen (SL-first within a bar = conservative; calibrated ~+0.01%/trade optimistic on the as-badge bracket),
non-overlap taken(), fees 0.04% RT + 0.03% slip per taker leg, prop FIRST-attempt MC (R0.4, daily 4%). 7 combos, no 5m/1m.
python study/radarrun_honest_deltapct_tp.py"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "study", "out")
ROOTS = {"clock": "study/clock_archive", "bucket": "study/recon_archive"}
COMBOS = [("bucket", "30m"), ("bucket", "4h"), ("clock", "1h"), ("bucket", "1h"), ("clock", "30m"), ("clock", "15m"), ("bucket", "15m")]
FEE, SLIP, HOLD = 0.0004, 0.0003, 400
PCT_WIN, PCT_STRONG = 50, 0.80
TPS = [("0.2% net", "fix", 0.0024), ("0.4% net", "fix", 0.0044), ("0.5% net", "fix", 0.0054),
       ("RR 1:1", "rr", 1.0), ("RR 1:1.2", "rr", 1.2), ("RR 1:1.5", "rr", 1.5), ("RR 1:2", "rr", 2.0)]
MINN = 40


def load_fires(src, tf):
    legacy = os.path.join(OUT, "rr30mbkt_live_fires_union.json")
    if src == "bucket" and tf == "30m" and os.path.exists(legacy):
        return json.load(open(legacy))
    return json.load(open(os.path.join(OUT, "rr_union_%s_%s_s1.json" % (src, tf))))


def delta_rank(D, b):
    """Pane-identical trailing percentile rank of |delta| at bar b vs its previous PCT_WIN-1 bars (causal)."""
    w = D[max(0, b - PCT_WIN + 1):b + 1]
    if len(w) < 2:
        return 0.5
    return float((w[:-1] < w[-1]).sum() + (w[:-1] == w[-1]).sum() * 0) / (len(w) - 1)


def resolve(s, e, sl, g, k, Hi, Lo, C):
    """Single-TP bar-level resolution, SL checked FIRST within a bar. Returns (net, exit_k)."""
    n = len(Hi); tp = e * (1 + s * g)
    for j in range(k + 1, min(n, k + 1 + HOLD)):
        if (Lo[j] <= sl) if s > 0 else (Hi[j] >= sl):
            return s * (sl - e) / e - FEE - 2 * SLIP, j
        if (Hi[j] >= tp) if s > 0 else (Lo[j] <= tp):
            return g - FEE - SLIP, j
    j = min(n - 1, k + HOLD)
    return s * (C[j] - e) / e - FEE - 2 * SLIP, j


def evaluate(fires, Hi, Lo, C, kind, val):
    from study.radarrun_hyro_prop import mc, day_blocks
    taken = []; busy = -1
    for (b, t, s, e, sl) in fires:
        if b < busy:
            continue
        sld = abs(e - sl) / e
        if sld <= 0:
            continue
        g = val if kind == "fix" else val * sld
        net, xk = resolve(s, e, sl, g, b, Hi, Lo, C)
        taken.append((t, net, net / sld, datetime.fromtimestamp(t, tz=timezone.utc).year)); busy = xk
    if len(taken) < 10:
        return dict(n=len(taken))
    nets = np.array([x[1] for x in taken]); rs = np.array([x[2] for x in taken]); yrs = np.array([x[3] for x in taken])
    eq = np.cumsum(0.4 * rs); dd = float((np.maximum.accumulate(eq) - eq).max())
    d = dict(n=len(taken), W=int((nets > 0).sum()), L=int((nets < 0).sum()), win=100 * (nets > 0).mean(),
             avg=nets.mean() * 100, avgR=rs.mean(), dd=dd,
             y25=nets[yrs == 2025].mean() * 100 if (yrs == 2025).any() else float("nan"),
             y26=nets[yrs == 2026].mean() * 100 if (yrs == 2026).any() else float("nan"))
    if len(taken) >= MINN:
        m = mc(day_blocks([(x[0], x[1], x[2]) for x in taken]), 0.4, 4.0, "R"); d["prop"] = m["p"]; d["propd"] = m["d50"]
    return d


def fmt(d):
    if d.get("n", 0) < 10:
        return "n=%-5d (too few)" % d.get("n", 0)
    p = ("%5.1f%%" % d["prop"]) if "prop" in d else "  n/a "
    return "n=%-5d W%-5d L%-5d win %5.1f%%  avg %+.3f%%  R %+.3f  DD %5.1f%%  prop %s | 25 %+.3f%% 26 %+.3f%%" % (
        d["n"], d["W"], d["L"], d["win"], d["avg"], d["avgR"], d["dd"], p, d["y25"], d["y26"])


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    print("RADAR RUNNER — DELTA>=P80 breakouts x TP sweep | same candle-SL, SINGLE TP | bar-level screen | non-overlap | fees+slip\n", flush=True)
    best = []
    for src, tf in COMBOS:
        t0 = time.time()
        fires = load_fires(src, tf)
        A = sorted(load_archive(tf, root=ROOTS[src], drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
        Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
        C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
        Dl = np.array([_f(b.get("buy_vol")) - _f(b.get("sell_vol")) for b in A]); D = np.abs(Dl)
        lit = [f for f in fires if delta_rank(D, f[0]) >= PCT_STRONG]
        ali = [f for f in lit if (Dl[f[0]] >= 0) == (f[2] > 0)]
        print("=" * 150, flush=True)
        print("%s %s | badges %d -> DELTA>=P80 literal %d (%.0f%%) -> aligned %d (%.0f%%)" % (
            src.upper(), tf, len(fires), len(lit), 100 * len(lit) / max(1, len(fires)), len(ali), 100 * len(ali) / max(1, len(fires))), flush=True)
        for name, kind, val in TPS:
            for lab, fs in (("LITERAL", lit), ("ALIGNED", ali), ("ALL    ", fires)):
                d = evaluate(fs, Hi, Lo, C, kind, val)
                print("  %-9s %-8s %s" % (name, lab, fmt(d)), flush=True)
                if "avg" in d and lab != "ALL    ":
                    best.append((d["avg"], src, tf, name, lab, d))
            print("", flush=True)
        print("  (%.0fs)" % (time.time() - t0), flush=True)
    print("=" * 150, flush=True)
    print("TOP filtered cells by avg net (screen is ~+0.01%/trade optimistic; needs BOTH years > 0 and n >= 100 to matter):", flush=True)
    for avg, src, tf, name, lab, d in sorted(best, key=lambda z: -z[0])[:12]:
        flag = "  <-- both-year positive" if (d["y25"] > 0 and d["y26"] > 0 and d["n"] >= 100) else ""
        print("  %-7s %-4s %-9s %-8s %s%s" % (src, tf, name, lab, fmt(d), flag), flush=True)


if __name__ == "__main__":
    main()
