"""Does the 1m INTERNAL structure of a 5m ENGULF signal bar predict WIN vs LOSS (TP-first vs SL-first)?
Same disciplined design as the breakout 1m study: pull the 1m buckets inside the signal bar's [start,end) span, build
finish/shape features (all known at the signal's close -> a legit ENTRY filter, unlike the next 5m bar), compare
win vs loss via AUC + 500-shuffle NULL CALIBRATION + SPLIT-HALF. Caches the 1m arrays to study/_1m_arrays.npz.
Reports ALL, NORMAL (bias), REVERSAL separately.
"""
import os, sys
import numpy as np
from scipy.stats import rankdata
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from study.archive_loader import load_archive
from app import support_resistance as SR
from app import engulf5m_detect as E5
from app.engulf_sr_detect import _ohlc

# ---- 5m: engulf signals + win/loss ----
_, r5, _ = load_archive("5m", root=os.path.join("study", "recon_archive"))
A = sorted(r5, key=lambda b: float(b.get("start_time", 0) or 0)); n = len(A)
O = np.empty(n); C = np.empty(n); H = np.empty(n); Lo = np.empty(n); ST = np.empty(n); ET = np.empty(n)
for i, b in enumerate(A):
    o, c, h, l = _ohlc(b); O[i] = o; C[i] = c; H[i] = h; Lo[i] = l
    ST[i] = float(b.get("start_time", 0) or 0); ET[i] = float(b.get("end_time", 0) or 0)
sigs = E5.detect(A)
sys.stdout.write("5m loaded %d; engulf signals %d\n" % (n, len(sigs))); sys.stdout.flush()


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
sys.stdout.write("engulf signals with outcome: %d (win %.1f%%)\n"
                 % (len(ev), 100 * np.mean([e[2] for e in ev]))); sys.stdout.flush()

# ---- 1m arrays (cached) ----
CACHE = os.path.join("study", "_1m_arrays.npz")
if os.path.exists(CACHE):
    z = np.load(CACHE)
    st1, o1, c1, h1, l1, bv1, sv1 = z["st1"], z["o1"], z["c1"], z["h1"], z["l1"], z["bv1"], z["sv1"]
    sys.stdout.write("1m loaded from cache %d\n" % len(st1)); sys.stdout.flush()
else:
    _, r1, _ = load_archive("1m", root=os.path.join("study", "recon_archive"))
    r1.sort(key=lambda b: float(b.get("start_time", 0) or 0)); m1 = len(r1)
    st1 = np.empty(m1); o1 = np.empty(m1); c1 = np.empty(m1); h1 = np.empty(m1); l1 = np.empty(m1)
    bv1 = np.empty(m1); sv1 = np.empty(m1)
    for j, b in enumerate(r1):
        oo, cc, hh, ll = _ohlc(b); o1[j] = oo; c1[j] = cc; h1[j] = hh; l1[j] = ll
        st1[j] = float(b.get("start_time", 0) or 0)
        bv1[j] = float(b.get("buy_vol", 0) or 0); sv1[j] = float(b.get("sell_vol", 0) or 0)
    del r1
    np.savez(CACHE, st1=st1, o1=o1, c1=c1, h1=h1, l1=l1, bv1=bv1, sv1=sv1)
    sys.stdout.write("1m loaded+cached %d\n" % m1); sys.stdout.flush()
del r5
d1 = bv1 - sv1; vol1 = bv1 + sv1; rng1 = np.maximum(h1 - l1, 1e-9); dir1 = np.sign(c1 - o1); body1 = np.abs(c1 - o1)

FKEYS = ["n1m", "aln_frac", "daln_frac", "net_daln", "thrust_pos", "thrust_pos_rng", "last_aln", "last_daln",
         "last_cloc_aln", "last_break_wick", "accel_d", "accel_rng", "max_run_aln", "opp_frac", "big1_share",
         "front_load", "mean_b2r"]


