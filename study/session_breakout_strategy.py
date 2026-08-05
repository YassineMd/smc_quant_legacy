"""LONDON-RANGE BREAKOUT in NY, absorption-EXTREME + non-doji entry. 1h recon, weekdays only.
  * LONG  if an NY candle CLOSES > London HIGH ;  SHORT if it CLOSES < London LOW  (the breakout IS the bias).
  * entry candle must be IN FAVOR (bullish for long / bearish for short), NON-DOJI, and absorption A<=-0.75 OR A>=1.0
    (extreme momentum OR extreme absorption; the "normal" middle -0.75<A<1 is skipped).
  * take the FIRST such NY (13-21 UTC) candle; entry = its close.  TP 1%.  SL 0.2% beyond the entry-candle extreme
    [reading B] (also 0.2% from entry [A]).  1 trade/day; non-overlap; fee 0.08%/rt.
Sessions: London [08,13) NY [13,21) UTC.  non-doji = body > upper wick AND body > lower wick (codebase `nd`).
Run: python study/session_breakout_strategy.py [TP_pct]   (default 0.01)
"""
from __future__ import annotations
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from collections import defaultdict
import numpy as np
import study.signal_search_lib as L
import study.mom_absorb_1h as MA

rng = np.random.default_rng(20260804)
F = L.load_features("1h")
A = F["A"]; n = F["n"]; absA = F["absA"]
st = F["start"]; O = F["o"]; C = F["c"]; Hh = F["h"]; Ll = F["l"]
dts = [datetime.fromtimestamp(float(t), tz=timezone.utc) for t in st]
hour = np.array([d.hour for d in dts]); dow = np.array([d.weekday() for d in dts])
dord = np.array([d.toordinal() for d in dts]); yr = np.array([d.year for d in dts])
order = np.argsort(st)
FEE = MA.FEE; TP_PCT = float(sys.argv[1]) if len(sys.argv) > 1 else 0.01; SL_PAD = 0.002
MODE = sys.argv[2] if len(sys.argv) > 2 else "both"   # both | mom (A<=-0.75) | abs (A>=1)


def absorb_ok(i):
    if MODE == "mom":
        return absA[i] <= -0.75
    if MODE == "abs":
        return absA[i] >= 1.0
    return absA[i] <= -0.75 or absA[i] >= 1.0

byday = defaultdict(list)
for i in order:
    if dow[i] < 5:
        byday[dord[i]].append(i)


def nondoji(i):
    o, c, h, l = O[i], C[i], Hh[i], Ll[i]
    body = abs(c - o)
    return body > (h - max(o, c)) and body > (min(o, c) - l)


signals = []; n_days_lon = 0; n_no_entry = 0
for d in sorted(byday):
    idxs = byday[d]
    lon = [i for i in idxs if 8 <= hour[i] < 13]
    if not lon:
        continue
    n_days_lon += 1
    lon_hi = max(Hh[i] for i in lon); lon_lo = min(Ll[i] for i in lon)
    entry = None
    for i in idxs:
        if not (13 <= hour[i] < 21):
            continue
        if not nondoji(i):
            continue
        if not absorb_ok(i):                                # MODE: momentum (A<=-.75) / absorbed (A>=1) / both
            continue
        if C[i] > lon_hi and C[i] > O[i]:                   # bullish close above the London high
            entry = (i, 1); break
        if C[i] < lon_lo and C[i] < O[i]:                   # bearish close below the London low
            entry = (i, -1); break
    if entry is None:
        n_no_entry += 1; continue
    i, bias = entry
    signals.append((i, bias, int(yr[i]), "mom" if absA[i] <= -0.75 else "abs"))
signals.sort()


def run(sl_mode):
    rows = []; last = -1
    for (i, bias, y, kind) in signals:
        if i <= last:
            continue
        e = C[i]
        sl = (Ll[i] * (1 - SL_PAD) if bias > 0 else Hh[i] * (1 + SL_PAD)) if sl_mode == "candle" \
            else (e * (1 - SL_PAD) if bias > 0 else e * (1 + SL_PAD))
        tp = e * (1 + TP_PCT) if bias > 0 else e * (1 - TP_PCT)
        if (bias > 0 and sl >= e) or (bias < 0 and sl <= e):
            continue
        win, ej = MA.walk(A, i, bias, sl, tp, n); last = ej
        dist = abs(e - sl) / e; tpret = abs(tp - e) / e
        rows.append(dict(net=(tpret if win else -dist) - FEE, bias=bias, yr=y, kind=kind, win=bool(win), dist=dist, rr=tpret / dist))
    return rows


def boot_ci(a, B=10000):
    a = np.asarray(a, float)
    if len(a) == 0:
        return (float("nan"),) * 3
    m = np.array([rng.choice(a, size=len(a), replace=True).mean() for _ in range(B)])
    return a.mean() * 100, np.percentile(m, 2.5) * 100, np.percentile(m, 97.5) * 100


def rep(label, rows):
    k = len(rows)
    if k == 0:
        print("  %-16s n=0" % label); return
    nt = np.array([r["net"] for r in rows]); w = 100.0 * sum(r["win"] for r in rows) / k
    tot = (np.prod(1 + nt) - 1) * 100; gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum()
    pf = (gg / ll) if ll > 0 else float("inf")
    dm = np.mean([r["dist"] for r in rows]) * 100; rrm = np.mean([r["rr"] for r in rows]); bal = MA.account(list(nt))
    print("  %-16s n=%4d  win %5.1f%%  net %+7.1f%%  PF %.2f  avgSL %.2f%%  RR %.2f  END $%9.0f (%+.1f%%)"
          % (label, k, w, tot, pf, dm, rrm, bal, (bal - MA.B0) / MA.B0 * 100))


nb = len(signals)
nmom = sum(1 for s in signals if s[3] == "mom"); nl = sum(1 for s in signals if s[1] > 0)
print("=" * 114)
print("LONDON-BREAKOUT + absorption-extreme + non-doji | NY | TP %.2f%% | 1h recon, weekdays" % (TP_PCT * 100))
print("  days with London range %d;  no qualifying breakout entry %d;  SIGNALS=%d  (%dL/%dS, %d momentum / %d absorbed)"
      % (n_days_lon, n_no_entry, nb, nl, nb - nl, nmom, nb - nmom))
print("=" * 114)
for mode, name in (("candle", "SL = 0.2% beyond entry-candle extreme [B]"),
                   ("entry", "SL = 0.2% from entry price [A]")):
    rows = run(mode)
    print("\n--- %s ---" % name)
    rep("ALL", rows); rep("LONG", [r for r in rows if r["bias"] > 0]); rep("SHORT", [r for r in rows if r["bias"] < 0])
    rep("momentum A<=-.75", [r for r in rows if r["kind"] == "mom"]); rep("absorbed A>=1", [r for r in rows if r["kind"] == "abs"])
    rep("2025", [r for r in rows if r["yr"] == 2025]); rep("2026", [r for r in rows if r["yr"] == 2026])
    nt = np.array([r["net"] for r in rows])
    if len(nt):
        m, lo, hi = boot_ci(nt)
        print("  bootstrap mean net/trade %+.4f%%  95%% CI [%+.4f%%, %+.4f%%]  -> %s"
              % (m, lo, hi, "clears 0" if lo > 0 else "INCLUDES 0"))
print("=" * 114)
