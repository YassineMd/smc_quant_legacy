# -*- coding: utf-8 -*-
"""TREND vs RANGE from wall CREATION + MITIGATION. Over a rolling window, derive wall features -- break rate, break
one-sidedness, creation rate, aggression-vs-absorption mix, lifespan -- and test whether they separate TRENDING from
RANGING windows (ground truth = Kaufman efficiency ratio ER over the same window: high ER = trend, low = range).
Also: do SIGNED wall features (net resistance-breaks, net R-creation) track the signed move (direction)? Both yr."""
import os, sys, bisect
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f, auc_p
from app import absorption_level_detect as AL

LF, W, STRIDE = 24, 96, 48                    # LF=resolve runs; W=window (24h on 15m); STRIDE=sample spacing
print("loading 15m + detect ...", flush=True)
_, r15, _ = load_archive("15m", root="study/recon_archive")
A = sorted(r15, key=lambda b: _f(b.get("start_time", 0)))
for b in A:
    b["open"] = _f(b.get("open_price")); b["close"] = _f(b.get("close_price"))
n = len(A)
C = np.array([b["close"] for b in A]); Hh = np.array([_f(b.get("high")) for b in A]); Ll = np.array([_f(b.get("low")) for b in A])
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
absmove = np.concatenate([[0.0], np.abs(np.diff(C))]); cum = np.cumsum(absmove)   # for windowed path length
walls = AL.detect(A)


def resolve(k0, r_lo, r_hi, side):
    for k in range(k0, min(n, k0 + LF)):
        r = (0 if C[k] > r_hi else (1 if C[k] < r_lo else None)) if side == "R" else \
            (0 if C[k] < r_lo else (1 if C[k] > r_hi else None))
        if r is not None:
            return k, r
    return None, None


# event lists: creations (i0, isR, isAgg) and mitigations (res_bar, hold?, isR, lifespan)
cr_bar, cr_isR, cr_agg = [], [], []
mi_bar, mi_hold, mi_isR, mi_life = [], [], [], []
for w in walls:
    P = w["price"]; band = w["band"]; side = w["side"]; r_lo = P - 3.0 * band; r_hi = P + 3.0 * band
    cr_bar.append(w["i0"]); cr_isR.append(1 if side == "R" else 0); cr_agg.append(1 if w["src"] in ("agg", "mix") else 0)
    for (k0, k1, pr) in w["radar_runs"]:
        ko, oc = resolve(k0, r_lo, r_hi, side)
        if oc is None:
            continue
        mi_bar.append(ko); mi_hold.append(oc); mi_isR.append(1 if side == "R" else 0); mi_life.append(ko - w["i0"])
cr_bar = np.array(cr_bar); cr_isR = np.array(cr_isR); cr_agg = np.array(cr_agg)
order = np.argsort(mi_bar)
mi_bar = np.array(mi_bar)[order]; mi_hold = np.array(mi_hold)[order]; mi_isR = np.array(mi_isR)[order]; mi_life = np.array(mi_life)[order]
cro = np.argsort(cr_bar); cr_bar = cr_bar[cro]; cr_isR = cr_isR[cro]; cr_agg = cr_agg[cro]
print("   walls %d | creations %d | mitigations %d" % (len(walls), len(cr_bar), len(mi_bar)), flush=True)

S = []
for t in range(W, n, STRIDE):
    t0 = t - W
    path = cum[t] - cum[t0]
    if path <= 0:
        continue
    er = abs(C[t] - C[t0]) / path                                   # trend strength (0..1)
    net_move = (C[t] - C[t0]) / C[t0] * 100.0                        # signed move over window
    a, b = np.searchsorted(mi_bar, t0), np.searchsorted(mi_bar, t)   # mitigations in [t0,t)
    ca, cb = np.searchsorted(cr_bar, t0), np.searchsorted(cr_bar, t)
    nmit = b - a; ncre = cb - ca
    if nmit < 5 or ncre < 2:
        continue
    hold = mi_hold[a:b]; isR = mi_isR[a:b]; life = mi_life[a:b]
    nbrk = int((hold == 0).sum())
    Rbrk = int(((hold == 0) & (isR == 1)).sum()); Sbrk = nbrk - Rbrk
    cagg = cr_agg[ca:cb]; cR = cr_isR[ca:cb]
    S.append({"yr": YR[t], "er": er, "net_move": net_move,
              "brk_rate": nbrk / nmit,
              "brk_asym": abs(Rbrk - Sbrk) / max(1, nbrk),
              "net_brk": (Rbrk - Sbrk) / max(1, nbrk),
              "create_rate": ncre / W * 100.0,
              "mitig_rate": nmit / W * 100.0,
              "agg_frac": float(cagg.mean()),
              "R_create": float(cR.mean()),
              "lifespan": float(life[hold == 0].mean()) if nbrk else np.nan})

