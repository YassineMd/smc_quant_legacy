# -*- coding: utf-8 -*-
"""Would a BASE-CONCENTRATION gate on AGGRESSION walls SHARPEN them or just THIN them? For each aggression wall compute
base_conc = share of the birth candle's taker volume in the ORIGIN third (buy-agg->bottom / sell-agg->top). Split by
base_conc tercile and compare wall QUALITY: (a) radar-visit RESIST rate (does it get respected), (b) ejection strength
(base). If high base_conc holds/ejects MORE (both yr) -> gate SHARPENS; if flat -> it just THINS. Absorption walls
(already gated) shown as reference."""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

LF, DECAY, STR, EXT = 24, 0.6, 0.12, AL.EXT_FRAC
print("loading 15m + detect ...", flush=True)
_, r15, _ = load_archive("15m", root="study/recon_archive")
A = sorted(r15, key=lambda b: _f(b.get("start_time", 0)))
for b in A:
    b["open"] = _f(b.get("open_price")); b["close"] = _f(b.get("close_price"))
n = len(A)
C = [b["close"] for b in A]; H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
walls = AL.detect(A)


def base_conc(w):
    i0 = w["i0"]; rows = AL._levels(A[i0]); rng = H[i0] - L[i0]
    tot = sum(b + s for _, b, s in rows)
    if tot <= 0 or rng <= 0:
        return None
    if w["side"] == "S":                                   # buy-agg -> origin at the BOTTOM third
        reg = [r for r in rows if r[0] <= L[i0] + EXT * rng]
    else:                                                  # sell-agg -> origin at the TOP third
        reg = [r for r in rows if r[0] >= H[i0] - EXT * rng]
    return sum(b + s for _, b, s in reg) / tot


def visits(w):
    """(resist, year) for each causal-strength MULTI-BAR radar visit of wall w."""
    P = w["price"]; band = w["band"]; side = w["side"]; rl = P - 3 * band; rh = P + 3 * band
    hits = w["hits"]; base = w["strength"] / (DECAY ** hits) if hits else w["strength"]
    out = []
    for j, (k0, k1, pr) in enumerate(w["radar_runs"]):
        if base * (DECAY ** j) < STR:
            continue
        for k in range(k0, min(n, k0 + LF)):
            r = (0 if C[k] > rh else (1 if C[k] < rl else None)) if side == "R" else \
                (0 if C[k] < rl else (1 if C[k] > rh else None))
            if r is not None:
                if k > k0:
                    out.append((r, YR[k0]))
                break
    return out


agg = []
for w in walls:
    if w["src"] != "agg":
        continue
    bc = base_conc(w)
    if bc is None:
        continue
    hits = w["hits"]; ej = w["strength"] / (DECAY ** hits) if hits else w["strength"]
    agg.append({"bc": bc, "ej": ej, "visits": visits(w)})

bcs = sorted(x["bc"] for x in agg); N = len(agg); t1, t2 = bcs[N // 3], bcs[2 * N // 3]
print("\n=== AGGRESSION walls by BASE-CONCENTRATION (n=%d) | tercile cuts %.2f / %.2f ===" % (N, t1, t2), flush=True)
print("   [does higher base_conc -> better wall? resist rate + ejection, both yr]\n", flush=True)


def rr(vs):
    if not vs:
        return "no visits"
    n = len(vs); res = sum(v[0] for v in vs)
    v25 = [v for v in vs if v[1] == 2025]; v26 = [v for v in vs if v[1] == 2026]
    return "visits=%4d RESIST %.1f%% (25:%.1f 26:%.1f)" % (
        n, 100 * res / n, 100 * sum(v[0] for v in v25) / max(1, len(v25)), 100 * sum(v[0] for v in v26) / max(1, len(v26)))


for lab, lo, hi in (("lo-base_conc", -1, t1), ("mid", t1, t2), ("hi-base_conc", t2, 1e9)):
    g = [x for x in agg if lo <= x["bc"] < hi]
    vs = [v for x in g for v in x["visits"]]
    print("   %-13s n=%4d  mean bc %.2f  mean ej %.2f  |  %s" % (
        lab, len(g), np.mean([x["bc"] for x in g]), np.mean([x["ej"] for x in g]), rr(vs)), flush=True)

# what a gate would keep, and whether kept > dropped
for thr in (0.34, 0.40, 0.50):
    keep = [x for x in agg if x["bc"] >= thr]; drop = [x for x in agg if x["bc"] < thr]
    vk = [v for x in keep for v in x["visits"]]; vd = [v for x in drop for v in x["visits"]]
    rk = 100 * sum(v[0] for v in vk) / max(1, len(vk)); rd = 100 * sum(v[0] for v in vd) / max(1, len(vd))
    print("   gate bc>=%.2f : KEEP %d walls (%.0f%%) resist %.1f%%  vs  DROP resist %.1f%%  (delta %+.1fpp, ej %.2f vs %.2f)" % (
        thr, len(keep), 100 * len(keep) / N, rk, rd, rk - rd,
        np.mean([x["ej"] for x in keep]), np.mean([x["ej"] for x in drop])), flush=True)

# reference: absorption (already gated) + all-aggression base rates
absv = [v for w in walls if w["src"] == "abs" for v in visits(w)]
allagg = [v for x in agg for v in x["visits"]]
print("\n   REFERENCE: ABSORPTION walls %s" % rr(absv), flush=True)
print("   REFERENCE: ALL aggression  %s" % rr(allagg), flush=True)
