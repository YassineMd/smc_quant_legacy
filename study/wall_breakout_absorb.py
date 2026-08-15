"""Does the breakout bar's ABSORPTION R (the stats-box 'Absorb R' = app.absorption.absorption) predict / improve the
Radar Runner (resisted-wall radar breakout)? A < 0 = an EASY, unopposed move out of the radar; A > 0 = an ABSORBED /
fought move (heavy opposing volume). Same event / outcome as study/wall_breakout_backtest.py.

Reports per TF (5m/15m/1h, both recon years): AUC of A for the directional bias (band-stratified = honest), the base
P(bias) by A-sign, and the scale+BE / trail P&L under A filters (easy A<=-.3 / A<=-1, absorbed A>=.3) -- does any A
band lift AVG/TRADE in BOTH years, or just thin the count? 0.04% RT fee, per-filter non-overlap. Usage: [tf ...]"""
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


def sim_trail(side, entry, sl0, tiers, ph, pl, pc):
    sl = sl0; reached = 0; nt = len(tiers)
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if side > 0 else (hi >= sl):
            return side * (sl - entry) / entry, off + 1
        while reached < nt and ((hi >= tiers[reached]) if side > 0 else (lo <= tiers[reached])):
            reached += 1; sl = entry if reached == 1 else tiers[reached - 2]
            if reached >= nt:
                return side * (tiers[-1] - entry) / entry, off + 1
    return (side * (pc[-1] - entry) / entry if len(pc) else 0.0), len(ph)


def auc(score, label):
    ok = ~np.isnan(score); s = score[ok]; y = label[ok]
    p = int((y == 1).sum()); q = int((y == 0).sum())
    if p == 0 or q == 0:
        return float("nan")
    o = s.argsort(kind="mergesort"); sv = s[o]; r = np.empty(len(s)); i = 0
    while i < len(sv):
        j = i
        while j + 1 < len(sv) and sv[j + 1] == sv[i]:
            j += 1
        r[o[i:j + 1]] = (i + j) / 2.0 + 1.0; i = j + 1
    return (r[y == 1].sum() - p * (p + 1) / 2.0) / (p * q)


def auc_strat(score, label, strat, nb=5):
    ok = ~np.isnan(score) & ~np.isnan(strat); s = score[ok]; y = label[ok]; g = strat[ok]
    if len(s) < nb * 30:
        return float("nan")
    qs = np.quantile(g, [b / nb for b in range(nb + 1)]); tot = w = 0.0
    for b in range(nb):
        mb = (g >= qs[b]) & (g <= qs[b + 1] if b == nb - 1 else g < qs[b + 1])
        if mb.sum() < 30 or (y[mb] == 1).sum() < 8 or (y[mb] == 0).sum() < 8:
            continue
        a = auc(s[mb], y[mb])
        if not np.isnan(a):
            tot += a * mb.sum(); w += mb.sum()
    return tot / w if w > 0 else float("nan")


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

    rows = []                                                 # (k, year, A, band%, bias, retBE, offBE, retTR, offTR)
    for (k, side) in sorted(ev):
        if k + 1 >= n:
            continue
        rlo, rhi = ev[(k, side)]; up = side == "S"; s = 1 if up else -1; L = rhi - rlo
        brk = rhi if up else rlo; sl0 = rlo if up else rhi
        tiers = [brk + s * N * L for N in range(1, MAXT + 1)]
        TP = tiers[0]; SL = sl0; bias = -1
        for j in range(k + 1, min(n, k + 1 + H)):
            slh = (Lo[j] <= SL) if up else (Hi[j] >= SL); tph = (Hi[j] >= TP) if up else (Lo[j] <= TP)
            if slh:
                bias = 0; break
            if tph:
                bias = 1; break
        if bias < 0:
            continue
        try:
            Aval = ABS.absorption(A, k)[0]
        except Exception:
            Aval = None
        if Aval is None:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H); ph = Hi[j0:j1]; pl = Lo[j0:j1]; pc = C[j0:j1]
        rBE, oBE = sim_scale(s, C[k], sl0, tiers[:3], [1 / 3, 1 / 3, 1 / 3], ph, pl, pc, True)
        rTR, oTR = sim_trail(s, C[k], sl0, tiers, ph, pl, pc)
        rows.append((k, int(yr[k]), float(Aval), band / C[k], bias, rBE - FEE, oBE, rTR - FEE, oTR))
    R = np.array(rows) if rows else np.zeros((0, 9))
    print("\n========  TF = %s   (events=%d)  ========" % (tf, len(rows)), flush=True)
    if len(rows) < 80:
        print("  too few events"); return

    def run(reti, offi, keep):
        res = {2025: [], 2026: []}; last = -1
        for r in R:
            k = int(r[0])
            if k <= last or not keep(r):
                continue
            res[int(r[1])].append(r[reti]); last = k + int(r[offi])
        return res

    def line(tag, res):
        for Y in (2025, 2026):
            a = np.array(res[Y])
            if len(a) < 15:
                print("      %-16s %d n<15" % (tag, Y)); continue
            print("      %-16s %d  n=%-4d win=%.0f%%  avg=%+.3f%%  net=%+.0f%%"
                  % (tag, Y, len(a), 100 * (a > 0).mean(), a.mean() * 100, a.sum() * 100), flush=True)

    for Y in (2025, 2026):
        m = R[:, 1] == Y; y = R[m, 4]; Av = R[m, 2]; bandq = R[m, 3]
        pe = 100.0 * y[Av < 0].mean() if (Av < 0).sum() else float("nan")
        pa = 100.0 * y[Av >= 0].mean() if (Av >= 0).sum() else float("nan")
        print("  %d  base=%.1f%%  AUC(A) raw=%.3f band=%.3f | P(bias): easy A<0=%.0f%% absorbed A>=0=%.0f%%"
              % (Y, 100 * y.mean(), auc(Av, y), auc_strat(Av, y, bandq), pe, pa), flush=True)
    FILT = [("baseline (all)", lambda r: True), ("easy A<=-0.3", lambda r: r[2] <= -0.3),
            ("very easy A<=-1", lambda r: r[2] <= -1.0), ("absorbed A>=0.3", lambda r: r[2] >= 0.3)]
    for schname, reti, offi in (("scale+BE", 5, 6), ("trail", 7, 8)):
        print("  --- %s ---" % schname, flush=True)
        for fname, keep in FILT:
            line(fname, run(reti, offi, keep))


if __name__ == "__main__":
    for tf in (sys.argv[1:] or ["1h", "15m", "5m"]):
        try:
            study(tf)
        except Exception as e:
            import traceback; print("TF %s FAILED: %r" % (tf, e)); traceback.print_exc()
