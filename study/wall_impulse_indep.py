# -*- coding: utf-8 -*-
"""Is the APPROACH IMPULSE (er/dist/vel) independent of the LOCAL 2-factor (volume vr x penetration pen)?
Same population as the shipped P(resist): radar visits that resolve STRICTLY after entry (ko>k0), so vr/pen defined.
(1) correlation of impulse with vr/pen; (2) OOS incremental AUC: fit logistic on one year, test the other, compare
AUC(vr+pen) vs AUC(vr+pen+er), both directions; (3) impulse hold-gap WITHIN volume x penetration cells. Both yr."""
import os, sys, bisect
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from math import fsum
from datetime import datetime, timezone
from scipy.optimize import minimize
from study.archive_loader import load_archive
from study.candle_bias_1h import _f, auc_p
from app import absorption_level_detect as AL

LF, DECAY, STR, MAXLEG, EPS = 24, 0.6, 0.12, 96, 1e-9
print("loading 15m + detect ...", flush=True)
_, r15, _ = load_archive("15m", root="study/recon_archive")
A = sorted(r15, key=lambda b: _f(b.get("start_time", 0)))
for b in A:
    b["open"] = _f(b.get("open_price")); b["close"] = _f(b.get("close_price"))
n = len(A)
C = np.array([b["close"] for b in A]); H = np.array([_f(b.get("high")) for b in A]); L = np.array([_f(b.get("low")) for b in A])
CVv = np.array([_f(b.get("curr_vol")) for b in A])
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]

walls = AL.detect(A)
Sanch = set(); Ranch = set()
for w in walls:
    (Sanch if w["side"] == "S" else Ranch).add(w["i0"])
    for r in w["radar_runs"]:
        (Sanch if w["side"] == "S" else Ranch).add(r[0])
Sanch = sorted(Sanch); Ranch = sorted(Ranch)


def last_anchor(anchors, k0):
    i = bisect.bisect_left(anchors, k0) - 1
    return anchors[i] if (i >= 0 and 6 <= k0 - anchors[i] <= MAXLEG) else None


V = []
for w in walls:
    hits = w["hits"]; base = w["strength"] / (DECAY ** hits) if hits else w["strength"]
    P = w["price"]; band = w["band"]; side = w["side"]; r_lo = P - 3.0 * band; r_hi = P + 3.0 * band
    for j, (k0, k1, *_) in enumerate(w["radar_runs"]):
        if base * (DECAY ** j) < STR:
            continue
        ko = oc = None
        for k in range(k0, min(n, k0 + LF)):
            r = (0 if C[k] > r_hi else (1 if C[k] < r_lo else None)) if side == "R" else \
                (0 if C[k] < r_lo else (1 if C[k] > r_hi else None))
            if r is not None:
                ko, oc = k, r; break
        if oc is None or ko <= k0:                    # resolve strictly after entry -> vr/pen defined
            continue
        j0 = last_anchor(Sanch if side == "R" else Ranch, k0)
        if j0 is None:
            continue
        d = 1.0 if side == "R" else -1.0
        if (C[k0] - C[j0]) * d <= 0:
            continue
        m = k0 - j0
        if m < 6:
            continue
        # LOCAL 2-factor (exactly as shipped P(resist))
        bx = fsum(_f(vv.get("b")) + _f(vv.get("s")) for k in range(k0, ko)
                  for _p, vv in (A[k].get("levels") or {}).items() if r_lo <= _f(_p) <= r_hi)
        rmv = float(np.median(CVv[max(0, k0 - 200):k0])) if k0 > 5 else CVv[k0]
        if rmv <= 0:
            continue
        vr = (bx / (ko - k0)) / rmv
        pen = ((H[k0] - r_lo) if side == "R" else (r_hi - L[k0])) / (r_hi - r_lo)
        # APPROACH IMPULSE (pre-entry [j0,k0-1])
        pc = C[j0:k0]; p0 = pc[0]
        path = float(np.sum(np.abs(np.diff(pc)))) + EPS
        er = ((pc[-1] - pc[0]) * d) / path
        dist = ((pc[-1] - pc[0]) * d) / p0 * 100.0
        vel = dist / m
        V.append({"resist": oc, "brk": 1 - oc, "yr": YR[k0], "vr": vr, "pen": min(1.0, max(0.0, pen)),
                  "er": er, "dist": dist, "vel": vel})

N = len(V)
y = np.array([v["brk"] for v in V], float); yr = np.array([v["yr"] for v in V])
vr = np.array([v["vr"] for v in V]); pen = np.array([v["pen"] for v in V]); er = np.array([v["er"] for v in V])
lvr = np.log1p(vr)
print("\n=== IMPULSE independence: %d visits (ko>k0) | base RESIST %.1f%% ===" % (N, 100 * (1 - y.mean())), flush=True)

