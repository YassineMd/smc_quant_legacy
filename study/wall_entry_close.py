# -*- coding: utf-8 -*-
"""Characterize the ENTRY-BAR close-position + body signal (surfaced by wall_5m_precision as a 15m predictor NOT in
the P(resist) model). Population: multi-bar 15m radar visits (ko>k0 -> entry bar strictly before resolution, causal).
    clpos15 = (C[k0]-r_lo)/span   -> where the entry bar CLOSES in the radar (0=near edge, 1=far/break edge)
    body15  = (C[k0]-O[k0])*dd/span -> entry body oriented toward BREAK
    pen     = (H[k0]-r_lo)/span    -> how deep the HIGH poked (the shipped factor)   [pen = clpos + wick]
    wick    = pen - clpos15         -> retreat from the high (poke-then-close-back)
Shape, magnitude (both yr), and INDEPENDENCE from penetration (corr, within-pen grid, OOS increment, decomposition)."""
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
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
walls = AL.detect(A)

V = []
for w in walls:
    hits = w["hits"]; base = w["strength"] / (DECAY ** hits) if hits else w["strength"]
    P = w["price"]; band = w["band"]; side = w["side"]; r_lo = P - 3.0 * band; r_hi = P + 3.0 * band
    span = r_hi - r_lo; dd = 1.0 if side == "R" else -1.0
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
        pen = min(1.0, max(0.0, ((H[k0] - r_lo) if side == "R" else (r_hi - L[k0])) / span))
        clpos = min(1.0, max(0.0, ((C[k0] - r_lo) if side == "R" else (r_hi - C[k0])) / span))
        body = float((C[k0] - O15[k0]) * dd) / span
        wick = max(0.0, pen - clpos)
        V.append({"resist": oc, "brk": 1 - oc, "yr": YR[k0], "vr": vr, "pen": pen,
                  "clpos": clpos, "body": body, "wick": wick})

N = len(V); baseR = 100 * sum(v["resist"] for v in V) / N
print("\n=== ENTRY close-position / body: %d multi-bar visits | base RESIST %.1f%% ===" % (N, baseR), flush=True)


def rauc(feat, pop=None):
    pop = pop or V
    a = [v[feat] for v in pop if v["resist"]]; b = [v[feat] for v in pop if not v["resist"]]
    return auc_p(a, b)[0] if len(a) > 10 and len(b) > 10 else float("nan")


print("\n(1) univariate AUC(resist)  [<0.5 -> higher -> BREAK]:", flush=True)
for f in ("clpos", "body", "pen", "wick", "vr"):
    print("    %-7s %.3f (25:%.2f 26:%.2f)" % (f, rauc(f), rauc(f, [v for v in V if v["yr"] == 2025]), rauc(f, [v for v in V if v["yr"] == 2026])), flush=True)


def curve(feat, nb=5):
    xs = sorted(v[feat] for v in V); edges = [xs[int(len(xs) * q / nb)] for q in range(nb)] + [xs[-1] + 1e-9]
    print("\n(2) SHAPE — RESIST%% by %s quintile (both yr):" % feat, flush=True)
    for q in range(nb):
        g = [v for v in V if edges[q] <= v[feat] < edges[q + 1]]
        if not g:
            continue
        g25 = [v for v in g if v["yr"] == 2025]; g26 = [v for v in g if v["yr"] == 2026]
        print("    Q%d %s in[%+.2f,%+.2f)  n=%4d  RESIST %5.1f%%  (25:%.1f 26:%.1f)" % (
            q + 1, feat, edges[q], edges[q + 1], len(g),
            100 * sum(v["resist"] for v in g) / len(g),
            100 * sum(v["resist"] for v in g25) / max(1, len(g25)), 100 * sum(v["resist"] for v in g26) / max(1, len(g26))), flush=True)


curve("clpos"); curve("body")

