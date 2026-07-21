"""DA2-REVERSION v1.1 detector for the LIVE terminal (1h) — the frozen quiet-tape candidate (2026-07-21).

Registered spec: study/da2_reversion_v11_validate.py · freeze study/out/da2_reversion_v11_freeze.json · ledger
row in study/out/forward_ledger.md. This module is the LIVE mirror of that frozen rule.
(v1.0 — the same signal WITHOUT the spread gate — stays frozen and forward-audited in study/, but the terminal
overlay now renders v1.1. Both remain registered; only what is DRAWN changed.)

RULE (per closed bucket b):
    universe : MATURE buckets only — **curr_vol** >= VOL_MIN.
               *** GATE ON curr_vol, NOT target_vol. *** `target_vol` is a SNAPSHOT-level field
               (app/protocol.py:130,156), NOT per-bucket, so live wire buckets carry no `target_vol` and a gate
               on it rejects EVERYTHING while still passing an archive-based test (archive rows come from
               history.db and DO have it). Measured equivalence over 3880 archive buckets: 1263 vs 1262
               accepted, ONE disagreement, zero false rejects.
    signal   : da2 OPPOSED to the candle
                 bearish (close<open) AND da2 > 0  -> LONG   (buying accelerated into a decline = absorbed)
                 bullish (close>open) AND da2 < 0  -> SHORT  (selling accelerated into a rally  = absorbed)
                 doji (close==open) never fires
    v1.1 gate: |eff-agg spread| <= SPREAD_MAX, spread = (2*eff_causal_share - 1)*100, NON-LOCKED/first-print/
               causal. Fading a candle only works when that candle had NO conviction; a large |spread| means one
               side owned the aggressor flow (a real move, not a failed probe).
               *** NEVER the LOCKED/settled variant *** — it repaints off future data and is precisely what
               invalidated the whole pre-V3 PIVOT line.
    entry    : the bucket close
    exit     : FIXED percentages off entry — stop SL_PCT (0.8%), target TP_PCT (1.0%). NOT extreme-derived.

    da2 = (buy_vol - sell_vol - 2*delta_h1)/curr_vol, delta_h1 = running net delta at the 50%-VOLUME mark. The
    daemon stamps it from the 2026-07-20 23:18 restart onward; buckets without it CANNOT be evaluated and are
    skipped (the study reconstructs them from the 1m archive, which the terminal does not load).

CALLER CONTRACTS — both required, and v1.1 ADDS the first one:
  1. WARM-UP >= WARMUP_MIN. v1.0 needed none (every input was per-bucket), but eff_causal_share is a RUNNING
     causal computation that restarts at index 0 of whatever list it receives. Measured on the 435 v1.0 signals,
     verdict errors by prefix length: 0 -> 275 WRONG (spread off by up to 189.5), 20 -> 11, 50 -> 1,
     **100 -> 0**, 250 -> 0. Callers MUST prepend history and discard entries landing in the prefix.
  2. CLOSED-ONLY (`skip_last`, default True). The terminal appends the still-forming active bucket, and
     delta_h1 is stamped at the 50%-volume mark while buy/sell volume keep moving, so da2 would repaint.
     Pass skip_last=False ONLY for a closed-buckets-only list (e.g. replay).

NO TRADE SEQUENCING. detect() returns EVERY qualifying signal; the frozen baseline applies a non-overlap filter
(a signal on the bar where the prior trade exited is SKIPPED). Trading these badges one-for-one runs a DIFFERENT
rule than the freeze measured.

detect(buckets, skip_last=True) -> [{i, side(+1/-1), entry, sl, tp, da2, spr}]
"""
from __future__ import annotations
import numpy as np

from .pivot_detect import eff_causal_share

SL_PCT = 0.008        # FROZEN — must equal study/da2_reversion_v11_validate.SL_PCT
TP_PCT = 0.010        # FROZEN — must equal study/da2_reversion_v11_validate.TP_PCT
SPREAD_MAX = 50.0     # FROZEN — |eff-agg spread| ceiling (non-locked, causal)
VOL_MIN = 100000.0    # maturity gate on the bucket's OWN curr_vol (target_vol is snapshot-level; see above)
WARMUP_MIN = 150      # measured sufficiency is 100; 150 for margin. The terminal supplies 250 (MMXSKEW's).


def _oc(b):
    """(open, close) — robust to wire (open/close) and persisted (open_price/close_price) bucket dicts."""
    return (float(b.get("open", b.get("open_price", 0.0)) or 0.0),
            float(b.get("close", b.get("close_price", 0.0)) or 0.0))


def da2_of(b):
    """Second-half delta acceleration, or None when the bucket carries no daemon `delta_h1`."""
    dh = b.get("delta_h1")
    if dh is None:
        return None
    cv = float(b.get("curr_vol", 0.0) or 0.0)
    if cv <= 0:
        return None
    tot = float(b.get("buy_vol", 0.0) or 0.0) - float(b.get("sell_vol", 0.0) or 0.0)
    return (tot - 2.0 * float(dh)) / cv


def detect(buckets: list, skip_last: bool = True) -> "list[dict]":
    n = len(buckets)
    if n == 0:
        return []
    try:
        spr = (2.0 * np.asarray(eff_causal_share(buckets), float) - 1.0) * 100.0
    except Exception:
        return []                                      # eff-agg unavailable -> draw nothing rather than a wrong gate
    out = []
    for i in range(n - 1 if skip_last else n):
        b = buckets[i]
        if float(b.get("curr_vol", 0.0) or 0.0) < VOL_MIN:
            continue                                   # immature bucket -> outside the frozen universe
        o, c = _oc(b)
        if o <= 0 or c <= 0 or c == o:
            continue                                   # doji never fires
        if i >= len(spr) or abs(float(spr[i])) > SPREAD_MAX:
            continue                                   # v1.1: the tape must be QUIET (no side in control)
        d = da2_of(b)
        if d is None:
            continue                                   # no delta_h1 on this bucket -> not evaluable
        if c < o and d > 0:
            s = 1                                      # bearish candle, buying accelerating -> fade UP
        elif c > o and d < 0:
            s = -1                                     # bullish candle, selling accelerating -> fade DOWN
        else:
            continue
        out.append(dict(i=i, side=s, entry=c, da2=d, spr=float(spr[i]),
                        sl=c * (1 - SL_PCT) if s > 0 else c * (1 + SL_PCT),
                        tp=c * (1 + TP_PCT) if s > 0 else c * (1 - TP_PCT)))
    return out
