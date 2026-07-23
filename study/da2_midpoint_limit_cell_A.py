"""IMPLEMENTATION A -- pre-specified cell: DA2-REVERSION v1.1 signals, FROZEN close-derived barriers,
resting LIMIT entry at the signal bucket's MIDPOINT, K=1 fill, non-overlap chain, displacement-stratified
permutation null.

Written from the written spec. Own walk (no reuse of the taken()/sim() machinery in the existing harnesses);
V11.build()/V11.GATE are used only as the SIGNAL DEFINITION, and study.mm_skew_feature_matrix only for the
maturity index. Two published numbers are reproduced first as a harness check.

Run: python study/da2_midpoint_limit_cell_A.py
"""
from __future__ import annotations
import os, sys, bisect, math
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from study.archive_loader import load_archive
from app.pivot_detect import eff_causal_share
import study.mm_skew_feature_matrix as FM
import study.da2_reversion_v11_validate as V11

SL = 0.008          # frozen 0.8% stop, close-derived
TP = 0.010          # frozen 1.0% target, close-derived
FEE_PCT = 0.08      # round-trip, in percent
SEED = 20260722
NPERM = 2000

MIN_SUBS = 12       # 926-universe reconstruction constants (validation (ii) only)
WIN = 30
MIN_OBS = 20


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


# ---------------------------------------------------------------------------
# my own exit scan / chained walk, close entry, two-valued P&L (validation only)
# ---------------------------------------------------------------------------
def _resolve(bars, side, stop, targ, j0):
    """First bar from j0 (skipping non-ok) that touches a level. Both -> STOP. None if unresolved."""
    for j in range(j0, len(bars)):
        b = bars[j]
        if not b["ok"]:
            continue
        htp = (b["h"] >= targ) if side > 0 else (b["l"] <= targ)
        hsl = (b["l"] <= stop) if side > 0 else (b["h"] >= stop)
        if htp and hsl:
            return False, j
        if htp:
            return True, j
        if hsl:
            return False, j
    return None


def walk_close(bars, sigs):
    """sigs = [(i, side)] ascending. Entry at bars[i]['c'], frozen +-pct barriers, non-overlap chain."""
    last = -1
    out = []
    for i, side in sigs:
        if i <= last or side == 0:
            continue
        e = bars[i]["c"]
        stop = e * (1 - SL) if side > 0 else e * (1 + SL)
        targ = e * (1 + TP) if side > 0 else e * (1 - TP)
        r = _resolve(bars, side, stop, targ, i + 1)
        if r is None:
            continue
        win, j = r
        out.append(dict(i=i, side=side, win=win, net=100.0 * (TP if win else -SL) - FEE_PCT))
        last = j
    return out


# ---------------------------------------------------------------------------
# the pre-specified cell: frozen close-derived barriers + midpoint resting limit
# ---------------------------------------------------------------------------
def precompute(bars, i, side, k_inf=False, stats=None):
    """Chain-independent evaluation of one signal bucket. Returns None if never filled / unresolved.

    Barriers are FROZEN at close-derived absolute price levels. Only the fill varies.
    K=1   : filled iff bars[i+1] trades through the midpoint; exit scan starts at i+2.
    K=inf : the order rests forward; the first later bucket containing the midpoint fills it (exit scan
            starts at that bucket+1). If a bucket touches a barrier LEVEL before the order fills, the
            order is abandoned (that is the reading of 'before the barriers resolve').
    """
    e = bars[i]["c"]
    stop = e * (1 - SL) if side > 0 else e * (1 + SL)
    targ = e * (1 + TP) if side > 0 else e * (1 - TP)
    lim = 0.5 * (bars[i]["h"] + bars[i]["l"])

    fill_bar = None
    if not k_inf:
        j = i + 1
        if j < len(bars) and bars[j]["l"] <= lim <= bars[j]["h"]:
            fill_bar = j
    else:
        for j in range(i + 1, len(bars)):
            b = bars[j]
            if not b["ok"]:
                continue
            if b["l"] <= lim <= b["h"]:
                fill_bar = j
                break
            htp = (b["h"] >= targ) if side > 0 else (b["l"] <= targ)
            hsl = (b["l"] <= stop) if side > 0 else (b["h"] >= stop)
            if htp or hsl:
                break                                   # barriers resolved with no fill -> abandon
    if fill_bar is None:
        return None
    if stats is not None:
        stats["filled"] += 1

    r = _resolve(bars, side, stop, targ, fill_bar + 1)
    if r is None:
        return None
    win, jx = r
    if side > 0:
        gross = 100.0 * ((targ / lim - 1.0) if win else (stop / lim - 1.0))
    else:
        gross = 100.0 * ((lim / targ - 1.0) if win else (lim / stop - 1.0))
    impr = 100.0 * ((e - lim) / e if side > 0 else (lim - e) / e)   # +ve = fill better than the close
    return dict(i=i, side=side, win=win, gross=gross, net=gross - FEE_PCT, jx=jx, fill=lim,
                close=e, impr=impr)


