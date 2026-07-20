"""MM×Skew 1h — sweep the breakeven-stop's ARM level and LOCK distance (both in R = SL-distance units).
  arm_R : move the stop once price reaches +arm_R (in R).   0 = no BE stop (baseline).
  lock_R: move the stop to entry + lock_R (in R).           0 = true breakeven; must be < arm_R.
Every trade resolves as TP(+RR) / BE(+lock_R) / SL(-1R). Intra-bar order is unknowable -> score each config
by its WORST case across {adverse-first, favorable-first}; a config only 'wins' if worst-case net AND worst-
case maxDD both beat baseline. Entry rules frozen (study/MMXSKEW.md).
Run:  python study/mm_skew_be_sweep.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import study.mm_skew_poc as P
import study.mm_skew_strategy as S

SL_BUF = 0.001
FEE = 0.0008


def sim(M, i, side, rr, order, arm_R, lock_R):
    e = M[i]["c"]
    if side > 0:
        sl0 = M[i]["l"] * (1 - SL_BUF); sld = e - sl0; tp = e + rr * sld; trig = e + arm_R * sld; be = e + lock_R * sld
    else:
        sl0 = M[i]["h"] * (1 + SL_BUF); sld = sl0 - e; tp = e - rr * sld; trig = e - arm_R * sld; be = e - lock_R * sld
    if sld <= 0:
        return None
    slf = sld / e
    use_be = arm_R > 0 and arm_R < rr and lock_R < arm_R
    armed = False
    for j in range(i + 1, len(M)):
        hi = M[j]["h"]; lo = M[j]["l"]
        if side > 0:
            f_tp = hi >= tp; f_tr = hi >= trig; a_sl = lo <= sl0; a_be = lo <= be
        else:
            f_tp = lo <= tp; f_tr = lo <= trig; a_sl = hi >= sl0; a_be = hi >= be
        if order == "adverse":
            if armed and a_be:
                return lock_R * slf, j
            if (not armed) and a_sl:
                return -slf, j
            if f_tp:
                return rr * slf, j
            if use_be and (not armed) and f_tr:
                armed = True
        else:
            if f_tp:
                return rr * slf, j
            if use_be and (not armed) and f_tr:
                armed = True
            if armed and a_be:
                return lock_R * slf, j
            if (not armed) and a_sl:
                return -slf, j
    return (M[-1]["c"] - e) / e * side, len(M) - 1


def equity(M, rr, order, arm_R, lock_R):
    bal = S.BAL0; i = 0; peak = bal; dd = 0.0; n = 0
    while i < len(M) - 1:
        s = P.sig(M[i])
        if s == 0:
            i += 1; continue
        res = sim(M, i, s, rr, order, arm_R, lock_R)
        if res is None:
            i += 1; continue
        retf, jc = res
        notional = S.POS_FRAC * bal * S.LEV
        bal += notional * retf - notional * FEE
        n += 1; peak = max(peak, bal); dd = max(dd, (peak - bal) / peak if peak > 0 else 0.0)
        i = jc + 1
    return (bal / S.BAL0 - 1) * 100.0, dd * 100.0, n


def main():
    M, span = P.build()
    print(f"mature 1h bars {len(M)}  span {span:.1f}d.  net@0.08% fee. worst = min over intra-bar orders.\n")
    ARMS = [0.3, 0.5, 0.7, 1.0]
    LOCKS = [0.0, 0.1, 0.2, 0.3, 0.5]
    for rr in (0.7, 1.0, 1.5):
        ba, bda, _ = equity(M, rr, "adverse", 0, 0); bf, bdf, _ = equity(M, rr, "favorable", 0, 0)
        b_worst = min(ba, bf); b_worstdd = max(bda, bdf)
        print("=" * 98)
        print(f"RR 1:{rr}   BASELINE net adv {ba:+.1f}% / fav {bf:+.1f}%  -> worst {b_worst:+.1f}% (worst maxDD {b_worstdd:.1f}%)")
        print("=" * 98)
        rows = []
        for arm in ARMS:
            if arm >= rr:
                continue
            for lock in LOCKS:
                if lock >= arm:
                    continue
                na, da, _ = equity(M, rr, "adverse", arm, lock)
                nf, df, _ = equity(M, rr, "favorable", arm, lock)
                worst = min(na, nf); worstdd = max(da, df)
                beats = worst > b_worst and worstdd <= b_worstdd + 0.3
                rows.append((worst, arm, lock, na, nf, worstdd, beats))
        rows.sort(key=lambda t: -t[0])
        print(f"  {'arm':>4} {'lock':>5} | {'adv net':>8} {'fav net':>8} | {'WORST net':>9} {'worstDD':>7} | beats baseline?")
        for worst, arm, lock, na, nf, wdd, beats in rows[:8]:
            print(f"  {arm:>4.1f} {lock:>5.1f} | {na:>+7.1f}% {nf:>+7.1f}% | {worst:>+8.1f}% {wdd:>6.1f}% | "
                  f"{'YES' if beats else 'no'}")
        print()


if __name__ == "__main__":
    main()