print("\n(3) INDEPENDENCE from penetration — correlations:", flush=True)
cl = np.array([v["clpos"] for v in V]); bd = np.array([v["body"] for v in V]); pn = np.array([v["pen"] for v in V]); wk = np.array([v["wick"] for v in V])
print("    corr(clpos,pen)=%+.2f  corr(body,pen)=%+.2f  corr(clpos,body)=%+.2f  corr(wick,pen)=%+.2f" % (
    np.corrcoef(cl, pn)[0, 1], np.corrcoef(bd, pn)[0, 1], np.corrcoef(cl, bd)[0, 1], np.corrcoef(wk, pn)[0, 1]), flush=True)

print("\n(4) within-PEN terciles: RESIST%% by clpos tercile (does clpos separate at fixed penetration?):", flush=True)
pe = sorted(pn); pt1, pt2 = pe[N // 3], pe[2 * N // 3]
ce = sorted(cl); ct1, ct2 = ce[N // 3], ce[2 * N // 3]
print("    pen-band \\ clpos   LOclpos          MIDclpos         HIclpos", flush=True)
for plab, plo, phi in (("LOpen", -1, pt1), ("MIDpen", pt1, pt2), ("HIpen", pt2, 1e9)):
    row = "    %-8s " % plab
    for clo, chi in ((-1, ct1), (ct1, ct2), (ct2, 1e9)):
        g = [v for v in V if plo <= v["pen"] < phi and clo <= v["clpos"] < chi]
        row += "  %5.1f%%(n=%3d)" % (100 * sum(v["resist"] for v in g) / max(1, len(g)), len(g))
    print(row, flush=True)


def fit(cols, yy):
    X = np.column_stack([np.ones(len(yy))] + cols)
    def nll(wv):
        z = X @ wv; p = np.clip(1 / (1 + np.exp(-z)), 1e-9, 1 - 1e-9)
        return -np.mean(yy * np.log(p) + (1 - yy) * np.log(1 - p))
    return minimize(nll, np.zeros(X.shape[1]), method="BFGS").x


def lin(w, cols):
    return np.column_stack([np.ones(len(cols[0]))] + cols) @ w


y = np.array([v["brk"] for v in V], float); yr = np.array([v["yr"] for v in V])
feild = {"clpos": cl, "body": bd, "pen": pn, "wick": wk, "vr": np.log1p(np.array([v["vr"] for v in V]))}


def oos(basecols, addcol):
    o = []
    for tr_y, te_y in ((2025, 2026), (2026, 2025)):
        tr = yr == tr_y; te = yr == te_y
        def zc(x): mu, sd = x[tr].mean(), x[tr].std() + EPS; return (x[tr] - mu) / sd, (x[te] - mu) / sd
        bcs_tr = []; bcs_te = []
        for c in basecols:
            a, b = zc(feild[c]); bcs_tr.append(a); bcs_te.append(b)
        w1 = fit(bcs_tr, y[tr]); z1 = lin(w1, bcs_te); a1 = auc_p(z1[y[te] == 1], z1[y[te] == 0])[0]
        atr, ate = zc(feild[addcol])
        w2 = fit(bcs_tr + [atr], y[tr]); z2 = lin(w2, bcs_te + [ate]); a2 = auc_p(z2[y[te] == 1], z2[y[te] == 0])[0]
        o.append("%.3f->%.3f(%+.3f)" % (a1, a2, a2 - a1))
    return o


print("\n(5) OOS incremental AUC (break model, held-out year):", flush=True)
for base, add in ((["pen"], "clpos"), (["pen"], "body"), (["pen", "body"], "clpos"),
                  (["clpos"], "wick"), (["wick"], "clpos"),
                  (["vr", "pen"], "clpos"), (["vr", "pen"], "body"), (["vr", "pen", "clpos"], "body")):
    o = oos(base, add)
    print("    base(%-16s)+%-6s   25->26 %s   26->25 %s" % ("+".join(base), add, o[0], o[1]), flush=True)
