"""NO-WICK WALL — MOMENTUM entry at the candle CLOSE (matches the terminal badge). Bullish no-LOWER-wick -> LONG at close;
bearish no-UPPER-wick -> SHORT at close. SL = FULL candle length beyond the close; TP = ratio x that. Enter at close of bar
i (market/taker), walk from i+1 (no fill -> no look-ahead), stop-first, non-overlap. Tests RR 0.5 (the badge) + RR 1.0
(coin-flip diagnostic). Grid: clock {5m,15m,30m,1h,4h} + bucket {5m,15m,1h,4h} excl 1m. IS/OOS, prop-MC.
python study/nowick_wall_close.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.ny_rangebreak_poc_prop import mc, day_blocks
FEE, SLIP, WICK_TOL, HOLD = 0.0004, 0.0003, 0.001, 48
GRID = [("study/clock_archive", tf) for tf in ("5m", "15m", "30m", "1h", "4h")] + \
       [("study/recon_archive", tf) for tf in ("5m", "15m", "1h", "4h")]


def load(root, tf):
    A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A); O = np.zeros(n); C = np.zeros(n); Hi = np.zeros(n); Lo = np.zeros(n); ST = np.zeros(n)
    for i, b in enumerate(A):
        O[i] = _f(b.get("open", b.get("open_price"))); C[i] = _f(b.get("close", b.get("close_price")))
        Hi[i] = _f(b.get("high")); Lo[i] = _f(b.get("low")); ST[i] = _f(b.get("start_time"))
    return O, C, Hi, Lo, ST, n


_CACHE = {}


def run(root, tf, tpr):
    if (root, tf) not in _CACHE:
        _CACHE[(root, tf)] = load(root, tf)                       # load each archive ONCE (both RR blocks reuse it)
    O, C, Hi, Lo, ST, n = _CACHE[(root, tf)]
    tr = []; i = 1
    while i < n - 1:
        rng = Hi[i] - Lo[i]
        if rng <= 0:
            i += 1; continue
        side = 0
        if C[i] > O[i] and (O[i] - Lo[i]) <= WICK_TOL * rng:
            side = 1
        elif C[i] < O[i] and (Hi[i] - O[i]) <= WICK_TOL * rng:
            side = -1
        if side == 0:
            i += 1; continue
        entry = C[i]; sl = entry - side * rng; tp = entry + side * tpr * rng; sld = rng / entry   # entry = CLOSE
        net = None; rj = i
        for j in range(i + 1, min(i + 1 + HOLD, n)):              # walk from the NEXT bar (entered at close, no fill)
            adverse = (Lo[j] <= sl) if side > 0 else (Hi[j] >= sl)
            favor = (Hi[j] >= tp) if side > 0 else (Lo[j] <= tp)
            rj = j
            if adverse:
                net = side * (sl - entry) / entry - FEE - 2 * SLIP; break
            if favor:
                net = side * (tp - entry) / entry - FEE - SLIP; break
        if net is None:
            net = side * (C[rj] - entry) / entry - FEE - 2 * SLIP
        tr.append((ST[i], datetime.fromtimestamp(ST[i], tz=timezone.utc).year, net, sld, side))
        i = rj + 1
    return tr


def stat(tr):
    if not tr:
        return "n=0                    "
    a = np.array([t[2] for t in tr]) * 100.0; rm = np.array([t[2] / t[3] for t in tr])
    return "n=%-4d win%4.1f%% exp%+.3f%% avgR%+.3f" % (len(a), 100 * (a > 0).mean(), a.mean(), rm.mean())


def block(tpr):
    print("== entry at CLOSE, SL=full candle, TP=%.1fx (RR %.1f) ==" % (tpr, tpr), flush=True)
    print("  source  tf   | ALL %-26s | IS %-26s | OOS %-26s | R0.4 pass" % ("", "", ""), flush=True)
    for root, tf in GRID:
        tr = run(root, tf, tpr)
        m = mc(day_blocks([(t[0], t[2] / t[3]) for t in tr])[0]) if tr else dict(p=0)
        src = "clock " if "clock" in root else "bucket"
        print("  %s %-4s | ALL %s | IS %s | OOS %s | %5.1f%%"
              % (src, tf, stat(tr), stat([t for t in tr if t[1] == 2025]), stat([t for t in tr if t[1] == 2026]), m["p"]), flush=True)


def main():
    print("NO-WICK WALL — momentum entry at CLOSE | strict no-wick | non-overlap | maker TP / taker entry+SL\n", flush=True)
    block(0.5); print("", flush=True); block(1.0)


if __name__ == "__main__":
    main()
