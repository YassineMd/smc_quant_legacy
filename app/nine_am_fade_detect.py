"""09:00-UTC 5m FADE — live overlay detector (5m CLOCK only).

FADE the 09:00 UTC 5m bar: if it is BULLISH -> SHORT / BEARISH -> LONG. Enter at the 09:05 open (immediate — it is a
mean-reversion signal). SL = fixed 0.8%; TP = 0.5x the Tokyo (00:00-09:00 UTC) range. Weekdays only.

Robustness-cleared recon CANDIDATE (study/ny_9am_fade_*.py, 2026-08-21): among the fades of every hour 06:00-14:00, ONLY
09:00 is net-positive; the fade beats its continuation (+0.044% vs -0.163%); a fixed-direction placebo loses -> the edge
is directional (the 09:00 bar mean-reverts at the Tokyo-close/London-ramp handoff), not a geometric artifact. Tuned recon
exp ~+0.12% / prop-pass ~61%; un-tuned floor ~+0.044%. ⚠ NOT live-confirmed — recon, single asset, 2026-H1 OOS.

detect(buckets) -> [{i, sig_i, side(+1 long/-1 short), entry, sl, tp, tHi, tLo}] per weekday (i = the 09:05 ENTRY bar
index in the passed list). Fail-safe: [] on any error / when the 09:00 or 09:05 bar or the Tokyo range is absent.
"""
from __future__ import annotations
from datetime import datetime, timezone

SL_FRAC = 0.008          # fixed 0.8% stop (the stop-sweep sweet spot: R:R viable, no whipsaw)
TP_TOK_MULT = 0.5        # TP = 0.5x the Tokyo (00-09) range (adaptive; the fade MFE median is ~1.57%)


def _o(b):
    return float(b.get("open", b.get("open_price", 0.0)) or 0.0)


def _c(b):
    return float(b.get("close", b.get("close_price", 0.0)) or 0.0)


def _h(b):
    return float(b.get("high", 0.0) or 0.0)


def _l(b):
    return float(b.get("low", 0.0) or 0.0)


def detect(buckets):
    n = len(buckets)
    if n < 3:
        return []
    try:
        days = {}                                             # UTC date -> [(hour, minute, index)]
        for i, b in enumerate(buckets):
            st = float(b.get("start_time", 0.0) or 0.0)
            if st <= 0:
                continue
            t = datetime.fromtimestamp(st, tz=timezone.utc)
            if t.weekday() >= 5:                              # weekdays only
                continue
            days.setdefault(t.date(), []).append((t.hour, t.minute, i))
        out = []
        for d, lst in days.items():
            tok = [i for (h, m, i) in lst if h < 9]           # Tokyo = 00:00-09:00 UTC
            if not tok:
                continue
            tHi = max(_h(buckets[i]) for i in tok); tLo = min(_l(buckets[i]) for i in tok)
            if not (tHi > tLo):
                continue
            sig = next((i for (h, m, i) in lst if h == 9 and m == 0), None)   # the 09:00 bar (the signal)
            ent = next((i for (h, m, i) in lst if h == 9 and m == 5), None)   # the 09:05 bar (the entry)
            if sig is None or ent is None:
                continue
            o = _o(buckets[sig]); c = _c(buckets[sig])
            if o <= 0 or c <= 0 or c == o:
                continue
            side = -1 if c > o else 1                         # FADE: bullish 09:00 -> SHORT / bearish -> LONG
            entry = _o(buckets[ent])
            if entry <= 0:
                continue
            sl = entry * (1.0 - side * SL_FRAC)
            tp = entry + side * TP_TOK_MULT * (tHi - tLo)
            if (tp <= entry or sl >= entry) if side > 0 else (tp >= entry or sl <= entry):
                continue
            out.append(dict(i=ent, sig_i=sig, side=side, entry=entry, sl=sl, tp=tp, tHi=tHi, tLo=tLo))
        out.sort(key=lambda e: e["i"])
        return out
    except Exception:
        return []
