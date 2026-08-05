"""1h Easy 0.5% — live overlay detector (FORWARD CANDIDATE, self-gated, fail-safe, 1h ONLY).

The validated in-sample setup from study/absorb_scaleout_1h.py, ported for the terminal:
  * candle carries an ABSORPTION badge  (an app/engulf1m_detect mark)
  * ease vw% >= 1     where vw% = (max(up_ticks,dn_ticks)/min(up_ticks,dn_ticks) - 1)*100  (stats-box "Ease +% vw")
  * absorption R (absA) <= -0.3   (easy / momentum candle — NOT absorbed)
  * SWING-ALIGNED: the entry side matches the Price&CVD developing swing leg (retracement leg -> OPPOSITE it)
  * the developing swing leg is NOT itself being absorbed:  A <= 0 AND A4 <= 0
ENTER the candle's side at the close.  Scale-out: 50% @ TP1 +0.5% / 50% @ TP2 +1.0%, SL 0.1% beyond the candle
extreme, runner trailed to break-even (+/-0.1%) after TP1.  detect() returns the entry + the four bracket levels;
the terminal draws the trade lines and (optionally) simulates the scale-out via the position tool.

NOT a proven edge: in-sample only, 95% CI on mean net/trade INCLUDES 0 (n~385).  See memory absorb-short-vw-swing.
The swing state is recomputed CAUSALLY per candidate (swing_lines over buckets[:i+1]); a caller-supplied `swing_cache`
keyed by the bar start_time makes that a one-time cost per closed bar (the forming edge is never cached).
"""
from __future__ import annotations

from . import engulf1m_detect as _e1m
from . import swing_lvn_detect as _sw
from . import absorption as _absorption

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


def _swing_state(buckets, i, n, cache):
    """(eff_side, swingA_ok) for the CAUSAL developing swing leg at bar i, or None if no leg / degenerate.
    eff_side = leg direction, flipped when the leg is a retracement. swingA_ok = not being absorbed (A<=0 & A4<=0).
    Cached by start_time for CLOSED bars only (the forming edge changes every frame -> never cached)."""
    key = None
    if cache is not None and i < n - 1:
        key = float(buckets[i].get("start_time", 0.0) or 0.0)
        if key in cache:
            return cache[key]
    try:
        legs = _sw.swing_lines(buckets[:i + 1])
    except Exception:
        return None
    dev = None
    for lg in reversed(legs):
        if lg.get("developing"):
            dev = lg
            break
    if dev is None:
        res = None
    else:
        a = dev.get("A"); a4 = dev.get("A4")
        ok = not ((a is not None and a > 0) or (a4 is not None and a4 > 0))
        legdir = 1 if dev.get("ends_high") else -1
        eff = -legdir if dev.get("is_retr") else legdir
        res = (eff, ok)
    if key is not None:
        cache[key] = res
    return res


def detect(buckets, skip_last=True, swing_cache=None, absorp=None):
    """Return [{i, side, entry, sl, tp1, tp2}] for each 1h candle passing the full 1h-Easy stack. Fail-safe: [] on error."""
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
    out = []
    for m in marks:
        try:
            i = int(m["i"]); side = int(m["side"])
            a = absorp[i]
            if a is None or a > ABS_MAX:                     # absorption R <= -0.3 (easy/momentum)
                continue
            if _vw(buckets[i]) < VW_MIN:                     # ease vw% >= 1
                continue
            st = _swing_state(buckets, i, n, swing_cache)     # causal swing-aligned + retrace-flip + swing-A/A4<=0
            if st is None:
                continue
            eff, ok = st
            if not ok or side != eff:
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
            ))
        except Exception:
            continue
    return out
