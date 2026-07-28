"""Adversarial confirmation: try HARDER to find a tape/delta-accel next-bar edge, expect NULL.

(A) Circular-shift null on 1h: rotate the cont[] array against the da1_dir & da2_dir orderings by many
    random offsets; fraction of rotations whose most-extreme decile |cont-base| >= the real max = honest
    search p.
(B) New angles: da3/terminal_burst terciles (1m subset), ker() continuation, absorption, magnitude-conditioned
    fade, |da1| extremes reversal, all disjoint + exact binomial + net of fee + 2025/26 split.

Run: python study/tape_accel_harder.py
"""
from __future__ import annotations
import os, sys, math, bisect, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.archive_loader import load_archive
from study.tape_accel_nextbar import build, binom_p, RECON

FEE = 0.0008
rng = np.random.default_rng(12345)


def circular_shift_null(recs, keys, base, nrot=20000):
    """For each feature key, sort recs by feature; slice into 10 disjoint deciles; the REAL statistic is the
    single most extreme |cont_mean - base| across ALL cells of ALL keys. Then rotate the cont array by a random
    offset (breaking any feature<->outcome link while preserving autocorrelation) and recompute the same max.
    p = fraction of rotations with rotated-max >= real-max."""
    cont_all = np.array([r["cont"] for r in recs], float)
    n = len(recs)
    orders = {k: np.argsort([r[k] for r in recs], kind="mergesort") for k in keys}
    # decile boundaries (index slices) are identical across rotations
    slices = np.array_split(np.arange(n), 10)

    def maxdev(cont_ordered_by_key):
        m = 0.0; cell = None
        for k in keys:
            c = cont_all[orders[k]]
            for j, sl in enumerate(slices):
                d = abs(c[sl].mean() - base)
                if d > m:
                    m = d; cell = (k, j + 1, c[sl].mean(), len(sl))
        return m, cell

    real_max, real_cell = maxdev(None)
    # rotations: shift the RAW cont array, then re-index by each key's order
    hits = 0
    for _ in range(nrot):
        off = rng.integers(1, n)
        rot = np.roll(cont_all, off)
        m = 0.0
        for k in keys:
            c = rot[orders[k]]
            for sl in slices:
                d = abs(c[sl].mean() - base)
                if d > m:
                    m = d
        if m >= real_max - 1e-12:
            hits += 1
    return real_max, real_cell, hits / nrot


# ---- 1m sub-bucket accel reconstruction (da3, terminal_burst) mapped into 1h bars ----
def _accel(subs):
    vols = [float(x.get("curr_vol", 0) or 0) for x in subs]
    dels = [float(x.get("buy_vol", 0) or 0) - float(x.get("sell_vol", 0) or 0) for x in subs]
    tot = sum(vols)
    if tot <= 0 or len(subs) < 12:
        return None

    def split(fracs):
        bounds = [f * tot for f in fracs]; groups = [0.0] * (len(fracs) + 1); cum = 0.0
        for v, d in zip(vols, dels):
            mid = cum + v / 2; g = sum(1 for b in bounds if mid > b); groups[g] += d; cum += v
        return groups
    dH1, dH2 = split([0.5]); dT1, dT2, dT3 = split([1 / 3, 2 / 3])
    return (dH2 - dH1) / tot, (dT3 - dT1) / tot, (dT3 - (dT1 + dT2) / 2) / tot, dH1 + dH2


def build_1h_with_terciles():
    _, rows, _ = load_archive("1h", root=RECON)
    _, subs, _ = load_archive("1m", root=RECON)
    subs = sorted(subs, key=lambda r: float(r.get("start_time", 0) or 0))
    sub_start = [float(r.get("start_time", 0) or 0) for r in subs]
    recs = []; ncorr = []
    for i in range(len(rows) - 1):
        b = rows[i]; o = float(b.get("open_price", 0) or 0); c = float(b.get("close_price", 0) or 0)
        bv = float(b.get("buy_vol", 0) or 0); sv = float(b.get("sell_vol", 0) or 0); vol = bv + sv
        on = float(rows[i + 1].get("open_price", 0) or 0); cn = float(rows[i + 1].get("close_price", 0) or 0)
        if o <= 0 or c <= 0 or cn <= 0 or on <= 0 or c == o or vol <= 0:
            continue
        s0 = float(b["start_time"]); s1 = float(b["end_time"])
        j = bisect.bisect_left(sub_start, s0); cons = []
        while j < len(subs) and sub_start[j] <= s1:
            cons.append(subs[j]); j += 1
        ac = _accel(cons)
        if ac is None:
            continue
        a2, a3, tb, rd = ac
        s = 1 if c > o else -1
        ncorr.append((rd, bv - sv))
        recs.append(dict(s=s, da2=a2, da3=a3, tb=tb, da2d=a2 * s, da3d=a3 * s, tbd=tb * s,
                         cont=int((1 if cn > on else -1) == s), ret_c=(cn - c) / c * s,
                         yr=dt.datetime.utcfromtimestamp(s0).year))
    nc = np.array(ncorr)
    corr = float(np.corrcoef(nc[:, 0], nc[:, 1])[0, 1]) if len(nc) > 2 else float("nan")
    return recs, corr


