"""LOCAL cold-archive reader for the terminal — extends the scanner frame backward past the daemon's
~10k/tf cap using the GCS-mirror chunks pulled to study/archive_data/ (see ops/archive_buckets.py).

Tiered by design: the terminal tries the VM live window first, then this LOCAL mirror; the GCS tier
(fetch-if-missing) layers on top later. Buckets are normalized DB-format `data` -> the WIRE snapshot via
persistence._bucket_from_dict().full_snapshot() — the SAME assembler the daemon streams — so an archived
bucket renders and computes bit-identically to a live one (open/close, liq_short/long, levels, …).

Lazily loaded + cached per tf, invalidated when a fresh pull changes the newest chunk's mtime. The archive
`bid` == the terminal's absolute Idx (both from total_closed), so a contiguous older run prepends right
before the daemon's oldest bucket with no re-indexing.
"""
from __future__ import annotations

import glob
import gzip
import json
import os
import time

from . import config
from .persistence import _bucket_from_dict

_ROOT = os.path.join(config.PROJECT_DIR, "study", "archive_data")
_cache: dict[str, dict[int, dict]] = {}     # tf -> {bid: wire_bucket}
_stamp: dict[str, tuple] = {}               # tf -> (newest-chunk mtime, overlay mtime) the cache was built from

# Half-bucket field OVERLAY — delta_h1 / price_h1 reconstructed from 1m for buckets that PREDATE the daemon
# stamping them (delta_h1 since 2026-07-20, price_h1 since 2026-07-22). Without it the terminal's Δ-accel / ΔP
# / R-h1-h2 read "--" on every older replay bucket. Filled at load time from a precomputed JSON
# ("tf|start_time" -> [delta_h1, price_h1], built by `python study/backfill_price_h1.py compute <path>`), so the
# append-only archive chunks are never rewritten (a gsutil rsync would clobber those). Coverage is bounded by 1m
# availability; buckets with no reconstructable pair stay blank. Missing file -> silent no-op.
_OVERLAY_PATH = os.path.join(config.PROJECT_DIR, "study", "out", "price_h1_backfill.json")
_overlay: "dict | None" = None
_overlay_mtime: float = -1.0
_overlay_checked: float = 0.0                        # last os.stat of the overlay (throttled to every 2 s)
_done: dict = {}                                     # id(bucket) -> start_time resolved under the current overlay
_done_mtime: float = -2.0


def local_dir() -> str:
    """The local mirror root (study/archive_data) that the GCS bucket rsyncs into."""
    return _ROOT


def invalidate(tf: "str | None" = None) -> None:
    """Drop the in-memory cache so the next _load re-reads freshly-pulled chunks from disk. (The mtime guard in
    _load already auto-reloads when a chunk changes; this is an explicit belt-and-suspenders after a GCS fetch.)"""
    if tf is None:
        _cache.clear(); _stamp.clear()
    else:
        _cache.pop(tf, None); _stamp.pop(tf, None)


def _chunk_paths(tf: str) -> list[str]:
    return sorted(glob.glob(os.path.join(_ROOT, tf, "%s_*.jsonl.gz" % tf)))


def _newest_mtime(tf: str) -> float:
    return max((os.path.getmtime(p) for p in _chunk_paths(tf)), default=0.0)


def _load_overlay() -> dict:
    """The delta_h1/price_h1 fill map ("tf|start" -> [delta_h1, price_h1]), reloaded when its file changes.
    {} when the file is absent/unreadable, so the archive path is a safe no-op without it."""
    global _overlay, _overlay_mtime, _overlay_checked
    now = time.time()
    if _overlay is not None and now - _overlay_checked < 2.0:
        return _overlay                              # the stat itself cost ~0.25 ms per 20 Hz tick
    _overlay_checked = now
    try:
        m = os.path.getmtime(_OVERLAY_PATH)
    except OSError:
        _overlay = {}; _overlay_mtime = -1.0; return _overlay
    if _overlay is not None and _overlay_mtime == m:
        return _overlay
    try:
        with open(_OVERLAY_PATH, "r", encoding="utf-8") as fh:
            _overlay = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        _overlay = {}
    _overlay_mtime = m
    return _overlay


