"""15m Engulfing S/R (app/momentum_detect) — take EVERY signal, then apply the Price&CVD swing filter:
trade WITH the developing swing leg, skip if the leg's A>0 or A4>0, FLIP to the opposite side on a retracement leg.
Each signal carries its OWN entry/SL/TP (momentum_detect). Non-overlap; fee 0.08%/rt.
Run: TF=15m SWING_LB=2000 python study/momentum_swing_15m.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
import study.signal_search_lib as L
import study.mom_absorb_1h as MA
from app import momentum_detect as MD, structure, swing_lvn_detect as SW

rng = np.random.default_rng(20260805)
TF = os.environ.get("TF", "15m")
SWING_LB = int(os.environ.get("SWING_LB", "2000"))            # bounded swing_lines lookback (speed; local structure)
SWING_ON = os.environ.get("SWING_OFF") != "1"                 # SWING_OFF=1 -> no swing filter (raw 'take every position')
F = L.load_features(TF)
A = F["A"]; n = F["n"]; O = F["o"]; C = F["c"]; Hh = F["h"]; Ll = F["l"]
yr = np.array([datetime.fromtimestamp(float(t), tz=timezone.utc).year for t in F["start"]])
FEE = MA.FEE
TP1 = float(os.environ.get("TP1", "0.3")) / 100.0; TP2 = float(os.environ.get("TP2", "1.0")) / 100.0
SL_PAD = 0.001; BE = 0.001                                    # SL 0.1% beyond candle; BE trail = entry +/- 0.1%

Harr = [float(b.get("high", 0.0) or 0.0) for b in A]; Larr = [float(b.get("low", 0.0) or 0.0) for b in A]
Carr = [float(b.get("close", b.get("close_price", 0.0)) or 0.0) for b in A]
_thr = SW._adaptive_thr(Harr, Larr, Carr, window=len(Carr))
_piv = sorted(structure._zigzag_confirmed(Harr, Larr, _thr), key=lambda p: p[3])
swing_dir = [0] * n; _pi = 0; _cur = 0
for _i in range(n):
    while _pi < len(_piv) and _piv[_pi][3] <= _i:
        _cur = -1 if _piv[_pi][2] else 1; _pi += 1
    swing_dir[_i] = _cur

sigs = MD.detect(A, skip_last=True)
print("15m Engulfing S/R signals (raw): %d" % len(sigs))


def scaleout(i, side):
    """(net, exit_bar, outcome). Enter candle close; 50% @ TP1 / 50% runner @ TP2 with BE-trail after TP1; else SL."""
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
            return (-dist - FEE), j, "SL"
        if (hi >= tp1) if side > 0 else (lo <= tp1):
            tp1_bar = j; break
    if tp1_bar is None:
        return (-dist - FEE), n - 1, "SL"
    runner = BE; outc = "TP1+BE"; ke = tp1_bar
    for k in range(tp1_bar, n):
        hi = float(A[k]["h"]); lo = float(A[k]["l"])
        t2 = (hi >= tp2) if side > 0 else (lo <= tp2)
        if k == tp1_bar:
            if t2:
                runner = TP2; outc = "TP1+TP2"; ke = k; break
            continue
        if (lo <= be) if side > 0 else (hi >= be):
            runner = BE; outc = "TP1+BE"; ke = k; break
        if t2:
            runner = TP2; outc = "TP1+TP2"; ke = k; break
        ke = k
    return (0.5 * TP1 + 0.5 * runner - FEE), ke, outc


rows = []; last = -1; _seen = 0
for s in sigs:
    i = int(s["i"]); side = int(s["side"]); e = float(s["entry"]); sl = float(s["sl"]); tp = float(s["tp"])
    if e <= 0 or sl <= 0 or tp <= 0:
        continue
    if SWING_ON:                                             # Price&CVD swing filter
        if swing_dir[i] == 0:
            continue
        _seen += 1
        if _seen % 1000 == 0:
            print("  ...swing-filtering %d" % _seen, file=sys.stderr)
        _lo = max(0, i - SWING_LB) if SWING_LB > 0 else 0
        legs = SW.swing_lines(A[_lo:i + 1])
        dev = next((lg for lg in reversed(legs) if lg.get("developing")), None)
        if dev is None:
            continue
        a = dev.get("A"); a4 = dev.get("A4")
        if (a is not None and a > 0) or (a4 is not None and a4 > 0):
            continue
        legdir = 1 if dev.get("ends_high") else -1
        eff = -legdir if dev.get("is_retr") else legdir
        if side != eff:
            continue
    if i <= last:
        continue
    r = scaleout(i, side)                                     # candle-side scale-out exit (ignores detector SL/TP)
    if r is None:
        continue
    net, ej, outc = r; last = ej
    rows.append(dict(net=net, side=side, yr=int(yr[i]), win=net > 0, outc=outc))


def rep(label, rs):
    k = len(rs)
    if k == 0:
        print("  %-10s n=0" % label); return
    nt = np.array([r["net"] for r in rs]); w = 100.0 * sum(r["win"] for r in rs) / k
    tot = (np.prod(1 + nt) - 1) * 100; gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum()
    pf = (gg / ll) if ll > 0 else float("inf"); bal = MA.account(list(nt))
    print("  %-10s n=%4d  win %5.1f%%  net %+7.1f%%  PF %.2f  mean %+.3f%%  END $%9.0f (%+.1f%%)"
          % (label, k, w, tot, pf, nt.mean() * 100, bal, (bal - MA.B0) / MA.B0 * 100))


from collections import Counter
oc = Counter(r["outc"] for r in rows); _K = max(1, len(rows))
print("=" * 104)
print("15m ENGULFING S/R + Price&CVD swing (with/A&A4<=0/retrace-flip) | scale-out TP1 %.1f%%/TP2 %.1f%% SL0.1%%+BE | %s recon | n=%d"
      % (TP1 * 100, TP2 * 100, TF, len(rows)))
print("  outcomes: SL %d (%.0f%%) | TP1+BE %d (%.0f%%) | TP1+TP2 %d (%.0f%%)"
      % (oc["SL"], 100 * oc["SL"] / _K, oc["TP1+BE"], 100 * oc["TP1+BE"] / _K, oc["TP1+TP2"], 100 * oc["TP1+TP2"] / _K))
print("=" * 104)
rep("ALL", rows); rep("LONG", [r for r in rows if r["side"] > 0]); rep("SHORT", [r for r in rows if r["side"] < 0])
rep("2025", [r for r in rows if r["yr"] == 2025]); rep("2026", [r for r in rows if r["yr"] == 2026])
if rows:
    nt = np.array([r["net"] for r in rows])
    mm = np.array([rng.choice(nt, size=len(nt), replace=True).mean() for _ in range(10000)]) * 100
    lo, hi = np.percentile(mm, [2.5, 97.5])
    print("  bootstrap mean net/trade %+.4f%%  95%% CI [%+.4f%%, %+.4f%%]  -> %s"
          % (nt.mean() * 100, lo, hi, "clears 0" if lo > 0 else ("sig NEGATIVE" if hi < 0 else "includes 0")))
print("=" * 104)
