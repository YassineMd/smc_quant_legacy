# -*- coding: utf-8 -*-
"""CONFLICT VP: a volume profile between two CONFLICT boxes, drawn with the HLH Volume Profile's lines.

The user, 2026-09-26: "This indicator creates VP from the last 2 conflicts/merged conflicts ... the ones preceeding
should be at least 10 bars away from it ... if they have the same high or low we skip ... we will draw our conflict VP
from conflict 2 to conflict 1, the High will be 101 and the low will be 99 ... we gonna draw the same lines as HLH VP
indicator". Then: "add a toggle so that I am able to see the previous ones ... each VP lines should be of different
color ... the VPs should NOT be overlapping".

  CONFLICT 1  the newest conflict RUN (consecutive conflict bars are one box, as the tablet draws them).
  CONFLICT 2  walking back from conflict 1: the first run that ends at least `min_gap` bars before conflict 1 starts
              and whose high AND low both differ from conflict 1's (a shared high or a shared low -> skipped).
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
  THE PREVIOUS ONES  a CONNECTED chain back in time (user, 2026-09-26: "the select conflict should normally be
              connected to the one that happened at 7am, we shouldnt have a gap"): the next older VP's conflict 1 IS
              the newer VP's conflict 2, and its own conflict 2 is found by the same rule. Each VP of the chain is the
              one that was CURRENT when its conflict 1 was the newest conflict, and its profile keeps both of its
              conflicts. Its LINES stop where the newer VP's begin -- the shared conflict's first bar -- so the chain
              has no gap and no overlap ("the VPs should NOT be overlapping"). A run with no conflict 2 breaks the
              chain: the run before it is tried as a conflict 1 (that VP's lines then run to its own conflict 1's end).
              ⚠ The first cut restarted the search at the run BEFORE the shared conflict, which skipped a link: the
              08:31 conflict never became a conflict 1, and its VP to 06:59 (07:49 shares its low) was missing.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

from . import hlh_profile as H


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


def _find_c2(rr, boxes, j: int, min_gap: int, tol: float):
    """Run j as conflict 1: (m, skipped), m = the index in rr of its conflict 2 or None. skipped = [(a, b, high,
    low, why)] for the runs passed over on the way back, why "near" / "same high" / "same low"."""
    a1 = rr[j][0]
    h1, l1 = boxes[j]
    skipped = []
    for m in range(j - 1, -1, -1):
        a2, b2 = rr[m]
        h2, l2 = boxes[m]
        if a1 - b2 < int(min_gap):
            skipped.append((a2, b2, h2, l2, "near"))
            continue
        if not (np.isfinite(h2) and np.isfinite(l2)):
            continue
        if abs(h2 - h1) < tol:
            skipped.append((a2, b2, h2, l2, "same high"))
            continue
        if abs(l2 - l1) < tol:
            skipped.append((a2, b2, h2, l2, "same low"))
            continue
        return m, skipped
    return None, skipped


def chain(rr, boxes, min_gap: int, tick: float, max_n: Optional[int] = None) -> List[Tuple[int, int, list]]:
    """[(j, m, skipped)] newest first: run j is a VP's conflict 1, run m its conflict 2. CONNECTED: the next VP's
    conflict 1 is this VP's conflict 2 (j = m); a run with no conflict 2 is passed over (j - 1)."""
    tol = 0.5 * float(tick)
    out = []
    j = len(rr) - 1
    while j >= 1 and (max_n is None or len(out) < int(max_n)):
        if not (np.isfinite(boxes[j][0]) and np.isfinite(boxes[j][1])):
            j -= 1
            continue
        m, sk = _find_c2(rr, boxes, j, min_gap, tol)
        if m is None:
            j -= 1
            continue
        out.append((j, m, sk))
        j = m                                              # the shared conflict: no gap between the two VPs
    return out


