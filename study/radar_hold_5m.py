"""5m-ONLY re-study: does tape / absorbR / tape x absorbR predict a 5m wall HOLD, measured on the 5m candles of the
visit? CAUSAL = first K=2/3 5m candles (visit must be longer, so the window is before the outcome). COINCIDENT =
whole visit (rk0..rk1, look-ahead reference). Per-feature AUC both years + incremental OOS over entry P(resist)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

W = 30
A = sorted(load_archive("5m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
n = len(A)
O = np.array([_f(b.get("open", b.get("open_price"))) for b in A]); Cc = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
BV = np.array([_f(b.get("buy_vol")) for b in A]); SV = np.array([_f(b.get("sell_vol")) for b in A])


def _tape(b):
    dur = max(1.0, _f(b.get("end_time")) - _f(b.get("start_time")))
    return sum(b.get("sz_cb") or []) / dur, sum(b.get("sz_cs") or []) / dur


TB = np.array([_tape(b)[0] for b in A]); TS = np.array([_tape(b)[1] for b in A])
print("5m bars=%d detecting + absR..." % n, flush=True)
walls = AL.detect(A)
# vectorized 5m absorption A (oriented +=absorbed), trailing-W causal
dV = BV - SV; dP = np.where(O > 0, (Cc - O) / np.where(O > 0, O, 1) * 100.0, 0.0)
cV = np.concatenate([[0], np.cumsum(dV)]); cV2 = np.concatenate([[0], np.cumsum(dV * dV)])
cP = np.concatenate([[0], np.cumsum(dP)]); cP2 = np.concatenate([[0], np.cumsum(dP * dP)]); cVP = np.concatenate([[0], np.cumsum(dV * dP)])
Aarr = np.full(n, np.nan)
for i in range(W, n):
    a = i - W; mV = (cV[i] - cV[a]) / W; mP = (cP[i] - cP[a]) / W
    vV = (cV2[i] - cV2[a]) / W - mV * mV; vP = (cP2[i] - cP2[a]) / W - mP * mP
    if vV <= 1e-12 or vP <= 1e-12:
        continue
    rho = max(-1.0, min(1.0, ((cVP[i] - cVP[a]) / W - mV * mP) / ((vV ** 0.5) * (vP ** 0.5))))
    Aarr[i] = ((dP[i] - mP) / (vP ** 0.5) - rho * (dV[i] - mV) / (vV ** 0.5))
    Aarr[i] = (-Aarr[i] if dV[i] > 0 else Aarr[i]) if dV[i] != 0 else 0.0

# windows: causal K=2,3 (need visit length >= K+2) + full visit (coincident)
recs = {"K2": [], "K3": [], "full": []}


def feats(side, s, e):                                # over 5m candle indices [s,e)
    tape = (TS if side == "S" else TB)[s:e]; aR = Aarr[s:e]
    if len(tape) < 2 or np.isnan(aR).any():
        return None
    return (float(np.mean(tape * aR)), float(np.mean(aR)), float(-np.polyfit(np.arange(len(tape)), tape, 1)[0]))


for w in walls:
    side = w["side"]; runs = w.get("radar_runs", ())
    broken = bool(w.get("broken")); i1 = int(w.get("i1", n - 1))
    for (rk0, rk1, pr) in runs:
        rk0 = int(rk0); rk1 = int(rk1)
        if rk1 >= n - 1 or rk0 < W:
            continue
        hold = 0 if (broken and rk0 <= i1 <= rk1 + 2) else 1
        yr = datetime.fromtimestamp(_f(A[rk0].get("start_time")), tz=timezone.utc).year
        L = rk1 - rk0 + 1
        for tagK, K in (("K2", 2), ("K3", 3)):
            if L >= K + 2:
                f = feats(side, rk0, rk0 + K)
                if f:
                    recs[tagK].append((yr, hold, _f(pr)) + f)
        if L >= 2:
            f = feats(side, rk0, rk1 + 1)
            if f:
                recs["full"].append((yr, hold, _f(pr)) + f)


def auc(s, y):
    o = s.argsort(); rk = np.empty(len(s)); rk[o] = np.arange(1, len(s) + 1)
    p = int((y == 1).sum()); q = int((y == 0).sum())
    return (rk[y == 1].sum() - p * (p + 1) / 2) / (p * q) if p and q else float("nan")


def logit_auc(R, cols, tr, te):
    Y = np.array([r[1] for r in R]); X = np.column_stack([[r[c] for r in R] for c in cols])
    Xtr = X[tr]; mu = Xtr.mean(0); sd = Xtr.std(0) + 1e-9
    Xtr = np.column_stack([np.ones(tr.sum()), (Xtr - mu) / sd]); Xte = np.column_stack([np.ones(te.sum()), (X[te] - mu) / sd])
    w = np.zeros(Xtr.shape[1]); ytr = Y[tr]
    for _ in range(800):
        p = 1 / (1 + np.exp(-Xtr @ w)); w -= 0.3 * (Xtr.T @ (p - ytr) / len(ytr))
    return auc(Xte @ w, Y[te])


NAMES = [("entry P(resist)", 2), ("tape x absorbR", 3), ("absorbR", 4), ("tape -slope(fade)", 5)]
for tag in ("K2", "K3", "full"):
    R = recs[tag]
    if not R:
        continue
    Y = np.array([r[1] for r in R]); yr = np.array([r[0] for r in R]); tr25 = yr == 2025; tr26 = yr == 2026
    lbl = {"K2": "CAUSAL first 2 5m candles", "K3": "CAUSAL first 3 5m candles", "full": "COINCIDENT whole visit (look-ahead)"}[tag]
    print("\n=== %s   n=%d  base P(hold)=%.1f%% ===" % (lbl, len(R), 100 * Y.mean()), flush=True)
    print("  %-20s 2025    2026" % "per-feature AUC", flush=True)
    for nm, c in NAMES:
        col = np.array([r[c] for r in R])
        print("   %-19s %.3f   %.3f" % (nm, auc(col[tr25], Y[tr25]), auc(col[tr26], Y[tr26])), flush=True)
    if tag != "full":
        print("  incremental OOS (add to entry P(resist)):  fit25->26  fit26->25", flush=True)
        for nm, cc in (("entry_pr alone", [2]), ("+ tapeXabsorbR", [2, 3]), ("+ tapeXabsorbR+absR+slope", [2, 3, 4, 5])):
            print("   %-28s   %.3f      %.3f" % (nm, logit_auc(R, cc, tr25, tr26), logit_auc(R, cc, tr26, tr25)), flush=True)
