"""NY ANCHOR (m10_nyanchor) — the session far-side hold level, straight from the two session studies
(study/session_side_fix_15m.py + study/session_range_fix_15m.py, 2026-08-25):

  From 15:00 UTC (~25-30% of the NY session), the session extreme FARTHER from price holds to the session
  close ~67-73% of days — +12-17pp over the shuffled-order null in the two most recent eras. From ~18:30 UTC
  the FULL session range is typically set (median full-fix ~18:00-19:00Z; on range days the 18:36Z range holds
  to the close ~68-93%).

  ANCHOR state (15:00-18:30Z): ONE line at the current far-side extreme (far = the side away from the last
  close vs the midpoint of the range-so-far; the side can flip if price crosses the midpoint).
  BOX state (18:30-21:00Z): BOTH session extremes drawn — the range is typically complete.
  OFF outside 15:00-21:00Z (incl. before 15:00: the study found NO reliable structure earlier).

DESCRIPTIVE display of measured level persistence — NOT an entry signal; hold odds are session-level
statistics, not per-touch odds. Causal: reads only closed buckets of the current UTC day's session."""
from __future__ import annotations

from datetime import datetime, timezone

NY_OPEN_H, ANCHOR_H, ANCHOR_M, BOX_H, BOX_M, NY_END_H = 13, 15, 0, 18, 30, 21


def _f(b, k, alt=None):
    v = b.get(k)
    if v is None and alt is not None:
        v = b.get(alt)
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def anchor_read(buckets):
    """Read for the LAST CLOSED bucket's UTC day. Returns {'state': 'OFF'} or
    {'state': 'ANCHOR'|'BOX', 'hi', 'lo', 'far': 'H'|'L', 'i0': first NY bucket index}."""
    try:
        if not buckets:
            return {"state": "OFF"}
        t = _f(buckets[-1], "start_time")
        d = datetime.fromtimestamp(t, timezone.utc)
        s0 = d.replace(hour=NY_OPEN_H, minute=0, second=0, microsecond=0).timestamp()
        t_anchor = d.replace(hour=ANCHOR_H, minute=ANCHOR_M, second=0, microsecond=0).timestamp()
        t_box = d.replace(hour=BOX_H, minute=BOX_M, second=0, microsecond=0).timestamp()
        t_end = d.replace(hour=NY_END_H, minute=0, second=0, microsecond=0).timestamp()
        if not (t_anchor <= t < t_end):
            return {"state": "OFF"}
        hi = -1e18; lo = 1e18; close = None; i0 = None
        for i, b in enumerate(buckets):
            st = _f(b, "start_time")
            if s0 <= st <= t:
                if i0 is None:
                    i0 = i
                hi = max(hi, _f(b, "high")); lo = min(lo, _f(b, "low"))
                close = _f(b, "close", "close_price")
        if i0 is None or close is None or hi <= lo:
            return {"state": "OFF"}
        far = "L" if close >= 0.5 * (hi + lo) else "H"
        return {"state": "BOX" if t >= t_box else "ANCHOR", "hi": hi, "lo": lo, "far": far, "i0": i0}
    except Exception:
        return {"state": "OFF"}
