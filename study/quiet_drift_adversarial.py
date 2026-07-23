"""Adversarial pricing of QUIET-DRIFT CONTINUATION v1.0.

Q1 novelty      corr(bucket Z_dV, h2 Z_dV), gate-set sizes, intersection, Jaccard.
Q2 comparison   the same cells as gemini_round3 sec.3, recomputed for BOTH features on BOTH universes.
Q3 mechanism    within the qualifying cohort, split by realised expansion (|ret| and oriented Z_dP).
Q5 power        MDE at 80% power / one-sided alpha 0.05 for the realised n.
Q6 displacement close-percentile / displacement of the gated cohort vs the universe.

ASCII output only.
"""
from __future__ import annotations
import os, sys, math, bisect
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


def two_prop(n1, w1, n2, w2):
    if n1 <= 0 or n2 <= 0:
        return float("nan"), float("nan")
    p1, p2 = w1 / n1, w2 / n2
    pb = (w1 + w2) / (n1 + n2)
    se = math.sqrt(pb * (1 - pb) * (1 / n1 + 1 / n2))
    if se <= 0:
        return float("nan"), float("nan")
    z = (p1 - p2) / se
    return z, 0.5 * math.erfc(z / math.sqrt(2.0))


def se_diff(n1, p1, n2, p2):
    return math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)


# --------------------------------------------------------------------- data
_, H, _ = load_archive("1h")
_, first, _, _ = FM.build()

bars = []
for b in H:
    o = _f(b.get("open_price")); c = _f(b.get("close_price"))
    h = _f(b.get("high")); l = _f(b.get("low"))
    bars.append(dict(o=o, c=c, h=h, l=l, t=_f(b.get("start_time")),
                     dv=_f(b.get("buy_vol")) - _f(b.get("sell_vol")),
                     ok=(o > 0 and c > 0 and h > l)))


def zser(vals, lo):
    a = np.array(vals, float)
    z = [None] * len(a)
    for i in range(lo + WIN, len(a)):
        w = a[i - WIN:i]
        s = w.std(ddof=1)
        if s > 0:
            z[i] = float((a[i] - w.mean()) / s)
    return z


zdv_b = zser([b["dv"] for b in bars], first)
rets = [(100.0 * (b["c"] - b["o"]) / b["o"] if b["o"] > 0 else 0.0) for b in bars]
zdp_b = zser(rets, first)

# quiet-drift spec universe
QD = {}
for i in range(first, len(bars)):
    b = bars[i]
    if not b["ok"] or zdv_b[i] is None or b["c"] == b["o"]:
        continue
    QD[i] = dict(i=i, z=zdv_b[i], zp=zdp_b[i], cdir=1 if b["c"] > b["o"] else -1,
                 ret=rets[i])

# entry-norm 926 universe, with h2 z's
bars2, U2 = C.build_universe()
EN = {u["i"]: u for u in U2}

# recover zv2 / zp2 (entry_norm_core stores them on recs, not on U) -> rebuild
_, M, _ = load_archive("1m")
mst = [_f(x.get("start_time")) for x in M]
recs = []
for i in range(len(H)):
    b = H[i]
    if i < first or not bars[i]["ok"]:
        continue
    cv = _f(b.get("curr_vol"))
    if cv <= 0:
        continue
    a = bisect.bisect_left(mst, _f(b.get("start_time")))
    z_ = bisect.bisect_left(mst, _f(b.get("end_time")))
    subs = M[a:z_]
    if len(subs) < C.MIN_SUBS:
        continue
    vols = [_f(s.get("curr_vol")) for s in subs]
    tot = sum(vols)
    if tot <= 0:
        continue
    cum = 0.0; k = None
    for j, v in enumerate(vols):
        cum += v
        if cum >= 0.5 * tot:
            k = j; break
    if k is None or k + 1 >= len(subs):
        continue
    h1 = subs[:k + 1]; h2 = subs[k + 1:]
    dv2 = sum(_f(s.get("buy_vol")) - _f(s.get("sell_vol")) for s in h2)
    p2o = _f(h2[0].get("open_price")); p2c = _f(h2[-1].get("close_price"))
    if min(p2o, p2c) <= 0:
        continue
    recs.append(dict(i=i, dp2=100.0 * (p2c - p2o) / p2o, dv2=dv2,
                     dvB=bars[i]["dv"]))

