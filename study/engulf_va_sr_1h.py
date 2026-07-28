"""ENGULF reversal at support/resistance = VA edge OR S/R indicator (union), with an optional VA+SR confluence
TP bump. tf-parametrized, importable API for the verification gauntlet.

Signal (LONG; SHORT mirrors): c1 bearish, c2 bullish, c2 close>c1 high, both non-doji, AND c2 touches-or-opens at
support where support = (a) the causal prev-day VA edge VAL, OR (b) an S/R-indicator support ZONE (fractal pivot
low candle's high-low range) that c2 does NOT close inside (close beyond the zone = clean rejection).
Exit: SL 0.1% beyond c2 extreme; TP = 1:1.2 the stop; if conf2 AND the signal is VA+SR confluence -> TP = 1:2.
VP-trend bias (use_bias): Day-2 vs Day-1 full-day VA -> long-only / short-only / both.
$200k @ 10% margin x10 = 100% notional, compounded, fee 0.08%.

API:  analyze(tf, conf2=False, use_bias=False) -> rows[{net, side, yr, src, dist, t}]  (t = signal bar start_time)
CLI:  python study/engulf_va_sr_1h.py [tf]
"""
from __future__ import annotations
import os, sys, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import study.signal_search_lib as L
import study.mom_absorb_1h as MA
from app import support_resistance as SR
from study.va_bias_1h_strategies import daily_va, _trend

VA_BAND = 0.0015; SL_PAD = 0.001; RR = 1.2; K = SR.SR_PIVOT_K; R_EASY = -0.75
_CTX = {}


def ctx(tf):
    if tf not in _CTX:
        F = L.load_features(tf); A = F["A"]; LEV = SR.detect(A, K)
        _CTX[tf] = dict(F=F, A=A, n=F["n"], d=F["dir"], o=F["o"], c=F["c"], h=F["h"], l=F["l"], yr=F["year"],
                        absA=F["absA"], sk=F["sk"], SUP=[x for x in LEV if x["kind"] == "S"],
                        RES=[x for x in LEV if x["kind"] == "R"], dayva=daily_va(A)[0], nlev=len(LEV))
    return _CTX[tf]


def _nd(X, i):
    c = X["c"]; o = X["o"]; h = X["h"]; l = X["l"]
    b = abs(c[i] - o[i]); return b > (h[i] - max(o[i], c[i])) and b > (min(o[i], c[i]) - l[i])


def _va_ref(X, i):
    va = X["dayva"].get(L._dtu(X["A"][i]["start_time"]).date() - dt.timedelta(days=1))
    return (va["vah"], va["val"]) if va else (None, None)


def _active(X, levs, i):
    return [x for x in levs if x["i0"] + K <= i and (x["i1"] is None or x["i1"] > i)]


def _at_va(X, i, level):
    o = X["o"]; h = X["h"]; l = X["l"]
    return level is not None and ((l[i] <= level <= h[i]) or abs(o[i] - level) <= VA_BAND * o[i])


def _at_sr(X, i, levs, support):
    """If candle i triggers off an active S/R zone (touch + close beyond), return the zone EDGE the SL sits beyond
    (support bottom zlo for longs / resistance top zhi for shorts; farthest edge if several); else None."""
    o = X["o"]; c = X["c"]; h = X["h"]; l = X["l"]; edge = None
    for x in _active(X, levs, i):
        i0 = x["i0"]; zlo = min(l[i0], h[i0]); zhi = max(l[i0], h[i0])
        touch = (l[i] <= zhi and h[i] >= zlo) or (zlo <= o[i] <= zhi)
        if touch and (c[i] > zhi if support else c[i] < zlo):
            e = zlo if support else zhi
            edge = e if edge is None else (min(edge, e) if support else max(edge, e))
    return edge


def _touches_sr(X, i, levs):
    """Does candle i's range (or open) overlap ANY active S/R-indicator zone in levs? (touch only, no close rule)."""
    o = X["o"]; h = X["h"]; l = X["l"]
    for x in _active(X, levs, i):
        i0 = x["i0"]; zlo = min(l[i0], h[i0]); zhi = max(l[i0], h[i0])
        if (l[i] <= zhi and h[i] >= zlo) or (zlo <= o[i] <= zhi):
            return True
    return False


def _sr_overlap(X, i):
    """True if any active S/R support zone overlaps any active resistance zone in price (contradictory S/R)."""
    h = X["h"]; l = X["l"]
    sup = [(min(l[x["i0"]], h[x["i0"]]), max(l[x["i0"]], h[x["i0"]])) for x in _active(X, X["SUP"], i)]
    res = [(min(l[x["i0"]], h[x["i0"]]), max(l[x["i0"]], h[x["i0"]])) for x in _active(X, X["RES"], i)]
    for slo, shi in sup:
        for rlo, rhi in res:
            if slo <= rhi and shi >= rlo:
                return True
    return False


def _bias(X, i):
    d3 = L._dtu(X["A"][i]["start_time"]).date()
    return _trend(X["dayva"].get(d3 - dt.timedelta(days=2)), X["dayva"].get(d3 - dt.timedelta(days=1)))


def _ok(b, side):
    return b == "both" or (b == "long" and side > 0) or (b == "short" and side < 0)


