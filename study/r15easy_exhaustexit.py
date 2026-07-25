"""15mReasy — EXHAUSTION-CONFIRMATION exit (15m ONLY).

ENTRY : candle i, when  A(i) <= -0.75 (R-easy)  AND  skew(i) agrees  AND  candle i-1 same direction as i.
STOP  : 0.1% beyond candle i's extreme (low*0.999 long / high*1.001 short).
EXIT/TP: NO fixed target. Close the trade at the CLOSE of an OPPOSITE-direction candle that immediately FOLLOWS a
        SAME-direction HEAVY candle. i.e. exit at candle j (dir = -side) when candle j-1 was (dir = +side AND
        A >= +0.75 heavy). "the move printed exhaustion (same-dir buyers/sellers absorbed) and the next candle
        turned against us." SL is checked every bar first (conservative on a same-bar tie).
        If neither SL nor the exhaustion pattern ever prints -> trade stays OPEN to the end of data.

heavy = A >= +0.75 (module label). A = absorption()[0], POSITIVE = that candle's aggressor was ABSORBED.

Because there is no target, outcome is SL / EXHAUST-EXIT (variable) / OPEN, and 'win' = net>0. Reported per side,
both CHAIN (tradeable) and CONTROLLED (n constant). A plain 1:1 TP on the SAME entries is shown for contrast.

Run: python study/r15easy_exhaustexit.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from app import absorption as ABS
from study.mm_skew_v11_tf import build

FEE = 0.0008
R_EASY = -0.75
HEAVY = 0.75
SL_BUF = 0.001


def prep(A, first):
    Aval = [None] * len(A); dirn = [0] * len(A)
    for i in range(len(A)):
        try:
            Aval[i] = ABS.absorption(A, i)[0]
        except Exception:
            Aval[i] = None
        dirn[i] = 1 if A[i]["up"] else (-1 if A[i]["dn"] else 0)

    def skew_ok(i, s):
        sk = A[i].get("sk")
        return sk is not None and ((sk > 0) if s > 0 else (sk < 0))

    sigs = []
    for i in range(max(first, 1), len(A) - 1):
        s = dirn[i]
        if s == 0 or Aval[i] is None or Aval[i] > R_EASY or not skew_ok(i, s):
            continue
        if dirn[i - 1] != s:                       # previous candle same direction
            continue
        sigs.append(dict(i=i, side=s, t=float(A[i].get("start_time", 0))))
    return sigs, Aval, dirn


def sim_exhaust(A, Aval, dirn, i, side):
    """SL 0.1% beyond entry extreme; exit at close of an opposite candle following a same-dir HEAVY candle."""
    e = A[i]["c"]
    sl = A[i]["l"] * (1 - SL_BUF) if side > 0 else A[i]["h"] * (1 + SL_BUF)
    if (e - sl) * side <= 0:
        return None
    slf = abs(e - sl) / e
    for j in range(i + 1, len(A)):
        hi, lo = A[j]["h"], A[j]["l"]
        if (lo <= sl) if side > 0 else (hi >= sl):          # SL first
            return ("SL", -slf, j)
        # exhaustion-confirmation: j is opposite, j-1 (after entry) was same-dir + heavy
        p = j - 1
        if p > i and dirn[j] == -side and dirn[p] == side and Aval[p] is not None and Aval[p] >= HEAVY:
            return ("EXHAUST", (A[j]["c"] - e) / e * side, j)
    return ("OPEN", (A[-1]["c"] - e) / e * side, len(A) - 1)


def sim_tp(A, i, side, rr=1.0):
    e = A[i]["c"]
    sl = A[i]["l"] * (1 - SL_BUF) if side > 0 else A[i]["h"] * (1 + SL_BUF)
    sld = (e - sl) if side > 0 else (sl - e)
    if sld <= 0:
        return None
    slf = sld / e; tp = e + rr * sld * side
    for j in range(i + 1, len(A)):
        hi, lo = A[j]["h"], A[j]["l"]
        if (lo <= sl) if side > 0 else (hi >= sl):
            return ("SL", -slf, j)
        if (hi >= tp) if side > 0 else (lo <= tp):
            return ("TP", rr * slf, j)
    return ("OPEN", (A[-1]["c"] - e) / e * side, len(A) - 1)


def evaluate(A, prep_data, sigs, chain, simfn):
    Aval, dirn = prep_data
    last = -1; out = []
    for sg in sigs:
        if chain and sg["i"] <= last:
            continue
        r = simfn(sg["i"], sg["side"])
        if r is None:
            continue
        out.append(dict(side=sg["side"], out=r[0], gross=r[1], net=r[1] - FEE)); last = r[2]
    return out


def table(rows, label):
    print("    %s" % label)
    print("      %-7s %5s | %5s %8s %5s | %6s %6s | %7s %8s %10s %10s"
          % ("side", "n", "SL", "exit/tp", "open", "WIN", "LOSS", "win%", "net%", "gross/tr", "net/tr"))
    for sd, nm in ((None, "ALL"), (1, "LONG"), (-1, "SHORT")):
        rs = [r for r in rows if sd is None or r["side"] == sd]
        if not rs:
            print("      %-7s n=0" % nm); continue
        n = len(rs)
        sl = sum(1 for r in rs if r["out"] == "SL")
        ex = sum(1 for r in rs if r["out"] in ("EXHAUST", "TP"))
        op = sum(1 for r in rs if r["out"] == "OPEN")
        w = sum(1 for r in rs if r["net"] > 0)
        g = np.array([r["gross"] for r in rs]); nt = np.array([r["net"] for r in rs])
        print("      %-7s %5d | %5d %8d %5d | %6d %6d | %6.1f%% %+7.1f%% %+9.4f%% %+9.4f%%"
              % (nm, n, sl, ex, op, w, n - w, 100.0 * w / n, (np.prod(1 + nt) - 1) * 100,
                 g.mean() * 100, nt.mean() * 100))


def main():
    A, first, _ = build("15m")
    sigs, Aval, dirn = prep(A, first)
    nl = sum(1 for s in sigs if s["side"] > 0)
    print("=" * 110)
    print("15mReasy + EXHAUSTION-CONFIRMATION EXIT (15m)  |  A<=-0.75 + skew + prev-same-dir  |  SL 0.1%%, no fixed TP")
    print("=" * 110)
    print("  signals: %d (%dL/%dS)   exit = opposite candle following a same-dir heavy(A>=%.2f) candle\n"
          % (len(sigs), nl, len(sigs) - nl, HEAVY))
    for chain, vl in ((True, "CHAIN (tradeable)"), (False, "CONTROLLED (n constant)")):
        print("-" * 110); print(vl); print("-" * 110)
        table(evaluate(A, (Aval, dirn), sigs, chain, lambda i, s: sim_exhaust(A, Aval, dirn, i, s)),
              ">>> exhaustion-confirmation exit:")
        table(evaluate(A, (Aval, dirn), sigs, chain, lambda i, s: sim_tp(A, i, s, 1.0)),
              "for contrast: plain TP 1:1.0:")
        print()


if __name__ == "__main__":
    main()
