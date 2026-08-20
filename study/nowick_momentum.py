"""NO-WICK MOMENTUM candle strategy (user 2026-08-20). Bullish candle with NO lower wick -> LONG; bearish candle with NO
upper wick -> SHORT. Entry = candle close (market). SL = 0.1% beyond the candle's stop-side extreme (below the low for a
long / above the high for a short). TP = 0.2% NET (gross 0.24% at maker 0.04%RT). 'No wick' = lower/upper wick <= WICK_TOL
of the candle range (0.05 primary = negligible; also report strict <=0.005). Higher-TF barrier, stop-first, taken()
non-overlap, OOS-split (2025 / 2026-H1). Clock + bucket, tf 15m/30m/1h/4h (NOT 1m/5m). IN-SAMPLE. python study/nowick_momentum.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app.engulf_sr_detect import _ohlc
FEE, TPNET, H, SLBUF = 0.0004, 0.0020, 200, 0.001   # maker 0.04%RT; net TP 0.2% -> gross 0.24%; SL 0.1% beyond candle
GTP = TPNET + FEE                                    # gross TP 0.24%
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
    return O, C, Hi, Lo, YR, n


def signals(O, C, Hi, Lo, YR, n, wick_tol):
    out = []
    for i in range(n):
        rng = Hi[i] - Lo[i]
        if rng <= 0:
            continue
        blo = min(O[i], C[i]); bhi = max(O[i], C[i])
        lw = (blo - Lo[i]) / rng; uw = (Hi[i] - bhi) / rng
        if C[i] > O[i] and lw <= wick_tol:                 # bullish, no lower wick -> LONG
            out.append((i, 1, C[i], Lo[i], int(YR[i])))
        elif C[i] < O[i] and uw <= wick_tol:               # bearish, no upper wick -> SHORT
            out.append((i, -1, C[i], Hi[i], int(YR[i])))
    return out


def evaluate(sigs, Hi, Lo, C, n):
    raw = []; last = -1
    for (i, s, entry, ext, yr) in sigs:
        if i <= last:
            continue
        sl = ext * (1 - SLBUF) if s > 0 else ext * (1 + SLBUF)   # 0.1% beyond the candle extreme
        dist = abs(entry - sl) / entry
        if dist <= 0:
            continue
        j0 = i + 1; j1 = min(n, i + 1 + H)
        outc, gross, off = sim(s, entry, entry * (1 + s * GTP), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE
        raw.append((net, dist, yr, s)); last = i + int(off)
    return raw


def cell_stats(raw, yr=None):
    r = [x for x in raw if (yr is None or x[2] == yr)]
    if not r:
        return None
    nets = np.array([x[0] for x in r]) * 100.0; dists = np.array([x[1] for x in r]) * 100.0
    w = nets[nets > 0]; l = nets[nets <= 0]
    return dict(n=len(r), win=100.0 * (nets > 0).mean(), aw=(w.mean() if len(w) else 0),
                al=(l.mean() if len(l) else 0), exp=nets.mean(), sl=dists.mean())


def main():
    print("NO-WICK MOMENTUM | bull no-lower-wick=LONG / bear no-upper-wick=SHORT | SL 0.1%% beyond candle | TP 0.2%% net (maker) | OOS | IN-SAMPLE\n", flush=True)
    for wick_tol in (0.05, 0.005):
        print("################  wick tolerance <= %.3f of range (%s)  ################" % (wick_tol, "negligible" if wick_tol >= 0.05 else "STRICT"), flush=True)
        print("  cell            n     IS(2025) win/exp/SL        OOS(2026) win/exp/SL", flush=True)
        for dsname, root in DATASETS:
            for tf in TFS:
                O, C, Hi, Lo, YR, n = load_arrays(root, tf)
                raw = evaluate(signals(O, C, Hi, Lo, YR, n, wick_tol), Hi, Lo, C, n)
                a = cell_stats(raw)
                if not a:
                    print("  %-6s %-5s   no signals" % (dsname, tf), flush=True); continue
                s25 = cell_stats(raw, 2025); s26 = cell_stats(raw, 2026)
                def fmt(x):
                    return ("n=%-4d %4.1f%% %+.3f%% SL%.2f%%" % (x["n"], x["win"], x["exp"], x["sl"])) if x else "n=0"
                print("  %-6s %-5s n=%-5d | %s | %s" % (dsname, tf, a["n"], fmt(s25), fmt(s26)), flush=True)
        print("", flush=True)


if __name__ == "__main__":
    main()
