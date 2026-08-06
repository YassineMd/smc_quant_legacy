"""1h Easy 0.5% — live overlay detector (FORWARD CANDIDATE, self-gated, fail-safe, 1h ONLY).

Ported from study/absorb_scaleout_1h.py.  The Price&CVD swing filter is applied DISCRETIONARILY by the operator, so
the badge is intentionally UNFILTERED by swing here:
  * candle carries an ABSORPTION badge  (an app/engulf1m_detect mark)
  * ease vw% >= 1     where vw% = (max(up_ticks,dn_ticks)/min(up_ticks,dn_ticks) - 1)*100  (stats-box "Ease +% vw")
  * absorption R (absA) <= -0.3   (easy / momentum candle — NOT absorbed)
ENTER the candle's side at the close.  Scale-out: 50% @ TP1 +0.5% / 50% @ TP2 +1.0%, SL 0.1% beyond the candle
extreme, runner trailed to break-even (+/-0.1%) after TP1.  detect() returns the entry + the four bracket levels;
the terminal draws the trade lines and (optionally) simulates the scale-out via the position tool.

GOLD TIER: a signal whose entry sits CLOSER to the nearest active support (long) / resistance (short) than to the
opposite side gets `sr=True` -> the terminal paints it GOLD. In-sample this proximity tier ~3x'd the per-trade edge
(mean +0.045%->+0.132%, PF 1.14->1.51) on a ~1-in-4 subset — the strongest quality cut found (still n-limited).

NOT a proven edge: in-sample only, 95% CI on mean net/trade INCLUDES 0.  The swing filter (removed here) was the
single most selective condition in-sample — the operator now supplies it by eye.  See memory absorb-short-vw-swing.
"""
from __future__ import annotations

from . import engulf1m_detect as _e1m
from . import absorption as _absorption
from . import support_resistance as _sr

VW_MIN = 1.0            # ease vw% floor
ABS_MAX = -0.3          # absorption R <= this (easy/momentum)
SL_PAD = 0.001          # SL 0.1% beyond the candle extreme
TP1_PCT = 0.005         # first target +0.5%
TP2_PCT = 0.010         # runner target +1.0%
BE_PCT = 0.001          # break-even trail after TP1 (+/-0.1%)


def _vw(b):
    ut = float(b.get("up_ticks", 0.0) or 0.0)
    dt = float(b.get("dn_ticks", 0.0) or 0.0)
    mn = min(ut, dt)
    return ((max(ut, dt) / mn - 1.0) * 100.0) if mn > 0 else -1.0


def _ohlc(b):
    o = float(b.get("open", b.get("open_price", 0.0)) or 0.0)
    c = float(b.get("close", b.get("close_price", 0.0)) or 0.0)
    h = float(b.get("high", 0.0) or 0.0)
    l = float(b.get("low", 0.0) or 0.0)
    return o, c, h, l


def _sr_levels(buckets):
    """(support, resistance, K) lists of (i0, i1, price) from the S/R indicator, for the proximity tier. None on error."""
    try:
        levs = _sr.detect(list(buckets))
    except Exception:
        return None
    sup = [(lv["i0"], lv["i1"], float(lv["price"])) for lv in levs if lv.get("kind") == "S"]
    res = [(lv["i0"], lv["i1"], float(lv["price"])) for lv in levs if lv.get("kind") == "R"]
    return sup, res, _sr.SR_PIVOT_K


def _sr_ok(i, side, e, srd):
    """entry e at bar i CLOSER to the nearest ACTIVE support (long) / resistance (short) than the opposite side."""
    if srd is None:
        return False
    sup, res, K = srd
    ds = min((abs(e - p) for (i0, i1, p) in sup if i0 + K <= i and (i1 is None or i1 > i)), default=None)
    dr = min((abs(e - p) for (i0, i1, p) in res if i0 + K <= i and (i1 is None or i1 > i)), default=None)
    if ds is None or dr is None:
        return False                                          # need both a support and a resistance to compare
    return (ds < dr) if side > 0 else (dr < ds)


def detect(buckets, skip_last=True, absorp=None):
    """Return [{i, side, entry, sl, tp1, tp2}] for each 1h candle passing the 1h-Easy stack. Fail-safe: [] on error."""
    n = len(buckets)
    if n < 6:
        return []
    try:
        if absorp is None:
            absorp = []
            for k in range(n):
                try:
                    absorp.append(_absorption.absorption(buckets, k)[0])
                except Exception:
                    absorp.append(None)
        marks = _e1m.detect(buckets, skip_last=skip_last, absorp=absorp)
    except Exception:
        return []
    srd = _sr_levels(buckets)                                # S/R proximity tier (gold badge); None -> no gold
    out = []
    for m in marks:
        try:
            i = int(m["i"]); side = int(m["side"])
            a = absorp[i]
            if a is None or a > ABS_MAX:                     # absorption R <= -0.3 (easy/momentum)
                continue
            if _vw(buckets[i]) < VW_MIN:                     # ease vw% >= 1
                continue
            o, c, h, l = _ohlc(buckets[i])
            if o <= 0 or c <= 0 or h <= l:
                continue
            sl = l * (1 - SL_PAD) if side > 0 else h * (1 + SL_PAD)
            if (side > 0 and sl >= c) or (side < 0 and sl <= c):
                continue
            out.append(dict(
                i=i, side=side, entry=c, sl=sl,
                tp1=c * (1 + TP1_PCT) if side > 0 else c * (1 - TP1_PCT),
                tp2=c * (1 + TP2_PCT) if side > 0 else c * (1 - TP2_PCT),
                sr=_sr_ok(i, side, c, srd),                  # True -> entry near support(L)/resistance(R) -> GOLD badge
            ))
        except Exception:
            continue
    return out
