# -*- coding: utf-8 -*-
"""Does 5m granularity add PRECISION about whether a 15m wall survives? (1m was checked -> null; 15m own flow null.)
For each 15m wall radar visit that resolves on a LATER 15m bar (ko>k0 -> fully causal), take the 5m candles in the
CAUSAL in-radar window [t(k0), t(ko)) -- entry through the bar BEFORE resolution, resolving 15m bar EXCLUDED -- and
measure 5m order flow / penetration. Test vs the 15m resist/break outcome, both years, and whether any 5m feature
adds OOS AUC on top of the 15m 2-factor (volume vr x penetration pen)."""
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

LF, DECAY, STR, EPS = 24, 0.6, 0.12, 1e-9
print("loading 15m + 5m + detect ...", flush=True)
_, r15, _ = load_archive("15m", root="study/recon_archive")
A = sorted(r15, key=lambda b: _f(b.get("start_time", 0)))
for b in A:
    b["open"] = _f(b.get("open_price")); b["close"] = _f(b.get("close_price"))
n = len(A)
O15 = np.array([b["open"] for b in A])
C = np.array([b["close"] for b in A]); H = np.array([_f(b.get("high")) for b in A]); L = np.array([_f(b.get("low")) for b in A])
CVv = np.array([_f(b.get("curr_vol")) for b in A]); T15 = np.array([_f(b.get("start_time")) for b in A])
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]

_, r5, _ = load_archive("5m", root="study/recon_archive")
B5 = sorted(r5, key=lambda b: _f(b.get("start_time", 0)))
t5 = np.array([_f(b.get("start_time")) for b in B5])
O5 = np.array([_f(b.get("open_price")) for b in B5]); C5 = np.array([_f(b.get("close_price")) for b in B5])
H5 = np.array([_f(b.get("high")) for b in B5]); L5 = np.array([_f(b.get("low")) for b in B5])
BV5 = np.array([_f(b.get("buy_vol")) for b in B5]); SV5 = np.array([_f(b.get("sell_vol")) for b in B5])
VOL5 = np.maximum(BV5 + SV5, EPS)
DB5 = (BV5 - SV5) / VOL5                                   # 5m delta share
RNG5 = np.maximum(H5 - L5, EPS); BF5 = np.abs(C5 - O5) / RNG5
print("   5m candles: %d  |  15m: %d" % (len(B5), n), flush=True)

walls = AL.detect(A)


def w5(a, b):
    return slice(a, b)


