"""Addendum: (i) displacement-stratified Mantel-Haenszel test of the QUIET-DRIFT gate,
(ii) the round3 cross-universe FADE check applied to the new whole-bucket feature.  ASCII only."""
from __future__ import annotations
import os, sys, math
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(HERE, "out"))

from study.archive_loader import load_archive          # noqa: E402
import study.mm_skew_feature_matrix as FM              # noqa: E402
import entry_norm_core as C                            # noqa: E402

SL, TP, FEE = 0.008, 0.010, 0.0008
BE = (FEE + SL) / (TP + SL)
WIN = 30


def _f(x, d=0.0):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return d
    return v if math.isfinite(v) else d


def binom_ge(n, w, p0=BE):
    if n <= 0:
        return 1.0
    w = max(0, min(n, w))
    return float(sum(math.comb(n, k) * p0 ** k * (1 - p0) ** (n - k) for k in range(w, n + 1)))


_, H, _ = load_archive("1h")
_, first, _, _ = FM.build()
bars = []
for b in H:
    o = _f(b.get("open_price")); c = _f(b.get("close_price"))
    h = _f(b.get("high")); l = _f(b.get("low"))
    bars.append(dict(o=o, c=c, h=h, l=l, dv=_f(b.get("buy_vol")) - _f(b.get("sell_vol")),
                     ok=(o > 0 and c > 0 and h > l)))
d = np.array([b["dv"] for b in bars], float)
z = [None] * len(bars)
for i in range(first + WIN, len(bars)):
    w = d[i - WIN:i]
    s = w.std(ddof=1)
    if s > 0:
        z[i] = float((d[i] - w.mean()) / s)

U = []
for i in range(first, len(bars)):
    b = bars[i]
    if not b["ok"] or z[i] is None or b["c"] == b["o"]:
        continue
    cd = 1 if b["c"] > b["o"] else -1
    mid = 0.5 * (b["h"] + b["l"])
    U.append(dict(i=i, z=z[i], cdir=cd, disp=100.0 * cd * (b["c"] - mid) / b["c"]))


def sim(sigs, ch):
    last = -1; out = []
    for i, s in sigs:
        if ch and i <= last:
            continue
        e = bars[i]["c"]
        stop = e * (1 - SL) if s > 0 else e * (1 + SL)
        targ = e * (1 + TP) if s > 0 else e * (1 - TP)
        res = None
        for j in range(i + 1, len(bars)):
            if not bars[j]["ok"]:
                continue
            htp = (bars[j]["h"] >= targ) if s > 0 else (bars[j]["l"] <= targ)
            hsl = (bars[j]["l"] <= stop) if s > 0 else (bars[j]["h"] >= stop)
            if htp and hsl:
                res = (False, j); break
            if htp:
                res = (True, j); break
            if hsl:
                res = (False, j); break
        if res is None:
            continue
        out.append(dict(i=i, win=res[0]))
        if ch:
            last = res[1]
    return out


# ------------------------------------------------------------- displacement stratified
print("=" * 100)
print("Q6-A  DISPLACEMENT-STRATIFIED (Mantel-Haenszel) TEST OF THE GATE -- unchained ride")
print("=" * 100)
res = {r["i"]: r["win"] for r in sim([(u["i"], u["cdir"]) for u in U], False)}
pool = [u for u in U if u["i"] in res]
disp = np.array([u["disp"] for u in pool], float)
edges = np.quantile(disp, np.linspace(0, 1, 11))
edges[0] -= 1e-9; edges[-1] += 1e-9
num = den = 0.0
vsum = 0.0
tot_g = tot_gw = tot_c = tot_cw = 0
print("  decile  disp-range          gated n/W  win%%    complement n/W  win%%   within-stratum lift")
for k in range(10):
    lo, hi = edges[k], edges[k + 1]
    S = [u for u in pool if lo < u["disp"] <= hi]
    g = [u for u in S if abs(u["z"]) <= 0.5]
    c = [u for u in S if abs(u["z"]) > 0.5]
    if not g or not c:
        continue
    a = sum(1 for u in g if res[u["i"]]); n1 = len(g)
    b_ = sum(1 for u in c if res[u["i"]]); n2 = len(c)
    N = n1 + n2; m1 = a + b_
    num += a - n1 * m1 / N
    den += 1
    vsum += n1 * n2 * m1 * (N - m1) / (N * N * (N - 1)) if N > 1 else 0
    tot_g += n1; tot_gw += a; tot_c += n2; tot_cw += b_
    print("   %2d    [%+.3f,%+.3f]  %4d/%-4d %6.2f%%     %4d/%-4d %6.2f%%    %+6.2f pp"
          % (k + 1, lo, hi, n1, a, 100.0 * a / n1, n2, b_, 100.0 * b_ / n2,
             100.0 * a / n1 - 100.0 * b_ / n2))
