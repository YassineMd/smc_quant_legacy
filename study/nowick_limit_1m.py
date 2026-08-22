"""NO-WICK candle retest — DEFINITIVE 1m-RESOLVED execution (removes the same-bar fill/TP ambiguity). Signals on the
coarse TF (no-wick directional candle), but the limit fill + TP/SL walk run on 1m bars, so the intrabar order is real.
Bullish no-bottom-wick -> limit long @open; bearish no-top-wick -> limit short @open. SL 1%%, TP 0.5%%. maker entry/TP.
Fill within 12·tf minutes; hold 48·tf minutes; non-overlap. clock {15m,30m,1h} on 1m-clock exec; bucket {15m,1h} on
recon-1m exec. IS/OOS, prop-MC. python study/nowick_limit_1m.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.ny_rangebreak_poc_prop import mc, day_blocks
FEE, SLIP, SL_FR, TP_FR, WICK_TOL = 0.0004, 0.0003, 0.01, 0.005, 0.05
TFMIN = {"15m": 15, "30m": 30, "1h": 60}


def load_ohlc(root, tf):
    A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A); O = np.zeros(n); C = np.zeros(n); Hi = np.zeros(n); Lo = np.zeros(n); ST = np.zeros(n); ET = np.zeros(n)
    for i, b in enumerate(A):
        O[i] = _f(b.get("open", b.get("open_price"))); C[i] = _f(b.get("close", b.get("close_price")))
        Hi[i] = _f(b.get("high")); Lo[i] = _f(b.get("low")); ST[i] = _f(b.get("start_time"))
        ET[i] = _f(b.get("end_time", 0) or (ST[i] + TFMIN.get(tf, 1) * 60))
    return O, C, Hi, Lo, ST, ET, n


def exec_1m(root1m):
    O, C, Hi, Lo, ST, ET, n = load_ohlc(root1m, "1m")
    return ST, Hi, Lo, C, n


def run(root, tf, X):
    ST1, Hi1, Lo1, C1, n1 = X
    O, C, Hi, Lo, ST, ET, n = load_ohlc(root, tf)
    fillwin = 12 * TFMIN[tf] * 60; hold = 48 * TFMIN[tf] * 60
    tr = []; busy = 0.0
    for i in range(1, n - 1):
        if ST[i] < busy:                                          # non-overlap: prior trade still open
            continue
        rng = Hi[i] - Lo[i]
        if rng <= 0:
            continue
        side = 0
        if C[i] > O[i] and (O[i] - Lo[i]) <= WICK_TOL * rng:
            side = 1
        elif C[i] < O[i] and (Hi[i] - O[i]) <= WICK_TOL * rng:
            side = -1
        if side == 0:
            continue
        entry = O[i]; sl = entry * (1 - side * SL_FR); tp = entry * (1 + side * TP_FR)
        s0 = int(np.searchsorted(ST1, ET[i] - 1.0, "left"))       # first 1m bar at/after the candle close
        e0 = int(np.searchsorted(ST1, ET[i] + fillwin, "right"))
        if e0 <= s0:
            continue
        fmask = (Lo1[s0:e0] <= entry) if side > 0 else (Hi1[s0:e0] >= entry)
        if not fmask.any():
            continue
        fj = s0 + int(np.argmax(fmask))                           # first 1m bar that touches the limit -> fill
        he = int(np.searchsorted(ST1, ST1[fj] + hold, "right"))
        adv = (Lo1[fj:he] <= sl) if side > 0 else (Hi1[fj:he] >= sl)   # 1m-resolved stop-first (true intrabar order)
        fav = (Hi1[fj:he] >= tp) if side > 0 else (Lo1[fj:he] <= tp)
        ai = int(np.argmax(adv)) if adv.any() else 1 << 30
        fi = int(np.argmax(fav)) if fav.any() else 1 << 30
        if ai == (1 << 30) and fi == (1 << 30):
            net = side * (C1[he - 1] - entry) / entry - FEE - SLIP; rt = ST1[he - 1]
        elif ai <= fi:
            net = side * (sl - entry) / entry - FEE - SLIP; rt = ST1[fj + ai]
        else:
            net = side * (tp - entry) / entry - FEE; rt = ST1[fj + fi]
        tr.append((ST[i], datetime.fromtimestamp(ST[i], tz=timezone.utc).year, net, SL_FR))
        busy = rt                                                 # non-overlap until resolution
    return tr


def stat(tr):
    if not tr:
        return "n=0                    "
    a = np.array([t[2] for t in tr]) * 100.0; rm = np.array([t[2] / t[3] for t in tr])
    return "n=%-4d win%4.1f%% exp%+.3f%% avgR%+.3f" % (len(a), 100 * (a > 0).mean(), a.mean(), rm.mean())


def main():
    print("NO-WICK retest — 1m-RESOLVED (true intrabar order) | SL 1%% TP 0.5%% | non-overlap | maker entry/TP\n", flush=True)
    Xc = exec_1m("study/clock_archive")
    print("  source  tf   | ALL %-26s | IS %-26s | OOS %-26s | pass" % ("", "", ""), flush=True)
    for root, tf, X, src in (("study/clock_archive", "15m", Xc, "clock "), ("study/clock_archive", "30m", Xc, "clock "),
                             ("study/clock_archive", "1h", Xc, "clock ")):
        tr = run(root, tf, X)
        m = mc(day_blocks([(t[0], t[2] / t[3]) for t in tr])[0]) if tr else dict(p=0)
        print("  %s %-4s | ALL %s | IS %s | OOS %s | %5.1f%%"
              % (src, tf, stat(tr), stat([t for t in tr if t[1] == 2025]), stat([t for t in tr if t[1] == 2026]), m["p"]), flush=True)


if __name__ == "__main__":
    main()
