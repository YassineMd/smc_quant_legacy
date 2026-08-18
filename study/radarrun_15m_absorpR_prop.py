"""15m (or any tf) Radar Runner @ 0.2% TP + candle-SL, FILTERED to absorpR >= threshold -> win%/maxDD/prop-pass/n/day.
Shows take-all baseline vs the absorpR-gated set side by side. The absorpR filter is applied to the signal list BEFORE
the taken()-nonoverlap, so a dropped low-absorpR signal frees the timeline (as it would live). Same MC as the prop eval.
Usage: python study/radarrun_15m_absorpR_prop.py [tf] [absorpR_thr]
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_proptp_alltf_clock import detect, eval_tp, day_blocks, mc, TP, SLBUF
from study.radarrun_winrate_dd import maxdd_pct
from app import absorption as ABS


def report(name, sigs, Hi, Lo, C, n):
    tr = eval_tp(sigs, Hi, Lo, C, n, TP)
    if not tr:
        print("  %-16s no trades" % name); return
    net = np.array([t[1] for t in tr]); rs = [t[2] for t in tr]
    days = day_blocks(tr); spd = sum(len(d) for d in days) / max(1, len(days))
    ps = [mc(days, Rp)[0] for Rp in (0.5, 0.75, 1.0)]
    verdict = "PASS" if ps[0] >= 80 else ("marginal" if ps[0] >= 40 else "FAIL")
    print("  %-16s | n=%-4d | win %4.1f%% | maxDD %5.2f%% | %5.2f trd/day | pass %3.0f/%3.0f/%3.0f%%  [%s]"
          % (name, len(tr), 100 * (net > 0).mean(), maxdd_pct(rs), spd, ps[0], ps[1], ps[2], verdict), flush=True)


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "15m"
    thr = float(sys.argv[2]) if len(sys.argv) > 2 else -0.25
    A = sorted(load_archive(tf, root="study/clock_archive", drop_degenerate=False)[1],
               key=lambda b: _f(b.get("start_time", 0)))
    sigs, Hi, Lo, C, n = detect(A, SLBUF.get(tf, 0.003))
    aR = {}
    for sg in sigs:
        k = sg[0]
        try:
            a = ABS.absorption(A, k)[0]
        except Exception:
            a = None
        aR[k] = a
    filt = [sg for sg in sigs if aR.get(sg[0]) is not None and aR[sg[0]] >= thr]
    print("Radar Runner %s @ %.1f%% TP + candle-SL, clock candles (2025 + 2026-H1)\n" % (tf, TP * 100), flush=True)
    report("TAKE-ALL", sigs, Hi, Lo, C, n)
    report("absorpR >= %+.2f" % thr, filt, Hi, Lo, C, n)


if __name__ == "__main__":
    main()
