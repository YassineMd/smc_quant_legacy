"""NY OPENING-range breakout (clock 15m). Range = the FIRST 2 candles of the NY session (13:00 + 13:15 UTC) BODY edges
(rhi/rlo = max/min of open|close; whi/wlo = wick extremes for the stop). Break = the first candle AFTER the range whose
CLOSE pierces it (close>rhi = LONG / close<rlo = SHORT), within a break window (by 16:00 / 18:00 / 21:00 UTC tested).
Same exits as the NY breakout: SL 0.1% past the range wick, vol-adaptive TP (2x/0.5x range @ 2.85% range%%). 2-day hold,
stop-first pessimistic, weekdays. BOTH sides (no prior — opening ORB may differ). exp = per-unit net %%; avgR = net /
stop-distance; prop-MC = HyroTrader $200k R0.4. IS(2025)/OOS(2026). python study/ny_opening_breakout_15m.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.ny_rangebreak_poc_prop import mc, day_blocks
FEE, SLIP, SL_PAD, TP_THR, TP_LOW, TP_HIGH = 0.0004, 0.0003, 0.001, 2.85, 2.0, 0.5
NY_OPEN = 13; MAXHOLD = 48 * 3600
ROOT, TF = "study/clock_archive", "15m"


def load():
    A = sorted(load_archive(TF, root=ROOT, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A); O = np.zeros(n); C = np.zeros(n); Hi = np.zeros(n); Lo = np.zeros(n); ST = np.zeros(n)
    HR = np.zeros(n, dtype=int); DATE = [None] * n; WD = np.zeros(n, dtype=int)
    for i, b in enumerate(A):
        O[i] = _f(b.get("open", b.get("open_price"))); C[i] = _f(b.get("close", b.get("close_price")))
        Hi[i] = _f(b.get("high")); Lo[i] = _f(b.get("low")); ST[i] = _f(b.get("start_time"))
        dt = datetime.fromtimestamp(ST[i], tz=timezone.utc); HR[i] = dt.hour; DATE[i] = dt.date(); WD[i] = dt.weekday()
    return O, C, Hi, Lo, ST, HR, DATE, WD, n


def _net(side, entry, exitp, is_tp):
    g = side * (exitp - entry) / entry
    return g - FEE - SLIP - (0.0 if is_tp else SLIP)


def run(D, brk_end):
    O, C, Hi, Lo, ST, HR, DATE, WD, n = D
    bydate = {}
    for i in range(n):
        bydate.setdefault(DATE[i], []).append(i)
    trS = []; trL = []; nday = 0
    for d, idxs in bydate.items():
        if WD[idxs[0]] >= 5:
            continue
        sess = [i for i in idxs if HR[i] >= NY_OPEN]                # NY session candles (13:00 UTC onward)
        if len(sess) < 3:
            continue
        r2 = sess[:2]                                              # first 2 candles = the opening range (13:00 + 13:15)
        rhi = max(max(O[i], C[i]) for i in r2); rlo = min(min(O[i], C[i]) for i in r2)
        whi = max(Hi[i] for i in r2); wlo = min(Lo[i] for i in r2); rng = whi - wlo
        if rng <= 0:
            continue
        nday += 1
        k = None; side = 0
        for j in sess[2:]:                                         # first close beyond the opening range, within window
            if HR[j] >= brk_end:
                break
            if C[j] > rhi:
                k = j; side = 1; break
            if C[j] < rlo:
                k = j; side = -1; break
        if k is None:
            continue
        entry = C[k]; sl = wlo * (1 - SL_PAD) if side > 0 else whi * (1 + SL_PAD)
        lowvol = (rng / entry * 100.0) < TP_THR; mult = TP_LOW if lowvol else TP_HIGH
        tp = entry + side * mult * rng
        if (tp <= entry or sl >= entry) if side > 0 else (tp >= entry or sl <= entry):
            continue
        seq = [j for j in range(k + 1, n) if ST[j] <= ST[k] + MAXHOLD]
        net = None
        for j in seq:
            adverse = (Lo[j] <= sl) if side > 0 else (Hi[j] >= sl)
            favor = (Hi[j] >= tp) if side > 0 else (Lo[j] <= tp)
            if adverse:
                net = _net(side, entry, sl, False); break
            if favor:
                net = _net(side, entry, tp, True); break
        if net is None:
            net = _net(side, entry, C[seq[-1]], False) if seq else _net(side, entry, entry, False)
        sld = abs(sl - entry) / entry
        rmult = net / sld if sld > 0 else 0.0
        (trL if side > 0 else trS).append((ST[k], net, rmult, HR[k]))
    return trS, trL, nday


def cell(tr, yr=None):
    r = [t for t in tr if (yr is None or datetime.fromtimestamp(t[0], tz=timezone.utc).year == yr)]
    if not r:
        return "n=0                     "
    a = np.array([t[1] for t in r]) * 100.0; rm = np.array([t[2] for t in r])
    return "n=%-3d win%4.1f%% exp%+.3f%% avgR%+.3f" % (len(a), 100.0 * (a > 0).mean(), a.mean(), rm.mean())


def line(nm, tr, nday):
    m = mc(day_blocks([(t[0], t[2]) for t in tr])[0]) if tr else dict(p=0, med=0, dd99=0, worst=0)
    bh = ("brkH~%.0f" % np.median([t[3] for t in tr])) if tr else "brkH -"
    cov = 100.0 * len(tr) / max(1, nday)
    print("  %-20s cov%3.0f%% %s | ALL %s | IS %s | OOS %s | R0.4 pass%5.1f%% DDp99%4.1f%% worst%4.1f%%"
          % (nm, cov, bh, cell(tr), cell(tr, 2025), cell(tr, 2026), m["p"], m["dd99"], m["worst"]), flush=True)


def main():
    print("NY OPENING-range breakout (clock 15m) | range = first 2 candles (13:00+13:15 UTC) | SL/TP as NY break | 2-day hold | IN-SAMPLE", flush=True)
    print("cov = breaks / weekdays; brkH = median break hour; exp per-unit net %%; avgR = net/stop; prop-MC HyroTrader $200k R0.4.\n", flush=True)
    D = load()
    for be in (16, 18, 21):
        trS, trL, nday = run(D, be)
        print("==== break window 13:30 - %02d:00 UTC ====" % be, flush=True)
        line("SHORT (close<rlo)", trS, nday)
        line("LONG  (close>rhi)", trL, nday)


if __name__ == "__main__":
    main()
