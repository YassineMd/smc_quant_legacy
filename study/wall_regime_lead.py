# -*- coding: utf-8 -*-
"""Does the CREATION SIDE (resistance-share of new walls) LEAD the forward move, or only coincide with the past one?
Predictor over the PAST window [t-W, t] (walls fully formed by t: i0 <= t-EJ_WIN, no ejection look-ahead). Outcome =
FORWARD move over [t, t+F]. Report corr(R_create_past, fwd_move) at several F, the CONTEMPORANEOUS corr for
reference, and the PARTIAL corr controlling PAST momentum (the real 'does it lead beyond momentum' test). Both yr."""
import os, sys
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

W, EJ = 96, AL.EJ_WIN                          # past window; ejection-window gap (no look-ahead on formation)
print("loading 15m + detect ...", flush=True)
_, r15, _ = load_archive("15m", root="study/recon_archive")
A = sorted(r15, key=lambda b: _f(b.get("start_time", 0)))
for b in A:
    b["open"] = _f(b.get("open_price")); b["close"] = _f(b.get("close_price"))
n = len(A)
C = np.array([b["close"] for b in A])
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]
walls = AL.detect(A)

cr_bar = np.array([w["i0"] for w in walls]); cr_isR = np.array([1 if w["side"] == "R" else 0 for w in walls])
o = np.argsort(cr_bar); cr_bar = cr_bar[o]; cr_isR = cr_isR[o]
print("   walls %d" % len(walls), flush=True)


def pcorr(x, y, z):                            # partial corr of x,y controlling z (residualize both on z)
    z1 = np.column_stack([np.ones(len(z)), z])
    rx = x - z1 @ np.linalg.lstsq(z1, x, rcond=None)[0]
    ry = y - z1 @ np.linalg.lstsq(z1, y, rcond=None)[0]
    return np.corrcoef(rx, ry)[0, 1]


def build(F, stride):
    rows = []
    for t in range(W, n - F, stride):
        ca = np.searchsorted(cr_bar, t - W); cb = np.searchsorted(cr_bar, t - EJ)   # formed & fully-confirmed by t
        if cb - ca < 3:
            continue
        rcreate = float(cr_isR[ca:cb].mean())
        past = (C[t] - C[t - W]) / C[t - W] * 100.0
        fwd = (C[t + F] - C[t]) / C[t] * 100.0
        rows.append((YR[t], rcreate, past, fwd))
    return np.array(rows, float)


print("\n=== creation-side LEAD test (R_create over past [t-W,t], move over [t,t+F]) ===", flush=True)
print("   reference: CONTEMPORANEOUS corr(R_create, same-window move) was -0.645\n", flush=True)
print("   F(bars)  n     corr(Rc_past, FWD)   both-yr        partial|past-move    both-yr", flush=True)
for F in (12, 24, 48, 96):
    R = build(F, max(24, F // 2))                                   # stride ~ F/2 to limit forward-window overlap
    yr = R[:, 0]; rc = R[:, 1]; past = R[:, 2]; fwd = R[:, 3]
    c = np.corrcoef(rc, fwd)[0, 1]
    c25 = np.corrcoef(rc[yr == 2025], fwd[yr == 2025])[0, 1]; c26 = np.corrcoef(rc[yr == 2026], fwd[yr == 2026])[0, 1]
    pc = pcorr(rc, fwd, past)
    p25 = pcorr(rc[yr == 2025], fwd[yr == 2025], past[yr == 2025]); p26 = pcorr(rc[yr == 2026], fwd[yr == 2026], past[yr == 2026])
    print("   %3d    %5d    %+.3f            (25:%+.2f 26:%+.2f)   %+.3f              (25:%+.2f 26:%+.2f)" % (
        F, len(R), c, c25, c26, pc, p25, p26), flush=True)

# also: does the past MOVE itself lead (momentum autocorr)? -> context for the partial
print("\n   context — corr(past_move, fwd_move) [pure momentum autocorrelation]:", flush=True)
for F in (24, 48, 96):
    R = build(F, max(24, F // 2)); c = np.corrcoef(R[:, 2], R[:, 3])[0, 1]
    print("     F=%3d  corr %+.3f" % (F, c), flush=True)

# directional hit-rate: sign(fwd) vs Rc extremes (does high resistance-creation precede a DOWN move?)
print("\n   directional check @F=48: P(forward DOWN) by R_create tercile:", flush=True)
R = build(48, 24); yr = R[:, 0]; rc = R[:, 1]; fwd = R[:, 3]
xs = np.sort(rc); t1, t2 = xs[len(xs) // 3], xs[2 * len(xs) // 3]
for lab, m in (("lo Rc (support-heavy)", rc <= t1), ("mid", (rc > t1) & (rc < t2)), ("hi Rc (resistance-heavy)", rc >= t2)):
    fd = fwd[m]
    y25 = fwd[m & (yr == 2025)]; y26 = fwd[m & (yr == 2026)]
    print("     %-24s n=%4d  P(down) %.1f%% (25:%.0f 26:%.0f)  mean fwd %+.3f%%" % (
        lab, m.sum(), 100 * (fd < 0).mean(), 100 * (y25 < 0).mean(), 100 * (y26 < 0).mean(), fd.mean()), flush=True)
