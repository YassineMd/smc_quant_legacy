"""STEP 1 — causal feature search: can a CAUSAL 1m feature (measured over the FIRST K 1m candles of a visit, strictly
before the outcome) predict whether a 5m wall HOLDS? Benchmark = the wall's existing entry P(resist) (hover 'holds%').

5m walls; per radar visit, take the underlying 1m candles in [start(5m rk0), end(5m rk1)); require the visit to have
>= K+3 1m candles so the K-candle feature window is EARLY (outcome resolves later). Label = resisted(1)/broke(0).
Features over the first K 1m candles, scored by AUC(hold) per year (2025/2026) — a single feature with AUC clearly
off 0.50 BOTH years = a real causal edge worth building live. absorption-R (effort-vs-result) is the top hypothesis.
"""
import os, sys, glob, gzip, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL, absorption as ABS

ROOT = "study/recon_archive"; W = 30
A5 = sorted(load_archive("5m", root=ROOT)[1], key=lambda b: _f(b.get("start_time", 0)))
n5 = len(A5)
st5 = np.array([_f(b.get("start_time")) for b in A5]); et5 = np.array([_f(b.get("end_time")) for b in A5])
print("5m bars=%d detecting walls..." % n5, flush=True)
walls = AL.detect(A5)
print("5m walls=%d streaming 1m..." % len(walls), flush=True)

# ---- 1m numeric arrays ----
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
                                 _f(d.get("high")), _f(d.get("low")), _f(d.get("buy_vol")), _f(d.get("sell_vol")),
                                 _f(d.get("curr_vol")), sum(d.get("sz_cb") or []) / dur, sum(d.get("sz_cs") or []) / dur)
order = sorted(by)
M = np.array([by[b] for b in order])            # cols: t,O,C,H,L,BV,SV,CV,TB,TS
t, O, C, H, L, BV, SV, CV, TB, TS = (M[:, k] for k in range(10))
N1 = len(t); print("1m candles=%d  computing absorption-R..." % N1, flush=True)

# ---- vectorized absorption A (oriented +=absorbed), trailing-W causal, validated vs app.absorption ----
dV = BV - SV
dP = np.where(O > 0, (C - O) / np.where(O > 0, O, 1) * 100.0, 0.0)
cV = np.concatenate([[0], np.cumsum(dV)]); cV2 = np.concatenate([[0], np.cumsum(dV * dV)])
cP = np.concatenate([[0], np.cumsum(dP)]); cP2 = np.concatenate([[0], np.cumsum(dP * dP)]); cVP = np.concatenate([[0], np.cumsum(dV * dP)])
A_arr = np.full(N1, np.nan)
for i in range(W, N1):
    a = i - W
    mV = (cV[i] - cV[a]) / W; mP = (cP[i] - cP[a]) / W
    vV = (cV2[i] - cV2[a]) / W - mV * mV; vP = (cP2[i] - cP2[a]) / W - mP * mP
    if vV <= 1e-12 or vP <= 1e-12:
        continue
    sV = vV ** 0.5; sP = vP ** 0.5
    cov = (cVP[i] - cVP[a]) / W - mV * mP
    rho = max(-1.0, min(1.0, cov / (sV * sP)))
    R = (dP[i] - mP) / sP - rho * (dV[i] - mV) / sV
    A_arr[i] = (-R if dV[i] > 0 else R) if dV[i] != 0 else 0.0
# validate against app.absorption on a sample
A1m = [{"open_price": O[i], "close_price": C[i], "buy_vol": BV[i], "sell_vol": SV[i]} for i in range(N1)]
errs = []
for i in np.random.default_rng(1).integers(W + 1, N1, 25):
    ref = ABS.absorption(A1m, int(i))[0]
    if ref is not None and not np.isnan(A_arr[i]):
        errs.append(abs(ref - A_arr[i]))
print("absR vectorized vs app.absorption max|err| on 25 samples = %.4g" % (max(errs) if errs else -1), flush=True)
del A1m

