"""Fit a monotonic recalibration map raw P(resist)% -> empirical hold% for the wall hover. Equal-count bins + PAVA
(isotonic) so it's monotone; anchor (0,0) and (100,100). Verify fit-2025 -> test-2026 (must flatten the gap OOS), then
emit copy-paste knots for absorption_level_detect._RECAL_X/_Y."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

A = sorted(load_archive("5m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
n = len(A)
print("5m bars=%d detecting..." % n, flush=True)
walls = AL.detect(A)
yr = []; PR = []; HOLD = []
for w in walls:
    broken = bool(w.get("broken")); i1 = int(w.get("i1", n - 1))
    for (rk0, rk1, pr) in w.get("radar_runs", ()):
        rk0 = int(rk0); rk1 = int(rk1)
        if rk1 >= n - 1:
            continue
        HOLD.append(0 if (broken and rk0 <= i1 <= rk1 + 2) else 1); PR.append(float(pr))
        yr.append(datetime.fromtimestamp(_f(A[rk0].get("start_time")), tz=timezone.utc).year)
PR = np.array(PR); HOLD = np.array(HOLD); yr = np.array(yr)


def pava(y, wt):                                   # pool-adjacent-violators -> non-decreasing
    y = list(map(float, y)); wt = list(map(float, wt)); i = 0
    ys = y[:]; ws = wt[:]
    k = 0
    while k < len(ys) - 1:
        if ys[k] > ys[k + 1] + 1e-12:
            nv = (ys[k] * ws[k] + ys[k + 1] * ws[k + 1]) / (ws[k] + ws[k + 1])
            ys[k] = nv; ws[k] += ws[k + 1]; del ys[k + 1]; del ws[k + 1]
            if k > 0:
                k -= 1
        else:
            k += 1
    # expand back is unnecessary for knot fitting; return the pooled level list with their mean-x handled by caller
    return ys, ws


def fit_knots(pr, hold, nb=20):
    o = np.argsort(pr); pr = pr[o]; hold = hold[o]
    idx = np.array_split(np.arange(len(pr)), nb)
    xs = np.array([pr[i].mean() for i in idx]); ys = np.array([hold[i].mean() * 100 for i in idx]); ws = np.array([len(i) for i in idx])
    # isotonic on ys with weights, keep same #knots by simple forward pooling then re-broadcast to bin x's
    yy = ys.copy()
    for _ in range(50):
        viol = np.where(yy[:-1] > yy[1:] + 1e-9)[0]
        if len(viol) == 0:
            break
        for k in viol:
            m = (yy[k] * ws[k] + yy[k + 1] * ws[k + 1]) / (ws[k] + ws[k + 1])
            yy[k] = yy[k + 1] = m
    kx = np.concatenate([[0.0], xs, [100.0]]); ky = np.concatenate([[0.0], np.clip(yy, 0, 100), [100.0]])
    # dedup/monotonic x
    kx2 = [kx[0]]; ky2 = [ky[0]]
    for a, b in zip(kx[1:], ky[1:]):
        if a > kx2[-1] + 1e-6:
            kx2.append(a); ky2.append(max(b, ky2[-1]))
    return np.array(kx2), np.array(ky2)


def cal_table(pr, hold, kx, ky, tag):
    cal = np.interp(pr, kx, ky)
    print("  [%s] after-recal calibration (stated-bin -> mean CALIBRATED vs ACTUAL hold):" % tag, flush=True)
    for lo, hi in ((0, 60), (60, 70), (70, 80), (80, 90), (90, 101)):
        m = (pr >= lo) & (pr < hi); N = int(m.sum())
        if N < 30:
            continue
        print("     raw %3d-%-3d n=%5d  calibrated=%5.1f%%  actual=%5.1f%%  (gap %+.1f)"
              % (lo, hi, N, cal[m].mean(), 100 * hold[m].mean(), cal[m].mean() - 100 * hold[m].mean()), flush=True)


kx, ky = fit_knots(PR, HOLD)
print("\nPOOLED knots (raw%% -> calibrated%%):", flush=True)
print("  X =", "[" + ", ".join("%.1f" % v for v in kx) + "]", flush=True)
print("  Y =", "[" + ", ".join("%.1f" % v for v in ky) + "]", flush=True)
print("\nVERIFY:", flush=True)
cal_table(PR, HOLD, kx, ky, "pooled on pooled")
kx25, ky25 = fit_knots(PR[yr == 2025], HOLD[yr == 2025])
cal_table(PR[yr == 2026], HOLD[yr == 2026], kx25, ky25, "fit2025 -> test2026")
print("\nspot map:", "  ".join("%d->%.0f" % (v, np.interp(v, kx, ky)) for v in (55, 65, 70, 77, 85, 90, 95)), flush=True)
