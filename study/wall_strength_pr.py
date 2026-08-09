# -*- coding: utf-8 -*-
"""Should STRENGTH factor into P(resist)? Strength = ejection base * DECAY^j (j = prior radar visits). Both pieces are
CAUSAL at the visit. Test on the P(resist) population (multi-bar visits, ko>k0): univariate AUC of ejection / prior-
visits / causal-strength for resist, their correlation with the 4 model factors, and OOS incremental AUC on top of
(log1p vr, pen, clpos, body). Both years."""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from math import fsum
from datetime import datetime, timezone
from scipy.optimize import minimize
from study.archive_loader import load_archive
from study.candle_bias_1h import _f, auc_p
from app import absorption_level_detect as AL

LF, DECAY, STR, EPS = 24, 0.6, 0.12, 1e-9
print("loading 15m + detect ...", flush=True)
_, r15, _ = load_archive("15m", root="study/recon_archive")
A = sorted(r15, key=lambda b: _f(b.get("start_time", 0)))
for b in A:
    b["open"] = _f(b.get("open_price")); b["close"] = _f(b.get("close_price"))
n = len(A)
O15 = np.array([b["open"] for b in A]); C = np.array([b["close"] for b in A])
H = np.array([_f(b.get("high")) for b in A]); L = np.array([_f(b.get("low")) for b in A])
CVv = np.array([_f(b.get("curr_vol")) for b in A])
YR = np.array([datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A])
walls = AL.detect(A)

V = []
for w in walls:
    hits = w["hits"]; base = w["strength"] / (DECAY ** hits) if hits else w["strength"]   # recover formation ejection
    P = w["price"]; band = w["band"]; side = w["side"]; r_lo = P - 3.0 * band; r_hi = P + 3.0 * band
    span = r_hi - r_lo; dd = 1.0 if side == "R" else -1.0
    for j, (k0, k1, pr) in enumerate(w["radar_runs"]):
        cstr = base * (DECAY ** j)
        if cstr < STR:
            continue
        ko = oc = None
        for k in range(k0, min(n, k0 + LF)):
            r = (0 if C[k] > r_hi else (1 if C[k] < r_lo else None)) if side == "R" else \
                (0 if C[k] < r_lo else (1 if C[k] > r_hi else None))
            if r is not None:
                ko, oc = k, r; break
        if oc is None or ko <= k0:
            continue
        bx = fsum(_f(vv.get("b")) + _f(vv.get("s")) for kk in range(k0, ko)
                  for _p, vv in (A[kk].get("levels") or {}).items() if r_lo <= _f(_p) <= r_hi)
        rmv = float(np.median(CVv[max(0, k0 - 200):k0])) if k0 > 5 else CVv[k0]
        if rmv <= 0:
            continue
        vr = (bx / (ko - k0)) / rmv
        pen = min(1.0, max(0.0, ((H[k0] - r_lo) if side == "R" else (r_hi - L[k0])) / span))
        clpos = min(1.0, max(0.0, ((C[k0] - r_lo) if side == "R" else (r_hi - C[k0])) / span))
        body = float((C[k0] - O15[k0]) * dd) / span
        V.append({"resist": oc, "yr": int(YR[k0]), "lnvr": np.log1p(vr), "pen": pen, "clpos": clpos, "body": body,
                  "ej": base, "nvis": float(j), "decay": DECAY ** j, "cstr": cstr})

N = len(V)
y = np.array([1 - v["resist"] for v in V], float)     # break model
yr = np.array([v["yr"] for v in V])
print("\n=== strength -> P(resist): %d visits | base RESIST %.1f%% ===" % (N, 100 * (1 - y.mean())), flush=True)


def rauc(feat, pop=None):
    pop = pop or V
    a = [v[feat] for v in pop if v["resist"]]; b = [v[feat] for v in pop if not v["resist"]]
    return auc_p(a, b)[0] if len(a) > 10 and len(b) > 10 else float("nan")


print("\n(1) univariate AUC(resist)  [>0.5 -> higher -> RESIST]:", flush=True)
for f in ("ej", "nvis", "decay", "cstr"):
    print("    %-6s %.3f (25:%.2f 26:%.2f)" % (f, rauc(f), rauc(f, [v for v in V if v["yr"] == 2025]), rauc(f, [v for v in V if v["yr"] == 2026])), flush=True)

