"""Does the 1m INTERNAL structure of a 5m Br (breakout) candle predict strong vs weak follow-through?
For each 5m breakout, pull the 1m buckets inside its [start,end) span and build shape/pattern features (where the
thrust lands, does it finish strong or exhaust, alignment, acceleration, single-thrust vs distributed). All known at
the break's close -> genuinely predictive. Strong/weak = the ATR-symmetric label from breakout_strength.

DISCIPLINE (per study/NEGATIVE_RESULTS_2026-07-21.md, which killed the 35-feature scan): ship NULL CALIBRATION
(shuffle labels 500x, count chance hits) + a SPLIT-HALF gate. A raw AUC means nothing without these.
"""
import os, sys, datetime as dt
import numpy as np
from scipy.stats import rankdata
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from study.archive_loader import load_archive
from app import support_resistance as SR
from app.engulf_sr_detect import _ohlc

K = SR.SR_PIVOT_K; ATR_W = 20; RR = 1.0
# ---- 5m: breakouts + strong/weak labels ----
_, r5, _ = load_archive("5m", root=os.path.join("study", "recon_archive"))
A = sorted(r5, key=lambda b: float(b.get("start_time", 0) or 0)); n = len(A)
O = np.empty(n); C = np.empty(n); H = np.empty(n); Lo = np.empty(n); ST = np.empty(n); ET = np.empty(n)
for i, b in enumerate(A):
    o, c, h, l = _ohlc(b); O[i] = o; C[i] = c; H[i] = h; Lo[i] = l
    ST[i] = float(b.get("start_time", 0) or 0); ET[i] = float(b.get("end_time", 0) or 0)
rng = H - Lo; ATR = np.zeros(n)
for i in range(n):
    a = max(0, i - ATR_W)
    if i > a: ATR[i] = np.median(rng[a:i])
levels = SR.detect(A, K, zone_mitigation=True)
brk = {}
for lv in levels:
    i1 = lv.get("i1")
    if i1 is not None:
        brk.setdefault(i1, 1 if lv["kind"] == "R" else -1)


def outcome(i, side):
    c = C[i]; atr = ATR[i]; up = c + RR * atr; dn = c - RR * atr
    for j in range(i + 1, n):
        adv = (H[j] >= up) if side > 0 else (Lo[j] <= dn)
        rev = (Lo[j] <= dn) if side > 0 else (H[j] >= up)
        if adv and rev: return 0
        if adv: return 1
        if rev: return 0
    return None


sys.stdout.write("5m loaded %d; labeling breakouts...\n" % n); sys.stdout.flush()
ev = []
for i, side in sorted(brk.items()):
    if not (ATR_W <= i < n - 1 and ATR[i] > 0): continue
    oc = outcome(i, side)
    if oc is None: continue
    ev.append((i, side, oc))
sys.stdout.write("labeled breakouts: %d\n" % len(ev)); sys.stdout.flush()

# ---- 1m: lean arrays (drop the dict list right after) ----
_, r1, _ = load_archive("1m", root=os.path.join("study", "recon_archive"))
r1.sort(key=lambda b: float(b.get("start_time", 0) or 0)); m1 = len(r1)
st1 = np.empty(m1); o1 = np.empty(m1); c1 = np.empty(m1); h1 = np.empty(m1); l1 = np.empty(m1)
bv1 = np.empty(m1); sv1 = np.empty(m1)
for j, b in enumerate(r1):
    oo, cc, hh, ll = _ohlc(b); o1[j] = oo; c1[j] = cc; h1[j] = hh; l1[j] = ll
    st1[j] = float(b.get("start_time", 0) or 0)
    bv1[j] = float(b.get("buy_vol", 0) or 0); sv1[j] = float(b.get("sell_vol", 0) or 0)
del r1, r5
d1 = bv1 - sv1; vol1 = bv1 + sv1; rng1 = np.maximum(h1 - l1, 1e-9); body1 = np.abs(c1 - o1)
dir1 = np.sign(c1 - o1)
sys.stdout.write("1m loaded %d\n" % m1); sys.stdout.flush()

FKEYS = ["n1m", "aln_frac", "daln_frac", "net_daln", "thrust_pos", "thrust_pos_rng", "last_aln", "last_daln",
         "last_cloc_aln", "last_break_wick", "accel_d", "accel_rng", "max_run_aln", "opp_frac", "big1_share",
         "front_load", "mean_b2r"]


