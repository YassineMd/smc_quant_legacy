"""DEEPER on the ABSORBED-breakout tilt (study/wall_breakout_absorb.py found absorbed A>=0.3 breaks outperform, easy
A<0 underperform). Robustness checks, per TF (5m/15m/1h), both recon years:
 [A] avg-per-trade (scale+BE) by ABSORPTION QUINTILE  -> is 'more absorbed = better' MONOTONIC (not a single-cut fluke)?
 [B] tier survival P(reach >=1x/2x/3x)  for easy (A<0) vs absorbed (A>=0.3)  -> does absorption push HIGHER tiers?
 [C] A-threshold sweep (>=0 / >=0.3 / >=0.5 / >=1) non-overlap avg/trade + n  -> where the lift lives, both years.
Same event/outcome as the other wall_breakout studies; 0.04% RT fee. Usage: [tf ...]"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL, absorption as ABS

H = 200; RM = float(getattr(AL, "RADAR_MULT", 3.0)); MINVISIT = 3; FEE = 0.0004; MAXT = 5


def sim_scale(side, entry, sl0, targets, weights, ph, pl, pc, be):
    sl = sl0; pos = 1.0; realized = 0.0; ti = 0; nt = len(targets)
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if side > 0 else (hi >= sl):
            return realized + pos * side * (sl - entry) / entry, off + 1
        while ti < nt and ((hi >= targets[ti]) if side > 0 else (lo <= targets[ti])):
            realized += weights[ti] * side * (targets[ti] - entry) / entry; pos -= weights[ti]; ti += 1
            if be and ti == 1:
                sl = entry
            if pos <= 1e-9:
                return realized, off + 1
    return realized + (pos * side * (pc[-1] - entry) / entry if len(pc) else 0.0), len(ph)


def study(tf):
    A = sorted(load_archive(tf, root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A])
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    yr = np.array([datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A])

    ev = {}; c0 = 0
    while c0 < n:
        c1 = min(n, c0 + 6000); S = A[c0:c1]
        for w in AL.detect(S, skip_last=False):
            side = w.get("side"); P = _f(w.get("price")); band = _f(w.get("band"))
            if band <= 0 or P <= 0:
                continue
            rlo = P - RM * band; rhi = P + RM * band
            for r in w.get("radar_runs", ()):
                if len(r) < 2:
                    continue
                a = int(r[0]) + c0; b = int(r[1]) + c0
                for k in range(b, min(b + 2, n - 1) + 1):
                    if not (rlo <= O[k] <= rhi):
                        continue
                    broke = (C[k] > rhi) if side == "S" else (C[k] < rlo)
                    if not broke or (k - a) < MINVISIT or (k, side) in ev:
                        continue
                    ev[(k, side)] = (rlo, rhi); break
        if c1 >= n:
            break
        c0 += 5000

    rows = []                                                 # (k, year, A, maxtier, retBE, offBE)
    for (k, side) in sorted(ev):
        if k + 1 >= n:
            continue
        rlo, rhi = ev[(k, side)]; up = side == "S"; s = 1 if up else -1; L = rhi - rlo
        brk = rhi if up else rlo; sl0 = rlo if up else rhi
        tiers = [brk + s * N * L for N in range(1, MAXT + 1)]
        reached = 0.0
        for j in range(k + 1, min(n, k + 1 + H)):
            if (Lo[j] <= sl0) if up else (Hi[j] >= sl0):
                break
            reached = max(reached, ((Hi[j] - brk) if up else (brk - Lo[j])) / L)
        maxtier = min(MAXT, int(reached))
        try:
            Aval = ABS.absorption(A, k)[0]
        except Exception:
            Aval = None
        if Aval is None:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H); ph = Hi[j0:j1]; pl = Lo[j0:j1]; pc = C[j0:j1]
        rBE, oBE = sim_scale(s, C[k], sl0, tiers[:3], [1 / 3, 1 / 3, 1 / 3], ph, pl, pc, True)
        rows.append((k, int(yr[k]), float(Aval), maxtier, rBE - FEE, oBE))
    R = np.array(rows) if rows else np.zeros((0, 6))
    print("\n========  TF = %s   (events=%d)  ========" % (tf, len(rows)), flush=True)
    if len(rows) < 100:
        print("  too few events"); return
    for Y in (2025, 2026):
        m = R[:, 1] == Y; Av = R[m, 2]; ret = R[m, 4]; mt = R[m, 3]
        # [A] avg/trade by absorption quintile (Q5 = most absorbed)
        qs = np.quantile(Av, [0, .2, .4, .6, .8, 1.0]); ql = "  %d [A] avg/trade by A-quintile:" % Y
        for b in range(5):
            mb = (Av >= qs[b]) & (Av <= qs[b + 1] if b == 4 else Av < qs[b + 1])
            ql += " Q%d(A~%.1f)=%+.3f%%" % (b + 1, np.median(Av[mb]) if mb.sum() else 0, 100 * ret[mb].mean() if mb.sum() else float("nan"))
        print(ql, flush=True)
        # [B] tier survival easy vs absorbed
        for lbl, msk in (("easy A<0    ", Av < 0), ("absorbed A>=.3", Av >= 0.3)):
            if msk.sum() < 20:
                continue
            tt = mt[msk]
            print("      [B] %s n=%-4d  P(>=1x)=%.0f%% >=2x=%.0f%% >=3x=%.0f%%"
                  % (lbl, msk.sum(), 100 * (tt >= 1).mean(), 100 * (tt >= 2).mean(), 100 * (tt >= 3).mean()), flush=True)
    # [C] threshold sweep (non-overlap), scale+BE
    print("  [C] A-threshold sweep (scale+BE, non-overlap):", flush=True)
    for thr in (-99, 0.0, 0.3, 0.5, 1.0):
        res = {2025: [], 2026: []}; last = -1
        for r in R:
            k = int(r[0])
            if k <= last or r[2] < thr:
                continue
            res[int(r[1])].append(r[4]); last = k + int(r[5])
        out = "      A>=%-5s" % ("all" if thr < -90 else ("%.1f" % thr))
        for Y in (2025, 2026):
            a = np.array(res[Y]); out += "  %d n=%-4d avg=%+.3f%%" % (Y, len(a), a.mean() * 100 if len(a) else float("nan"))
        print(out, flush=True)


if __name__ == "__main__":
    for tf in (sys.argv[1:] or ["5m", "15m", "1h"]):
        try:
            study(tf)
        except Exception as e:
            import traceback; print("TF %s FAILED: %r" % (tf, e)); traceback.print_exc()