_call_fp: dict = {}          # tf -> the last bucket list this ran over, so an unchanged list is free


def _call_fingerprint(buckets, tf: str):
    """Identify a bucket list cheaply: its length, its two endpoints, and whether those endpoints are FILLED.

    ⚠ Deliberately not id()-based -- ids are reused after a collection, and an id()-keyed memo has bitten this
    project before. These are stable DATA values.

    ⚠ The two `delta_h1 is None` flags are what make it safe against freshly deserialised dicts: the times
    would match but the fills would be gone, so without them the guard would skip re-filling. They only work
    because the fingerprint is STORED AFTER the walk, when the endpoints reflect the filled state."""
    try:
        n = len(buckets)
    except TypeError:
        return None                                   # a generator (archive._load): no guard, walk it
    if n <= 0:
        return None
    try:
        b0 = buckets[0]; bz = buckets[-1]
        return (tf, n, b0.get("start_time"), bz.get("end_time"), _overlay_mtime,
                b0.get("delta_h1") is None, bz.get("delta_h1") is None)
    except (TypeError, AttributeError, IndexError, KeyError):
        return None                                   # not indexable -> walk it


def enrich_halves(buckets, tf: str) -> None:
    """Fill missing delta_h1/price_h1 on each bucket IN PLACE from the reconstruction overlay, so the terminal's
    Δ-accel / ΔP / R-h1-h2 render on old buckets. Used for BOTH archive-extend buckets (here) and the daemon's
    live-window closed_buckets (the terminal calls this on the assembled frame — those tfs stay under the 10k cap,
    so they never reach archive-extend yet still predate the daemon stamping the fields). No-op without the file
    or on a bucket with no reconstructable pair; only ever ADDS a missing field, never overwrites a live one."""
    ov = _load_overlay()
    if not ov:
        return
    global _done_mtime
    if _done_mtime != _overlay_mtime:                 # a new overlay file -> every bucket is worth a fresh look
        _done.clear(); _done_mtime = _overlay_mtime
    elif len(_done) > 400_000:
        _done.clear()
    # WHOLE-CALL GUARD (perf 2026-09-11). The per-bucket memo below already works -- measured 914 hits and 0
    # lookups per call -- but the LOOP itself ran over 3,234 buckets on every 20 Hz tick just to conclude that
    # nothing had changed: 5.4 ms per tick, the largest single per-tick cost in the terminal and most of
    # _on_timer's 6 ms median. The list is append-only and time-ordered, so its length plus its two endpoints
    # plus the overlay's mtime identify it: four dict reads instead of ~3,200 iterations.
    # ⚠ Deliberately NOT id()-based -- ids are reused after a collection, and this project has been bitten by
    # an id()-keyed memo before. These are stable DATA values.
    fp = _call_fingerprint(buckets, tf)
    if fp is not None and _call_fp.get(tf) == fp:
        return
    done = _done
    for b in buckets:
        if b.get("delta_h1") is not None and b.get("price_h1") is not None:
            continue                                 # complete: nothing to look up (the common case, 2 gets)
        k = id(b)
        st = b.get("start_time"); fp = (st, b.get("end_time"), len(b))
        if done.get(k) == fp:                        # known to have NO reconstructable pair under this overlay
            continue                                 # (perf 2026-09-08: this ran over the whole window per 20 Hz tick)
        pair = ov.get("%s|%.3f" % (tf, float(st or 0.0)))
        if not pair:
            done[k] = fp
            continue
        if b.get("delta_h1") is None and pair[0] is not None:
            b["delta_h1"] = pair[0]
        if b.get("price_h1") is None and pair[1] is not None:
            b["price_h1"] = pair[1]
    # recomputed AFTER the walk so it carries the FILLED state of the endpoints -- see _call_fingerprint
    fp2 = _call_fingerprint(buckets, tf)
    if fp2 is not None:
        _call_fp[tf] = fp2


