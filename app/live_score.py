"""SCORE v1-SEL — live terminal scorer. Loads the FROZEN app/score_v1.json and reuses the study feature
engine (score_core -> features.py / features_b.py) on a strict 16-bucket frame [b-15, b] taken from the
terminal's cached buckets. Forward-test display only — the walk-forward exam verdict was FAIL; this is NOT
a validated probability.
"""
import os, sys, csv, json

_HERE = os.path.dirname(os.path.abspath(__file__)); _REPO = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_REPO, "study"))   # accepted: reuse the study feature engine at runtime
import score_core                                   # noqa: E402
from app.persistence import bucket_from_snapshot     # noqa: E402
from app import config                               # noqa: E402

_BUNDLE = None
FRAME = 16


def bundle():
    global _BUNDLE
    if _BUNDLE is None:
        with open(os.path.join(_HERE, "score_v1.json"), encoding="utf-8") as f:
            _BUNDLE = json.load(f)
    return _BUNDLE


def _kinds(b):
    return {c: b["sides"]["long"]["features"][c]["kind"] for c in b["sides"]["long"]["features"]}


def score_bucket(frame_snaps):
    """frame_snaps: chronological BucketSnapshot dicts ending at the target bucket. (pred_long, pred_short)
    in %, or (None, None) when <16 buckets of lookback exist (line gap — never zero-filled)."""
    if len(frame_snaps) < FRAME:
        return None, None
    b = bundle(); fr = frame_snaps[-FRAME:]
    bks = [bucket_from_snapshot(d) for d in fr]
    feat = score_core.sel16_features(fr, bks, _kinds(b))     # entry-legal values shared by both sides
    return score_core.score_side(feat, b["sides"]["long"]), score_core.score_side(feat, b["sides"]["short"])


def score_selection(cache, lo, hi):
    """Per-bucket (pred_long, pred_short) for cache indices [lo, hi] — the two panel lines across the
    selection. cache = the terminal's filtered BucketSnapshot list (live edge included)."""
    out = []
    for b in range(lo, hi + 1):
        out.append(score_bucket(cache[max(0, b - FRAME + 1): b + 1]))
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
