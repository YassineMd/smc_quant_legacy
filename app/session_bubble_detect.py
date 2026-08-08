"""BUBBLE @ LEVEL detector — CONFIRMING aggressive bubble at a PRIOR-SESSION level (m10_sessbub overlay).

Marks the events behind the "confirming bubble -> ~88-95% rejection" statistic so they can be EYEBALLED. A session
candle (London / NY) that REACHES a prior-session structural level AND whose net-delta confirms the rejection side
(sellers hitting a resistance / buyers lifting a support) gets a yellow area.

  London context = TOKYO      [00,08) UTC  {high, low, POC}
  NY     context = TOKYO + pre-NY LONDON [08,13) UTC  {high, low, POC}   (both fully precede the tested session)

⚠ DESCRIPTIVE ONLY. The ~88% rejection is REWARD:RISK GEOMETRY, not a tradeable edge — the confirming candle already
closed near the reject side, so a near-target/far-stop wins often but nets ~0 gross (study/delta_momentum_trade.py +
study/session_level_bubbles.py). This overlay just lets you SEE the events; it is NOT a signal. Fail-safe: [].

detect(buckets, skip_last=False) -> [{i, level, side('res'|'sup')}]  (i = the confirming session candle).
"""
from __future__ import annotations

from datetime import datetime, timezone
from collections import defaultdict

from .engulf_sr_detect import _ohlc                          # parity-verified OHLC accessor

EPS = 0.0015            # level proximity: the candle reaches within 0.15% of the level
BThr = 15.0            # net-delta% magnitude that counts as a "confirming bubble" (>=57.5/42.5 one-sided)


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _poc(buckets, idxs, H, L):
    """Volume-by-price POC (+ high/low) of a session from the footprint; fallback = mid."""
    agg = defaultdict(float); hi = -1e18; lo = 1e18
    for i in idxs:
        hi = max(hi, H[i]); lo = min(lo, L[i])
        for ps, vv in (buckets[i].get("levels") or {}).items():
            try:
                p = float(ps)
            except (TypeError, ValueError):
                continue
            agg[p] += _f(vv.get("b")) + _f(vv.get("s"))
    poc = max(agg.items(), key=lambda kv: kv[1])[0] if agg else ((hi + lo) / 2.0 if hi > -1e17 else 0.0)
    return hi, lo, poc


def _scan(idxs, levels, C, H, L, DP, out):
    """Flag session candles that reach a prior level (nearest, first-touch per level) with a CONFIRMING bubble."""
    seen = set()
    for i in idxs:
        if i == 0:
            continue
        pc = C[i - 1]; best = None; bd = 1e18
        for lvl in levels:
            if lvl <= 0:
                continue
            eps = lvl * EPS
            if lvl > pc and H[i] >= lvl - eps:                       # resistance from below
                d = abs(H[i] - lvl)
                if d < bd and round(lvl, 2) not in seen:
                    best = (lvl, True); bd = d
            elif lvl < pc and L[i] <= lvl + eps:                     # support from above
                d = abs(L[i] - lvl)
                if d < bd and round(lvl, 2) not in seen:
                    best = (lvl, False); bd = d
        if best is None:
            continue
        lvl, reject_down = best; seen.add(round(lvl, 2))
        confirm = (DP[i] <= -BThr) if reject_down else (DP[i] >= BThr)   # sellers @ resistance / buyers @ support
        if confirm:
            out.append({"i": i, "level": lvl, "side": ("res" if reject_down else "sup")})


def detect(buckets, skip_last=False):
    n = len(buckets)
    if n < 12:
        return []
    try:
        O = [0.0] * n; C = [0.0] * n; H = [0.0] * n; L = [0.0] * n; DP = [0.0] * n
        day = [0] * n; hour = [0] * n
        for i, b in enumerate(buckets):
            O[i], C[i], H[i], L[i] = _ohlc(b)
            cv = _f(b.get("curr_vol"))
            if cv > 0:
                DP[i] = (_f(b.get("buy_vol")) - _f(b.get("sell_vol"))) / cv * 100.0
            d = datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc)
            day[i] = d.toordinal(); hour[i] = d.hour
        byday = defaultdict(list)
        for i in range(n):
            byday[day[i]].append(i)
        out = []
        for _d, idxs in byday.items():
            tok = [i for i in idxs if 0 <= hour[i] < 8]
            lon = [i for i in idxs if 8 <= hour[i] < 16]
            lon_pre = [i for i in idxs if 8 <= hour[i] < 13]
            ny = [i for i in idxs if 13 <= hour[i] < 21]
            if tok:
                th, tl, tp = _poc(buckets, tok, H, L)
                _scan(lon, [th, tl, tp], C, H, L, DP, out)
                if lon_pre:
                    lh, ll, lp = _poc(buckets, lon_pre, H, L)
                    _scan(ny, [th, tl, tp, lh, ll, lp], C, H, L, DP, out)
        seen = set(); res = []                                       # a candle can be flagged by both tests -> dedup
        for e in sorted(out, key=lambda e: e["i"]):
            if e["i"] in seen:
                continue
            seen.add(e["i"]); res.append(e)
        return res
    except Exception:
        return []
