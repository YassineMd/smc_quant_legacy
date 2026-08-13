"""15m ENGULFING WALL — SCALE-OUT exit. SL/TP = the detector's OWN levels (current settings: SL 0.1% beyond prev
candle, TP 1:1.2 / gold 1:2). NO radar stop. Management:
  - bank 50% of the position at the 50%-to-TP level (half = entry + 0.5*(tp-entry)),
  - then move the stop to BREAKEVEN (entry) for the remaining 50%, which runs to the full TP.
(Interpreting "50% of TP at 0.5% the move" as scale 50% at 50%-of-the-move-to-TP; the runner is de-risked to BE.)

Outcomes: SL = stopped before scaling (full loss) | BE = scaled then stopped at breakeven (banked half, runner 0) |
TP = scaled + runner hit TP | TO = horizon exit at close. Conservative tie-breaks: SL-first pre-scale, BE-first post.
All day, day-open 08:00 Morocco (07:00 UTC), gate = TODAY & FLOW reward/eff aligned. Both years, 0.04% RT, non-overlap."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bisect
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL, momentum_detect as MOM, reward_eff

FEE = 0.0004; HORIZON = 96; DAY_OFF = 7 * 3600
A = sorted(load_archive("15m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
n = len(A)
C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
H = np.array([_f(b.get("high")) for b in A]); L = np.array([_f(b.get("low")) for b in A])
ST = [_f(b.get("start_time")) for b in A]
YR = np.array([datetime.fromtimestamp(t, tz=timezone.utc).year for t in ST])

print("bars=%d detecting..." % n, flush=True)
sigs = {}; c0 = 0
while c0 < n:
    c1 = min(n, c0 + 6000); S = A[c0:c1]
    for e in MOM.detect(S, AL.detect(S, skip_last=False), skip_last=False):
        gi = int(e["i"]) + c0
        if gi not in sigs:
            sigs[gi] = (int(e["side"]), float(e["entry"]), float(e["sl"]), float(e["tp"]), e.get("tier", "normal"))
    if c1 >= n:
        break
    c0 += 5000


def flow(i):
    ws = [s for s, ok in (reward_eff.share(A, i - w + 1, i) for w in (20, 30, 50, 75)) if ok]
    if not ws:
        return None
    sw = sorted(ws); m = len(sw)
    return sw[m // 2] if (m % 2) else 0.5 * (sw[m // 2 - 1] + sw[m // 2])


def today(i):
    d0 = (int((ST[i] - DAY_OFF) // 86400)) * 86400 + DAY_OFF
    s, ok = reward_eff.share(A, bisect.bisect_left(ST, float(d0)), i)
    return s if ok else None


def score(gi, side, entry, sl, tp):
    """Scale 50% at half-to-TP, runner to BE-or-TP. Returns (cat, blended_return, exit_bar)."""
    half = entry + 0.5 * (tp - entry); banked = 0.0; scaled = False
    xi = min(n - 1, gi + HORIZON)
    for k in range(gi + 1, min(n, gi + 1 + HORIZON)):
        lo = L[k]; hi = H[k]
        if not scaled:
            hit_sl = (lo <= sl) if side > 0 else (hi >= sl)
            hit_hf = (hi >= half) if side > 0 else (lo <= half)
            if hit_sl:                                       # SL-first on same-bar tie (conservative)
                return "SL", side * (sl - entry) / entry - FEE, k
            if hit_hf:
                scaled = True; banked = 0.5 * side * (half - entry) / entry
                hit_be = (lo <= entry) if side > 0 else (hi >= entry)   # runner, same-bar (BE-first)
                hit_tp = (hi >= tp) if side > 0 else (lo <= tp)
                if hit_be:
                    return "BE", banked - FEE, k
                if hit_tp:
                    return "TP", banked + 0.5 * side * (tp - entry) / entry - FEE, k
        else:
            hit_be = (lo <= entry) if side > 0 else (hi >= entry)
            hit_tp = (hi >= tp) if side > 0 else (lo <= tp)
            if hit_be:
                return "BE", banked - FEE, k
            if hit_tp:
                return "TP", banked + 0.5 * side * (tp - entry) / entry - FEE, k
    if scaled:
        return "TO", banked + 0.5 * side * (C[xi] - entry) / entry - FEE, xi
    return "TO", side * (C[xi] - entry) / entry - FEE, xi


rows = []                                                    # (yr, side, cat, ret, gi, xi, tier, fl_ok, td_ok)
for gi in sorted(sigs):
    if gi + 1 >= n:
        continue
    side, entry, sl, tp, tier = sigs[gi]
    if entry <= 0:
        continue
    fs = flow(gi); td = today(gi)
    if fs is None or td is None:
        continue
    fl_ok = (fs > 50) if side > 0 else (fs < 50)
    td_ok = (td > 50) if side > 0 else (td < 50)
    cat, ret, xi = score(gi, side, entry, sl, tp)
    rows.append((int(YR[gi]), side, cat, ret, gi, xi, tier, fl_ok, td_ok))
print("scored=%d\n" % len(rows), flush=True)


def report(tag, sel):
    R = [r for r in rows if sel(r)]

    def cell(RR):
        if not RR:
            return "n=0"
        N = len(RR)
        pct = lambda c: 100 * sum(1 for r in RR if r[2] == c) / N
        pos = 100 * sum(1 for r in RR if r[3] > 0) / N
        ov = -1; net = 0.0; nn = 0
        for r in sorted(RR, key=lambda x: x[4]):
            if r[4] <= ov:
                continue
            net += r[3]; ov = r[5]; nn += 1
        return "n=%-4d SL=%.0f%% BE=%.0f%% TP=%.0f%% TO=%.0f%% | net+%.0f%% | net(no)=%+.1f%%/%d exp=%+.3f%%" % (
            N, pct("SL"), pct("BE"), pct("TP"), pct("TO"), pos, net * 100, nn, (net / nn * 100) if nn else 0)
    print("  %-24s BOTH %s" % (tag, cell(R)))
    print("  %-24s 2025 %s" % ("", cell([r for r in R if r[0] == 2025])))
    print("  %-24s 2026 %s" % ("", cell([r for r in R if r[0] == 2026])))


print("=== 15m ENGULFING WALL — SCALE 50% @ 50%-to-TP + stop->BE (detector SL/TP) — all day, 08:00 Morocco ===")
report("all", lambda r: True)
report("+ TODAY & FLOW align", lambda r: r[7] and r[8])
report("+ TODAY&FLOW + gold", lambda r: r[7] and r[8] and r[6] == "gold")
