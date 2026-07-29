"""Turn the 1m-finish signal into a tradeable read: finish-strength composite, strong-rate by tercile (full +
split-half), and whether the strong-finish subset clears the fee wall at 1:1 and 1:2 ATR brackets. Shares the
study/_1m_arrays.npz cache with the engulf 1m study."""
import os, sys
import numpy as np
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from study.archive_loader import load_archive
from app import support_resistance as SR
from app.engulf_sr_detect import _ohlc
K = SR.SR_PIVOT_K; ATR_W = 20; FEE = 0.08
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
levels = SR.detect(A, K, zone_mitigation=True); brk = {}
for lv in levels:
    i1 = lv.get("i1")
    if i1 is not None: brk.setdefault(i1, 1 if lv["kind"] == "R" else -1)


def outc(i, side, rr):
    c = C[i]; atr = ATR[i]; tgt = c + rr * atr * side; stp = c - 1.0 * atr * side
    for j in range(i + 1, n):
        adv = (H[j] >= tgt) if side > 0 else (Lo[j] <= tgt)
        rev = (Lo[j] <= stp) if side > 0 else (H[j] >= stp)
        if adv and rev: return 0
        if adv: return 1
        if rev: return 0
    return None


ev = []
for i, side in sorted(brk.items()):
    if not (ATR_W <= i < n - 1 and ATR[i] > 0): continue
    o1_ = outc(i, side, 1.0); o2_ = outc(i, side, 2.0)
    if o1_ is None: continue
    ev.append((i, side, o1_, o2_ if o2_ is not None else -1))

CACHE = os.path.join("study", "_1m_arrays.npz")
if os.path.exists(CACHE):
    z = np.load(CACHE); st1, o1, c1, h1, l1, bv1, sv1 = z["st1"], z["o1"], z["c1"], z["h1"], z["l1"], z["bv1"], z["sv1"]
    sys.stdout.write("1m from cache %d\n" % len(st1)); sys.stdout.flush()
else:
    _, r1, _ = load_archive("1m", root=os.path.join("study", "recon_archive"))
    r1.sort(key=lambda b: float(b.get("start_time", 0) or 0)); m1 = len(r1)
    st1 = np.empty(m1); o1 = np.empty(m1); c1 = np.empty(m1); h1 = np.empty(m1); l1 = np.empty(m1)
    bv1 = np.empty(m1); sv1 = np.empty(m1)
    for j, b in enumerate(r1):
        oo, cc, hh, ll = _ohlc(b); o1[j] = oo; c1[j] = cc; h1[j] = hh; l1[j] = ll
        st1[j] = float(b.get("start_time", 0) or 0); bv1[j] = float(b.get("buy_vol", 0) or 0); sv1[j] = float(b.get("sell_vol", 0) or 0)
    del r1
    np.savez(CACHE, st1=st1, o1=o1, c1=c1, h1=h1, l1=l1, bv1=bv1, sv1=sv1)
    sys.stdout.write("1m loaded+cached %d\n" % m1); sys.stdout.flush()
del r5
d1 = bv1 - sv1; dir1 = np.sign(c1 - o1)


def finish(p0, p1, side):
    m = p1 - p0
    if m < 3: return None
    dd = d1[p0:p1]; di = dir1[p0:p1]; oo = o1[p0:p1]; cc = c1[p0:p1]; hh = h1[p0:p1]; ll = l1[p0:p1]
    lr = max(hh[-1] - ll[-1], 1e-9); last_cloc = ((cc[-1] - ll[-1]) / lr - 0.5) * side
    bw = ((hh[-1] - max(oo[-1], cc[-1])) if side > 0 else (min(oo[-1], cc[-1]) - ll[-1])) / lr
    aln = (di == side).astype(float); half = m // 2; cum = np.cumsum(dd * side); net = cum[-1]
    front = (cum[half - 1] / net) if abs(net) > 1e-9 else 0.5
    return (last_cloc, 1.0 if di[-1] == side else 0.0, dd[-1] * side / max(bv1[p1 - 1] + sv1[p1 - 1], 1e-9),
            aln.mean(), bw, float(front))


rows = []
for i, side, y1, y2 in ev:
    p0 = int(np.searchsorted(st1, ST[i], "left")); p1 = int(np.searchsorted(st1, ET[i], "left"))
    f = finish(p0, p1, side)
    if f is None: continue
    rows.append((ST[i], y1, y2, ATR[i] / C[i] * 100.0) + f)
R = np.array(rows, float)
t = R[:, 0]; Y1 = R[:, 1].astype(int); Y2 = R[:, 2].astype(int); atrpct = R[:, 3]


def z(x): s = x.std(); return (x - x.mean()) / s if s > 0 else x * 0


comp = z(R[:, 4]) + z(R[:, 5]) + z(R[:, 6]) + z(R[:, 7]) - z(R[:, 8]) - z(R[:, 9])
N = len(Y1)
print("N=%d  base strong(1:1)=%.1f%%  base reach+2ATR=%.1f%%  median ATR%%=%.3f"
      % (N, 100 * Y1.mean(), 100 * (Y2 == 1).mean(), np.median(atrpct)))
mA = np.median(atrpct)
print("fee break-even: 1:1 needs %.1f%% wins | 1:2 needs %.1f%% wins" % (100 * (0.5 + FEE / (2 * mA)), 100 * ((FEE + mA) / (3 * mA))))
order = np.argsort(comp); ter = np.array_split(order, 3); lab = ["WEAK-finish (bot 3rd)", "MID", "STRONG-finish (top 3rd)"]
print("\n  %-24s %6s %12s %13s %14s" % ("composite tercile", "n", "strong%(1:1)", "reach+2ATR%", "net@1:2 %/tr"))
for k, idx in enumerate(ter):
    w1 = 100 * Y1[idx].mean(); valid2 = Y2[idx] >= 0; wr2 = (Y2[idx][valid2] == 1).mean() if valid2.any() else 0.0
    a = np.median(atrpct[idx]); net2 = wr2 * 2 * a - (1 - wr2) * a - FEE
    print("  %-24s %6d %11.1f %12.1f %13.3f" % (lab[k], len(idx), w1, 100 * wr2, net2))
mid = N // 2; torder = np.argsort(t); h1i = set(torder[:mid].tolist()); strong_idx = ter[2]
ie = np.array([j for j in strong_idx if j in h1i]); il = np.array([j for j in strong_idx if j not in h1i])
print("\nSTRONG-finish tercile strong%%(1:1): early %.1f%% (n=%d) | late %.1f%% (n=%d)"
      % (100 * Y1[ie].mean() if len(ie) else 0, len(ie), 100 * Y1[il].mean() if len(il) else 0, len(il)))
print("\nVerdict: real signal only becomes an EDGE if a tercile's strong%%/reach%% clears the fee break-even above.")
