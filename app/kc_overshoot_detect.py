"""Keltner-Channel OVERSHOOT -> 2nd-ENTRY continuation setup (user strategy, 2026-08-16).

Momentum-continuation, NOT mean-reversion. A close BEYOND a Keltner extreme signals a thrust; after a pullback we
enter WITH the thrust on the SECOND entry (Wade/Brooks second-entry style). Bands = the terminal's Keltner exactly:
EMA(close, 20) +/- 2.25 * ATR(20, Wilder RMA).

SHORT setup (LONG mirrors: swap band/high<->low/open comparisons):
  1. ARM / "0" - a bar CLOSES BELOW the lower band. The "0" (Brooks/Wade) is the LAST bar to close beyond the band
                 before the pullback: if, after we've started counting entries, a bar CLOSES BELOW the band AGAIN, that
                 bar becomes the new "0" and the entry count RESTARTS (the earlier 1st entry is dismissed).
  2. RE-ENTER  - price pulls back up until a bar CLOSES back inside the band (close > lower).
  3. 1ST ENTRY - a bar CLOSES BELOW the low of the bar preceding it (marked, NOT traded); remember its OPEN.
  4. PULLBACK  - a bar CLOSES ABOVE the OPEN of that 1st-entry bar.
  5. 2ND ENTRY - again a bar CLOSES BELOW the low of the preceding bar -> the SHORT signal (entry = its close).
                 NOT a DOJI (body >= DOJI_BODY_FRAC of the bar's range) -> a doji is indecision, not a thrust; if the
                 trigger bar is a doji we skip it and keep waiting for the next valid non-doji 2nd-entry trigger.
  INVALIDATE   - if, any time before the 2nd entry, price TOUCHES the opposite band (short: high >= upper), kill it.
  EXPIRE       - if the 2nd entry has not fired within MAX_WAIT bars of the arm, drop the setup.

detect(buckets) -> list of fired setups: dict(side=+1 long / -1 short, i_over, i_e1, i_e2, entry). One active setup at
a time (a new arm is ignored until the current setup fires / invalidates / expires)."""

KC_LENGTH = 20
KC_MULT = 2.25
MAX_WAIT = 40
DOJI_BODY_FRAC = 0.20   # 2nd-entry bar is a DOJI (skip, wait for a better bar) if |close-open| < this fraction of its (high-low) range
FAIL_WAIT = 20          # bars after the 2nd entry to watch for its FAILURE (the reversal) before it reaches its TP


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _is_doji(o, h, l, c, frac):
    rng = h - l
    return rng <= 0.0 or (abs(c - o) / rng) < frac


def keltner(highs, lows, closes, length=KC_LENGTH, mult=KC_MULT):
    """EXACT replica of terminal._keltner_bands: EMA(close) basis +/- mult*ATR (Wilder RMA of True Range)."""
    n = len(closes)
    if n == 0:
        return [], [], []
    k = 2.0 / (length + 1)
    mid = [0.0] * n; atr = [0.0] * n
    e = closes[0]; a = highs[0] - lows[0]
    mid[0] = e; atr[0] = a
    for i in range(1, n):
        e = closes[i] * k + e * (1.0 - k)
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        a = (a * (length - 1) + tr) / length
        mid[i] = e; atr[i] = a
    up = [mid[i] + mult * atr[i] for i in range(n)]
    lo = [mid[i] - mult * atr[i] for i in range(n)]
    return up, mid, lo


