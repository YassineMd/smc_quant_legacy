"""REWARD/EFF during a radar/wall VISIT.  What does the reward-per-effort share read while price is testing a wall,
and does it separate HOLD from BREAK?

reward/eff buy-share over a set of candles = 100 * rpe_buy / (rpe_buy + rpe_sell), rpe_side = (sum of that side's
rewarded price moves) / (that side's taker volume).  DEFENDER share = buy-share at a BUY wall (support S, buyers
defend) / sell-share (=100-buy) at a SELL wall (resistance R, sellers defend).  Hypothesis: a HOLD = the DEFENDER is
being rewarded (presser absorbed) -> defender reward/eff high / rising; a BREAK = presser rewarded -> defender low.

5m walls; per radar visit take the underlying 1m candles in [start(rk0), end(rk1)); label resisted(1)/broke(0).
- DESCRIPTIVE (coincident): defender reward/eff over the WHOLE visit + its 1st-half->2nd-half evolution, HOLD vs BREAK.
- CAUSAL: defender reward/eff over the FIRST K 1m candles (before the outcome), AUC(hold) per year vs entry P(resist).
"""
import os, sys, glob, gzip, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

ROOT = "study/recon_archive"
A5 = sorted(load_archive("5m", root=ROOT)[1], key=lambda b: _f(b.get("start_time", 0)))
n5 = len(A5)
st5 = np.array([_f(b.get("start_time")) for b in A5]); et5 = np.array([_f(b.get("end_time")) for b in A5])
print("5m bars=%d detecting walls..." % n5, flush=True)
walls = AL.detect(A5)
print("5m walls=%d streaming 1m..." % len(walls), flush=True)

by = {}
for fn in sorted(glob.glob(os.path.join(ROOT, "1m", "1m_*.jsonl.gz"))):
    with gzip.open(fn, "rt", encoding="utf-8") as gz:
        for line in gz:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line); d = json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]
            by[int(r["bid"])] = (_f(d.get("start_time")), _f(d.get("open", d.get("open_price"))),
                                 _f(d.get("close", d.get("close_price"))), _f(d.get("buy_vol")), _f(d.get("sell_vol")))
order = sorted(by)
M = np.array([by[b] for b in order]); t, O, C, BV, SV = (M[:, k] for k in range(5))
N1 = len(t); print("1m candles=%d" % N1, flush=True)

dP = np.where(O > 0, (C - O) / np.where(O > 0, O, 1.0), 0.0)
UP = np.where(dP > 0, dP, 0.0); DN = np.where(dP < 0, -dP, 0.0)
cUP = np.concatenate([[0], np.cumsum(UP)]); cDN = np.concatenate([[0], np.cumsum(DN)])
cBV = np.concatenate([[0], np.cumsum(BV)]); cSV = np.concatenate([[0], np.cumsum(SV)])


def buyshare(a, b):
    """reward/eff BUY share over 1m candles [a,b). NaN if flat / no volume."""
    if b <= a:
        return np.nan
    eb = cBV[b] - cBV[a]; es = cSV[b] - cSV[a]
    rb = (cUP[b] - cUP[a]) / eb if eb > 0 else 0.0
    rs = (cDN[b] - cDN[a]) / es if es > 0 else 0.0
    tot = rb + rs
    return 100.0 * rb / tot if tot > 0 else np.nan


KS = (5, 10)
rows = []   # (year, hold, side, entry_pr, def_whole, def_h1, def_h2, {K: def_earlyK})
for w in walls:
    side = w["side"]; band = _f(w.get("band")); runs = w.get("radar_runs", ())
    if band <= 0 or not runs:
        continue
    broken = bool(w.get("broken")); i1 = int(w.get("i1", n5 - 1))
    for (rk0, rk1, pr) in runs:
        rk0 = int(rk0); rk1 = int(rk1)
        if rk1 >= n5 - 1:
            continue
        hold = 0 if (broken and rk0 <= i1 <= rk1 + 2) else 1
        j0 = int(np.searchsorted(t, st5[rk0], "left")); jE = int(np.searchsorted(t, et5[rk1], "left"))
        vlen = jE - j0
        if vlen < 6:                                          # need a few 1m candles to read a share
            continue

        def deff(a, b):                                       # defender reward/eff share over 1m [a,b)
            bs = buyshare(a, b)
            if np.isnan(bs):
                return np.nan
            return bs if side == "S" else (100.0 - bs)        # S: buyers defend / R: sellers defend
        mid = j0 + vlen // 2
        rec = [datetime.fromtimestamp(st5[rk0], tz=timezone.utc).year, hold, side, _f(pr),
               deff(j0, jE), deff(j0, mid), deff(mid, jE)]
        ek = {}
        for K in KS:
            ek[K] = deff(j0, j0 + K) if vlen >= K + 2 else np.nan
        rec.append(ek)
        rows.append(tuple(rec))

print("visits=%d" % len(rows), flush=True)