def chain(pre_by_i, order):
    """order = signal bucket indices ascending. Dropped (unfilled/unresolved) signals do not advance `last`."""
    last = -1
    out = []
    for i in order:
        if i <= last:
            continue
        r = pre_by_i.get(i)
        if r is None:
            continue
        out.append(r)
        last = r["jx"]
    return out


# ---------------------------------------------------------------------------
# universes
# ---------------------------------------------------------------------------
def build_926(bars):
    """The 926-bucket universe used for the published 'fade all' cell: mature, ok, >=MIN_SUBS 1m subs,
    computable half split, computable R_h2 (30-bucket rolling window, >=20 obs), non-doji."""
    _, H, _ = load_archive("1h")
    _, M, _ = load_archive("1m")
    mst = [_f(x.get("start_time")) for x in M]
    _, first, _, _ = FM.build()

    recs = []
    for i in range(len(H)):
        b = H[i]
        if i < first or not bars[i]["ok"]:
            continue
        if _f(b.get("curr_vol")) <= 0:
            continue
        a = bisect.bisect_left(mst, _f(b.get("start_time")))
        z = bisect.bisect_left(mst, _f(b.get("end_time")))
        subs = M[a:z]
        if len(subs) < MIN_SUBS:
            continue
        vols = [_f(s.get("curr_vol")) for s in subs]
        tot = sum(vols)
        if tot <= 0:
            continue
        cum = 0.0
        k = None
        for jj, v in enumerate(vols):
            cum += v
            if cum >= 0.5 * tot:
                k = jj
                break
        if k is None or k + 1 >= len(subs):
            continue
        h1 = subs[:k + 1]
        h2 = subs[k + 1:]
        dv2 = sum(_f(s.get("buy_vol")) - _f(s.get("sell_vol")) for s in h2)
        p2o = _f(h2[0].get("open_price"))
        p2c = _f(h2[-1].get("close_price"))
        p1o = _f(h1[0].get("open_price"))
        p1c = _f(h1[-1].get("close_price"))
        if min(p1o, p1c, p2o, p2c) <= 0:
            continue
        recs.append(dict(i=i, dp2=100.0 * (p2c - p2o) / p2o, dv2=dv2))

    U = []
    for idx, r in enumerate(recs):
        lo = max(0, idx - WIN)
        hist = recs[lo:idx]
        if len(hist) < MIN_OBS:
            continue
        P = np.array([q["dp2"] for q in hist], float)
        V = np.array([q["dv2"] for q in hist], float)
        if not (P.std(ddof=1) > 0 and V.std(ddof=1) > 0):
            continue
        i = r["i"]
        if bars[i]["c"] == bars[i]["o"]:
            continue                                    # doji
        U.append(i)
    return U


