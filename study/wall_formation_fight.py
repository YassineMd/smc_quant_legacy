# -*- coding: utf-8 -*-
"""Does a hard-fought FORMATION make a wall more likely to HOLD? (causal: measured at the formation candle i0)
  form_vol  = formation candle total volume / rolling-median curr_vol   (a busy, contested creation)
  contest   = min(buy,sell)/(buy+sell) at i0                            (two-sided fight, not a one-sided walkover)
  opp_abs   = losing-side aggressive volume / rolling-median            (how hard the opposing side pushed)
  form_rng  = (H-L)/C at i0 / vpct                                      (a big volatile fight)
Outcome = resist (wall holds this visit). AUC both years + resist by tercile + control WITHIN visit-volume terciles."""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f, auc_p
from app import absorption_level_detect as AL

LF, DECAY, STR, ATR = 24, 0.6, 0.12, 50
_, r15, _ = load_archive("15m", root="study/recon_archive")
A = sorted(r15, key=lambda b: _f(b.get("start_time", 0)))
n = len(A)
C = [_f(b.get("close_price")) for b in A]; H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
BV = np.array([_f(b.get("buy_vol")) for b in A]); SV = np.array([_f(b.get("sell_vol")) for b in A])
CVv = np.array([_f(b.get("curr_vol")) for b in A])
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
vpct = np.array([(H[i] - L[i]) / C[i] if C[i] > 0 else 0.0 for i in range(n)])
vpma = np.array([vpct[max(0, i - ATR):i].mean() if i > 0 else vpct[i] for i in range(n)])
walls = AL.detect(A)


def box_vol(b, r_lo, r_hi):
    v = 0.0
    for ps, vv in (b.get("levels") or {}).items():
        try:
            p = float(ps)
        except (TypeError, ValueError):
            continue
        if r_lo <= p <= r_hi:
            v += _f(vv.get("b")) + _f(vv.get("s"))
    return v


V = []
for w in walls:
    hits = w["hits"]; base = w["strength"] / (DECAY ** hits) if hits else w["strength"]
    P = w["price"]; band = w["band"]; side = w["side"]; r_lo = P - 3.0 * band; r_hi = P + 3.0 * band
    i0 = w["i0"]
    rmf = float(np.median(CVv[max(0, i0 - 200):i0])) if i0 > 5 else CVv[i0]
    if rmf <= 0 or CVv[i0] <= 0:
        continue
    fv = CVv[i0] / rmf
    contest = min(BV[i0], SV[i0]) / (BV[i0] + SV[i0]) if (BV[i0] + SV[i0]) > 0 else 0.0
    oppa = min(BV[i0], SV[i0]) / rmf
    frng = (vpct[i0] / vpma[i0]) if vpma[i0] > 0 else 1.0
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
        bx = sum(box_vol(A[k], r_lo, r_hi) for k in range(k0, ko))
        rmv = float(np.median(CVv[max(0, k0 - 200):k0])) if k0 > 5 else CVv[k0]
        vr = (bx / (ko - k0)) / rmv if rmv > 0 else 0.0
        V.append({"resist": oc, "fv": fv, "contest": contest, "oppa": oppa, "frng": frng, "vr": vr, "yr": YR[k0]})

base_r = sum(v["resist"] for v in V) / len(V)
print("\n=== formation FIGHT -> hold? === %d causal visits | base RESIST %.1f%%" % (len(V), 100 * base_r))
for feat in ("fv", "contest", "oppa", "frng"):
    a = auc_p([v[feat] for v in V if v["resist"]], [v[feat] for v in V if not v["resist"]])[0]
    a25 = auc_p([v[feat] for v in V if v["resist"] and v["yr"] == 2025], [v[feat] for v in V if not v["resist"] and v["yr"] == 2025])[0]
    a26 = auc_p([v[feat] for v in V if v["resist"] and v["yr"] == 2026], [v[feat] for v in V if not v["resist"] and v["yr"] == 2026])[0]
    print("   AUC %-8s %.3f (25:%.2f 26:%.2f)   [>0.5 -> more fight -> HOLD]" % (feat, a, a25, a26))

print("\n   -- resist by formation-volume tercile --")
fvs = sorted(v["fv"] for v in V); t1 = fvs[len(fvs) // 3]; t2 = fvs[2 * len(fvs) // 3]
for lab, lo, hi in (("LO fv", -1e9, t1), ("MID fv", t1, t2), ("HI fv", t2, 1e9)):
    sel = [v for v in V if lo <= v["fv"] < hi]
    print("   %-7s n=%4d  RESIST %.1f%%" % (lab, len(sel), 100 * sum(v["resist"] for v in sel) / max(1, len(sel))))

print("\n   -- does formation-vol ADD within VISIT-volume terciles? (resist%: LO-fv / HI-fv) --")
vrs = sorted(v["vr"] for v in V); u1 = vrs[len(vrs) // 3]; u2 = vrs[2 * len(vrs) // 3]
fmed = float(np.median([v["fv"] for v in V]))
for lab, lo, hi in (("LO-visitvol", -1e9, u1), ("MID-visitvol", u1, u2), ("HI-visitvol", u2, 1e9)):
    seg = [v for v in V if lo <= v["vr"] < hi]
    lof = [v for v in seg if v["fv"] < fmed]; hif = [v for v in seg if v["fv"] >= fmed]
    print("   %-13s  LO-fv %.1f%%(n%d)  |  HI-fv %.1f%%(n%d)  delta %+.1f" % (
        lab, 100 * sum(v["resist"] for v in lof) / max(1, len(lof)), len(lof),
        100 * sum(v["resist"] for v in hif) / max(1, len(hif)), len(hif),
        100 * (sum(v["resist"] for v in hif) / max(1, len(hif)) - sum(v["resist"] for v in lof) / max(1, len(lof)))))