def detect(buckets, length=KC_LENGTH, mult=KC_MULT, max_wait=MAX_WAIT, doji_frac=DOJI_BODY_FRAC,
           with_failed=False, fail_wait=FAIL_WAIT, fail_tp=0.005, skip_last=False):
    n = len(buckets)
    if skip_last:
        n -= 1
    if n < length + 2:
        return []
    O = [_f(b.get("open", b.get("open_price"))) for b in buckets[:n]]
    C = [_f(b.get("close", b.get("close_price"))) for b in buckets[:n]]
    Hh = [_f(b.get("high")) for b in buckets[:n]]
    Ll = [_f(b.get("low")) for b in buckets[:n]]
    up, _mid, lo = keltner(Hh, Ll, C, length, mult)

    events = []
    state = 0            # 0 idle, 1 await re-enter, 2 await 1st entry, 3 await pullback, 4 await 2nd entry
    side = 0; i_zero = -1; over_close = 0.0; i_e1 = -1; open1 = 0.0    # i_zero = the "0" = the LAST bar that closed
    i = 1                                                             #          beyond the band; over_close = its close (TP)
    while i < n:
        if Hh[i] <= 0 or Ll[i] <= 0 or up[i] <= 0:
            i += 1; continue
        if state == 0:                                               # IDLE -> arm on the first close beyond a band
            if C[i] < lo[i]:
                side = -1; state = 1; i_zero = i; over_close = C[i]; i_e1 = -1
            elif C[i] > up[i]:
                side = 1; state = 1; i_zero = i; over_close = C[i]; i_e1 = -1
            i += 1; continue
        # --- active setup (state >= 1) ---
        if ((Hh[i] >= up[i]) if side < 0 else (Ll[i] <= lo[i])) or (i - i_zero) > max_wait:   # opposite-band touch / expiry
            state = 0                                                # kill; this bar may itself be a fresh arm
            if C[i] < lo[i]:
                side = -1; state = 1; i_zero = i; over_close = C[i]; i_e1 = -1
            elif C[i] > up[i]:
                side = 1; state = 1; i_zero = i; over_close = C[i]; i_e1 = -1
            i += 1; continue
        if (C[i] < lo[i]) if side < 0 else (C[i] > up[i]):           # closed beyond the SAME band again -> this is the NEW
            i_zero = i; over_close = C[i]; i_e1 = -1; state = 1       # "0"; dismiss any counted entries, restart the count
            i += 1; continue
        # bar closed INSIDE the band
        if state == 1:                                               # was awaiting re-enter -> re-entered now
            state = 2
        elif state == 2:                                             # await 1st entry (close beyond the prior bar's extreme)
            if (C[i] < Ll[i - 1]) if side < 0 else (C[i] > Hh[i - 1]):
                i_e1 = i; open1 = O[i]; state = 3
        elif state == 3:                                             # await pullback past the 1st-entry OPEN
            if (C[i] > open1) if side < 0 else (C[i] < open1):
                state = 4
        elif state == 4:                                             # await 2nd entry (same trigger, NON-doji)
            _trig = (C[i] < Ll[i - 1]) if side < 0 else (C[i] > Hh[i - 1])
            if _trig and not _is_doji(O[i], Hh[i], Ll[i], C[i], doji_frac):   # skip a doji -> keep waiting
                events.append(dict(kind="entry", side=side, i_over=i_zero, i_e1=i_e1, i_e2=i, entry=C[i],
                                   over_close=over_close, i_lastout=i_zero))
                state = 0
        i += 1

    if with_failed:                                       # FAILED 2nd entry (Wade) -> reverse to the OPPOSITE side
        rev = []
        for e in events:
            k = e["i_e2"]; osd = e["side"]; ent = C[k]
            for j in range(k + 1, min(n, k + 1 + fail_wait)):
                if osd > 0:                               # 2nd entry LONG: won if it reaches +TP first (no reversal);
                    if Hh[j] >= ent * (1.0 + fail_tp):    #   FAILS when a BEARISH bar closes BELOW the entry bar's low
                        break
                    if C[j] < O[j] and C[j] < Ll[k]:
                        rev.append(dict(kind="failed", side=-1, orig_side=1, i_e2=k, i_fail=j, entry=C[j])); break
                else:                                     # 2nd entry SHORT: mirror -> reverse LONG on a bullish close
                    if Ll[j] <= ent * (1.0 - fail_tp):    #   above the entry bar's high
                        break
                    if C[j] > O[j] and C[j] > Hh[k]:
                        rev.append(dict(kind="failed", side=1, orig_side=-1, i_e2=k, i_fail=j, entry=C[j])); break
        events = events + rev
    return events
