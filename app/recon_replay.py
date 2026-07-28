"""Pre-daemon RECONSTRUCTION replay source — a SEPARATE, read-only cold store for the Binance-reconstructed
buckets in ``study/recon_archive`` (built 2025-01-01 .. 2026-06-19; see study/recon_archive + memory).

This is deliberately DISTINCT from :mod:`app.archive` (the daemon's cold mirror): the reconstructed data must
NEVER appear in normal mode or merge with daemon buckets. The terminal calls in here ONLY inside a replay whose
cursor is before the daemon era (``CUTOFF``), producing a self-contained "Binance replay" that hard-stops at the
boundary. Nothing here writes any file or DB; the daemon's ``study/archive_data`` and ``history.db`` are untouched.

Time-windowed: a replay frame needs only a small [left, right] slice, so we index each gz chunk by its first
bucket's start_time (reading ONE line per chunk) and load only the overlapping chunks — so even the 1.26M-bucket
1m scale stays cheap.
"""
from __future__ import annotations

import bisect
import datetime as _dt
import glob
import gzip
import json
import os

from . import config
from .persistence import _bucket_from_dict

# The reconstruction ends the instant before the daemon's era; the replay wall sits at 2026-06-20 00:00 UTC
# (recon strictly BEFORE — last bucket ~2026-06-19 23:59; the daemon's first bucket is 2026-06-20 12:29).
CUTOFF = int(_dt.datetime(2026, 6, 20, tzinfo=_dt.timezone.utc).timestamp())   # == 1781913600
_ROOT = os.path.join(config.PROJECT_DIR, "study", "recon_archive")

_index: "dict[str, list[tuple[float, str]]]" = {}   # tf -> [(first_start_time, chunk_path)] ascending
_index_sig: "dict[str, tuple]" = {}
_chunk_cache: "dict[str, list[dict]]" = {}          # chunk_path -> [wire_bucket] (LRU-ish, bounded)
_CHUNK_CACHE_MAX = 24


def local_dir() -> str:
    return _ROOT


def available(tf: str) -> bool:
    return bool(_chunk_paths(tf))


def _chunk_paths(tf: str) -> "list[str]":
    return sorted(glob.glob(os.path.join(_ROOT, tf, "%s_*.jsonl.gz" % tf)))


def _newest_sig(tf: str) -> tuple:
    paths = _chunk_paths(tf)
    return tuple((p, os.path.getmtime(p)) for p in paths)


def _first_start(path: str) -> float:
    """start_time of a chunk's FIRST bucket — reads only the first non-blank line (cheap)."""
    with gzip.open(path, "rt", encoding="utf-8") as gz:
        for line in gz:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            data = r["data"]
            if isinstance(data, str):
                data = json.loads(data)
            return float(data.get("start_time", 0.0) or 0.0)
    return 0.0


def _chunk_index(tf: str) -> "list[tuple[float, str]]":
    sig = _newest_sig(tf)
    if _index.get(tf) is not None and _index_sig.get(tf) == sig:
        return _index[tf]
    idx = sorted((_first_start(p), p) for p in _chunk_paths(tf))
    _index[tf] = idx
    _index_sig[tf] = sig
    return idx


def _load_chunk(path: str) -> "list[dict]":
    """All wire-buckets in a chunk (DB -> wire via full_snapshot, identical to app.archive._load), cached."""
    cached = _chunk_cache.get(path)
    if cached is not None:
        return cached
    out: list[dict] = []
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
                out.append(_bucket_from_dict(data).full_snapshot())
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        out = []
    if len(_chunk_cache) >= _CHUNK_CACHE_MAX:
        _chunk_cache.pop(next(iter(_chunk_cache)))     # evict oldest inserted
    _chunk_cache[path] = out
    return out


def earliest_start(tf: str):
    idx = _chunk_index(tf)
    return idx[0][0] if idx else None


def latest_start(tf: str):
    """start_time of the LAST reconstructed bucket (the replay's right wall) — loads only the final chunk."""
    idx = _chunk_index(tf)
    if not idx:
        return None
    last = _load_chunk(idx[-1][1])
    return float(last[-1].get("start_time", 0.0)) if last else idx[-1][0]


def window_by_time(tf: str, left_unix: float, right_unix: float) -> "list[dict]":
    """Ascending wire-buckets with left_unix <= start_time <= right_unix, clamped to the recon era (< CUTOFF).
    Loads only the chunks overlapping the range. Empty when tf/range isn't covered."""
    idx = _chunk_index(tf)
    if not idx:
        return []
    right_unix = min(float(right_unix), CUTOFF - 1)
    if right_unix < left_unix:
        return []
    starts = [x[0] for x in idx]
    lo = max(0, bisect.bisect_right(starts, left_unix) - 1)   # chunk that may hold the first in-range bucket
    hi = bisect.bisect_right(starts, right_unix)              # up to (incl.) the chunk starting <= right
    out: list[dict] = []
    for _st, path in idx[lo:max(hi, lo + 1)]:
        for b in _load_chunk(path):
            s = float(b.get("start_time", 0.0) or 0.0)
            if left_unix <= s <= right_unix:
                out.append(b)
    out.sort(key=lambda b: float(b.get("start_time", 0.0) or 0.0))
    return out


def invalidate() -> None:
    _index.clear(); _index_sig.clear(); _chunk_cache.clear()
