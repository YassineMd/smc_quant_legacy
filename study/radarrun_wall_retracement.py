"""Does WALL PROXIMITY correlate with the RETRACEMENT of a Radar Runner trade? For each RR trade measure MAE (max adverse
excursion = deepest retracement against the position, %% of entry) and MFE (max favorable), and the distance to the
WITH-trade wall (support below a long / resistance above a short) and the OPPOSING wall (mirror). Report Pearson +
Spearman corr(MAE, with_dist / opp_dist) and corr(MFE, opp_dist), plus opp_dist-quartile buckets (avg MAE / MFE / win%).
Live sources 15c/30c/30bkt (+1h). TP 0.25%% candle-SL. Walls formed causally. IN-SAMPLE. python study/radarrun_wall_retracement.py"""
import os, sys, bisect
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_proptp_alltf_clock import detect, SLBUF
from app import absorption_level_detect as AL
FEE, SLIP, TP, H, RM = 0.0004, 0.0003, 0.0025, 200, 3.0
CELLS = [("clock", "study/clock_archive", "15m"), ("clock", "study/clock_archive", "30m"),
         ("bucket", "study/recon_archive", "30m"), ("bucket", "study/recon_archive", "1h")]


def sim_path(s, entry, tp, sl, ph, pl, pc):
    mae = mfe = 0.0
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        mae = max(mae, ((entry - lo) if s > 0 else (hi - entry)) / entry)
        mfe = max(mfe, ((hi - entry) if s > 0 else (entry - lo)) / entry)
        if (lo <= sl) if s > 0 else (hi >= sl):
            return "sl", off + 1, mae, mfe
        if (hi >= tp) if s > 0 else (lo <= tp):
            return "tp", off + 1, mae, mfe
    return "end", len(ph), mae, mfe


def all_walls(A):
    n = len(A); Sw = []; Rw = []; c0 = 0; step = 6000
    while c0 < n:
        c1 = min(n, c0 + step); Sl = A[c0:c1]
        try:
            ws = AL.detect(Sl, skip_last=False, radar_mult=RM)
        except Exception:
            ws = []
        for w in ws:
            side = w.get("side"); P = _f(w.get("price")); i0 = int(w.get("i0", -1))
            if side in ("S", "R") and P > 0 and i0 >= 0:
                (Sw if side == "S" else Rw).append((i0 + c0, P))
        if c1 >= n:
            break
        c0 += step - 1000
    Sw.sort(); Rw.sort()
    return Sw, Rw


def nearest(walls, i0s, k, entry, below):
    idx = bisect.bisect_right(i0s, k); best = None
    for j in range(idx):
        p = walls[j][1]
        if below and p < entry:
            d = entry - p
        elif (not below) and p > entry:
            d = p - entry
        else:
            continue
        if best is None or d < best:
            best = d
    return best


def spearman(x, y):
    if len(x) < 5:
        return float("nan")
    rx = np.argsort(np.argsort(x)); ry = np.argsort(np.argsort(y))
    return float(np.corrcoef(rx, ry)[0, 1])


def main():
    print("RR wall-proximity vs RETRACEMENT (MAE) correlation | TP0.25%% candle-SL | live sources | IN-SAMPLE\n", flush=True)
    for dsname, root, tf in CELLS:
        A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
        n = len(A)
        Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A]); C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
        Sw, Rw = all_walls(A); S_i0 = [w[0] for w in Sw]; R_i0 = [w[0] for w in Rw]
        sigs = detect(A, SLBUF.get(tf, 0.003))[0]
        MAE = []; MFE = []; WD = []; OD = []; WIN = []; last = -1
        for (k, s, entry, sl, dist, ts) in sigs:
            if k <= last:
                continue
            j0 = k + 1; j1 = min(n, k + 1 + H)
            if j0 >= n:
                continue
            outc, off, mae, mfe = sim_path(s, entry, entry * (1 + s * TP), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1]); last = k + int(off)
            sup = nearest(Sw, S_i0, k, entry, True); res = nearest(Rw, R_i0, k, entry, False)
            if sup is None or res is None:
                continue
            wd = (sup if s > 0 else res) / entry; od = (res if s > 0 else sup) / entry
            net = (mfe if outc == "tp" else 0)  # placeholder
            MAE.append(mae * 100); MFE.append(mfe * 100); WD.append(wd * 100); OD.append(od * 100); WIN.append(1 if outc == "tp" else 0)
        MAE = np.array(MAE); MFE = np.array(MFE); WD = np.array(WD); OD = np.array(OD); WIN = np.array(WIN)
        print("================ %s %s  (%d trades, avg MAE %.3f%% MFE %.3f%% win %.1f%%) ================"
              % (dsname, tf, len(MAE), MAE.mean(), MFE.mean(), 100 * WIN.mean()), flush=True)
        print("  corr(MAE, with_dist)  Pear %+.3f  Spear %+.3f    corr(MAE, opp_dist) Pear %+.3f  Spear %+.3f"
              % (np.corrcoef(MAE, WD)[0, 1], spearman(MAE, WD), np.corrcoef(MAE, OD)[0, 1], spearman(MAE, OD)), flush=True)
        print("  corr(MFE, opp_dist)   Pear %+.3f  Spear %+.3f    corr(MFE, with_dist) Pear %+.3f  Spear %+.3f"
              % (np.corrcoef(MFE, OD)[0, 1], spearman(MFE, OD), np.corrcoef(MFE, WD)[0, 1], spearman(MFE, WD)), flush=True)
        # opp_dist quartiles -> avg MAE / MFE / win
        q = np.percentile(OD, [25, 50, 75])
        print("  by OPP_DIST quartile (nearest opposing wall):  avg MAE / MFE / win%", flush=True)
        edges = [-1e9, q[0], q[1], q[2], 1e9]; labels = ["Q1 nearest", "Q2", "Q3", "Q4 farthest"]
        for a, b, lab in zip(edges[:-1], edges[1:], labels):
            m = (OD > a) & (OD <= b)
            if m.sum():
                print("    %-12s opp_dist %.2f-%.2f%%  MAE %.3f%%  MFE %.3f%%  win %.1f%%  (n=%d)"
                      % (lab, OD[m].min(), OD[m].max(), MAE[m].mean(), MFE[m].mean(), 100 * WIN[m].mean(), m.sum()), flush=True)
        print("", flush=True)


if __name__ == "__main__":
    main()
