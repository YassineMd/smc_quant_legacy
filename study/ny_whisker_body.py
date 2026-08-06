"""NY WHISKER-BODY migration (1h).  Weekdays only.
Candle-1 = the FIRST 1h candle in the NY session (hour>=NY0 UTC).  Candle-2 = the next 1h candle (wait until fully
formed).  Read each candle's WHISKERPLOT body via bar_quantiles.vq(levels) = (q25, q50, q75) by cumulative VOLUME:
body_low=q25, body_high=q75 (the 'W'-mode box).  Direction (bull/bear) and wicks are IGNORED.
  both q25 & q75 of candle-2 ABOVE candle-1  -> LONG
  both q25 & q75 of candle-2 BELOW candle-1  -> SHORT   (else: no trade)
Entry = close of candle-2.  NO STOP.  TP = entry +/- candle-2 body size (q75-q25).  Hold until TP or the horizon
(HOLD=eod -> end of the UTC day; HOLD=<hours> -> that many hours past candle-2), then exit at the last close.
One trade / day.  fee 0.08% round-trip.
Run: python study/ny_whisker_body.py      (HOLD=24  HOLD=48  NY0=13 to vary)
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
import study.signal_search_lib as L
import study.mom_absorb_1h as MA
from app import bar_quantiles as BQ

rng = np.random.default_rng(20260806)
F1 = L.load_features("1h"); FEE = MA.FEE
n1 = F1["n"]; C1 = F1["c"]; h1 = F1["h"]; l1 = F1["l"]; st1 = F1["start"]; A1 = F1["A"]
NY0 = int(os.environ.get("NY0", "13")); NY_END = int(os.environ.get("NY_END", "21"))
HOLD = os.environ.get("HOLD", "eod")                          # "eod" | <hours>

first_idx = {}
for i in range(n1):
    t = datetime.fromtimestamp(float(st1[i]), tz=timezone.utc)
    if t.weekday() >= 5 or not (NY0 <= t.hour < NY_END):
        continue
    d = t.date()
    if d not in first_idx:                                    # first NY 1h candle of the day
        first_idx[d] = i


def horizon_end(entry_t):
    if HOLD == "eod":
        tt = datetime.fromtimestamp(entry_t, tz=timezone.utc)
        return datetime(tt.year, tt.month, tt.day, 23, 59, 59, tzinfo=timezone.utc).timestamp()
    return entry_t + float(HOLD) * 3600.0


rows = []; no_trade = 0
for d, i1 in first_idx.items():
    i2 = i1 + 1
    if i2 >= n1:
        continue
    q1 = BQ.vq(A1[i1].get("levels") or {}); q2 = BQ.vq(A1[i2].get("levels") or {})
    blo1, _, bhi1 = q1; blo2, _, bhi2 = q2
    if any(x != x for x in (blo1, bhi1, blo2, bhi2)):         # NaN ladder(s)
        continue
    body2 = bhi2 - blo2
    if body2 <= 0:
        continue
    if bhi2 > bhi1 and blo2 > blo1:
        side = 1
    elif bhi2 < bhi1 and blo2 < blo1:
        side = -1
    else:
        no_trade += 1; continue
    e = float(C1[i2])
    if e <= 0:
        continue
    tp = e + side * body2
    entry_t = float(A1[i2].get("end_time", 0.0) or 0.0) or float(st1[i2])
    hend = horizon_end(entry_t)
    yr = datetime.fromtimestamp(entry_t, tz=timezone.utc).year
    net = None; jlast = None
    for j in range(i2 + 1, n1):
        if float(st1[j]) > hend:
            break
        jlast = j
        hi = float(h1[j]); lo = float(l1[j])
        if (hi >= tp) if side > 0 else (lo <= tp):
            net = side * (tp / e - 1.0) - FEE; break
    if net is None:                                           # timed out -> exit at last close in horizon
        px = float(C1[jlast]) if jlast is not None else e
        net = side * (px / e - 1.0) - FEE
    rows.append(dict(net=net, side=side, yr=yr, win=net > 0, hit=(jlast is not None and net > 0),
                     bodyp=body2 / e))


def rep(label, rs):
    k = len(rs)
    if k == 0:
        print("  %-12s n=0" % label); return
    nt = np.array([r["net"] for r in rs]); w = 100.0 * sum(r["win"] for r in rs) / k
    tot = (np.prod(1 + nt) - 1) * 100; gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum()
    pf = (gg / ll) if ll > 0 else float("inf"); bal = MA.account(list(nt))
    print("  %-12s n=%4d  win %5.1f%%  net %+8.1f%%  PF %.2f  mean %+.3f%%  worst %+.2f%%  END $%10.0f (%+.1f%%)"
          % (label, k, w, tot, pf, nt.mean() * 100, nt.min() * 100, bal, (bal - MA.B0) / MA.B0 * 100))


print("=" * 122)
print("NY WHISKER-BODY migration | c1=first NY 1h (h>=%dUTC) c2=next | both q25&q75 up->LONG / down->SHORT | entry=c2 close | NO SL | TP=c2 body | HOLD=%s"
      % (NY0, HOLD))
print("  weekdays | 1h recon | trades=%d  no-trade days=%d  avg body %.2f%%"
      % (len(rows), no_trade, float(np.mean([r["bodyp"] for r in rows])) * 100 if rows else 0.0))
print("=" * 122)
rep("ALL", rows); rep("LONG", [r for r in rows if r["side"] > 0]); rep("SHORT", [r for r in rows if r["side"] < 0])
rep("2025", [r for r in rows if r["yr"] == 2025]); rep("2026", [r for r in rows if r["yr"] == 2026])
if rows:
    nt = np.array([r["net"] for r in rows]); hitr = 100.0 * sum(r["hit"] for r in rows) / len(rows)
    mm = np.array([rng.choice(nt, size=len(nt), replace=True).mean() for _ in range(10000)]) * 100
    lo, hi = np.percentile(mm, [2.5, 97.5])
    print("  TP-hit rate %.1f%%  |  bootstrap mean net/trade %+.4f%%  95%% CI [%+.4f%%, %+.4f%%]  -> %s"
          % (hitr, nt.mean() * 100, lo, hi, "clears 0" if lo > 0 else ("sig NEGATIVE" if hi < 0 else "includes 0")))
print("=" * 122)
