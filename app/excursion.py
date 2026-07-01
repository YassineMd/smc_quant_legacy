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
    """``ceil(N_used / H)`` — the CONTIGUOUS-selection floor (spec §10). Retired as the eff_n
    DEFINITION by ruling R1 (it assumes maximal overlap a scattered cohort doesn't have); kept only as
    a conservative-floor annotation next to :func:`eff_n_packed`."""
    return int(math.ceil(n_used / H)) if H > 0 else int(n_used)


def eff_n_packed(member_indices, H: int) -> int:
    """``eff_n`` (ruling R1) = size of the maximum set of members with pairwise-DISJOINT H-forward
    windows. Greedy packing over global-time-sorted members: keep a member iff its window starts
    ``>= H`` buckets after the last kept member's (windows ``[j+1, j+H]`` are disjoint iff the start
    indices differ by ``>= H``). Reduces to ``ceil(n/H)`` for a contiguous run, to the isolated-group
    count when scattered, and is correct in the mixed case — one source of truth for BOTH the ``g_effn``
    haircut and the E[R] band (the greedy-segment bootstrap resamples the very same units)."""
    ms = sorted(member_indices)
    if not ms:
        return 0
    kept = 1; last = ms[0]
    for m in ms[1:]:
        if m - last >= H:
            kept += 1; last = m
    return kept


# --------------------------------------------------------------------------- #
# Cohort matcher (commit 2) — member selection by 12-state verdict or scaled-L2 kNN.
#
# REPRESENTATION NOTE (verify-against-code catch): classify_bucket / region_state consume
# BucketSnapshot ``.get()`` DICTS (keys open/close/vol_mult/liq_short/liq_long), NOT the QuantBucket
# objects the loader returns nor the DB's _bucket_to_dict form. So the cohort layer runs on
# ``b.full_snapshot()`` dicts (quant_engine._assemble). VPIN's target_vol isn't in the snapshot →
# taken from the QuantBucket object.
# --------------------------------------------------------------------------- #
import statistics
from dataclasses import dataclass

from . import bucket_state, region_state

# feature order (all per-bucket, from full_snapshot except VPIN which needs target_vol from the object):
_FEATS = ("delta_frac", "opL_frac", "opS_frac", "vel_ratio",
          "buyer_er", "seller_er", "vpin", "liq_short_frac", "liq_long_frac")


class InsufficientSample(Exception):
    """Cohort can't clear the guards (degenerate radius, or effN < MIN_EFF_N). propose_zones catches
    this and draws only the 'insufficient sample' note (spec §2; addendum A5 nudge)."""

    def __init__(self, reason: str, eff_n_: int = 0):
        super().__init__(reason)
        self.reason = reason
        self.eff_n = eff_n_


@dataclass
class Cohort:
    members: list                     # cohort bucket indices (all have a full H-future)
    mode: str
    eff_n: int                        # R1: greedy non-overlapping window count (drives haircut + band)
    n_used: int
    radius: Optional[float]           # adaptive radius (knn) or None (state)
    verdict: Optional[str]            # anchor 12-state verdict (state mode) or None
    censored_fraction: float          # of the same-context set, fraction lost to the H-horizon
    eff_n_floor: int = 0              # ceil(n/H) conservative-floor annotation (divergence flag, R1)


def _bucket_vpin(buckets: list) -> list:
    """Per-bucket VPIN = Σ|buy−sell| over the trailing VPIN_WINDOW / (n·target_vol) — the
    quant_engine.py:410 formula, recomputed per bucket (closed_buckets carry no per-bucket vpin).
    target_vol from the QuantBucket object (not in full_snapshot)."""
    n = len(buckets); W = config.VPIN_WINDOW
    absd = [abs(float(b.buy_vol) - float(b.sell_vol)) for b in buckets]
    out = [0.0] * n; run = 0.0; dq: deque = deque()
    for i in range(n):
        dq.append(absd[i]); run += absd[i]
        if len(dq) > W:
            run -= dq.popleft()
        tv = float(getattr(buckets[i], "target_vol", 0.0)) or config.DEFAULT_TARGET_VOL
        out[i] = (run / (len(dq) * tv)) if (dq and tv > 0) else 0.0
    return out


