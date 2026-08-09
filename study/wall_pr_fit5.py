# -*- coding: utf-8 -*-
"""Fit the 5-factor P(RESIST) logistic (adds EJECTION to the shipped 4-factor). Features raw so coeffs hardcode:
log1p(vr), pen, clpos, body, ej (formation ejection base, 0..1). Full recon, both yr, multi-bar causal visits.
OOS both directions, full-sample AUC, calibration, paste-ready coefficients."""
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

rows = []
for w in walls:
    hits = w["hits"]; base = w["strength"] / (DECAY ** hits) if hits else w["strength"]
    P = w["price"]; band = w["band"]; side = w["side"]; r_lo = P - 3.0 * band; r_hi = P + 3.0 * band
    span = r_hi - r_lo; dd = 1.0 if side == "R" else -1.0
    for j, (k0, k1, pr) in enumerate(w["radar_runs"]):
        if base * (DECAY ** j) < STR:
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
        rows.append((oc, np.log1p(vr), pen, clpos, body, min(1.0, max(0.0, base)), YR[k0]))

R = np.array(rows, float)
y = R[:, 0]; X = R[:, 1:6]; yr = R[:, 6]
print("=== fit 5-factor P(resist): %d visits | base RESIST %.1f%% ===" % (len(R), 100 * y.mean()), flush=True)


def fit(Xt, yt):
    Z = np.column_stack([np.ones(len(yt)), Xt])
    def nll(wv):
        z = Z @ wv; p = np.clip(1 / (1 + np.exp(-z)), 1e-9, 1 - 1e-9)
        return -np.mean(yt * np.log(p) + (1 - yt) * np.log(1 - p))
    return minimize(nll, np.zeros(Z.shape[1]), method="BFGS").x


def prc(w, Xt):
    return 1 / (1 + np.exp(-(np.column_stack([np.ones(len(Xt)), Xt]) @ w)))


print("\nOOS AUC(resist): 5-factor vs 4-factor:", flush=True)
for tr_y, te_y in ((2025, 2026), (2026, 2025)):
    tr = yr == tr_y; te = yr == te_y
    w5 = fit(X[tr], y[tr]); a5 = auc_p(prc(w5, X[te])[y[te] == 1], prc(w5, X[te])[y[te] == 0])[0]
    w4 = fit(X[tr][:, :4], y[tr]); a4 = auc_p(prc(w4, X[te][:, :4])[y[te] == 1], prc(w4, X[te][:, :4])[y[te] == 0])[0]
    print("   %d->%d   4-factor %.3f   5-factor %.3f   (+%.3f)" % (tr_y, te_y, a4, a5, a5 - a4), flush=True)

W = fit(X, y); pf = prc(W, X)
print("\nfull-sample AUC 5-factor: %.3f" % auc_p(pf[y == 1], pf[y == 0])[0], flush=True)
print("\n=== PASTE -> _PR_COEF = (b0, b_lnvr, b_pen, b_clpos, b_body, b_ej) ===", flush=True)
print("   _PR_COEF = (%.5f, %.5f, %.5f, %.5f, %.5f, %.5f)" % tuple(W), flush=True)

print("\ncalibration (predicted decile -> actual):", flush=True)
order = np.argsort(pf)
for d in range(10):
    idx = order[d * len(order) // 10:(d + 1) * len(order) // 10]
    print("   dec%2d  pred %5.1f%%   actual %5.1f%%   n=%d" % (d + 1, 100 * pf[idx].mean(), 100 * y[idx].mean(), len(idx)), flush=True)

print("\nsample P(resist)%% [body=0.45; ej weak=0.15 vs strong=0.75]:", flush=True)
for ejv in (0.15, 0.75):
    cells = []
    for vrv, pv, cv in ((0.2, 0.15, 0.05), (0.6, 0.55, 0.20), (1.2, 0.90, 0.60)):
        z = W @ np.array([1.0, np.log1p(vrv), pv, cv, 0.45, ejv])
        cells.append("%5.1f%%" % (100 / (1 + np.exp(-z))))
    print("   ej=%.2f   shallow %s   mid %s   deep %s" % (ejv, cells[0], cells[1], cells[2]), flush=True)
