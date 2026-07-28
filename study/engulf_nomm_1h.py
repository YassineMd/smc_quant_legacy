"""1h engulf-at-S/R with the movmag filter REMOVED (quick test).
LONG: c1 bearish, c2 bullish, c2 open within 0.15% of support(VAL), c2 close>c1 high, both non-doji.
SHORT mirror (open at resistance, c2 close<c1 open). Exit structural SL 0.1% + 1:1 TP. $200k @ 10x, fee 0.08%."""
from __future__ import annotations
import os, sys, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import study.signal_search_lib as L
import study.mom_absorb_1h as MA
from study.va_bias_1h_strategies import daily_va, partial_va

PROX = 0.0015
F = L.load_features("1h")
A = F["A"]; d = F["dir"]; o = F["o"]; c = F["c"]; h = F["h"]; l = F["l"]; n = F["n"]; yr = F["year"]
dayva, dfirst = daily_va(A)


def nd(i):
    b = abs(c[i] - o[i]); return b > (h[i] - max(o[i], c[i])) and b > (min(o[i], c[i]) - l[i])


def sr_twophase(i):
    t = L._dtu(A[i]["start_time"]); d3 = t.date()
    va = dayva.get(d3 - dt.timedelta(days=1)) if t.hour < 15 else partial_va(A, i, dfirst.get(d3, i))
    return (va["vah"], va["val"]) if va else (None, None)


def sr_prevday(i):
    va = dayva.get(L._dtu(A[i]["start_time"]).date() - dt.timedelta(days=1))
    return (va["vah"], va["val"]) if va else (None, None)


def gen(srfn):
    sigs = []
    for i in range(1, n):
        if not (nd(i) and nd(i - 1)):                          # NO movmag filter
            continue
        vah, val = srfn(i)
        if d[i - 1] == -1 and d[i] == 1 and c[i] > h[i - 1] and val is not None and abs(o[i] - val) <= PROX * o[i]:
            side = 1; ext = float(l[i])
        elif d[i - 1] == 1 and d[i] == -1 and c[i] < o[i - 1] and vah is not None and abs(o[i] - vah) <= PROX * o[i]:
            side = -1; ext = float(h[i])
        else:
            continue
        sigs.append(dict(i=i, side=side, entry=float(c[i]), ext=ext, yr=int(yr[i])))
    return sigs


for label, srfn in (("two-phase S/R (partial incl. bar)", sr_twophase), ("prev-day causal S/R (honest)", sr_prevday)):
    rows = MA.taken(F, gen(srfn))
    print("=" * 100)
    print("1h ENGULF-at-S/R, movmag REMOVED  |  %s  |  fee 0.08%%, structural SL 0.1%% + 1:1 TP" % label)
    print("=" * 100)
    MA.report("ALL", rows)
    MA.report("LONG", [r for r in rows if r["side"] > 0])
    MA.report("SHORT", [r for r in rows if r["side"] < 0])
    MA.report("2025", [r for r in rows if r["yr"] == 2025])
    MA.report("2026", [r for r in rows if r["yr"] == 2026])
    print()
