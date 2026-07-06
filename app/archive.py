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

from . import config
from .persistence import _bucket_from_dict

_ROOT = os.path.join(config.PROJECT_DIR, "study", "archive_data")
_cache: dict[str, dict[int, dict]] = {}     # tf -> {bid: wire_bucket}
_stamp: dict[str, float] = {}               # tf -> newest-chunk mtime the cache was built from


def _chunk_paths(tf: str) -> list[str]:
    return sorted(glob.glob(os.path.join(_ROOT, tf, "%s_*.jsonl.gz" % tf)))


def _newest_mtime(tf: str) -> float:
    return max((os.path.getmtime(p) for p in _chunk_paths(tf)), default=0.0)


def _load(tf: str) -> dict[int, dict]:
    """{bid: wire_bucket} for a tf from the local mirror; cached until a new pull touches the chunks."""
    m = _newest_mtime(tf)
    if tf in _cache and _stamp.get(tf) == m:
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
    _cache[tf] = out
    _stamp[tf] = m
    return out


def available(tf: str) -> bool:
    return bool(_chunk_paths(tf))


def oldest_bid(tf: str):
    d = _load(tf)
    return min(d) if d else None


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
