# -*- coding: utf-8 -*-
"""CONFLICT VP: a volume profile between two CONFLICT boxes, drawn with the HLH Volume Profile's lines.

The user, 2026-09-26: "This indicator creates VP from the last 2 conflicts/merged conflicts ... the ones preceeding
should be at least 10 bars away from it ... if they have the same high or low we skip ... we will draw our conflict VP
from conflict 2 to conflict 1, the High will be 101 and the low will be 99 ... we gonna draw the same lines as HLH VP
indicator". Then: "add a toggle so that I am able to see the previous ones ... each VP lines should be of different
color ... the VPs should NOT be overlapping", "the select conflict should normally be connected to the one that
happened at 7am, we shouldnt have a gap", and "when a conflict VP is draw it stays fix it shouldnt change".

  CONFLICT 1  a conflict RUN (consecutive conflict bars are one box, as the tablet draws them) once it is COMPLETE:
              the bar after it has closed without being a conflict. A forming candle never makes a VP: its tapes move
              until it closes, and one that read "conflict" for 8 s at 11:08:02 swapped the VP there and back.
  CONFLICT 2  walking back from conflict 1: the first conflict that ends at least `min_gap` bars before conflict 1
              starts and whose high AND low both differ from conflict 1's (a shared high or low -> skipped).
  THE PAIR RULE (user 2026-09-26: "from conflict 1 we should take either the high/low and from conflict 2 we should
              either take the high or low") -- each conflict gives ONE end of the VP's range:
              * conflict 1 INSIDE that conflict 2 (lower high AND higher low): conflict 1 gives neither end, so it makes
                NO VP -- "conflict 1 had to be skipped and considered as part of the previous VP untill a conflict emerges
                that respects the rules": the previous VP's lines run on over it (its frozen levels do not move);
              * conflict 1 ENGULFING that conflict 2 (higher high AND lower low): that conflict 2 gives neither end -- it
                is passed over ("engulfed") and the walk back goes on (the user's choice, 2026-09-26).
              So every VP has exactly one end from each conflict, and exactly one arrow.
  THE PRIORITY RULE (user 2026-09-26, on 03:13:48 against 03:18:47: "conflict 3:13 has the priority because it offers
              a lower low than 3:19 and 3:09"; their choice "Beyond the VP") -- a new conflict whose walk PASSES OVER
              the current VP's conflict 1 (under min_gap bars from it, or sharing its high / low) would replace that
              VP; it may only when it offers a level BEYOND that VP's range, a lower low or a higher high. Otherwise it
              is part of that VP ("within"): no VP of its own, the current VP's lines run on over it. Before this, the
              newest conflict always won: 03:18:47 (low 120.46) replaced 03:13:48's VP (low 119.47).
  A conflict's high / low are its RED BOX's: the reach to the closest previous lime / purple area
              (terminal._conflict_boxes), held to at least its own candles, as the tablet frames it.
  THE VP      from conflict 2's first bar to conflict 1's last, from the lower of the two lows to the higher of the
              two highs. Its volume is the flow store's 1 s bins over EXACTLY the seconds those cycles own: each
              second's taker buy + sell $ spread evenly over the rows between that second's true low and high, inside
              the VP's range (a second traded wholly outside it is not the VP's). Rows are whole ticks, about HLH_ROWS
              of them, so no row holds more ticks than its neighbour does.
  THE LINES   the HLH rules: the POC is the busiest row (a run of equal rows is one POC, reported at its middle); the
              value area grows from it to the bigger neighbouring row (tie -> above) until va_pct % of the volume is
              inside (hlh_profile.value_area, the HLH's own function); the outer value area does the same to va2_pct %.
              VAH / VAL are the top / bottom TICK of the value area's rows.
  FROZEN      everything is decided ONCE, the moment a conflict completes (Frozen.freeze_new): its box, its conflict 2
              and its VP's lines. Nothing recomputes them afterwards -- not late prints, not LINES IMPACT's areas
              arriving with the wall grid, not an engine restart (the store is a file). The red boxes of the frozen
              conflicts are drawn from the same record, so a VP's high / low always sit on its boxes.
  THE CHAIN   the current VP is the newest conflict's that HAS a VP; the previous ones follow the conflict-2 links back
              (each older VP's conflict 1 IS the newer one's conflict 2: no gap). A VP is DRAWN from its conflict 2's
              END up to where the next newer VP begins -- normally its own conflict 1's end, further when conflicts it
              absorbed (no VP of their own) lie beyond it; the current VP runs to the newest frozen conflict's end. So
              the chain never gaps or overlaps. The one change the rule itself makes: a new conflict that shares a
              high / low with the last one (or is under min_gap bars from it) links to an OLDER conflict, and its VP
              covers -- replaces -- the last VP (the user's own example: 100-101 skips 100-100.5), but only when it
              reaches beyond that VP (THE PRIORITY RULE).
  UNTESTED AREAS (user 2026-09-26, a sub-toggle): "after every conflict VP that ends, in the future the above/below
              yellow area to be tested, we are always expecting the price to come back to it ... a green arrow conflict
              VP ... we are expecting the below yellow line area to be tested, and if it was a red arrow conflict VP we
              gonna be expecting its above yellow area to be tested", then "dont limit it to 24h ... when i toggle it on
              it should hide the previous VPs that got already tested, omit the most recent VP we keep it". See
              Frozen.untested (what has been tested is remembered, so the check reaches past the 72 h of candles).
  EXPECTED TEST (user 2026-09-26, a sub-toggle): "the points we expect to be tested are the lines impact area / if
              its an up arrow VP we expect the lime green lines impact areas to be tested and for the down arrow VP we
              expect the purple areas from lines impact to be tested. / so when this indictor is toggled on it expends
              these areas up to the point where the conflict VP ends". See expected_areas().
  THE BIAS    (user 2026-09-26, the tablet's Market Position buttons): "detect the last break of the conflict VP ...
              a candle that closes above/below a most recent high/low of the conflict VP (the thickest lines of the
              conflict VP) / if above we have a bullish bias / if below we have a bearish bias". Frozen.bias(): every
              CLOSED candle is judged against the conflict VP that was the most recent one when it closed; the last
              close beyond its high / low decides. See Frozen.bias.
"""
from __future__ import annotations

