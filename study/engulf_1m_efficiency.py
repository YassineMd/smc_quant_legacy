"""Do path-EFFICIENCY metrics (Kaufman ER, R2, micro-MAE, delta-ER) on the 1m constituents of a 5m ENGULF
signal predict WIN vs LOSS BETTER than the current finish tier (app.finish_strength)? Same harness as
engulf_1m_internal: recon 5m signals + TP/SL outcome, causal 1m features, AUC + 500-shuffle NULL + split-half,
plus disjoint-tercile win-rates w/ exact binomial. Runs a 10-day slice AND the full set. Single process.
"""
import os, sys
import numpy as np
from scipy.stats import rankdata, binomtest
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from study.archive_loader import load_archive
from app import engulf5m_detect as E5
from app import finish_strength as FIN
from app.engulf_sr_detect import _ohlc

# ---- 5m engulf signals + win/loss (identical to engulf_1m_internal) ----
_, r5, _ = load_archive("5m", root=os.path.join("study", "recon_archive"))
A = sorted(r5, key=lambda b: float(b.get("start_time", 0) or 0)); n = len(A)
O = np.empty(n); C = np.empty(n); H = np.empty(n); Lo = np.empty(n); ST = np.empty(n); ET = np.empty(n)
for i, b in enumerate(A):
    o, c, h, l = _ohlc(b); O[i] = o; C[i] = c; H[i] = h; Lo[i] = l
    ST[i] = float(b.get("start_time", 0) or 0); ET[i] = float(b.get("end_time", 0) or 0)
sigs = E5.detect(A)
print("5m loaded %d; engulf signals %d" % (n, len(sigs))); sys.stdout.flush()


def outcome(s):
    i = s["i"]; side = s["side"]; tp = s["tp"]; sl = s["sl"]
    for j in range(i + 1, n):
        hs = (Lo[j] <= sl) if side > 0 else (H[j] >= sl)
        ht = (H[j] >= tp) if side > 0 else (Lo[j] <= tp)
        if hs and ht: return 0
        if ht: return 1
        if hs: return 0
    return None


ev = []
for s in sigs:
    if not (1 <= s["i"] < n - 1): continue
    oc = outcome(s)
    if oc is None: continue
    ev.append((s["i"], s["side"], oc, bool(s["rev"])))
print("engulf signals with outcome: %d (win %.1f%%)" % (len(ev), 100 * np.mean([e[2] for e in ev]))); sys.stdout.flush()

# ---- 1m arrays (cached) ----
CACHE = os.path.join("study", "_1m_arrays.npz")
z = np.load(CACHE)
st1, o1, c1, h1, l1, bv1, sv1 = z["st1"], z["o1"], z["c1"], z["h1"], z["l1"], z["bv1"], z["sv1"]
d1 = bv1 - sv1; vol1 = bv1 + sv1
print("1m loaded from cache %d" % len(st1)); sys.stdout.flush()
del r5

NEW = ["price_er", "delta_er", "r2", "micro_mae", "smi"]     # the hypotheses under test
BASE = ["fin_strong", "ring"]                                # current production finish tier (to beat)
REF = ["aln_frac", "daln_frac", "net_daln"]                  # path-alignment cousins already in engulf_1m_internal
FKEYS = NEW + BASE + REF


def feats(p0, p1, side):
    m = p1 - p0
    if m < 3: return None
    sl = slice(p0, p1)
    c = c1[sl]; o = o1[sl]; h = h1[sl]; l = l1[sl]; d = d1[sl]
    gross = np.abs(np.diff(c)).sum()
    price_er = (c[-1] - c[0]) * side / gross if gross > 1e-12 else 0.0     # Kaufman ER, signed to the trade side
    gd = np.abs(d).sum()
    delta_er = (d.sum() * side) / gd if gd > 1e-12 else 0.0               # net signed / gross delta path
    t = np.arange(m, dtype=float)
    if np.ptp(c) < 1e-12:
        r2 = 0.0
    else:
        cc = np.corrcoef(t, c)[0, 1]; r2 = float(cc * cc) if np.isfinite(cc) else 0.0
    rngw = max(h.max() - l.min(), 1e-9)
    if side > 0:
        mae = (np.maximum.accumulate(c) - c).max()
    else:
        mae = (c - np.minimum.accumulate(c)).max()
    micro_mae = mae / rngw                                               # worst adverse excursion / window range
    smi = max(price_er, 0.0) * r2                                        # Gemini's composite ER*R2
    di = np.sign(c - o)
    aln_frac = float((di == side).mean()); daln_frac = float((np.sign(d) == side).mean())
    net_daln = d.sum() * side / (vol1[sl].sum() or 1e-9)
    sub = [{"open": o1[j], "close": c1[j], "high": h1[j], "low": l1[j],
            "buy_vol": bv1[j], "sell_vol": sv1[j]} for j in range(p0, p1)]
    fs = FIN.strong_finish(sub, side); fin_strong = 0.0 if fs is None else float(fs)
    ring = float(FIN.ring_tier(sub, side))
    return dict(price_er=price_er, delta_er=delta_er, r2=r2, micro_mae=micro_mae, smi=smi,
                aln_frac=aln_frac, daln_frac=daln_frac, net_daln=net_daln, fin_strong=fin_strong, ring=ring)


X = {f: [] for f in FKEYS}; Y = []; T = []; REV = []
for i, side, oc, rev in ev:
    p0 = int(np.searchsorted(st1, ST[i], "left")); p1 = int(np.searchsorted(st1, ET[i], "left"))
    ft = feats(p0, p1, side)
    if ft is None: continue
    for f in FKEYS: X[f].append(ft[f])
    Y.append(oc); T.append(ST[i]); REV.append(rev)
