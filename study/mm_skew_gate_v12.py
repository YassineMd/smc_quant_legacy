"""MMXSKEW v1.2 CANDIDATE GATE — combined primary filter: run_pos<=4 AND mov_mag>=39.

Applies the two significant, two-sided edges the 2026-07-20 study surfaced as an ADDITIONAL gate on the frozen
v1.1 signal (nothing about v1.1 entry/exit changes — this is a candidate overlay for forward-testing):
  - Sequence gate  : run_pos <= 4  (avoid the 5th+ consecutive same-side signal = late-run fatigue)
  - Expansion gate : mov_mag >= 39 (avoid low-displacement churn buckets)
run_pos is counted over ALL raw v1.1 signals (same as the mined feature); a signal is TAKEN one-at-a-time only if
it passes BOTH gates. Reports retention, win% (both RR, per side), cumulative net%, total R, maxDD, profit factor,
net expectancy after 0.08% fee, and — the honest check — the split-half OOS lift (both thresholds were mined
in-sample, so the combined lift is optimistic; the split-half tells us if it survives).

Run: python study/mm_skew_gate_v12.py
"""
from __future__ import annotations
import os, sys, statistics
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import study.mm_skew_feature_matrix as FM
import study.mm_skew_rr_sweep as RR
import study.mm_skew_strategy as S

FEE = 0.0008; RUN_MAX = 4; MM_MIN = 39.0


def all_signals(A, first):
    sigs = []; run = 0; prev = 0
    for i in range(first, len(A) - 1):
        s = FM.sig(A[i])
        if s == 0:
            continue
        run = run + 1 if s == prev else 1; prev = s
        b = A[i]; ref = b["l"] if b["c"] > b["o"] else (b["h"] if b["c"] < b["o"] else b["o"])
        mm = ((((b["c"] * 100.0) / ref) - 100.0) ** 2) * 100.0 if ref > 0 else 0.0
        sigs.append(dict(i=i, side=s, run=run, mm=mm))
    return sigs


def walk(A, sigs, gate, rr):
    """One-at-a-time over signals passing gate(). Returns per-trade records."""
    out = []; last = -1
    for sg in sigs:
        if sg["i"] <= last or not gate(sg):
            continue
        res = RR.simulate_rr(A, sg["i"], sg["side"], rr, "sl")
        if res is None:
            continue
        b = A[sg["i"]]; e = b["c"]; ext = b["l"] if sg["side"] > 0 else b["h"]
        sl = ext * (1 - 0.001) if sg["side"] > 0 else ext * (1 + 0.001)
        sld = abs(e - sl) / e * 100.0                       # stop distance %
        net = res[1] * 100.0 - FEE * 100.0                  # net % after fee
        r_mult = (res[1] * 100.0) / sld - (FEE * 100.0) / sld if sld > 0 else 0.0   # net R
        out.append(dict(side=sg["side"], win=(res[0] == "TP"), net=net, R=r_mult, t=float(b["start_time"])))
        last = res[2]
    return out


def summarize(tr):
    if not tr:
        return None
    bal = S.BAL0; peak = bal; dd = 0.0
    for x in tr:
        bal *= (1 + x["net"] / 100.0); peak = max(peak, bal); dd = max(dd, (peak - bal) / peak)
    wins = [x["net"] for x in tr if x["net"] > 0]; loss = [x["net"] for x in tr if x["net"] <= 0]
    pf = (sum(wins) / abs(sum(loss))) if loss and sum(loss) != 0 else float("inf")
    lo = [x for x in tr if x["side"] > 0]; sh = [x for x in tr if x["side"] < 0]
    return dict(n=len(tr), win=100 * np.mean([x["win"] for x in tr]),
                lwin=(100 * np.mean([x["win"] for x in lo]) if lo else float("nan")), ln=len(lo),
                swin=(100 * np.mean([x["win"] for x in sh]) if sh else float("nan")), sn=len(sh),
                cum=(bal / S.BAL0 - 1) * 100, R=sum(x["R"] for x in tr), dd=dd * 100, pf=pf,
                exp=np.mean([x["net"] for x in tr]))


def main():
    A, first, daylist, dayfull = FM.build()
    sigs = all_signals(A, first)
    ALL = lambda sg: True
    GATE = lambda sg: sg["run"] <= RUN_MAX and sg["mm"] >= MM_MIN
    print("MMXSKEW v1.2 CANDIDATE GATE  =  run_pos<=%d  AND  mov_mag>=%.0f" % (RUN_MAX, MM_MIN))
    print("(additional filter on the frozen v1.1 signal; nothing about v1.1 changes)\n")
    for rr in (1.0, 1.5):
        a = summarize(walk(A, sigs, ALL, rr)); g = summarize(walk(A, sigs, GATE, rr))
        print("=" * 92); print("RR 1:%s" % rr); print("=" * 92)
        print("  %-10s | %4s | %-22s | %7s %6s %6s %5s | %6s" %
              ("cohort", "n", "win%  (L / S)", "cumNet%", "totR", "maxDD", "PF", "exp%"))
        for lbl, s in (("BASELINE", a), ("v1.2 GATE", g)):
            print("  %-10s | %4d | %5.1f (%4.1f/%4.1f) n%d/%d | %+6.1f %+6.1f %5.1f %5.2f | %+.3f" %
                  (lbl, s["n"], s["win"], s["lwin"], s["swin"], s["ln"], s["sn"],
                   s["cum"], s["R"], s["dd"], s["pf"], s["exp"]))
        print("  retention: %d/%d signals kept (%.0f%%)\n" % (g["n"], a["n"], 100 * g["n"] / a["n"]))

    # split-half OOS on the gate (both thresholds mined in-sample -> is the lift real?)
    print("=" * 92); print("SPLIT-HALF OOS — win% gated vs baseline, within each time-half"); print("=" * 92)
    for rr in (1.0, 1.5):
        base = walk(A, sigs, lambda sg: True, rr); gate = walk(A, sigs, GATE, rr)
        base.sort(key=lambda z: z["t"]); gate.sort(key=lambda z: z["t"])
        bmid = base[len(base) // 2]["t"]
        for half, lo, hi in (("H1", -1e18, bmid), ("H2", bmid, 1e18)):
            bb = [x["win"] for x in base if lo <= x["t"] < hi]; gg = [x["win"] for x in gate if lo <= x["t"] < hi]
            print("  RR 1:%s %s | baseline %.0f%% (n%d)  ->  gated %.0f%% (n%d)   lift %+.0f pp"
                  % (rr, half, 100 * np.mean(bb), len(bb), 100 * np.mean(gg) if gg else 0, len(gg),
                     (100 * np.mean(gg) - 100 * np.mean(bb)) if gg else 0))
        print()


if __name__ == "__main__":
    main()
