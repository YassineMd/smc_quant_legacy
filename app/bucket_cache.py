"""On-disk per-timeframe bucket cache — the local BASE a reopened terminal seeds from so the
daemon only has to stream the DELTA (the buckets closed since the cached position) instead of
re-sending the whole 10k-bucket window on every reopen / tf-switch.

Measured motivation (data/startup_perf.log): the catch-up cost is ~95% ``net_idle`` — the client
waiting while the daemon builds + streams the window — and it scales with bucket count (10k → ~6s,
3k → ~1s). The parse is a rounding error (<1s) and the resident heap is only ~0.1-0.5M objects, so
the fix is to stop RE-SENDING buckets the client already has, not to shrink the parse.

Wire: the client seeds ``closed_buckets`` from :func:`load`, sends ``set_tf`` with
``since=total_closed``; the daemon (``_send_catchup``) validates the cursor and, if the delta is
contiguous with its retained window, replies with a ``delta=True`` catch-up carrying only the new
buckets — which the client APPENDS. Any mismatch (old daemon, gap past retention, schema change)
falls back to today's full catch-up with zero risk.

Format: one pickle per tf at ``data/bucket_cache_<tf>.pkl``, guarded by
(cache-version, bucket-schema, symbol, tf). Writes are atomic (temp + ``os.replace``) so a crash
mid-write can never leave a torn file — the worst case is the previous good cache (a larger delta
next open). The file is written and read only by this app, so ``pickle`` is safe here.
"""
from __future__ import annotations

import os
import pickle
import tempfile
from typing import Optional

from . import config
from .persistence import BUCKET_SCHEMA_VERSION

CACHE_VERSION = 1   # bump to invalidate every cache file when THIS module's payload shape changes


def _path(tf: str) -> str:
    return os.path.join(config.DATA_DIR, "bucket_cache_%s.pkl" % tf)


def save(tf: str, closed_buckets: list, total_closed: int,
         footprints, target_vol: float) -> bool:
    """Atomically persist one tf's base window. Best-effort — returns True on success, never raises.

    ``closed_buckets`` / ``footprints`` should be shallow copies taken under the worker lock; the
    inner bucket dicts are immutable once closed, so the shallow copy is safe to pickle off-lock.
    """
    try:
        payload = {
            "cache_v": CACHE_VERSION,
            "schema": BUCKET_SCHEMA_VERSION,
            "symbol": config.SYMBOL,
            "tf": tf,
            "total_closed": int(total_closed),
            "target_vol": float(target_vol),
            "footprints": dict(footprints) if footprints else {},
            "buckets": list(closed_buckets),
        }
        path = _path(tf)
        fd, tmp = tempfile.mkstemp(prefix=".bc_%s_" % tf, suffix=".tmp",
                                   dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, "wb") as f:
                pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, path)   # atomic rename — readers see the old or the new file, never torn
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        return True
    except Exception:
        return False


def discard(tf: str) -> None:
    """Delete a tf's cache file (best-effort). Used when a delta desyncs so the next request is a
    clean FULL catch-up rather than re-seeding from a base we no longer trust."""
    try:
        p = _path(tf)
        if os.path.exists(p):
            os.remove(p)
    except OSError:
        pass


def load(tf: str) -> Optional[dict]:
    """Return the cached payload for ``tf`` iff present AND valid
    (cache-version + bucket-schema + symbol + tf all match and the bucket list is non-empty),
    else ``None`` — the caller then requests a FULL catch-up exactly as today."""
    try:
        path = _path(tf)
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            payload = pickle.load(f)
        if (not isinstance(payload, dict)
                or payload.get("cache_v") != CACHE_VERSION
                or payload.get("schema") != BUCKET_SCHEMA_VERSION
                or payload.get("symbol") != config.SYMBOL
                or payload.get("tf") != tf):
            return None
        buckets = payload.get("buckets")
        if not isinstance(buckets, list) or not buckets:
            return None
        return payload
    except Exception:
        return None