def _feat_raw(snap: dict, vpin: float) -> list:
    cv = float(snap.get("curr_vol", 0.0)) or (float(snap.get("buy_vol", 0.0)) + float(snap.get("sell_vol", 0.0)))
    cv = cv if cv > 0 else 1e-9
    return [(float(snap.get("buy_vol", 0.0)) - float(snap.get("sell_vol", 0.0))) / cv,
            float(snap.get("opL", 0.0)) / cv, float(snap.get("opS", 0.0)) / cv,
            float(snap.get("vol_mult", 1.0)),
            float(snap.get("buyer_er", 0.0)), float(snap.get("seller_er", 0.0)), vpin,
            float(snap.get("liq_short", 0.0)) / cv, float(snap.get("liq_long", 0.0)) / cv]


def _zscale(rows: list):
    """z-score each feature column across ``rows``; std floored to 1e-9. Returns scaled rows."""
    m = len(_FEATS); n = len(rows)
    means = [sum(r[k] for r in rows) / n for k in range(m)]
    stds = [max((sum((r[k] - means[k]) ** 2 for r in rows) / n) ** 0.5, 1e-9) for k in range(m)]
    return [[(r[k] - means[k]) / stds[k] for k in range(m)] for r in rows]


def _l2(a: list, b: list) -> float:
    return math.sqrt(sum((a[k] - b[k]) ** 2 for k in range(len(a))))


def _verdict(snaps: list, j: int) -> str:
    """12-state verdict for bucket j — exhaustion_mults computed PER MEMBER (addendum A5 nudge),
    fed to classify_bucket (both consume the snapshot dicts)."""
    bm, sm, _ = region_state.exhaustion_mults(snaps, j)
    return bucket_state.classify_bucket(snaps, j, bm, sm)[0]


def build_cohort(buckets: list, anchor_idx: int, direction: str,
                 mode: str = "knn", H: int = H_DEFAULT) -> Cohort:
    """Cohort of historical buckets matching the anchor's context, in ``direction`` (long/short).
    ``buckets`` = QuantBucket objects (from load_closed_buckets). Raises InsufficientSample on a
    degenerate radius or effN < MIN_EFF_N. Only full-H-future buckets are eligible (censoring)."""
    n = len(buckets)
    snaps = [b.full_snapshot() for b in buckets]                 # BucketSnapshot dicts for state/features
    last_full = n - 1 - H
    eligible = [j for j in range(n) if j <= last_full and j != anchor_idx]
    if not eligible:
        raise InsufficientSample("no full-H-future buckets", 0)
    a = buckets[anchor_idx]
    a_sign = 1 if a.close_price >= a.open_price else -1

    if mode == "state":
        av = _verdict(snaps, anchor_idx)
        # same context = same verdict AND same directional sign as the anchor (spec §S0 "and direction")
        ctx_all = [j for j in range(n)
                   if _verdict(snaps, j) == av
                   and (1 if buckets[j].close_price >= buckets[j].open_price else -1) == a_sign
                   and j != anchor_idx]
        members = [j for j in ctx_all if j <= last_full]
        cens = 1.0 - (len(members) / len(ctx_all)) if ctx_all else 1.0
        radius = None; verdict = av
    else:  # knn
        vpins = _bucket_vpin(buckets)
        scaled = _zscale([_feat_raw(snaps[j], vpins[j]) for j in range(n)])
        x0 = scaled[anchor_idx]
        d_all = sorted(((j, _l2(scaled[j], x0)) for j in range(n) if j != anchor_idx), key=lambda t: t[1])
        seed = [(j, d) for (j, d) in d_all if j <= last_full][:config.KNN_K]     # k-nearest ELIGIBLE
        # near-identical distances (z-space) count as ZERO: 1e-6 >> float-sum rounding (~1e-7 on an
        # identical pool) yet << any real inter-bucket distance (~O(1) z-scored) -> the explicit guard fires.
        nz = [d for _, d in seed if d > 1e-6]
        if len(nz) < config.COHORT_MIN_NONZERO:
            raise InsufficientSample("degenerate cohort — radius ~0 (identical/too-few pool)", 0)
        radius = max(statistics.median(nz), config.MATCH_RADIUS_FLOOR)
        members = [j for (j, d) in seed if d <= config.MATCH_RADIUS_MULT * radius]
        knn_all = d_all[:config.KNN_K]                                            # incl. censored, for cens_frac
        cens = sum(1 for (j, _) in knn_all if j > last_full) / len(knn_all) if knn_all else 0.0
        verdict = None

    n_used = len(members)
    en = eff_n_packed(members, H)                 # R1: greedy disjoint-window count (not ceil(n/H))
    en_floor = eff_n(n_used, H)                    # conservative-floor annotation
    if en < config.MIN_EFF_N:
        raise InsufficientSample("effN %d < MIN_EFF_N %d" % (en, config.MIN_EFF_N), en)
    return Cohort(members=members, mode=mode, eff_n=en, n_used=n_used,
                  radius=radius, verdict=verdict, censored_fraction=cens, eff_n_floor=en_floor)


