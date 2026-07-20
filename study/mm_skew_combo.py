"""Do MM x Skew combinations lean the NEXT 1h bar?  (causal, in-sample)

Features known at a bar's CLOSE:
  mm    = Mov.Magnitude  = ((close*100/ref - 100)^2)*100 , ref=low(bull)/high(bear)  [>=0, direction-blind]
  skew  = profile-read skewness of the volume-by-price profile  [+ = mass HIGH, - = mass LOW]
  dir   = sign(close-open)                                       [+1 bull / -1 bear]
Composites tested:
  prod  = mm * skew                 (move scaled by volume one-sidedness; sign = where volume sat)
  ratio = mm / skew                 (move per unit lopsidedness; UNSTABLE — skew~0 for most bars)
  ct    = mm * skew * dir           ("confirmation thrust": + = volume confirmed the move, - = diverged)

Targets (strictly forward, close[i]->close[i+1]):
  nret  = next-bar return %                     (raw up/down)
  cont  = nret * dir  = continuation return %   (+ = price kept going the SIGNAL bar's way)

Mature 1h region only (target_vol >= 100k; cold-start warmup dropped). Bars are constant-VOLUME so the
horizon is the single next bar. Reports Pearson + Spearman (rank; robust to the ratio's blow-ups) and a
sign-cohort test with a two-proportion z vs the base rate.

Run:  python study/mm_skew_combo.py
"""
from __future__ import annotations
import os, sys, math, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from study.archive_loader import load_archive
from app.footprint_panel import profile_skewness

MATURE_TV = 100000.0


def mov_mag(o, h, l, c):
    ref = l if c > o else (h if c < o else o)
    return (((c * 100.0 / ref) - 100.0) ** 2) * 100.0 if ref > 0 else 0.0


def load():
    _, raws, _ = load_archive("1h")
    B = []
    for r in raws:
        o = r.get("open_price"); h = r.get("high"); l = r.get("low"); c = r.get("close_price")
        tv = r.get("target_vol", 0.0) or 0.0
        if not o or not c or o <= 0 or h is None or l is None:
            continue
        B.append(dict(o=o, h=h, l=l, c=c, tv=tv, mm=mov_mag(o, h, l, c),
                      sk=profile_skewness(r.get("levels")), dir=(1 if c > o else (-1 if c < o else 0))))
    first = next((i for i in range(len(B)) if B[i]["tv"] >= MATURE_TV
                  and statistics.median([b["tv"] for b in B[i:i + 30]]) >= MATURE_TV), len(B))
    return B[first:]


def pearson(a, b):
    n = len(a); ma = sum(a) / n; mb = sum(b) / n
    num = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    r = num / den if den else 0.0
    t = r * math.sqrt((n - 2) / (1 - r * r)) if abs(r) < 1 else float("inf")
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2))))
    return r, p


def _rank(x):
    order = sorted(range(len(x)), key=lambda i: x[i])
    rank = [0.0] * len(x)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and x[order[j + 1]] == x[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            rank[order[k]] = avg
        i = j + 1
    return rank


def spearman(a, b):
    return pearson(_rank(a), _rank(b))


def ztest_rate(k, n, base_rate):
    if n < 10:
        return float("nan")
    se = math.sqrt(base_rate * (1 - base_rate) / n)
    z = (k / n - base_rate) / se if se > 0 else 0.0
    return 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))


def sign_cohort(feats, targets, base_rate, name, target_name):
    """Split by feature sign; report positive-target rate + mean vs base for each side."""
    print(f"\n  {name}  ->  {target_name}   (base rate {100*base_rate:.1f}% positive)")
    for lbl, keep in (("feature > 0", lambda v: v > 0), ("feature < 0", lambda v: v < 0)):
        idx = [i for i in range(len(feats)) if keep(feats[i])]
        if len(idx) < 10:
            print(f"    {lbl:12s} n={len(idx):<4} (too few)"); continue
        tv = [targets[i] for i in idx]
        pos = sum(1 for v in tv if v > 0); rate = 100.0 * pos / len(tv)
        p = ztest_rate(pos, len(tv), base_rate)
        print(f"    {lbl:12s} n={len(idx):<4} pos-rate={rate:5.1f}%  edge={rate-100*base_rate:+5.1f}pp  "
              f"mean={statistics.mean(tv):+.4f}%  p={p:.3f}")


def main():
    B = load()
    # pair each defined bar with the NEXT bar's return
    rows = []
    for i in range(len(B) - 1):
        b = B[i]
        if b["sk"] is None or b["dir"] == 0:
            continue
        nret = (B[i + 1]["c"] - b["c"]) / b["c"] * 100.0
        rows.append(dict(mm=b["mm"], sk=b["sk"], dir=b["dir"],
                         prod=b["mm"] * b["sk"], ratio=(b["mm"] / b["sk"] if b["sk"] != 0 else 0.0),
                         ct=b["mm"] * b["sk"] * b["dir"], nret=nret, cont=nret * b["dir"]))
    n = len(rows)
    nret = [x["nret"] for x in rows]; cont = [x["cont"] for x in rows]
    base_up = sum(1 for v in nret if v > 0) / n
    base_cont = sum(1 for v in cont if v > 0) / n
    print(f"mature 1h bars paired: {n}   (skew defined, non-doji)")
    print(f"base next-bar UP rate:        {100*base_up:.1f}%   mean nret {statistics.mean(nret):+.4f}%")
    print(f"base CONTINUATION rate:       {100*base_cont:.1f}%   mean cont {statistics.mean(cont):+.4f}%")
    print("  (continuation = did price keep going the signal bar's own direction)")

    print("\n" + "=" * 90)
    print("CORRELATIONS  (feature vs forward target)   Pearson r (p) | Spearman r (p)")
    print("=" * 90)

    def corr_line(fname, target, tname):
        f = [x[fname] for x in rows]
        pr, pp = pearson(f, target); sr, sp = spearman(f, target)
        print(f"  {fname:6s} vs {tname:5s} :  Pearson {pr:+.4f} (p={pp:.3f})   Spearman {sr:+.4f} (p={sp:.3f})")

    # composites vs raw next return (their sign = volume location for prod/ratio)
    corr_line("prod", nret, "nret")
    corr_line("ratio", nret, "nret")
    # confirmation thrust is about continuation, so test vs cont
    corr_line("ct", cont, "cont")
    print("  ---- baselines / building blocks ----")
    corr_line("mm", cont, "cont")      # does raw move-size predict continuation?
    corr_line("sk", nret, "nret")      # does volume location alone lean next return?
    corr_line("mm", nret, "nret")

    print("\n" + "=" * 90)
    print("SIGN COHORTS  (does the feature's sign lean the next bar?)")
    print("=" * 90)
    sign_cohort([x["prod"] for x in rows], nret, base_up, "prod (mm*skew)", "next-bar UP")
    sign_cohort([x["ratio"] for x in rows], nret, base_up, "ratio (mm/skew)", "next-bar UP")
    sign_cohort([x["ct"] for x in rows], cont, base_cont, "ct (confirmation)", "CONTINUATION")
    # confirm vs diverge, restricted to a real move (mm>=20) where the composite carries weight
    print("\n  --- confirmation gated on a real move (mm>=20) ---")
    big = [x for x in rows if x["mm"] >= 20.0]
    if big:
        bcont = [x["cont"] for x in big]; bbase = sum(1 for v in bcont if v > 0) / len(big)
        sign_cohort([x["ct"] for x in big], bcont, bbase, f"ct | mm>=20 (n={len(big)})", "CONTINUATION")


if __name__ == "__main__":
    main()
