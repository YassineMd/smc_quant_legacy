"""15m2pmSwitch (POC-TP variant) — same fade as r15_2pm_switch.py but the TARGET is the current-day causal POC
(point of control = max-volume price), not the opposing VA edge.  CAUSAL current-day VP only.

RULE (UTC day; 2PM = 14:00 UTC):
  direction : fade midnight->2PM.  midnight_close < 2pm_close (up) -> SHORT ; midnight_close > 2pm_close (down) -> LONG.
  entry     : from 2PM onward, first bar whose close is OUTSIDE the causal current-day value area on the fade side
              (SHORT close > VAH ; LONG close < VAL).  causal VP = day midnight..that bar (va_poc 70%).
  stop      : SL distance = 0.50 * (prev-day high - prev-day low), beyond entry.
  target    : the causal current-day POC (max-volume price), fixed at the entry bar.  POC lies INSIDE [VAL,VAH],
              so for a SHORT (entry>VAH) POC<entry and for a LONG (entry<VAL) POC>entry -> a valid, CLOSER target
              than the opposing VA edge.
  one trade per day; no trigger by 23:59 -> no trade.

Two exits: (A) run to SL/TP ; (B) + EOD force-close.  R:R varies per trade.  Fee 0.08%.  win = TP.

Run: python study/r15_2pm_switch_poc.py
"""
from __future__ import annotations
import os, sys, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.mm_skew_feature_matrix import va_poc
from study.mm_skew_v11_tf import build
import study.mm_skew_strategy as S

FEE = 0.0008
PM_HOUR = 14                                                   # "2PM" = 14:00 UTC
SL_FRAC = 0.50


def _dtu(ts):
    return dt.datetime.utcfromtimestamp(float(ts))


def _levels_add(prof, b):
    for pr, v in (b.get("levels") or {}).items():
        try:
            p = float(pr)
        except (TypeError, ValueError):
            continue
        prof[p] = prof.get(p, 0.0) + float(v.get("b", 0) or 0) + float(v.get("s", 0) or 0)


def day_groups(A):
    idx = {}; hilo = {}
    for i, b in enumerate(A):
        d = _dtu(b.get("start_time", 0.0) or 0.0).date()
        idx.setdefault(d, []).append(i)
        h = float(b["h"]); l = float(b["l"])
        if d not in hilo:
            hilo[d] = [h, l]
        else:
            hilo[d][0] = max(hilo[d][0], h); hilo[d][1] = min(hilo[d][1], l)
    return idx, {d: (v[0], v[1]) for d, v in hilo.items()}


def find_signals(A):
    idx, hilo = day_groups(A)
    sigs = []
    for d3 in sorted(idx):
        d2 = d3 - dt.timedelta(days=1)
        if d2 not in hilo:
            continue
        bars = idx[d3]
        if _dtu(A[bars[0]]["start_time"]).hour != 0:
            continue
        mid_close = float(A[bars[0]]["c"])
        pm_pos = next((k for k, i in enumerate(bars) if _dtu(A[i]["start_time"]).hour >= PM_HOUR), None)
        if pm_pos is None:
            continue
        pm_close = float(A[bars[pm_pos]]["c"])
        if mid_close == pm_close:
            continue
        side = -1 if mid_close < pm_close else 1
        prng = hilo[d2][0] - hilo[d2][1]
        if prng <= 0:
            continue
        sl_dist = SL_FRAC * prng
        prof = {}
        for k in range(0, pm_pos):
            _levels_add(prof, A[bars[k]])
        entry = None
        for k in range(pm_pos, len(bars)):
            i = bars[k]
            _levels_add(prof, A[i])                            # causal VP midnight..i (incl. i)
            va = va_poc(prof)
            if not va:
                continue
            vah = va["vah"]; val = va["val"]
            if not (vah > val):
                continue
            poc = max(prof, key=prof.get)                      # POC = max-volume price (the TARGET)
            c = float(A[i]["c"])
            if side < 0 and c > vah and poc < c:               # SHORT: stretched above value -> fade to POC
                entry = (i, c, c + sl_dist, poc); break
            if side > 0 and c < val and poc > c:               # LONG: stretched below value -> fade to POC
                entry = (i, c, c - sl_dist, poc); break
        if entry is None:
            continue
        i, epx, sl, tp = entry
        sigs.append(dict(i=i, side=side, entry=epx, sl=sl, tp=tp, day=d3,
                         day_end=bars[-1], sl_dist=sl_dist / epx, tp_dist=abs(tp - epx) / epx))
    return sigs


