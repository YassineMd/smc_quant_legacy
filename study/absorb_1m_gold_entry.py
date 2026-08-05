"""1h signal (EVERY candle) + a 1m GOLD-SQUARE entry trigger, scale-out exit. 1h recon + 1m recon.

1h SIGNAL (no Absorption badge — every candle): candle side, ease vw%>=VW_MIN, absR<=ABS_LO, Price&CVD swing
  (aligned + developing-leg A<=0 & A4<=0 + retrace->opposite).
1m ENTRY: on the candle FORMING right after the signal candle, enter the FIRST 1m GOLD SQUARE (engulf1m gold tier:
  a 1m engulfing candle whose body > each of the last 5 1m ranges) that is (a) in our direction, and (b) whose close
  sits INSIDE the signal candle's body: long -> open<gold<close, short -> close<gold<open. If the next 1h candle fully
  forms with no such gold, DROP the signal. Entry = the gold 1m close.
EXIT: scale-out 50% @ TP1 +0.3% / 50% @ TP2 +1.0%, SL 0.1% beyond the ENTRY (gold) 1m candle, runner trailed to
  break-even (+/-0.1%) after TP1. Walked on the 1m series. fee 0.08%/rt. Non-overlap by entry time.
Run: VW_MIN=1 ABS_LO=-1 python study/absorb_1m_gold_entry.py
"""
from __future__ import annotations
import os, sys, bisect
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
import study.signal_search_lib as L
import study.mom_absorb_1h as MA
from app import structure, swing_lvn_detect as SW

rng = np.random.default_rng(20260805)
VW_MIN = float(os.environ.get("VW_MIN", "1"))
ABS_LO = float(os.environ.get("ABS_LO", "-1"))
ENGULF_1H = os.environ.get("ENGULF_1H") == "1"           # ENGULF_1H=1 -> the 1h signal candle must ENGULF the prior body
FEE = MA.FEE; TP1 = 0.003; TP2 = 0.010; SL_PAD = 0.001; BE = 0.001; MAXF = 8000   # cap forward 1m bars

# ---------- 1h signals ----------
F = L.load_features("1h")
A = F["A"]; n = F["n"]; absA = F["absA"]; O = F["o"]; C = F["c"]; Hh = F["h"]; Ll = F["l"]
st1h = [float(t) for t in F["start"]]
et1h = [float(b.get("end_time", 0.0) or 0.0) for b in A]
yr = np.array([datetime.fromtimestamp(float(t), tz=timezone.utc).year for t in st1h])

Harr = [float(b.get("high", 0.0) or 0.0) for b in A]; Larr = [float(b.get("low", 0.0) or 0.0) for b in A]
Carr = [float(b.get("close", b.get("close_price", 0.0)) or 0.0) for b in A]
_thr = SW._adaptive_thr(Harr, Larr, Carr, window=len(Carr))
_piv = sorted(structure._zigzag_confirmed(Harr, Larr, _thr), key=lambda p: p[3])
swing_dir = [0] * n; _pi = 0; _cur = 0
for _i in range(n):
    while _pi < len(_piv) and _piv[_pi][3] <= _i:
        _cur = -1 if _piv[_pi][2] else 1; _pi += 1
    swing_dir[_i] = _cur


def vw_ok(i):
    ut = float(A[i].get("up_ticks", 0.0) or 0.0); dt = float(A[i].get("dn_ticks", 0.0) or 0.0)
    return min(ut, dt) > 0 and (max(ut, dt) / min(ut, dt) - 1.0) * 100.0 >= VW_MIN


def engulf_1h(i):
    """+1/-1 if 1h candle i ENGULFS candle i-1's body (non-doji), else 0 — same test as the Absorption indicator."""
    if i < 1:
        return 0
    o = O[i]; c = C[i]; h = Hh[i]; l = Ll[i]; po = O[i - 1]; pc = C[i - 1]
    if o <= 0 or c <= 0 or (h - l) <= 0:
        return 0
    pbhi = po if po > pc else pc; pblo = pc if po > pc else po
    if c > o and o <= pblo and c >= pbhi:
        eng = 1
    elif c < o and o >= pbhi and c <= pblo:
        eng = -1
    else:
        return 0
    b = abs(c - o)
    return eng if (b > (h - (o if o > c else c)) and b > ((o if o < c else c) - l)) else 0   # non-doji


