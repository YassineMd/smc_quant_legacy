"""Shared PURE liquidity-sweep detector — the exact frozen Tier-A/Tier-B rule, used by BOTH the offline
study (study/liq_sweeps.py) and the terminal's live bucket-close detection, so "same function" is literal.

No I/O, no Qt, no numpy dependency beyond stdlib. Feed a list of closed-bucket dicts (the terminal's
`closed_buckets` / a snapshot's rows, each with high/low/close_price/curr_vol/clS/clL/opL/opS/levels);
get back sweep events keyed by their index in that list.

Rule (frozen, mirrors study/liq_sweeps.py):
  STRUCTURE: a k=5 confirmed pivot pierced by the bar wick with the close back inside.
  FORCED-FLOW: clS (upside) / clL (downside) z >= 2 vs the 30-bucket trailing baseline AND OI delta < 0.
  VACUUM: ladder volume beyond the swept level < 10% of the bar's ladder volume.
  Tier-A = structure + forced-flow + vacuum. Tier-B = structure + exactly one. Intent: upside -> "S",
  downside -> "B" (the harvest side).
"""
from __future__ import annotations

K = 5
LOOKBACK = 100
Z_BASE = 30
Z_MIN = 2.0
VACUUM_MAX = 0.10


def _f(d, k):
    v = d.get(k)
    return float(v) if v is not None else 0.0


def _pivots(H, L):
    n = len(H); ph = {}; pl = {}
    for j in range(K, n - K):
        if H[j] > max(H[j - K:j]) and H[j] > max(H[j + 1:j + K + 1]):
            ph[j] = H[j]
        if L[j] < min(L[j - K:j]) and L[j] < min(L[j + 1:j + K + 1]):
            pl[j] = L[j]
    return ph, pl


def _z(arr, i):
    base = arr[i - Z_BASE:i]
    m = sum(base) / len(base)
    var = sum((x - m) ** 2 for x in base) / len(base)
    s = var ** 0.5
    return (arr[i] - m) / s if s > 1e-9 else 0.0


def _beyond_frac(bucket, level, upside):
    lv = bucket.get("levels") or {}
    tot = 0.0; beyond = 0.0
    for p, v in lv.items():
        sz = float(v.get("b", 0.0)) + float(v.get("s", 0.0))
        tot += sz
        if (float(p) > level) if upside else (float(p) < level):
            beyond += sz
    return (beyond / tot) if tot > 0 else 1.0          # no ladder -> not a vacuum (fail-safe)


def detect_sweeps(buckets, start=None):
    """Return sweep events over ``buckets`` (list of dicts). Each event:
    {i, side ('S'/'B'), kind ('Sweep'), level, wick_pct, forced_z, vacuum_frac, oi_delta, tier ('A'/'B'),
     forced (0/1), vacuum (0/1)}. ``start`` limits the scan to i >= start (for cheap live-edge updates);
    the k=5 pivots and the 30-bucket baseline still look back into the full list."""
    n = len(buckets)
    if n < Z_BASE + K + 1:
        return []
    H = [_f(b, "high") for b in buckets]; L = [_f(b, "low") for b in buckets]
    C = [_f(b, "close_price") for b in buckets]
    clS = [_f(b, "clS") for b in buckets]; clL = [_f(b, "clL") for b in buckets]
    oi = [(_f(b, "opL") + _f(b, "opS")) - (_f(b, "clL") + _f(b, "clS")) for b in buckets]
    ph, pl = _pivots(H, L)
    ph_bars = sorted(ph); pl_bars = sorted(pl)
    lo = max(Z_BASE, K + 1) if start is None else max(start, Z_BASE, K + 1)
    out = []
    for i in range(lo, n):
        for upside in (True, False):
            bars = ph_bars if upside else pl_bars
            level = None
            for j in reversed(bars):
                if j + K > i - 1:
                    continue
                if j < i - LOOKBACK:
                    break
                P = ph[j] if upside else pl[j]
                if ((H[i] > P and C[i] <= P) if upside else (L[i] < P and C[i] >= P)):
                    level = P
                    break
            if level is None:
                continue
            fz = _z(clS if upside else clL, i)
            forced = fz >= Z_MIN and oi[i] < 0
            vac = _beyond_frac(buckets[i], level, upside)
            vacuum = vac < VACUUM_MAX
            if not (forced or vacuum):
                continue
            wick = (H[i] - level) if upside else (level - L[i])
            out.append(dict(i=i, side="S" if upside else "B", kind="Sweep", level=level,
                            wick_pct=round(wick / level * 100.0, 4), forced_z=round(fz, 2),
                            vacuum_frac=round(vac, 4), oi_delta=round(oi[i], 1),
                            tier="A" if (forced and vacuum) else "B",
                            forced=int(forced), vacuum=int(vacuum)))
    return out
