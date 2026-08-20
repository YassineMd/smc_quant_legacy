"""Exit sweep on the NO-WICK MOMENTUM signal (bull no-lower-wick=LONG / bear no-upper-wick=SHORT, entry@close, candle stop
= 0.1% beyond the candle extreme = the RISK UNIT R). The flat 0.2% TP failed (SL too wide). Try exits that SCALE to the
risk: single TP at 0.5R/1R/1.5R/2R; a Radar-Runner-style SCALE-OUT (50% at 1R + 50% at 2R, stop->BE after 1R); and a
TRAILING runner (activate at 1R, trail by 1xR). If EVERY exit is ~-fees, the signal has no edge. Higher-TF barrier,
stop-first, taken(). Cells: clock+bucket 15m/30m. OOS-split. IN-SAMPLE. python study/nowick_exits.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app.engulf_sr_detect import _ohlc
FEE, H, SLBUF, WICK_TOL = 0.0004, 200, 0.001, 0.05
CELLS = [("clock", "study/clock_archive", "15m"), ("clock", "study/clock_archive", "30m"),
         ("bucket", "study/recon_archive", "15m"), ("bucket", "study/recon_archive", "30m")]


def load_arrays(root, tf):
    A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A); O = np.zeros(n); C = np.zeros(n); Hi = np.zeros(n); Lo = np.zeros(n); YR = np.zeros(n, dtype=int)
    for i, b in enumerate(A):
        O[i], C[i], Hi[i], Lo[i] = _ohlc(b)
        t = _f(b.get("start_time", 0)); YR[i] = datetime.fromtimestamp(t, tz=timezone.utc).year if t else 0
    return O, C, Hi, Lo, YR, n


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


def sim_R(s, entry, d, R, ph, pl, pc):
    """single TP at R*d beyond entry, SL at d (the candle stop). returns (net, off)."""
    sl = entry * (1 - s * d); tp = entry * (1 + s * R * d)
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if s > 0 else (hi >= sl):
            return s * (sl - entry) / entry - FEE, off + 1
        if (hi >= tp) if s > 0 else (lo <= tp):
            return s * (tp - entry) / entry - FEE, off + 1
    return s * (pc[-1] - entry) / entry - FEE, len(ph)


def sim_scaleout(s, entry, d, ph, pl, pc):
    """50% at 1R + 50% at 2R, stop->BE after 1R. returns (net, off)."""
    sl = entry * (1 - s * d); tp1 = entry * (1 + s * d); tp2 = entry * (1 + s * 2 * d)
    hit1 = False; slp = sl; m = len(ph)
    for off in range(m):
        hi = ph[off]; lo = pl[off]
        if not hit1:
            if (lo <= sl) if s > 0 else (hi >= sl):
                return s * (sl - entry) / entry - FEE, off + 1
            if (hi >= tp1) if s > 0 else (lo <= tp1):
                hit1 = True; slp = entry
                if (hi >= tp2) if s > 0 else (lo <= tp2):
                    return 0.5 * (s * (tp1 - entry) / entry) + 0.5 * (s * (tp2 - entry) / entry) - FEE, off + 1
        else:
            if (lo <= slp) if s > 0 else (hi >= slp):
                return 0.5 * (s * (tp1 - entry) / entry) + 0.5 * (s * (slp - entry) / entry) - FEE, off + 1
            if (hi >= tp2) if s > 0 else (lo <= tp2):
                return 0.5 * (s * (tp1 - entry) / entry) + 0.5 * (s * (tp2 - entry) / entry) - FEE, off + 1
    netB = s * (pc[-1] - entry) / entry if hit1 else s * (pc[-1] - entry) / entry
    return (0.5 * (s * (tp1 - entry) / entry) + 0.5 * netB - FEE) if hit1 else (netB - FEE), m


def sim_trail(s, entry, d, ph, pl, pc):
    """activate at 1R then trail the stop by 1*R below the running favorable extreme. returns (net, off)."""
    stop = entry * (1 - s * d); ref = entry; act = False
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= stop) if s > 0 else (hi >= stop):
            return s * (stop - entry) / entry - FEE, off + 1
        if s > 0:
            ref = max(ref, hi)
            if (ref - entry) / entry >= d:
                act = True
            if act:
                stop = max(stop, ref * (1 - d))
        else:
            ref = min(ref, lo)
            if (entry - ref) / entry >= d:
                act = True
            if act:
                stop = min(stop, ref * (1 + d))
    return s * (pc[-1] - entry) / entry - FEE, len(ph)


def run_exit(sigs, Hi, Lo, C, n, fn):
    raw = []; last = -1
    for (i, s, entry, ext, yr) in sigs:
        if i <= last:
            continue
        d = abs(entry - (ext * (1 - SLBUF) if s > 0 else ext * (1 + SLBUF))) / entry
        j0 = i + 1; j1 = min(n, i + 1 + H)
        if d <= 0 or j0 >= n:
            continue
        net, off = fn(s, entry, d, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        raw.append((net, yr)); last = i + int(off)
    return raw


def exp_str(raw, yr):
    r = [x[0] for x in raw if x[1] == yr]
    if not r:
        return "n=0"
    a = np.array(r) * 100.0
    return "%4.1f%% %+.4f%%" % (100.0 * (a > 0).mean(), a.mean())


def main():
    print("NO-WICK exits (candle stop = R unit) | 0.2%% flat TP FAILED -> scale TP to risk | clock+bucket 15m/30m | OOS | IN-SAMPLE\n", flush=True)
    exits = [("TP 0.5R", lambda s, e, d, a, b, c: sim_R(s, e, d, 0.5, a, b, c)),
             ("TP 1.0R", lambda s, e, d, a, b, c: sim_R(s, e, d, 1.0, a, b, c)),
             ("TP 1.5R", lambda s, e, d, a, b, c: sim_R(s, e, d, 1.5, a, b, c)),
             ("TP 2.0R", lambda s, e, d, a, b, c: sim_R(s, e, d, 2.0, a, b, c)),
             ("scale 1R+2R BE", sim_scaleout),
             ("trail 1R", sim_trail)]
    for dsname, root, tf in CELLS:
        O, C, Hi, Lo, YR, n = load_arrays(root, tf)
        sig = signals(O, C, Hi, Lo, YR, n)
        print("================ %s %s  (%d signals) ================  win / exp  (IS 2025  |  OOS 2026)" % (dsname, tf, len(sig)), flush=True)
        for name, fn in exits:
            raw = run_exit(sig, Hi, Lo, C, n, fn)
            print("  %-16s  IS %s  |  OOS %s" % (name, exp_str(raw, 2025), exp_str(raw, 2026)), flush=True)
        print("", flush=True)


if __name__ == "__main__":
    main()