def _load(tf: str) -> dict[int, dict]:
    """{bid: wire_bucket} for a tf from the local mirror; cached until a new pull touches the chunks (or the
    half-bucket overlay file changes). Reconstructed delta_h1/price_h1 are merged in where the bucket lacks
    them, so Δ-accel / ΔP / R-h1-h2 render on old replay buckets instead of "--"."""
    _load_overlay()                              # refresh _overlay_mtime for the cache key
    m = _newest_mtime(tf)
    key = (m, _overlay_mtime)
    if tf in _cache and _stamp.get(tf) == key:
        return _cache[tf]
    out: dict[int, dict] = {}
    for path in _chunk_paths(tf):
        try:
            with gzip.open(path, "rt", encoding="utf-8") as gz:
                for line in gz:
                    line = line.strip()
                    if not line:
                        continue
                    r = json.loads(line)
                    data = r["data"]
                    if isinstance(data, str):
                        data = json.loads(data)
                    out[int(r["bid"])] = _bucket_from_dict(data).full_snapshot()   # DB -> wire, identical to live
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    enrich_halves(out.values(), tf)              # fill pre-daemon delta_h1/price_h1 in place
    _cache[tf] = out
    _stamp[tf] = key
    return out


def available(tf: str) -> bool:
    return bool(_chunk_paths(tf))


def oldest_bid(tf: str):
    d = _load(tf)
    return min(d) if d else None


_earliest_cache: dict = {}


def earliest_start(tf: str):
    """Unix ``start_time`` of the oldest archived bucket for ``tf`` (the earliest data the terminal can reach),
    or ``None`` when the archive is empty. Used to bound the date picker so no-data days are disabled.

    Reads ONLY the first line of the FIRST chunk. Chunk files are id-sorted, so that line IS the oldest bucket --
    `_load(tf)` returns the same number but decompresses the entire archive to do it: measured 5,375 ms against
    0.36 ms here, which is exactly the freeze the user hit on the first calendar click (235 chunks, 3.6 MB in the
    first one alone). Cached on (path, mtime)."""
    ps = _chunk_paths(tf)
    if not ps:
        return None
    try:
        key = (ps[0], os.path.getmtime(ps[0]))
    except OSError:
        key = (ps[0], 0.0)
    hit = _earliest_cache.get(tf)
    if hit is not None and hit[0] == key:
        return hit[1]
    st = None
    try:
        with gzip.open(ps[0], "rt", encoding="utf-8") as fh:
            line = fh.readline()
        if line:
            st = float(json.loads(line).get("start_time", 0.0)) or None
    except (OSError, ValueError, json.JSONDecodeError):
        st = None
    if st is None:                                   # malformed first line -> fall back to the full load
        d = _load(tf)
        st = float(d[min(d)].get("start_time", 0.0)) or None if d else None
    _earliest_cache[tf] = (key, st)
    return st


def subbuckets(tf: str, start_unix: float, end_unix: float) -> "list[dict]":
    """Wire-buckets of ``tf`` whose start_time falls in [start_unix, end_unix), ascending — the fine-grained
    constituents of a coarser candle (e.g. the 1m buckets inside a clicked 1h/4h bucket). From the LOCAL mirror;
    returns [] when the range isn't covered (outside the ~pulled 1m window)."""
    d = _load(tf)
    if not d:
        return []
    out = [snap for snap in d.values()
           if start_unix <= float(snap.get("start_time", 0.0) or 0.0) < end_unix]
    out.sort(key=lambda s: float(s.get("start_time", 0.0) or 0.0))
    return out


def window(tf: str, min_start_unix: float, before_bid: int) -> list[dict]:
    """Contiguous wire-buckets with bid < ``before_bid``, walking back until one starts before
    ``min_start_unix`` (so the frame reaches the Zero Point) or the archive runs out / breaks contiguity.
    Returns ascending-bid; [] when the archive is empty or doesn't abut the live window."""
    d = _load(tf)
    if not d:
        return []
    out: list[dict] = []
    b = int(before_bid) - 1
    while b in d:
        snap = d[b]
        out.append(snap)
        if float(snap.get("start_time", 0.0)) < float(min_start_unix):
            break                          # reached the Zero Point — stop
        b -= 1
    out.reverse()                          # ascending bid: [oldest ... before_bid-1]
    return out