def internal_feats(p0, p1, side):
    m = p1 - p0
    if m < 3: return None
    sl = slice(p0, p1); dd = d1[sl]; vv = vol1[sl]; rr = rng1[sl]; di = dir1[sl]
    oo = o1[sl]; cc = c1[sl]; hh = h1[sl]; ll = l1[sl]; tv = vv.sum() or 1e-9
    aln = (di == side).astype(float); dsign = np.sign(dd)
    lr = max(hh[-1] - ll[-1], 1e-9); last_cloc = (cc[-1] - ll[-1]) / lr
    bw = ((hh[-1] - max(oo[-1], cc[-1])) if side > 0 else (min(oo[-1], cc[-1]) - ll[-1])) / lr
    half = m // 2
    accel_d = ((dd[half:] * side).mean() - (dd[:half] * side).mean()) / (tv / m)
    run = mx = 0
    for a in aln:
        run = run + 1 if a > 0 else 0; mx = max(mx, run)
    cum = np.cumsum(dd * side); net = cum[-1]; front = (cum[half - 1] / net) if abs(net) > 1e-9 else 0.5
    return {"n1m": float(m), "aln_frac": aln.mean(), "daln_frac": (dsign == side).mean(),
            "net_daln": dd.sum() * side / tv, "thrust_pos": int(np.argmax(np.abs(dd))) / (m - 1),
            "thrust_pos_rng": int(np.argmax(rr)) / (m - 1), "last_aln": 1.0 if di[-1] == side else 0.0,
            "last_daln": dd[-1] * side / (vv[-1] or 1e-9), "last_cloc_aln": (last_cloc - 0.5) * side,
            "last_break_wick": bw, "accel_d": accel_d, "accel_rng": rr[half:].mean() - rr[:half].mean(),
            "max_run_aln": float(mx), "opp_frac": (dsign == -side).mean(), "big1_share": rr.max() / (rr.sum() or 1e-9),
            "front_load": float(front), "mean_b2r": (body1[sl] / rr).mean()}


X = {f: [] for f in FKEYS}; Y = []; T = []; REV = []
for i, side, oc, rev in ev:
    p0 = int(np.searchsorted(st1, ST[i], "left")); p1 = int(np.searchsorted(st1, ET[i], "left"))
    ft = internal_feats(p0, p1, side)
    if ft is None: continue
    for f in FKEYS: X[f].append(ft[f])
    Y.append(oc); T.append(ST[i]); REV.append(rev)
Y = np.array(Y); T = np.array(T); REV = np.array(REV); N = len(Y)
Xa = {f: np.array(X[f], float) for f in FKEYS}
sys.stdout.write("engulf signals with >=3 inside 1m bars: %d (win %.1f%%)\n" % (N, 100 * Y.mean())); sys.stdout.flush()


def auc_from_rank(ranks, mask):
    ns = int(mask.sum()); nw = len(mask) - ns
    if ns < 8 or nw < 8: return None
    u = ranks[mask].sum() - ns * (ns + 1) / 2.0
    return u / (ns * nw)


def scan(label, sel):
    yy = Y[sel]; tt = T[sel]; nn = len(yy); win = (yy == 1)
    print("\n" + "=" * 82)
    print("%s | n=%d  WIN %.1f%%" % (label, nn, 100 * win.mean()))
    print("=" * 82)
    if win.sum() < 12 or (~win).sum() < 12:
        print("  (too few)"); return
    ranks = {f: rankdata(Xa[f][sel]) for f in FKEYS}
    real = {f: auc_from_rank(ranks[f], win) for f in FKEYS}
    print("  %-16s %10s %10s %8s" % ("feature", "win-mean", "loss-mean", "AUC"))
    for f in sorted(FKEYS, key=lambda k: abs((real[k] or 0.5) - 0.5), reverse=True):
        xf = Xa[f][sel]; a = real[f]
        print("  %-16s %10.3f %10.3f %8s" % (f, xf[win].mean(), xf[~win].mean(), ("%.3f" % a) if a else "  --"))
    best = max(abs((real[f] or 0.5) - 0.5) for f in FKEYS)
    rs = np.random.RandomState(11); ns = int(win.sum()); ge = 0
    for _ in range(500):
        perm = rs.permutation(nn); mask = np.zeros(nn, bool); mask[perm[:ns]] = True
        dev = max(abs((auc_from_rank(ranks[f], mask) or 0.5) - 0.5) for f in FKEYS)
        if dev >= best: ge += 1
    print("  real MAX|AUC-.5|=%.4f  |  NULL scan P(noise>=real)=%.1f%%" % (best, 100 * ge / 500.0))
    mid = nn // 2; order = np.argsort(tt); h1i = order[:mid]; h2i = order[mid:]
    top = sorted(FKEYS, key=lambda k: abs((real[k] or 0.5) - 0.5), reverse=True)[:4]
    print("  split-half (early|late AUC):", end="")
    for f in top:
        e = auc_from_rank(rankdata(Xa[f][sel][h1i]), win[h1i]); la = auc_from_rank(rankdata(Xa[f][sel][h2i]), win[h2i])
        print("  %s %.3f|%.3f" % (f, e or .5, la or .5), end="")
    print()


scan("ALL 5m ENGULF (1m-internal, win vs loss)", np.ones(N, bool))
scan("NORMAL (bias)", ~REV)
scan("REVERSAL", REV)
print("\nAUC>0.5 => feature higher in WINNERS. A feature is REAL only if it beats the NULL P-value AND holds sign")
print("across halves. Directional 1m features side-aligned to the trade. All known at the signal's close.")