zmh = num / math.sqrt(vsum)
print()
print("  pooled gated %d/%d = %.2f%%   complement %d/%d = %.2f%%"
      % (tot_gw, tot_g, 100.0 * tot_gw / tot_g, tot_cw, tot_c, 100.0 * tot_cw / tot_c))
print("  MANTEL-HAENSZEL z = %+.3f   one-sided p = %.4f   (H0: gate has no effect WITHIN"
      % (zmh, 0.5 * math.erfc(zmh / math.sqrt(2.0))))
print("  displacement strata -- i.e. after removing everything displacement can explain)")
print()
# displacement-implied prediction
gd = np.array([u["disp"] for u in pool if abs(u["z"]) <= 0.5])
cd = np.array([u["disp"] for u in pool if abs(u["z"]) > 0.5])
dd = cd.mean() - gd.mean()
print("  displacement advantage the gate BUYS: complement %+.4f%% - gated %+.4f%% = %.4f%%"
      % (cd.mean(), gd.mean(), dd))
print("  round3 measured coefficient: 0.20%% of entry displacement = 11.1-11.3 pp of win rate")
print("  => displacement ALONE predicts a lift of %+.2f pp for this cohort" % (dd / 0.20 * 11.1))
print("     observed lift vs complement            +1.30 pp")
print("     RESIDUAL (signal beyond displacement)  %+.2f pp" % (1.30 - dd / 0.20 * 11.1))

# ------------------------------------------------------------- cross-universe fade
print()
print("=" * 100)
print("Q2-B  ROUND3 CROSS-UNIVERSE CHECK, applied to the NEW whole-bucket feature (926 basis)")
print("=" * 100)
bars2, U2 = C.build_universe()
ZB = {u["i"]: z[u["i"]] for u in U2 if z[u["i"]] is not None}


def cellrun(sigs, ch):
    T = C.sim(bars2, sigs, C.entry_fn("E1"), chained=ch)
    n = len(T); w = sum(1 for x in T if x["win"])
    e = 100.0 * sum(x["net"] for x in T) / n if n else float("nan")
    return n, w, (100.0 * w / n if n else float("nan")), e


for ch, lab in ((True, "CHAINED"), (False, "UNCHAINED")):
    print("  --- %s ---" % lab)
    for tag, sel, sgn in (
        ("OPPOSED universe FADED, no gate", lambda u: u["opposed"], -1),
        ("OPPOSED FADED, |bucket Z_dV|<=0.5", lambda u: u["opposed"] and abs(ZB.get(u["i"], 9)) <= 0.5, -1),
        ("OPPOSED FADED, |bucket Z_dV| >0.5", lambda u: u["opposed"] and abs(ZB.get(u["i"], 9)) > 0.5, -1),
        ("ALIGNED RIDDEN, no gate", lambda u: u["aligned"], +1),
        ("ALIGNED RIDDEN, |bucket Z_dV|<=0.5", lambda u: u["aligned"] and abs(ZB.get(u["i"], 9)) <= 0.5, +1),
        ("ALIGNED RIDDEN, |bucket Z_dV| >0.5", lambda u: u["aligned"] and abs(ZB.get(u["i"], 9)) > 0.5, +1),
    ):
        sg = [(u["i"], sgn * u["cdir"]) for u in U2 if u["i"] in ZB and sel(u)]
        n, w, wp, e = cellrun(sg, ch)
        print("    %-38s n=%-5d W=%-4d %6.2f%%  %+.4f  binom p %.4f" % (tag, n, w, wp, e, binom_ge(n, w)))
    print()
print("  round3 reported for the H2-HALF feature: opposed ungated 56.5%% -> |Z_dV|<=0.5 47.6%% /")
print("  >0.5 57.0%%   (i.e. it INVERTED across universes).  Compare the bucket feature above.")
