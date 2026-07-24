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
    global _overlay, _overlay_mtime
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


def enrich_halves(buckets, tf: str) -> None:
    """Fill missing delta_h1/price_h1 on each bucket IN PLACE from the reconstruction overlay, so the terminal's
    Δ-accel / ΔP / R-h1-h2 render on old buckets. Used for BOTH archive-extend buckets (here) and the daemon's
    live-window closed_buckets (the terminal calls this on the assembled frame — those tfs stay under the 10k cap,
    so they never reach archive-extend yet still predate the daemon stamping the fields). No-op without the file
    or on a bucket with no reconstructable pair; only ever ADDS a missing field, never overwrites a live one."""
    ov = _load_overlay()
    if not ov:
        return
    for b in buckets:
        if b.get("delta_h1") is not None and b.get("price_h1") is not None:
            continue
        pair = ov.get("%s|%.3f" % (tf, float(b.get("start_time", 0.0) or 0.0)))
        if not pair:
            continue
        if b.get("delta_h1") is None and pair[0] is not None:
            b["delta_h1"] = pair[0]
        if b.get("price_h1") is None and pair[1] is not None:
            b["price_h1"] = pair[1]


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


def earliest_start(tf: str):
    """Unix ``start_time`` of the oldest archived bucket for ``tf`` (the earliest data the terminal can reach),
    or ``None`` when the archive is empty. Used to bound the date picker so no-data days are disabled."""
    d = _load(tf)
    if not d:
        return None
    st = float(d[min(d)].get("start_time", 0.0))
    return st or None


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
