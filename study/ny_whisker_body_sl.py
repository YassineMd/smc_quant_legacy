"""NY WHISKER-BODY migration (1h) WITH stop + bigger-body target.  Weekdays only.
Candle-1 = first 1h candle in the NY session (hour>=NY0 UTC).  Candle-2 = next 1h candle (wait fully formed).
Whiskerplot body via bar_quantiles.vq(levels)=(q25,q50,q75) by cumulative VOLUME: body_low=q25, body_high=q75.
  both q25 & q75 of c2 ABOVE c1 -> LONG | both BELOW -> SHORT | else no trade.   (direction/wicks IGNORED)
Entry = close of c2.
Stop  = SL_PCT beyond candle-1's extreme (SL_REF=range: c1 high/low  |  SL_REF=body: c1 q75/q25).
Target= the BIGGER of the two whisker bodies -> TP dist = max(q75-q25 of c1, q75-q25 of c2).
Walk 1h from the candle after c2 (adverse-first). Unresolved by horizon (HOLD=eod | <hours>) -> exit at last close.
One trade/day. fee 0.08% round-trip.
Run: python study/ny_whisker_body_sl.py    (SL_REF=body  HOLD=24  SL_PCT=0.1 to vary)
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
n1 = F1["n"]; C1 = F1["c"]; H1 = F1["h"]; L1 = F1["l"]; st1 = F1["start"]; A1 = F1["A"]
NY0 = int(os.environ.get("NY0", "13")); NY_END = int(os.environ.get("NY_END", "21"))
SL_PCT = float(os.environ.get("SL_PCT", "0.1")) / 100.0
SL_REF = os.environ.get("SL_REF", "range")                    # range (c1 high/low) | body (c1 q75/q25)
HOLD = os.environ.get("HOLD", "eod")
HOLD_C = os.environ.get("HOLD_C")                             # if set: hold at most N candles after c2 (time stop) -> overrides HOLD
TP_FIX = os.environ.get("TP_FIX")                            # if set: fixed TP as a % of entry (overrides max-body TP)

first_idx = {}
for i in range(n1):
    t = datetime.fromtimestamp(float(st1[i]), tz=timezone.utc)
    if t.weekday() >= 5 or not (NY0 <= t.hour < NY_END):
        continue
    d = t.date()
    if d not in first_idx:
        first_idx[d] = i


def horizon_end(entry_t):
    if HOLD == "eod":
        tt = datetime.fromtimestamp(entry_t, tz=timezone.utc)
        return datetime(tt.year, tt.month, tt.day, 23, 59, 59, tzinfo=timezone.utc).timestamp()
    return entry_t + float(HOLD) * 3600.0


rows = []; no_trade = 0; invalid = 0
for d, i1 in first_idx.items():
    i2 = i1 + 1
    if i2 >= n1:
        continue
    q1 = BQ.vq(A1[i1].get("levels") or {}); q2 = BQ.vq(A1[i2].get("levels") or {})
    b1l, _, b1h = q1; b2l, _, b2h = q2
    if any(x != x for x in (b1l, b1h, b2l, b2h)):
        continue
    body1 = b1h - b1l; body2 = b2h - b2l
    if body1 <= 0 or body2 <= 0:
        continue
    if b2h > b1h and b2l > b1l:
        side = 1
    elif b2h < b1h and b2l < b1l:
        side = -1
    else:
        no_trade += 1; continue
    e = float(C1[i2])
    if e <= 0:
        continue
    tpdist = (float(TP_FIX) / 100.0 * e) if TP_FIX else max(body1, body2)   # fixed % TP, else bigger whisker body
    tp = e + side * tpdist
    if SL_REF == "body":
        ref = b1l if side > 0 else b1h
    else:
        ref = float(L1[i1]) if side > 0 else float(H1[i1])
    sl = ref * (1 - SL_PCT) if side > 0 else ref * (1 + SL_PCT)
    if (side > 0 and e <= sl) or (side < 0 and e >= sl):      # entry already beyond stop -> invalid
        invalid += 1; continue
    risk = abs(e - sl); rr = tpdist / risk if risk > 0 else float("nan")
    entry_t = float(A1[i2].get("end_time", 0.0) or 0.0) or float(st1[i2])
    hend = horizon_end(entry_t); yr = datetime.fromtimestamp(entry_t, tz=timezone.utc).year
    net = None; jlast = None
    jend = min(n1, i2 + 1 + int(HOLD_C)) if HOLD_C else n1     # HOLD_C = candle time-stop (e.g. 1 = the 3rd candle only)
    for j in range(i2 + 1, jend):
        if not HOLD_C and float(st1[j]) > hend:
            break
        jlast = j
        hi = float(H1[j]); lo = float(L1[j])
        if (lo <= sl) if side > 0 else (hi >= sl):            # adverse-first
            net = side * (sl / e - 1.0) - FEE; break
        if (hi >= tp) if side > 0 else (lo <= tp):
            net = side * (tp / e - 1.0) - FEE; break
    if net is None:
        px = float(C1[jlast]) if jlast is not None else e
        net = side * (px / e - 1.0) - FEE
    rows.append(dict(net=net, side=side, yr=yr, win=net > 0, rr=rr, riskp=risk / e, tpp=tpdist / e))


def rep(label, rs):
    k = len(rs)
    if k == 0:
        print("  %-12s n=0" % label); return
    nt = np.array([r["net"] for r in rs]); w = 100.0 * sum(r["win"] for r in rs) / k
    tot = (np.prod(1 + nt) - 1) * 100; gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum()
    pf = (gg / ll) if ll > 0 else float("inf"); bal = MA.account(list(nt))
    print("  %-12s n=%4d  win %5.1f%%  net %+8.1f%%  PF %.2f  mean %+.3f%%  worst %+.2f%%  END $%10.0f (%+.1f%%)"
          % (label, k, w, tot, pf, nt.mean() * 100, nt.min() * 100, bal, (bal - MA.B0) / MA.B0 * 100))


avg_rr = float(np.nanmean([r["rr"] for r in rows])) if rows else 0.0
avg_risk = float(np.mean([r["riskp"] for r in rows])) * 100 if rows else 0.0
avg_tp = float(np.mean([r["tpp"] for r in rows])) * 100 if rows else 0.0
print("=" * 122)
print("NY WHISKER-BODY +SL | c1=first NY 1h c2=next | q25&q75 up->L/down->S | entry=c2 close | SL %.1f%% past c1 %s | TP=%s | %s"
      % (SL_PCT * 100, SL_REF, ("fixed %.2f%%" % float(TP_FIX)) if TP_FIX else "max(body1,body2)",
         ("time-stop %s candle(s)" % HOLD_C) if HOLD_C else ("HOLD=%s" % HOLD)))
print("  weekdays | 1h recon | trades=%d  no-trade=%d  invalid=%d | avg risk %.2f%%  avg TP %.2f%%  avg RR %.2f"
      % (len(rows), no_trade, invalid, avg_risk, avg_tp, avg_rr))
print("=" * 122)
rep("ALL", rows); rep("LONG", [r for r in rows if r["side"] > 0]); rep("SHORT", [r for r in rows if r["side"] < 0])
rep("2025", [r for r in rows if r["yr"] == 2025]); rep("2026", [r for r in rows if r["yr"] == 2026])
if rows:
    nt = np.array([r["net"] for r in rows])
    mm = np.array([rng.choice(nt, size=len(nt), replace=True).mean() for _ in range(10000)]) * 100
    lo, hi = np.percentile(mm, [2.5, 97.5])
    print("  bootstrap mean net/trade %+.4f%%  95%% CI [%+.4f%%, %+.4f%%]  -> %s"
          % (nt.mean() * 100, lo, hi, "clears 0" if lo > 0 else ("sig NEGATIVE" if hi < 0 else "includes 0")))
print("=" * 122)
