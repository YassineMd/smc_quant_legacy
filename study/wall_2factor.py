# -*- coding: utf-8 -*-
"""Validate a 2-factor P(resist): does PENETRATION add to VOLUME out-of-sample? Fit logistic on one year, test AUC
on the OTHER (both directions). Only keep pen if it beats volume-alone OOS in both. Then emit a calibration."""
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

LF, DECAY, STR = 24, 0.6, 0.12
_, r15, _ = load_archive("15m", root="study/recon_archive")
A = sorted(r15, key=lambda b: _f(b.get("start_time", 0)))
for b in A:
    b["open"] = _f(b.get("open_price")); b["close"] = _f(b.get("close_price"))
n = len(A)
C = [b["close"] for b in A]; H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
CVv = np.array([_f(b.get("curr_vol")) for b in A])
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
walls = AL.detect(A)

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
        if oc is None or ko <= k0:
            continue
        bx = fsum(_f(vv.get("b")) + _f(vv.get("s")) for k in range(k0, ko)
                  for _p, vv in (A[k].get("levels") or {}).items() if r_lo <= _f(_p) <= r_hi)
        rmv = float(np.median(CVv[max(0, k0 - 200):k0])) if k0 > 5 else CVv[k0]
        if rmv <= 0:
            continue
        vr = (bx / (ko - k0)) / rmv
        pen = ((H[k0] - r_lo) if side == "R" else (r_hi - L[k0])) / (r_hi - r_lo)
        V.append({"brk": 1 - oc, "vr": vr, "pen": pen, "yr": YR[k0]})

vr = np.array([v["vr"] for v in V]); pen = np.array([v["pen"] for v in V])
y = np.array([v["brk"] for v in V], float); yr = np.array([v["yr"] for v in V])
lvr = np.log1p(vr)                                            # tame the long tail; monotone -> AUC unchanged
print("=== 2-factor validation: %d visits | base BREAK %.1f%% ===" % (len(V), 100 * y.mean()))


def fit(cols, yy):
    X = np.column_stack([np.ones(len(yy))] + cols)
    def nll(wv):
        z = X @ wv; p = np.clip(1 / (1 + np.exp(-z)), 1e-9, 1 - 1e-9)
        return -np.mean(yy * np.log(p) + (1 - yy) * np.log(1 - p))
    return minimize(nll, np.zeros(X.shape[1]), method="BFGS").x


def lin(w, cols):
    return np.column_stack([np.ones(len(cols[0]))] + cols) @ w


print("\n   train -> test    AUC(vol)   AUC(vol+pen)   delta   (pen coef)")
for tr_y, te_y in ((2025, 2026), (2026, 2025)):
    tr = yr == tr_y; te = yr == te_y
    mv, sv = lvr[tr].mean(), lvr[tr].std(); mp, sp = pen[tr].mean(), pen[tr].std()
    vtr = (lvr[tr] - mv) / sv; ptr = (pen[tr] - mp) / sp; vte = (lvr[te] - mv) / sv; pte = (pen[te] - mp) / sp
    w1 = fit([vtr], y[tr]); z1 = lin(w1, [vte])
    w2 = fit([vtr, ptr], y[tr]); z2 = lin(w2, [vte, pte])
    a1 = auc_p(z1[y[te] == 1], z1[y[te] == 0])[0]
    a2 = auc_p(z2[y[te] == 1], z2[y[te] == 0])[0]
    print("   %d -> %d       %.3f       %.3f        %+.3f    (%.2f)" % (tr_y, te_y, a1, a2, a2 - a1, w2[2]))

# full-sample 2-factor calibration table for the display: P(resist) by (vr band, pen band)
print("\n   -- P(RESIST) by volume x penetration (full sample, for the display) --")
vb = [0, 0.4, 0.7, 1.1, 1e9]; pb = [0, 0.45, 0.7, 1.01]
print("      pen\\vr   " + "  ".join("vr<%.1f" % (x if x < 1e8 else 9.9) for x in vb[1:]))
for pi in range(len(pb) - 1):
    row = "   pen<%.2f " % pb[pi + 1]
    for vi in range(len(vb) - 1):
        sel = [v for v in V if vb[vi] <= v["vr"] < vb[vi + 1] and pb[pi] <= v["pen"] < pb[pi + 1]]
        row += "  %5.0f%%(%d)" % (100 * (1 - np.mean([v["brk"] for v in sel])) if sel else 0, len(sel))
    print(row)