# --------------------------------------------------------------------------- #
# First-passage evaluator (commit 3) — competing risks, ENVELOPE + A2 bracket.
#
# Per member, walk the forward window buckets[j+1 .. j+H] and find which barrier is touched FIRST
# for a fixed geometry (fill ``f``, ``stop``, nested ``tps`` ordered nearest->farthest). ENVELOPE
# only (bucket OHLC) — no tick order (tape-exact rides a later increment, addendum A3).
#
# SAME-BUCKET AMBIGUITY (addendum A2, supersedes the spec's conservative-stop-first default): a bucket
# that holds BOTH the stop AND an as-yet-unreached TP has UNKNOWN intrabar order -> ``clean=False``.
# We do NOT assume; we BRACKET: reached_lo assigns the collision stop-first (TPs NOT counted),
# reached_hi assigns it target-first (TPs counted). Clean members have reached_lo == reached_hi.
#
# EXECUTION (spec §6/§7): TP touch reads HIGH/LOW (a wick tags a resting limit); the stop executes on
# TOUCH by default (``STOP_EXEC='touch'``; the stop LEVEL is close-anchored in commit 5). NOTE a
# §6-vs-§7 tension (stop touch vs close in the evaluator) is surfaced at the commit-3 checkpoint; the
# ``stop_exec`` arg lets the Architect pick. SHORT mirrors LONG by reflection (low<->high, <=<->>=).
# --------------------------------------------------------------------------- #
@dataclass
class Passage:
    """One member's first-passage resolution for a fixed geometry. ``reached_lo`` / ``reached_hi`` =
    TP indices reached BEFORE the stop under the A2 stop-first / target-first assignment of same-bucket
    collisions (equal when ``clean``). ``mtm_px`` = horizon-close price when ``timeout`` (mark-to-market,
    -> R in the optimiser where the stop is known), else None."""
    idx: int
    clean: bool
    reached_lo: tuple
    reached_hi: tuple
    stopped: bool
    timeout: bool
    mtm_px: Optional[float]
    touch_off: dict = None          # {k: bucket-offset (>=1) where TP_k was first reached} — median-to-touch (commit 6)
    stop_off: Optional[int] = None  # bucket-offset where the stop hit, else None


