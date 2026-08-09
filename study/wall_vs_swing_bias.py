# -*- coding: utf-8 -*-
"""Head-to-head: WALL bias (creation-side) vs PRICE/CVD SWING bias (swing_lvn_detect.bias) — which LEADS the forward
move? Both computed CAUSALLY at each sample t (swing on a trailing window buckets[t-WIN:t]; wall from creations in
[t-W,t)). Metric = oriented forward return = fwd_move * bias_dir (>0 => the bias leads), + directional hit-rate, both
years, at several horizons. Also the swing HIGH-CONFIDENCE subset. Single process."""
import os, sys, time
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL
from app import swing_lvn_detect as SW
from app import wall_regime_detect as WR

W, WIN, STRIDE = 96, 600, 64        # wall window; swing trailing window; sample stride
print("loading 15m + detect ...", flush=True)
_, r15, _ = load_archive("15m", root="study/recon_archive")
A = sorted(r15, key=lambda b: _f(b.get("start_time", 0)))
for b in A:
    b["open"] = _f(b.get("open_price")); b["close"] = _f(b.get("close_price"))
n = len(A)
C = np.array([b["close"] for b in A])
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
marks = AL.detect(A)
cr_bar = np.array(sorted((w["i0"], 1 if w["side"] == "R" else 0) for w in marks))   # (i0, isR) sorted by i0
cbars = cr_bar[:, 0]; cisR = cr_bar[:, 1]

# time a few swing bias() calls
t0 = time.time()
for tt in (5000, 20000, 60000):
    SW.bias(A[max(0, tt - WIN):tt])
print("   swing bias() ~%.1f ms/call over %d-bar window" % ((time.time() - t0) / 3 * 1000, WIN), flush=True)

FS = (24, 48, 96)
rows = []
for t in range(WIN, n - max(FS), STRIDE):
    a = np.searchsorted(cbars, t - W); b = np.searchsorted(cbars, t)     # creations in [t-W, t)
    if b - a >= 3:
        rc = float(cisR[a:b].mean())
        wall_dir = 1 if rc <= WR.RC_UP else (-1 if rc >= WR.RC_DOWN else 0)   # support-heavy -> up
    else:
        wall_dir = 0
    sw = SW.bias(A[t - WIN:t])
    sw_dir = 1 if sw["dir"] == "long" else (-1 if sw["dir"] == "short" else 0)
    fwd = [(C[t + F] - C[t]) / C[t] * 100.0 for F in FS]
    rows.append((YR[t], wall_dir, sw_dir, sw["confidence"], *fwd))

R = np.array(rows, float)
yr = R[:, 0]; wall = R[:, 1]; sw = R[:, 2]; conf = R[:, 3]; FWD = {F: R[:, 4 + i] for i, F in enumerate(FS)}
print("\n=== %d samples | wall non-neutral %.0f%% | swing non-neutral %.0f%% ===" % (
    len(R), 100 * (wall != 0).mean(), 100 * (sw != 0).mean()), flush=True)


def report(name, d, F, mask=None):
    m = (d != 0) if mask is None else ((d != 0) & mask)
    fwd = FWD[F]
    orient = fwd[m] * d[m]                                    # return going WITH the bias
    hit = ((np.sign(fwd[m]) == d[m]).mean()) if m.sum() else float("nan")
    o25 = fwd[m & (yr == 2025)] * d[m & (yr == 2025)]; o26 = fwd[m & (yr == 2026)] * d[m & (yr == 2026)]
    print("   %-16s F=%3d  n=%4d cov=%2.0f%%  hit %.1f%%  oriented-ret %+.3f%%  (25:%+.3f 26:%+.3f)" % (
        name, F, m.sum(), 100 * m.mean(), 100 * hit,
        orient.mean() if len(orient) else 0.0, o25.mean() if len(o25) else 0.0, o26.mean() if len(o26) else 0.0), flush=True)


print("\n[oriented-ret > 0 both yr => the bias LEADS; ~0 => coincident/no edge]", flush=True)
for F in FS:
    report("WALL bias", wall, F)
    report("SWING bias", sw, F)
    cth = np.median(conf[sw != 0]) if (sw != 0).any() else 0.5
    report("SWING conf>med", sw, F, mask=conf >= cth)
    print("", flush=True)

# agreement between the two + correlation of each dir with forward move
both = (wall != 0) & (sw != 0)
print("   wall vs swing agree on direction: %.0f%% of jointly-non-neutral (n=%d)" % (
    100 * (wall[both] == sw[both]).mean(), both.sum()), flush=True)
for F in FS:
    print("   corr(sign) F=%3d : wall %+.3f  swing %+.3f" % (
        F, np.corrcoef(wall, np.sign(FWD[F]))[0, 1], np.corrcoef(sw, np.sign(FWD[F]))[0, 1]), flush=True)
