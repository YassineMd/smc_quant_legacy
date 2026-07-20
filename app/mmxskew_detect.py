"""MMXSKEW / MMXSKEW-ORB entry detector for the LIVE terminal (1h). Causal, self-contained: reuses only
app.region_state (via pivot_detect.eff_causal_share), app.footprint_panel.profile_skewness and app.config —
so it is import-safe and never touches the pivot subsystem. Mirrors study/mm_skew_poc.py + study/mm_skew_orb.py.

Rule (frozen, study/MMXSKEW.md v1.1), per bucket b, LONG (short mirrors):
  bull (close>open) AND skew>0 AND panel-2 non-locked spread >= +35 AND close > POC-baseline AND delta < +15%.
ORB variant (study/MMXSKEW_ORB.md): the FIRST such signal each NY session (bucket start in [14:00 UTC, next
day) with an opening range in 13:30-14:00 UTC) that also breaks the opening range -> tagged 'orb'; its SL sits
at the session OPEN instead of the candle extreme.

detect(buckets, rr) -> list of {i, side(+1/-1), orb(bool), entry, sl, tp}. SL 0.1% beyond the extreme (mmx) /
session open (orb); TP = rr * SL distance (rr=1.5 default). Feed the SAME bucket window the terminal renders.
"""
from __future__ import annotations
import datetime as dt
from collections import defaultdict

import numpy as np

from . import config
from .pivot_detect import eff_causal_share
from .footprint_panel import profile_skewness

SPREAD_THR = 35.0
DELTA_MAX = 15.0
SL_BUF = 0.001
DEF_RR = 1.5
# UTC minutes (EDT = UTC−4 over the sample). NY open 9:30 ET = 13:30 UTC; opening range = first 30 min;
# ORB breakout entry is valid ONLY DURING THE NY RTH SESSION — 10:00 ET (14:00 UTC) .. 16:00 ET (20:00 UTC).
_OR_LO, _OR_HI, _POST, _SESS_END = 13 * 60 + 30, 14 * 60, 14 * 60, 20 * 60


def _oc(b):
    """(open, close) — robust to wire (open/close) and persisted (open_price/close_price) bucket dicts."""
    return (float(b.get("open", b.get("open_price", 0.0)) or 0.0),
            float(b.get("close", b.get("close_price", 0.0)) or 0.0))


def detect(buckets: list, rr: float = DEF_RR) -> "list[dict]":
    n = len(buckets)
    if n == 0:
        return []
    spr = (2.0 * np.asarray(eff_causal_share(buckets), float) - 1.0) * 100.0
    poc = [float(b.get("poc_price", 0.0) or 0.0) for b in buckets]
    base = [0.0] * n
    base[0] = poc[0] if poc[0] > 0 else next((p for p in poc if p > 0), 0.0)
    for k in range(1, n):
        base[k] = poc[k] * 0.05 + base[k - 1] * 0.95 if poc[k] > 0 else base[k - 1]

    sig = [0] * n
    for i in range(n):
        b = buckets[i]
        o, c = _oc(b)
        if o <= 0 or c <= 0:
            continue
        sk = profile_skewness(b.get("levels"))
        if sk is None:
            continue
        cv = float(b.get("curr_vol", 0.0)) or 1.0
        delta = (float(b.get("buy_vol", 0.0)) - float(b.get("sell_vol", 0.0))) / cv * 100.0
        if c > o and sk > 0 and spr[i] >= SPREAD_THR and c > base[i] and delta < DELTA_MAX:
            sig[i] = 1
        elif c < o and sk < 0 and spr[i] <= -SPREAD_THR and c < base[i]:
            sig[i] = -1

    # ORB tag: first post-open breakout signal per ET day (EDT = UTC-4 over the studied sample)
    days = defaultdict(list)
    for i in range(n):
        u = dt.datetime.utcfromtimestamp(float(buckets[i].get("start_time", 0.0)))
        days[(u - dt.timedelta(hours=4)).date()].append((i, u.hour * 60 + u.minute))
    orb_open = {}
    for _d, lst in days.items():
        orw = [i for i, um in lst if _OR_LO <= um < _OR_HI]
        if not orw:
            continue
        orh = max(float(buckets[i].get("high", 0.0)) for i in orw)
        orl = min(float(buckets[i].get("low", 0.0)) for i in orw)
        so, _ = _oc(buckets[orw[0]])
        for i, um in lst:
            if not (_POST <= um < _SESS_END) or sig[i] == 0:      # ORB only DURING the NY RTH session
                continue
            _o, c = _oc(buckets[i])
            if (sig[i] == 1 and c > orh) or (sig[i] == -1 and c < orl):
                orb_open[i] = so
                break

    out = []
    for i in range(n):
        s = sig[i]
        if s == 0:
            continue
        b = buckets[i]; _o, c = _oc(b); is_orb = i in orb_open
        if is_orb:
            so = orb_open[i]
            sl = so * (1 - SL_BUF) if s > 0 else so * (1 + SL_BUF)
        else:
            sl = float(b.get("low", 0.0)) * (1 - SL_BUF) if s > 0 else float(b.get("high", 0.0)) * (1 + SL_BUF)
        sld = (c - sl) if s > 0 else (sl - c)
        if sld <= 0:
            continue
        out.append(dict(i=i, side=s, orb=is_orb, entry=c, sl=sl, tp=c + rr * sld * s))
    return out