print("\n(2) correlation of strength pieces with the 4 model factors:", flush=True)
arr = {k: np.array([v[k] for v in V]) for k in ("lnvr", "pen", "clpos", "body", "ej", "nvis", "decay", "cstr")}
for s in ("ej", "nvis", "cstr"):
    print("    %-5s : lnvr %+.2f  pen %+.2f  clpos %+.2f  body %+.2f" % (
        s, np.corrcoef(arr[s], arr["lnvr"])[0, 1], np.corrcoef(arr[s], arr["pen"])[0, 1],
        np.corrcoef(arr[s], arr["clpos"])[0, 1], np.corrcoef(arr[s], arr["body"])[0, 1]), flush=True)


def fit(cols, yy):
    X = np.column_stack([np.ones(len(yy))] + cols)
    def nll(wv):
        z = X @ wv; p = np.clip(1 / (1 + np.exp(-z)), 1e-9, 1 - 1e-9)
        return -np.mean(yy * np.log(p) + (1 - yy) * np.log(1 - p))
    return minimize(nll, np.zeros(X.shape[1]), method="BFGS").x


def lin(w, cols):
    return np.column_stack([np.ones(len(cols[0]))] + cols) @ w


base4 = ("lnvr", "pen", "clpos", "body")
print("\n(3) OOS incremental AUC on top of the 4-factor model  [break model, held-out year]:", flush=True)
print("    add        2025->2026 (base->+)      2026->2025 (base->+)", flush=True)
for add in ("ej", "nvis", "cstr", "decay"):
    out = []
    for tr_y, te_y in ((2025, 2026), (2026, 2025)):
        tr = yr == tr_y; te = yr == te_y
        def zc(x): mu, sd = x[tr].mean(), x[tr].std() + EPS; return (x[tr] - mu) / sd, (x[te] - mu) / sd
        bt = [zc(arr[c])[0] for c in base4]; be = [zc(arr[c])[1] for c in base4]
        at, ae = zc(arr[add])
        w1 = fit(bt, y[tr]); z1 = lin(w1, be); a1 = auc_p(z1[y[te] == 1], z1[y[te] == 0])[0]
        w2 = fit(bt + [at], y[tr]); z2 = lin(w2, be + [ae]); a2 = auc_p(z2[y[te] == 1], z2[y[te] == 0])[0]
        out.append("%.3f->%.3f(%+.3f)" % (a1, a2, a2 - a1))
    print("    %-6s     %s      %s" % (add, out[0], out[1]), flush=True)

print("\n(4) resist rate by ejection (ej) tercile & prior-visits (both yr):", flush=True)
xs = sorted(v["ej"] for v in V); t1, t2 = xs[N // 3], xs[2 * N // 3]
for lab, lo, hi in (("lo-ej", -1, t1), ("mid-ej", t1, t2), ("hi-ej", t2, 1e9)):
    g = [v for v in V if lo <= v["ej"] < hi]
    print("    %-6s n=%4d RESIST %.1f%% (25:%.1f 26:%.1f)" % (
        lab, len(g), 100 * sum(v["resist"] for v in g) / max(1, len(g)),
        100 * sum(v["resist"] for v in g if v["yr"] == 2025) / max(1, sum(1 for v in g if v["yr"] == 2025)),
        100 * sum(v["resist"] for v in g if v["yr"] == 2026) / max(1, sum(1 for v in g if v["yr"] == 2026))), flush=True)
for j in (0, 1, 2, 3):
    g = [v for v in V if int(v["nvis"]) == j]
    if len(g) > 20:
        print("    visit#%d n=%4d RESIST %.1f%% (25:%.1f 26:%.1f)" % (
            j, len(g), 100 * sum(v["resist"] for v in g) / len(g),
            100 * sum(v["resist"] for v in g if v["yr"] == 2025) / max(1, sum(1 for v in g if v["yr"] == 2025)),
            100 * sum(v["resist"] for v in g if v["yr"] == 2026) / max(1, sum(1 for v in g if v["yr"] == 2026))), flush=True)