V = []
for w in walls:
    hits = w["hits"]; base = w["strength"] / (DECAY ** hits) if hits else w["strength"]
    P = w["price"]; band = w["band"]; side = w["side"]; r_lo = P - 3.0 * band; r_hi = P + 3.0 * band
    span = r_hi - r_lo; dd = 1.0 if side == "R" else -1.0     # dd toward BREAK (through the far edge)
    for j, (k0, k1, *_) in enumerate(w["radar_runs"]):
        if base * (DECAY ** j) < STR:
            continue
        ko = oc = None
        for k in range(k0, min(n, k0 + LF)):
            r = (0 if C[k] > r_hi else (1 if C[k] < r_lo else None)) if side == "R" else \
                (0 if C[k] < r_lo else (1 if C[k] > r_hi else None))
            if r is not None:
                ko, oc = k, r; break
        if oc is None or ko <= k0:                            # multi-bar only -> 5m window is fully causal
            continue
        # 15m 2-factor (as shipped)
        bx = fsum(_f(vv.get("b")) + _f(vv.get("s")) for k in range(k0, ko)
                  for _p, vv in (A[k].get("levels") or {}).items() if r_lo <= _f(_p) <= r_hi)
        rmv = float(np.median(CVv[max(0, k0 - 200):k0])) if k0 > 5 else CVv[k0]
        if rmv <= 0:
            continue
        vr = (bx / (ko - k0)) / rmv
        pen15 = min(1.0, max(0.0, ((H[k0] - r_lo) if side == "R" else (r_hi - L[k0])) / span))
        # FIXED causal window: the 5m candles of the ENTRY 15m bar only [t(k0), t(k0)+900). Known when the entry
        # bar closes; does NOT depend on the future resolution time ko -> no window-selection leak.
        a = bisect.bisect_left(t5, T15[k0]); b = bisect.bisect_left(t5, T15[k0] + 900.0)
        if b - a < 2:                                         # need >=2 sub-candles to see a sequence
            continue
        sl = w5(a, b); cl = C5[sl]; m5 = b - a
        pos = ((cl - r_lo) / span) if side == "R" else ((r_hi - cl) / span)      # each 5m close position in radar
        # sub-bar SEQUENCE features (genuinely 5m-only; 15m OHLC can't see ordering across sub-candles)
        advance = float((pos[-1] - pos[0]))                   # climbing toward break vs retreating within the bar
        within_eff = ((cl[-1] - cl[0]) * dd) / (float(np.sum(np.abs(np.diff(cl)))) + EPS)
        db_last = float(DB5[sl][-1]) * dd                     # last sub-candle net delta toward break
        db_trend = float(DB5[sl][-1] - DB5[sl][0]) * dd       # delta accelerating toward break across sub-candles
        maxpen = (np.max(H5[sl]) - r_lo) / span if side == "R" else (r_hi - np.min(L5[sl])) / span
        pokeback = min(1.5, max(0.0, float(maxpen))) - float(pos[-1])   # poked deep then closed back = intrabar reject
        absorb = float(np.mean(1.0 - BF5[sl]))
        clpos15 = min(1.0, max(0.0, ((C[k0] - r_lo) if side == "R" else (r_hi - C[k0])) / span))   # 15m entry CLOSE-pos
        body15 = float((C[k0] - O15[k0]) * dd) / span                                              # 15m entry BODY
        V.append({"resist": oc, "brk": 1 - oc, "yr": YR[k0], "vr": vr, "pen": pen15,
                  "clpos15": clpos15, "body15": body15,
                  "d5_advance": advance, "d5_within_eff": within_eff, "d5_db_last": db_last,
                  "d5_db_trend": db_trend, "d5_pokeback": pokeback, "d5_absorb": absorb, "n5": float(m5)})

N = len(V)
y = np.array([v["brk"] for v in V], float); yr = np.array([v["yr"] for v in V])
print("\n=== 5m PRECISION on 15m walls: %d multi-bar visits | base RESIST %.1f%% ===" % (N, 100 * (1 - y.mean())), flush=True)


def rauc(feat, pop=None):
    pop = pop or V
    a = [v[feat] for v in pop if v["resist"] and v[feat] == v[feat]]; b = [v[feat] for v in pop if not v["resist"] and v[feat] == v[feat]]
    return auc_p(a, b)[0] if len(a) > 10 and len(b) > 10 else float("nan")


print("\n(A) 5m SUB-BAR feature univariate AUC(resist)  [<0.5 -> higher feature -> BREAK]  vs the 15m 2-factor:", flush=True)
feats = ["d5_advance", "d5_within_eff", "d5_db_last", "d5_db_trend", "d5_pokeback", "d5_absorb", "n5",
         "clpos15", "body15", "pen", "vr"]
rows = []
for f in feats:
    g = rauc(f); g25 = rauc(f, [v for v in V if v["yr"] == 2025]); g26 = rauc(f, [v for v in V if v["yr"] == 2026])
    rows.append((abs(g - 0.5), f, g, g25, g26))
rows.sort(reverse=True)
for _, f, g, g25, g26 in rows:
    tag = "  [15m factor]" if f in ("pen", "vr") else ""
    flag = "  <-- both-yr" if (g - 0.5) * (g25 - 0.5) > 0 and (g - 0.5) * (g26 - 0.5) > 0 and abs(g - 0.5) >= 0.04 else ""
    print("    %-13s %.3f (25:%.2f 26:%.2f)%s%s" % (f, g, g25, g26, tag, flag), flush=True)


