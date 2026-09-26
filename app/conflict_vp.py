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
  THE CHAIN   the current VP is the newest frozen conflict's; the previous ones follow the conflict-2 links back
              (each older VP's conflict 1 IS the newer one's conflict 2: no gap). A VP is DRAWN from its conflict 2's
              END to its conflict 1's end, so the next VP begins exactly where the older one's lines stop and adding
              a VP never moves an older one's lines. The one change the rule itself makes: a new conflict that
              shares a high / low with the last one (or is under min_gap bars from it) links to an OLDER conflict,
              and its VP covers -- replaces -- the last VP (the user's own example: 100-101 skips 100-100.5).
"""
from __future__ import annotations

import json
import os
from typing import List, Optional, Tuple

import numpy as np

from . import hlh_profile as H

VER = 1


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
        except Exception:
            self.items = []

    def save(self) -> None:
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"ver": VER, "items": self.items}, fh, separators=(",", ":"))
        os.replace(tmp, self.path)

    def prune(self, now: float) -> bool:
        n0 = len(self.items)
        self.items = [x for x in self.items if float(x["te"]) >= float(now) - self.keep]
        return len(self.items) != n0

    def newest_t0(self) -> Optional[float]:
        return float(self.items[-1]["t0"]) if self.items else None

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
                  "hi": round(hi, 6), "lo": round(lo, 6), "n": int(b - a + 1), "c2": None, "skip": [], "vp": None}
            c2 = None
            for f in reversed(self.items):
                if float(f["tb"]) >= float(t[0]) - 0.5:
                    gap = a - int(np.searchsorted(t, float(f["tb"]) - 0.5))   # bars from its last bar to our first
                else:
                    gap = int(min_gap)                    # older than the read: more than a read away
                if gap < int(min_gap):
                    it["skip"].append([f["t0"], "near"])
                    continue
                if abs(float(f["hi"]) - hi) < tol:
                    it["skip"].append([f["t0"], "same high"])
                    continue
                if abs(float(f["lo"]) - lo) < tol:
                    it["skip"].append([f["t0"], "same low"])
                    continue
                c2 = f
                break
            if c2 is not None:
                it["c2"] = c2["t0"]
                vlo, vhi = min(lo, float(c2["lo"])), max(hi, float(c2["hi"]))
                vp = profile(base, bin_secs, buy, sell, bpxh, bpxl, float(c2["t0"]), float(t_end[b]), vlo, vhi, tick,
                             rows_target, va_pct, va2_pct)
                if vp is not None:
                    it["vp"] = {"lo": round(vlo, 6), "hi": round(vhi, 6), "poc": round(vp["poc"], 6),
                                "vah": round(vp["vah"], 6), "val": round(vp["val"], 6), "vah2": round(vp["vah2"], 6),
                                "val2": round(vp["val2"], 6), "usd": round(vp["usd"], 2), "rows": vp["rows"],
                                "k": vp["k"], "d0": float(c2["te"]), "d1": it["te"]}
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