def internal_feats(p0, p1, side):
    m = p1 - p0
    if m < 3: return None
    sl = slice(p0, p1)
    dd = d1[sl]; vv = vol1[sl]; rr = rng1[sl]; di = dir1[sl]; oo = o1[sl]; cc = c1[sl]; hh = h1[sl]; ll = l1[sl]
    tv = vv.sum() or 1e-9
    aln = (di == side).astype(float)
    dsign = np.sign(dd)
    idxmaxd = int(np.argmax(np.abs(dd))); idxmaxr = int(np.argmax(rr))
    # last bar
    lr = max(hh[-1] - ll[-1], 1e-9)
    last_cloc = (cc[-1] - ll[-1]) / lr
    bw = ((hh[-1] - max(oo[-1], cc[-1])) if side > 0 else (min(oo[-1], cc[-1]) - ll[-1])) / lr
    # accel: 2nd half minus 1st half, aligned
    half = m // 2
    accel_d = ((dd[half:] * side).mean() - (dd[:half] * side).mean()) / (tv / m)
    accel_rng = rr[half:].mean() - rr[:half].mean()
    # longest aligned run
    run = mx = 0
    for a in aln:
        run = run + 1 if a > 0 else 0; mx = max(mx, run)
    # front-load: share of net aligned delta accumulated by the midpoint
    cum = np.cumsum(dd * side); net = cum[-1]
    front = (cum[half - 1] / net) if abs(net) > 1e-9 else 0.5
    return {
        "n1m": float(m), "aln_frac": aln.mean(), "daln_frac": (dsign == side).mean(),
        "net_daln": dd.sum() * side / tv, "thrust_pos": idxmaxd / (m - 1), "thrust_pos_rng": idxmaxr / (m - 1),
        "last_aln": 1.0 if di[-1] == side else 0.0, "last_daln": dd[-1] * side / (vv[-1] or 1e-9),
        "last_cloc_aln": (last_cloc - 0.5) * side, "last_break_wick": bw, "accel_d": accel_d, "accel_rng": accel_rng,
        "max_run_aln": float(mx), "opp_frac": (dsign == -side).mean(), "big1_share": rr.max() / (rr.sum() or 1e-9),
        "front_load": float(front), "mean_b2r": (body1[sl] / rr).mean(),
    }


X = {f: [] for f in FKEYS}; Y = []; T = []
for i, side, oc in ev:
    p0 = int(np.searchsorted(st1, ST[i], "left")); p1 = int(np.searchsorted(st1, ET[i], "left"))
    ft = internal_feats(p0, p1, side)
    if ft is None: continue
    for f in FKEYS: X[f].append(ft[f])
    Y.append(oc); T.append(ST[i])
Y = np.array(Y); T = np.array(T); N = len(Y)
sys.stdout.write("breakouts with >=3 inside 1m bars: %d  (strong %.1f%%)\n" % (N, 100 * Y.mean())); sys.stdout.flush()


def auc_from_rank(ranks, mask):
    ns = mask.sum(); nw = len(mask) - ns
    if ns < 8 or nw < 8: return None
    u = ranks[mask].sum() - ns * (ns + 1) / 2.0
    return u / (ns * nw)


ranks = {f: rankdata(np.array(X[f], float)) for f in FKEYS}
strong = (Y == 1)
real = {f: auc_from_rank(ranks[f], strong) for f in FKEYS}
print("\n" + "=" * 78)
print("1m-INTERNAL features of a 5m break | N=%d | strong vs weak | AUC" % N)
print("=" * 78)
print("  %-16s %11s %11s %8s" % ("feature", "strong-mean", "weak-mean", "AUC"))
sa = np.array([X[f] for f in FKEYS])  # for means
for f in sorted(FKEYS, key=lambda k: abs((real[k] or 0.5) - 0.5), reverse=True):
    xf = np.array(X[f], float); a = real[f]
    print("  %-16s %11.3f %11.3f %8s" % (f, xf[strong].mean(), xf[~strong].mean(),
          ("%.3f" % a) if a is not None else "  --"))
best = max((abs((real[f] or 0.5) - 0.5) for f in FKEYS))
print("\nreal MAX |AUC-0.5| across %d features = %.4f" % (len(FKEYS), best))

# ---- NULL CALIBRATION: shuffle labels 500x, count chance 'hits' >= real best ----
rng_seed = np.random.RandomState(7); ns = int(strong.sum())
null_best = []; null_ge = 0
for s in range(500):
    perm = rng_seed.permutation(N); mask = np.zeros(N, bool); mask[perm[:ns]] = True
    dev = max(abs((auc_from_rank(ranks[f], mask) or 0.5) - 0.5) for f in FKEYS)
    null_best.append(dev)
    if dev >= best: null_ge += 1
null_best = np.array(null_best)
print("NULL: max|AUC-0.5| over 17 shuffled features -> mean %.4f  p90 %.4f  max %.4f" %
      (null_best.mean(), np.percentile(null_best, 90), null_best.max()))
print("P(noise's best feature >= real best) = %.1f%%   <-- the real p-value of this scan" % (100 * null_ge / 500.0))

# ---- SPLIT-HALF: does the top feature hold on both time halves? ----
mid = N // 2; order = np.argsort(T); h1i = order[:mid]; h2i = order[mid:]
print("\nSPLIT-HALF (by time) — AUC on early vs late half:")
top = sorted(FKEYS, key=lambda k: abs((real[k] or 0.5) - 0.5), reverse=True)[:5]
for f in top:
    r = ranks[f]
    def half_auc(idx):
        xf = np.array(X[f], float)[idx]; yy = strong[idx]; rr2 = rankdata(xf)
        return auc_from_rank(rr2, yy)
    print("  %-16s full %.3f | early %.3f | late %.3f" %
          (f, real[f], half_auc(h1i) or 0.5, half_auc(h2i) or 0.5))
print("\nVerdict rule (ledger): a feature is real only if it beats the NULL scan p-value AND holds sign across halves.")
