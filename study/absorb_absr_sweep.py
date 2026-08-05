"""absR sweep on the scale-out stack @ vw>=1, TP1=0.5/TP2=1.0, SL 0.1% beyond candle + BE-trail, swing+swingA+RETR.
Precomputes the (slow) per-bar swing filter ONCE, then sweeps the absorption-R condition cheaply.
Run: python study/absorb_absr_sweep.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
import study.signal_search_lib as L
import study.mom_absorb_1h as MA
from app import engulf1m_detect as E, structure, swing_lvn_detect as SW

rng = np.random.default_rng(20260805)
F = L.load_features("1h")
A = F["A"]; n = F["n"]; absA = F["absA"]; O = F["o"]; C = F["c"]; Hh = F["h"]; Ll = F["l"]
yr = np.array([datetime.fromtimestamp(float(t), tz=timezone.utc).year for t in F["start"]])
delta = F["delta"]                                            # (buy-sell)/vol*100 per candle
FEE = MA.FEE; TP1 = 0.005; TP2 = 0.010; SL_PAD = 0.001; BE = 0.001; VW_MIN = 1.0


def divergent(i):
    """Delta-divergence: bullish candle on NET SELLING (delta<0) / bearish candle on NET BUYING (delta>0)."""
    if C[i] > O[i]:
        return delta[i] < 0
    if C[i] < O[i]:
        return delta[i] > 0
    return False

Harr = [float(b.get("high", 0.0) or 0.0) for b in A]; Larr = [float(b.get("low", 0.0) or 0.0) for b in A]
Carr = [float(b.get("close", b.get("close_price", 0.0)) or 0.0) for b in A]
_thr = SW._adaptive_thr(Harr, Larr, Carr, window=len(Carr))
_piv = sorted(structure._zigzag_confirmed(Harr, Larr, _thr), key=lambda p: p[3])
swing_dir = [0] * n; _pi = 0; _cur = 0
for _i in range(n):
    while _pi < len(_piv) and _piv[_pi][3] <= _i:
        _cur = -1 if _piv[_pi][2] else 1; _pi += 1
    swing_dir[_i] = _cur


def vw(i):
    ut = float(A[i].get("up_ticks", 0.0) or 0.0); dt = float(A[i].get("dn_ticks", 0.0) or 0.0)
    return ((max(ut, dt) / min(ut, dt) - 1.0) * 100.0) if min(ut, dt) > 0 else -1.0


marks = E.detect(A, skip_last=True, absorp=list(absA))
# PRECOMPUTE the swing filter (eff side + swingA pass) once per mark bar — independent of vw/absR
print("precomputing swing filter for %d marks ..." % len(marks))
scache = {}
for m in marks:
    i = m["i"]
    if swing_dir[i] == 0:
        scache[i] = None; continue
    legs = SW.swing_lines(A[:i + 1])
    dev = next((lg for lg in reversed(legs) if lg.get("developing")), None)
    if dev is None:
        scache[i] = None; continue
    a = dev.get("A"); a4 = dev.get("A4")
    ok = not ((a is not None and a > 0) or (a4 is not None and a4 > 0))
    legdir = 1 if dev.get("ends_high") else -1
    eff = -legdir if dev.get("is_retr") else legdir
    scache[i] = (eff, ok)


def scaleout(i, side):
    e = C[i]; sl = Ll[i] * (1 - SL_PAD) if side > 0 else Hh[i] * (1 + SL_PAD)
    if (side > 0 and sl >= e) or (side < 0 and sl <= e):
        return None
    dist = abs(e - sl) / e
    tp1 = e * (1 + TP1) if side > 0 else e * (1 - TP1)
    tp2 = e * (1 + TP2) if side > 0 else e * (1 - TP2)
    be = e * (1 + BE) if side > 0 else e * (1 - BE)
    tp1_bar = None
    for j in range(i + 1, n):
        hi = float(A[j]["h"]); lo = float(A[j]["l"])
        if (lo <= sl) if side > 0 else (hi >= sl):
            return (-dist - FEE), j
        if (hi >= tp1) if side > 0 else (lo <= tp1):
            tp1_bar = j; break
    if tp1_bar is None:
        return (-dist - FEE), n - 1
    runner = BE; ke = tp1_bar
    for k in range(tp1_bar, n):
        hi = float(A[k]["h"]); lo = float(A[k]["l"])
        t2 = (hi >= tp2) if side > 0 else (lo <= tp2)
        if k == tp1_bar:
            if t2:
                runner = TP2; ke = k; break
            continue
        beh = (lo <= be) if side > 0 else (hi >= be)
        if beh:
            runner = BE; ke = k; break
        if t2:
            runner = TP2; ke = k; break
        ke = k
    return (0.5 * TP1 + 0.5 * runner - FEE), ke


def run(abs_pred):
    rows = []; last = -1
    for m in marks:
        i = m["i"]
        if vw(i) < VW_MIN or not abs_pred(i):
            continue
        sc = scache.get(i)
        if sc is None:
            continue
        eff, ok = sc
        if not ok or m["side"] != eff:
            continue
        if i <= last:
            continue
        r = scaleout(i, m["side"])
        if r is None:
            continue
        net, ej = r; last = ej
        rows.append(dict(net=net, side=m["side"], yr=int(yr[i])))
    return rows


def stat(rows):
    nt = np.array([r["net"] for r in rows])
    if len(nt) == 0:
        return "n=0"
    w = 100.0 * (nt > 0).mean(); tot = (np.prod(1 + nt) - 1) * 100
    gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum(); pf = (gg / ll) if ll > 0 else 9.9
    m2 = np.array([rng.choice(nt, size=len(nt), replace=True).mean() for _ in range(4000)]) * 100
    lo, hi = np.percentile(m2, [2.5, 97.5])
    y25 = [r["net"] for r in rows if r["yr"] == 2025]; y26 = [r["net"] for r in rows if r["yr"] == 2026]
    t25 = (np.prod(1 + np.array(y25)) - 1) * 100 if y25 else 0.0
    t26 = (np.prod(1 + np.array(y26)) - 1) * 100 if y26 else 0.0
    sig = "clears 0" if lo > 0 else ("SIG NEG" if hi < 0 else "~0")
    return "n=%4d win %4.1f%% net %+6.1f%% PF %.2f | 2025 %+5.1f 2026 %+5.1f | CI[%+.3f,%+.3f] %s" % (
        len(rows), w, tot, pf, t25, t26, lo, hi, sig)


print("=" * 116)
print("absR SWEEP | vw>=1, TP1 0.5%%/TP2 1%%, SL 0.1%% beyond candle + BE-trail, swing+swingA+RETR | 1h recon")
print("=" * 116)
configs = [
    ("absR <= -0.5 (easy, baseline)", lambda i: absA[i] <= -0.5),
    ("absR >= 1 & delta-DIVERGENT",  lambda i: absA[i] >= 1.0 and divergent(i)),
    ("absR >= 1 & NOT divergent",    lambda i: absA[i] >= 1.0 and not divergent(i)),
    ("absR >= 1 (all)",              lambda i: absA[i] >= 1.0),
    ("absR > 0 & delta-DIVERGENT",   lambda i: absA[i] > 0 and divergent(i)),
    ("easy<=-0.5 OR (absR>=1 & div)", lambda i: absA[i] <= -0.5 or (absA[i] >= 1.0 and divergent(i))),
    ("easy<=-0.5 OR (absR>0 & div)",  lambda i: absA[i] <= -0.5 or (absA[i] > 0 and divergent(i))),
]
for name, pred in configs:
    print("  %-30s %s" % (name, stat(run(pred))))
print("=" * 116)
