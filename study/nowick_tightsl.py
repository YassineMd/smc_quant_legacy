"""NO-WICK momentum with a TIGHT FIXED 0.1% SL (not the whole-candle stop) + wider TP. Bull no-lower-wick=LONG / bear
no-upper-wick=SHORT, entry@close, SL 0.1% from entry, TP in {0.1%(1:1), 0.2, 0.3, 0.4, 0.5%}. Reports win% + NET
expectancy (maker 0.04%RT) + the break-even win rate + the RANDOM-barrier win baseline (so we see if the signal beats
random). Higher-TF barrier, stop-first, taken(). clock+bucket 15m/30m/1h. OOS-split. IN-SAMPLE. python study/nowick_tightsl.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app.engulf_sr_detect import _ohlc
FEE, H, SL, WICK_TOL = 0.0004, 200, 0.001, 0.05      # tight 0.1% stop
TPS = [0.001, 0.002, 0.003, 0.004, 0.005]            # 0.1%(1:1) / 0.2 / 0.3 / 0.4 / 0.5%
CELLS = [("clock", "study/clock_archive", "15m"), ("clock", "study/clock_archive", "30m"), ("clock", "study/clock_archive", "1h"),
         ("bucket", "study/recon_archive", "15m"), ("bucket", "study/recon_archive", "30m"), ("bucket", "study/recon_archive", "1h")]


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
            out.append((i, 1, C[i], int(YR[i])))
        elif C[i] < O[i] and (Hi[i] - bhi) / rng <= WICK_TOL:
            out.append((i, -1, C[i], int(YR[i])))
    return out


def sim(s, entry, tp_frac, ph, pl, pc):
    sl = entry * (1 - s * SL); tp = entry * (1 + s * tp_frac)
    for off in range(len(ph)):
        hi = ph[off]; lo = pl[off]
        if (lo <= sl) if s > 0 else (hi >= sl):
            return s * (sl - entry) / entry - FEE, off + 1
        if (hi >= tp) if s > 0 else (lo <= tp):
            return s * (tp - entry) / entry - FEE, off + 1
    return s * (pc[-1] - entry) / entry - FEE, len(ph)


def eval_tp(sigs, Hi, Lo, C, n, tp_frac):
    raw = []; last = -1
    for (i, s, entry, yr) in sigs:
        if i <= last:
            continue
        j0 = i + 1; j1 = min(n, i + 1 + H)
        if j0 >= n:
            continue
        net, off = sim(s, entry, tp_frac, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        raw.append((net, yr)); last = i + int(off)
    return raw


def stat(raw, yr):
    r = [x[0] for x in raw if x[1] == yr]
    if not r:
        return "n=0"
    a = np.array(r) * 100.0
    return "n=%-5d win%4.1f%% exp%+.4f%%" % (len(a), 100.0 * (a > 0).mean(), a.mean())


def main():
    print("NO-WICK + TIGHT 0.1%% SL | TP 0.1%%(1:1)/0.2/0.3/0.4/0.5 | net maker | random-baseline shown | OOS | IN-SAMPLE\n", flush=True)
    for dsname, root, tf in CELLS:
        O, C, Hi, Lo, YR, n = load_arrays(root, tf)
        sig = signals(O, C, Hi, Lo, YR, n)
        print("================ %s %s  (%d signals) ================" % (dsname, tf, len(sig)), flush=True)
        for tp in TPS:
            rr = tp / SL                                            # reward:risk
            be = 100.0 * (SL + FEE) / (SL + tp)                     # break-even win% (net, incl fee both sides ~)
            rand = 100.0 * SL / (SL + tp)                           # random-barrier win baseline
            raw = eval_tp(sig, Hi, Lo, C, n, tp)
            print("  TP %.1f%% (%.0f:1RR  BE~%.0f%%  rand~%.0f%%)  IS %s | OOS %s"
                  % (tp * 100, rr, be, rand, stat(raw, 2025), stat(raw, 2026)), flush=True)
        print("", flush=True)


if __name__ == "__main__":
    main()