def pick_pair(conf, cfh, cfl, pxh, pxl, min_gap: int, tick: float):
    """(c1, c2, skipped) of the CURRENT VP: c1 / c2 = (a, b, high, low), c2 None when no run qualifies, c1 None when
    there is no conflict at all (or its box is unknown)."""
    rr = runs(conf)
    if not rr:
        return None, None, []
    boxes = [run_box(a, b, cfh, cfl, pxh, pxl) for a, b in rr]
    j = len(rr) - 1
    if not (np.isfinite(boxes[j][0]) and np.isfinite(boxes[j][1])):
        return None, None, []
    c1 = rr[j] + boxes[j]
    m, sk = _find_c2(rr, boxes, j, min_gap, 0.5 * float(tick))
    return c1, (rr[m] + boxes[m] if m is not None else None), sk


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


def build(t, t_end, conf, cfh, cfl, pxh, pxl, base: int, bin_secs: float, buy, sell, bpxh, bpxl, tick: float,
          min_gap: int, rows_target: int, va_pct: float, va2_pct: float, done=None, t_draw=None,
          memo: Optional[Dict] = None, rev: int = 0, max_n: Optional[int] = None) -> dict:
    """Every VP of the chain, newest first: {"vps": [{c1, c2, skipped, lo, hi, t0, t1, poc, vah, val, vah2, val2, usd,
    rows, k, live, cur}, ...], "runs": number of conflict runs}.

    `t_end` is crosses()' own (the next crossing; the end of the tape for the forming cycle): it cuts the seconds.
    `t_draw` (optional) is where a VP's lines END on the chart -- the engine's t_end with the forming cycle clamped to
    now, as the candles are drawn. cur = its conflict 1 is the NEWEST conflict run (the one Conflict VP); live = that
    run's last bar is the forming cycle (`done` False there). `memo` (a dict the caller keeps) holds the profiles
    already built, keyed on the seconds, the range and `rev` (the store's rev_hist: older bins rewritten) -- a VP
    whose seconds end within a minute of the tape's end is always rebuilt (late prints)."""
    rr = runs(conf)
    out = {"vps": [], "runs": len(rr)}
    if len(rr) < 2:
        return out
    boxes = [run_box(a, b, cfh, cfl, pxh, pxl) for a, b in rr]
    td = t_draw if t_draw is not None else t_end
    edge = (int(base) + int(np.size(buy))) * float(bin_secs)       # the end of the tape
    new_memo = {}
    newer_c2 = None               # the run index of the newer DRAWN VP's conflict 2 (the conflict the next one shares)
    for j, m, sk in chain(rr, boxes, min_gap, tick, max_n):
        a1, b1 = rr[j]; a2, b2 = rr[m]
        h1, l1 = boxes[j]; h2, l2 = boxes[m]
        lo = min(l1, l2); hi = max(h1, h2)
        t0 = float(t[a2]); tc = float(t_end[b1])
        key = (round(t0, 3), round(tc, 3), round(lo, 6), round(hi, 6), int(rev))
        vp = memo.get(key) if (memo is not None and tc < edge - 60.0) else None
        if vp is None:
            vp = profile(base, bin_secs, buy, sell, bpxh, bpxl, t0, tc, lo, hi, tick, rows_target, va_pct, va2_pct)
        if vp is None:
            newer_c2 = None       # not drawn: the next one runs its lines to its own conflict 1's end
            continue
        if tc < edge - 60.0:
            new_memo[key] = vp
        # the LINES of a VP whose conflict 1 is the newer VP's conflict 2 stop at that shared conflict's first bar,
        # where the newer VP's begin: connected, not overlapping. Its PROFILE keeps both of its conflicts.
        shared = newer_c2 is not None and j == newer_c2
        d = dict(vp)
        d.update({"c1": (a1, b1, h1, l1), "c2": (a2, b2, h2, l2), "skipped": sk, "lo": float(lo), "hi": float(hi),
                  "t0": t0, "t1": float(t[a1]) if shared else float(td[b1]), "shared": bool(shared),
                  "cur": j == len(rr) - 1,
                  "live": bool(j == len(rr) - 1 and done is not None and not bool(np.asarray(done)[b1]))})
        newer_c2 = m
        out["vps"].append(d)
    if memo is not None:
        memo.clear()
        memo.update(new_memo)
    return out