signals = []; _seen = 0
for i in range(0, n - 1):
    if C[i] == O[i]:
        continue
    side = 1 if C[i] > O[i] else -1
    if not vw_ok(i) or not (absA[i] <= ABS_LO) or swing_dir[i] == 0:
        continue
    if ENGULF_1H and engulf_1h(i) == 0:                  # 1h signal candle must be engulfing
        continue
    _seen += 1
    if _seen % 1000 == 0:
        print("  ...1h swing-filtering %d" % _seen, file=sys.stderr)
    legs = SW.swing_lines(A[:i + 1])
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
    signals.append((i, side))
print("1h signals (pre-1m-gate): %d" % len(signals))

# ---------- 1m arrays + GOLD squares ----------
z = np.load(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_1m_arrays.npz"))
st1 = z["st1"]; o1 = z["o1"]; c1 = z["c1"]; h1 = z["h1"]; l1 = z["l1"]
m = len(st1)
body1 = np.abs(c1 - o1); rng1 = h1 - l1
print("1m buckets: %d  [%s .. %s]" % (m,
      datetime.utcfromtimestamp(float(st1[0])).date(), datetime.utcfromtimestamp(float(st1[-1])).date()))

# GOLD = engulf (of prev body) + non-doji + body > each of the last 5 ranges (engulf1m_detect gold tier, OHLC only)
gk_list = []; gs_list = []
for k in range(5, m):
    o = o1[k]; c = c1[k]; h = h1[k]; l = l1[k]
    if o <= 0 or c <= 0 or (h - l) <= 0:
        continue
    po = o1[k - 1]; pc = c1[k - 1]
    pbhi = po if po > pc else pc; pblo = pc if po > pc else po
    if c > o and o <= pblo and c >= pbhi:
        eng = 1
    elif c < o and o >= pbhi and c <= pblo:
        eng = -1
    else:
        continue
    b = body1[k]
    if not (b > (h - (o if o > c else c)) and b > ((o if o < c else c) - l)):   # non-doji
        continue
    if b > rng1[k - 5:k].max():                                                  # big-engulf gold
        gk_list.append(k); gs_list.append(eng)
gold_k = np.array(gk_list); gold_side = np.array(gs_list); gold_t = st1[gold_k]
print("1m GOLD squares: %d" % len(gold_k))


def scaleout_1m(ek, side, e, sl):
    dist = abs(e - sl) / e
    tp1 = e * (1 + TP1) if side > 0 else e * (1 - TP1)
    tp2 = e * (1 + TP2) if side > 0 else e * (1 - TP2)
    be = e * (1 + BE) if side > 0 else e * (1 - BE)
    end = min(m, ek + 1 + MAXF); tp1_k = None
    for j in range(ek + 1, end):
        hi = h1[j]; lo = l1[j]
        if (lo <= sl) if side > 0 else (hi >= sl):
            return (-dist - FEE), j, "SL"
        if (hi >= tp1) if side > 0 else (lo <= tp1):
            tp1_k = j; break
    if tp1_k is None:
        return (-dist - FEE), end - 1, "SL"
    runner = BE; outc = "TP1+BE"; ke = tp1_k; end2 = min(m, tp1_k + MAXF)
    for k in range(tp1_k, end2):
        hi = h1[k]; lo = l1[k]
        t2 = (hi >= tp2) if side > 0 else (lo <= tp2)
        if k == tp1_k:
            if t2:
                runner = TP2; outc = "TP1+TP2"; ke = k; break
            continue
        if (lo <= be) if side > 0 else (hi >= be):
            runner = BE; outc = "TP1+BE"; ke = k; break
        if t2:
            runner = TP2; outc = "TP1+TP2"; ke = k; break
        ke = k
    return (0.5 * TP1 + 0.5 * runner - FEE), ke, outc


# ---------- entry: first qualifying 1m gold in the NEXT candle window ----------
rows = []; last_exit_t = -1.0; no_gold = 0
for (i, side) in signals:
    ncs = st1h[i + 1]; nce = et1h[i + 1]
    if nce <= ncs:
        nce = st1h[i + 2] if (i + 2) < n else (ncs + 3600.0)
    lo = bisect.bisect_left(gold_t, ncs); hi = bisect.bisect_right(gold_t, nce)
    ek = None
    for gj in range(lo, hi):
        if gold_side[gj] != side:
            continue
        gc = c1[gold_k[gj]]
        if side > 0 and not (O[i] < gc < C[i]):     # long: gold close inside body, below signal close & above open
            continue
        if side < 0 and not (C[i] < gc < O[i]):     # short: gold close inside body, above close & below open
            continue
        ek = int(gold_k[gj]); break
    if ek is None:
        no_gold += 1; continue
    e = c1[ek]
    sl = l1[ek] * (1 - SL_PAD) if side > 0 else h1[ek] * (1 + SL_PAD)
    if (side > 0 and sl >= e) or (side < 0 and sl <= e):
        continue
    if st1[ek] <= last_exit_t:                        # non-overlap by entry time
        continue
    net, xk, outc = scaleout_1m(ek, side, e, sl)
    last_exit_t = st1[xk]
    rows.append(dict(net=net, side=side, yr=int(yr[i]), outc=outc, win=net > 0))

# ---------- report ----------
from collections import Counter
oc = Counter(r["outc"] for r in rows)
K = len(rows)
print("=" * 100)
print("1h signal + 1m GOLD-square entry + scale-out | vw>=%.0f absR<=%.2f | 1h+1m recon | n=%d" % (VW_MIN, ABS_LO, K))
print("  signals=%d  entered=%d  dropped(no gold)=%d" % (len(signals), K, no_gold))
if K:
    print("  outcomes: SL %d (%.0f%%) | TP1+BE %d (%.0f%%) | TP1+TP2 %d (%.0f%%)"
          % (oc["SL"], 100 * oc["SL"] / K, oc["TP1+BE"], 100 * oc["TP1+BE"] / K, oc["TP1+TP2"], 100 * oc["TP1+TP2"] / K))
print("=" * 100)


def rep(label, rs):
    k = len(rs)
    if k == 0:
        print("  %-10s n=0" % label); return
    nt = np.array([r["net"] for r in rs]); w = 100.0 * sum(r["win"] for r in rs) / k
    tot = (np.prod(1 + nt) - 1) * 100; gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum()
    pf = (gg / ll) if ll > 0 else float("inf"); bal = MA.account(list(nt))
    print("  %-10s n=%4d  win %5.1f%%  net %+7.1f%%  PF %.2f  mean %+.3f%%  END $%9.0f (%+.1f%%)"
          % (label, k, w, tot, pf, nt.mean() * 100, bal, (bal - MA.B0) / MA.B0 * 100))


rep("ALL", rows); rep("LONG", [r for r in rows if r["side"] > 0]); rep("SHORT", [r for r in rows if r["side"] < 0])
rep("2025", [r for r in rows if r["yr"] == 2025]); rep("2026", [r for r in rows if r["yr"] == 2026])
if K:
    nt = np.array([r["net"] for r in rows])
    mm = np.array([rng.choice(nt, size=len(nt), replace=True).mean() for _ in range(10000)]) * 100
    lo, hi = np.percentile(mm, [2.5, 97.5])
    print("  bootstrap mean net/trade %+.4f%%  95%% CI [%+.4f%%, %+.4f%%]  -> %s"
          % (nt.mean() * 100, lo, hi, "clears 0" if lo > 0 else ("sig NEGATIVE" if hi < 0 else "includes 0")))
print("=" * 100)