Y = np.array(Y); T = np.array(T); REV = np.array(REV); N = len(Y)
Xa = {f: np.array(X[f], float) for f in FKEYS}
print("engulf signals w/ >=3 inside 1m bars: %d (win %.1f%%)" % (N, 100 * Y.mean())); sys.stdout.flush()
print("collinearity: corr(price_er,r2)=%.2f  corr(price_er,delta_er)=%.2f  corr(price_er,aln_frac)=%.2f"
      % (np.corrcoef(Xa["price_er"], Xa["r2"])[0, 1], np.corrcoef(Xa["price_er"], Xa["delta_er"])[0, 1],
         np.corrcoef(Xa["price_er"], Xa["aln_frac"])[0, 1]))


def auc(ranks, mask):
    ns = int(mask.sum()); nw = len(mask) - ns
    if ns < 8 or nw < 8: return None
    u = ranks[mask].sum() - ns * (ns + 1) / 2.0
    return u / (ns * nw)


def bands(x, y, f):
    """Disjoint terciles of feature f: win-rate per band + exact-binomial p vs the subset base rate."""
    base = y.mean()
    qs = np.quantile(x, [1 / 3, 2 / 3])
    lab = ["low ", "mid ", "high"]
    edges = [(-np.inf, qs[0]), (qs[0], qs[1]), (qs[1], np.inf)]
    out = []
    for (lo, hi), nm in zip(edges, lab):
        msk = (x > lo) & (x <= hi) if lo != -np.inf else (x <= hi)
        k = int(y[msk].sum()); nn = int(msk.sum())
        if nn == 0: out.append("%s n=0" % nm); continue
        p = binomtest(k, nn, base).pvalue
        out.append("%s n=%3d win %4.1f%% p=%.2f" % (nm, nn, 100 * k / nn, p))
    return "  |  ".join(out)


def scan(label, sel):
    yy = Y[sel]; tt = T[sel]; nn = len(yy); win = (yy == 1)
    print("\n" + "=" * 96)
    print("%s | n=%d  WIN %.1f%%" % (label, nn, 100 * win.mean())); print("=" * 96)
    if win.sum() < 12 or (~win).sum() < 12:
        print("  (too few winners/losers for AUC — n=%d win=%d)" % (nn, int(win.sum()))); return
    ranks = {f: rankdata(Xa[f][sel]) for f in FKEYS}
    real = {f: auc(ranks[f], win) for f in FKEYS}
    print("  %-11s %10s %10s %8s   group" % ("feature", "win-mean", "loss-mean", "AUC"))
    grp = {**{f: "NEW " for f in NEW}, **{f: "BASE" for f in BASE}, **{f: "ref " for f in REF}}
    for f in sorted(FKEYS, key=lambda k: abs((real[k] or 0.5) - 0.5), reverse=True):
        xf = Xa[f][sel]; a = real[f]
        print("  %-11s %10.3f %10.3f %8s   %s" % (f, xf[win].mean(), xf[~win].mean(),
                                                  ("%.3f" % a) if a else "  --", grp[f]))
    best_new = max(abs((real[f] or 0.5) - 0.5) for f in NEW)
    best_base = max(abs((real[f] or 0.5) - 0.5) for f in BASE)
    rs = np.random.RandomState(11); ns = int(win.sum()); ge = 0
    for _ in range(500):
        perm = rs.permutation(nn); mask = np.zeros(nn, bool); mask[perm[:ns]] = True
        dev = max(abs((auc(ranks[f], mask) or 0.5) - 0.5) for f in NEW)
        if dev >= best_new: ge += 1
    print("  NEW-metrics best |AUC-.5|=%.4f  vs BASE(finish) best=%.4f  |  NULL P(noise>=NEW)=%.1f%%"
          % (best_new, best_base, 100 * ge / 500.0))
    mid = nn // 2; order = np.argsort(tt); h1i = order[:mid]; h2i = order[mid:]
    top = sorted(NEW + BASE, key=lambda k: abs((real[k] or 0.5) - 0.5), reverse=True)[:4]
    print("  split-half (early|late AUC):", end="")
    for f in top:
        e = auc(rankdata(Xa[f][sel][h1i]), win[h1i]); la = auc(rankdata(Xa[f][sel][h2i]), win[h2i])
        print("  %s %.3f|%.3f" % (f, e or .5, la or .5), end="")
    print()
    bf = max(NEW, key=lambda k: abs((real[k] or 0.5) - 0.5))
    print("  bands[%s] %s" % (bf, bands(Xa[bf][sel], yy, bf)))
    print("  bands[ring] %s" % bands(Xa["ring"][sel], yy, "ring"))


slice10 = T >= (T.max() - 10 * 86400)
scan("(A) LAST 10 DAYS of recon (2026-06-09..06-19) — ALL engulf", slice10)
scan("(B) FULL 18mo — ALL engulf", np.ones(N, bool))
scan("(C) FULL 18mo — NORMAL (bias)", ~REV)
scan("(D) FULL 18mo — REVERSAL", REV)
print("\nAUC>0.5 => feature higher in WINNERS. A NEW metric only counts as an edge if it (1) beats the NULL P,")
print("(2) holds sign across the split-half, AND (3) its AUC exceeds BASE (the current finish tier). micro_mae")
print("is expected AUC<0.5 (deeper pullback in losers). Fee context: engulf finish edge sits behind the ~0.08% wall.")
