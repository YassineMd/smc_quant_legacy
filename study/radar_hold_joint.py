"""JOINT development of Tape-B & Tape-S through a 5m wall visit: does how the two sides move RELATIVE to each other
predict a HOLD? Oriented so DEFENDER = the wall's own side (buy wall -> buyers Tape-B; sell wall -> sellers Tape-S)
and PRESSER = the absorbed side. Features (higher = hold-favorable) over CAUSAL first K=2/3 5m candles + COINCIDENT
whole visit: def_share level & slope (imbalance toward the defender), defender rise, presser fade. Per-feature AUC
both years + incremental OOS over entry P(resist)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

A = sorted(load_archive("5m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
n = len(A); W = 30


def _tape(b):
    dur = max(1.0, _f(b.get("end_time")) - _f(b.get("start_time")))
    return sum(b.get("sz_cb") or []) / dur, sum(b.get("sz_cs") or []) / dur


TB = np.array([_tape(b)[0] for b in A]); TS = np.array([_tape(b)[1] for b in A])
print("5m bars=%d detecting..." % n, flush=True)
walls = AL.detect(A)


def slope(y):
    return float(np.polyfit(np.arange(len(y)), y, 1)[0]) if len(y) >= 2 else 0.0


def feats(side, s, e):
    presser = (TS if side == "S" else TB)[s:e]          # absorbed side
    defender = (TB if side == "S" else TS)[s:e]         # wall's own side
    if len(presser) < 2:
        return None
    share = defender / (defender + presser + 1e-9)      # defender share of tape
    return (float(share.mean()),          # def_share level  (higher = defender-dominant)
            slope(share),                 # def_share slope  (imbalance shifting to defender)
            slope(defender),              # defender rise
            -slope(presser))              # presser fade


recs = {"K2": [], "K3": [], "full": []}
for w in walls:
    side = w["side"]; broken = bool(w.get("broken")); i1 = int(w.get("i1", n - 1))
    for (rk0, rk1, pr) in w.get("radar_runs", ()):
        rk0 = int(rk0); rk1 = int(rk1)
        if rk1 >= n - 1 or rk0 < W:
            continue
        hold = 0 if (broken and rk0 <= i1 <= rk1 + 2) else 1
        yr = datetime.fromtimestamp(_f(A[rk0].get("start_time")), tz=timezone.utc).year
        L = rk1 - rk0 + 1
        for tag, K in (("K2", 2), ("K3", 3)):
            if L >= K + 2:
                f = feats(side, rk0, rk0 + K)
                if f:
                    recs[tag].append((yr, hold, _f(pr)) + f)
        f = feats(side, rk0, rk1 + 1)
        if f:
            recs["full"].append((yr, hold, _f(pr)) + f)


def auc(s, y):
    o = s.argsort(); rk = np.empty(len(s)); rk[o] = np.arange(1, len(s) + 1)
    p = int((y == 1).sum()); q = int((y == 0).sum())
    return (rk[y == 1].sum() - p * (p + 1) / 2) / (p * q) if p and q else float("nan")


def logit_auc(R, cols, tr, te):
    Y = np.array([r[1] for r in R]); X = np.column_stack([[r[c] for r in R] for c in cols])
    mu = X[tr].mean(0); sd = X[tr].std(0) + 1e-9
    Xtr = np.column_stack([np.ones(tr.sum()), (X[tr] - mu) / sd]); Xte = np.column_stack([np.ones(te.sum()), (X[te] - mu) / sd])
    w = np.zeros(Xtr.shape[1]); ytr = Y[tr]
    for _ in range(800):
        p = 1 / (1 + np.exp(-Xtr @ w)); w -= 0.3 * (Xtr.T @ (p - ytr) / len(ytr))
    return auc(Xte @ w, Y[te])


NAMES = [("entry P(resist)", 2), ("def_share level", 3), ("def_share slope", 4), ("defender rise", 5), ("presser fade", 6)]
for tag in ("K2", "K3", "full"):
    R = recs[tag]
    Y = np.array([r[1] for r in R]); yr = np.array([r[0] for r in R]); a = yr == 2025; b = yr == 2026
    lbl = {"K2": "CAUSAL first 2 5m", "K3": "CAUSAL first 3 5m", "full": "COINCIDENT whole visit (look-ahead)"}[tag]
    print("\n=== %s  n=%d base P(hold)=%.1f%% ===" % (lbl, len(R), 100 * Y.mean()), flush=True)
    print("  %-18s 2025    2026" % "per-feature AUC", flush=True)
    for nm, c in NAMES:
        col = np.array([r[c] for r in R])
        print("   %-17s %.3f   %.3f" % (nm, auc(col[a], Y[a]), auc(col[b], Y[b])), flush=True)
    if tag != "full":
        print("  incremental OOS over entry P(resist):  fit25->26  fit26->25", flush=True)
        for nm, cc in (("entry_pr alone", [2]), ("+ def_share level+slope", [2, 3, 4]), ("+ ALL joint", [2, 3, 4, 5, 6])):
            print("   %-26s   %.3f      %.3f" % (nm, logit_auc(R, cc, a, b), logit_auc(R, cc, b, a)), flush=True)