import json
import os
from typing import List, Optional, Tuple

import numpy as np

from . import hlh_profile as H

VER = 1
RULE = 3      # the rule the records were decided with: 2 = + the pair rule (inside -> no VP, engulfed -> passed),
              # 3 = + the priority rule (a conflict passing over the current VP's conflict 1 within its range -> no VP)


def runs(conf) -> List[Tuple[int, int]]:
    """[(a, b)] inclusive cycle-index ranges of consecutive conflict bars, oldest first."""
    cf = np.asarray(conf).astype(bool)
    if not cf.any():
        return []
    x = np.diff(np.concatenate(([0], cf.astype(np.int8), [0])))
    return list(zip(np.flatnonzero(x == 1).tolist(), (np.flatnonzero(x == -1) - 1).tolist()))


def run_box(a: int, b: int, cfh, cfl, pxh, pxl) -> Tuple[float, float]:
    """(high, low) of the red box of run [a, b]: the engine's reach (cfh / cfl), held to at least the run's candles."""
    hs = np.concatenate((np.asarray(cfh[a:b + 1], dtype=np.float64), np.asarray(pxh[a:b + 1], dtype=np.float64)))
    ls = np.concatenate((np.asarray(cfl[a:b + 1], dtype=np.float64), np.asarray(pxl[a:b + 1], dtype=np.float64)))
    hs = hs[np.isfinite(hs) & (hs > 0)]
    ls = ls[np.isfinite(ls) & (ls > 0)]
    if not hs.size or not ls.size:
        return float("nan"), float("nan")
    return float(hs.max()), float(ls.min())


