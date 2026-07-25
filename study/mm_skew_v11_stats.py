"""MMXSKEW v1.1 — FULL STATS of the CURRENT frozen gate (all three 2026-07-24 freezes) on ALL available data.

Gate (mirrors app/mmxskew_detect.detect's v11 flag exactly):
  BASE       close>open & skew>0 & spread >= +35        (SHORT: close<open & skew<0 & spread <= -35)
  + DELTA    LONG 0 < delta <= 15                       (SHORT delta < 0)
  + MOMENTUM LONG spread(i) > spread(i-1)               (SHORT spread(i) < spread(i-1))
  + ABSORB   A_h2 < 0  (both sides, FAIL-CLOSED)
  exit       SL 0.1% beyond the bucket extreme, TP = RR x SL, SL-first on a same-bar tie.

All stats on the canonical taken() NON-OVERLAP basis (independent per-signal evaluation inflates n and
significance). Net is fee-in at 0.08% round-trip. Account sim uses the house convention: $200k, 10% margin at
10x leverage => notional = balance, one position at a time, compounding.

Run: python study/mm_skew_v11_stats.py
"""
from __future__ import annotations
import os, sys, math, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from app import absorption as ABS
import study.mm_skew_rr_sweep as RR
import study.mm_skew_strategy as S
from study.mm_skew_v11_secondhalf import build, binom_ge, FEE

RRS = (1.0, 1.5)


def funnel(A, first):
    """Every stage of the gate, so the attrition is visible."""
    base = delta = mom = full = 0
    sigs = []
    for i in range(max(first, 1), len(A) - 1):
        b = A[i]
        if b.get("sk") is None:
            continue
        d = b["delta"]
        if b["up"] and b["sk"] > 0 and b["spread"] >= 35:
            s = 1
        elif b["dn"] and b["sk"] < 0 and b["spread"] <= -35:
            s = -1
        else:
            continue
        base += 1
        if not ((0.0 < d <= 15.0) if s > 0 else (d < 0.0)):
            continue
        delta += 1
        if not ((b["spread"] - A[i - 1]["spread"]) * s > 0.0):
            continue
        mom += 1
        try:
            a2 = ABS.absorption_halves(A, i)[1]
        except Exception:
            a2 = None
        if a2 is None or a2 >= 0.0:
            continue
        full += 1
        sigs.append(dict(i=i, side=s, t=float(b.get("start_time", 0))))
    return sigs, (base, delta, mom, full)


def taken(A, sigs, rr):
    last = -1; out = []
    for sg in sigs:
        if sg["i"] <= last:
            continue
        res = RR.simulate_rr(A, sg["i"], sg["side"], rr, "sl")
        if res is None:
            continue
        out.append(dict(side=sg["side"], win=(res[0] == "TP"), net=res[1] - FEE, t=sg["t"]))
        last = res[2]
    return sorted(out, key=lambda z: z["t"])


def block(rows, be, label):
    n = len(rows)
    if n == 0:
        print("  %-12s n=0" % label); return
    w = sum(1 for r in rows if r["win"])
    net = np.array([r["net"] for r in rows])
    tot = (np.prod(1 + net) - 1) * 100
    gains = net[net > 0].sum(); losses = -net[net < 0].sum()
    pf = (gains / losses) if losses > 0 else float("inf")
    path = np.cumprod(1 + net); peak = np.maximum.accumulate(path)
    dd = float(np.max((peak - path) / peak)) * 100
    p = binom_ge(w, n, be)
    print("  %-12s n=%3d  W/L %3d/%-3d  win %5.1f%% (BE %.0f%%)  net %+7.1f%%  mean %+.3f%%  PF %4.2f  maxDD %4.1f%%  P=%.3f"
          % (label, n, w, n - w, 100.0 * w / n, be * 100, tot, net.mean() * 100, pf, dd, p))


def account(rows):
    bal = S.BAL0; peak = bal; dd = 0.0
    for r in rows:
        bal += S.POS_FRAC * bal * S.LEV * r["net"]
        peak = max(peak, bal); dd = max(dd, (peak - bal) / peak if peak > 0 else 0.0)
    return bal, (bal / S.BAL0 - 1) * 100, dd * 100


def main():
    A, first = build()
    sigs, (nb, nd, nm, nf) = funnel(A, first)
    t0 = min(s["t"] for s in sigs); t1 = max(s["t"] for s in sigs)
    span = (A[-1]["start_time"] - A[first]["start_time"]) / 86400.0
    nl = sum(1 for s in sigs if s["side"] > 0)
    print("=" * 104)
    print("MMXSKEW v1.1 — CURRENT FROZEN GATE (base + delta + eff-agg momentum + A_h2<0)")
    print("=" * 104)
    print("data: %d mature 1h buckets, %.1f days (%s -> %s UTC)"
          % (len(A) - first, span,
             dt.datetime.utcfromtimestamp(A[first]["start_time"]).strftime("%Y-%m-%d"),
             dt.datetime.utcfromtimestamp(A[-1]["start_time"]).strftime("%Y-%m-%d")))
    print("signal funnel:  base(spread gate) %d  ->  +delta %d  ->  +momentum %d  ->  +A_h2<0 %d   (%dL / %dS)"
          % (nb, nd, nm, nf, nl, nf - nl))
    print("  first signal %s   last signal %s"
          % (dt.datetime.utcfromtimestamp(t0).strftime("%Y-%m-%d %H:%M"),
             dt.datetime.utcfromtimestamp(t1).strftime("%Y-%m-%d %H:%M")))
    print()

    for rr in RRS:
        be = 1.0 / (1 + rr)
        T = taken(A, sigs, rr)
        print("-" * 104)
        print("RR 1:%.1f   (SL 0.1%% beyond the extreme, TP = %.1f x SL, fee 0.08%% round-trip)" % (rr, rr))
        print("-" * 104)
        block(T, be, "ALL")
        block([r for r in T if r["side"] > 0], be, "LONG")
        block([r for r in T if r["side"] < 0], be, "SHORT")
        n = len(T)
        if n >= 4:
            mid = n // 2
            block(T[:mid], be, "  H1 (1st half)")
            block(T[mid:], be, "  H2 (2nd half)")
        bal, ret, dd = account(T)
        print("  account sim ($%s, 10%% margin x10 lev, compounding, 1 position at a time):"
              % f"{S.BAL0:,.0f}")
        print("      final $%s   return %+.1f%%   maxDD %.1f%%" % (f"{bal:,.0f}", ret, dd))
        print()

    print("=" * 104)
    print("CAVEATS: in-sample, ONE ~%.0f-day regime, forward n=0. Small n (see above) — the momentum leg's" % span)
    print("  in-sample gain was NOT significant (p=0.357 on 5 trades) and A_h2's evidence rests on 1m-")
    print("  RECONSTRUCTED price_h1 that is UNVERIFIED (the live gate uses the exact daemon field). Magnitudes")
    print("  WILL regress. The forward tape is the only real test.")
    print("=" * 104)


if __name__ == "__main__":
    main()
