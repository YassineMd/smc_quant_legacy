"""Why do the MM/Skew-strategy winners win and losers lose?  Profile the SIGNAL candle's
Mov.Magnitude, Skew and MM*Skew for TP (win) vs SL (loss), and look for a separating range.

Signals = the exact 1h strategy fires (bull&skew>0&close-top-tail -> long ; bear&skew<0&close-bottom-tail
-> short), fixed SL 0.1% beyond the high/low, fixed TP at 0.5x the SL distance, first-touch (SL-first).
Every trade is win or loss (OPEN excluded). Per SIDE, because skew/product signs are fixed by the entry.

Honesty rails: winners/losers overlap is the null; a "range" only counts if (a) Mann-Whitney separates the
two AND (b) the win-rate-by-tercile pattern REPEATS in both chronological halves. In-sample threshold-hunting
always finds something — split-half is the judge.

Run:  python study/mm_skew_winloss.py
"""
from __future__ import annotations
import os, sys, math, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import study.mm_skew_strategy as S


def collect(B):
    out = []
    for i in range(len(B) - 1):
        s = S.signal(B[i], S.CP_THR)
        if s == 0:
            continue
        res = S.simulate(B, i, s, "sl")
        if res is None or res[0] == "OPEN":
            continue
        b = B[i]; mm = S.mov_mag(b["o"], b["h"], b["l"], b["c"])
        out.append(dict(side=s, win=(res[0] == "TP"), mm=mm, sk=b["sk"], prod=mm * b["sk"], i=i))
    return out


def q(vals, p):
    if not vals:
        return float("nan")
    xs = sorted(vals); k = p * (len(xs) - 1); f = int(k)
    return xs[f] if f + 1 >= len(xs) else xs[f] + (k - f) * (xs[f + 1] - xs[f])


def mann_whitney(a, b):
    """rank-sum; returns (p, median_a - median_b)."""
    if len(a) < 5 or len(b) < 5:
        return float("nan"), float("nan")
    allv = [(v, 0) for v in a] + [(v, 1) for v in b]
    allv.sort(key=lambda t: t[0])
    ranks = [0.0] * len(allv); i = 0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1][0] == allv[i][0]:
            j += 1
        rr = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[k] = rr
        i = j + 1
    R1 = sum(ranks[k] for k in range(len(allv)) if allv[k][1] == 0)
    n1, n2 = len(a), len(b)
    U1 = R1 - n1 * (n1 + 1) / 2.0
    mu = n1 * n2 / 2.0; sd = math.sqrt(n1 * n2 * (n1 + n2 + 1) / 12.0)
    z = (U1 - mu) / sd if sd > 0 else 0.0
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    return p, statistics.median(a) - statistics.median(b)


def dist_line(name, W, L, key):
    w = [x[key] for x in W]; l = [x[key] for x in L]
    p, dm = mann_whitney(w, l)
    print(f"    {name:9s} WIN  {q(w,.25):+8.3f} [{q(w,.5):+8.3f}] {q(w,.75):+8.3f}   "
          f"LOSE {q(l,.25):+8.3f} [{q(l,.5):+8.3f}] {q(l,.75):+8.3f}   MW p={p:.3f}")


def tercile_winrate(sigs, key, label):
    xs = sorted(sigs, key=lambda x: x[key])
    n = len(xs); c1 = n // 3; c2 = 2 * n // 3
    print(f"    {label} tercile win-rate:")
    for lbl, lo, hi in (("low ", 0, c1), ("mid ", c1, c2), ("high", c2, n)):
        grp = xs[lo:hi]
        if not grp:
            continue
        w = sum(1 for x in grp if x["win"])
        rng = f"[{grp[0][key]:+.3f} .. {grp[-1][key]:+.3f}]"
        print(f"      {lbl}: n={len(grp):<3} win={100*w/len(grp):5.1f}%   {key} {rng}")


def side_report(sigs, side, name):
    ss = [x for x in sigs if x["side"] == side]
    W = [x for x in ss if x["win"]]; L = [x for x in ss if not x["win"]]
    base = 100.0 * len(W) / len(ss) if ss else float("nan")
    print(f"\n{'='*96}\n{name}  n={len(ss)}  ({len(W)}W / {len(L)}L, win {base:.1f}%)\n{'='*96}")
    print("    feature   -------- WINNERS Q1[med]Q3 --------   -------- LOSERS Q1[med]Q3 --------")
    for nm, key in (("MovMag", "mm"), ("Skew", "sk"), ("MMxSkew", "prod")):
        dist_line(nm, W, L, key)
    print()
    for nm, key in (("MovMag", "mm"), ("Skew", "sk"), ("MMxSkew", "prod")):
        tercile_winrate(ss, key, nm)


def split_half_check(sigs, side, key, name):
    ss = sorted([x for x in sigs if x["side"] == side], key=lambda x: x["i"])
    mid = len(ss) // 2
    print(f"\n  split-half repeat check — {name} {key} tercile win-rates:")
    for hlbl, sub in (("H1", ss[:mid]), ("H2", ss[mid:])):
        xs = sorted(sub, key=lambda x: x[key]); n = len(xs); c1 = n // 3; c2 = 2 * n // 3
        cells = []
        for lo, hi in ((0, c1), (c1, c2), (c2, n)):
            g = xs[lo:hi]; cells.append(100.0 * sum(1 for x in g if x["win"]) / len(g) if g else float("nan"))
        print(f"    {hlbl}: low={cells[0]:4.0f}%  mid={cells[1]:4.0f}%  high={cells[2]:4.0f}%")


def main():
    B = S.load()
    sigs = collect(B)
    nW = sum(1 for x in sigs if x["win"])
    print(f"strategy signals resolved: {len(sigs)}  ({nW}W / {len(sigs)-nW}L = {100*nW/len(sigs):.1f}% win)")
    print("distributions are of the SIGNAL candle's feature; MW p = Mann-Whitney winners-vs-losers (rank).")

    side_report(sigs, +1, "LONGS  (bull & skew>0 & close-top-tail)")
    side_report(sigs, -1, "SHORTS (bear & skew<0 & close-bottom-tail)")

    print(f"\n{'='*96}\nSPLIT-HALF VALIDATION (does any tercile pattern REPEAT across both halves?)\n{'='*96}")
    for side, name in ((+1, "LONG"), (-1, "SHORT")):
        for nm, key in (("MovMag", "mm"), ("Skew", "sk"), ("MMxSkew", "prod")):
            split_half_check(sigs, side, key, name + " " + nm)


if __name__ == "__main__":
    main()
