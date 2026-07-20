"""Sweep the MM/Skew strategy over R:R = 0.5 / 0.7 / 1.0 / 1.5.
Same entry (bull&skew>0&close-top-tail -> long ; bear&skew<0&close-bottom-tail -> short) and same FIXED SL
(0.1% beyond the high/low). Only the TP distance = RR * SL distance changes, so the 405 signals are identical;
outcomes shift. Every trade is win(TP)/loss(SL); OPEN = still unresolved at data end (excluded from win%).

PART 1: topline per RR (win% vs the RR's break-even 1/(1+RR), per-trade edge, equity gross/net).
PART 2: winner-vs-loser separation on MM / Skew / MM*Skew per side (Mann-Whitney), with a split-half repeat
        check on anything that clears p<0.05 (in-sample range-hunting overfits — split-half is the judge).

Run:  python study/mm_skew_rr_sweep.py
"""
from __future__ import annotations
import os, sys, math, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import study.mm_skew_strategy as S
import study.mm_skew_winloss as WL

RRS = [0.5, 0.7, 1.0, 1.5]
CP = S.CP_THR
SL_BUF = S.SL_BUF


def simulate_rr(B, i, side, rr, both="sl"):
    entry = B[i]["c"]
    if side > 0:
        sl = B[i]["l"] * (1.0 - SL_BUF); sld = entry - sl; tp = entry + rr * sld
    else:
        sl = B[i]["h"] * (1.0 + SL_BUF); sld = sl - entry; tp = entry - rr * sld
    if sld <= 0:
        return None
    slf = sld / entry
    for j in range(i + 1, len(B)):
        hi = B[j]["h"]; lo = B[j]["l"]
        if side > 0:
            htp = hi >= tp; hsl = lo <= sl
        else:
            htp = lo <= tp; hsl = hi >= sl
        if htp and hsl:
            win = (both == "tp"); return ("TP" if win else "SL", (rr if win else -1.0) * slf, j)
        if htp:
            return ("TP", rr * slf, j)
        if hsl:
            return ("SL", -1.0 * slf, j)
    return ("OPEN", (B[-1]["c"] - entry) / entry * side, len(B) - 1)


def collect(B, rr):
    out = []
    for i in range(len(B) - 1):
        s = S.signal(B[i], CP)
        if s == 0:
            continue
        res = simulate_rr(B, i, s, rr, "sl")
        if res is None:
            continue
        b = B[i]; mm = S.mov_mag(b["o"], b["h"], b["l"], b["c"])
        out.append(dict(side=s, outcome=res[0], win=(res[0] == "TP"), retf=res[1],
                        mm=mm, sk=b["sk"], prod=mm * b["sk"], i=i))
    return out


def equity(B, rr, fee):
    bal = S.BAL0; i = 0; n = w = 0; peak = bal; dd = 0.0
    while i < len(B) - 1:
        s = S.signal(B[i], CP)
        if s == 0:
            i += 1; continue
        res = simulate_rr(B, i, s, rr, "sl")
        if res is None:
            i += 1; continue
        outcome, retf, jclose = res
        notional = S.POS_FRAC * bal * S.LEV
        bal += notional * retf - notional * fee
        n += 1; w += (1 if outcome == "TP" else 0)
        peak = max(peak, bal); dd = max(dd, (peak - bal) / peak if peak > 0 else 0.0)
        i = jclose + 1
    return bal, n, w, dd