def deciles_tbl(recs, vkey, base, title, n_disjoint=10):
    arr = [recs[i] for i in np.argsort([r[vkey] for r in recs], kind="mergesort")]
    print("\n%s   base cont=%.1f%% n=%d" % (title, base * 100, len(recs)))
    print("  %-4s %14s %6s %7s %9s   %-6s %-6s %6s" % ("bin", "band", "n", "cont%", "net%", "25", "26", "p"))
    for j, ch in enumerate(np.array_split(arr, n_disjoint)):
        if len(ch) == 0:
            continue
        vv = np.array([r[vkey] for r in ch]); cont = np.mean([r["cont"] for r in ch]); ret = np.mean([r["ret_c"] for r in ch])
        c25 = [r for r in ch if r["yr"] == 2025]; c26 = [r for r in ch if r["yr"] == 2026]
        print("  %-4d %6.3f-%6.3f %6d %6.1f%% %+8.4f%%   %-6s %-6s %6.3f" %
              (j + 1, vv.min(), vv.max(), len(ch), cont * 100, ret * 100 - FEE * 100,
               ("%.0f%%" % (np.mean([r["cont"] for r in c25]) * 100)) if c25 else "-",
               ("%.0f%%" % (np.mean([r["cont"] for r in c26]) * 100)) if c26 else "-",
               binom_p(sum(r["cont"] for r in ch), len(ch), base)))


def bucket_tbl(recs, name, groups, base):
    print("\n%s   base %.1f%%" % (name, base * 100))
    for lab, sel in groups:
        g = [r for r in recs if sel(r)]
        if not g:
            print("  %-30s n=0" % lab); continue
        cont = np.mean([r["cont"] for r in g]); ret = np.mean([r["ret_c"] for r in g])
        c25 = [r for r in g if r["yr"] == 2025]; c26 = [r for r in g if r["yr"] == 2026]
        print("  %-30s n=%5d  cont %5.1f%%  net %+.4f%%  25:%s 26:%s  p=%.3f" %
              (lab, len(g), cont * 100, ret * 100 - FEE * 100,
               ("%.0f%%" % (np.mean([r["cont"] for r in c25]) * 100)) if c25 else "-",
               ("%.0f%%" % (np.mean([r["cont"] for r in c26]) * 100)) if c26 else "-",
               binom_p(sum(r["cont"] for r in g), len(g), base)))


def main():
    recs = build("1h")
    base = np.mean([r["cont"] for r in recs])
    print("=" * 100)
    print("1h recon n=%d  base cont=%.2f%%  P(nextUP)=%.2f%%" %
          (len(recs), base * 100, np.mean([r["nup"] for r in recs]) * 100))
    print("=" * 100)

    # (A) circular-shift null over da1_dir + da2_dir decile cells
    rmax, rcell, p = circular_shift_null(recs, ["da1d", "da2d"], base, nrot=20000)
    print("\n(A) CIRCULAR-SHIFT NULL over da1_dir & da2_dir deciles (20 cells, 20000 rotations)")
    print("    most-extreme REAL cell: key=%s dec=%d cont=%.3f%% n=%d  |dev|=%.4f" %
          (rcell[0], rcell[1], rcell[2] * 100, rcell[3], rmax))
    print("    honest search p (frac rotations >= real max) = %.4f" % p)

    # (B1) new angle: |da1| extreme reversal (exhaustion -> fade next bar). directional 'nup' fade.
    q = np.percentile([abs(r["da1"]) for r in recs], [80, 90, 95])
    bucket_tbl(recs, "(B1) EXTREME aggression |da1| -> does candle FADE (cont<50)?", [
        ("|da1|>p95 (top 5%)", lambda r: abs(r["da1"]) > q[2]),
        ("p90<|da1|<=p95", lambda r: q[1] < abs(r["da1"]) <= q[2]),
        ("p80<|da1|<=p90", lambda r: q[0] < abs(r["da1"]) <= q[1]),
    ], base)

    # (B2) magnitude of candle x da1_dir sign (does aggression only matter on big/small candles?)
    rng_pct = np.array([abs(r["ret_c"]) for r in recs])  # proxy for realized bar size (own-move mag)
    # use candle body via s*ret? better: use |da2| accel magnitude conditioning
    bucket_tbl(recs, "(B2) da1_dir sign split by whether da2 ACCELERATES same dir", [
        ("da1d>0 & da2d>0 (both aligned)", lambda r: r["da1d"] > 0 and r["da2d"] > 0),
        ("da1d>0 & da2d<=0", lambda r: r["da1d"] > 0 and r["da2d"] <= 0),
        ("da1d<=0 & da2d<=0 (both opp)", lambda r: r["da1d"] <= 0 and r["da2d"] <= 0),
        ("da1d<=0 & da2d>0", lambda r: r["da1d"] <= 0 and r["da2d"] > 0),
    ], base)

    # (B3) tercile family on the 1m subset
    trec, corr = build_1h_with_terciles()
    n25 = sum(1 for r in trec if r["yr"] == 2025)
    print("\n(B3) 1m-tercile subset: n=%d (recon corr=%.3f)  spans yr2025=%d yr2026=%d" %
          (len(trec), corr, n25, len(trec) - n25))
    tbase = np.mean([r["cont"] for r in trec])
    deciles_tbl(trec, "da3d", tbase, "(B3a) da3_dir (last vs first tercile) -> cont", n_disjoint=5)
    deciles_tbl(trec, "tbd", tbase, "(B3b) terminal_burst_dir (final third burst) -> cont", n_disjoint=5)
    q3 = np.percentile([r["da3d"] for r in trec], [33.3, 66.6])
    qt = np.percentile([r["tbd"] for r in trec], [33.3, 66.6])
    bucket_tbl(trec, "(B3c) tercile extremes", [
        ("da3_dir top-third (accel in)", lambda r: r["da3d"] > q3[1]),
        ("da3_dir bottom-third (decel)", lambda r: r["da3d"] <= q3[0]),
        ("term_burst top-third", lambda r: r["tbd"] > qt[1]),
        ("term_burst bottom-third", lambda r: r["tbd"] <= qt[0]),
    ], tbase)

    print("\nDONE. Verdict = does any net-positive cell survive multiplicity / the circular-shift p?")


if __name__ == "__main__":
    main()
