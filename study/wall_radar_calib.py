# -*- coding: utf-8 -*-
"""Calibrate P(RESIST) vs the wall-level VOLUME INTENSITY inside the radar, for the hover tooltip.
CAUSAL strength visits. vr = box vol (footprint at radar levels) per bar over [k0,ko) / rolling-median curr_vol.
Outcome = resist (close back out the NEAR edge) vs break (close beyond the FAR edge), first-passage. Both years must agree."""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f, auc_p
from app import absorption_level_detect as AL

LF, DECAY, STR = 24, 0.6, 0.12
_, r15, _ = load_archive("15m", root="study/recon_archive")
A = sorted(r15, key=lambda b: _f(b.get("start_time", 0)))
n = len(A)
C = [_f(b.get("close_price")) for b in A]; H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
CV = np.array([_f(b.get("curr_vol")) for b in A])
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
walls = AL.detect(A)


def box_vol(bucket, r_lo, r_hi):
    v = 0.0
    for ps, vv in (bucket.get("levels") or {}).items():
        try:
            p = float(ps)
        except (TypeError, ValueError):
            continue
        if r_lo <= p <= r_hi:
            v += _f(vv.get("b")) + _f(vv.get("s"))
    return v


def outcome(k0, r_lo, r_hi, side):
    for k in range(k0, min(n, k0 + LF)):
        if side == "R":
            if C[k] > r_hi: return 0        # break
            if C[k] < r_lo: return 1        # resist
        else:
            if C[k] < r_lo: return 0
            if C[k] > r_hi: return 1
    return None


V = []
for w in walls:
    hits = w["hits"]; base = w["strength"] / (DECAY ** hits) if hits else w["strength"]
    P = w["price"]; band = w["band"]; side = w["side"]; r_lo = P - 3.0 * band; r_hi = P + 3.0 * band
    for j, (k0, k1) in enumerate(w["radar_runs"]):
        if base * (DECAY ** j) < STR:
            continue
        # find outcome bar (causal) then measure vr over [k0, ko)
        ko = None; oc = None
        for k in range(k0, min(n, k0 + LF)):
            r = None
            if side == "R":
                r = 0 if C[k] > r_hi else (1 if C[k] < r_lo else None)
            else:
                r = 0 if C[k] < r_lo else (1 if C[k] > r_hi else None)
            if r is not None:
                ko, oc = k, r; break
        if oc is None or ko <= k0:
            continue
        bx = sum(box_vol(A[k], r_lo, r_hi) for k in range(k0, ko))
        rm = float(np.median(CV[max(0, k0 - 200):k0])) if k0 > 5 else CV[k0]
        if bx <= 0 or rm <= 0:
            continue
        vr = (bx / (ko - k0)) / rm
        V.append({"resist": oc, "vr": vr, "yr": YR[k0]})

base_r = sum(v["resist"] for v in V) / len(V)
a = auc_p([v["vr"] for v in V if not v["resist"]], [v["vr"] for v in V if v["resist"]])[0]  # high vr -> break
print("\n=== calibration set: %d causal visits | base RESIST %.1f%% | AUC(vr->break) %.3f ===" % (len(V), 100 * base_r, a))

vrs = sorted(v["vr"] for v in V)
edges = [vrs[int(q * len(vrs))] for q in (0.0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875)] + [vrs[-1] + 1e-9]
print("   vr-octile   vr<=      RESIST%%   (2025 / 2026)   n")
tbl = []
for i in range(len(edges) - 1):
    sel = [v for v in V if edges[i] <= v["vr"] < edges[i + 1]]
    if not sel:
        continue
    r = sum(v["resist"] for v in sel) / len(sel)
    s25 = [v for v in sel if v["yr"] == 2025]; s26 = [v for v in sel if v["yr"] == 2026]
    r25 = (sum(v["resist"] for v in s25) / len(s25)) if s25 else 0
    r26 = (sum(v["resist"] for v in s26) / len(s26)) if s26 else 0
    mid_vr = np.median([v["vr"] for v in sel])
    print("   %d           %6.2f    %5.1f%%    (%4.1f / %4.1f)   %d" % (i + 1, edges[i + 1], 100 * r, 100 * r25, 100 * r26, len(sel)))
    tbl.append((round(float(mid_vr), 3), round(100 * r, 1)))
print("   -> calibration table (median vr, resist%%):", tbl)
