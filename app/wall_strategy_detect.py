# -*- coding: utf-8 -*-
"""WALL STRATEGY — a 5m confluence entry marker. On a wall/radar VISIT that meets:
  [ (1) contains >= 1 Big or Crazy Wall-Absorption (app.crazy_wall_detect), same side as the wall
    OR
    (2) its absorption tally over the visit favours the wall's side — BUY wall (support): buyers-absorbed-sellers >
        sellers-absorbed-buyers; SELL wall (resistance): the mirror (sellers-absorbed-buyers > buyers-absorbed-sellers);
        (a candle 'buyers absorbed sellers' = sellers aggressive Tape-S>Tape-B but closed UP; mirror for the other) ]
  AND
  (3) an entry fires in the wall's direction on a candle within the visit — an Easy Gold OR Pure Aggression signal,
      OR a strong-easy candle (absorption A < -1) moving in the position direction (bullish at a buy wall / bearish
      at a sell wall).
-> BUY wall -> LONG (green ▲); SELL wall -> SHORT (red ▼), placed on that entry candle. First trigger per visit.

DESCRIPTIVE marker for eyeballing the setup (NOT backtested yet). detect(buckets, walls) -> [{i, side('long'|'short'), wall_side}].
"""
from __future__ import annotations


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _tape(b):
    dur = max(1.0, _f(b.get("end_time")) - _f(b.get("start_time")))
    return sum(b.get("sz_cb") or []) / dur, sum(b.get("sz_cs") or []) / dur


def detect(buckets, walls, skip_last=False, entry_absorbr=True, cond_or=True, require_def_fade=False):
    """`walls` = app.absorption_level_detect.detect() marks. Returns first-per-visit entry triggers.
    entry_absorbr=False drops the (absorption A<-1 in-direction) entry, leaving only Easy Gold / Pure Aggression.
    cond_or=False makes (1)&(2) an AND (both required) instead of OR. require_def_fade=True adds: the DEFENDER's tape
    rate (buyers@buy wall / sellers@sell wall) must have DECREASED over the visit (2nd-half mean < 1st-half mean)."""
    n = len(buckets)
    if n < 2 or not walls:
        return []
    try:
        from app import crazy_wall_detect as CW, easy_gold_detect as EG, pure_aggression_detect as PA, absorption as ABS
        events = CW.detect(buckets, walls, skip_last=skip_last)          # Big/Crazy absorptions (i, wall_side, tier)
        abs_S = sorted(int(e["i"]) for e in events if e.get("wall_side") == "S")
        abs_R = sorted(int(e["i"]) for e in events if e.get("wall_side") == "R")
        entry_long = set(); entry_short = set()                          # Easy Gold OR Pure Aggression, by direction
        for g in EG.from_walls(buckets, walls) + PA.from_walls(buckets, walls):
            (entry_long if g.get("side") == "long" else entry_short).add(int(g["i"]))
        out = []; seen = set()
        for w in walls:
            side = w.get("side", "R")                                    # S = buy wall/support (LONG) / R = sell wall/resistance (SHORT)
            _p = _f(w.get("price")); _bd = _f(w.get("band"))
            r_lo = _p - 3.0 * _bd; r_hi = _p + 3.0 * _bd                 # radar bounds (for a structural SL just beyond them)
            absbars = abs_S if side == "S" else abs_R
            want = entry_long if side == "S" else entry_short
            for r in w.get("radar_runs", ()):
                if len(r) < 2:
                    continue
                rk0 = int(r[0]); rk1 = min(int(r[1]), n - 1)
                if rk1 < rk0:
                    continue
                has_abs = any(rk0 <= a <= rk1 for a in absbars)         # (1) >= 1 same-side Big/Crazy absorption in the visit
                n_ba_s = 0; n_sa_b = 0                                   # (2) absorption tally over the visit
                for k in range(rk0, rk1 + 1):
                    b = buckets[k]; tb, ts = _tape(b)
                    o = _f(b.get("open", b.get("open_price"))); c = _f(b.get("close", b.get("close_price")))
                    if ts > tb and c > o:
                        n_ba_s += 1
                    elif tb > ts and c < o:
                        n_sa_b += 1
                tally_ok = (n_ba_s > n_sa_b) if side == "S" else (n_sa_b > n_ba_s)
                if not ((has_abs or tally_ok) if cond_or else (has_abs and tally_ok)):   # (1) OR/AND (2)
                    continue
                if require_def_fade:                                     # defender tape rate must DECREASE over the visit
                    Ln = rk1 - rk0 + 1
                    if Ln < 2:
                        continue
                    dr = [(_tape(buckets[k])[0] if side == "S" else _tape(buckets[k])[1]) for k in range(rk0, rk1 + 1)]
                    m = Ln // 2
                    if sum(dr[m:]) / (Ln - m) >= sum(dr[:m]) / m:        # 2nd-half mean >= 1st-half -> not decreased
                        continue
                for j in range(rk0, rk1 + 1):                            # (3) first entry in the wall's dir: EG / PA / (A<-1 in-dir)
                    if j in seen:
                        continue
                    trig = j in want                                    # Easy Gold OR Pure Aggression
                    if not trig and entry_absorbr:
                        A = ABS.absorption(buckets, j)[0]               # OR a strong-easy candle in the position direction
                        if A is not None and A < -1.0:
                            bj = buckets[j]
                            _o = _f(bj.get("open", bj.get("open_price"))); _c = _f(bj.get("close", bj.get("close_price")))
                            trig = (_c > _o) if side == "S" else (_c < _o)
                    if trig:
                        out.append({"i": j, "side": "long" if side == "S" else "short", "wall_side": side,
                                    "r_lo": r_lo, "r_hi": r_hi})
                        seen.add(j)
                        break
        out.sort(key=lambda e: e["i"])
        return out
    except Exception:
        return []
