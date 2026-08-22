"""NO-WICK candle retest. Bullish candle with NO bottom wick (open==low) -> limit LONG at its open; bearish candle with
NO top wick (open==high) -> limit SHORT at its open. The no-wick side = the open was an extreme; enter the pullback to it.
SL 1%%, TP 0.5%% (RR 0.5). Limit entry+TP = maker (no slip); SL = taker. Fill within FILLWIN bars (prompt retest); hold to
TP/SL within HOLD bars; NON-OVERLAPPING (skip signals while a position is open). Grid: clock {15m,30m,1h,4h} + bucket
{15m,1h,4h} (recon has no 30m), excl 1m/5m. IS(2025)/OOS(2026), prop-MC. python study/nowick_limit_retest.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.ny_rangebreak_poc_prop import mc, day_blocks
FEE, SLIP, SL_FR, TP_FR, WICK_TOL, FILLWIN, HOLD = 0.0004, 0.0003, 0.01, 0.005, 0.05, 12, 48
GRID = [("study/clock_archive", tf) for tf in ("15m", "30m", "1h", "4h")] + \
       [("study/recon_archive", tf) for tf in ("15m", "1h", "4h")]


def load(root, tf):
    A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A); O = np.zeros(n); C = np.zeros(n); Hi = np.zeros(n); Lo = np.zeros(n); ST = np.zeros(n)
    for i, b in enumerate(A):
        O[i] = _f(b.get("open", b.get("open_price"))); C[i] = _f(b.get("close", b.get("close_price")))
        Hi[i] = _f(b.get("high")); Lo[i] = _f(b.get("low")); ST[i] = _f(b.get("start_time"))
    return O, C, Hi, Lo, ST, n


def run(root, tf):
    O, C, Hi, Lo, ST, n = load(root, tf)
    tr = []; i = 1
    while i < n - 1:
        rng = Hi[i] - Lo[i]
        if rng <= 0:
            i += 1; continue
        side = 0
        if C[i] > O[i] and (O[i] - Lo[i]) <= WICK_TOL * rng:          # bullish, no bottom wick -> long at open (=low)
            side = 1
        elif C[i] < O[i] and (Hi[i] - O[i]) <= WICK_TOL * rng:        # bearish, no top wick -> short at open (=high)
            side = -1
        if side == 0:
            i += 1; continue
        entry = O[i]; sl = entry * (1 - side * SL_FR); tp = entry * (1 + side * TP_FR)
        fj = None
        for j in range(i + 1, min(i + 1 + FILLWIN, n)):              # limit fills on the pullback to the open
            if (Lo[j] <= entry) if side > 0 else (Hi[j] >= entry):
                fj = j; break
        if fj is None:
            i += 1; continue                                         # no retest within the window
        net = None; rj = fj
        # FILL BAR: check SL only (skip same-bar TP -> the bar's TP-side extreme may predate the pullback fill = look-ahead)
        if (Lo[fj] <= sl) if side > 0 else (Hi[fj] >= sl):
            net = side * (sl - entry) / entry - FEE - SLIP
        else:
            for j in range(fj + 1, min(fj + HOLD, n)):               # subsequent bars: stop-first
                adverse = (Lo[j] <= sl) if side > 0 else (Hi[j] >= sl)
                favor = (Hi[j] >= tp) if side > 0 else (Lo[j] <= tp)
                rj = j
                if adverse:
                    net = side * (sl - entry) / entry - FEE - SLIP; break
                if favor:
                    net = side * (tp - entry) / entry - FEE; break
            if net is None:
                net = side * (C[rj] - entry) / entry - FEE - SLIP
        tr.append((ST[fj], datetime.fromtimestamp(ST[fj], tz=timezone.utc).year, net, SL_FR, side))
        i = rj + 1                                                    # NON-OVERLAP: resume after resolution
    return tr


def stat(tr):
    if not tr:
        return "n=0                    "
    a = np.array([t[2] for t in tr]) * 100.0; rm = np.array([t[2] / t[3] for t in tr])
    return "n=%-4d win%4.1f%% exp%+.3f%% avgR%+.3f" % (len(a), 100 * (a > 0).mean(), a.mean(), rm.mean())


def main():
    print("NO-WICK candle retest | limit @open of a no-wick momentum candle | SL 1%% TP 0.5%% (RR 0.5) | non-overlap | maker entry/TP\n", flush=True)
    print("  source  tf   | ALL %-26s | IS %-26s | OOS %-26s | R0.4 pass" % ("", "", ""), flush=True)
    for root, tf in GRID:
        tr = run(root, tf)
        m = mc(day_blocks([(t[0], t[2] / t[3]) for t in tr])[0]) if tr else dict(p=0)
        src = "clock " if "clock" in root else "bucket"
        print("  %s %-4s | ALL %s | IS %s | OOS %s | %5.1f%%"
              % (src, tf, stat(tr), stat([t for t in tr if t[1] == 2025]), stat([t for t in tr if t[1] == 2026]), m["p"]), flush=True)


if __name__ == "__main__":
    main()
