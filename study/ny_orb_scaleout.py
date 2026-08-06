"""NY OPENING-RANGE BREAKOUT (ORB) + scale-out.  Weekdays only.
Range   = the FIRST 1h candle that prints in the NY session (hour>=NY0 UTC) -> its high/low.
Entry   = the FIRST 15m candle (after that 1h candle, before NY_END) that BREAKS the range:
          break UP -> LONG, break DOWN -> SHORT.  (BREAK=wick: high/low pierces the level, entry AT the level;
          BREAK=close: 15m CLOSE beyond the level, entry at that close.)
Stop    = SL_PCT beyond the OPPOSITE range edge (long: SL_PCT below range low / short: SL_PCT above range high).
Targets = risk R = |entry-SL|;  TP1 = 1:TP1_R,  TP2 = 1:TP2_R.  50% off at TP1, then SL -> breakeven+BE_PCT,
          remaining 50% runs to TP2 (or the BE+ stop).  Unresolved by EoD -> exit remaining at the last 15m close.
Walk on 15m from the candle AFTER the break (adverse-first within a candle; favourable-on-trigger-candle for TP1).
One trade / day (non-overlap).  fee 0.08% round-trip.
Run: python study/ny_orb_scaleout.py     (BREAK=close TP2_R=3 SL_PCT=0.1 ... to vary)
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from collections import defaultdict
import numpy as np
import study.signal_search_lib as L
import study.mom_absorb_1h as MA

rng = np.random.default_rng(20260806)
F1 = L.load_features("1h"); F15 = L.load_features("15m")
FEE = MA.FEE

BREAK = os.environ.get("BREAK", "wick")                       # wick | close
SL_PCT = float(os.environ.get("SL_PCT", "0.1")) / 100.0
TP1_R = float(os.environ.get("TP1_R", "1.0")); TP2_R = float(os.environ.get("TP2_R", "2.0"))
BE_PCT = float(os.environ.get("BE_PCT", "0.1")) / 100.0
NY0 = int(os.environ.get("NY0", "13")); NY_END = int(os.environ.get("NY_END", "21"))   # session start / entry cutoff (UTC)

# --- 1h: FIRST NY candle per weekday -> (range_hi, range_lo, end_time, year) ---
h1 = F1["h"]; l1 = F1["l"]; st1 = F1["start"]; A1 = F1["A"]; n1 = F1["n"]
first_ny = {}
for i in range(n1):
    t = datetime.fromtimestamp(float(st1[i]), tz=timezone.utc)
    if t.weekday() >= 5 or not (NY0 <= t.hour < NY_END):
        continue
    d = t.date()
    if d in first_ny:                                         # keep only the first NY 1h candle of the day
        continue
    et = float(A1[i].get("end_time", 0.0) or 0.0) or float(st1[i])
    first_ny[d] = (float(h1[i]), float(l1[i]), et, t.year)

# --- 15m: weekday buckets grouped by UTC date, time-ordered ---
h15 = F15["h"]; l15 = F15["l"]; c15 = F15["c"]; st15 = F15["start"]; n15 = F15["n"]
days15 = defaultdict(list)
for j in range(n15):
    t = datetime.fromtimestamp(float(st15[j]), tz=timezone.utc)
    if t.weekday() >= 5:
        continue
    days15[t.date()].append((t.hour, j))


def walk(side, e, sl0, tp1, tp2, be, seq):
    """50% at TP1 -> SL to breakeven+BE_PCT -> 50% at TP2. Returns net fraction (fee once, round-trip)."""
    half = False; sl = sl0; realized = 0.0
    for j in seq:
        hi = float(h15[j]); lo = float(l15[j])
        if side > 0:
            if not half:
                if lo <= sl:                                  # full stop before TP1 (adverse-first)
                    return side * (sl / e - 1) - FEE
                if hi >= tp1:
                    realized += 0.5 * (side * (tp1 / e - 1)); half = True; sl = be
                    if hi >= tp2:                             # same candle also tags TP2
                        return realized + 0.5 * (side * (tp2 / e - 1)) - FEE
            else:
                if lo <= sl:                                  # runner stopped at breakeven+
                    return realized + 0.5 * (side * (sl / e - 1)) - FEE
                if hi >= tp2:
                    return realized + 0.5 * (side * (tp2 / e - 1)) - FEE
        else:
            if not half:
                if hi >= sl:
                    return side * (sl / e - 1) - FEE
                if lo <= tp1:
                    realized += 0.5 * (side * (tp1 / e - 1)); half = True; sl = be
                    if lo <= tp2:
                        return realized + 0.5 * (side * (tp2 / e - 1)) - FEE
            else:
                if hi >= sl:
                    return realized + 0.5 * (side * (sl / e - 1)) - FEE
                if lo <= tp2:
                    return realized + 0.5 * (side * (tp2 / e - 1)) - FEE
    if not seq:                                               # no data after entry
        return 0.0
    cl = float(c15[seq[-1]])                                  # EoD -> exit remaining at last close
    return (side * (cl / e - 1) - FEE) if not half else (realized + 0.5 * (side * (cl / e - 1)) - FEE)


rows = []; no_break = 0
for d, (rhi, rlo, et, yr) in first_ny.items():
    lst = days15.get(d)
    if not lst or rhi <= rlo:
        continue
    ent = None
    for k, (hr, j) in enumerate(lst):
        if float(st15[j]) < et:                               # must start after the 1h range candle closes
            continue
        if hr >= NY_END:                                      # entry window closed
            break
        hj = float(h15[j]); lj = float(l15[j]); cj = float(c15[j])
        up = (hj > rhi) if BREAK == "wick" else (cj > rhi)
        dn = (lj < rlo) if BREAK == "wick" else (cj < rlo)
        if up and dn:                                         # outside bar -> resolve by close side
            up = cj > rhi; dn = cj < rlo
            if not (up or dn):
                continue
        if up:
            ent = (1, rhi if BREAK == "wick" else cj, k); break
        if dn:
            ent = (-1, rlo if BREAK == "wick" else cj, k); break
    if ent is None:
        no_break += 1; continue
    side, e, k = ent
    sl0 = rlo * (1 - SL_PCT) if side > 0 else rhi * (1 + SL_PCT)
    R = abs(e - sl0)
    tp1 = e + side * R * TP1_R; tp2 = e + side * R * TP2_R; be = e * (1 + side * BE_PCT)
    seq = [j for _, j in lst[k + 1:]]
    net = walk(side, e, sl0, tp1, tp2, be, seq)
    rows.append(dict(net=net, side=side, yr=yr, win=net > 0, rangep=(rhi / rlo - 1.0)))


def rep(label, rs):
    k = len(rs)
    if k == 0:
        print("  %-12s n=0" % label); return
    nt = np.array([r["net"] for r in rs]); w = 100.0 * sum(r["win"] for r in rs) / k
    tot = (np.prod(1 + nt) - 1) * 100; gg = nt[nt > 0].sum(); ll = -nt[nt < 0].sum()
    pf = (gg / ll) if ll > 0 else float("inf"); bal = MA.account(list(nt))
    print("  %-12s n=%4d  win %5.1f%%  net %+8.1f%%  PF %.2f  mean %+.3f%%  END $%10.0f (%+.1f%%)"
          % (label, k, w, tot, pf, nt.mean() * 100, bal, (bal - MA.B0) / MA.B0 * 100))


print("=" * 116)
print("NY ORB scale-out | range=first NY 1h candle (h>=%dUTC) | %s break -> 15m entry (<%dUTC) | SL %.1f%% past opp edge | TP1 1:%.1f TP2 1:%.1f BE+%.1f%%"
      % (NY0, BREAK, NY_END, SL_PCT * 100, TP1_R, TP2_R, BE_PCT * 100))
print("  weekdays | 1h range + 15m entry/walk recon | trades=%d  no-break days=%d  avg range %.2f%%"
      % (len(rows), no_break, float(np.mean([abs(r["rangep"]) for r in rows])) * 100 if rows else 0.0))
print("=" * 116)
rep("ALL", rows); rep("LONG", [r for r in rows if r["side"] > 0]); rep("SHORT", [r for r in rows if r["side"] < 0])
rep("2025", [r for r in rows if r["yr"] == 2025]); rep("2026", [r for r in rows if r["yr"] == 2026])
if rows:
    nt = np.array([r["net"] for r in rows])
    mm = np.array([rng.choice(nt, size=len(nt), replace=True).mean() for _ in range(10000)]) * 100
    lo, hi = np.percentile(mm, [2.5, 97.5])
    print("  bootstrap mean net/trade %+.4f%%  95%% CI [%+.4f%%, %+.4f%%]  -> %s"
          % (nt.mean() * 100, lo, hi, "clears 0" if lo > 0 else ("sig NEGATIVE" if hi < 0 else "includes 0")))
print("=" * 116)