def auc(scores, labels):
    s = np.asarray(scores, float); y = np.asarray(labels)
    ok = ~np.isnan(s); s = s[ok]; y = y[ok]
    npos = int((y == 1).sum()); nneg = int((y == 0).sum())
    if npos == 0 or nneg == 0:
        return float("nan")
    o = s.argsort(); ranks = np.empty(len(s)); ranks[o] = np.arange(1, len(s) + 1)
    return (ranks[y == 1].sum() - npos * (npos + 1) / 2) / (npos * nneg)


def mean_nan(v):
    v = np.asarray(v, float); v = v[~np.isnan(v)]
    return float(v.mean()) if len(v) else float("nan")


yr = np.array([r[0] for r in rows]); hold = np.array([r[1] for r in rows]); sidearr = np.array([r[2] for r in rows])
defw = np.array([r[4] for r in rows]); defh1 = np.array([r[5] for r in rows]); defh2 = np.array([r[6] for r in rows])

print("\n=== DESCRIPTIVE (coincident): DEFENDER reward/eff share during the visit — HOLD vs BREAK ===")
print("(>50 = defender being rewarded/absorbing the presser; higher-on-HOLD = the expected signature)")
for Y in (2025, 2026):
    for sd, lbl in (("S", "BUY wall/support "), ("R", "SELL wall/resist "), (None, "ALL sides       ")):
        m = (yr == Y) & (sidearr == sd if sd else np.ones(len(rows), bool))
        h = m & (hold == 1); b = m & (hold == 0)
        print("  %d %s  n=%-4d hold=%-4d break=%-4d | whole HOLD=%.1f BREAK=%.1f (diff %+.1f) | evo(h1->h2) HOLD %.1f->%.1f  BREAK %.1f->%.1f"
              % (Y, lbl, int(m.sum()), int(h.sum()), int(b.sum()),
                 mean_nan(defw[h]), mean_nan(defw[b]), mean_nan(defw[h]) - mean_nan(defw[b]),
                 mean_nan(defh1[h]), mean_nan(defh2[h]), mean_nan(defh1[b]), mean_nan(defh2[b])), flush=True)

print("\n=== CAUSAL: does EARLY-visit reward/eff predict HOLD?  AUC(hold) per year vs entry P(resist) ===")
print("(AUC>0.5 = higher early defender reward/eff -> HOLD; compare to the wall's entry P(resist) ~0.74 benchmark)")
prc = np.array([r[3] for r in rows])
print("  %-26s AUC2025  AUC2026" % "feature")
print("  %-26s %6.3f   %6.3f" % ("entry P(resist) [benchmark]", auc(prc[yr == 2025], hold[yr == 2025]), auc(prc[yr == 2026], hold[yr == 2026])))
print("  %-26s %6.3f   %6.3f" % ("defender r/eff WHOLE (leak)", auc(defw[yr == 2025], hold[yr == 2025]), auc(defw[yr == 2026], hold[yr == 2026])))
for K in KS:
    ek = np.array([r[7][K] for r in rows])
    print("  %-26s %6.3f   %6.3f" % ("defender r/eff first %d 1m" % K, auc(ek[yr == 2025], hold[yr == 2025]), auc(ek[yr == 2026], hold[yr == 2026])))

def logit_auc(Xtr, ytr, Xte, yte, iters=800, lr=0.4):
    """Tiny standardized logistic (GD) -> AUC on the held-out year. The score is monotone in P, so auc() gives AUC."""
    mu = Xtr.mean(0); sd = Xtr.std(0); sd[sd == 0] = 1.0
    Xtr = (Xtr - mu) / sd; Xte = (Xte - mu) / sd
    Xtr = np.column_stack([np.ones(len(Xtr)), Xtr]); Xte = np.column_stack([np.ones(len(Xte)), Xte])
    w = np.zeros(Xtr.shape[1])
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-np.clip(Xtr @ w, -30, 30)))
        w -= lr * (Xtr.T @ (p - ytr)) / len(ytr)
    return auc(Xte @ w, yte)


print("\n=== INCREMENTAL: does EARLY reward/eff add to entry P(resist)? (logistic, fit one year -> test the other) ===")
for K in KS:
    ek = np.array([r[7][K] for r in rows])
    m = ~np.isnan(prc) & ~np.isnan(ek)
    for tr in (2025, 2026):
        te = 2026 if tr == 2025 else 2025
        mtr = m & (yr == tr); mte = m & (yr == te)
        if mtr.sum() < 50 or mte.sum() < 50:
            continue
        ytr = hold[mtr].astype(float); yte = hold[mte].astype(float)
        ab = logit_auc(prc[mtr].reshape(-1, 1), ytr, prc[mte].reshape(-1, 1), yte)
        ao = logit_auc(np.column_stack([prc[mtr], ek[mtr]]), ytr, np.column_stack([prc[mte], ek[mte]]), yte)
        print("  first %2d 1m: fit%d->test%d  P(resist)=%.3f  +reward/eff=%.3f  (incremental %+.3f)"
              % (K, tr, te, ab, ao, ao - ab), flush=True)
print("\n[done]")
