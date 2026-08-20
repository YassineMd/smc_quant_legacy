"""OOS gate for the strong-reject + candle-SL wall entry: is it a real edge or a mined artifact? Split every trade by
YEAR (2025 = in-sample where the config was chosen; 2026-H1 = untouched OOS) and report win% + net (taker & maker) per
year. If 2026 collapses -> mined. Config fixed: strong rejection WICK (K=3), SL below the rejection candle +0.05%.
15m/30m clock candles. python study/wall_reject_oos.py [tf ...]"""
import os, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL
from app.engulf_sr_detect import _ohlc

H = 200; K = 3; WICK_MIN = 0.40; CLOSE_FAR = 0.40
TPS = [0.003, 0.004, 0.005]
FEES = {"taker": 0.0007, "maker": 0.0003}


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
    O = np.zeros(n); Hi = np.zeros(n); Lo = np.zeros(n); C = np.zeros(n); YR = np.zeros(n, dtype=int)
    for i, b in enumerate(A):
        O[i], C[i], Hi[i], Lo[i] = _ohlc(b)
        YR[i] = datetime.fromtimestamp(_f(b.get("start_time", 0)), tz=timezone.utc).year
    try:
        walls = AL.detect(A, skip_last=False)
    except Exception:
        walls = []
    return O, Hi, Lo, C, YR, n, walls


def strong_sigs(walls, O, Hi, Lo, C, YR, n):
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
            bh = max(O[j], C[j]); bl = min(O[j], C[j]); cp = (C[j] - Lo[j]) / rng
            if side == "R":
                ok = (P - band) <= Hi[j] <= (P + band) and (Hi[j] - bh) / rng >= WICK_MIN and cp <= CLOSE_FAR
            else:
                ok = (P - band) <= Lo[j] <= (P + band) and (bl - Lo[j]) / rng >= WICK_MIN and cp >= (1.0 - CLOSE_FAR)
            if ok and C[j] > 0:
                out.append((j, s, float(C[j]), float(Hi[j]), float(Lo[j]), int(YR[j]))); break
    out.sort(); return out


def eval_raw(sigs, Hi, Lo, C, n, tp_frac):
    raw = []; last = -1
    for (i, s, entry, chi, clo, yr) in sigs:
        if i <= last:
            continue
        sl = clo * (1 - 0.0005) if s > 0 else chi * (1 + 0.0005)
        if abs(entry - sl) / entry <= 0:
            continue
        tp = entry * (1 + s * tp_frac)
        j0 = i + 1; j1 = min(n, i + 1 + H)
        outc, gross, off = sim(s, entry, tp, sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        raw.append((gross, outc, yr)); last = i + int(off)
    return raw


def main():
    tfs = sys.argv[1:] or ["15m", "30m"]
    print("OOS split (2025 in-sample / 2026 OOS) | strong reject wick + candle SL, K=3 | taken()\n", flush=True)
    for tf in tfs:
        O, Hi, Lo, C, YR, n, walls = load_tf(tf)
        sigs = strong_sigs(walls, O, Hi, Lo, C, YR, n)
        print("================ TF = %s  (%d strong-reject sigs) ================" % (tf, len(sigs)), flush=True)
        for tp in TPS:
            raw = eval_raw(sigs, Hi, Lo, C, n, tp)
            print("  TP %.2f%%  (n=%d)" % (tp * 100, len(raw)), flush=True)
            for yr in (2025, 2026):
                yraw = [(g, o) for g, o, y in raw if y == yr]
                if not yraw:
                    continue
                cells = []
                for fname, fee in FEES.items():
                    net = np.array([g - fee - (fee if o != "tp" else 0.0) for g, o in yraw])
                    cells.append("%s win %4.1f%% net %+.3f%%" % (fname, 100 * (net > 0).mean(), net.mean() * 100))
                print("     %d  n=%-4d | %s" % (yr, len(yraw), "  |  ".join(cells)), flush=True)
        print("", flush=True)


if __name__ == "__main__":
    main()