M = len(S)
er_all = sorted(v["er"] for v in S); lo, hi = er_all[M // 3], er_all[2 * M // 3]
RANGE = [v for v in S if v["er"] <= lo]; TREND = [v for v in S if v["er"] >= hi]
print("\n=== TREND vs RANGE from walls: %d windows (W=%d) | ER split lo<=%.2f (RANGE) hi>=%.2f (TREND) ===" % (M, W, lo, hi), flush=True)
print("   RANGE n=%d  TREND n=%d  |  mean ER  range %.2f  trend %.2f" % (
    len(RANGE), len(TREND), np.mean([v["er"] for v in RANGE]), np.mean([v["er"] for v in TREND])), flush=True)


def au(feat, R, T):
    a = [v[feat] for v in T if v[feat] == v[feat]]; b = [v[feat] for v in R if v[feat] == v[feat]]
    return auc_p(a, b)[0] if len(a) > 10 and len(b) > 10 else float("nan")


print("\n(A) does the feature separate TREND from RANGE?  AUC>0.5 -> higher in TREND  [both-yr flag |dev|>=.05]", flush=True)
feats = ["brk_rate", "brk_asym", "mitig_rate", "create_rate", "agg_frac", "lifespan"]
R25, T25 = [v for v in RANGE if v["yr"] == 2025], [v for v in TREND if v["yr"] == 2025]
R26, T26 = [v for v in RANGE if v["yr"] == 2026], [v for v in TREND if v["yr"] == 2026]
rows = []
for f in feats:
    g = au(f, RANGE, TREND); g25 = au(f, R25, T25); g26 = au(f, R26, T26)
    rows.append((abs(g - 0.5), f, g, g25, g26, np.nanmean([v[f] for v in RANGE]), np.nanmean([v[f] for v in TREND])))
rows.sort(reverse=True)
for _, f, g, g25, g26, mr, mt in rows:
    flag = "  <== both-yr" if (g - 0.5) * (g25 - 0.5) > 0 and (g - 0.5) * (g26 - 0.5) > 0 and abs(g - 0.5) >= 0.05 else ""
    print("   %-12s AUC %.3f (25:%.2f 26:%.2f)   range %.2f -> trend %.2f%s" % (f, g, g25, g26, mr, mt, flag), flush=True)

print("\n(B) DIRECTION: do signed wall features track the signed move? (corr over all windows, both yr)", flush=True)
nm = np.array([v["net_move"] for v in S])
for f in ("net_brk", "R_create"):
    x = np.array([v[f] for v in S])
    c = np.corrcoef(x, nm)[0, 1]
    c25 = np.corrcoef([v[f] for v in S if v["yr"] == 2025], [v["net_move"] for v in S if v["yr"] == 2025])[0, 1]
    c26 = np.corrcoef([v[f] for v in S if v["yr"] == 2026], [v["net_move"] for v in S if v["yr"] == 2026])[0, 1]
    print("   corr(%-9s, net_move) = %+.3f  (25:%+.2f 26:%+.2f)" % (f, c, c25, c26), flush=True)

print("\n(C) TREND vs RANGE profile (means):", flush=True)
for lab, G in (("RANGE", RANGE), ("TREND", TREND)):
    print("   %-6s brk_rate %.2f  brk_asym %.2f  agg_frac %.2f  create/100 %.2f  lifespan %.1f" % (
        lab, np.mean([v["brk_rate"] for v in G]), np.mean([v["brk_asym"] for v in G]),
        np.mean([v["agg_frac"] for v in G]), np.mean([v["create_rate"] for v in G]),
        np.nanmean([v["lifespan"] for v in G])), flush=True)
