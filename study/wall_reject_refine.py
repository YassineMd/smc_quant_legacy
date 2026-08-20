"""Refine the fresh-wall REJECTION entry: require a CLEAR rejection WICK (not just 'closed in the half'), K=3, and report
win% + net at BOTH taker (~0.07%RT) and maker/low (~0.03%RT) fees, across TPs, with a FIXED 0.2% SL and a candle-
anchored SL variant. Isolates whether a proper rejection candle + cheaper fills tips the (validated) signal positive.
15m/30m/1h clock candles (fast; add 5m via arg). taken() non-overlap. python study/wall_reject_refine.py [tf ...]"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL
from app.engulf_sr_detect import _ohlc

SL_FRAC = 0.002
TPS = [0.002, 0.003, 0.004, 0.005]
H = 200
K = 3
WICK_MIN = 0.40        # rejection wick >= this fraction of the candle range
CLOSE_FAR = 0.40       # close within this fraction of the range from the DEFENSE end (far from the tested extreme)
FEES = {"taker~0.07%": 0.0007, "maker~0.03%": 0.0003}


def sim(s, entry, tp, sl, Hi, Lo, C):
    for off in range(len(Hi)):
        if (Lo[off] <= sl) if s > 0 else (Hi[off] >= sl):
            return "sl", s * (sl - entry) / entry, off + 1
        if (Hi[off] >= tp) if s > 0 else (Lo[off] <= tp):
            return "tp", s * (tp - entry) / entry, off + 1
    return "end", (s * (C[-1] - entry) / entry if len(C) else 0.0), len(Hi)


def load_tf(tf):
    A = sorted(load_archive(tf, root="study/clock_archive", drop_degenerate=False)[1],
               key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    O = np.zeros(n); Hi = np.zeros(n); Lo = np.zeros(n); C = np.zeros(n)
    for i, b in enumerate(A):
        O[i], C[i], Hi[i], Lo[i] = _ohlc(b)
    try:
        walls = AL.detect(A, skip_last=False)
    except Exception:
        walls = []
    return O, Hi, Lo, C, n, walls


def strong_reject_sigs(walls, O, Hi, Lo, C, n):
    out = []
    for w in walls:
        i0 = int(w.get("i0", -1)); side = w.get("side")
        P = float(w.get("price") or 0.0); band = float(w.get("band") or 0.0)
        if i0 < 0 or side not in ("S", "R") or P <= 0 or band <= 0:
            continue
        s = 1 if side == "S" else -1
        for j in range(i0 + 1, min(i0 + 1 + K, n)):
            rng = Hi[j] - Lo[j]
            if rng <= 0:
                continue
            body_hi = max(O[j], C[j]); body_lo = min(O[j], C[j])
            if side == "R":                                        # test the resistance, reject DOWN
                tested = (P - band) <= Hi[j] <= (P + band)
                wick = (Hi[j] - body_hi) / rng                     # upper rejection wick
                closepos = (C[j] - Lo[j]) / rng                    # 0=at low
                strong = wick >= WICK_MIN and closepos <= CLOSE_FAR
            else:                                                  # test the support, reject UP
                tested = (P - band) <= Lo[j] <= (P + band)
                wick = (body_lo - Lo[j]) / rng
                closepos = (C[j] - Lo[j]) / rng                    # 1=at high
                strong = wick >= WICK_MIN and closepos >= (1.0 - CLOSE_FAR)
            if tested and strong and C[j] > 0:
                out.append((j, s, float(C[j]), float(Hi[j]), float(Lo[j]))); break
    out.sort(); return out


def eval_raw(sigs, Hi, Lo, C, n, tp_frac, candle_sl):
    raw = []; last = -1
    for (i, s, entry, chi, clo) in sigs:
        if i <= last:
            continue
        if candle_sl:                                              # SL beyond the rejection candle's extreme + 0.05% buf
            sl = clo * (1 - 0.0005) if s > 0 else chi * (1 + 0.0005)
        else:
            sl = entry * (1 - s * SL_FRAC)
        dist = abs(entry - sl) / entry
        if dist <= 0:
            continue
        tp = entry * (1 + s * tp_frac)
        j0 = i + 1; j1 = min(n, i + 1 + H)
        outc, gross, off = sim(s, entry, tp, sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        raw.append((gross, outc)); last = i + int(off)
    return raw


def line(tag, raw):
    for fname, fee in FEES.items():
        net = np.array([g - fee - (fee if o != "tp" else 0.0) for g, o in raw])
        if len(net):
            print("       %-12s win %4.1f%%  avgNet %+.3f%%  totNet %+.0f%%"
                  % (fname, 100 * (net > 0).mean(), net.mean() * 100, net.sum() * 100), flush=True)


def main():
    tfs = sys.argv[1:] or ["15m", "30m", "1h"]
    print("STRONG-reject (clear wick, K=3) | fixed 0.2%% SL vs candle SL | taker vs maker fee | taken()\n", flush=True)
    for tf in tfs:
        O, Hi, Lo, C, n, walls = load_tf(tf)
        sigs = strong_reject_sigs(walls, O, Hi, Lo, C, n)
        print("================ TF = %s  (%d walls -> %d strong-reject sigs) ================" % (tf, len(walls), len(sigs)), flush=True)
        for candle_sl in (False, True):
            sl_tag = "candle-SL" if candle_sl else "0.2%-SL"
            for tp in TPS:
                raw = eval_raw(sigs, Hi, Lo, C, n, tp, candle_sl)
                print("  [%s  TP %.2f%%]  n=%d" % (sl_tag, tp * 100, len(raw)), flush=True)
                line("", raw)
            print("", flush=True)


if __name__ == "__main__":
    main()
