"""CAUSAL robustness check for study/wall_breakout_bias.py. The main study detects walls in 6000-bar chunks that extend
PAST the breakout, so radar_lo/hi (-> TP/SL) could carry a slight future-inclusion. Here, for each candidate breakout
at bar k, we RE-DETECT the wall using ONLY bars [k-CW, k-1] (strictly before the breakout), take THAT wall's causal
radar for the barriers, and re-measure the outcome. Events with no causally-present matching wall are DROPPED as
chunk artifacts. Report: survival rate + base P(bias) chunk-vs-causal, both years. TFs 5m/15m/1h (not 1m/4h).

Usage: python study/wall_breakout_causal.py [SAMPLE] [tf ...]   (SAMPLE = max events re-detected per TF; default 700)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

H = 50; RM = float(getattr(AL, "RADAR_MULT", 3.0)); MINVISIT = 3; CW = 3000


def candidates(A, n, O, C):
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
                    ev[(k, side)] = (P, band)
                    break
        if c1 >= n:
            break
        c0 += 5000
    return ev


def outcome(A_hi, A_lo, C_arr, n, k, side, rlo, rhi):
    L = rhi - rlo
    TP = (rhi + L) if side == "S" else (rlo - L); SL = rlo if side == "S" else rhi
    for j in range(k + 1, min(n, k + 1 + H)):
        sl = (A_lo[j] <= SL) if side == "S" else (A_hi[j] >= SL)
        tp = (A_hi[j] >= TP) if side == "S" else (A_lo[j] <= TP)
        if sl:
            return 0
        if tp:
            return 1
    return -1


def causal_wall(A, k, side, P0, O_k, C_k):
    """Re-detect over [k-CW, k-1] (strictly before the breakout); return causal (rlo, rhi) of the nearest same-side
    wall whose radar makes k a valid breakout (open inside, close beyond the defended extreme). None if absent."""
    lo = max(0, k - CW); win = A[lo:k]                       # bars up to k-1
    if len(win) < 60:
        return None
    best = None; bd = 1e18
    for w in AL.detect(win, skip_last=False):
        if w.get("side") != side:
            continue
        band = _f(w.get("band")); P = _f(w.get("price"))
        if band <= 0 or P <= 0:
            continue
        rlo = P - RM * band; rhi = P + RM * band
        if not (rlo <= O_k <= rhi):
            continue
        if not ((C_k > rhi) if side == "S" else (C_k < rlo)):
            continue
        if abs(P - P0) < bd:
            bd = abs(P - P0); best = (rlo, rhi)
    return best


def study(tf, sample):
    A = sorted(load_archive(tf, root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A])
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    yr = np.array([datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A])
    ev = candidates(A, n, O, C)
    keys = sorted(ev)
    if len(keys) > sample:                                    # even-spaced deterministic subsample
        step = len(keys) / sample
        keys = [keys[int(i * step)] for i in range(sample)]
    rec = []                                                  # (year, chunk_bias, survived, causal_bias)
    for (k, side) in keys:
        if k + 1 >= n:
            continue
        P0, band0 = ev[(k, side)]
        rlo0 = P0 - RM * band0; rhi0 = P0 + RM * band0
        chunk_b = outcome(Hi, Lo, C, n, k, side, rlo0, rhi0)
        cw = causal_wall(A, k, side, P0, O[k], C[k])
        if cw is None:
            rec.append((int(yr[k]), chunk_b, 0, -1)); continue
        cb = outcome(Hi, Lo, C, n, k, side, cw[0], cw[1])
        rec.append((int(yr[k]), chunk_b, 1, cb))
    R = np.array(rec)
    print("\n====  TF = %s  (candidates=%d, sampled=%d)  ====" % (tf, len(ev), len(rec)), flush=True)
    for Y in (2025, 2026):
        m = R[:, 0] == Y
        if m.sum() < 20:
            print("  %d n<20" % Y); continue
        surv = R[m, 2] == 1
        cbz = R[m, 1][(R[m, 1] >= 0)]                          # chunk base over resolved (all resolve)
        cau = R[m, 3][surv & (R[m, 3] >= 0)]                   # causal base over survivors
        print("  %d  sampled=%-4d  survived=%.0f%%  |  base(chunk)=%.1f%% n=%d  |  base(CAUSAL)=%.1f%% n=%d"
              % (Y, int(m.sum()), 100.0 * surv.mean(), 100.0 * cbz.mean() if len(cbz) else float("nan"), len(cbz),
                 100.0 * cau.mean() if len(cau) else float("nan"), len(cau)), flush=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    sample = int(args[0]) if args and args[0].isdigit() else 700
    tfs = [a for a in args if not a.isdigit()] or ["15m", "1h", "5m"]
    for tf in tfs:
        try:
            study(tf, sample)
        except Exception as e:
            import traceback; print("TF %s FAILED: %r" % (tf, e)); traceback.print_exc()
