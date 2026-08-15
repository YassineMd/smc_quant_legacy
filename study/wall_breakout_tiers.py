"""TIERED extension of study/wall_breakout_bias.py. Same event (a RESISTED wall's radar breakout: open inside
[radar_lo, radar_hi], close beyond the defended extreme). Instead of a binary 1x-radar-length outcome, measure the
MAX TIER reached before touching the opposite radar extreme: tier N = radar_hi + N*L (support) / radar_lo - N*L
(resistance), L = radar_hi - radar_lo. SL = the opposite extreme (fixed). Scan k+1..k+H, SL-first.

Reports per TF/year: the tier SURVIVAL curve P(reach >= N x) for N=1..5, the tier-to-tier CONTINUATION P(>=N+1 | >=N),
the censored fraction (SL not hit within H -> a high-tier lower bound), and whether penetration / breakout-bar strength
/ visit defensive-strength predict reaching >= 2x (band-stratified AUC). TFs 5m 15m 1h. Both recon years.
Usage: python study/wall_breakout_tiers.py [tf ...]"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL, reward_eff

H = 200; RM = float(getattr(AL, "RADAR_MULT", 3.0)); MINVISIT = 3; MAXT = 5


def auc(score, label):
    ok = ~np.isnan(score); s = score[ok]; y = label[ok]
    npos = int((y == 1).sum()); nneg = int((y == 0).sum())
    if npos == 0 or nneg == 0:
        return float("nan")
    order = s.argsort(kind="mergesort"); sv = s[order]; r = np.empty(len(s)); i = 0
    while i < len(sv):
        j = i
        while j + 1 < len(sv) and sv[j + 1] == sv[i]:
            j += 1
        r[order[i:j + 1]] = (i + j) / 2.0 + 1.0; i = j + 1
    return (r[y == 1].sum() - npos * (npos + 1) / 2.0) / (npos * nneg)


def auc_strat(score, label, strat, nb=5):
    ok = ~np.isnan(score) & ~np.isnan(strat); s = score[ok]; y = label[ok]; g = strat[ok]
    if len(s) < nb * 30 or (y == 1).sum() < 15 or (y == 0).sum() < 15:
        return float("nan")
    qs = np.quantile(g, [b / nb for b in range(nb + 1)]); tot = w = 0.0
    for b in range(nb):
        mb = (g >= qs[b]) & (g <= qs[b + 1] if b == nb - 1 else g < qs[b + 1])
        if mb.sum() < 30 or (y[mb] == 1).sum() < 5 or (y[mb] == 0).sum() < 5:
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
                    ev[(k, side)] = (a, rlo, rhi, band)
                    break
        if c1 >= n:
            break
        c0 += 5000

    rows = []                                                 # (year, max_tier, censored, pen, bo_eff, str_whole, bandpct)
    for (k, side), (a, rlo, rhi, band) in ev.items():
        if k + 1 >= n:
            continue
        L = rhi - rlo; SL = rlo if side == "S" else rhi
        reached = 0.0; sl_hit = False
        for j in range(k + 1, min(n, k + 1 + H)):
            if side == "S":
                if Lo[j] <= SL:
                    sl_hit = True; break
                reached = max(reached, (Hi[j] - rhi) / L)
            else:
                if Hi[j] >= SL:
                    sl_hit = True; break
                reached = max(reached, (rlo - Lo[j]) / L)
        max_tier = min(MAXT, int(reached))
        vb = k - 1
        if vb - a < 2:
            continue
        base = reward_eff.strength_baseline(A, k)
        if not base or base.get("vol") is None:
            continue
        defk = "buy" if side == "S" else "sell"
        sw = reward_eff.strength(A, a, vb, base=base); sb = reward_eff.strength(A, k, k, base=base)
        if not (sw["ok"] and sb["ok"]):
            continue
        pen = (C[k] - rhi) / band if side == "S" else (rlo - C[k]) / band
        rows.append((int(yr[k]), max_tier, 0 if sl_hit else 1, pen, sb[defk]["effort_z"],
                     sw[defk]["effort_z"], band / C[k]))

    R = np.array(rows) if rows else np.zeros((0, 7))
    print("\n============  TF = %s  (events=%d, horizon=%d bars)  ============" % (tf, len(rows), H), flush=True)
    if len(rows) < 60:
        print("  too few events"); return
    for Y in (2025, 2026):
        m = R[:, 0] == Y
        if m.sum() < 40:
            print("  %d n<40" % Y); continue
        mt = R[m, 1]; cens = 100.0 * R[m, 2].mean()
        pN = [100.0 * (mt >= N).mean() for N in range(1, MAXT + 1)]
        print("  %d  n=%-5d censored=%.0f%%   P(reach>=Nx):  1x=%.0f%%  2x=%.0f%%  3x=%.0f%%  4x=%.0f%%  5x=%.0f%%"
              % (Y, int(m.sum()), cens, pN[0], pN[1], pN[2], pN[3], pN[4]), flush=True)
        cont = "        continuation  P(>=N+1|>=N): "
        for N in range(1, MAXT):
            num = (mt >= N + 1).sum(); den = (mt >= N).sum()
            cont += " %dx->%dx=%.0f%%" % (N, N + 1, 100.0 * num / den if den else float("nan"))
        print(cont, flush=True)
        y2 = (mt >= 2).astype(float); bandq = R[m, 6]
        line = "        AUC reach>=2x (band-strat):"
        for nm, col in (("pen", R[m, 3]), ("bo_eff", R[m, 4]), ("str_whole", R[m, 5])):
            line += "  %s=%.3f" % (nm, auc_strat(col, y2, bandq))
        print(line, flush=True)


if __name__ == "__main__":
    for tf in (sys.argv[1:] or ["15m", "1h", "5m"]):
        try:
            study(tf)
        except Exception as e:
            import traceback; print("TF %s FAILED: %r" % (tf, e)); traceback.print_exc()
