"""CROSS-BAR delta-acceleration -> next-bar edge (1h SOLUSDT recon).

New angle vs single-bar work: does a MULTI-BAR aggression trajectory predict the next bar?
  dda1  = da1[i] - da1[i-1]        (is signed aggression LEVEL building bar-over-bar?)
  dda2  = da2[i] - da2[i-1]        (is intra-bar accel building bar-over-bar?)
  dda1_dir = dda1 * s              (directionalized to THIS candle)
  runs: k consecutive bars with same-sign da1_dir>0 (aggression aligned & sustained)

Targets: P(next continues this candle) [cont], P(next UP) [nup].
Disjoint deciles/bands, exact-binomial two-sided p vs correct baseline, 2025/26 split, net of 0.08% fee.

Run: python study/tape_crossbar_nextbar.py [tf]
"""
from __future__ import annotations
import os, sys, math, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.tape_accel_nextbar import build, binom_p, FEE


def _a(recs, k):
    return np.array([r[k] for r in recs], float)


def cross(recs):
    """Attach cross-bar features. recs already ordered chronologically by build()."""
    out = []
    for i in range(1, len(recs)):
        p, r = recs[i - 1], dict(recs[i])
        r["dda1"] = r["da1"] - p["da1"]
        r["dda2"] = r["da2"] - p["da2"]
        r["dda1d"] = (r["da1"] - p["da1"]) * r["s"]
        r["dda2d"] = (r["da2"] - p["da2"]) * r["s"]
        r["_i"] = i
        out.append(r)
    return out


def runs(recs):
    """Same-sign run length of da1_dir>0 up to and including bar i (aligned aggression sustained)."""
    rl = 0
    for r in recs:
        if r["da1d"] > 0:
            rl += 1
        else:
            rl = 0
        r["run"] = rl
    return recs


def deciles(recs, vkey, hkey, rkey, base, title, hlab):
    if not recs:
        print("  (no rows)"); return
    arr = [recs[i] for i in np.argsort(_a(recs, vkey), kind="mergesort")]
    print("\n%s   baseline %s=%.1f%%  (n=%d)" % (title, hlab, base * 100, len(recs)))
    print("  %-4s %15s %6s %8s %9s %9s   %-10s %-10s %6s" % ("dec", "band", "n", hlab, "ret%", "net%", "2025", "2026", "p"))
    for j, ch in enumerate(np.array_split(arr, 10)):
        if len(ch) == 0:
            continue
        vv = _a(ch, vkey); hh = _a(ch, hkey); rr = _a(ch, rkey)
        c25 = [x for x in ch if x["yr"] == 2025]; c26 = [x for x in ch if x["yr"] == 2026]
        print("  %-4d %6.3f-%6.3f %6d %7.1f%% %+8.4f%% %+8.4f%%   %-10s %-10s %6.3f" %
              (j + 1, vv.min(), vv.max(), len(ch), hh.mean() * 100, rr.mean() * 100, rr.mean() * 100 - FEE * 100,
               ("%.0f%%n%d" % (np.mean([x[hkey] for x in c25]) * 100, len(c25))) if c25 else "-",
               ("%.0f%%n%d" % (np.mean([x[hkey] for x in c26]) * 100, len(c26))) if c26 else "-",
               binom_p(hh.sum(), len(ch), base)))