def gen(tf, use_bias=False):
    X = ctx(tf); d = X["d"]; c = X["c"]; h = X["h"]; o = X["o"]; l = X["l"]; n = X["n"]; sigs = []
    absA = X["absA"]; sk = X["sk"]
    for i in range(1, n):
        if not _nd(X, i):                                          # c2 must ALWAYS be non-doji
            continue
        if _sr_overlap(X, i):                                      # ADD#1: contradictory (support & resistance overlap) -> skip
            continue
        vah, val = _va_ref(X, i); bi = _bias(X, i) if use_bias else "both"
        if d[i] == 1 and c[i] > h[i - 1] and _ok(bi, 1):           # LONG: c2 bullish, closes above c1 high
            if _touches_sr(X, i, X["RES"]):                        # ADD#2: bullish signal touching a resistance zone -> skip
                continue
            relax = absA[i] <= R_EASY and sk[i] > 0               # relaxed c1 (doji/same-dir) needs R-easy AND skew>0
            if not (relax or (d[i - 1] == -1 and _nd(X, i - 1))):  # else strict c1 = bearish + non-doji
                continue
            va = _at_va(X, i, val); sr = _at_sr(X, i, X["SUP"], True)
            if va or sr is not None:                              # SR trigger -> SL 0.1% below the support zone; else candle low
                sigs.append(dict(i=i, side=1, entry=float(c[i]), ext=(float(sr) if sr is not None else float(l[i])),
                                 src=("VA" if va else "") + ("SR" if sr is not None else "")))
        elif d[i] == -1 and c[i] < o[i - 1] and _ok(bi, -1):       # SHORT: c2 bearish, closes below c1 open
            if _touches_sr(X, i, X["SUP"]):                        # ADD#2: bearish signal touching a support zone -> skip
                continue
            relax = absA[i] <= R_EASY and sk[i] < 0               # relaxed c1 needs R-easy AND skew<0
            if not (relax or (d[i - 1] == 1 and _nd(X, i - 1))):   # else strict c1 = bullish + non-doji
                continue
            va = _at_va(X, i, vah); sr = _at_sr(X, i, X["RES"], False)
            if va or sr is not None:                              # SR trigger -> SL 0.1% above the resistance zone; else candle high
                sigs.append(dict(i=i, side=-1, entry=float(c[i]), ext=(float(sr) if sr is not None else float(h[i])),
                                 src=("VA" if va else "") + ("SR" if sr is not None else "")))
    return sigs


def analyze(tf, conf2=False, use_bias=False):
    X = ctx(tf); A = X["A"]; n = X["n"]; yr = X["yr"]; last = -1; rows = []
    for sg in gen(tf, use_bias):
        i = sg["i"]
        if i <= last:
            continue
        e = sg["entry"]; s = sg["side"]; ext = sg["ext"]
        rr = 2.0 if (conf2 and sg["src"] == "VASR") else RR
        if s > 0:
            sl = ext * (1 - SL_PAD); dist = (e - sl) / e; tp = e * (1 + rr * dist)
        else:
            sl = ext * (1 + SL_PAD); dist = (sl - e) / e; tp = e * (1 - rr * dist)
        win, ej = MA.walk(A, i, s, sl, tp, n); last = ej
        rows.append(dict(net=(rr * dist if win else -dist) - MA.FEE, side=s, yr=int(yr[i]),
                         src=sg["src"], dist=dist, t=float(A[i]["start_time"])))
    return rows


def report(label, rows):
    m = len(rows)
    if m == 0:
        print("  %-16s n=0" % label); return
    nt = np.array([r["net"] for r in rows]); w = 100.0 * (nt > 0).sum() / m
    tot = (np.prod(1 + nt) - 1) * 100
    gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum(); pf = (gg / ll) if ll > 0 else float("inf")
    bal = MA.account(list(nt))
    print("  %-16s n=%4d  win %5.1f%%  net %+7.1f%%  PF %.2f  avgSL %.2f%%  END $%9.0f  P&L $%+8.0f"
          % (label, m, w, tot, pf, np.mean([r["dist"] for r in rows]) * 100, bal, bal - MA.B0))


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "1h"
    X = ctx(tf)
    print("=" * 104)
    print("%s ENGULF at (VA edge OR S/R-indicator zone)  |  %d %s buckets  |  %d S/R levels" % (tf, X["n"], tf, X["nlev"]))
    print("  Entry c2 close, SL 0.1%% beyond c2 extreme. Causal prev-day VA. Account $%.0f @ 10%%x10, fee %.2f%%." % (MA.B0, MA.FEE * 100))
    print("=" * 104)
    for conf2, lbl in ((False, "FIXED 1:1.2 all (no-bias version)"), (True, "THIS VERSION: VA+SR confluence -> 1:2, else 1:1.2")):
        rows = analyze(tf, conf2)
        print("--- %s   |   %d taken ---" % (lbl, len(rows)))
        for lab, f in (("ALL", lambda r: True), ("LONG", lambda r: r["side"] > 0), ("SHORT", lambda r: r["side"] < 0),
                       ("2025", lambda r: r["yr"] == 2025), ("2026", lambda r: r["yr"] == 2026),
                       ("  both VA+SR", lambda r: r["src"] == "VASR")):
            report(lab, [r for r in rows if f(r)])
        print()


if __name__ == "__main__":
    main()
