"""15mReasy — PREV-or-NEXT confirmation with DEFERRED entry (15m ONLY, CAUSAL).

SIGNAL candle i : R(i) <= -0.75 (A<=-0.75) AND skew(i) agrees with the side s.
CONFIRMATION (filter 3, updated):
    PATH A — prev candle same direction (dir(i-1)==s)  -> ENTER at candle i's close (as before).
    PATH B — else if NEXT candle same direction (dir(i+1)==s) AND R(i+1) < 0 (A(i+1)<0)
             -> ENTER at candle i+1's close (deferred one bar). CAUSAL: dir(i+1)/A(i+1) are known at i+1's
             close, which is when you enter — NO look-ahead.
    (if neither confirms -> no trade.)
EXIT : SL 0.1% beyond the ENTRY candle's extreme; TP = RR x SL (RR 1:1.0 and 1:1.5).

Entries are de-duplicated by entry bar and run one-position-at-a-time (chain) + independently (controlled).
Compared against the CURRENT prev-only config. Gross is the binding number on 15m; BE* = fee-adjusted break-even.

Run: python study/r15easy_nextconfirm.py
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
RRS = (1.0, 1.5)


def prep(A, first):
    Aval = [None] * len(A); dirn = [0] * len(A)
    for i in range(len(A)):
        try:
            Aval[i] = ABS.absorption(A, i)[0]
        except Exception:
            Aval[i] = None
        dirn[i] = 1 if A[i]["up"] else (-1 if A[i]["dn"] else 0)

    def sk_ok(i, s):
        sk = A[i].get("sk")
        return sk is not None and ((sk > 0) if s > 0 else (sk < 0))

    prev_only = {}     # entry_bar -> side  (current config)
    combined = {}      # entry_bar -> (side, path)  deduped by entry bar
    for i in range(max(first, 1), len(A) - 2):
        s = dirn[i]
        if s == 0 or Aval[i] is None or Aval[i] > R_EASY or not sk_ok(i, s):
            continue
        if dirn[i - 1] == s:                                   # PATH A: prev confirms -> enter at i
            prev_only.setdefault(i, s)
            combined.setdefault(i, (s, "A"))
        elif dirn[i + 1] == s and Aval[i + 1] is not None and Aval[i + 1] < 0.0:   # PATH B: next confirms + R<0 -> enter at i+1
            combined.setdefault(i + 1, (s, "B"))
    po = [dict(i=k, side=v) for k, v in sorted(prev_only.items())]
    cb = [dict(i=k, side=v[0], path=v[1]) for k, v in sorted(combined.items())]
    return po, cb


def sim(A, i, side, rr):
    e = A[i]["c"]
    sl = A[i]["l"] * (1 - SL_BUF) if side > 0 else A[i]["h"] * (1 + SL_BUF)
    sld = (e - sl) if side > 0 else (sl - e)
    if sld <= 0:
        return None
    slf = sld / e; tp = e + rr * sld * side
    for j in range(i + 1, len(A)):
        hi, lo = A[j]["h"], A[j]["l"]
        if (lo <= sl) if side > 0 else (hi >= sl):
            return ("SL", -slf, j, slf)
        if (hi >= tp) if side > 0 else (lo <= tp):
            return ("TP", rr * slf, j, slf)
    return ("OPEN", (A[-1]["c"] - e) / e * side, len(A) - 1, slf)


def run(A, sigs, rr, chain, only=None):
    last = -1; out = []
    for sg in sigs:
        if chain and sg["i"] <= last:
            continue
        if only is not None and sg.get("path") != only:
            continue
        r = sim(A, sg["i"], sg["side"], rr)
        if r is None:
            continue
        out.append(dict(side=sg["side"], out=r[0], gross=r[1], net=r[1] - FEE, slf=r[3])); last = r[2]
    return out


def stat(rows, rr, label):
    n = len(rows)
    if n == 0:
        print("  %-14s n=0" % label); return
    tp = sum(1 for r in rows if r["out"] == "TP")
    w = sum(1 for r in rows if r["net"] > 0)
    g = np.array([r["gross"] for r in rows]); nt = np.array([r["net"] for r in rows])
    tot = (np.prod(1 + nt) - 1) * 100
    gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum(); pf = (gg / ll) if ll > 0 else float("inf")
    path = np.cumprod(1 + nt); peak = np.maximum.accumulate(path); dd = float(np.max((peak - path) / peak)) * 100
    s = float(np.median([r["slf"] for r in rows])); be = (FEE + s) / (s * (1 + rr)) * 100
    bal = S.BAL0
    for r in rows:
        bal += S.POS_FRAC * bal * S.LEV * r["net"]
    print("  %-14s n=%3d  win %5.1f%% (BE* %.1f%%)  net %+6.1f%%  gross/tr %+.4f%%  net/tr %+.4f%%  PF %4.2f  DD %4.1f%%  $%s"
          % (label, n, 100.0 * w / n, be, tot, g.mean() * 100, nt.mean() * 100, pf, dd, f"{bal:,.0f}"))


def block(A, sigs, rr, tag, only=None):
    for sd, nm in ((None, "ALL"), (1, "LONG"), (-1, "SHORT")):
        rows = [r for r in run(A, sigs, rr, True, only) if sd is None or r["side"] == sd]
        stat(rows, rr, "%s %s" % (tag, nm))


def main():
    A, first, _ = build("15m")
    po, cb = prep(A, first)
    npa = sum(1 for s in cb if s["path"] == "A"); npb = sum(1 for s in cb if s["path"] == "B")
    cl = sum(1 for s in cb if s["side"] > 0)
    print("=" * 122)
    print("15mReasy + prev-OR-next confirmation (DEFERRED entry, CAUSAL)  |  R-easy+skew  |  SL 0.1%%, one-at-a-time")
    print("=" * 122)
    print("  CURRENT prev-only entries : %d" % len(po))
    print("  NEW combined entries      : %d (%dL/%dS)  = %d via PATH A (prev, enter at i) + %d via PATH B (next, enter at i+1)\n"
          % (len(cb), cl, len(cb) - cl, npa, npb))
    for rr in RRS:
        print("-" * 122); print("RR 1:%.1f  (chain / one-at-a-time)" % rr); print("-" * 122)
        block(A, po, rr, "CURRENT(prev)")
        block(A, cb, rr, ">>>combined")
        print("  path split:")
        block(A, cb, rr, "  PATH-A(prev)", only="A")
        block(A, cb, rr, "  PATH-B(next)", only="B")
        print()
    print("CAVEAT: 15m, one ~34-day regime, forward n=0. PATH B defers entry 1 bar (causal). Small n per side.")


if __name__ == "__main__":
    main()
