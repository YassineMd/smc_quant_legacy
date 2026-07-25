"""15mReasy + SIGNAL-DEATH EXIT — exit early when absorption turns HEAVY.

Setup   : LONG = bullish bucket AND A <= -0.75 ; SHORT = bearish bucket AND A <= -0.75.
Stop    : 0.1% beyond the ENTRY bucket's extreme (low*0.999 long / high*1.001 short).
Target  : 1.5 x the stop distance  ***OR*** close early at the CLOSE of the first later bucket that prints
          HEAVY absorption — the "the easy move is over" exit.

`A` (app.absorption.absorption()[0]) is oriented POSITIVE = that bucket's AGGRESSOR was ABSORBED. Labels:
ABSORBED >= +1.5 | heavy >= +0.75 | light <= -0.75 | EASY <= -1.5.  "heavy prints" = A >= +0.75.

WHOSE aggressor matters, so two readings are tested:
    ANY      — any bucket with A >= 0.75, whichever side was aggressing
    ADVERSE  — A >= 0.75 AND the absorbed aggressor is OUR side (long: buyers absorbed) => the exit that is
               actually directionally bad for the position. Strictly the more meaningful one.
Also swept: the heavy threshold (0.75 vs 1.5 = the ABSORBED label).

Bar priority: SL first (conservative on a same-bar tie), then TP, then the heavy check at that bucket's CLOSE.

BOTH VIEWS ARE REPORTED, because they disagree and the disagreement is informative:
  * CHAIN      — taken() one-at-a-time non-overlap. Tradeable, but its trade COUNT changes with the exit rule,
                 so cells are not the same trades (this is what made the wide-stop sweep look fake).
  * CONTROLLED — every signal evaluated independently, n identical across rows, so the only thing that varies is
                 the EXIT RULE. Not a P&L claim (overlaps aren't tradeable) — a clean comparison.

Run: python study/r15easy_heavyexit.py [tf]      (default 15m)
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from app import absorption as ABS
import study.mm_skew_strategy as S
from study.mm_skew_v11_tf import build

FEE = 0.0008
R_EASY = -0.75
SL_BUF = 0.001
RR = 1.5


def prep(A, first):
    """Absorption (value, aggressor side) for EVERY bucket, once; plus the signal list."""
    Aval = [None] * len(A); Aside = [0] * len(A)
    for i in range(len(A)):
        try:
            v, _r, sd = ABS.absorption(A, i)
        except Exception:
            v, sd = None, 0
        Aval[i] = v; Aside[i] = sd
    sigs = []
    for i in range(max(first, 1), len(A) - 1):
        b = A[i]
        s = 1 if b["up"] else (-1 if b["dn"] else 0)
        if s == 0 or Aval[i] is None or Aval[i] > R_EASY:
            continue
        sigs.append(dict(i=i, side=s, t=float(b.get("start_time", 0))))
    return sigs, Aval, Aside


def sim(A, Aval, Aside, i, side, rr, heavy=None, thr=0.75):
    """heavy: None | 'any' | 'adverse'. Returns (outcome, net_fraction, exit_idx)."""
    e = A[i]["c"]
    sl = A[i]["l"] * (1 - SL_BUF) if side > 0 else A[i]["h"] * (1 + SL_BUF)
    sld = (e - sl) if side > 0 else (sl - e)
    if sld <= 0:
        return None
    slf = sld / e
    tp = e + rr * sld * side
    for j in range(i + 1, len(A)):
        hi, lo = A[j]["h"], A[j]["l"]
        if (lo <= sl) if side > 0 else (hi >= sl):
            return ("SL", -slf, j)
        if (hi >= tp) if side > 0 else (lo <= tp):
            return ("TP", rr * slf, j)
        if heavy is not None and Aval[j] is not None and Aval[j] >= thr:
            if heavy == "any" or Aside[j] == side:          # 'adverse' = OUR aggressor got absorbed
                return ("HEAVY", (A[j]["c"] - e) / e * side, j)
    return ("OPEN", (A[-1]["c"] - e) / e * side, len(A) - 1)


CELLS = (("baseline  TP only", None, 0.75),
         ("exit ANY heavy>=0.75", "any", 0.75),
         ("exit ADVERSE heavy>=0.75", "adverse", 0.75),
         ("exit ANY absorbed>=1.5", "any", 1.5),
         ("exit ADVERSE absorbed>=1.5", "adverse", 1.5))


def stats(rows, be_ref):
    n = len(rows)
    if n == 0:
        return None
    net = np.array([r["net"] for r in rows])
    w = sum(1 for r in rows if r["net"] > 0)
    tot = (np.prod(1 + net) - 1) * 100
    g = net[net > 0].sum(); l = -net[net < 0].sum()
    pf = (g / l) if l > 0 else float("inf")
    path = np.cumprod(1 + net); peak = np.maximum.accumulate(path)
    dd = float(np.max((peak - path) / peak)) * 100
    bal = S.BAL0
    for r in rows:
        bal += S.POS_FRAC * bal * S.LEV * r["net"]
    return dict(n=n, win=100.0 * w / n, net=tot, mean=net.mean() * 100, pf=pf, dd=dd, bal=bal)


def show(lbl, st, extra=""):
    if st is None:
        print("  %-30s n=0" % lbl); return
    print("  %-30s n=%4d  profitable %5.1f%%  net %+7.1f%%  mean %+.3f%%  PF %4.2f  DD %4.1f%%  $%s%s"
          % (lbl, st["n"], st["win"], st["net"], st["mean"], st["pf"], st["dd"], f"{st['bal']:,.0f}", extra))


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "15m"
    A, first, _ = build(tf)
    sigs, Aval, Aside = prep(A, first)
    print("=" * 116)
    print("15mReasy + HEAVY-ABSORPTION EXIT on %s   |  dir + A<=%.2f, SL 0.1%% beyond extreme, TP %.1fR or heavy"
          % (tf.upper(), R_EASY, RR))
    print("=" * 116)
    print("  signals: %d   ('profitable %%' = share of trades with net>0, since an early exit is neither TP nor SL)\n"
          % len(sigs))

    print("-" * 116)
    print("CHAIN view (one-at-a-time non-overlap — tradeable, but trade COUNT varies with the rule)")
    print("-" * 116)
    for lbl, hv, thr in CELLS:
        last = -1; rows = []; nh = 0
        for sg in sigs:
            if sg["i"] <= last:
                continue
            r = sim(A, Aval, Aside, sg["i"], sg["side"], RR, hv, thr)
            if r is None:
                continue
            rows.append(dict(net=r[1] - FEE)); nh += (r[0] == "HEAVY"); last = r[2]
        show(lbl, stats(rows, None), "  [%d early exits]" % nh)

    print()
    print("-" * 116)
    print("CONTROLLED view (every signal independently — n IDENTICAL, so only the EXIT RULE varies)")
    print("-" * 116)
    for lbl, hv, thr in CELLS:
        rows = []; nh = 0; outc = {}
        for sg in sigs:
            r = sim(A, Aval, Aside, sg["i"], sg["side"], RR, hv, thr)
            if r is None:
                continue
            rows.append(dict(net=r[1] - FEE)); nh += (r[0] == "HEAVY")
            outc[r[0]] = outc.get(r[0], 0) + 1
        show(lbl, stats(rows, None), "  [%d early exits]  %s" % (nh, outc))


if __name__ == "__main__":
    main()