def sim(A, sg, eod):
    side = sg["side"]; e = sg["entry"]; sl = sg["sl"]; tp = sg["tp"]
    end = sg["day_end"] if eod else (len(A) - 1)
    for j in range(sg["i"] + 1, end + 1):
        hi = float(A[j]["h"]); lo = float(A[j]["l"])
        if (hi >= sl) if side < 0 else (lo <= sl):
            return -sg["sl_dist"], "SL"
        if (lo <= tp) if side < 0 else (hi >= tp):
            return sg["tp_dist"], "TP"
    if eod:
        cx = float(A[end]["c"])
        return ((e - cx) / e if side < 0 else (cx - e) / e), "EOD"
    return ((e - float(A[-1]["c"]) if side < 0 else float(A[-1]["c"]) - e) / e), "OPEN"


def block(A, sigs, eod, label):
    rows = []
    for sg in sigs:
        g, out = sim(A, sg, eod)
        rows.append(dict(gross=g, net=g - FEE, win=(out == "TP"), out=out))
    n = len(rows)
    if n == 0:
        print("  %-16s n=0" % label); return
    w = sum(1 for r in rows if r["net"] > 0)
    tp = sum(1 for r in rows if r["out"] == "TP"); slc = sum(1 for r in rows if r["out"] == "SL")
    nt = np.array([r["net"] for r in rows])
    tot = (np.prod(1 + nt) - 1) * 100
    gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum(); pf = (gg / ll) if ll > 0 else float("inf")
    rr = np.mean([sg["tp_dist"] / sg["sl_dist"] for sg in sigs]) if sigs else 0.0
    bal = S.BAL0
    for r in rows:
        bal += S.POS_FRAC * bal * S.LEV * r["net"]
    print("  %-16s n=%2d  TP %2d SL %2d  win %5.1f%%  net %+6.1f%%  mean %+.3f%%  PF %4.2f  avgR:R 1:%.2f  $%s"
          % (label, n, tp, slc, 100.0 * w / n, tot, nt.mean() * 100, pf, rr, f"{bal:,.0f}"))


def main():
    A, _first, _floor = build("15m")
    sigs = find_signals(A)
    nl = sum(1 for s in sigs if s["side"] > 0)
    span = (A[-1]["start_time"] - A[0]["start_time"]) / 86400.0
    print("=" * 100)
    print("15m2pmSwitch  POC-TP  - fade midnight->2PM, VA-edge entry, half-prev-range SL, POC target  (15m CAUSAL)")
    print("=" * 100)
    print("  %.0f days 15m  |  2PM = 14:00 UTC  |  signals %d  (%dL / %dS)" % (span, len(sigs), nl, len(sigs) - nl))
    if sigs:
        print("  avg SL dist %.2f%% (half prev range)  |  avg TP dist %.2f%% (entry->POC)"
              % (100 * np.mean([s["sl_dist"] for s in sigs]), 100 * np.mean([s["tp_dist"] for s in sigs])))
    print()
    for eod, tag in ((False, "A) run to SL/TP"), (True, "B) + EOD close")):
        print("%s" % tag)
        block(A, sigs, eod, "ALL")
        block(A, [s for s in sigs if s["side"] > 0], eod, "LONG")
        block(A, [s for s in sigs if s["side"] < 0], eod, "SHORT")
        print()
    print("CAVEAT: TP = current-day causal POC (closer than the VA edge -> higher win, smaller reward).")
    print("        2PM=14:00 UTC, day=UTC midnight. One 34-day regime, tiny n, forward n=0.")


if __name__ == "__main__":
    main()