for idx, r in enumerate(recs):
    lo = max(0, idx - WIN)
    hist = recs[lo:idx]
    if len(hist) < C.MIN_OBS:
        r["zv2"] = None; r["zvB_m"] = None; r["zp2"] = None
        continue
    for key, tag in (("dv2", "zv2"), ("dvB", "zvB_m"), ("dp2", "zp2")):
        V = np.array([q[key] for q in hist], float)
        s = V.std(ddof=1)
        r[tag] = float((r[key] - V.mean()) / s) if s > 0 else None

RC = {r["i"]: r for r in recs}

print("=" * 100)
print("Q1  NOVELTY, MEASURED")
print("=" * 100)
common = sorted(set(QD) & set(EN) & set(k for k, v in RC.items() if v.get("zv2") is not None))
print("  quiet-drift spec universe (mature/ok/z/non-doji)  n = %d" % len(QD))
print("  entry-norm 926 universe (round3/round4 basis)     n = %d" % len(EN))
print("  COMMON universe used for every Q1/Q2 number       n = %d" % len(common))
zb = np.array([QD[i]["z"] for i in common], float)
zh = np.array([RC[i]["zv2"] for i in common], float)
zbm = np.array([RC[i]["zvB_m"] for i in common], float)
print()
print("  corr(bucket Z_dV, h2 Z_dV)                         = %+.4f   (Spearman %+.4f)"
      % (np.corrcoef(zb, zh)[0, 1],
         np.corrcoef(np.argsort(np.argsort(zb)), np.argsort(np.argsort(zh)))[0, 1]))
print("  corr with WINDOW CONVENTION MATCHED (both on the recs series, trailing 30 usable):")
print("    corr(bucket Z_dV[recs-window], h2 Z_dV)          = %+.4f" % np.corrcoef(zbm, zh)[0, 1])
print("    corr(bucket Z_dV[mature-window], bucket Z_dV[recs-window]) = %+.4f" % np.corrcoef(zb, zbm)[0, 1])
print()
A = set(i for i in common if abs(QD[i]["z"]) <= 0.5)
B = set(i for i in common if abs(RC[i]["zv2"]) <= 0.5)
print("  gate |bucket Z_dV| <= 0.5    n = %d   (%.1f%% of common)" % (len(A), 100 * len(A) / len(common)))
print("  gate |h2     Z_dV| <= 0.5    n = %d   (%.1f%% of common)" % (len(B), 100 * len(B) / len(common)))
print("  INTERSECTION                 n = %d" % len(A & B))
print("  UNION                        n = %d" % len(A | B))
print("  JACCARD                      = %.4f" % (len(A & B) / len(A | B)))
print("  P(h2 gate | bucket gate) = %.3f   base rate P(h2 gate) = %.3f   lift %+0.3f"
      % (len(A & B) / len(A), len(B) / len(common), len(A & B) / len(A) - len(B) / len(common)))
ph = len(A) * len(B) / len(common)
print("  expected intersection if the two gates were INDEPENDENT = %.1f   observed %d   ratio %.2f"
      % (ph, len(A & B), len(A & B) / ph))
# phi
n = len(common); a = len(A & B); bb = len(A) - a; cc = len(B) - a; dd = n - a - bb - cc
phi = (a * dd - bb * cc) / math.sqrt(len(A) * (n - len(A)) * len(B) * (n - len(B)))
print("  2x2 phi = %+.4f   chi2 = %.2f" % (phi, n * phi * phi))

# --------------------------------------------------------------------- Q2
print()
print("=" * 100)
print("Q2  WHAT WAS ALREADY MEASURED -- both features, both universes, RIDE, common 926-basis")
print("=" * 100)


def cellrun(sigs, ch):
    T = C.sim(bars2, sigs, C.entry_fn("E1"), chained=ch)
    n = len(T); w = sum(1 for x in T if x["win"])
    e = 100.0 * sum(x["net"] for x in T) / n if n else float("nan")
    return n, w, (100.0 * w / n if n else float("nan")), e


def show(tag, ids, ch):
    sg = [(i, EN[i]["cdir"]) for i in sorted(ids)]
    n, w, wp, e = cellrun(sg, ch)
    print("  %-46s n=%-5d W=%-4d %6.2f%%  %+.4f   binom p %.4f"
          % (tag, n, w, wp, e, binom_ge(n, w)))
    return n, w, wp, e