# (1) correlation
print("\n(1) correlation of impulse with the local factors:", flush=True)
print("    corr(er, log1p vr) = %+.3f    corr(er, pen) = %+.3f    corr(er, vel) = %+.3f" % (
    np.corrcoef(er, lvr)[0, 1], np.corrcoef(er, pen)[0, 1], np.corrcoef(er, np.array([v["vel"] for v in V]))[0, 1]), flush=True)


def rauc(feat):
    a = [v[feat] for v in V if v["resist"]]; b = [v[feat] for v in V if not v["resist"]]
    return auc_p(a, b)[0]


print("    univariate AUC(resist):  er=%.3f  vr=%.3f  pen=%.3f  (pen: higher->break, so <0.5 expected)" % (
    rauc("er"), rauc("vr"), rauc("pen")), flush=True)


# (2) OOS incremental AUC
def fit(cols, yy):
    X = np.column_stack([np.ones(len(yy))] + cols)
    def nll(wv):
        z = X @ wv; p = np.clip(1 / (1 + np.exp(-z)), 1e-9, 1 - 1e-9)
        return -np.mean(yy * np.log(p) + (1 - yy) * np.log(1 - p))
    return minimize(nll, np.zeros(X.shape[1]), method="BFGS").x


def lin(w, cols):
    return np.column_stack([np.ones(len(cols[0]))] + cols) @ w


print("\n(2) OOS: does er add AUC on top of (vr, pen)?  [break model; AUC on held-out year]", flush=True)
print("    train->test   AUC(vr+pen)  AUC(vr+pen+er)   delta     er_coef(std)", flush=True)
for tr_y, te_y in ((2025, 2026), (2026, 2025)):
    tr = yr == tr_y; te = yr == te_y
    def z(x, xt): mu, sd = x[tr].mean(), x[tr].std() + EPS; return (x[tr] - mu) / sd, (xt[te] - mu) / sd if False else (x[te] - mu) / sd
    mv, sv = lvr[tr].mean(), lvr[tr].std() + EPS; mp, sp = pen[tr].mean(), pen[tr].std() + EPS; me, se = er[tr].mean(), er[tr].std() + EPS
    vtr, vte = (lvr[tr] - mv) / sv, (lvr[te] - mv) / sv
    ptr, pte = (pen[tr] - mp) / sp, (pen[te] - mp) / sp
    etr, ete = (er[tr] - me) / se, (er[te] - me) / se
    w1 = fit([vtr, ptr], y[tr]); z1 = lin(w1, [vte, pte])
    w2 = fit([vtr, ptr, etr], y[tr]); z2 = lin(w2, [vte, pte, ete])
    a1 = auc_p(z1[y[te] == 1], z1[y[te] == 0])[0]
    a2 = auc_p(z2[y[te] == 1], z2[y[te] == 0])[0]
    print("    %d->%d      %.3f        %.3f         %+.3f    %+.2f" % (tr_y, te_y, a1, a2, a2 - a1, w2[3]), flush=True)


# (3) impulse hold-gap WITHIN vol x pen cells
print("\n(3) impulse hold-gap WITHIN volume x penetration cells (global er terciles):", flush=True)
xs = sorted(er); lo_t = xs[N // 3]; hi_t = xs[2 * N // 3]
vmed = np.median(vr); pmed = np.median(pen)
print("    cell         n(bot/top)   RESIST bot-er   RESIST top-er   gap   (25 / 26 gap)", flush=True)
for vlab, vlo, vhi in (("LOvol", -1e9, vmed), ("HIvol", vmed, 1e18)):
    for plab, plo, phi in (("LOpen", -1e9, pmed), ("HIpen", pmed, 1e18)):
        cell = [v for v in V if vlo <= v["vr"] < vhi and plo <= v["pen"] < phi]
        bot = [v for v in cell if v["er"] <= lo_t]; top = [v for v in cell if v["er"] >= hi_t]
        def rr(g): return 100 * sum(x["resist"] for x in g) / max(1, len(g))
        def rry(g, yy): gg = [x for x in g if x["yr"] == yy]; return 100 * sum(x["resist"] for x in gg) / max(1, len(gg))
        g_all = rr(top) - rr(bot); g25 = rry(top, 2025) - rry(bot, 2025); g26 = rry(top, 2026) - rry(bot, 2026)
        print("    %-5s %-5s  %4d/%-4d    %5.1f%%          %5.1f%%         %+5.1f  (%+.1f / %+.1f)" % (
            vlab, plab, len(bot), len(top), rr(bot), rr(top), g_all, g25, g26), flush=True)