# ---- build visit rows with CAUSAL first-K features ----
KS = (5, 10)
rows = {k: [] for k in KS}   # per K: (year, hold, entry_pr, absR, tapeSlope, tapeDrop, netd, pen, prog)
for w in walls:
    side = w["side"]; band = _f(w.get("band")); runs = w.get("radar_runs", ())
    if band <= 0 or not runs:
        continue
    broken = bool(w.get("broken")); i1 = int(w.get("i1", n5 - 1)); P = _f(w["price"])
    r_lo = P - 3 * band; r_hi = P + 3 * band
    for (rk0, rk1, pr) in runs:
        rk0 = int(rk0); rk1 = int(rk1)
        if rk1 >= n5 - 1:
            continue
        hold = 0 if (broken and rk0 <= i1 <= rk1 + 2) else 1
        j0 = int(np.searchsorted(t, st5[rk0], "left")); jE = int(np.searchsorted(t, et5[rk1], "left"))
        vlen = jE - j0
        for K in KS:
            if vlen < K + 3 or j0 < W:
                continue
            sl = slice(j0, j0 + K)
            tape = TS[sl] if side == "S" else TB[sl]          # absorbed side
            aR = A_arr[sl]
            if np.isnan(aR).any():
                continue
            slope = np.polyfit(np.arange(K), tape, 1)[0]
            peak = tape.max()
            drop = (peak - tape[-1]) / peak if peak > 0 else 0.0
            vol = CV[sl].sum()
            netd = (SV[sl].sum() - BV[sl].sum()) / vol if vol > 0 else 0.0     # net SELL share (S: absorbed pressure)
            netd = netd if side == "S" else -netd                              # orient: absorbed-side net pressure
            pen = ((r_hi - L[sl].min()) if side == "R" else (H[sl].max() - r_lo)) / (6 * band)  # depth poked, /radar-height
            prog = ((C[j0 + K - 1] - O[j0]) if side == "R" else (O[j0] - C[j0 + K - 1])) / band  # + = toward the BREAK edge
            rows[K].append((datetime.fromtimestamp(st5[rk0], tz=timezone.utc).year, hold, _f(pr),
                            float(np.mean(aR)), float(-slope), float(drop), float(netd), float(pen), float(prog)))


def auc(scores, labels):
    s = np.asarray(scores, float); y = np.asarray(labels)
    ok = ~np.isnan(s); s = s[ok]; y = y[ok]
    npos = int((y == 1).sum()); nneg = int((y == 0).sum())
    if npos == 0 or nneg == 0:
        return float("nan")
    order = s.argsort(); ranks = np.empty(len(s)); ranks[order] = np.arange(1, len(s) + 1)
    return (ranks[y == 1].sum() - npos * (npos + 1) / 2) / (npos * nneg)


FEATS = [("entry P(resist)", 2), ("absorption-R", 3), ("tape -slope(fade)", 4), ("tape drop-from-peak", 5),
         ("absorbed net-delta", 6), ("penetration", 7), ("price->break progress", 8)]
for K in KS:
    R = rows[K]
    lab = np.array([r[1] for r in R])
    y25 = np.array([r[0] == 2025 for r in R]); y26 = ~y25
    base = 100.0 * lab.mean()
    print("\n=== K=%d first 1m candles   n=%d  (2025=%d 2026=%d)  base P(hold)=%.1f%% ===   [AUC>.5 -> higher feature = HOLD]"
          % (K, len(R), y25.sum(), y26.sum(), base), flush=True)
    print("  %-24s  AUC2025  AUC2026" % "feature", flush=True)
    for name, idx in FEATS:
        col = np.array([r[idx] for r in R])
        a25 = auc(col[y25], lab[y25]); a26 = auc(col[y26], lab[y26])
        print("  %-24s  %6.3f   %6.3f" % (name, a25, a26), flush=True)