def profile(base: int, bin_secs: float, buy, sell, pxh, pxl, t0: float, t1: float, lo: float, hi: float,
            tick: float, rows_target: int, va_pct: float, va2_pct: float) -> Optional[dict]:
    """The VP of the 1 s bins from the one HOLDING t0 up to, not including, the one holding t1, on [lo, hi]:
    {poc, vah, val, vah2, val2, usd, rows, k} (k = ticks per row), or None when nothing traded inside it.

    ⚠ The cycles' own second rule (flow_pane._cross_scan, "one second belongs to one cycle"): a cycle's start is its
    CROSSING time, a fraction into the second the lines crossed in, and the cycle owns that second through the second
    before the next cycle's crossing second. So t0 = the first cycle's start and t1 = the last cycle's end (the next
    crossing, or the end of the tape for the forming one) are FLOORED: rounding them up dropped the first cycle's
    crossing second and took the next cycle's -- the seconds holding the prints that make the lines cross (measured
    live 2026-09-26: $81.81M against the 27 cycles' $81.62M)."""
    tick = float(tick)
    lo_t = int(round(float(lo) / tick)); hi_t = int(round(float(hi) / tick))
    span = hi_t - lo_t                                      # ticks from the low to the high
    if span < 0:
        return None
    n = int(np.size(buy))
    i0 = max(0, int(np.floor(float(t0) / bin_secs)) - int(base))
    i1 = min(n, int(np.floor(float(t1) / bin_secs)) - int(base))
    if i1 <= i0:
        return None
    v = np.asarray(buy[i0:i1], dtype=np.float64) + np.asarray(sell[i0:i1], dtype=np.float64)
    h = np.asarray(pxh[i0:i1], dtype=np.float64)
    l = np.asarray(pxl[i0:i1], dtype=np.float64)
    ok = (v > 0) & np.isfinite(h) & np.isfinite(l) & (h > 0) & (l > 0)
    if not ok.any():
        return None
    v, h, l = v[ok], h[ok], l[ok]
    jT = np.round(h / tick).astype(np.int64) - lo_t          # tick levels above the VP's low
    jB = np.round(l / tick).astype(np.int64) - lo_t
    jB = np.minimum(jB, jT)
    inside = (jT >= 0) & (jB <= span)                         # a second that traded wholly outside is not the VP's
    if not inside.any():
        return None
    k = max(1, int(round((span + 1) / float(max(1, int(rows_target))))))
    rows = span // k + 1
    iB = np.clip(jB[inside], 0, span) // k
    iT = np.clip(jT[inside], 0, span) // k
    vp = H._spread(v[inside], iB, iT, rows)                 # the HLH's own spreading: exact sums, empty rows stay 0
    mx = float(vp.max()) if vp.size else 0.0
    if not mx > 0:
        return None
    pS = int(np.argmax(vp)); pE = pS
    while pE < rows - 1 and vp[pE + 1] == mx:
        pE += 1

    def top(r):                                             # the highest tick of row r
        return lo_t + min(span, r * k + k - 1)

    poc = 0.5 * ((lo_t + pS * k) + top(pE)) * tick
    vLo, vHi = H.value_area(vp, 0, rows - 1, float(va_pct))
    wLo, wHi = H.value_area(vp, 0, rows - 1, float(va2_pct))
    return {"poc": poc, "vah": top(vHi) * tick, "val": (lo_t + vLo * k) * tick,
            "vah2": top(wHi) * tick, "val2": (lo_t + wLo * k) * tick,
            "usd": float(vp.sum()), "rows": int(rows), "k": int(k)}


def expected_areas(drawn, by_t0: dict, areas, tol: float) -> List[Tuple[int, int, float, float, float, float]]:
    """EXPECTED TEST (user 2026-09-26: "as I told you we expect the price to test the area, but the points we expect to
    be tested are the lines impact area / if its an up arrow VP we expect the lime green lines impact areas to be tested
    and for the down arrow VP we expect the purple areas from lines impact to be tested. / so when this indictor is
    toggled on it expends these areas up to the point where the conflict VP ends").

    `drawn` = Frozen.drawn() (newest first), `by_t0` = the records by t0 (for each VP's conflict 2), `areas` = LINES
    IMPACT's BRIGHT areas over the read, (side, t0, t1, low, high) with +1 lime (buyers) / -1 purple (sellers) -- the
    ones the tablet's price pane boxes. A VP with a GREEN arrow (its conflict 1 made the high) takes the LIME areas, a
    RED arrow the PURPLE ones, that START inside it: from its conflict 2's first bar to where its lines end (x1).
    Returns [(k, side, a_t0, a_t1, low, high)] -- k = the VP's index in `drawn`; the tablet draws each area on to x1."""
    out = []
    for k, (it, x0, x1) in enumerate(drawn):
        a = area_of(it, tol)
        if a is None:
            continue
        side = a[0]                                       # +1 green arrow -> lime, -1 red arrow -> purple
        c2 = by_t0.get(float(it["c2"])) if it.get("c2") is not None else None
        start = float(c2["t0"]) if c2 is not None else float(x0)
        for (sd, a0, a1, lo, hi) in (areas or ()):
            if int(sd) == side and start - 0.5 <= float(a0) < float(x1):
                out.append((k, side, float(a0), float(a1), float(lo), float(hi)))
    return out


