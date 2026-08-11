"""User hypothesis: a wall BREAKS when the DEFENDER is tape-dominant but the candle moves AGAINST them (toward the
break) -- their aggression is being absorbed. Per candle: bad = (defender tape > presser tape) AND (candle toward the
break: bearish at support / bullish at resistance). Feature = bad_frac over the visit; net = good_frac - bad_frac
(good = defender-dominant AND candle WITH the defender). Test AUC(HOLD) causal (first 2/3 5m) + coincident (whole
visit) + incremental over entry P(resist). AUC<0.5 for bad_frac => it predicts a BREAK (the hypothesis)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

A = sorted(load_archive("5m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
n = len(A); W = 30
O = np.array([_f(b.get("open", b.get("open_price"))) for b in A]); Cc = np.array([_f(b.get("close", b.get("close_price"))) for b in A])


def _tape(b):
    dur = max(1.0, _f(b.get("end_time")) - _f(b.get("start_time")))
    return sum(b.get("sz_cb") or []) / dur, sum(b.get("sz_cs") or []) / dur


TB = np.array([_tape(b)[0] for b in A]); TS = np.array([_tape(b)[1] for b in A])
print("5m bars=%d detecting..." % n, flush=True)
walls = AL.detect(A)


def feats(side, s, e):
    o = O[s:e]; c = Cc[s:e]; tb = TB[s:e]; ts = TS[s:e]
    if len(o) < 1:
        return None
    if side == "S":                                    # defender = buyers; toward-break = bearish (c<o)
        defdom = tb > ts; toward = c < o; withdef = c > o
    else:                                              # defender = sellers; toward-break = bullish (c>o)
        defdom = ts > tb; toward = c > o; withdef = c < o
    bad = np.mean(defdom & toward); good = np.mean(defdom & withdef)
    return (float(bad), float(good - bad))             # bad_frac, net(good-bad)


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


NAMES = [("entry P(resist)", 2), ("bad_frac [<.5=break]", 3), ("net good-bad", 4)]
for tag in ("K2", "K3", "full"):
    R = recs[tag]
    Y = np.array([r[1] for r in R]); yr = np.array([r[0] for r in R]); a = yr == 2025; b = yr == 2026
    lbl = {"K2": "CAUSAL first 2 5m", "K3": "CAUSAL first 3 5m", "full": "COINCIDENT whole visit"}[tag]
    print("\n=== %s  n=%d base P(hold)=%.1f%% ===" % (lbl, len(R), 100 * Y.mean()), flush=True)
    print("  %-22s 2025    2026" % "per-feature AUC(hold)", flush=True)
    for nm, c in NAMES:
        col = np.array([r[c] for r in R])
        print("   %-21s %.3f   %.3f" % (nm, auc(col[a], Y[a]), auc(col[b], Y[b])), flush=True)
    if tag != "full":
        print("  incremental OOS over entry P(resist):  fit25->26  fit26->25", flush=True)
        for nm, cc in (("entry_pr alone", [2]), ("+ bad_frac", [2, 3]), ("+ bad_frac + net", [2, 3, 4])):
            print("   %-22s   %.3f      %.3f" % (nm, logit_auc(R, cc, a, b), logit_auc(R, cc, b, a)), flush=True)
