"""MMXSKEW research sprint (2026-07-20) — three baseline experiments + the mov_mag_ratio fine sweep.

Protocol: v1.1 WITH-POC signal (frozen), one-at-a-time, RR 1:1.0 & 1:1.5, net @0.08% fee. Full 149-signal set,
or the 114 with 1m sub-bucket coverage where da2 is needed. Split-half PASS = both time-halves net-positive.

  EXP1  Dynamic vol ratio:  mov_mag_ratio = mov_mag / trailing-EMA50(mov_mag)  vs static mov_mag>=25 (gate run<=4).
  EXP2  Long delta audit:   how many longs the delta<15 filter blocks, and whether those are worth unlocking.
  EXP3  Composite score S = 1(run<=4)+1(mov_mag>=25)+1(da2>0 asymmetric); S>=2 vs S==3 vs baseline (114 covered).
  SWEEP mov_mag_ratio threshold T in [0.70,1.60] step 0.05 (incl. T<1.0 to test low-vol harvesting).

Findings (in-sample, one regime): EXP1 vol-ratio beats static (esp @1:1.5); EXP2 the blocked longs LOSE money
(keep delta<15); EXP3 soft S>=2 dilutes, quality only at S==3 (the hard AND). The vol-ratio is the lead.

Run: python study/mm_skew_sprint.py
"""
from __future__ import annotations
import os, sys, bisect
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import study.mm_skew_feature_matrix as FM
import study.mm_skew_rr_sweep as RR
from study.archive_loader import load_archive


def _mm(b):
    ref = b["l"] if b["c"] > b["o"] else (b["h"] if b["c"] < b["o"] else b["o"])
    return ((((b["c"] * 100) / ref) - 100) ** 2) * 100 if ref > 0 else 0.0


def build():
    A, first, _, _ = FM.build()
    _, m1, _ = load_archive("1m")
    subb = sorted(m1, key=lambda r: float(r.get("start_time", 0))); ssv = [float(r.get("start_time", 0)) for r in subb]

    def cons(s0, s1):
        j = bisect.bisect_left(ssv, s0); out = []
        while j < len(subb) and ssv[j] <= s1:
            out.append(subb[j]); j += 1
        return out

    def da2f(subs):
        # da2 = (delta_total - 2*delta_h1)/curr_vol -> the NUMERATOR total is total DELTA (sum of dels), only
        # the denominator is volume. (Pre-2026-07-23 both used sum(vols), offsetting da2 by ~+1 -> da2>0 vacuous.)
        vols = [float(x.get("curr_vol", 0)) for x in subs]; dels = [float(x.get("buy_vol", 0)) - float(x.get("sell_vol", 0)) for x in subs]
        vtot = sum(vols); dtot = sum(dels)
        if vtot <= 0 or len(subs) < 12:
            return None
        half = 0.5 * vtot; cum = 0.0; run = 0.0
        for v, d in zip(vols, dels):
            run += d; cum += v
            if cum >= half:
                return (dtot - 2 * run) / vtot
        return (dtot - 2 * run) / vtot

    mm_all = [_mm(b) for b in A]; ratio = [1.0] * len(A); ema = None
    for k in range(len(A)):                          # trailing EMA-50, excludes current bucket
        ratio[k] = (mm_all[k] / ema) if (ema and ema > 0) else 1.0
        ema = mm_all[k] if ema is None else mm_all[k] * (2 / 51) + ema * (1 - 2 / 51)

    def sig(b):
        if b["sk"] is None:
            return 0
        if b["up"] and b["sk"] > 0 and b["spread"] >= 35 and b["c"] > b["base"] and b["delta"] < 15:
            return 1
        if b["dn"] and b["sk"] < 0 and b["spread"] <= -35 and b["c"] < b["base"]:
            return -1
        return 0

    SIG = []; rc = 0; prev = 0
    for i in range(first, len(A) - 1):
        s = sig(A[i])
        if s == 0:
            continue
        rc = rc + 1 if s == prev else 1; prev = s
        b = A[i]; d = da2f(cons(b["start_time"], b["end_time"]))
        SIG.append(dict(i=i, side=s, t=float(b["start_time"]), run=rc, mm=mm_all[i], ratio=ratio[i],
                        da2=d, delta=b["delta"], spread=b["spread"]))
    # long-base signals (bull+skew>0+spread>=35+close>base — the long WITHOUT the delta filter)
    LB = [dict(i=i, side=1, t=float(A[i]["start_time"]), delta=A[i]["delta"], spread=A[i]["spread"])
          for i in range(first, len(A) - 1)
          if A[i]["sk"] is not None and A[i]["up"] and A[i]["sk"] > 0 and A[i]["spread"] >= 35 and A[i]["c"] > A[i]["base"]]
    return A, SIG, [sg for sg in SIG if sg["da2"] is not None], LB


