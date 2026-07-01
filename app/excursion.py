"""Forward-excursion core for the zone planner (ZONE_PLANNER_SPEC commit 1; addendum A1/A3/A6).

Pure / no-Qt / no-daemon — computes over ``closed_buckets`` ONLY, so it runs terminal-side with
zero daemon dependency (addendum A3). The honesty rails that apply at THIS layer are enforced here:

  * Horizon ``H`` is in BUCKETS (a volume clock), never seconds (spec §15).
  * The SIGNAL BAR IS EXCLUDED: the forward window for bucket ``j`` is ``buckets[j+1 .. j+H]`` — no
    look-ahead into the bar that generated the signal (spec §S1).
  * Anchors without a full ``H``-bucket future are CENSORED (``U/D = None``), never silently
    truncated.
  * ENVELOPE only. ``U/D`` come from bucket HIGH/LOW — the excursion ENVELOPE, a magnitude, NOT an
    ordering. Which barrier is hit FIRST (target-vs-stop within a bar) is a separate concern
    (first-passage, commit 3); this module deliberately makes NO ordering claim (addendum A2/A3).

``U`` (up excursion) and ``D`` (down excursion) are DIRECTION-NEUTRAL primitives: the long/short
reading (U = long MFE / short MAE, D = long MAE / short MFE) is applied downstream, so "short mirrors
long by reflection" costs nothing here.
"""
from __future__ import annotations

import bisect
import json
import math
import sqlite3
from collections import deque
from typing import List, Optional, Sequence

from . import config
from .persistence import _bucket_from_dict

H_DEFAULT = getattr(config, "H_DEFAULT", 20)


# --------------------------------------------------------------------------- #
# A1 loader — none existed; thin read-only load via persistence._bucket_from_dict
# --------------------------------------------------------------------------- #
def load_closed_buckets(tf: str, db_path: Optional[str] = None,
                        limit: Optional[int] = None) -> list:
    """Closed buckets for ``tf`` from history.db, oldest->newest, as reconstructed ``QuantBucket``
    objects (so the detectors — attribute access — AND this module share one representation). READ-
    ONLY connection; ``limit`` keeps only the most recent N. (addendum A1: no loader existed; this
    is the thin one, built on ``persistence._bucket_from_dict``.)"""
    path = db_path or config.HISTORY_DB
    con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    try:
        rows = con.execute("SELECT data FROM closed_buckets WHERE tf=? ORDER BY id", (tf,)).fetchall()
    finally:
        con.close()
    bk = [_bucket_from_dict(json.loads(r[0])) for r in rows]
    return bk[-limit:] if (limit and limit < len(bk)) else bk


# --------------------------------------------------------------------------- #
# Sliding-window extreme over a FIXED window of size H — O(N) monotonic deque
# --------------------------------------------------------------------------- #
def _window_extreme(arr: Sequence[float], H: int, want_max: bool) -> list:
    """``result[s] = extreme(arr[s : s+H])`` for every start ``s`` with a FULL window (s+H <= N);
    ``None`` where the window would run off the end. O(N) via a monotonic deque of indices."""
    n = len(arr)
    res: list = [None] * n
    dq: deque = deque()
    for i in range(n):
        if want_max:
            while dq and arr[i] >= arr[dq[-1]]:
                dq.pop()
        else:
            while dq and arr[i] <= arr[dq[-1]]:
                dq.pop()
        dq.append(i)
        if dq[0] <= i - H:                 # evict the index that fell out of the left edge
            dq.popleft()
        s = i - H + 1                      # bucket i closes the window that starts at s
        if s >= 0:
            res[s] = arr[dq[0]]
    return res


