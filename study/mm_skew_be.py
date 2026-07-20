"""MM×Skew 1h — test a BREAKEVEN STOP: once price reaches +0.5R, move the SL to entry +0.1% (long) /
entry -0.1% (short), locking ~0.1% (≈ breakeven after fees). Baseline = fixed SL at extreme, no BE move.
Entry/skew/spread/POC rules unchanged (study/MMXSKEW.md). Outcomes: TP (+RR), BE (+0.1%), SL (-1R).

Trades resolve in ~1 bar, so the intra-bar order is material -> BOUND it:
  adverse-first  (pessimistic): within a bar the adverse extreme is touched before the favorable one;
  favorable-first(optimistic): favorable first.
Baseline uses the SAME two orders (SL-first / TP-first) for a like-for-like compare.
Run:  python study/mm_skew_be.py
"""
from __future__ import annotations
import os, sys, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import study.mm_skew_poc as P
import study.mm_skew_strategy as S

RRS = [0.7, 1.0, 1.5]
SL_BUF = 0.001
BE = 0.001                     # lock +0.1% in favour


def sim(M, i, side, rr, order, use_be):
    e = M[i]["c"]
    if side > 0:
        sl0 = M[i]["l"] * (1 - SL_BUF); sld = e - sl0; tp = e + rr * sld; trig = e + 0.5 * sld; be = e * (1 + BE)
    else:
        sl0 = M[i]["h"] * (1 + SL_BUF); sld = sl0 - e; tp = e - rr * sld; trig = e - 0.5 * sld; be = e * (1 - BE)
    if sld <= 0:
        return None
    slf = sld / e
    can_be = use_be and (0.5 * sld > BE * e)          # trigger sits above the BE level
    armed = False
    for j in range(i + 1, len(M)):
        hi = M[j]["h"]; lo = M[j]["l"]
        if side > 0:
            f_tp = hi >= tp; f_tr = hi >= trig; a_sl = lo <= sl0; a_be = lo <= be
        else:
            f_tp = lo <= tp; f_tr = lo <= trig; a_sl = hi >= sl0; a_be = hi >= be
        if order == "adverse":
            if armed and a_be:
                return ("BE", BE, j)
            if (not armed) and a_sl:
                return ("SL", -slf, j)
            if f_tp:
                return ("TP", rr * slf, j)
            if can_be and (not armed) and f_tr:
                armed = True
        else:  # favorable-first
            if f_tp:
                return ("TP", rr * slf, j)
            if can_be and (not armed) and f_tr:
                armed = True
            if armed and a_be:
                return ("BE", BE, j)
            if (not armed) and a_sl:
                return ("SL", -slf, j)
    return ("OPEN", (M[-1]["c"] - e) / e * side, len(M) - 1)


def stats(M, rr, order, use_be):
    tp = be = sl = op = 0; rets = []
    for i in range(len(M) - 1):
        s = P.sig(M[i])
        if s == 0:
            continue
        res = sim(M, i, s, rr, order, use_be)
        if res is None:
            continue
        o, rf, _ = res; rets.append(rf)
        tp += o == "TP"; be += o == "BE"; sl += o == "SL"; op += o == "OPEN"
    n = len(rets)
    return dict(n=n, tp=tp, be=be, sl=sl, op=op, mean=statistics.mean(rets) * 100 if rets else 0.0)


def equity(M, rr, order, use_be, fee):
    bal = S.BAL0; i = 0; peak = bal; dd = 0.0; n = 0
    while i < len(M) - 1:
        s = P.sig(M[i])
        if s == 0:
            i += 1; continue
        res = sim(M, i, s, rr, order, use_be)
        if res is None:
            i += 1; continue
        notional = S.POS_FRAC * bal * S.LEV
        bal += notional * res[1] - notional * fee
        n += 1; peak = max(peak, bal); dd = max(dd, (peak - bal) / peak if peak > 0 else 0.0)
        i = res[2] + 1
    return (bal / S.BAL0 - 1) * 100.0, n, dd * 100.0


def main():
    M, span = P.build()
    print(f"mature 1h bars {len(M)}  span {span:.1f}d   (BE stop: at +0.5R move SL to entry +/-0.1%)\n")
    for order in ("adverse", "favorable"):
        tag = "SL-first / pessimistic" if order == "adverse" else "TP-first / optimistic"
        print("=" * 98); print(f"INTRA-BAR ORDER: {order}  ({tag})"); print("=" * 98)
        print(f"  {'RR':>5} | {'variant':>10} | {'TP':>4} {'BE':>4} {'SL':>4} | {'mean/t%':>8} | "
              f"{'eqGROSS':>8} {'eqNET':>7} {'maxDD':>6}")
        for rr in RRS:
            for use_be, lbl in ((False, "baseline"), (True, "BE-stop")):
                st = stats(M, rr, order, use_be)
                gg, _, dg = equity(M, rr, order, use_be, 0.0); gn, _, _ = equity(M, rr, order, use_be, 0.0008)
                print(f"  1:{rr:>3} | {lbl:>10} | {st['tp']:>4} {st['be']:>4} {st['sl']:>4} | "
                      f"{st['mean']:>+7.4f} | {gg:>+7.1f}% {gn:>+6.1f}% {dg:>5.1f}%")
            print()


if __name__ == "__main__":
    main()