# (C) OOS incremental: does the best 5m feature add on top of (vr, pen)?
def fit(cols, yy):
    X = np.column_stack([np.ones(len(yy))] + cols)
    def nll(wv):
        z = X @ wv; p = np.clip(1 / (1 + np.exp(-z)), 1e-9, 1 - 1e-9)
        return -np.mean(yy * np.log(p) + (1 - yy) * np.log(1 - p))
    return minimize(nll, np.zeros(X.shape[1]), method="BFGS").x


def lin(w, cols):
    return np.column_stack([np.ones(len(cols[0]))] + cols) @ w


lvr = np.log1p(np.array([v["vr"] for v in V])); pen = np.array([v["pen"] for v in V])
print("\n(C) OOS: does each 5m SUB-BAR feature add AUC on top of (vr, pen)?  [break model, held-out year]", flush=True)
print("    feature        2025->2026 (base->+5m)     2026->2025 (base->+5m)", flush=True)
for f in ("d5_advance", "d5_within_eff", "d5_db_last", "d5_db_trend", "d5_pokeback", "d5_absorb"):
    xf = np.array([v[f] for v in V]); out = []
    for tr_y, te_y in ((2025, 2026), (2026, 2025)):
        tr = yr == tr_y; te = yr == te_y
        def zc(x): mu, sd = x[tr].mean(), x[tr].std() + EPS; return (x[tr] - mu) / sd, (x[te] - mu) / sd
        vtr, vte = zc(lvr); ptr, pte = zc(pen); ftr, fte = zc(xf)
        w1 = fit([vtr, ptr], y[tr]); z1 = lin(w1, [vte, pte])
        w2 = fit([vtr, ptr, ftr], y[tr]); z2 = lin(w2, [vte, pte, fte])
        a1 = auc_p(z1[y[te] == 1], z1[y[te] == 0])[0]; a2 = auc_p(z2[y[te] == 1], z2[y[te] == 0])[0]
        out.append("%.3f->%.3f (%+.3f)" % (a1, a2, a2 - a1))
    print("    %-13s %s   %s" % (f, out[0], out[1]), flush=True)

# (D) DECISIVE: baseline = 15m ONLY (vr, pen, clpos15, body15). Do the truly-5m-UNIQUE features add on top?
cp = np.array([v["clpos15"] for v in V]); bd = np.array([v["body15"] for v in V])
print("\n(D) baseline = 15m-only (vr,pen,clpos15,body15). Does any 5m-UNIQUE feature still add OOS?", flush=True)
print("    feature        2025->2026 (base15->+5m)   2026->2025 (base15->+5m)   [base15 is 15m alone]", flush=True)
for f in ("d5_within_eff", "d5_db_last", "d5_db_trend", "d5_absorb"):
    xf = np.array([v[f] for v in V]); out = []
    for tr_y, te_y in ((2025, 2026), (2026, 2025)):
        tr = yr == tr_y; te = yr == te_y
        def zc(x): mu, sd = x[tr].mean(), x[tr].std() + EPS; return (x[tr] - mu) / sd, (x[te] - mu) / sd
        vtr, vte = zc(lvr); ptr, pte = zc(pen); ctr, cte = zc(cp); btr, bte = zc(bd); ftr, fte = zc(xf)
        w1 = fit([vtr, ptr, ctr, btr], y[tr]); z1 = lin(w1, [vte, pte, cte, bte])
        w2 = fit([vtr, ptr, ctr, btr, ftr], y[tr]); z2 = lin(w2, [vte, pte, cte, bte, fte])
        a1 = auc_p(z1[y[te] == 1], z1[y[te] == 0])[0]; a2 = auc_p(z2[y[te] == 1], z2[y[te] == 0])[0]
        out.append("%.3f->%.3f (%+.3f)" % (a1, a2, a2 - a1))
    print("    %-13s %s   %s" % (f, out[0], out[1]), flush=True)