aligned = set(i for i in common if EN[i]["aligned"])
for ch, lab in ((True, "CHAINED"), (False, "UNCHAINED")):
    print("  --- %s ---" % lab)
    show("ALIGNED universe, no gate", aligned, ch)
    show("ALIGNED, |h2 Z_dV|<=0.5   [round3 cell]", aligned & B, ch)
    show("ALIGNED, |bucket Z_dV|<=0.5 [new feature]", aligned & A, ch)
    show("FULL universe, no gate (ride-all)", set(common), ch)
    show("FULL, |h2 Z_dV|<=0.5", B, ch)
    show("FULL, |bucket Z_dV|<=0.5  [QUIET-DRIFT]", A, ch)
    opp = set(common) - aligned
    show("OPPOSED universe, no gate", opp, ch)
    show("OPPOSED, |h2 Z_dV|<=0.5", opp & B, ch)
    show("OPPOSED, |bucket Z_dV|<=0.5", opp & A, ch)
    print()

# --------------------------------------------------------------------- Q3
print("=" * 100)
print("Q3  MECHANISM -- within the QUIET-DRIFT qualifying cohort, does big expansion win?")
print("=" * 100)


def sim_qd(sigs, ch):
    """quiet-drift engine on the FULL bar series (same as quiet_drift_validate.sim)."""
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
        out.append(dict(i=i, win=res[0], net=((TP if res[0] else -SL) - FEE) * 100.0))
        if ch:
            last = res[1]
    return out


def cq(T):
    n = len(T); w = sum(1 for x in T if x["win"])
    return n, w, (100.0 * w / n if n else float("nan")),


QDG = [QD[i] for i in sorted(QD) if abs(QD[i]["z"]) <= 0.5]
print("  qualifying cohort (spec universe, |bucket Z_dV| <= 0.5): %d signals" % len(QDG))
for key, lab in (("absret", "|bucket return| %"),
                 ("oz", "oriented sign(candle)*Z_dP")):
    if key == "absret":
        vals = np.array([abs(r["ret"]) for r in QDG], float)
    else:
        vals = np.array([r["cdir"] * (r["zp"] if r["zp"] is not None else 0.0) for r in QDG], float)
    med = float(np.median(vals))
    print()
    print("  --- split by %s  (median %.4f) ---" % (lab, med))
    for ch, mlab in ((False, "unchained"), (True, "chained")):
        rows = []
        for half, mask in (("LOW  (small expansion)", vals <= med), ("HIGH (big expansion)", vals > med)):
            sg = [(r["i"], r["cdir"]) for r, m in zip(QDG, mask) if m]
            T = sim_qd(sg, ch)
            n = len(T); w = sum(1 for x in T if x["win"])
            e = float(np.mean([x["net"] for x in T])) if n else float("nan")
            rows.append((half, n, w, 100.0 * w / n if n else float("nan"), e))
            print("    %-9s %-24s n=%-4d W=%-4d %6.2f%%  %+.4f%%   binom p %.4f"
                  % (mlab, half, n, w, rows[-1][3], e, binom_ge(n, w)))
        z, p = two_prop(rows[1][1], rows[1][2], rows[0][1], rows[0][2])
        print("    %-9s HIGH minus LOW = %+.2f pp   z = %+.3f   two-sided p = %.4f"
              % (mlab, rows[1][3] - rows[0][3], z, 2 * min(p, 1 - p)))
    # terciles, unchained
    q1, q2 = np.quantile(vals, [1 / 3, 2 / 3])
    out = []
    for lo, hi, tg in ((-1e9, q1, "T1 low"), (q1, q2, "T2"), (q2, 1e9, "T3 high")):
        sg = [(r["i"], r["cdir"]) for r, v in zip(QDG, vals) if lo < v <= hi]
        T = sim_qd(sg, False)
        n = len(T); w = sum(1 for x in T if x["win"])
        out.append("%s n=%d %.1f%%" % (tg, n, 100.0 * w / n if n else float("nan")))
    print("    terciles (unchained): " + " | ".join(out))

# --------------------------------------------------------------------- Q5
print()
print("=" * 100)
print("Q5  POWER -- MDE in win-rate pp at 80% power, one-sided alpha 0.05")
print("=" * 100)
ZA, ZB = 1.6448536269514722, 0.8416212335729143


def mde(n1, n2, p2):
    d = 0.0
    for _ in range(200):
        p1 = p2 + d
        if p1 >= 0.999:
            break
        pb = (n1 * p1 + n2 * p2) / (n1 + n2)
        need = ZA * math.sqrt(pb * (1 - pb) * (1 / n1 + 1 / n2)) + \
            ZB * math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
        d = need
    return 100.0 * d


def mde_one(n1, p0):
    d = 0.0
    for _ in range(200):
        p1 = p0 + d
        d = ZA * math.sqrt(p0 * (1 - p0) / n1) + ZB * math.sqrt(p1 * (1 - p1) / n1)
    return 100.0 * d