def area_of(it: dict, tol: float) -> Optional[Tuple[int, float, float, float]]:
    """A VP's EXPECTED AREA (user 2026-09-26: "for example we have a green arrow conflict VP that finished because
    another one just got created. we are expecting the below yellow line area to be tested, and if it was a red arrow
    conflict VP we gonna be expecting its above yellow area to be tested"): (side, lo, hi, mid) -- side +1 = BELOW its
    yellow midline, midline .. its low (green arrow: conflict 1 made the high); -1 = ABOVE it, midline .. its high (red
    arrow: conflict 1 made the low). None when the VP has no single arrow (never under the pair rule)."""
    v = it.get("vp")
    if v is None:
        return None
    vhi, vlo = float(v["hi"]), float(v["lo"])
    up = abs(vhi - float(it["hi"])) < tol
    dn = abs(vlo - float(it["lo"])) < tol
    if up == dn:
        return None
    mid = 0.5 * (vhi + vlo)
    return (1, vlo, mid, mid) if up else (-1, mid, vhi, mid)


class Frozen:
    """The FROZEN conflicts, oldest first, each decided once when it completed and never touched again:
    {t0, tb, te: its first bar's start, last bar's start, last bar's end; hi, lo: its box; n: bars; c2: the t0 of its
    conflict 2 or None; skip: [[t0, why], ...] the conflicts passed over on the way back; vp: None or {lo, hi, poc,
    vah, val, vah2, val2, usd, rows, k, d0, d1} with d0 / d1 the span its lines are DRAWN over}.

    `path` keeps it across engine restarts (JSON, rewritten whole -- a few hundred records at most). Conflicts older
    than `keep_secs` are dropped. Only conflicts NEWER than the newest frozen one are ever added: the frozen history
    is never rewritten, whatever the live flags later say about it."""

    def __init__(self, path: Optional[str] = None, keep_secs: float = 4 * 86400.0):
        self.path = path
        self.keep = float(keep_secs)
        self.items: List[dict] = []
        self.rule = RULE                                  # a store without one predates the pair rule: see redecide()
        # THE UNTESTED AREAS' memory (see untested): t0 -> when its area was found TESTED (for good), and t0 -> the time
        # up to which it was verified UNTESTED -- so a VP whose end has left the 72 h of candles is still known
        self.tested: dict = {}
        self.watch: dict = {}
        self.load()

    # ---------------------------------------------------------------- persistence
    def load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            if int(d.get("ver", 0)) == VER:
                self.items = sorted((dict(x) for x in (d.get("items") or [])), key=lambda x: float(x["t0"]))
                self.rule = int(d.get("rule", 1))
                self.tested = {round(float(k), 3): float(v) for k, v in (d.get("tested") or {}).items()}
                self.watch = {round(float(k), 3): float(v) for k, v in (d.get("watch") or {}).items()}
        except Exception:
            self.items = []

    def save(self) -> None:
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"ver": VER, "rule": self.rule, "items": self.items,
                       "tested": {"%.3f" % k: v for k, v in self.tested.items()},
                       "watch": {"%.3f" % k: v for k, v in self.watch.items()}}, fh, separators=(",", ":"))
        os.replace(tmp, self.path)

    def prune(self, now: float) -> bool:
        n0 = len(self.items)
        self.items = [x for x in self.items if float(x["te"]) >= float(now) - self.keep]
        keys = {round(float(x["t0"]), 3) for x in self.items}
        self.tested = {k: v for k, v in self.tested.items() if k in keys}
        self.watch = {k: v for k, v in self.watch.items() if k in keys}
        return len(self.items) != n0

    def newest_t0(self) -> Optional[float]:
        return float(self.items[-1]["t0"]) if self.items else None

    # ---------------------------------------------------------------- deciding
    @staticmethod
    def _decide(before: List[dict], hi: float, lo: float, tol: float, near):
        """Conflict 1 with box (hi, lo), walking back over the conflicts `before` it (oldest first; `near(f)` -> f ends
        under min_gap bars before conflict 1 starts): (c2 or None, skip list, inside -- the t0 of the conflict it lies
        inside, else None)."""
        skip = []
        for f in reversed(before):
            fh_, fl_ = float(f["hi"]), float(f["lo"])
            if near(f):
                skip.append([f["t0"], "near"])
                continue
            if abs(fh_ - hi) < tol:
                skip.append([f["t0"], "same high"])
                continue
            if abs(fl_ - lo) < tol:
                skip.append([f["t0"], "same low"])
                continue
            if fh_ > hi and fl_ < lo:                     # INSIDE it: conflict 1 would give neither end -> no VP
                return None, skip, f["t0"]
            if hi > fh_ and lo < fl_:                     # ENGULFS it: it would give neither end -> walk on
                skip.append([f["t0"], "engulfed"])
                continue
            return f, skip, None
        return None, skip, None

    @staticmethod
    def _within(it: dict, cur: dict, tol: float) -> bool:
        """THE PRIORITY RULE: `it`, whose walk PASSED OVER `cur` -- the current VP's conflict 1 -- (under min_gap bars
        from it, or sharing its high / low), would replace that VP; it may only when it offers a level BEYOND that VP's
        range, a lower low or a higher high. True = it does not: it is part of that VP. (An engulfed pass always
        reaches beyond: conflict 1 gives one end of its VP.)"""
        if not any(float(k) == float(cur["t0"]) for k, _w in (it.get("skip") or [])):
            return False
        v = cur["vp"]
        return float(it["lo"]) >= float(v["lo"]) - tol and float(it["hi"]) <= float(v["hi"]) + tol

    def _current(self, before: Optional[int] = None) -> Optional[dict]:
        """The current VP's record among the first `before` records (all by default): the newest with a VP."""
        for f in reversed(self.items[:before] if before is not None else self.items):
            if f.get("c2") is not None and f.get("vp") is not None:
                return f
        return None

    @staticmethod
    def _vp(c2: dict, it: dict, base: int, bin_secs: float, buy, sell, bpxh, bpxl, tick: float, rows_target: int,
            va_pct: float, va2_pct: float) -> Optional[dict]:
        """The frozen VP of conflict 1 `it` with conflict 2 `c2`: its lines from the bins, drawn from c2's end."""
        lo, hi = float(it["lo"]), float(it["hi"])
        vlo, vhi = min(lo, float(c2["lo"])), max(hi, float(c2["hi"]))
        vp = profile(base, bin_secs, buy, sell, bpxh, bpxl, float(c2["t0"]), float(it["te"]), vlo, vhi, tick,
                     rows_target, va_pct, va2_pct)
        if vp is None:
            return None
        return {"lo": round(vlo, 6), "hi": round(vhi, 6), "poc": round(vp["poc"], 6), "vah": round(vp["vah"], 6),
                "val": round(vp["val"], 6), "vah2": round(vp["vah2"], 6), "val2": round(vp["val2"], 6),
                "usd": round(vp["usd"], 2), "rows": vp["rows"], "k": vp["k"], "d0": float(c2["te"]), "d1": float(it["te"])}

    def redecide(self, base: int, bin_secs: float, buy, sell, bpxh, bpxl, tick: float, rows_target: int,
                 va_pct: float, va2_pct: float) -> Tuple[int, int]:
        """Re-decide every record's pairing with the CURRENT rule from what it RECORDED when it froze -- its box, the
        conflicts it passed over (near / same high / same low: the new rule passes over the same ones, in the same
        order) and the first one it did not, its old conflict 2. The new rule's walk goes on from there: inside it ->
        no VP; engulfing it -> passed over, and on back (every older conflict is further than min_gap bars away: the
        distance only grows going back). So no bar is counted again and the boxes never change. A record whose conflict
        2 is unchanged keeps its VP exactly; a NEW conflict 2 gets its VP from the bins, or none when the bins no longer
        hold its start. Then, for a store decided before THE PRIORITY RULE, oldest first: a record whose VP would
        replace the current one without reaching beyond its range loses its VP ("within"). Returns (records changed,
        of them left without a VP for want of bins); the store is then marked as decided by RULE."""
        tol = 0.5 * float(tick)
        idx = {float(x["t0"]): i for i, x in enumerate(self.items)}
        first = float(base) * float(bin_secs)            # the oldest second the bins hold
        changed = set()
        nobins = 0
        for i, it in enumerate(self.items if self.rule < 2 else []):
            old = it.get("c2")
            j = idx.get(float(old)) if old is not None else None
            if j is None or j >= i:
                continue                                  # no conflict 2 then, none now (or its own was pruned)
            c2, more, inside = self._decide(self.items[:j + 1], float(it["hi"]), float(it["lo"]), tol, lambda f: False)
            if c2 is not None and float(c2["t0"]) == float(old):
                continue                                  # its pairing stands: record and VP unchanged
            it["skip"] = list(it.get("skip") or []) + more
            it["inside"] = inside
            it["c2"] = c2["t0"] if c2 is not None else None
            it["vp"] = None
            if c2 is not None:
                if float(c2["t0"]) >= first:
                    it["vp"] = self._vp(c2, it, base, bin_secs, buy, sell, bpxh, bpxl, tick, rows_target, va_pct,
                                        va2_pct)
                else:
                    nobins += 1
            changed.add(float(it["t0"]))
        if self.rule < 3:
            cur = None
            for it in self.items:
                if it.get("c2") is None or it.get("vp") is None:
                    continue
                if cur is not None and self._within(it, cur, tol):
                    it["vp"] = None
                    it["within"] = cur["t0"]
                    changed.add(float(it["t0"]))
                    continue
                cur = it
        self.rule = RULE
        return len(changed), nobins

    # ---------------------------------------------------------------- freezing
    def freeze_new(self, t, t_end, done, conf, cfh, cfl, pxh, pxl, now: float, settle: float, base: int,
                   bin_secs: float, buy, sell, bpxh, bpxl, tick: float, min_gap: int, rows_target: int,
                   va_pct: float, va2_pct: float) -> List[dict]:
        """Freeze every conflict run of the read that has COMPLETED -- the bar after it closed at least `settle`
        seconds ago without being a conflict -- and starts after the newest frozen one, oldest first. Each gets its
        box now, its conflict 2 by the rule over the FROZEN conflicts (their frozen boxes; bars counted on this
        read), and its VP's lines from the bins now. Returns the records added."""
        t = np.asarray(t, dtype=np.float64)
        n = int(t.size)
        tol = 0.5 * float(tick)
        last = self.newest_t0()
        added = []
        for a, b in runs(conf):
            if b + 1 >= n or not bool(done[b + 1]) or float(now) - float(t_end[b + 1]) < float(settle):
                continue                                  # still open: the bar after it is forming, or just closed
            t0 = float(t[a])
            if last is not None and t0 <= last + 0.5:
                continue                                  # frozen already, or older than the frozen history
            hi, lo = run_box(a, b, cfh, cfl, pxh, pxl)
            if not (np.isfinite(hi) and np.isfinite(lo)):
                continue
            it = {"t0": round(t0, 3), "tb": round(float(t[b]), 3), "te": round(float(t_end[b]), 3),
                  "hi": round(hi, 6), "lo": round(lo, 6), "n": int(b - a + 1), "c2": None, "skip": [], "vp": None,
                  "inside": None, "within": None}
            def near(f, a=a):
                if float(f["tb"]) < float(t[0]) - 0.5:
                    return False                          # older than the read: more than a read away
                return a - int(np.searchsorted(t, float(f["tb"]) - 0.5)) < int(min_gap)   # bars, its last to our first
            c2, it["skip"], it["inside"] = self._decide(self.items, hi, lo, tol, near)
            if c2 is not None:
                it["c2"] = c2["t0"]
                cur = self._current()
                if cur is not None and self._within(it, cur, tol):
                    it["within"] = cur["t0"]             # THE PRIORITY RULE: part of the current VP, no VP of its own
                else:
                    it["vp"] = self._vp(c2, it, base, bin_secs, buy, sell, bpxh, bpxl, tick, rows_target, va_pct,
                                        va2_pct)
            self.items.append(it)
            last = t0
            added.append(it)
        return added

    # ---------------------------------------------------------------- reading
    def chain(self) -> List[dict]:
        """The VPs to draw, newest first: from the newest frozen conflict, follow the conflict-2 links; a conflict
        without a VP hands on to the frozen conflict just before it."""
        idx = {float(x["t0"]): i for i, x in enumerate(self.items)}
        out = []
        i = len(self.items) - 1
        while i >= 0:
            it = self.items[i]
            if it.get("c2") is None or it.get("vp") is None:
                i -= 1
                continue
            out.append(it)
            j = idx.get(float(it["c2"]))
            if j is None or j >= i:
                break                                     # its conflict 2 has been dropped (older than keep)
            i = j
        return out

    def drawn(self) -> List[Tuple[dict, float, float]]:
        """(record, x0, x1) of every VP to draw, newest first: from its conflict 2's end to where the next newer VP
        begins -- its own conflict 1's end, or further over the conflicts it absorbed (no VP of their own); the
        current VP (the first) runs to the newest frozen conflict's end. Never a gap, never an overlap."""
        out = []
        chn = self.chain()
        for k, it in enumerate(chn):
            v = it["vp"]
            x1 = float(v["d1"])
            if k == 0:
                x1 = max(x1, float(self.items[-1]["te"]))
            else:
                x1 = max(x1, float(chn[k - 1]["vp"]["d0"]))
            out.append((it, float(v["d0"]), x1))
        return out

    def untested(self, drawn, t, pxh, pxl, now: float, tick: float) -> Tuple[dict, bool]:
        """THE UNTESTED AREAS of the FINISHED conflict VPs (user 2026-09-26: "after every conflict VP that ends, in the
        future the above/below yellow area to be tested, we are always expecting the price to come back to it ... so
        this indicator when toggled in checks for the below/above yellow line areas that were not tested, and hides the
        one that got tested", then "actually dont limit it to 24h and as I told you when i toggle it on it should hide
        the previous VPs that got already tested, omit the most recent VP we keep it").

        `drawn` = drawn() (newest first; the first is the CURRENT VP: it has not ended -- the tablet always keeps it).
        Every other VP ENDED where its lines stop, where the next newer VP begins (x1); its area (area_of) is TESTED by
        the first price that trades at or under its midline (green arrow) / at or over it (red arrow) from x1 on -- a
        candle's low / high, so a wick counts, the forming candle too. No time limit.
        The read holds 72 h of candles and the records 4 days, so the store REMEMBERS: `tested` (t0 -> when; tested
        once is tested for good) and `watch` (t0 -> the time up to which it was verified untested). A VP whose end lies
        before the first candle held is checked from that candle on only if it was watched into the read; otherwise
        it cannot be told and is not returned (the tablet hides it with the tested ones).
        Returns ({index in drawn: (side, lo, hi, x1)} of the areas NOT tested, whether `tested` grew)."""
        t = np.asarray(t, dtype=np.float64)
        hs = np.asarray(pxh, dtype=np.float64)
        ls = np.asarray(pxl, dtype=np.float64)
        tol = 0.5 * float(tick)
        read0 = float(t[0]) if t.size else float("inf")
        out = {}
        grew = False
        for k, (it, _x0, x1) in enumerate(drawn):
            if k == 0:
                continue                                  # the current VP has not ended
            key = round(float(it["t0"]), 3)
            if key in self.tested:
                continue
            a = area_of(it, tol)
            if a is None:
                continue
            side, lo, hi, mid = a
            x1 = float(x1)
            if x1 >= read0 - 0.5:
                i0 = int(np.searchsorted(t, x1 - 0.5))
            elif float(self.watch.get(key, -np.inf)) >= read0 - 0.5:
                i0 = 0                                    # watched untested into the read: carry on from its start
            else:
                continue                                  # ended before the candles held and never watched: unknown
            if side > 0:
                seen = ls[i0:]
                hit = bool(seen.size) and float(np.nanmin(seen)) <= mid + 1e-9
            else:
                seen = hs[i0:]
                hit = bool(seen.size) and float(np.nanmax(seen)) >= mid - 1e-9
            if hit:
                self.tested[key] = float(now)
                self.watch.pop(key, None)
                grew = True
                continue
            self.watch[key] = float(now)
            out[k] = (side, lo, hi, x1)
        return out, grew

    def bias(self, t, t_end, done, close, settle: float, tick: float) -> Tuple[int, int, Optional[dict]]:
        """THE MARKET POSITION BIAS (user 2026-09-26): "first we have to detect the last break of the conflict VP, what
        I mean by breach is a candle that closes above/below a most recent high/low of the conflict VP (the thickest
        lines of the conflict VP) / if above we have a bullish bias / if below we have a bearish bias".

        Every CLOSED candle of the read (the cycle candles: `close` = each one's last price, the forming one never
        counts) is judged against the conflict VP that was THE MOST RECENT ONE WHEN IT CLOSED -- the newest record
        with a VP of its own that was frozen by then. A VP exists from the moment its conflict 1 froze live: the bar
        after that conflict closed, plus `settle` seconds (freeze_new's own condition); records older than the read
        existed before it. So no candle is judged against a VP that did not exist yet, and the bias read later is the
        one read live (what is frozen never changes). Close above that VP's HIGH -> +1 (bullish), below its LOW -> -1
        (bearish), on the tick (a close ON the line is no break). The bias is the LAST break's side.

        Returns (bias, k, rec): k = the breaking candle's index in the read (-1 and bias 0 when none), rec = the VP
        record it broke."""
        t = np.asarray(t, dtype=np.float64)
        te = np.asarray(t_end, dtype=np.float64)
        dn = np.asarray(done, dtype=bool)
        cl = np.asarray(close, dtype=np.float64)
        n = int(t.size)
        if n == 0:
            return 0, -1, None
        tol = 0.5 * float(tick)
        recs, known = [], []
        for it in self.items:
            if it.get("c2") is None or it.get("vp") is None:
                continue                                  # no VP of its own: the current VP stays the current one
            tb = float(it["tb"])
            if tb < float(t[0]) - 0.5:
                k_at = -np.inf                            # frozen before the read begins
            else:
                b = int(np.searchsorted(t, tb - 0.5))
                k_at = float(te[min(b + 1, n - 1)]) + float(settle)
            recs.append(it)
            known.append(k_at)
        if not recs:
            return 0, -1, None
        known = np.maximum.accumulate(np.asarray(known, dtype=np.float64))
        j = np.searchsorted(known, te, side="right") - 1          # the most recent VP when each candle closed
        ok = dn & (j >= 0) & np.isfinite(cl)
        jj = np.clip(j, 0, len(recs) - 1)
        hi = np.array([float(r["vp"]["hi"]) for r in recs])[jj]
        lo = np.array([float(r["vp"]["lo"]) for r in recs])[jj]
        brk = np.where(ok & (cl > hi + tol), 1, np.where(ok & (cl < lo - tol), -1, 0))
        idx = np.flatnonzero(brk)
        if not idx.size:
            return 0, -1, None
        k = int(idx[-1])
        return int(brk[k]), k, recs[int(jj[k])]

    def apply(self, t, conf, cfh, cfl):
        """The conflict flags and box reach the tablet draws: the FROZEN record over the frozen history (a live flag
        or reach there is not drawn -- what was drawn stays), the live values after the newest frozen conflict."""
        conf = np.array(conf, copy=True)
        cfh = np.array(cfh, dtype=np.float64, copy=True)
        cfl = np.array(cfl, dtype=np.float64, copy=True)
        if not self.items:
            return conf, cfh, cfl
        t = np.asarray(t, dtype=np.float64)
        hist = t < float(self.items[-1]["te"]) - 0.5
        conf[hist] = 0
        cfh[hist] = np.nan
        cfl[hist] = np.nan
        for it in self.items:
            i0 = int(np.searchsorted(t, float(it["t0"]) - 0.5))
            i1 = int(np.searchsorted(t, float(it["tb"]) + 0.5))
            if i1 > i0:
                conf[i0:i1] = 1
                cfh[i0:i1] = float(it["hi"])
                cfl[i0:i1] = float(it["lo"])
        return conf, cfh, cfl
