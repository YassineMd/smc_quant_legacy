# -*- coding: utf-8 -*-
"""Does the wall SOURCE (mix = absorption+aggression, agg-only, abs-only) change P(RESIST)?
Causal-strength radar visits. Resist rate by src, both years; + WITHIN volume terciles (control for the volume signal).
Also mean volume-intensity per src (confound check)."""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

LF, DECAY, STR = 24, 0.6, 0.12
_, r15, _ = load_archive("15m", root="study/recon_archive")
A = sorted(r15, key=lambda b: _f(b.get("start_time", 0)))
n = len(A)
C = [_f(b.get("close_price")) for b in A]; H = [_f(b.get("high")) for b in A]; L = [_f(b.get("low")) for b in A]
CV = np.array([_f(b.get("curr_vol")) for b in A])
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
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
    bsrc = w.get("base_src", w["src"]); mbar = w.get("mix_bar", -1)
    for j, (k0, k1, *_) in enumerate(w["radar_runs"]):
        if base * (DECAY ** j) < STR:
            continue
        src = "mix" if (0 <= mbar <= k0) else bsrc          # CAUSAL src: was it ALREADY mix at this visit?
        ko = oc = None
        for k in range(k0, min(n, k0 + LF)):
            r = (0 if C[k] > r_hi else (1 if C[k] < r_lo else None)) if side == "R" else \
                (0 if C[k] < r_lo else (1 if C[k] > r_hi else None))
            if r is not None:
                ko, oc = k, r; break
        if oc is None or ko <= k0:
            continue
        bx = sum(box_vol(A[k], r_lo, r_hi) for k in range(k0, ko))
        rm = float(np.median(CV[max(0, k0 - 200):k0])) if k0 > 5 else CV[k0]
        vr = (bx / (ko - k0)) / rm if (rm > 0) else 0.0
        V.append({"resist": oc, "src": src, "vr": vr, "yr": YR[k0]})

base_r = sum(v["resist"] for v in V) / len(V)
print("\n=== resist by wall SOURCE === %d causal visits | base RESIST %.1f%%" % (len(V), 100 * base_r))
print("   src      n      RESIST%   (2025 / 2026)   mean_vr")
for s in ("mix", "agg", "abs"):
    sel = [v for v in V if v["src"] == s]
    if not sel:
        continue
    r = sum(v["resist"] for v in sel) / len(sel)
    s25 = [v for v in sel if v["yr"] == 2025]; s26 = [v for v in sel if v["yr"] == 2026]
    r25 = (sum(v["resist"] for v in s25) / len(s25)) if s25 else 0
    r26 = (sum(v["resist"] for v in s26) / len(s26)) if s26 else 0
    print("   %-6s  %5d   %5.1f%%    (%4.1f / %4.1f)   %.2f" % (s, len(sel), 100 * r, 100 * r25, 100 * r26, np.mean([v["vr"] for v in sel])))

# control for volume: resist by src WITHIN volume terciles
vrs = sorted(v["vr"] for v in V); t1 = vrs[len(vrs) // 3]; t2 = vrs[2 * len(vrs) // 3]
print("\n   -- resist by src WITHIN volume terciles (does src add beyond volume?) --")
for lab, lo, hi in (("LO-vol", -1e9, t1), ("MID-vol", t1, t2), ("HI-vol", t2, 1e9)):
    row = "   %-8s" % lab
    for s in ("mix", "agg", "abs"):
        sel = [v for v in V if v["src"] == s and lo <= v["vr"] < hi]
        row += "  %s %4.1f%%(n%d)" % (s, 100 * sum(v["resist"] for v in sel) / max(1, len(sel)), len(sel))
    print(row)