print("  two-proportion, gate vs DISJOINT gated-OUT complement (the independent comparison):")
print("    unchained  n1=478 vs n2=727  base 43.05%%   MDE = %+.2f pp   observed lift %+.2f pp"
      % (mde(478, 727, 0.4305), 44.35 - 43.05))
print("    chained    n1=161 vs n2=208  base 44.71%%   MDE = %+.2f pp   observed lift %+.2f pp"
      % (mde(161, 208, 0.4471), 44.10 - 44.71))
print("  two-proportion, gate vs matched ride-all base (NESTED, so this SE is optimistic):")
print("    unchained  n1=478 vs n2=1205 base 43.57%%   MDE = %+.2f pp   observed lift %+.2f pp"
      % (mde(478, 1205, 0.4357), 44.35 - 43.57))
print("    chained    n1=161 vs n2=234  base 37.18%%   MDE = %+.2f pp   observed lift %+.2f pp"
      % (mde(161, 234, 0.3718), 44.10 - 37.18))
print("  one-sample (base treated as KNOWN, the most generous framing):")
print("    unchained  n=478  p0=43.57%%   MDE = %+.2f pp" % mde_one(478, 0.4357))
print("    chained    n=161  p0=37.18%%   MDE = %+.2f pp" % mde_one(161, 0.3718))
print("  vs break-even 48.889%%:")
print("    unchained  n=478   MDE over 48.889%% = %+.2f pp -> needs %.2f%% win   observed 44.35%%"
      % (mde_one(478, BE), 100 * BE + mde_one(478, BE)))
print("    chained    n=161   MDE over 48.889%% = %+.2f pp -> needs %.2f%% win   observed 44.10%%"
      % (mde_one(161, BE), 100 * BE + mde_one(161, BE)))
print("  SE of the observed lifts:")
print("    unchained vs complement  SE = %.2f pp  -> 95%% CI [%+.2f, %+.2f]"
      % (100 * se_diff(478, .4435, 727, .4305),
         1.30 - 196 * se_diff(478, .4435, 727, .4305), 1.30 + 196 * se_diff(478, .4435, 727, .4305)))
print("    chained   vs complement  SE = %.2f pp  -> 95%% CI [%+.2f, %+.2f]"
      % (100 * se_diff(161, .4410, 208, .4471),
         -0.61 - 196 * se_diff(161, .4410, 208, .4471), -0.61 + 196 * se_diff(161, .4410, 208, .4471)))

# --------------------------------------------------------------------- Q6
print()
print("=" * 100)
print("Q6  DOES THE GATE SELECT ON DISPLACEMENT AT ALL?")
print("=" * 100)


def dispstats(ids):
    pc, dp = [], []
    for i in ids:
        b = bars[i]
        rng = b["h"] - b["l"]
        if rng <= 0:
            continue
        d = 1 if b["c"] > b["o"] else -1
        pc.append((b["c"] - b["l"]) / rng if d > 0 else (b["h"] - b["c"]) / rng)
        mid = 0.5 * (b["h"] + b["l"])
        dp.append(100.0 * d * (b["c"] - mid) / b["c"])
    return np.array(pc), np.array(dp)


gset = [i for i in QD if abs(QD[i]["z"]) <= 0.5]
oset = [i for i in QD if abs(QD[i]["z"]) > 0.5]
for tag, ids in (("QUIET-DRIFT gated cohort", gset), ("gated-OUT complement", oset),
                 ("whole spec universe", list(QD))):
    pc, dp = dispstats(ids)
    print("  %-26s n=%-5d close-pct mean %.4f median %.4f | displacement mean %+.4f%% median %+.4f%%"
          % (tag, len(pc), pc.mean(), np.median(pc), dp.mean(), np.median(dp)))
pcg, dpg = dispstats(gset)
pco, dpo = dispstats(oset)
sd = math.sqrt(pcg.var(ddof=1) / len(pcg) + pco.var(ddof=1) / len(pco))
print("  close-pct  gated minus complement = %+.4f   t = %+.2f" % (pcg.mean() - pco.mean(),
                                                                   (pcg.mean() - pco.mean()) / sd))
sd2 = math.sqrt(dpg.var(ddof=1) / len(dpg) + dpo.var(ddof=1) / len(dpo))
print("  displacement gated minus complement = %+.4f%%   t = %+.2f"
      % (dpg.mean() - dpo.mean(), (dpg.mean() - dpo.mean()) / sd2))
print()
print("  reference: round4 reported the A_h2 cohort at close-pct 0.928 / displacement +0.333%%")
print("             against a universe 0.761 / +0.180%% (KS D=0.427, p=4e-13).")
