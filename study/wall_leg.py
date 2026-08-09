# -*- coding: utf-8 -*-
"""THE LEG study: order-flow DEVELOPMENT from the last OPPOSING wall to the radar — does HOW price arrived predict
whether the target wall RESISTS or BREAKS? Anchor = most recent opposing-side wall (formation or a visit to it) before
entry, within MAXLEG. Over the leg [j0,k0] (oriented toward the target): CVD confirmation / divergence, flow accel,
candle-stat trends. Causal. AUC both years + control WITHIN the 2-factor (volume x penetration) model."""
import os, sys, bisect
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from math import fsum
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import build_stats, auc_p, _f
from app import absorption_level_detect as AL

LF, DECAY, STR, MAXLEG = 24, 0.6, 0.12, 96
print("loading 15m + build_stats + detect ...", flush=True)
_, r15, _ = load_archive("15m", root="study/recon_archive")
A = sorted(r15, key=lambda b: _f(b.get("start_time", 0)))
for b in A:
    b["open"] = _f(b.get("open_price")); b["close"] = _f(b.get("close_price"))
n = len(A)
C = [b["close"] for b in A]; H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
BV = np.array([_f(b.get("buy_vol")) for b in A]); SV = np.array([_f(b.get("sell_vol")) for b in A])
CVv = np.array([_f(b.get("curr_vol")) for b in A])
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
CD = np.concatenate([[0.0], np.cumsum(BV - SV)])[1:]          # CVD (cumulative delta)
DP = np.array([(BV[i] - SV[i]) / CVv[i] * 100.0 if CVv[i] > 0 else 0.0 for i in range(n)])
S, _O, _C = build_stats(A)
EFF = np.array([_f(S["effagg_sp"][i]) if S["effagg_sp"][i] == S["effagg_sp"][i] else 0.0 for i in range(n)])
ABS = np.array([_f(S["absorb_R"][i]) if S["absorb_R"][i] == S["absorb_R"][i] else 0.0 for i in range(n)])
walls = AL.detect(A)

# opposing anchors: bars where an S-wall (or R-wall) formed OR was visited (price bounced off / created it)
Sanch = set(); Ranch = set()
for w in walls:
    (Sanch if w["side"] == "S" else Ranch).add(w["i0"])
    for r in w["radar_runs"]:
        (Sanch if w["side"] == "S" else Ranch).add(r[0])
Sanch = sorted(Sanch); Ranch = sorted(Ranch)


def last_anchor(anchors, k0):
    i = bisect.bisect_left(anchors, k0) - 1
    if i >= 0 and k0 - anchors[i] <= MAXLEG:
        return anchors[i]
    return None


def slope(a):
    m = len(a)
    return float(np.polyfit(np.arange(m), a, 1)[0]) if m >= 2 else 0.0


