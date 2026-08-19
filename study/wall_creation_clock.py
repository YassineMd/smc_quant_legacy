"""Wall-CREATION strategy on CLOCK candles (5m/15m/30m/1h/4h, NOT 1m).

Enter at the formation bar of an order-flow wall in the DEFENSE (bounce) direction — Support(S)->LONG, Resistance(R)->
SHORT — at the formation candle's close; SL a fixed 0.2%; TP in {0.2,0.25,0.3,0.4,0.5}%. Wall = app.absorption_level_
detect.detect (creation bar i0 is CAUSAL — the candle's own footprint/body; strength is post-hoc and NOT used to gate).
taken() non-overlap, fee 0.04%RT + 0.03% slip, SL-first on a same-bar tie (conservative). Both recon years pooled.
Also prints the REVERSE (break) direction for context. DESCRIPTIVE only. python study/wall_creation_clock.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

FEE, SLIP = 0.0004, 0.0003
SL_FRAC = 0.002
TPS = [0.002, 0.0025, 0.003, 0.004, 0.005]
H = 200
TFS = ["5m", "15m", "30m", "1h", "4h"]


def sim(s, entry, tp, sl, Hi, Lo, C):
    for off in range(len(Hi)):
        hi, lo = Hi[off], Lo[off]
        if (lo <= sl) if s > 0 else (hi >= sl):          # SL first on a same-bar tie (adverse-first, conservative)
            return "sl", s * (sl - entry) / entry, off + 1
        if (hi >= tp) if s > 0 else (lo <= tp):
            return "tp", s * (tp - entry) / entry, off + 1
    return "end", (s * (C[-1] - entry) / entry if len(C) else 0.0), len(Hi)


def wall_signals(A, reverse=False):
    n = len(A)
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    try:
        walls = AL.detect(A, skip_last=False)
    except Exception:
        walls = []
    sigs = []
    for w in walls:
        i0 = int(w.get("i0", -1)); side = w.get("side")
        if i0 < 0 or i0 + 1 >= n or side not in ("S", "R"):
            continue
        s = (1 if side == "S" else -1)
        if reverse:
            s = -s
        entry = float(C[i0])
        if entry > 0:
            sigs.append((i0, s, entry))
    sigs.sort()
    return sigs, Hi, Lo, C, n


def eval_tp(sigs, Hi, Lo, C, n, tp_frac):
    tr = []; last = -1
    for (i0, s, entry) in sigs:
        if i0 <= last:                                    # taken(): non-overlap
            continue
        sl = entry * (1 - s * SL_FRAC); tp = entry * (1 + s * tp_frac)
        j0 = i0 + 1; j1 = min(n, i0 + 1 + H)
        outc, gross, off = sim(s, entry, tp, sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        tr.append(net); last = i0 + int(off)
    return tr


def _report(label, tf, sg):
    print("  %s  (%d walls)" % (label, len(sg[0])), flush=True)
    print("     TP    | n     | win%% | avgNet%% | totNet%%", flush=True)
    for tp in TPS:
        tr = np.array(eval_tp(*sg, tp))
        if len(tr):
            print("     %.2f%% | %-5d | %4.1f | %+.3f  | %+.0f"
                  % (tp * 100, len(tr), 100 * (tr > 0).mean(), tr.mean() * 100, tr.sum() * 100), flush=True)
    print("", flush=True)


def main():
    print("WALL-CREATION entry on CLOCK candles | SL 0.2% | fee 0.04%%RT + 0.03%% slip | taken() non-overlap | 2025+2026H1\n", flush=True)
    for tf in TFS:
        A = sorted(load_archive(tf, root="study/clock_archive", drop_degenerate=False)[1],
                   key=lambda b: _f(b.get("start_time", 0)))
        print("================  TF = %s  ================" % tf, flush=True)
        if not A:
            print("  no data\n", flush=True); continue
        _report("DEFENSE (S->LONG / R->SHORT)  [the ask]", tf, wall_signals(A, reverse=False))
        _report("REVERSE (S->SHORT / R->LONG)  [context]", tf, wall_signals(A, reverse=True))


if __name__ == "__main__":
    main()