def walk(A, lst, gate, rr, side=None):
    last = -1; tr = []
    for sg in lst:
        if sg["i"] <= last or (side is not None and sg["side"] != side) or not gate(sg):
            continue
        res = RR.simulate_rr(A, sg["i"], sg["side"], rr, "sl")
        if res is None:
            continue
        tr.append((sg["side"], res[0] == "TP", res[1] * 100 - 0.08, sg["t"])); last = res[2]
    return sorted(tr, key=lambda z: z[3])


def rep(tr):
    if not tr:
        return None
    n = len(tr); w = sum(1 for t in tr if t[1]); nets = [t[2] for t in tr]; l = sum(1 for t in tr if t[0] > 0)
    m = n // 2; h1 = tr[:m]; h2 = tr[m:]
    hw = lambda x: 100 * np.mean([1 if t[1] else 0 for t in x]) if x else 0
    nt = lambda x: sum(t[2] for t in x)
    wn = sum(x for x in nets if x > 0); ls = abs(sum(x for x in nets if x <= 0))
    return dict(n=n, l=l, s=n - l, win=100 * w / n, exp=float(np.mean(nets)), pf=(wn / ls if ls else 9.9),
                h1=hw(h1), h2=hw(h2), sh=("PASS" if nt(h1) > 0 and nt(h2) > 0 else "FAIL"))


def line(A, name, lst, gate, side=None):
    r10 = rep(walk(A, lst, gate, 1.0, side)); r15 = rep(walk(A, lst, gate, 1.5, side))
    if not r10:
        return "| %s | 0 | — | — | — | — |" % name
    return ("| %s | %d (%dL/%dS) | %.0f%% / %.0f%% | %+.2f%% / %+.2f%% | %.1f/%.1f | %s/%s |"
            % (name, r10["n"], r10["l"], r10["s"], r10["win"], (r15["win"] if r15 else 0),
               r10["exp"], (r15["exp"] if r15 else 0), r10["pf"], (r15["pf"] if r15 else 0),
               r10["sh"], (r15["sh"] if r15 else "—")))


def main():
    A, SIG, COV, LB = build()
    H = "| rule | n (L/S) | win 1.0/1.5 | exp 1.0/1.5 | PF 1.0/1.5 | SplitHalf |"
    SEP = "|---|---|---|---|---|---|"
    print("## EXPERIMENT 1 — Dynamic vol ratio (full 149; gate = run<=4 & [threshold])"); print(H); print(SEP)
    print(line(A, "static mov_mag>=25", SIG, lambda sg: sg["run"] <= 4 and sg["mm"] >= 25))
    for T in (1.10, 1.25, 1.50):
        print(line(A, "ratio>=%.2f" % T, SIG, lambda sg, T=T: sg["run"] <= 4 and sg["ratio"] >= T))
    print("\n## EXPERIMENT 2 — Long delta filter (long-base, long-only, no gate)")
    print("Long-base: %d total; blocked solely by delta>=15: %d" % (len(LB), sum(1 for sg in LB if sg["delta"] >= 15)))
    print(H.replace("(L/S)", "(L)")); print(SEP)
    print(line(A, "baseline delta<15", LB, lambda sg: sg["delta"] < 15))
    print(line(A, "A: no delta filter", LB, lambda sg: True))
    print(line(A, "B: delta<15 OR spread>=50", LB, lambda sg: sg["delta"] < 15 or sg["spread"] >= 50))
    print(line(A, "unlocked (delta>=15)", LB, lambda sg: sg["delta"] >= 15))
    print(line(A, "unlocked-B (d>=15 & sp>=50)", LB, lambda sg: sg["delta"] >= 15 and sg["spread"] >= 50))
    print("\n## EXPERIMENT 3 — Composite score S (114 covered)")
    for sg in COV:
        sg["S"] = (1 if sg["run"] <= 4 else 0) + (1 if sg["mm"] >= 25 else 0) + (1 if sg["da2"] > 0 else 0)
    print(H); print(SEP)
    print(line(A, "baseline v1.1 (S>=0)", COV, lambda sg: True))
    print(line(A, "S>=2", COV, lambda sg: sg["S"] >= 2))
    print(line(A, "S==3", COV, lambda sg: sg["S"] == 3))
    print("\n## FINE SWEEP — mov_mag_ratio threshold T (full 149; gate = run<=4 & ratio>=T)"); print(H); print(SEP)
    T = 0.70
    while T <= 1.601:
        print(line(A, "ratio>=%.2f" % T, SIG, lambda sg, T=T: sg["run"] <= 4 and sg["ratio"] >= T))
        T = round(T + 0.05, 2)


if __name__ == "__main__":
    main()
