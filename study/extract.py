"""TP-vs-SL barrier study — extraction pipeline (Phase 2). STUDY_SPEC.md is authoritative.

This module is built foundation-first:
  * ``load_1m`` — closed 1m buckets from the downloaded snapshot, via the repo's own
    persistence._bucket_from_dict (NO re-implementation; bit-identical QuantBucket objects).
  * ``label_episodes`` — the §2 labeler: entry = bucket close; barriers TP=±0.5% / SL=∓0.3%;
    ambiguity (one bucket spans both) -> SL (L.07); 6h horizon; UNRESOLVED + censor reasons.
  * L.* / O.* families — episode identity, outcome, and directional excursion (all OHLC-computable
    from history.db; depth.db is out of scope by design).

Entry-legal feature families (E*, G*, C.*, K.*) and the B-/J-/X- scoped panels are added in
subsequent tranches on THIS labeler; every field lands as a registry code VERBATIM, NULL+masked
where a window is unfilled, and post-hoc prefixes (J-/X-/L./O.) never inform entry-side code.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from app.persistence import _bucket_from_dict  # noqa: E402  (repo pure fn — reuse, not re-impl)

SNAPSHOT = os.path.join(REPO, "study", "data", "history_snapshot_20260702.db")
TP_PCT = 0.005          # spec §2: TP = ±0.5%
SL_PCT = 0.003          # spec §2: SL = ∓0.3%
HORIZON_S = 6 * 3600    # 6h from entry close
FIRST_IDX = 16          # spec §2: first studied bucket = index 16 (needs 15 priors)


def load_1m(db_path: str = SNAPSHOT):
    """Closed 1m buckets (oldest->newest) as QuantBucket objects + a parallel global bucket_id list.
    bucket_id = the monotonic all-time index (meta.total_closed_1m base + offset) so it survives the
    10000/tf retention cap and matches the terminal 'Idx' convention."""
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    try:
        rows = con.execute("SELECT data FROM closed_buckets WHERE tf='1m' ORDER BY id").fetchall()
        tc = con.execute("SELECT value FROM meta WHERE key='total_closed_1m'").fetchone()
    finally:
        con.close()
    bk = [_bucket_from_dict(json.loads(r[0])) for r in rows]
    total_closed = int(tc[0]) if tc else len(bk)
    base = total_closed - len(bk)                 # global index of bk[0] is base+1 (1-based, monotonic)
    ids = [base + i + 1 for i in range(len(bk))]
    return bk, ids


def label_episode(bk: list, i: int, direction: str) -> dict:
    """One (bucket i, direction) episode. Barrier scan over subsequent 1m buckets; bucket-level high/low
    touch; ambiguity(one bucket hits BOTH) -> SL. 6h horizon from entry close; UNRESOLVED + censor reason."""
    n = len(bk)
    entry = float(bk[i].close_price)
    entry_close = float(bk[i].end_time)
    long = direction == "long"
    tp = entry * (1 + TP_PCT) if long else entry * (1 - TP_PCT)   # long TP up / short TP down
    sl = entry * (1 - SL_PCT) if long else entry * (1 + SL_PCT)   # long SL down / short SL up
    horizon_end = entry_close + HORIZON_S

    outcome = "UNRESOLVED"; ambiguous = False; touch_idx = None
    up_first = down_first = None                                  # 3-way price path
    data_reaches_6h = False
    for j in range(i + 1, n):
        if float(bk[j].start_time) > horizon_end:
            data_reaches_6h = True
            break
        hi = float(bk[j].high); lo = float(bk[j].low)
        hit_tp = (hi >= tp) if long else (lo <= tp)
        hit_sl = (lo <= sl) if long else (hi >= sl)
        if hit_tp and hit_sl:                                    # one bucket spanned both -> SL by convention
            outcome = "SL"; ambiguous = True; touch_idx = j
            up_first = None; down_first = None                   # WHIPSAW (see O.01/L.08)
            break
        if hit_tp:
            outcome = "TP"; touch_idx = j; break
        if hit_sl:
            outcome = "SL"; touch_idx = j; break
    else:
        # ran off the end of the data without a touch and without crossing the 6h edge
        data_reaches_6h = False

    resolved = outcome in ("TP", "SL")
    if resolved:
        censor = "none"
    else:
        censor = "6h-window" if data_reaches_6h else "end-of-data"

    return dict(i=i, direction=direction, entry=entry, entry_close=entry_close, tp=tp, sl=sl,
                outcome=outcome, resolved=resolved, censor=censor, ambiguous=ambiguous,
                touch_idx=touch_idx, horizon_end=horizon_end)


def joint_label(long_ep: dict, short_ep: dict) -> str:
    """O.01 / L.08 — the per-BUCKET JOINT 3-way, mirrored onto both direction rows. Long TP means price
    reached +0.5% first (so it crossed the short-SL at +0.3% en route -> short=SL): UP-resolve. Short TP
    means price reached -0.5% first (crossed long-SL): DOWN-resolve. BOTH stopped (long-SL AND short-SL) =
    price tagged +/-0.3% both ways without either +/-0.5% TP = WHIPSAW. NOT the single-bucket span (that is
    the per-direction ambiguity, ~0 on 1m)."""
    lo, so = long_ep["outcome"], short_ep["outcome"]
    if lo == "UNRESOLVED" or so == "UNRESOLVED":
        return "unresolved"
    if lo == "TP":
        return "UP-resolve"          # long-TP  (implies short-SL)
    if so == "TP":
        return "DOWN-resolve"        # short-TP (implies long-SL)
    return "WHIPSAW"                 # long-SL AND short-SL


def build_episodes(bk: list, ids: list) -> list:
    n = len(bk)
    eps = []
    for i in range(FIRST_IDX, n):
        lep = label_episode(bk, i, "long")
        sep = label_episode(bk, i, "short")
        j = joint_label(lep, sep)
        for ep, d in ((lep, "long"), (sep, "short")):
            ep["joint3"] = j                     # L.08/O.01 identical across the pair (mirror)
            ep["bucket_id"] = ids[i]             # L.06 (part)
            ep["episode_id"] = "%d_%s" % (ids[i], d)   # L.09
            ep["entry_ts"] = ep["entry_close"]   # L.06 entry timestamp = entry-bucket close
        eps.append(lep); eps.append(sep)
    return eps


def write_labels_preview(eps: list, path: str) -> None:
    import csv as _csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = _csv.writer(f)
        w.writerow(["episode_id", "bucket_id", "entry_ts", "direction", "outcome", "joint_label"])
        for e in eps:
            w.writerow([e["episode_id"], e["bucket_id"], "%.3f" % e["entry_ts"],
                        e["direction"], e["outcome"], e["joint3"]])


if __name__ == "__main__":
    bk, ids = load_1m()
    n = len(bk)
    import datetime as _dt
    span = (_dt.datetime.utcfromtimestamp(bk[0].start_time), _dt.datetime.utcfromtimestamp(bk[-1].end_time))
    print("1m buckets loaded: %d | span %s -> %s UTC" % (n, span[0], span[1]))
    eps = build_episodes(bk, ids)
    print("episodes: %d (from idx %d, x2 directions)" % (len(eps), FIRST_IDX))
    _prev = os.path.join(REPO, "study", "out", "labels_preview.csv")
    write_labels_preview(eps, _prev)
    print("wrote %s" % _prev)

    from collections import Counter
    for d in ("long", "short"):
        sub = [e for e in eps if e["direction"] == d]
        oc = Counter(e["outcome"] for e in sub)
        res = [e for e in sub if e["resolved"]]
        tp_rate_all = oc["TP"] / len(sub)
        tp_rate_res = (oc["TP"] / len(res)) if res else 0.0
        print("\n%-6s n=%d  TP=%d SL=%d UNRESOLVED=%d" % (d.upper(), len(sub), oc["TP"], oc["SL"], oc["UNRESOLVED"]))
        print("       TP rate (of all)=%.1f%%  TP rate (of resolved)=%.1f%%  [random-walk null ~37.5%%]"
              % (100 * tp_rate_all, 100 * tp_rate_res))
        print("       UNRESOLVED=%.1f%%  ambiguous(SL-by-span)=%.1f%% of all  censor=%s"
              % (100 * oc["UNRESOLVED"] / len(sub),
                 100 * sum(1 for e in sub if e["ambiguous"]) / len(sub),
                 dict(Counter(e["censor"] for e in sub))))
    # JOINT 3-way per BUCKET (O.01 / L.08) — one label per bucket, mirrored onto both rows
    buckets = [e for e in eps if e["direction"] == "long"]       # exactly one row per bucket
    nb = len(buckets)
    jc = Counter(e["joint3"] for e in buckets)
    lc = Counter(e["outcome"] for e in eps if e["direction"] == "long")
    sc = Counter(e["outcome"] for e in eps if e["direction"] == "short")
    print("\n=== JOINT 3-way per bucket (O.01/L.08), n=%d ===" % nb)
    for k in ("UP-resolve", "DOWN-resolve", "WHIPSAW", "unresolved"):
        print("  %-13s %5d  (%.1f%%)" % (k, jc[k], 100 * jc[k] / nb))
    print("  reconcile: UP==long-TP? %d==%d %s | DOWN==short-TP? %d==%d %s"
          % (jc["UP-resolve"], lc["TP"], jc["UP-resolve"] == lc["TP"],
             jc["DOWN-resolve"], sc["TP"], jc["DOWN-resolve"] == sc["TP"]))
    print("  WHIPSAW: direct both-SL=%d  vs  long-SL - short-TP=%d  vs  short-SL - long-TP=%d"
          % (jc["WHIPSAW"], lc["SL"] - sc["TP"], sc["SL"] - lc["TP"]))