def first_passage_member(buckets: list, j: int, f: float, stop: float, tps: Sequence[float],
                         direction: str, H: int = H_DEFAULT, stop_exec: str = "touch") -> Passage:
    n = len(buckets); K = len(tps)
    long = direction == "long"
    reached: list = []
    coll: list = []
    clean = True; stopped = False; timeout = True; mtm_px = None
    touch_off: dict = {}; stop_off = None
    end = min(j + H, n - 1)
    for i in range(j + 1, end + 1):
        b = buckets[i]
        off = i - j                                # bucket-offset ahead of the signal (>=1)
        if long:
            stop_hit = (b.low <= stop) if stop_exec == "touch" else (b.close_price <= stop)
            new = [k for k in range(K) if k not in reached and b.high >= tps[k]]
        else:
            stop_hit = (b.high >= stop) if stop_exec == "touch" else (b.close_price >= stop)
            new = [k for k in range(K) if k not in reached and b.low <= tps[k]]
        if stop_hit and new:                       # collision -> ambiguous, bracket it
            clean = False; coll = sorted(new); stopped = True; timeout = False; stop_off = off
            for k in coll:
                touch_off.setdefault(k, off)       # target-first timing (reached_hi assignment)
            break
        if stop_hit:
            stopped = True; timeout = False; stop_off = off; break
        if new:
            for k in sorted(new):
                touch_off[k] = off
            reached.extend(sorted(new))
            if len(reached) == K:                  # all TPs cleared before any stop -> full winner
                timeout = False; break
    if timeout:
        mtm_px = float(buckets[end].close_price)
    return Passage(idx=j, clean=clean, reached_lo=tuple(reached),
                   reached_hi=tuple(sorted(reached + coll)), stopped=stopped,
                   timeout=timeout, mtm_px=mtm_px, touch_off=touch_off, stop_off=stop_off)


def first_passage(buckets: list, members: Sequence[int], f: float, stop: float, tps: Sequence[float],
                  direction: str, H: int = H_DEFAULT, stop_exec: str = "touch") -> dict:
    """A2 bracket over ``members`` for a fixed geometry. Returns per-TP ``P_up[k] = (lo, clean, hi)``
    (touch TP_k before stop), ``P_dn = (lo, clean, hi)`` (stop before ANY tp), ``P_timeout`` (clean,
    unambiguous), ``amb_frac``, and the per-member ``passages`` (for the scale-out replay, commit 7).
    ``clean`` = rate over the unambiguous members only (order already determined). lo/hi = ALL members
    with same-bucket mass assigned pessimistically / optimistically for that quantity."""
    K = len(tps)
    ps = [first_passage_member(buckets, j, f, stop, tps, direction, H, stop_exec) for j in members]
    N = len(ps)
    clean_ps = [p for p in ps if p.clean]

    def _rate(pred, pool):
        m = len(pool)
        return (sum(1 for p in pool if pred(p)) / m) if m else 0.0

    P_up = {}
    for k in range(K):
        lo = _rate(lambda p, k=k: k in p.reached_lo, ps)     # stop-first assignment -> fewer TPs counted
        hi = _rate(lambda p, k=k: k in p.reached_hi, ps)     # target-first -> more
        cl = _rate(lambda p, k=k: k in p.reached_lo, clean_ps)   # clean: lo == hi
        P_up[k] = (lo, cl, hi)
    # stop before ANY tp: "reached empty AND stopped". Ambiguous collisions push this UP under
    # target-first-empty (reached_hi) vs stop-first; bracket = [target-first, stop-first].
    dn_hi = _rate(lambda p: p.stopped and not p.reached_hi, ps)   # target-first -> fewest pure-stops
    dn_lo = _rate(lambda p: p.stopped and not p.reached_lo, ps)   # stop-first  -> most pure-stops
    dn_cl = _rate(lambda p: p.stopped and not p.reached_lo, clean_ps)
    P_dn = (min(dn_hi, dn_lo), dn_cl, max(dn_hi, dn_lo))
    P_timeout = _rate(lambda p: p.timeout, ps)                   # unambiguous (a collision -> stopped)
    return dict(P_up=P_up, P_dn=P_dn, P_timeout=P_timeout,
                amb_frac=(1.0 - len(clean_ps) / N) if N else 0.0,
                n=N, n_clean=len(clean_ps), passages=ps)
