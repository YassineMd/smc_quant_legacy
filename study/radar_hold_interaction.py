"""Does tape x absorb-R (the interaction) predict a 5m wall HOLD causally, and does it add ANYTHING on top of the
existing entry P(resist)? Features over the FIRST K 1m candles of the visit (causal). Per-feature AUC both years +
incremental logistic AUC (entry_pr vs entry_pr + feature), OOS both directions."""
import os, sys, glob, gzip, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

ROOT = "study/recon_archive"; W = 30
A5 = sorted(load_archive("5m", root=ROOT)[1], key=lambda b: _f(b.get("start_time", 0)))
n5 = len(A5)
st5 = np.array([_f(b.get("start_time")) for b in A5]); et5 = np.array([_f(b.get("end_time")) for b in A5])
print("5m bars=%d detecting..." % n5, flush=True)
walls = AL.detect(A5)
print("walls=%d streaming 1m..." % len(walls), flush=True)
by = {}
for fn in sorted(glob.glob(os.path.join(ROOT, "1m", "1m_*.jsonl.gz"))):
    with gzip.open(fn, "rt", encoding="utf-8") as gz:
        for line in gz:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line); d = json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]
            s = _f(d.get("start_time")); dur = max(1.0, _f(d.get("end_time")) - s)
            by[int(r["bid"])] = (s, _f(d.get("open", d.get("open_price"))), _f(d.get("close", d.get("close_price"))),
                                 _f(d.get("buy_vol")), _f(d.get("sell_vol")), sum(d.get("sz_cb") or []) / dur, sum(d.get("sz_cs") or []) / dur)
order = sorted(by); M = np.array([by[b] for b in order])
t, O, C, BV, SV, TB, TS = (M[:, k] for k in range(7))
N1 = len(t); print("1m=%d  absR..." % N1, flush=True)
dV = BV - SV; dP = np.where(O > 0, (C - O) / np.where(O > 0, O, 1) * 100.0, 0.0)
cV = np.concatenate([[0], np.cumsum(dV)]); cV2 = np.concatenate([[0], np.cumsum(dV * dV)])
cP = np.concatenate([[0], np.cumsum(dP)]); cP2 = np.concatenate([[0], np.cumsum(dP * dP)]); cVP = np.concatenate([[0], np.cumsum(dV * dP)])
Aarr = np.full(N1, np.nan)
for i in range(W, N1):
    a = i - W
    mV = (cV[i] - cV[a]) / W; mP = (cP[i] - cP[a]) / W
    vV = (cV2[i] - cV2[a]) / W - mV * mV; vP = (cP2[i] - cP2[a]) / W - mP * mP
    if vV <= 1e-12 or vP <= 1e-12:
        continue
    rho = max(-1.0, min(1.0, ((cVP[i] - cVP[a]) / W - mV * mP) / ((vV ** 0.5) * (vP ** 0.5))))
    R = (dP[i] - mP) / (vP ** 0.5) - rho * (dV[i] - mV) / (vV ** 0.5)
    Aarr[i] = (-R if dV[i] > 0 else R) if dV[i] != 0 else 0.0

K = 5
Y = []; PR = []; TX = []; ABSR = []; SLP = []; YR = []
for w in walls:
    side = w["side"]; runs = w.get("radar_runs", ())
    if not runs:
        continue
    broken = bool(w.get("broken")); i1 = int(w.get("i1", n5 - 1))
    for (rk0, rk1, pr) in runs:
        rk0 = int(rk0); rk1 = int(rk1)
        if rk1 >= n5 - 1:
            continue
        j0 = int(np.searchsorted(t, st5[rk0], "left")); jE = int(np.searchsorted(t, et5[rk1], "left"))
        if jE - j0 < K + 3 or j0 < W:
            continue
        sl = slice(j0, j0 + K); aR = Aarr[sl]
        if np.isnan(aR).any():
            continue
        tape = TS[sl] if side == "S" else TB[sl]
        Y.append(0 if (broken and rk0 <= i1 <= rk1 + 2) else 1)
        PR.append(_f(pr)); TX.append(float(np.mean(tape * aR))); ABSR.append(float(np.mean(aR)))
        SLP.append(float(-np.polyfit(np.arange(K), tape, 1)[0]))
        YR.append(datetime.fromtimestamp(st5[rk0], tz=timezone.utc).year)
Y = np.array(Y); PR = np.array(PR); TX = np.array(TX); ABSR = np.array(ABSR); SLP = np.array(SLP); YR = np.array(YR)
print("visits=%d base P(hold)=%.1f%%" % (len(Y), 100 * Y.mean()), flush=True)


def auc(s, y):
    o = s.argsort(); rk = np.empty(len(s)); rk[o] = np.arange(1, len(s) + 1)
    npos = int((y == 1).sum()); nneg = int((y == 0).sum())
    return (rk[y == 1].sum() - npos * (npos + 1) / 2) / (npos * nneg) if npos and nneg else float("nan")


def logit_auc(cols, tr, te):
    Xtr = np.column_stack(cols)[tr]; Xte = np.column_stack(cols)[te]
    mu = Xtr.mean(0); sd = Xtr.std(0) + 1e-9
    Xtr = (Xtr - mu) / sd; Xte = (Xte - mu) / sd
    Xtr = np.column_stack([np.ones(len(Xtr)), Xtr]); Xte = np.column_stack([np.ones(len(Xte)), Xte])
    w = np.zeros(Xtr.shape[1]); ytr = Y[tr]
    for _ in range(800):
        p = 1 / (1 + np.exp(-Xtr @ w)); w -= 0.3 * (Xtr.T @ (p - ytr) / len(ytr))
    return auc(Xte @ w, Y[te])


print("\nper-feature AUC (K=%d):   feature            2025    2026" % K, flush=True)
for nm, col in (("entry P(resist)", PR), ("tape x absorbR", TX), ("absorbR", ABSR), ("tape -slope(fade)", SLP)):
    print("   %-20s  %.3f   %.3f" % (nm, auc(col[YR == 2025], Y[YR == 2025]), auc(col[YR == 2026], Y[YR == 2026])), flush=True)

tr25 = YR == 2025; tr26 = YR == 2026
print("\nincremental OOS AUC (does the feature add to entry P(resist)?):", flush=True)
print("   %-28s  fit25->test26   fit26->test25" % "model", flush=True)
for nm, cols in (("entry_pr alone", [PR]), ("entry_pr + tapeXabsorbR", [PR, TX]),
                 ("entry_pr + tape-slope", [PR, SLP]), ("entry_pr + tapeXabsorbR + slope + absR", [PR, TX, SLP, ABSR])):
    a1 = logit_auc(cols, tr25, tr26); a2 = logit_auc(cols, tr26, tr25)
    print("   %-28s     %.3f          %.3f" % (nm, a1, a2), flush=True)
