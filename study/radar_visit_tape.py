"""Does the ABSORBED-side tape WANE over a RESISTED radar visit (5m)?

Unit = one numbered radar VISIT (a wall's radar_run). Keep visits with >= MINBARS 5m candles that RESISTED (the wall
held and EJECTED price). By construction a non-break, completed radar run IS resisted+ejected: a support wall's only
non-break exit is UP, a resistance wall's is DOWN (a body close through the radar is a BREAK, not a visit end).

Hypothesis:
  * BUY wall (support S) that resisted  -> the SELL tape (Tape-S) decreases before price exits the radar (sellers
    hitting support get absorbed and fade).
  * SELL wall (resistance R) that resisted -> the BUY tape (Tape-B) decreases before exit.

"Decreases before exiting" (per visit, self-normalized): (1) linear SLOPE over the visit candles < 0, and
(2) mean(last 2 candles) < mean(first 2 candles). Tape-B/Tape-S = per-print buy/sell size per second (sz_cb/sz_cs).

CONTROLS (a decline only means something if it's SPECIFIC):
  A. OPPOSITE-side tape over the same resisted visits (should decline LESS if the effect is the absorbed side fading).
  B. BROKEN visits (wall failed) — same absorbed-side tape (should decline LESS if waning tape ~ holding).
  C. Both recon years.
Descriptive reliability only. Run: python study/radar_visit_tape.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

MINBARS = 5
TF = "5m"
A = sorted(load_archive(TF, root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
n = len(A)
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]


def _tape(b):
    dur = max(1.0, _f(b.get("end_time")) - _f(b.get("start_time")))
    return sum(b.get("sz_cb") or []) / dur, sum(b.get("sz_cs") or []) / dur


TB = np.array([_tape(b)[0] for b in A])
TS = np.array([_tape(b)[1] for b in A])
print("bars=%d  detecting walls ..." % n, flush=True)
walls = AL.detect(A)
print("walls=%d" % len(walls), flush=True)


def decline(series):
    """(slope<0, mean(last2)<mean(first2), rel_drop) for a >=MINBARS tape series over one visit."""
    y = np.asarray(series, float)
    m = len(y)
    xs = np.arange(m)
    slope = np.polyfit(xs, y, 1)[0]
    f2 = y[:2].mean(); l2 = y[-2:].mean()
    rel = (f2 - l2) / f2 if f2 > 0 else 0.0
    return slope < 0, l2 < f2, rel


# collect visits: (year, side, is_resist, tgt_slopeneg, tgt_lastlt, tgt_rel, opp_slopeneg, opp_lastlt)
rows = []
for w in walls:
    side = w["side"]; runs = w.get("radar_runs", ())
    if not runs:
        continue
    broken = bool(w.get("broken")); i1 = int(w.get("i1", n - 1))
    for (rk0, rk1, _pr) in runs:
        rk0 = int(rk0); rk1 = int(rk1)
        bars = rk1 - rk0 + 1
        if bars < MINBARS or rk1 >= n - 1:            # need >=5 candles + a COMPLETED visit (price left the radar)
            continue
        is_break = broken and (rk0 <= i1 <= rk1 + 2)  # the run at/just before the wall's break -> did NOT resist
        is_resist = not is_break
        tgt = TS[rk0:rk1 + 1] if side == "S" else TB[rk0:rk1 + 1]   # absorbed side: S->sellers, R->buyers
        opp = TB[rk0:rk1 + 1] if side == "S" else TS[rk0:rk1 + 1]
        ts_neg, ts_ll, ts_rel = decline(tgt)
        op_neg, op_ll, _ = decline(opp)
        rows.append((YR[rk0], side, is_resist, ts_neg, ts_ll, ts_rel, op_neg, op_ll))


def pct(sub, idx):
    if not sub:
        return float("nan"), 0
    return 100.0 * sum(1 for r in sub if r[idx]) * 1.0 / len(sub), len(sub)


def med_rel(sub):
    v = [r[5] for r in sub]
    return float(np.median(v)) * 100 if v else float("nan")


def report(tag, sub):
    p_slope, N = pct(sub, 3); p_last, _ = pct(sub, 4)
    op_slope, _ = pct(sub, 6); op_last, _ = pct(sub, 7)
    print("  %-26s n=%-4d  ABSORBED-tape decline: slope<0 %4.1f%% / last<first %4.1f%% (median drop %+4.1f%%)   |   OPP-tape: slope<0 %4.1f%% / last<first %4.1f%%"
          % (tag, N, p_slope, p_last, med_rel(sub), op_slope, op_last), flush=True)


print("\n=== RESISTED visits (wall held & ejected), >=5 candles — does the ABSORBED side fade? ===", flush=True)
for lbl, side in (("SUPPORT (buy wall) -> Tape-S", "S"), ("RESIST (sell wall) -> Tape-B", "R")):
    res = [r for r in rows if r[2] and r[1] == side]
    report(lbl + " [BOTH]", res)
    for y in (2025, 2026):
        report(lbl + " [%d]" % y, [r for r in res if r[0] == y])

print("\n=== CONTROL: BROKEN visits (wall failed), >=5 candles — same absorbed-side tape ===", flush=True)
for lbl, side in (("SUPPORT broke -> Tape-S", "S"), ("RESIST broke -> Tape-B", "R")):
    brk = [r for r in rows if (not r[2]) and r[1] == side]
    report(lbl + " [BOTH]", brk)

print("\n(base rate: a pure-noise series gives ~50%% slope<0 and ~50%% last<first)", flush=True)
