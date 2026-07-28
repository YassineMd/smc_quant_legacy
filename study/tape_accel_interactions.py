"""INTERACTIONS: does da2_dir (accelerate INTO the move) predict CONTINUATION
conditioned on context = candle magnitude / KER efficiency / absorption?

Disjoint cells, exact-binomial two-sided p vs the CELL's own within-band continuation
baseline (so we isolate the da2 effect, not the band effect), net of 0.08% fee, 2025/26.
"""
from __future__ import annotations
import os, sys, math, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.archive_loader import load_archive
from study.ker_continuation import ker
from app.absorption import absorption

RECON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "study", "recon_archive")
FEE = 0.0008


def binom_p(k, n, p):
    if n <= 0 or p <= 0 or p >= 1:
        return float("nan")
    k = int(round(k))
    if n <= 500:
        pv = sum(math.comb(n, j) * p ** j * (1 - p) ** (n - j) for j in range(k, n + 1))
    else:
        mu = n * p; sd = math.sqrt(n * p * (1 - p)); pv = 0.5 * math.erfc((k - 0.5 - mu) / (sd * math.sqrt(2.0)))
    return min(min(pv, 1 - pv) * 2, 1.0)


def build():
    _, rows, _ = load_archive("1h", root=RECON)
    recs = []
    for i in range(len(rows) - 1):
        b = rows[i]; o = float(b.get("open_price", 0) or 0); c = float(b.get("close_price", 0) or 0)
        bv = float(b.get("buy_vol", 0) or 0); sv = float(b.get("sell_vol", 0) or 0)
        dh1 = b.get("delta_h1"); vol = bv + sv
        on = float(rows[i + 1].get("open_price", 0) or 0); cn = float(rows[i + 1].get("close_price", 0) or 0)
        if o <= 0 or c <= 0 or cn <= 0 or on <= 0 or c == o or vol <= 0 or dh1 is None:
            continue
        s = 1 if c > o else -1; delta = bv - sv
        da2 = (delta - 2 * float(dh1)) / vol
        kb, ks = ker(b)
        A = absorption(rows, i)[0]
        ndir = 1 if cn > on else (-1 if cn < on else 0)
        recs.append(dict(
            s=s, da2d=da2 * s, cont=int(ndir == s), ret_c=(cn - c) / c * s,
            mag=abs(c - o) / o, kerd=(kb if s == 1 else ks), A=(A if A is not None else None),
            yr=dt.datetime.utcfromtimestamp(float(b["start_time"])).year))
    return recs


def yr_cont(g, y):
    gg = [r for r in g if r["yr"] == y]
    return ("%.0f%%n%d" % (np.mean([r["cont"] for r in gg]) * 100, len(gg))) if gg else "-"


def cell_report(title, recs, band_fn, bands):
    print("\n" + "=" * 118)
    print(title)
    print("=" * 118)
    print("  %-22s %-14s %5s %7s %7s %8s %8s   %-10s %-10s %6s" %
          ("band", "da2_dir", "n", "cont%", "base%", "ret%", "net%", "2025", "2026", "p"))
    ncells = 0
    best = None
    for blab, bsel in bands:
        g = [r for r in recs if bsel(band_fn(r))]
        if len(g) < 15:
            print("  %-22s (n=%d, skip)" % (blab, len(g))); continue
        base = np.mean([r["cont"] for r in g])
        for dlab, dsel in [("da2_dir>0", lambda r: r["da2d"] > 0), ("da2_dir<=0", lambda r: r["da2d"] <= 0)]:
            gg = [r for r in g if dsel(r)]
            if len(gg) < 12:
                print("  %-22s %-14s n=%d skip" % (blab, dlab, len(gg))); continue
            ncells += 1
            cont = np.mean([r["cont"] for r in gg]); ret = np.mean([r["ret_c"] for r in gg])
            p = binom_p(sum(r["cont"] for r in gg), len(gg), base)
            net = ret - FEE
            print("  %-22s %-14s %5d %6.1f%% %6.1f%% %+7.4f%% %+7.4f%%   %-10s %-10s %6.3f" %
                  (blab, dlab, len(gg), cont * 100, base * 100, ret * 100, net * 100,
                   yr_cont(gg, 2025), yr_cont(gg, 2026), p))
            cand = dict(desc="%s | %s" % (blab, dlab), n=len(gg), cont=cont, base=base, net=net, p=p)
            if best is None or (net > 0 and p < best["p"]):
                if best is None or net > 0:
                    if best is None or (net > 0 and (best["net"] <= 0 or p < best["p"])):
                        best = cand
    return ncells, best


def main():
    recs = build()
    base_all = np.mean([r["cont"] for r in recs])
    print("TAPE-ACCEL x CONTEXT interactions | 1h recon | n=%d | all-continue=%.1f%% | fee=%.2f%%" %
          (len(recs), base_all * 100, FEE * 100))
    yrs = sorted(set(r["yr"] for r in recs))
    for y in yrs:
        gg = [r for r in recs if r["yr"] == y]
        print("   %d: n=%d cont=%.1f%%" % (y, len(gg), np.mean([r["cont"] for r in gg]) * 100))

    # magnitude terciles (disjoint) from empirical quantiles
    mags = np.array([r["mag"] for r in recs])
    q33, q66 = np.quantile(mags, [1 / 3, 2 / 3])
    magb = [("mag LOW (<=%.4f)" % q33, lambda v: v <= q33),
            ("mag MID", lambda v: q33 < v <= q66),
            ("mag HIGH (>%.4f)" % q66, lambda v: v > q66)]
    n1, b1 = cell_report("[A] da2_dir -> continuation, WITHIN candle-MAGNITUDE terciles", recs, lambda r: r["mag"], magb)

    # KER directional bands (disjoint): low / mid / high / vacuum(inf==9999)
    kerb = [("ker=0", lambda v: v == 0.0),
            ("ker (0,1e-3]", lambda v: 0 < v <= 1e-3),
            ("ker (1e-3,1e-2]", lambda v: 1e-3 < v <= 1e-2),
            ("ker (1e-2,3e-2]", lambda v: 1e-2 < v <= 3e-2),
            ("ker (3e-2,9998]", lambda v: 3e-2 < v <= 9998.0),
            ("ker vacuum(inf)", lambda v: v == 9999.0)]
    n2, b2 = cell_report("[B] da2_dir -> continuation, WITHIN KER (efficiency) bands", recs, lambda r: r["kerd"], kerb)

    # absorption bands (disjoint). A>0 = absorbed. R-easy = A<=-0.75 (NOT absorbed, efficient).
    ra = [r for r in recs if r["A"] is not None]
    print("\n  (absorption available on n=%d of %d)" % (len(ra), len(recs)))
    absb = [("A R-easy(<=-0.75)", lambda v: v <= -0.75),
            ("A mid(-0.75,0.75)", lambda v: -0.75 < v < 0.75),
            ("A absorbed(>=0.75)", lambda v: v >= 0.75)]
    n3, b3 = cell_report("[C] da2_dir -> continuation, WITHIN ABSORPTION bands", ra, lambda r: r["A"], absb)

    print("\n" + "=" * 60)
    print("TOTAL da2-cells tested = %d" % (n1 + n2 + n3))
    for tag, b in [("mag", b1), ("ker", b2), ("absorb", b3)]:
        if b:
            print("  best[%s]: %-32s n=%d cont=%.1f%% base=%.1f%% net=%+.4f%% p=%.3f" %
                  (tag, b["desc"], b["n"], b["cont"] * 100, b["base"] * 100, b["net"] * 100, b["p"]))


if __name__ == "__main__":
    main()
