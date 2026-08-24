"""DAY COMPASS (m10_daycompass) — bottom-left BIAS TABLE: where is price versus YESTERDAY's value, and which way
does TODAY's wall ledger lean. Two causal axes, one plain-language needle:

  VALUE axis  — yesterday's (previous UTC day's) volume profile from the chart buckets' per-price footprints;
                classic 70% VALUE AREA grown from the POC. Price state: ABOVE / INSIDE / BELOW value.
  LEDGER axis — TODAY's order-flow wall ledger from the SAME wall marks the chart draws (m10_absorblvl set):
                walls born today (S vs R) and walls mitigated today (S vs R).
                bias = (S created - S mitigated) - (R created - R mitigated); UP / NEUT / DOWN by sign.
  NEEDLE      — both agree up -> UP BIAS; both agree down -> DOWN BIAS; inside value + flat ledger -> ROTATION;
                anything conflicting -> MIXED.

DESCRIPTIVE + COINCIDENT until proven otherwise — the pre-registered RadarRun conditioning study
(study/radarrun_daybias_30m.py) is the honest referee; no predictive claim is made by this table.
Definitions match that study exactly (same VA construction, same ledger arithmetic)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np


def _f(b, k, alt=None):
    v = b.get(k)
    if v is None and alt is not None:
        v = b.get(alt)
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _day(ts):
    return datetime.fromtimestamp(float(ts), timezone.utc).date()


def day_va(buckets, day):
    """(VAL, POC, VAH) for one UTC day from per-price footprints, classic 70% value area. None if < 5 levels."""
    acc = {}
    for b in buckets:
        if _day(_f(b, "start_time")) != day:
            continue
        for p, v in (b.get("levels") or {}).items():
            try:
                pf = round(float(p), 2)
            except (TypeError, ValueError):
                continue
            acc[pf] = acc.get(pf, 0.0) + float(v.get("b", 0.0)) + float(v.get("s", 0.0))
    if len(acc) < 5:
        return None
    prices = sorted(acc)
    vols = np.array([acc[p] for p in prices]); tot = float(vols.sum())
    if tot <= 0:
        return None
    poc = int(np.argmax(vols)); lo = hi = poc; cum = float(vols[poc])
    while cum < 0.70 * tot and (lo > 0 or hi < len(prices) - 1):
        vlo = vols[lo - 1] if lo > 0 else -1.0
        vhi = vols[hi + 1] if hi < len(prices) - 1 else -1.0
        if vhi >= vlo:
            hi += 1; cum += float(vols[hi])
        else:
            lo -= 1; cum += float(vols[lo])
    return (prices[lo], prices[poc], prices[hi])


def compass_read(buckets, wall_marks, va_cache=None):
    """The full table read for the LAST closed bucket. Fail-safe: {'ready': False} when there is no prev-day VP.
    `va_cache` ({date: va}) memoizes the prev-day VP — a finished day's profile is immutable, so the O(window)
    day_va scan runs once per day instead of once per call (terminal perf rule: never O(window) per frame)."""
    try:
        if not buckets:
            return {"ready": False}
        n = len(buckets)

        def _st(i):                                  # lazy start_time — the old full list comp was 10k _f calls
            return _f(buckets[i], "start_time")      # (~30ms/recompute) to read ~5 values (profiled 2026-08-25)
        today = _day(_st(n - 1))
        px = _f(buckets[-1], "close", "close_price")
        pday = today - timedelta(days=1)
        if va_cache is not None and pday in va_cache:
            va = va_cache[pday]
        else:
            va = day_va(buckets, pday)
            if va_cache is not None and va is not None:
                va_cache[pday] = va
        astate = None
        if va is not None and px > 0:
            val, poc, vah = va
            astate = "ABOVE" if px > vah else ("BELOW" if px < val else "INSIDE")
        cs = cr = ms = mr = 0
        for m in (wall_marks or []):
            side = m.get("side")
            if side not in ("S", "R"):
                continue
            i0 = int(m.get("i0", 0))
            if 0 <= i0 < n and _day(_st(i0)) == today:
                cs += side == "S"; cr += side == "R"
            i1 = m.get("i1")
            if bool(m.get("broken")) and i1 is not None and 0 <= int(i1) < n and _day(_st(int(i1))) == today:
                ms += side == "S"; mr += side == "R"
        bias = (cs - ms) - (cr - mr)
        bstate = "UP" if bias > 0 else ("DOWN" if bias < 0 else "NEUT")
        if astate is None:
            needle = "NO PREV DAY"
        elif astate == "ABOVE" and bias > 0:
            needle = "UP BIAS"
        elif astate == "BELOW" and bias < 0:
            needle = "DOWN BIAS"
        elif astate == "INSIDE" and bias == 0:
            needle = "ROTATION"
        else:
            needle = "MIXED"
        return {"ready": va is not None, "px": px, "va": va, "astate": astate,
                "cs": cs, "cr": cr, "ms": ms, "mr": mr, "bias": bias, "bstate": bstate, "needle": needle}
    except Exception:
        return {"ready": False}
