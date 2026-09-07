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
import threading
import time
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
    finally absorbed the order). MONOTONIC (user 2026-09-07): one order only walks the book in its own direction,
    so a same-ms same-side fill that comes back (buy below the previous fill / sell above) starts a NEW group."""
    out = []
    cur = None                                          # [ms, side, p_first, p_last, usd, {prices}, ts_s]
    for (ts_s, price, usd, side) in rows:
        ms = int(round(float(ts_s) * 1000.0)); side = int(side)
        if cur is not None and cur[0] == ms and cur[1] == side \
                and (float(price) >= cur[3] - 1e-9 if side > 0 else float(price) <= cur[3] + 1e-9):
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


def _prev_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    m -= 1
    if m < 1:
        m = 12; y -= 1
    return "%04d-%02d" % (y, m)


def latest_archived_ts() -> float:
    """The archive's coverage end (s): the newest print of the current month file, else of the previous month; 0 if none."""
    month = _month_of(time.time())
    for _ in range(2):
        rows = _load_month(month)
        if rows:
            return float(rows[-1][0])
        month = _prev_month(month)
    return 0.0


_refresh_lock = threading.Lock()
_refresh_busy = False


def _default_builder(month: str, scratch: str) -> int:
    import importlib.util
    p = os.path.join(os.path.dirname(ROOT), "bigprint_archive.py")            # study/bigprint_archive.py
    spec = importlib.util.spec_from_file_location("bigprint_archive", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return int(mod.build_current_month(month, scratch))


def refresh_current_month_async(max_age_s: float, on_done=None, builder=None) -> bool:
    """Rebuild the CURRENT month from Binance's daily dumps (day 1 .. yesterday UTC) in a background thread when the
    month file is missing or older than max_age_s (a daily dump appears a few hours after UTC midnight, so a stale
    file is retried at the next check). One run at a time; the reader's mtime cache picks the new file up by
    itself. Returns True when a refresh was started."""
    global _refresh_busy
    month = _month_of(time.time())
    path = os.path.join(ROOT, "%s-bigprints-%s.jsonl.gz" % (SYMBOL, month))
    try:
        age = time.time() - os.path.getmtime(path)
    except OSError:
        age = float("inf")
    if age < max_age_s:
        return False
    with _refresh_lock:
        if _refresh_busy:
            return False
        _refresh_busy = True
    run = builder or _default_builder

    def _work():
        global _refresh_busy
        try:
            scratch = os.path.join(ROOT, "_raw")
            os.makedirs(scratch, exist_ok=True)
            n = run(month, scratch)
            if on_done is not None:
                on_done(n)
        except Exception as ex:
            print("BIGPRINT ARCHIVE REFRESH ERROR: %s" % ex)
        finally:
            with _refresh_lock:
                _refresh_busy = False
    threading.Thread(target=_work, name="bigprint-archive-refresh", daemon=True).start()
    return True


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
