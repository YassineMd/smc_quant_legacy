"""15mReasy (direction + A <= -0.75) — STOP-DISTANCE SWEEP.

Setup:  LONG = bullish bucket AND A <= -0.75 ; SHORT = bearish bucket AND A <= -0.75.   (eff-agg leg DROPPED —
it was monotone harmful, see study/r15easy.py.)  TP is always RR x the stop distance, SL-first on a same-bar tie.

WHY THIS TEST: on 15m the fee (0.08% round-trip) is a large fraction of a small stop, so the FEE-ADJUSTED
break-even win rate is
        w* = (fee + s) / (s * (1 + RR)),   s = stop distance as a fraction of entry
which FALLS as the stop widens. At s=0.35% / RR1:1 the bar is 61.4%; at s=1.0% it is 54.0%. The setup wins
~56% with a 0.1%-beyond-the-extreme stop, i.e. it is ~2pp short of its bar — so the question is whether a wider
stop lowers the bar faster than it lowers the win rate. In a pure random walk a proportional bracket is
scale-invariant (win rate unchanged, fee drag shrinks => strictly better); any DEPARTURE from that is real
market structure, which is exactly what this measures.

Two stop families:
  STRUCTURAL  SL = bucket extreme -/+ buf   (buf = 0.1 / 0.2 / 0.3 / 0.5 / 1.0 %)  — the current design
  FIXED       SL = entry -/+ pct           (pct = 0.4 / 0.6 / 0.8 / 1.0 / 1.5 / 2.0 %)

Every row reports its OWN median stop and its OWN fee-adjusted break-even, so win% is always judged against the
bar that actually applies to it. taken() non-overlap; each config re-links its own chain.

Run: python study/r15easy_stops.py [tf]      (default 15m)
"""
from __future__ import annotations
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from app import absorption as ABS
import study.mm_skew_strategy as S
from study.mm_skew_v11_tf import build

FEE = 0.0008
R_EASY = -0.75
RRS = (1.0, 1.5)


def scan(A, first):
    out = []
    for i in range(max(first, 1), len(A) - 1):
        b = A[i]
        s = 1 if b["up"] else (-1 if b["dn"] else 0)
        if s == 0:
            continue
        try:
            a = ABS.absorption(A, i)[0]
        except Exception:
            a = None
        if a is None or a > R_EASY:
            continue
        out.append(dict(i=i, side=s, t=float(b.get("start_time", 0))))
    return out


def sim(A, i, side, sl_price, rr):
    """First-passage with an ARBITRARY stop price. SL-first on a same-bar tie (conservative)."""
    e = A[i]["c"]
    sld = (e - sl_price) if side > 0 else (sl_price - e)
    if sld <= 0:
        return None
    slf = sld / e
    tp = e + rr * sld * side
    for j in range(i + 1, len(A)):
        hi, lo = A[j]["h"], A[j]["l"]
        htp = (hi >= tp) if side > 0 else (lo <= tp)
        hsl = (lo <= sl_price) if side > 0 else (hi >= sl_price)
        if hsl:                      # SL-first tie-break
            return ("SL", -slf, j, slf)
        if htp:
            return ("TP", rr * slf, j, slf)
    return ("OPEN", (A[-1]["c"] - e) / e * side, len(A) - 1, slf)


def run(A, sigs, rr, stop_fn):
    last = -1; rows = []; slfs = []
    for sg in sigs:
        if sg["i"] <= last:
            continue
        slp = stop_fn(A, sg["i"], sg["side"])
        r = sim(A, sg["i"], sg["side"], slp, rr)
        if r is None:
            continue
        rows.append(dict(side=sg["side"], win=(r[0] == "TP"), net=r[1] - FEE))
        slfs.append(r[3]); last = r[2]
    return rows, (float(np.median(slfs)) if slfs else 0.0)


def report(lbl, rows, s, rr):
    n = len(rows)
    if n == 0:
        print("  %-26s n=0" % lbl); return None
    w = sum(1 for r in rows if r["win"])
    net = np.array([r["net"] for r in rows])
    tot = (np.prod(1 + net) - 1) * 100
    g = net[net > 0].sum(); l = -net[net < 0].sum()
    pf = (g / l) if l > 0 else float("inf")
    path = np.cumprod(1 + net); peak = np.maximum.accumulate(path)
    dd = float(np.max((peak - path) / peak)) * 100
    wbe = (FEE + s) / (s * (1 + rr)) * 100 if s > 0 else float("nan")
    wr = 100.0 * w / n
    bal = S.BAL0
    for r in rows:
        bal += S.POS_FRAC * bal * S.LEV * r["net"]
    print("  %-26s n=%4d  win %5.1f%%  stop %.3f%%  BE* %5.1f%%  gap %+5.1f  net %+7.1f%%  PF %4.2f  DD %4.1f%%  $%s"
          % (lbl, n, wr, s * 100, wbe, wr - wbe, tot, pf, dd, f"{bal:,.0f}"))
    return tot


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "15m"
    A, first, _ = build(tf)
    sigs = scan(A, first)
    print("=" * 118)
    print("15mReasy STOP SWEEP on %s   |  direction + A <= %.2f   |  %d signals   (BE* = fee-adjusted break-even)"
          % (tf.upper(), R_EASY, len(sigs)))
    print("=" * 118)
    for rr in RRS:
        print("-" * 118)
        print("RR 1:%.1f" % rr)
        print("-" * 118)
        print("  STRUCTURAL (stop beyond the bucket extreme):")
        for buf in (0.001, 0.002, 0.003, 0.005, 0.010):
            fn = (lambda A_, i, sd, b=buf: A_[i]["l"] * (1 - b) if sd > 0 else A_[i]["h"] * (1 + b))
            rows, s = run(A, sigs, rr, fn)
            report("extreme %+.1f%%" % (buf * 100), rows, s, rr)
        print("  FIXED (stop a fixed %% from entry):")
        for pct in (0.004, 0.006, 0.008, 0.010, 0.015, 0.020):
            fn = (lambda A_, i, sd, p=pct: A_[i]["c"] * (1 - p) if sd > 0 else A_[i]["c"] * (1 + p))
            rows, s = run(A, sigs, rr, fn)
            report("fixed %.1f%%" % (pct * 100), rows, s, rr)
        print()


if __name__ == "__main__":
    main()
