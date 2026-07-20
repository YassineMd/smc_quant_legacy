"""Does the Mode-10 panel-2 (eff-agg) NON-LOCKED spread separate the MM/Skew strategy's winners from losers?

Non-locked spread = first-print eff-agg panel-2 lean, exactly as the live D-tier reads it:
    share = app.pivot_detect.eff_causal_share(buckets)   (causal, non-repainting)
    spread = (2*share - 1) * 100   in [-100,+100]   (+ = bull-dominant eff-agg, - = bear-dominant)
Computed over the FULL bucket stream (so each bar has the same lookback the terminal had live), then read at
each signal candle. Three views:
  aligned = spread * side   (>0 = eff-agg CONFIRMS the trade direction)   <- the meaningful test
  signed  = spread, per side
  |spread|= badge magnitude (strength either way)
Same 405 signals / fixed SL / RR sweep as study/mm_skew_rr_sweep.py. Mann-Whitney winners-vs-losers +
tercile win-rate + frozen-threshold split-half (the judge). CAUSAL throughout.

Run:  python study/mm_skew_spread.py
"""
from __future__ import annotations
import os, sys, math, statistics
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from study.archive_loader import load_archive
from app import pivot_detect as PD
from app.footprint_panel import profile_skewness
import study.mm_skew_strategy as S
import study.mm_skew_rr_sweep as RR
import study.mm_skew_winloss as WL

RRS = [0.5, 0.7, 1.0, 1.5]


def load_with_spread():
    _, raws, _ = load_archive("1h")
    A = []
    for r in raws:
        o = r.get("open_price"); c = r.get("close_price"); h = r.get("high"); l = r.get("low")
        if not o or not c or o <= 0 or h is None or l is None:
            continue
        d = dict(r)
        d["open"] = o; d["close"] = c                      # remap for region_state
        d["o"] = o; d["c"] = c; d["h"] = h; d["l"] = l     # study fields
        d["tv"] = r.get("target_vol", 0.0) or 0.0
        d["sk"] = profile_skewness(r.get("levels"))
        d["up"] = c > o; d["dn"] = c < o
        A.append(d)
    share = PD.eff_causal_share(A)
    spr = (2.0 * np.asarray(share, float) - 1.0) * 100.0
    for i in range(len(A)):
        A[i]["spread"] = float(spr[i])
    first = next((i for i in range(len(A)) if A[i]["tv"] >= 100000.0
                  and statistics.median([A[j]["tv"] for j in range(i, min(i + 30, len(A)))]) >= 100000.0), len(A))
    return A[first:]


def collect(M, rr):
    out = []
    for i in range(len(M) - 1):
        s = S.signal(M[i], S.CP_THR)
        if s == 0:
            continue
        res = RR.simulate_rr(M, i, s, rr, "sl")
        if res is None:
            continue
        sp = M[i]["spread"]
        out.append(dict(side=s, win=(res[0] == "TP"), outcome=res[0], i=i,
                        spread=sp, aligned=sp * s, absspread=abs(sp)))
    return out


def mw_line(sigs, key, label):
    W = [x[key] for x in sigs if x["win"]]; L = [x[key] for x in sigs if not x["win"]]
    if len(W) < 5 or len(L) < 5:
        print(f"    {label:9s}: too few"); return
    p, dm = WL.mann_whitney(W, L)
    star = " *" if p < 0.05 else ""
    print(f"    {label:9s}: WIN med {WL.q(W,.5):+6.1f} [{WL.q(W,.25):+6.1f},{WL.q(W,.75):+6.1f}]   "
          f"LOSE med {WL.q(L,.5):+6.1f} [{WL.q(L,.25):+6.1f},{WL.q(L,.75):+6.1f}]   MW p={p:.3f}{star}")


def tercile(sigs, key, label):
    xs = sorted(sigs, key=lambda z: z[key]); n = len(xs); c1 = n // 3; c2 = 2 * n // 3
    parts = []
    for nm, lo, hi in (("low", 0, c1), ("mid", c1, c2), ("high", c2, n)):
        g = xs[lo:hi]; wr = 100.0 * sum(1 for z in g if z["win"]) / len(g) if g else float("nan")
        parts.append(f"{nm}={wr:.0f}%[{xs[lo][key]:+.0f}..{xs[hi-1][key]:+.0f}]")
    print(f"    {label} win% by tercile: " + "  ".join(parts))


def split_half(sigs, key, thr_side):
    """frozen median-threshold split-half; thr_side=+1 -> above-median subset, -1 -> below-median."""
    ss = sorted(sigs, key=lambda z: z["i"])
    vals = [z[key] for z in ss]; thr = statistics.median(vals); mid = len(ss) // 2
    outs = []
    for sub in (ss[:mid], ss[mid:]):
        keep = [z for z in sub if (z[key] >= thr if thr_side > 0 else z[key] <= thr)]
        base = 100.0 * sum(1 for z in sub if z["win"]) / len(sub) if sub else float("nan")
        wr = 100.0 * sum(1 for z in keep if z["win"]) / len(keep) if keep else float("nan")
        outs.append((wr, len(keep), base))
    return thr, outs


def main():
    M = load_with_spread()
    print(f"mature 1h bars: {len(M)}   panel-2 NON-LOCKED spread attached (causal first-print).\n")
    hits = []
    for rr in RRS:
        sigs = collect(M, rr)
        nW = sum(1 for x in sigs if x["win"])
        print("=" * 98)
        print(f"R:R 1:{rr}   n={len(sigs)}  ({nW}W/{len(sigs)-nW}L = {100*nW/len(sigs):.1f}% win)")
        print("=" * 98)
        mw_line(sigs, "aligned", "aligned")            # spread*side: eff-agg confirms trade dir
        for side, sn in ((+1, "LONG"), (-1, "SHORT")):
            ss = [x for x in sigs if x["side"] == side]
            mw_line(ss, "spread", f"{sn} sig")
        mw_line(sigs, "absspread", "|spread|")
        tercile(sigs, "aligned", "aligned")
        # collect any significant for split-half
        Wa = [x["aligned"] for x in sigs if x["win"]]; La = [x["aligned"] for x in sigs if not x["win"]]
        pa, _ = WL.mann_whitney(Wa, La)
        if pa < 0.10:
            hits.append((rr, "aligned"))
        print()

    print("=" * 98)
    print("SPLIT-HALF (frozen median threshold on 'aligned' spread, above-median subset — must beat base BOTH halves)")
    print("=" * 98)
    for rr in RRS:
        sigs = collect(M, rr)
        thr, outs = split_half(sigs, "aligned", +1)
        print(f"  1:{rr} thr(aligned>={thr:+.1f}):  H1 sub={outs[0][0]:.0f}%(n{outs[0][1]}) base={outs[0][2]:.0f}%  |  "
              f"H2 sub={outs[1][0]:.0f}%(n{outs[1][1]}) base={outs[1][2]:.0f}%")


if __name__ == "__main__":
    main()