def forward_extremes(buckets: list, H: int = H_DEFAULT):
    """Per index ``j``: ``(fmax_high, fmin_low)`` over the forward window ``buckets[j+1 .. j+H]``
    (signal bar excluded). Both ``None`` where fewer than ``H`` buckets lie ahead (CENSORED).
    Returns ``(fmax_high, fmin_low)`` — two lists of length N."""
    n = len(buckets)
    highs = [float(b.high) for b in buckets]
    lows = [float(b.low) for b in buckets]
    wmax = _window_extreme(highs, H, True)     # wmax[s] = max(highs[s : s+H])
    wmin = _window_extreme(lows, H, False)
    fmax: list = [None] * n
    fmin: list = [None] * n
    for j in range(n):
        s = j + 1                              # forward window starts at the NEXT bucket
        if s + H <= n:                         # a full H-bucket future exists
            fmax[j] = wmax[s]; fmin[j] = wmin[s]
    return fmax, fmin


# --------------------------------------------------------------------------- #
# U/D labelling (envelope) + censoring + fidelity tag
# --------------------------------------------------------------------------- #
def label_ud(buckets: list, H: int = H_DEFAULT, signal: str = "close",
             tape_cutoff_epoch: Optional[float] = None) -> list:
    """Per bucket -> ``dict(idx, s, U, D, censored, fidelity)``.

    ``s`` = signal price = the bucket's CLOSE (spec §S1; override with ``signal`` = another attr).
    ``U = max(0, fmax_high - s)`` — up excursion (long MFE / short MAE).
    ``D = max(0, s - fmin_low)`` — down excursion (long MAE / short MFE).
    ``U/D = None, censored=True`` where the H-bucket future is incomplete (never truncated).
    ``fidelity`` = ``'tape'`` when the whole forward window sits inside the depth tape window
    (``buckets[j+1].start_time >= tape_cutoff_epoch``), else ``'envelope'``. v1 passes
    ``tape_cutoff_epoch=None`` -> everything ``'envelope'`` (addendum A3: v1 first-passage is
    envelope-only; the tape slice rides a later increment)."""
    fmax, fmin = forward_extremes(buckets, H)
    out: list = []
    for j, b in enumerate(buckets):
        s = float(b.close_price) if signal == "close" else float(getattr(b, signal))
        if fmax[j] is None:
            out.append(dict(idx=j, s=s, U=None, D=None, censored=True, fidelity="envelope"))
            continue
        fid = "envelope"
        if tape_cutoff_epoch is not None:
            nxt = buckets[j + 1]
            if nxt.start_time is not None and nxt.start_time >= tape_cutoff_epoch:
                fid = "tape"
        out.append(dict(idx=j, s=s,
                        U=max(0.0, fmax[j] - s), D=max(0.0, s - fmin[j]),
                        censored=False, fidelity=fid))
    return out


# --------------------------------------------------------------------------- #
# Distribution primitives (shared by the box-builders + confidence, later commits)
# --------------------------------------------------------------------------- #
def quantile(values, q: float):
    """Linear-interpolated ``q``-quantile (``q`` in [0,1]) over the non-``None`` values; ``None``
    if empty."""
    v = sorted(x for x in values if x is not None)
    n = len(v)
    if n == 0:
        return None
    k = (n - 1) * q
    f = int(math.floor(k)); c = min(f + 1, n - 1)
    return v[f] + (v[c] - v[f]) * (k - f)


def survival(values, levels):
    """MARGINAL survival ``P(value >= L)`` for each ``L`` in ``levels`` over non-``None`` values
    (one-sided). This is a marginal touch rate, NOT competing-risks first-passage (commit 3)."""
    v = sorted(x for x in values if x is not None)
    n = len(v)
    if n == 0:
        return [0.0 for _ in levels]
    return [(n - bisect.bisect_left(v, L)) / n for L in levels]


def eff_n(n_used: int, H: int) -> int:
    """``effN = ceil(N_used / H)`` — overlapping H-bucket forward windows share a volume clock, so
    the real independent count is ~ the stride-``H`` non-overlap count (spec §10). Every
    quantile/probability downstream is quoted on ``eff_n``, never raw N."""
    return int(math.ceil(n_used / H)) if H > 0 else int(n_used)