def main():
    bars_v, sigs = V11.build()
    bars = [dict(o=b["o"], c=b["c"], h=b["h"], l=b["l"], t=b["t"], ok=b["ok"]) for b in bars_v]

    print("=" * 86)
    print("HARNESS VALIDATION")
    print("=" * 86)

    # (i) v1.1 chained at close, frozen brackets  -> n 99, 59W, 59.6%, +0.1927
    v11 = [(s["i"], s["side"]) for s in sigs if V11.GATE(s)]
    T = walk_close(bars, v11)
    n = len(T); w = sum(1 for t in T if t["win"]); m = float(np.mean([t["net"] for t in T]))
    ok1 = (n == 99 and w == 59 and abs(m - 0.1927) < 5e-4)
    print("  (i)  v1.1 close-entry chained : n=%d  W=%d  win %.1f%%  exp %+.4f%%   [target 99 / 59 / 59.6 / +0.1927]  %s"
          % (n, w, 100.0 * w / n, m, "OK" if ok1 else "MISMATCH"))

    # (ii) fade-all chained at close on the 926 universe -> n 158, 51.3%, +0.0428
    U = build_926(bars)
    fade = [(i, -1 if bars[i]["c"] > bars[i]["o"] else 1) for i in U]
    T2 = walk_close(bars, fade)
    n2 = len(T2); w2 = sum(1 for t in T2 if t["win"]); m2 = float(np.mean([t["net"] for t in T2]))
    ok2 = (n2 == 158 and abs(100.0 * w2 / n2 - 51.3) < 0.1 and abs(m2 - 0.0428) < 5e-4)
    print("  (ii) fade-all close chained   : n=%d  W=%d  win %.1f%%  exp %+.4f%%   [target 158 / 51.3 / +0.0428]  %s"
          % (n2, w2, 100.0 * w2 / n2, m2, "OK" if ok2 else "MISMATCH"))
    print("       926-universe size = %d" % len(U))
    if not (ok1 and ok2):
        print("\nSTOP: harness does not reproduce the published numbers.")
        return

    # ------------------------------------------------------------------ CELL
    print()
    print("=" * 86)
    print("PRE-SPECIFIED CELL -- frozen close-derived barriers, resting limit at the bucket MIDPOINT")
    print("=" * 86)
    qual = [s for s in sigs if V11.GATE(s)]
    qi = [s["i"] for s in qual]
    side_by_i = {s["i"]: s["side"] for s in qual}
    print("  signals qualifying (V11.GATE) : %d" % len(qual))

    for tag, kinf in (("K=1  (DECLARED CELL)", False), ("K=inf (secondary)", True)):
        pre = {}
        st = dict(filled=0)
        for i in qi:
            r = precompute(bars, i, side_by_i[i], k_inf=kinf, stats=st)
            if r is not None:
                pre[i] = r
        nfill = st["filled"]
        TT = chain(pre, qi)
        r = np.array([t["net"] for t in TT], float)
        g = np.array([t["gross"] for t in TT], float)
        wm = g[r > 0] if False else np.array([t["gross"] for t in TT if t["win"]], float)
        lm = np.array([t["gross"] for t in TT if not t["win"]], float)
        nw = len(wm)
        be = 100.0 * (abs(lm.mean()) + FEE_PCT) / (wm.mean() + abs(lm.mean())) if nw and len(lm) else float("nan")
        impr = np.array([t["impr"] for t in TT], float)
        print("\n  --- %s ---" % tag)
        print("    filled / qualified      : %d / %d   (fill rate %.1f%%)" % (nfill, len(qi), 100.0 * nfill / len(qi)))
        print("    filled AND resolvable   : %d   (%d dropped unresolved at end of data)" % (len(pre), nfill - len(pre)))
        print("    chain took              : %d   (%d W / %d L, win %.1f%%)"
              % (len(TT), nw, len(TT) - nw, 100.0 * nw / len(TT) if len(TT) else 0.0))
        print("    NET EXPECTANCY          : %+.4f%% / trade      total %+.2f%%" % (r.mean(), r.sum()))
        print("    mean win (gross)        : %+.4f%%     mean loss (gross) : %+.4f%%" % (wm.mean(), lm.mean()))
        print("    implied break-even win  : %.2f%%   (realised %.2f%%)" % (be, 100.0 * nw / len(TT)))
        print("    mean fill vs close      : %+.4f%%   (+ = better than close)  median %+.4f%%"
              % (impr.mean(), float(np.median(impr))))
        if not kinf:
            keep = (pre, TT, r)
    pre1, T1, r1 = keep
    real_exp = float(r1.mean())

    # ------------------------------------------------------------------ NULL
    print()
    print("=" * 86)
    print("NULL -- %d displacement-decile-stratified fade cohorts, identical machinery (seed %d)"
          % (NPERM, SEED))
    print("=" * 86)
    pool = [s["i"] for s in sigs if bars[s["i"]]["c"] != bars[s["i"]]["o"]]
    disp = {}
    for i in pool:
        b = bars[i]
        d = 1.0 if b["c"] > b["o"] else -1.0
        disp[i] = d * (b["c"] - 0.5 * (b["h"] + b["l"])) / b["c"] * 100.0
    dv = np.array([disp[i] for i in pool], float)
    edges = np.quantile(dv, np.linspace(0, 1, 11))
    edges[0] = -np.inf
    edges[-1] = np.inf

    def dec(x):
        return int(np.searchsorted(edges, x, side="right") - 1)

    pool_by_dec = {k: [] for k in range(10)}
    for i in pool:
        pool_by_dec[min(9, max(0, dec(disp[i])))].append(i)
    real_dec = {k: 0 for k in range(10)}
    for i in qi:
        real_dec[min(9, max(0, dec(disp[i])))] += 1

    print("  pool (mature, non-doji, fade-able) : %d buckets" % len(pool))
    print("  pool displacement  mean %+.4f%%  median %+.4f%%" % (dv.mean(), float(np.median(dv))))
    print("  decile counts pool / real S2 : %s" %
          " ".join("%d:%d/%d" % (k + 1, len(pool_by_dec[k]), real_dec[k]) for k in range(10)))

    # chain-independent precompute over the WHOLE pool (side = fade), reused by every draw
    pre_pool = {}
    for i in pool:
        side = -1 if bars[i]["c"] > bars[i]["o"] else 1
        r = precompute(bars, i, side, k_inf=False)
        if r is not None:
            pre_pool[i] = r

    rng = np.random.default_rng(SEED)
    arrs = {k: np.array(pool_by_dec[k]) for k in range(10)}
    nulls = np.empty(NPERM, float)
    for it in range(NPERM):
        pick = []
        for k in range(10):
            c = real_dec[k]
            if c:
                pick.append(rng.choice(arrs[k], size=c, replace=False))
        idx = np.sort(np.concatenate(pick))
        last = -1
        tot = 0.0
        cnt = 0
        for i in idx:
            ii = int(i)
            if ii <= last:
                continue
            rr = pre_pool.get(ii)
            if rr is None:
                continue
            tot += rr["net"]
            cnt += 1
            last = rr["jx"]
        nulls[it] = tot / cnt if cnt else 0.0

    pct = 100.0 * float(np.mean(nulls < real_exp))
    print("\n  real net expectancy   : %+.4f%% / trade  (n=%d)" % (real_exp, len(T1)))
    print("  null mean / sd        : %+.4f%%  /  %.4f" % (nulls.mean(), nulls.std(ddof=1)))
    print("  null p5 / p50 / p95   : %+.4f%%  /  %+.4f%%  /  %+.4f%%"
          % (np.percentile(nulls, 5), np.percentile(nulls, 50), np.percentile(nulls, 95)))
    print("  null min / max        : %+.4f%%  /  %+.4f%%" % (nulls.min(), nulls.max()))
    print("  EMPIRICAL PERCENTILE  : %.1f     P(null >= real) = %.4f"
          % (pct, float(np.mean(nulls >= real_exp))))
    print("  VERDICT               : %s"
          % ("OUTSIDE the null band (>=95th pct)" if pct >= 95.0 else
             "INSIDE the null band -- nothing beyond a displacement-matched random fade cohort"))


if __name__ == "__main__":
    main()
