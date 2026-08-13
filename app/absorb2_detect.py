"""5m ABSORPTION-SEQUENCE (WALL) setup — LIVE overlay signal riding the 5m Absorption Wall overlay as a
BLUE (long) / ORANGE (short) TRIANGLE. NOT an engulf: a two-candle absorption continuation defended at a wall radar.

  C1 (prev bar): WITH-POSITION (may be small-bodied), with an ORANGE/BLUE BORDER = a delta-vs-direction divergence.
                 BLUE = a BULLISH candle on NEGATIVE delta (price closed UP on net aggressive SELLING -> buyers absorbed
                 it); ORANGE = a BEARISH candle on POSITIVE delta (price closed DOWN on net aggressive BUYING). delta =
                 buy_vol - sell_vol. A long needs a BLUE C1, a short needs an ORANGE C1.
  C2 (signal bar, follows C1): WITH-POSITION, NON-doji, absorption A < 0 (a with-trend candle that is NOT absorbed).
  C1 and C2 must be the SAME side (both bullish for a long, both bearish for a short); C1 may be small/weak, C2 may not.

REWORKED 2026-08-13 (user): the S/R bias + overlap/into-zone guards + VA confluence are replaced by the WALL radar
context, mirroring engulf5m_detect — a LONG must sit in a SUPPORT wall's radar visit, a SHORT in a RESISTANCE wall's.
Fires ONLY on the continuation side (no reversal exception). Every signal is a TRIANGLE (up = long, down = short).
EXIT: SL 0.1% beyond the widest of {C1,C2} extreme; TP 1:1.5. Entry = C2 close.

detect(buckets, walls=None, skip_last=True, absorp=None, require_border=True) -> [{i, side(+1/-1), entry, sl, tp,
  src('ABSORB2'), conf}]. `walls` = absorption_level_detect.detect() marks (detected internally if None).
"""
from __future__ import annotations

from . import absorption_level_detect as _al
from . import absorption as _absorption
from .engulf_sr_detect import _ohlc

SL_PAD = 0.001
RR = 1.5
ABS_C2 = 0.0         # C2 absorption gate: A < 0 (a with-trend candle that is NOT absorbed / an easy push)


def _radar_flags(buckets, walls, n):
    """Per-bar (in_support_radar, in_resistance_radar) from the walls' radar visit windows. Detects walls if None."""
    if walls is None:
        try:
            walls = _al.detect(buckets, skip_last=False)
        except Exception:
            walls = []
    sup = [False] * n; res = [False] * n
    for w in (walls or []):
        s = w.get("side")
        for r in w.get("radar_runs", ()):
            if len(r) < 2:
                continue
            rk0 = max(0, int(r[0])); rk1 = min(n - 1, int(r[1]))
            for k in range(rk0, rk1 + 1):
                if s == "S":
                    sup[k] = True
                elif s == "R":
                    res[k] = True
    return sup, res


def detect(buckets, walls=None, skip_last=True, absorp=None, require_border=True):
    n = len(buckets)
    if n < 3:
        return []
    O = [0.0] * n; C = [0.0] * n; Hi = [0.0] * n; Lo = [0.0] * n; DLT = [0.0] * n
    for i, b in enumerate(buckets):
        O[i], C[i], Hi[i], Lo[i] = _ohlc(b)
        DLT[i] = float(b.get("buy_vol", 0.0) or 0.0) - float(b.get("sell_vol", 0.0) or 0.0)   # delta = buy - sell aggression
    sup_radar, res_radar = _radar_flags(buckets, walls, n)

    def nd(i):
        b = abs(C[i] - O[i]); return b > (Hi[i] - max(O[i], C[i])) and b > (min(O[i], C[i]) - Lo[i])

    def border(i):
        """(blue, orange) BORDER = delta-vs-direction divergence. BLUE = a BULLISH candle on NEGATIVE delta (price up on
        net selling -> buyers absorbing). ORANGE = a BEARISH candle on POSITIVE delta (price down on net buying)."""
        return (C[i] > O[i] and DLT[i] < 0.0), (C[i] < O[i] and DLT[i] > 0.0)

    out = []
    for i in range(1, (n - 1) if skip_last else n):
        if O[i] <= 0 or C[i] <= 0 or (Hi[i] - Lo[i]) <= 0 or O[i - 1] <= 0 or (Hi[i - 1] - Lo[i - 1]) <= 0:
            continue
        if absorp is not None:
            a2 = absorp[i]
        else:
            try:
                a2 = _absorption.absorption(buckets, i)[0]
            except Exception:
                a2 = None
        if a2 is None or a2 >= ABS_C2 or not nd(i):   # C2 must be a real (non-doji) move with A < 0; C1 need NOT be non-doji
            continue                                  # — the border (a bullish/bearish candle on divergent delta) can be small
        blue1, orange1 = border(i - 1)
        for side in (1, -1):
            if side > 0:
                trend = (C[i] > O[i]) and (C[i - 1] > O[i - 1]); imb_ok = blue1; ctx = sup_radar[i]
            else:
                trend = (C[i] < O[i]) and (C[i - 1] < O[i - 1]); imb_ok = orange1; ctx = res_radar[i]
            if not (trend and (imb_ok or not require_border)):    # C1 border + C1/C2 same-side (A<0 already gated on C2)
                continue
            if not ctx:                                           # WALL radar context replaces the S/R bias + guards
                continue
            c = C[i]
            if side > 0:
                sl = min(Lo[i], Lo[i - 1]) * (1 - SL_PAD)
                if sl >= c:
                    continue
            else:
                sl = max(Hi[i], Hi[i - 1]) * (1 + SL_PAD)
                if sl <= c:
                    continue
            sld = (c - sl) if side > 0 else (sl - c)
            out.append(dict(i=i, side=side, entry=c, sl=sl, tp=c + RR * sld * side, src="ABSORB2", conf=False))
            break
    return out
