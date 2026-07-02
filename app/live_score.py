"""SCORE v1-SEL — live terminal scorer. Loads the FROZEN app/score_v1.json and reuses the study feature
engine (score_core -> features.py / features_b.py) on a strict 16-bucket frame [b-15, b] taken from the
terminal's cached buckets. Forward-test display only — the walk-forward exam verdict was FAIL; this is NOT
a validated probability.
"""
import os, sys, csv, json
from collections import OrderedDict

_HERE = os.path.dirname(os.path.abspath(__file__)); _REPO = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_REPO, "study"))   # accepted: reuse the study feature engine at runtime
import score_core                                   # noqa: E402
from app.persistence import bucket_from_snapshot     # noqa: E402
from app import config                               # noqa: E402

_BUNDLE = None


def bundle():
    global _BUNDLE
    if _BUNDLE is None:
        with open(os.path.join(_HERE, "score_v1.json"), encoding="utf-8") as f:
            _BUNDLE = json.load(f)
    return _BUNDLE


def frame():
    """Scoring frame length — read from the FROZEN bundle (SEL16=16, SEL7=7, ...)."""
    return int(bundle().get("frame", 16))


def _kinds(b):
    return {c: b["sides"]["long"]["features"][c]["kind"] for c in b["sides"]["long"]["features"]}


_cache = OrderedDict()          # LRU {target fingerprint -> (pred_long, pred_short)}; scores are immutable per
_CACHE_CAP = 20000             # closed bucket, so this is a pure memoization (identical output, no recompute)


def clear_cache():
    _cache.clear()


def _fp(t):
    """Immutable fingerprint of a CLOSED bucket (the live/forming edge changes end_time+curr_vol every tick,
    so it naturally misses the cache and re-scores until it closes)."""
    return (round(float(t.get("end_time", 0.0)), 3), round(float(t.get("curr_vol", 0.0)), 2),
            round(float(t.get("close", 0.0)), 5))


def score_bucket(frame_snaps):
    """frame_snaps: chronological BucketSnapshot dicts ending at the target bucket. (pred_long, pred_short)
    in %, or (None, None) when fewer than frame() buckets of lookback exist (line gap — never zero-filled).
    Memoized: a closed bucket's score is a pure function of its own trailing frame, so it computes once."""
    F = frame()
    if len(frame_snaps) < F:
        return None, None
    fr = frame_snaps[-F:]
    key = _fp(fr[-1])
    hit = _cache.get(key)
    if hit is not None:
        _cache.move_to_end(key)
        return hit
    b = bundle()
    bks = [bucket_from_snapshot(d) for d in fr]
    feat = score_core.sel16_features(fr, bks, _kinds(b))     # entry-legal values shared by both sides
    res = (score_core.score_side(feat, b["sides"]["long"]), score_core.score_side(feat, b["sides"]["short"]))
    _cache[key] = res; _cache.move_to_end(key)
    if len(_cache) > _CACHE_CAP:
        _cache.popitem(last=False)
    return res


def score_selection(cache, lo, hi):
    """Per-bucket (pred_long, pred_short) for cache indices [lo, hi] — the panel line across the selection.
    cache = the terminal's filtered BucketSnapshot list (live edge included)."""
    F = frame()
    out = []
    for b in range(lo, hi + 1):
        out.append(score_bucket(cache[max(0, b - F + 1): b + 1]))
    return out


def baselines():
    b = bundle()
    return b["sides"]["long"]["baseline"], b["sides"]["short"]["baseline"]


_FWD = os.path.join(config.DATA_DIR, "score_v1_forward.csv")
_last_logged = {"id": None}


def log_forward(bucket_id, ts, pred_long, pred_short):
    """Append ONE row per newly closed bucket (idempotent by bucket_id). v2's forward-evidence accumulator."""
    if bucket_id is None or bucket_id == _last_logged["id"] or pred_long is None or pred_short is None:
        return
    new = not os.path.exists(_FWD)
    with open(_FWD, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "bucket_id", "pred_long", "pred_short", "variant"])
        w.writerow(["%.3f" % ts, bucket_id, "%.3f" % pred_long, "%.3f" % pred_short, bundle()["variant"]])
    _last_logged["id"] = bucket_id
