"""FRESH-WALL REJECTION entry on CLOCK candles (user hypothesis).

Distinct from the null enter-at-formation and from the radar-visit->break Radar Runner: after a wall forms at i0, watch
the next K candles; the FIRST candle whose extreme TESTS the wall zone [P-band, P+band] AND closes in the DEFENSE half
(rejected) is the entry — Support(S)->LONG / Resistance(R)->SHORT at that candle's close. Catches the near rejection
the formal radar-run misses. SL 0.2%, TP {0.2/0.3/0.5}%. Compares vs BASELINE (enter@formation = the null) so the
rejection filter's lift is isolated. taken() non-overlap, fees, SL-first tie. Detect walls ONCE per tf.
python study/wall_reject_clock.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL
from app.engulf_sr_detect import _ohlc

FEE, SLIP = 0.0004, 0.0003
SL_FRAC = 0.002
TPS = [0.002, 0.003, 0.005]
H = 200
TFS = ["15m", "30m", "1h", "5m"]        # user trades 15m/30m; 5m last (slow wall detect)


def sim(s, entry, tp, sl, Hi, Lo, C):
    for off in range(len(Hi)):
        hi, lo = Hi[off], Lo[off]
        if (lo <= sl) if s > 0 else (hi >= sl):
            return "sl", s * (sl - entry) / entry, off + 1
        if (hi >= tp) if s > 0 else (lo <= tp):
            return "tp", s * (tp - entry) / entry, off + 1
    return "end", (s * (C[-1] - entry) / entry if len(C) else 0.0), len(Hi)


def load_tf(tf):
    A = sorted(load_archive(tf, root="study/clock_archive", drop_degenerate=False)[1],
               key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    O = np.zeros(n); Hi = np.zeros(n); Lo = np.zeros(n); C = np.zeros(n)
    for i, b in enumerate(A):
        O[i], C[i], Hi[i], Lo[i] = _ohlc(b)   # _ohlc -> (open, close, high, low)
    try:
        walls = AL.detect(A, skip_last=False)
    except Exception:
        walls = []
    return O, Hi, Lo, C, n, walls


def baseline_sigs(walls, C, n):
    out = []
    for w in walls:
        i0 = int(w.get("i0", -1)); side = w.get("side")
        if 0 <= i0 < n - 1 and side in ("S", "R") and C[i0] > 0:
            out.append((i0, 1 if side == "S" else -1, float(C[i0])))
    out.sort(); return out


def reject_sigs(walls, O, Hi, Lo, C, n, K):
    out = []
    for w in walls:
        i0 = int(w.get("i0", -1)); side = w.get("side")
        P = float(w.get("price") or 0.0); band = float(w.get("band") or 0.0)
        if i0 < 0 or side not in ("S", "R") or P <= 0 or band <= 0:
            continue
        s = 1 if side == "S" else -1
        for j in range(i0 + 1, min(i0 + 1 + K, n)):
            mid = (Hi[j] + Lo[j]) / 2.0
            if side == "R":                                    # resistance: high tested the zone, closed in lower half
                tested = (P - band) <= Hi[j] <= (P + band)
                rejected = C[j] < mid
            else:                                              # support: low tested the zone, closed in upper half
                tested = (P - band) <= Lo[j] <= (P + band)
                rejected = C[j] > mid
            if tested and rejected and C[j] > 0:
                out.append((j, s, float(C[j]))); break
    out.sort(); return out


def eval_tp(sigs, Hi, Lo, C, n, tp_frac):
    tr = []; last = -1
    for (i, s, entry) in sigs:
        if i <= last:
            continue
        sl = entry * (1 - s * SL_FRAC); tp = entry * (1 + s * tp_frac)
        j0 = i + 1; j1 = min(n, i + 1 + H)
        outc, gross, off = sim(s, entry, tp, sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        tr.append(gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)); last = i + int(off)
    return tr


def report(label, sigs, Hi, Lo, C, n):
    print("  %-26s (raw n=%d)" % (label, len(sigs)), flush=True)
    for tp in TPS:
        tr = np.array(eval_tp(sigs, Hi, Lo, C, n, tp))
        if len(tr):
            print("     TP %.2f%%: n=%-5d win %4.1f%%  avgNet %+.3f%%  totNet %+.0f%%"
                  % (tp * 100, len(tr), 100 * (tr > 0).mean(), tr.mean() * 100, tr.sum() * 100), flush=True)
    print("", flush=True)


def main():
    print("FRESH-WALL REJECTION entry on CLOCK candles | SL 0.2%% | fee 0.04%%RT+0.03%%slip | taken()\n", flush=True)
    for tf in TFS:
        O, Hi, Lo, C, n, walls = load_tf(tf)
        print("================ TF = %s  (%d walls) ================" % (tf, len(walls)), flush=True)
        report("BASELINE enter@formation", baseline_sigs(walls, C, n), Hi, Lo, C, n)
        for K in (3, 5, 8):
            report("REJECT within %d bars" % K, reject_sigs(walls, O, Hi, Lo, C, n, K), Hi, Lo, C, n)


if __name__ == "__main__":
    main()
