"""NO-WICK MOMENTUM + WALL CONFLUENCE filter (user 2026-08-20). Same signals as study/nowick_momentum (bull no-lower-wick
=LONG / bear no-upper-wick=SHORT, entry@close, SL 0.1% beyond candle extreme, TP 0.2% net maker), BUT take a signal ONLY
if the entry price sits inside a MATCHING-side wall's radar zone (support/buy wall [P+-3band] for a long, resistance/sell
wall for a short), formed causally before the signal bar. Reports BASELINE vs WALL-FILTERED (n, win, exp) per cell, OOS.
Clock+bucket 15m/30m/1h/4h. IN-SAMPLE. python study/nowick_wall.py"""
import os, sys, bisect
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app.engulf_sr_detect import _ohlc
from app import absorption_level_detect as AL
FEE, TPNET, H, SLBUF, WICK_TOL, RM = 0.0004, 0.0020, 200, 0.001, 0.05, 3.0
GTP = TPNET + FEE
DATASETS = [("clock", "study/clock_archive"), ("bucket", "study/recon_archive")]
TFS = ["15m", "30m", "1h", "4h"]


def sim(s, entry, tp, sl, ph, pl, pc):
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if s > 0 else (hi >= sl):
            return "sl", s * (sl - entry) / entry, off + 1
        if (hi >= tp) if s > 0 else (lo <= tp):
            return "tp", s * (tp - entry) / entry, off + 1
    return "end", (s * (pc[-1] - entry) / entry if len(pc) else 0), len(ph)


def load_arrays(root, tf):
    A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    O = np.zeros(n); C = np.zeros(n); Hi = np.zeros(n); Lo = np.zeros(n); YR = np.zeros(n, dtype=int)
    for i, b in enumerate(A):
        O[i], C[i], Hi[i], Lo[i] = _ohlc(b)
        t = _f(b.get("start_time", 0)); YR[i] = datetime.fromtimestamp(t, tz=timezone.utc).year if t else 0
    return A, O, C, Hi, Lo, YR, n


def detect_walls(A):
    """chunked AL.detect -> per-side sorted-by-i0 lists of (i0, rlo, rhi)."""
    n = len(A); S_w = []; R_w = []; c0 = 0; step = 6000
    while c0 < n:
        c1 = min(n, c0 + step); Sl = A[c0:c1]
        try:
            ws = AL.detect(Sl, skip_last=False, radar_mult=RM)
        except Exception:
            ws = []
        for w in ws:
            side = w.get("side"); P = _f(w.get("price")); band = _f(w.get("band")); i0 = int(w.get("i0", -1))
            if side not in ("S", "R") or P <= 0 or band <= 0 or i0 < 0:
                continue
            rlo = P - RM * band; rhi = P + RM * band
            (S_w if side == "S" else R_w).append((i0 + c0, rlo, rhi))
        if c1 >= n:
            break
        c0 += step - 1000
    S_w.sort(); R_w.sort()
    return S_w, R_w


def in_wall(side_walls, i, price):
    """any wall formed <= i whose radar [rlo,rhi] contains price."""
    idx = bisect.bisect_right([w[0] for w in side_walls], i)   # walls formed at/before bar i
    for j in range(idx):
        if side_walls[j][1] <= price <= side_walls[j][2]:
            return True
    return False


def signals(O, C, Hi, Lo, YR, n):
    out = []
    for i in range(n):
        rng = Hi[i] - Lo[i]
        if rng <= 0:
            continue
        blo = min(O[i], C[i]); bhi = max(O[i], C[i])
        if C[i] > O[i] and (blo - Lo[i]) / rng <= WICK_TOL:
            out.append((i, 1, C[i], Lo[i], int(YR[i])))
        elif C[i] < O[i] and (Hi[i] - bhi) / rng <= WICK_TOL:
            out.append((i, -1, C[i], Hi[i], int(YR[i])))
    return out


def evaluate(sigs, Hi, Lo, C, n):
    raw = []; last = -1
    for (i, s, entry, ext, yr) in sigs:
        if i <= last:
            continue
        sl = ext * (1 - SLBUF) if s > 0 else ext * (1 + SLBUF)
        dist = abs(entry - sl) / entry
        if dist <= 0:
            continue
        j0 = i + 1; j1 = min(n, i + 1 + H)
        outc, gross, off = sim(s, entry, entry * (1 + s * GTP), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        raw.append((gross - FEE, yr)); last = i + int(off)
    return raw


def stats(raw, yr=None):
    r = [x for x in raw if (yr is None or x[1] == yr)]
    if not r:
        return "n=0"
    nets = np.array([x[0] for x in r]) * 100.0
    return "n=%-5d %4.1f%% %+.4f%%" % (len(r), 100.0 * (nets > 0).mean(), nets.mean())


def main():
    print("NO-WICK MOMENTUM + WALL CONFLUENCE | long in BUY-wall radar / short in SELL-wall radar | TP0.2%%net | OOS | IN-SAMPLE\n", flush=True)
    print("  cell           BASELINE (all)  IS / OOS         |  WALL-FILTERED  IS / OOS", flush=True)
    for dsname, root in DATASETS:
        for tf in TFS:
            A, O, C, Hi, Lo, YR, n = load_arrays(root, tf)
            S_w, R_w = detect_walls(A)
            sig = signals(O, C, Hi, Lo, YR, n)
            filt = []
            S_i0 = [w[0] for w in S_w]; R_i0 = [w[0] for w in R_w]
            for (i, s, entry, ext, yr) in sig:
                walls = S_w if s > 0 else R_w
                idx = bisect.bisect_right(S_i0 if s > 0 else R_i0, i)
                hit = False
                for j in range(idx):
                    if walls[j][1] <= entry <= walls[j][2]:
                        hit = True; break
                if hit:
                    filt.append((i, s, entry, ext, yr))
            rb = evaluate(sig, Hi, Lo, C, n); rf = evaluate(filt, Hi, Lo, C, n)
            print("  %-6s %-4s  %s / %s  |  %s / %s"
                  % (dsname, tf, stats(rb, 2025), stats(rb, 2026), stats(rf, 2025), stats(rf, 2026)), flush=True)
    print("\n  (BASELINE = all no-wick signals; WALL-FILTERED = only those with entry inside a matching-side wall radar)", flush=True)


if __name__ == "__main__":
    main()
