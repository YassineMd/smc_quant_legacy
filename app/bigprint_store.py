"""Read-only loader for the big-print archive (study/bigprint_archive, built by study/bigprint_archive.py):
the Big Player Levels overlay's REPLAY source for bars older than the live tape store. One gz-jsonl per
month; rows {"t": epoch_ms, "p": price, "q": qty, "u": usd, "s": side}. Months are cached by file mtime
(the current month is rebuilt as new daily dumps land). Missing months simply yield nothing."""
from __future__ import annotations

import gzip
import json
import os
from datetime import datetime, timezone

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "study", "bigprint_archive")
SYMBOL = "SOLUSDT"
CACHE_FLOOR_USD = 0.0                             # studies may raise this before loading (RAM: months cache only >= it)
_cache: "dict[str, tuple[float, list]]" = {}      # month -> (mtime, [(ts_s, price, usd, side), ...])


def _month_of(ts_s: float) -> str:
    return datetime.fromtimestamp(ts_s, tz=timezone.utc).strftime("%Y-%m")


def _next_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    m += 1
    if m > 12:
        m = 1; y += 1
    return "%04d-%02d" % (y, m)


def _load_month(month: str) -> list:
    path = os.path.join(ROOT, "%s-bigprints-%s.jsonl.gz" % (SYMBOL, month))
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return []
    hit = _cache.get(month)
    if hit is not None and hit[0] == mt:
        return hit[1]
    rows = []
    try:
        with gzip.open(path, "rt", encoding="utf-8") as g:
            for ln in g:
                r = json.loads(ln)
                if float(r["u"]) < CACHE_FLOOR_USD:
                    continue
                rows.append((float(r["t"]) / 1000.0, float(r["p"]), float(r["u"]), int(r["s"])))
    except Exception:
        rows = []
    rows.sort()
    _cache[month] = (mt, rows)
    return rows


def load_prints(t0_s: float, t1_s: float, min_usd: float = 0.0) -> list:
    """Big prints with t0_s <= ts <= t1_s and usd >= min_usd, as (ts_s, price, usd, side), time-ordered."""
    if t1_s < t0_s:
        return []
    out = []
    month = _month_of(t0_s)
    last = _month_of(t1_s)
    while True:
        for row in _load_month(month):
            if t0_s <= row[0] <= t1_s and row[2] >= min_usd:
                out.append(row)
        if month == last:
            break
        month = _next_month(month)
    return out
