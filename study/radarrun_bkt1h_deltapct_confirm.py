"""CONFIRM the one surviving cell of the DELTA>=P80 x TP sweep: BUCKET 1h, delta-strong breakouts, RR-style single TP
(bar-level screen: RR 1:1 n=474 win 57.4% avg +0.103% both years > 0, prop 55%; unfiltered control -0.006%).
Honest gates applied here:
  A. DISJOINT rank bands (standing rule: bands, not cumulative ladders) at RR 1:1 / 1:1.2 — gradient or cliff?
  B. CIRCULAR-SHIFT PLACEBO: shift the delta-rank series by random offsets (misaligned filter, same selection size) —
     if a misaligned filter produces the same lift, the 'edge' is selection noise. Reports the null distribution + p.
  C. 1-MINUTE first-touch resolution (clock_archive/1m) of the filtered set for RR 1:1 / 1:1.2 / 1:1.5 / 1:2 and the
     0.4% / 0.5% net fixed TPs, plus the ALL control at RR 1:1 — same-minute SL+TP -> conservative SL. Non-overlap on
     1m exit times. Prop FIRST-attempt MC (R0.4, daily 4%). Both-year split.
python study/radarrun_bkt1h_deltapct_confirm.py"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.radarrun_honest_deltapct_tp import load_fires, delta_rank, evaluate, fmt, ROOTS, FEE, SLIP, PCT_STRONG

SRC, TF = "bucket", "1h"
CONFIGS = [("RR 1:1", "rr", 1.0), ("RR 1:1.2", "rr", 1.2), ("RR 1:1.5", "rr", 1.5), ("RR 1:2", "rr", 2.0),
           ("0.4% net", "fix", 0.0044), ("0.5% net", "fix", 0.0054)]
NSHIFT = 200


def resolve_1m(s, e, sl, g, t0, T1, H1, L1, cap=30000):
    tp = e * (1 + s * g); i0 = int(np.searchsorted(T1, t0 - 1))
    if i0 >= len(T1):
        return None, None
    for j in range(i0, min(len(T1), i0 + cap)):
        sl_hit = (L1[j] <= sl) if s > 0 else (H1[j] >= sl)
        tp_hit = (H1[j] >= tp) if s > 0 else (L1[j] <= tp)
        if sl_hit:                                                # same-minute both -> conservative SL
            return s * (sl - e) / e - FEE - 2 * SLIP, T1[j]
        if tp_hit:
            return g - FEE - SLIP, T1[j]
    return None, None


def eval_1m(fires, kind, val, T1, H1, L1):
    from study.radarrun_hyro_prop import mc, day_blocks
    taken = []; busy = -1.0; unres = 0
    for (b, t, s, e, sl) in fires:
        if t < busy:
            continue
        sld = abs(e - sl) / e
        if sld <= 0:
            continue
        g = val if kind == "fix" else val * sld
        net, tx = resolve_1m(s, e, sl, g, t, T1, H1, L1)
        if net is None:
            unres += 1; continue
        taken.append((t, net, net / sld, datetime.fromtimestamp(t, tz=timezone.utc).year)); busy = tx
    if len(taken) < 10:
        return dict(n=len(taken)), unres
    nets = np.array([x[1] for x in taken]); rs = np.array([x[2] for x in taken]); yrs = np.array([x[3] for x in taken])
    eq = np.cumsum(0.4 * rs); dd = float((np.maximum.accumulate(eq) - eq).max())
    d = dict(n=len(taken), W=int((nets > 0).sum()), L=int((nets < 0).sum()), win=100 * (nets > 0).mean(),
             avg=nets.mean() * 100, avgR=rs.mean(), dd=dd,
             y25=nets[yrs == 2025].mean() * 100 if (yrs == 2025).any() else float("nan"),
             y26=nets[yrs == 2026].mean() * 100 if (yrs == 2026).any() else float("nan"))
    m = mc(day_blocks([(x[0], x[1], x[2]) for x in taken]), 0.4, 4.0, "R"); d["prop"] = m["p"]; d["propd"] = m["d50"]
    d["dd99"] = m["dd99"]
    return d, unres


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    fires = load_fires(SRC, TF)
    A = sorted(load_archive(TF, root=ROOTS[SRC], drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    D = np.abs(np.array([_f(b.get("buy_vol")) - _f(b.get("sell_vol")) for b in A]))
    ranks = np.array([delta_rank(D, f[0]) for f in fires])
    print("BUCKET 1h — CONFIRMATION of DELTA>=P80 x RR cells | badges %d\n" % len(fires), flush=True)

    # ---- A. disjoint rank bands (bar-level) ----
    print("== A. DISJOINT delta-rank bands (bar-level screen), single TP ==", flush=True)
    bands = [(0.0, 0.2), (0.2, 0.5), (0.5, 0.8), (0.8, 1.01)]
    for name, kind, val in (("RR 1:1", "rr", 1.0), ("RR 1:1.2", "rr", 1.2)):
        print("  %s:" % name, flush=True)
        for lo, hi in bands:
            sub = [f for f, r in zip(fires, ranks) if lo <= r < hi]
            print("    rank [%.1f,%.1f): %s" % (lo, min(hi, 1.0), fmt(evaluate(sub, Hi, Lo, C, kind, val))), flush=True)

    # ---- B. circular-shift placebo (bar-level) ----
    print("\n== B. CIRCULAR-SHIFT PLACEBO (bar-level), RR 1:1: misaligned delta-rank filter, same selection rule ==", flush=True)
    real = evaluate([f for f, r in zip(fires, ranks) if r >= PCT_STRONG], Hi, Lo, C, "rr", 1.0)["avg"]
    rng = np.random.default_rng(11); null = []
    for _ in range(NSHIFT):
        sh = int(rng.integers(50, len(ranks) - 50)); rr = np.roll(ranks, sh)
        d = evaluate([f for f, r in zip(fires, rr) if r >= PCT_STRONG], Hi, Lo, C, "rr", 1.0)
        if "avg" in d:
            null.append(d["avg"])
    null = np.array(null)
    print("  REAL filtered avg %+.3f%%  |  placebo null: mean %+.3f%%  p50 %+.3f%%  p95 %+.3f%%  max %+.3f%%  |  p(null >= real) = %.3f  (%d shifts)"
          % (real, null.mean(), np.percentile(null, 50), np.percentile(null, 95), null.max(), (null >= real).mean(), len(null)), flush=True)

    # ---- C. 1-minute confirmation ----
    print("\n== C. 1-MINUTE first-touch confirmation (conservative ties), non-overlap on 1m exits ==", flush=True)
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    del A1
    lit = [f for f, r in zip(fires, ranks) if r >= PCT_STRONG]
    for name, kind, val in CONFIGS:
        d, unres = eval_1m(lit, kind, val, T1, H1, L1)
        extra = ("  DD99(MC) %.1f%%  med-days %.0f" % (d["dd99"], d["propd"])) if "dd99" in d else ""
        print("  %-9s DELTA>=P80 : %s%s  (unresolved %d)" % (name, fmt(d), extra, unres), flush=True)
    d, unres = eval_1m(fires, "rr", 1.0, T1, H1, L1)
    print("  %-9s ALL control: %s  (unresolved %d)" % ("RR 1:1", fmt(d), unres), flush=True)


if __name__ == "__main__":
    main()
