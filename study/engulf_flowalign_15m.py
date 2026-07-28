"""Does a trailing multi-bucket FLOW-alignment gate help the 15m Engulfing S/R (a continuation trade)?

Motivated by the Overnight-Drift SOL test: multi-hour flow imbalance TRENDS (momentum, beta up to +227),
so a breakout aligned with the prevailing 3-12h flow should have a tailwind. Trailing flow = sum(buy-sell)/
sum(buy+sell) over the last K buckets (K~12/24/48 ~= 3h/6h/12h on 15m volume buckets). ALIGN = LONG needs
trailing>0 / SHORT needs trailing<0; ANTI = the opposite; BASE = live rule unchanged. Non-overlap taken() basis.

CLI: python study/engulf_flowalign_15m.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import study.absorb_engulf_lastmit_15m as ENG
import study.engulf_va_sr_1h as E
import study.mom_absorb_1h as MA

TF = "15m"
KS = (12, 24, 48)
TRADE = ("gold", "conf", "us")


def _stats(rows):
    m = len(rows)
    if m == 0:
        return (0, 0.0, 0.0, 0.0)
    nets = [r["net"] for r in rows]; w = sum(1 for x in nets if x > 0)
    gg = sum(x for x in nets if x > 0); ll = -sum(x for x in nets if x < 0)
    pf = (gg / ll) if ll > 0 else float("inf")
    eq = MA.B0
    for x in nets:
        eq *= 1 + x
    return (m, 100.0 * w / m, pf, (eq / MA.B0 - 1) * 100.0)


def _fmt(rows):
    n, win, pf, net = _stats(rows)
    pfs = "inf " if pf == float("inf") else "%4.2f" % pf
    return "n=%3d win=%4.1f%% PF=%s net=%+7.1f%%" % (n, win, pfs, net)


def run(sigs, A, n, yr, trsv, K, mode):
    last = -1; rows = []
    for sg in sigs:
        i = sg["i"]
        if i <= last:
            continue
        s = sg["side"]; tr = trsv(i, K)
        if mode == "align" and not ((s > 0 and tr > 0) or (s < 0 and tr < 0)):
            continue
        if mode == "anti" and not ((s > 0 and tr < 0) or (s < 0 and tr > 0)):
            continue
        e = sg["entry"]; ext = sg["ext"]; gold = bool(sg["gold"]); conf = bool(sg["conf"])
        rr = 2.0 if (gold or conf) else ENG.RR
        if s > 0:
            sl = ext * (1 - ENG.SL_PAD); dist = (e - sl) / e; tp = e * (1 + rr * dist)
        else:
            sl = ext * (1 + ENG.SL_PAD); dist = (sl - e) / e; tp = e * (1 - rr * dist)
        if dist <= 0:
            continue
        win, ej = MA.walk(A, i, s, sl, tp, n); last = ej
        tier = "gold" if gold else ("conf" if conf else ("us" if ENG._us(A[i]) else "normal"))
        rows.append(dict(net=(rr * dist if win else -dist) - MA.FEE, side=s, tier=tier, yr=int(yr[i])))
    return rows


def main():
    X = E.ctx(TF); A = X["A"]; n = X["n"]; yr = X["yr"]
    buy = np.array([float(b.get("buy_vol", 0) or 0) for b in A])
    sell = np.array([float(b.get("sell_vol", 0) or 0) for b in A])
    cbuy = np.concatenate([[0.0], np.cumsum(buy)]); csell = np.concatenate([[0.0], np.cumsum(sell)])

    def trsv(i, K):
        a = max(0, i - K + 1)
        bb = cbuy[i + 1] - cbuy[a]; ss = csell[i + 1] - csell[a]
        return (bb - ss) / (bb + ss) if bb + ss > 0 else 0.0

    sigs = ENG.gen(TF, body_break=True, opp_guard=False, ab2=-0.3, tier_skew=True, r2_relax=True)  # live config

    def cohorts(rows, lab):
        allr = rows
        trd = [r for r in rows if r["tier"] in TRADE]
        nrm = [r for r in rows if r["tier"] == "normal"]
        L = [r for r in rows if r["side"] > 0]; S = [r for r in rows if r["side"] < 0]
        print("  %-6s ALL   %s" % (lab, _fmt(allr)))
        print("         TRADE %s   (gold+blue+us)" % _fmt(trd))
        print("         NORM  %s" % _fmt(nrm))
        print("         LONG  %s | SHORT %s" % (_fmt(L), _fmt(S)))

    print("=" * 96)
    print("15m Engulfing S/R + trailing-flow gate | %d buckets | live config (tier_skew, r2_relax, ab2=-0.3)" % n)
    print("  ALIGN = flow with side (momentum tailwind) | ANTI = flow against | BASE = unchanged | non-overlap")
    print("=" * 96)
    base = run(sigs, A, n, yr, trsv, 0, "base")
    print("[BASELINE - no flow gate]")
    cohorts(base, "base")
    for K in KS:
        print("-" * 96)
        print("[K=%d buckets ~= %dh flow]" % (K, K // 4))
        cohorts(run(sigs, A, n, yr, trsv, K, "align"), "align")
        cohorts(run(sigs, A, n, yr, trsv, K, "anti"), "anti")

    # ---- robustness of the winning cell: K=12 align, tradeable cohort by YEAR and SIDE ----
    print("=" * 96)
    print("ROBUSTNESS  K=12 align  TRADEABLE cohort (gold+blue+us) split by year & side")
    print("=" * 96)
    a12 = [r for r in run(sigs, A, n, yr, trsv, 12, "align") if r["tier"] in TRADE]
    for lab, f in (("TRADE all", lambda r: True),
                   ("  2025", lambda r: r["yr"] == 2025), ("  2026", lambda r: r["yr"] == 2026),
                   ("  LONG", lambda r: r["side"] > 0), ("  SHORT", lambda r: r["side"] < 0),
                   ("  LONG 2025", lambda r: r["side"] > 0 and r["yr"] == 2025),
                   ("  LONG 2026", lambda r: r["side"] > 0 and r["yr"] == 2026),
                   ("  SHORT 2025", lambda r: r["side"] < 0 and r["yr"] == 2025),
                   ("  SHORT 2026", lambda r: r["side"] < 0 and r["yr"] == 2026)):
        print("  %-12s %s" % (lab, _fmt([r for r in a12 if f(r)])))


if __name__ == "__main__":
    main()
