"""Do the features PREDICT a break? Compare candles that TEST an active S/R (range reaches it) and BREAK (close
through) vs REJECT (close back). AUC per feature for the test candle itself AND its previous candle (a true leading
predictor). Directional features aligned to the level's break direction. 5m, same 30 random days.
"""
import os, sys, datetime as dt, random
import numpy as np
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from study.archive_loader import load_archive
from app import support_resistance as SR
from app import absorption as ABS
from app.footprint_panel import profile_skewness
from app.engulf_sr_detect import _ohlc

K = SR.SR_PIVOT_K
_, rows, _ = load_archive("5m", root=os.path.join("study", "recon_archive"))
A = sorted(rows, key=lambda b: float(b.get("start_time", 0) or 0)); n = len(A)
O = np.empty(n); C = np.empty(n); H = np.empty(n); Lo = np.empty(n); DAY = [None] * n
buy = np.empty(n); sell = np.empty(n)
for i, b in enumerate(A):
    o, c, h, l = _ohlc(b); O[i] = o; C[i] = c; H[i] = h; Lo[i] = l
    buy[i] = float(b.get("buy_vol", 0) or 0); sell[i] = float(b.get("sell_vol", 0) or 0)
    st = float(b.get("start_time", 0) or 0)
    if st > 0:
        DAY[i] = dt.datetime.utcfromtimestamp(st).date()
sys.stdout.write("loaded %d\n" % n); sys.stdout.flush()
random.seed(30)
seldays = set(random.sample(sorted({d for d in DAY if d}), 30))

levels = SR.detect(A)
cls = {}                                             # candle -> (is_break, kind); prefer a break if it broke anything
for lv in levels:
    kind = lv["kind"]; price = lv["price"]; start = lv["i0"] + K; end = lv["i1"] if lv["i1"] is not None else n
    for j in range(start, min(end + 1, n)):
        if DAY[j] not in seldays or j < 1:
            continue
        if kind == "R":
            if H[j] < price:
                continue
            brk = C[j] > price
        else:
            if Lo[j] > price:
                continue
            brk = C[j] < price
        if j not in cls or (brk and not cls[j][0]):
            cls[j] = (brk, kind)
brk_idx = [(j, k) for j, (b, k) in cls.items() if b]
rej_idx = [(j, k) for j, (b, k) in cls.items() if not b]
sys.stdout.write("tests in sample: BREAK %d  REJECT %d\n" % (len(brk_idx), len(rej_idx))); sys.stdout.flush()

SIGNED0 = {"dir", "delta", "dvpin", "da2", "skew", "tick_imb", "oi_open", "oi_open_n", "A", "A_h2"}
SHARE5 = {"buy_share", "close_loc", "er_share"}


def feats(i):
    b = A[i]; o, c, h, l = O[i], C[i], H[i], Lo[i]; bv = buy[i]; sv = sell[i]; vol = bv + sv or 1e-9
    d = bv - sv; dh1 = float(b.get("delta_h1", 0) or 0); rng = max(h - l, 1e-9)
    up = float(b.get("up_ticks", 0) or 0); dn = float(b.get("dn_ticks", 0) or 0)
    ber = float(b.get("buyer_er", 0) or 0); ser = float(b.get("seller_er", 0) or 0)
    oi = (float(b.get("opL", 0) or 0) + float(b.get("opS", 0) or 0)) - (float(b.get("clL", 0) or 0) + float(b.get("clS", 0) or 0))
    try: Aab = ABS.absorption(A, i)[0]
    except Exception: Aab = None
    try: Ah2 = ABS.absorption_halves(A, i)[1]
    except Exception: Ah2 = None
    sk = profile_skewness(b.get("levels"))
    return {"dir": 1.0 if c > o else (-1.0 if c < o else 0.0), "body_pct": abs(c - o) / o * 100,
            "range_pct": rng / o * 100, "body_to_range": abs(c - o) / rng, "upper_wick": (h - max(o, c)) / o * 100,
            "lower_wick": (min(o, c) - l) / o * 100, "close_loc": (c - l) / rng, "vpin": abs(d) / vol, "dvpin": d / vol,
            "buy_share": bv / vol, "delta": d, "da2": (d - 2 * dh1) / vol, "A": Aab, "A_h2": Ah2, "skew": sk,
            "oi_open": oi, "oi_open_n": oi / vol, "buyer_er": ber, "seller_er": ser,
            "er_share": ber / (ber + ser) if (ber + ser) > 0 else 0.5,
            "tick_imb": (up - dn) / (up + dn) if (up + dn) > 0 else 0.0,
            "vel_ratio": float(b.get("vel_ratio", 0) or 0)}


FKEYS = list(feats(brk_idx[0][0]).keys())


def align(f, v, k):
    bs = 1.0 if k == "R" else -1.0
    if f in SIGNED0: return v * bs
    if f in SHARE5: return (v - 0.5) * bs
    return v


def collect(idxs, off):
    d = {f: [] for f in FKEYS}
    for j, k in idxs:
        jj = j + off
        if jj < 0 or jj >= n:
            continue
        fd = feats(jj)
        for f, v in fd.items():
            if v is not None:
                d[f].append(align(f, v, k))
    return d


def auc(pos, neg):
    pos = np.array(pos, float); neg = np.array(neg, float)
    if len(pos) < 5 or len(neg) < 5:
        return None
    allv = np.concatenate([pos, neg]); order = allv.argsort(kind="mergesort")
    ranks = np.empty(len(allv)); ranks[order] = np.arange(1, len(allv) + 1)
    u = ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2.0
    return u / (len(pos) * len(neg))


bC = collect(brk_idx, 0); rC = collect(rej_idx, 0)          # the test candle itself
bP = collect(brk_idx, -1); rP = collect(rej_idx, -1)        # its PREVIOUS candle (leading)
rows_out = []
for f in FKEYS:
    ac = auc(bC[f], rC[f]); ap = auc(bP[f], rP[f])
    bm = np.mean(bC[f]) if bC[f] else 0; rm = np.mean(rC[f]) if rC[f] else 0
    rows_out.append((abs((ac or 0.5) - 0.5), f, bm, rm, ac, ap))
print("=" * 96)
print("PREDICT-THE-BREAK | break %d vs reject %d at active S/R | AUC>0.5 => higher in BREAK candles" % (len(brk_idx), len(rej_idx)))
print("=" * 96)
print("  %-13s %10s %10s %10s %10s" % ("feature", "break-mean", "reject-mean", "AUC(cand)", "AUC(prev)"))
for _, f, bm, rm, ac, ap in sorted(rows_out, reverse=True):
    print("  %-13s %10.3f %10.3f %10s %10s" % (f, bm, rm, ("%.3f" % ac) if ac else "  --", ("%.3f" % ap) if ap else "  --"))
print("\nAUC 0.50 = no separation; >0.60 useful; on-candle body/close/flow is partly DEFINITIONAL (a break IS a")
print("directional close-through). The honest predictors are AUC(prev) and non-shape on-candle features (vpin/oi/absorb).")
