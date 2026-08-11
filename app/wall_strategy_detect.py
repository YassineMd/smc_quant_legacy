# -*- coding: utf-8 -*-
"""WALL STRATEGY — a 5m confluence entry marker. On a wall/radar VISIT that meets ALL of:
  (1) contains >= 1 Big or Crazy Wall-Absorption (app.crazy_wall_detect), same side as the wall;
  (2) its absorption tally over the visit favours the wall's side — BUY wall (support): buyers-absorbed-sellers >
      sellers-absorbed-buyers; SELL wall (resistance): the mirror (sellers-absorbed-buyers > buyers-absorbed-sellers);
      (a candle 'buyers absorbed sellers' = sellers aggressive Tape-S>Tape-B but closed UP; mirror for the other);
  (3) an entry fires in the wall's direction: an Easy Gold OR Pure Aggression signal (long at a buy wall / short at a
      sell wall) on a candle within the visit.
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


def detect(buckets, walls, skip_last=False):
    """`walls` = app.absorption_level_detect.detect() marks. Returns first-per-visit entry triggers."""
    n = len(buckets)
    if n < 2 or not walls:
        return []
    try:
        from app import crazy_wall_detect as CW, easy_gold_detect as EG, pure_aggression_detect as PA
        events = CW.detect(buckets, walls, skip_last=skip_last)          # Big/Crazy absorptions (i, wall_side, tier)
        abs_S = sorted(int(e["i"]) for e in events if e.get("wall_side") == "S")
        abs_R = sorted(int(e["i"]) for e in events if e.get("wall_side") == "R")
        entry_long = set(); entry_short = set()                          # Easy Gold OR Pure Aggression, by direction
        for g in EG.from_walls(buckets, walls) + PA.from_walls(buckets, walls):
            (entry_long if g.get("side") == "long" else entry_short).add(int(g["i"]))
        out = []; seen = set()
        for w in walls:
            side = w.get("side", "R")                                    # S = buy wall/support (LONG) / R = sell wall/resistance (SHORT)
            absbars = abs_S if side == "S" else abs_R
            want = entry_long if side == "S" else entry_short
            for r in w.get("radar_runs", ()):
                if len(r) < 2:
                    continue
                rk0 = int(r[0]); rk1 = min(int(r[1]), n - 1)
                if rk1 < rk0:
                    continue
                if not any(rk0 <= a <= rk1 for a in absbars):            # (1) >= 1 same-side Big/Crazy absorption in the visit
                    continue
                n_ba_s = 0; n_sa_b = 0                                   # (2) absorption tally over the visit
                for k in range(rk0, rk1 + 1):
                    b = buckets[k]; tb, ts = _tape(b)
                    o = _f(b.get("open", b.get("open_price"))); c = _f(b.get("close", b.get("close_price")))
                    if ts > tb and c > o:
                        n_ba_s += 1
                    elif tb > ts and c < o:
                        n_sa_b += 1
                if (n_ba_s <= n_sa_b) if side == "S" else (n_sa_b <= n_ba_s):
                    continue
                for j in range(rk0, rk1 + 1):                            # (3) first Easy-Gold/Pure-Aggression entry in the wall's dir
                    if j in want and j not in seen:
                        out.append({"i": j, "side": "long" if side == "S" else "short", "wall_side": side})
                        seen.add(j)
                        break
        out.sort(key=lambda e: e["i"])
        return out
    except Exception:
        return []