def part1(B):
    print("=" * 100)
    print("PART 1 — RESULTS per R:R   (405 signals; win% among RESOLVED; edge = win% - break-even)")
    print("=" * 100)
    print(f"  {'R:R':>5} {'resolved':>8} {'OPEN':>5} {'win%':>6} {'break-even':>10} {'edge':>6} "
          f"{'exp/trade(R)':>12} | {'equityGROSS':>11} {'equityNET':>10} {'maxDD':>6}")
    for rr in RRS:
        sigs = collect(B, rr)
        res = [x for x in sigs if x["outcome"] != "OPEN"]
        op = len(sigs) - len(res)
        tp = sum(1 for x in res if x["win"]); win = 100.0 * tp / len(res) if res else float("nan")
        be = 100.0 / (1.0 + rr)
        exp_r = (win / 100.0) * (1.0 + rr) - 1.0      # expectancy in units of SL (R)
        bg, ng, wg, ddg = equity(B, rr, 0.0)
        bn, _, _, ddn = equity(B, rr, S.FEE_RT)
        rg = (bg / S.BAL0 - 1.0) * 100.0; rn = (bn / S.BAL0 - 1.0) * 100.0
        print(f"  {'1:'+str(rr):>5} {len(res):>8} {op:>5} {win:>6.1f} {be:>9.1f}% {win-be:>+5.1f} "
              f"{exp_r:>+12.3f} | {rg:>+10.1f}% {rn:>+9.1f}% {ddg*100:>5.1f}%")
    print("  exp/trade(R): +ve means positive expectancy in SL-units before fees. break-even win% = 1/(1+RR).")


def part2(B):
    print("\n" + "=" * 100)
    print("PART 2 — WINNER vs LOSER separation (Mann-Whitney p; * = p<0.05)   signal candle's feature")
    print("=" * 100)
    print(f"  {'R:R':>5} {'side':>6} | {'MovMag p':>9} {'Skew p':>9} {'MMxSkew p':>10}   (winners vs losers)")
    hits = []
    for rr in RRS:
        sigs = [x for x in collect(B, rr) if x["outcome"] != "OPEN"]
        for side, sn in ((+1, "LONG"), (-1, "SHORT")):
            ss = [x for x in sigs if x["side"] == side]
            W = [x for x in ss if x["win"]]; L = [x for x in ss if not x["win"]]
            ps = {}
            for key in ("mm", "sk", "prod"):
                p, dm = WL.mann_whitney([x[key] for x in W], [x[key] for x in L])
                ps[key] = p
                if p < 0.05:
                    hits.append((rr, side, sn, key))
            def f(k):
                return f"{ps[k]:.3f}" + ("*" if ps[k] < 0.05 else " ")
            print(f"  {'1:'+str(rr):>5} {sn:>6} | {f('mm'):>9} {f('sk'):>9} {f('prod'):>10}   "
                  f"({len(W)}W/{len(L)}L)")
    print(f"\n  significant (p<0.05) cells: {len(hits)}   |   expected by chance over 24 tests: ~1.2")
    return hits


def split_half(B, rr, side, key):
    sigs = sorted([x for x in collect(B, rr) if x["outcome"] != "OPEN" and x["side"] == side],
                  key=lambda x: x["i"])
    mid = len(sigs) // 2
    rows = []
    for sub in (sigs[:mid], sigs[mid:]):
        xs = sorted(sub, key=lambda x: x[key]); n = len(xs); c1 = n // 3; c2 = 2 * n // 3
        cells = []
        for lo, hi in ((0, c1), (c1, c2), (c2, n)):
            g = xs[lo:hi]; cells.append(100.0 * sum(1 for x in g if x["win"]) / len(g) if g else float("nan"))
        rows.append(cells)
    return rows


def main():
    B = S.load()
    print(f"mature 1h bars: {len(B)}   (entry fixed; RR only moves the TP)\n")
    part1(B)
    hits = part2(B)
    if hits:
        print("\n" + "=" * 100)
        print("SPLIT-HALF on the p<0.05 cells (tercile win% low/mid/high per half — must REPEAT to be real)")
        print("=" * 100)
        for rr, side, sn, key in hits:
            rows = split_half(B, rr, side, key)
            print(f"  1:{rr} {sn} {key}:  H1 {rows[0][0]:.0f}/{rows[0][1]:.0f}/{rows[0][2]:.0f}   "
                  f"H2 {rows[1][0]:.0f}/{rows[1][1]:.0f}/{rows[1][2]:.0f}")


if __name__ == "__main__":
    main()
