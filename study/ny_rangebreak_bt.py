"""NY Range-break backtest (app/ny_rangebreak_detect rules) on CLOCK 5m/15m/30m/1h/4h + BUCKET 15m/30m/1h.
Range = 13:00-16:00 UTC body edges (rhi/rlo) + wick extremes (whi/wlo). Break = FIRST candle 16:00-21:00 UTC (same
weekday) whose CLOSE pierces the range (close>rhi LONG / close<rlo SHORT). Entry@break close, SL 0.1%% beyond the range
wick, TP vol-adaptive (range%<2.85 -> 2x range, else 0.5x). Weekends excluded. Hold <=48h. Report n, win%%, exp%%, by
side, OOS-split. Fees 0.04%%RT+0.03%% slip. IN-SAMPLE. python study/ny_rangebreak_bt.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
FEE, SLIP, SL_PAD, TP_THR = 0.0004, 0.0003, 0.001, 2.85
R_HRS = {13, 14, 15}; B_HRS = {16, 17, 18, 19, 20}; MAXHOLD = 48 * 3600
CELLS = [("clock", "study/clock_archive", "5m"), ("clock", "study/clock_archive", "15m"),
         ("clock", "study/clock_archive", "30m"), ("clock", "study/clock_archive", "1h"),
         ("clock", "study/clock_archive", "4h"), ("bucket", "study/recon_archive", "15m"),
         ("bucket", "study/recon_archive", "30m"), ("bucket", "study/recon_archive", "1h")]


def load_arrays(root, tf):
    A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    O = np.zeros(n); C = np.zeros(n); Hi = np.zeros(n); Lo = np.zeros(n); ST = np.zeros(n)
    HR = np.zeros(n, dtype=int); DATE = [None] * n; WD = np.zeros(n, dtype=int)
    for i, b in enumerate(A):
        O[i] = _f(b.get("open", b.get("open_price"))); C[i] = _f(b.get("close", b.get("close_price")))
        Hi[i] = _f(b.get("high")); Lo[i] = _f(b.get("low")); ST[i] = _f(b.get("start_time"))
        dt = datetime.fromtimestamp(ST[i], tz=timezone.utc); HR[i] = dt.hour; DATE[i] = dt.date(); WD[i] = dt.weekday()
    return O, C, Hi, Lo, ST, HR, DATE, WD, n


def backtest(root, tf):
    O, C, Hi, Lo, ST, HR, DATE, WD, n = load_arrays(root, tf)
    # index by date
    bydate = {}
    for i in range(n):
        bydate.setdefault(DATE[i], []).append(i)
    trades = []
    for d, idxs in bydate.items():
        if WD[idxs[0]] >= 5:                                   # Sat/Sun excluded
            continue
        rng_idx = [i for i in idxs if HR[i] in R_HRS]
        brk_idx = sorted([i for i in idxs if HR[i] in B_HRS])
        if not rng_idx or not brk_idx:
            continue
        rhi = max(max(O[i], C[i]) for i in rng_idx); rlo = min(min(O[i], C[i]) for i in rng_idx)
        whi = max(Hi[i] for i in rng_idx); wlo = min(Lo[i] for i in rng_idx)
        rng = whi - wlo
        if rng <= 0 or rhi <= 0:
            continue
        k = None; s = 0
        for i in brk_idx:
            if C[i] > rhi:
                k, s = i, 1; break
            if C[i] < rlo:
                k, s = i, -1; break
        if k is None:
            continue
        entry = C[k]; rngpct = 100.0 * rng / entry
        sl = wlo * (1 - SL_PAD) if s > 0 else whi * (1 + SL_PAD)
        tpmult = 2.0 if rngpct < TP_THR else 0.5
        tp = entry + s * tpmult * rng
        # sim forward (<=48h, stop-first)
        outc = "end"; gross = 0.0; et = ST[k]
        for j in range(k + 1, n):
            if ST[j] > et + MAXHOLD:
                gross = s * (C[j - 1] - entry) / entry; break
            if (Lo[j] <= sl) if s > 0 else (Hi[j] >= sl):
                outc = "sl"; gross = s * (sl - entry) / entry; break
            if (Hi[j] >= tp) if s > 0 else (Lo[j] <= tp):
                outc = "tp"; gross = s * (tp - entry) / entry; break
        else:
            gross = s * (C[-1] - entry) / entry if n else 0.0
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        yr = datetime.fromtimestamp(et, tz=timezone.utc).year
        trades.append((net, s, rngpct, yr, outc))
    return trades


def stat(tr, yr=None, side=None):
    r = [t for t in tr if (yr is None or t[3] == yr) and (side is None or t[1] == side)]
    if not r:
        return "n=0"
    a = np.array([t[0] for t in r]) * 100.0
    return "n=%-4d win%4.1f%% exp%+.3f%%" % (len(a), 100.0 * (a > 0).mean(), a.mean())


def main():
    print("NY RANGE-BREAK backtest | 13-16 UTC range, break 16-21 UTC | SL 0.1%% past wick, vol-adaptive TP | OOS | IN-SAMPLE\n", flush=True)
    for dsname, root, tf in CELLS:
        tr = backtest(root, tf)
        if not tr:
            print("  %-6s %-4s  no trades (window not aligned to bars)" % (dsname, tf), flush=True); continue
        avgrng = np.mean([t[2] for t in tr])
        print("  %-6s %-4s  ALL %s (avgRange %.2f%%)" % (dsname, tf, stat(tr), avgrng), flush=True)
        print("           SHORT (the edge):  IS %s | OOS %s     [LONG all: %s]"
              % (stat(tr, 2025, -1), stat(tr, 2026, -1), stat(tr, side=1)), flush=True)
    print("", flush=True)


if __name__ == "__main__":
    main()
