"""DA2-REVERSION v1.0 detector for the LIVE terminal (1h) — the frozen mean-reversion candidate (2026-07-21).

Registered spec: study/da2_reversion_validate.py · freeze study/out/da2_reversion_freeze.json · ledger row in
study/out/forward_ledger.md. This module is the LIVE mirror of that frozen rule — it must stay byte-equivalent.

RULE (per closed bucket b):
    universe : MATURE buckets only — target_vol >= TV_MIN. The study excludes everything before
               FM.build()'s `first` (=2618), an 11.6h backfill burst at target_vol=5000 whose sub-second
               buckets make a % stop meaningless. TV_MIN is the live equivalent of that cut.
    signal   : da2 OPPOSED to the candle
                 bearish (close<open) AND da2 > 0  -> LONG   (buying accelerated into a decline = absorbed)
                 bullish (close>open) AND da2 < 0  -> SHORT  (selling accelerated into a rally  = absorbed)
                 doji (close==open) never fires
    entry    : the bucket close
    exit     : FIXED percentages off entry — stop SL_PCT (0.8%), target TP_PCT (1.0%). NOT extreme-derived.

    da2 = (buy_vol - sell_vol - 2*delta_h1) / curr_vol, where delta_h1 is the running net delta at the bucket's
    50%-VOLUME mark. The daemon stamps `delta_h1` from the 2026-07-20 23:18 restart onward; buckets without it
    CANNOT be evaluated and are silently skipped (the study reconstructs them from the 1m archive, which the
    terminal does not load). Expect no badges on pre-restart history.

WHAT THIS MODULE DOES *NOT* DO — and the caller must:
  * NO WARM-UP NEEDED. Unlike app/mmxskew_detect, every input here (da2, direction, target_vol) is computed
    from the bucket ALONE — no EMA, no run_pos, no eff-agg — so a truncated render window cannot change a
    verdict. Do not add a warm-up prefix; it would be pure cost.
  * CLOSED-ONLY still applies. `skip_last` defaults True: the terminal appends the still-forming active bucket,
    and delta_h1 is stamped at the 50%-volume mark while buy_vol/sell_vol keep moving, so da2 on a forming
    bucket repaints. Pass skip_last=False ONLY for a closed-buckets-only list (e.g. replay).
  * NO TRADE SEQUENCING. detect() returns EVERY qualifying signal. The frozen baseline applies a non-overlap
    filter (a signal on the bar where the prior trade exited is SKIPPED — study/MMXSKEW_NOPOC.md "Execution
    contract"). Trading these badges one-for-one runs a DIFFERENT rule than the freeze measured.

detect(buckets, skip_last=True) -> [{i, side(+1/-1), entry, sl, tp, da2}]
"""
from __future__ import annotations

SL_PCT = 0.008        # FROZEN — must equal study/da2_reversion_validate.SL_PCT
TP_PCT = 0.010        # FROZEN — must equal study/da2_reversion_validate.TP_PCT
TV_MIN = 100000.0     # maturity gate; the live equivalent of the study's `first` (target_vol >= 100k)


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
    out = []
    for i in range(n - 1 if skip_last else n):
        b = buckets[i]
        if float(b.get("target_vol", 0.0) or 0.0) < TV_MIN:
            continue                                   # immature bucket -> outside the frozen universe
        o, c = _oc(b)
        if o <= 0 or c <= 0 or c == o:
            continue                                   # doji never fires
        d = da2_of(b)
        if d is None:
            continue                                   # no delta_h1 on this bucket -> not evaluable
        if c < o and d > 0:
            s = 1                                      # bearish candle, buying accelerating -> fade UP
        elif c > o and d < 0:
            s = -1                                     # bullish candle, selling accelerating -> fade DOWN
        else:
            continue
        out.append(dict(i=i, side=s, entry=c, da2=d,
                        sl=c * (1 - SL_PCT) if s > 0 else c * (1 + SL_PCT),
                        tp=c * (1 + TP_PCT) if s > 0 else c * (1 - TP_PCT)))
    return out