V = []
for w in walls:
    hits = w["hits"]; base = w["strength"] / (DECAY ** hits) if hits else w["strength"]
    P = w["price"]; band = w["band"]; side = w["side"]; r_lo = P - 3.0 * band; r_hi = P + 3.0 * band
    for j, (k0, k1, *pr) in enumerate(w["radar_runs"]):
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
        j0 = last_anchor(Sanch if side == "R" else Ranch, k0)   # last OPPOSING wall (support for R target)
        if j0 is None or k0 - j0 < 3:
            continue
        d = 1.0 if side == "R" else -1.0                        # leg direction toward the target
        if (C[k0] - C[j0]) * d <= 0:                            # require the leg actually moved toward the target
            continue
        seg = slice(j0, k0 + 1)
        vol = float(CVv[seg].sum())
        legflow = ((CD[k0] - CD[j0]) / vol * 100.0) * d if vol > 0 else 0.0   # CVD confirmation (oriented)
        pslope = slope(np.array(C[j0:k0 + 1])) * d
        cslope = slope(CD[seg].astype(float)) * d
        hard_div = 1.0 if (CD[k0] - CD[j0]) * d < 0 else 0.0    # price toward target but net delta OPPOSED
        chop = float(np.mean((DP[seg] * d) < 0))               # fraction of leg bars with delta opposing the leg
        # 2-factor context (to control)
        span = r_hi - r_lo
        pen = ((H[k0] - r_lo) if side == "R" else (r_hi - L[k0])) / span if span > 0 else 0.0
        bx = fsum(_f(vv.get("b")) + _f(vv.get("s")) for k in range(k0, ko)
                  for _p, vv in (A[k].get("levels") or {}).items() if r_lo <= _f(_p) <= r_hi)
        rmv = float(np.median(CVv[max(0, k0 - 200):k0])) if k0 > 5 else CVv[k0]
        vr = (bx / (ko - k0)) / rmv if rmv > 0 else 0.0
        V.append({"resist": oc, "yr": YR[k0],
                  "legflow": legflow, "flow_slope": slope(DP[seg] * d), "cvd_conf": cslope,
                  "hard_div": hard_div, "chop": chop, "leg_len": float(k0 - j0),
                  "leg_dist": abs(C[k0] - C[j0]) / C[j0] * 100.0,
                  "eff": float(np.mean(EFF[seg]) * d), "absorb": float(np.mean(ABS[seg])),
                  "vr": vr, "pen": min(1.0, max(0.0, pen))})

base_r = sum(v["resist"] for v in V) / len(V)
vmed = np.median([v["vr"] for v in V]); pmed = np.median([v["pen"] for v in V])
print("\n=== LEG study: %d visits with an opposing-wall leg | base RESIST %.1f%% ===" % (len(V), 100 * base_r), flush=True)
print("   [AUC>0.5 -> higher feature -> RESIST (wall holds)]", flush=True)


def auc(feat):
    a = auc_p([v[feat] for v in V if v["resist"]], [v[feat] for v in V if not v["resist"]])[0]
    a25 = auc_p([v[feat] for v in V if v["resist"] and v["yr"] == 2025], [v[feat] for v in V if not v["resist"] and v["yr"] == 2025])[0]
    a26 = auc_p([v[feat] for v in V if v["resist"] and v["yr"] == 2026], [v[feat] for v in V if not v["resist"] and v["yr"] == 2026])[0]
    # add within the 2-factor cells (vr x pen quadrants) -> hi-lo delta of this feature's resist
    dl = []
    for vlab, vlo, vhi in (("LOv", -1, vmed), ("HIv", vmed, 1e9)):
        seg = [v for v in V if vlo <= v["vr"] < vhi]
        med = np.median([v[feat] for v in seg])
        lo = [v for v in seg if v[feat] < med]; hi = [v for v in seg if v[feat] >= med]
        dl.append("%s%+.0f" % (vlab, 100 * (sum(v["resist"] for v in hi) / max(1, len(hi)) - sum(v["resist"] for v in lo) / max(1, len(lo)))))
    print("   AUC %-11s %.3f (25:%.2f 26:%.2f)   within-vol hi-lo: %s" % (feat, a, a25, a26, " ".join(dl)), flush=True)


for f in ("legflow", "flow_slope", "cvd_conf", "hard_div", "chop", "leg_len", "leg_dist", "eff", "absorb"):
    auc(f)
print("\n   -- resist by hard CVD-divergence (price to target but net delta opposed) --", flush=True)
for hd in (0.0, 1.0):
    sel = [v for v in V if v["hard_div"] == hd]
    if sel:
        s25 = [v for v in sel if v["yr"] == 2025]; s26 = [v for v in sel if v["yr"] == 2026]
        print("   hard_div=%d n=%4d RESIST %.1f%% (25:%.1f/26:%.1f)" % (
            hd, len(sel), 100 * sum(v["resist"] for v in sel) / len(sel),
            100 * sum(v["resist"] for v in s25) / max(1, len(s25)), 100 * sum(v["resist"] for v in s26) / max(1, len(s26))))
