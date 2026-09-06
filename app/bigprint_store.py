"""Read-only loader for the big-print archive (study/bigprint_archive, built by study/bigprint_archive.py):
the Big Player Levels overlay's REPLAY source for bars older than the live tape store. One gz-jsonl per
month; rows {"t": epoch_ms, "p": price, "q": qty, "u": usd, "s": side}. Months are cached by file mtime
(the current month is rebuilt as new daily dumps land). Missing months simply yield nothing.
SWEEPS (user 2026-09-06): one taker order that ate through several levels = prints with the same millisecond +
side. A month built by the updated study/bigprint_archive.py carries explicit rows
{"k": "sw", "t": ms, "p0": first px, "p1": last px, "u": total usd, "s": side, "n": levels} (exact: every fill
was seen); an older month yields sweeps reconstructed from its archived prints (a LOWER bound: fills under the
store floor are not archived)."""
from __future__ import annotations

import gzip
import json
import os
from datetime import datetime, timezone

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "study", "bigprint_archive")
SYMBOL = "SOLUSDT"
CACHE_FLOOR_USD = 0.0                             # studies may raise this before loading (RAM: months cache only >= it)
_cache: "dict[str, tuple[float, list]]" = {}      # month -> (mtime, [(ts_s, price, usd, side), ...])
_scache: "dict[str, tuple[float, list, bool]]" = {}   # month -> (mtime, [sweep rows], explicit?)


def group_sweeps(rows, min_levels: int = 2, min_usd: float = 0.0) -> list:
    """Group time-ordered prints (ts_s, price, usd, side) by IDENTICAL millisecond + side -> one taker order.
    Returns [(ts_s, p_first, p_last, usd_total, side, n_levels)] for groups that crossed >= min_levels distinct
    prices and total >= min_usd, time-ordered. p_first/p_last follow the fill order (p_last = where the book
    finally absorbed the order)."""
    out = []
    cur = None                                          # [ms, side, p_first, p_last, usd, {prices}, ts_s]
    for (ts_s, price, usd, side) in rows:
        ms = int(round(float(ts_s) * 1000.0)); side = int(side)
        if cur is not None and cur[0] == ms and cur[1] == side:
            cur[3] = float(price); cur[4] += float(usd); cur[5].add(round(float(price), 6))
            continue
        if cur is not None and len(cur[5]) >= min_levels and cur[4] >= min_usd:
            out.append((cur[6], cur[2], cur[3], cur[4], cur[1], len(cur[5])))
        cur = [ms, side, float(price), float(price), float(usd), {round(float(price), 6)}, float(ts_s)]
    if cur is not None and len(cur[5]) >= min_levels and cur[4] >= min_usd:
        out.append((cur[6], cur[2], cur[3], cur[4], cur[1], len(cur[5])))
    return out


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
    rows = []; sweeps = []; explicit = False
    try:
        with gzip.open(path, "rt", encoding="utf-8") as g:
            for ln in g:
                r = json.loads(ln)
                if r.get("k") == "sw":                    # explicit sweep record (updated archive builder)
                    explicit = True
                    sweeps.append((float(r["t"]) / 1000.0, float(r["p0"]), float(r["p1"]), float(r["u"]),
                                   int(r["s"]), int(r.get("n", 2))))
                    continue
                if float(r["u"]) < CACHE_FLOOR_USD:
                    continue
                rows.append((float(r["t"]) / 1000.0, float(r["p"]), float(r["u"]), int(r["s"])))
    except Exception:
        rows = []; sweeps = []; explicit = False
    rows.sort(key=lambda r: r[0])                     # by time only, STABLE: same-ms fills keep their fill order
    if not explicit:
        sweeps = group_sweeps(rows)                     # reconstructed from the archived prints (lower bound)
    sweeps.sort(key=lambda r: r[0])
    _cache[month] = (mt, rows)
    _scache[month] = (mt, sweeps, explicit)
    return rows


def load_sweeps(t0_s: float, t1_s: float, min_usd: float = 0.0, min_levels: int = 2) -> list:
    """Sweeps with t0_s <= ts <= t1_s, total usd >= min_usd and >= min_levels levels, as
    (ts_s, p_first, p_last, usd, side, n_levels), time-ordered."""
    if t1_s < t0_s:
        return []
    out = []
    month = _month_of(t0_s)
    last = _month_of(t1_s)
    while True:
        _load_month(month)
        for row in (_scache.get(month) or (0.0, [], False))[1]:
            if t0_s <= row[0] <= t1_s and row[3] >= min_usd and row[5] >= min_levels:
                out.append(row)
        if month == last:
            break
        month = _next_month(month)
    return out


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