def bands(recs, name, groups, hkey, rkey, base, hlab):
    print("\n%s   (baseline %s=%.1f%%)" % (name, hlab, base * 100))
    for lab, sel in groups:
        g = [r for r in recs if sel(r)]
        if not g:
            print("  %-30s n=0" % lab); continue
        h = np.mean([r[hkey] for r in g]); ret = np.mean([r[rkey] for r in g]) * 100
        c25 = [r for r in g if r["yr"] == 2025]; c26 = [r for r in g if r["yr"] == 2026]
        print("  %-30s n=%5d  %s %5.1f%%  ret %+.4f%%  net %+.4f%%  25:%s 26:%s  p=%.3f" %
              (lab, len(g), hlab, h * 100, ret, ret - FEE * 100,
               ("%.0f%%n%d" % (np.mean([r[hkey] for r in c25]) * 100, len(c25))) if c25 else "-",
               ("%.0f%%n%d" % (np.mean([r[hkey] for r in c26]) * 100, len(c26))) if c26 else "-",
               binom_p(sum(r[hkey] for r in g), len(g), base)))


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "1h"
    base_recs = runs(build(tf))
    recs = cross(base_recs)
    bull = [r for r in recs if r["s"] == 1]; bear = [r for r in recs if r["s"] == -1]
    b_up = np.mean([r["nup"] for r in recs]); b_all = np.mean([r["cont"] for r in recs])
    b_cb = np.mean([r["cont"] for r in bull]); b_cs = np.mean([r["cont"] for r in bear])
    print("=" * 116)
    print("CROSS-BAR TAPE ACCEL -> next-bar  |  %s recon, %d usable bars" % (tf, len(recs)))
    print("  P(next UP)=%.1f%% | all-cont %.1f%% | bull-cont %.1f%% | bear-cont %.1f%% | fee %.2f%%" %
          (b_up * 100, b_all * 100, b_cb * 100, b_cs * 100, FEE * 100))
    print("=" * 116)

    # 1. dda1_dir : aggression LEVEL building bar-over-bar, aligned to candle -> continuation
    deciles(recs, "dda1d", "cont", "ret_c", b_all, "[1] dda1_dir (aggr level building into candle) -> P(continue)", "cont%")
    # 2. dda2_dir : intra-bar accel building bar-over-bar -> continuation
    deciles(recs, "dda2d", "cont", "ret_c", b_all, "[2] dda2_dir (accel building bar/bar) -> P(continue)", "cont%")
    # 3. raw dda1 -> next UP (does building buy-aggression regardless of candle predict up?)
    deciles(recs, "dda1", "nup", "ret_L", b_up, "[3] dda1 raw (signed) -> P(next UP), long-next ret", "nextUP%")
    deciles(recs, "dda2", "nup", "ret_L", b_up, "[4] dda2 raw (signed) -> P(next UP), long-next ret", "nextUP%")

    # 5. sign quadrants: is da1 already high AND still building? (level x change)
    bands(recs, "[5] da1_dir level x dda1_dir change (sustained vs fading aggression)", [
        ("high&building (d1d>0,dd1d>0)", lambda r: r["da1d"] > 0 and r["dda1d"] > 0),
        ("high&fading   (d1d>0,dd1d<=0)", lambda r: r["da1d"] > 0 and r["dda1d"] <= 0),
        ("low&building  (d1d<=0,dd1d>0)", lambda r: r["da1d"] <= 0 and r["dda1d"] > 0),
        ("low&fading    (d1d<=0,dd1d<=0)", lambda r: r["da1d"] <= 0 and r["dda1d"] <= 0)],
        "cont", "ret_c", b_all, "cont%")

    # 6. same-sign RUNS of aligned aggression (multi-bar)
    bands(recs, "[6] run-length of da1_dir>0 (consecutive bars aggression aligned w/ move)", [
        ("run==0 (not aligned)", lambda r: r["run"] == 0),
        ("run==1", lambda r: r["run"] == 1),
        ("run==2", lambda r: r["run"] == 2),
        ("run==3", lambda r: r["run"] == 3),
        ("run>=4", lambda r: r["run"] >= 4)],
        "cont", "ret_c", b_all, "cont%")

    # 7. both da2_dir accel AND dda2 building (accelerating into close, and MORE than last bar)
    bands(recs, "[7] da2_dir x dda2_dir (accel now x accel building)", [
        ("accel&building (a2d>0,dd2d>0)", lambda r: r["da2d"] > 0 and r["dda2d"] > 0),
        ("accel&fading   (a2d>0,dd2d<=0)", lambda r: r["da2d"] > 0 and r["dda2d"] <= 0),
        ("decel&building (a2d<=0,dd2d>0)", lambda r: r["da2d"] <= 0 and r["dda2d"] > 0),
        ("decel&fading   (a2d<=0,dd2d<=0)", lambda r: r["da2d"] <= 0 and r["dda2d"] <= 0)],
        "cont", "ret_c", b_all, "cont%")

    # 8. bull/bear separated extreme dda1 deciles for next-UP (directional, avoids the cont/geometry trap)
    deciles(bull, "dda1", "nup", "ret_L", np.mean([r["nup"] for r in bull]),
            "[8b] BULL bars: dda1 -> P(next UP)", "nextUP%")
    deciles(bear, "dda1", "nup", "ret_L", np.mean([r["nup"] for r in bear]),
            "[8s] BEAR bars: dda1 -> P(next UP)", "nextUP%")


if __name__ == "__main__":
    main()
