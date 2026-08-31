"""FOOTPRINT BACKFILL from Binance aggTrades REST — heal clock candles that have OHLC but NO footprint.

WHY (2026-08-31): before the recording fix (fb71ecc) the daemon only persisted clock candles on client-driven
paths, so the store accumulated years of kline-only candles — real OHLC, EMPTY `levels`. Measured on the VM at
deploy: 1m 3124/3224, 5m 1243/1279, 15m 636/805, 30m 571/885, 1h 692/845, 4h 794/834 candles had no footprint.
The raw trades still exist upstream: Binance serves the FULL aggTrade history over REST. This module rebuilds
the missing footprints from that tape, exactly as the live engine would have (`time_candles.build_time_candles`
uses the daemon's own binning primitives).

HOW:
  * A GAP = a stored candle whose `levels` is empty while it actually traded (curr_vol > 0). Honest flats are
    left alone; the forming edge is never touched.
  * Work unit = a 4h-ALIGNED block (covers every tf's candle boundaries), processed NEWEST-FIRST so recent
    history heals first. Inside a block only the HOURS that intersect a gap are fetched.
  * Fetch = windows <= 30 min by startTime/endTime, then `fromId` pagination inside a window (exact across
    same-millisecond bursts). The injected `http_get(params) -> list|None` does symbol/pacing/rate-limit
    handling, so this module stays pure and unit-testable.
  * A candle is healed ONLY when every hour it spans was FULLY fetched (a budget/network stop mid-hour heals
    nothing half-way — no torn footprints). The stored candle's official kline OHLC is PRESERVED; only the
    tape-derived fields (levels, buy/sell/curr vol, POC, n_trades) are replaced.
  * An interval whose kline says it traded but whose tape comes back empty is left UNTOUCHED and counted
    (`no_tape`) — never fabricate.
  * Hard per-pass request budget -> the healer converges over passes (hourly in the daemon) without ever
    hammering the API. aggTrades weight = 20; the daemon paces ~1 req/s (~1200 weight/min vs the 2400 limit).

Pure logic in / marks out — no network, no Qt. The daemon wires it in feeds._tc_backfill_loop.
"""
from __future__ import annotations

import time

AGG_URL = "https://fapi.binance.com/fapi/v1/aggTrades"
_HOUR = 3600
_BLOCK = 4 * _HOUR                      # covers 1m/5m/15m/30m/1h/4h candle boundaries exactly
_WINDOW_MS = 30 * 60 * 1000             # <= 30 min per startTime/endTime request (API constraint is < 1h)


def find_gaps(store: dict, tf_secs: int, now: float) -> "list[tuple[int, int]]":
    """[(start, end), ...] of stored candle intervals that TRADED but carry no footprint. Ascending."""
    out = []
    for k, w in store.items():
        try:
            stk = int(k)
        except (TypeError, ValueError):
            continue
        if stk + tf_secs > now - 120:                    # forming / too fresh — the live engine owns it
            continue
        if w.get("levels"):
            continue
        try:
            if float(w.get("curr_vol", 0.0) or 0.0) <= 0.0:
                continue                                 # honest flat interval — nothing to rebuild
        except (TypeError, ValueError):
            continue
        out.append((stk, stk + tf_secs))
    out.sort()
    return out


def fetch_window(http_get, t0_ms: int, t1_ms: int, budget: int) -> "tuple[list, int, int]":
    """Fetch every aggTrade in [t0_ms, t1_ms) -> (trades, covered_ms, requests_used).

    Windows of <= 30 min via startTime/endTime; inside a window, `fromId` pagination walks past the 1000-row
    cap (exact even when 1000+ trades share a millisecond). `covered_ms` = the exclusive upper bound of the
    span that is COMPLETE — a budget/network stop returns what was fetched and where coverage ends."""
    trades = []; used = 0; t = int(t0_ms)
    while t < t1_ms:
        if used >= budget:
            return trades, t, used
        w1 = min(t + _WINDOW_MS, int(t1_ms))
        raw = http_get({"startTime": t, "endTime": w1 - 1, "limit": 1000})
        used += 1
        if raw is None:
            return trades, t, used                       # network / rate-limit stop
        trades.extend(r for r in raw if t0_ms <= int(r["T"]) < w1)
        last = raw
        while len(last) == 1000 and int(last[-1]["T"]) < w1:
            if used >= budget:
                # incomplete window: coverage ends at the last trade actually seen
                return trades, min(int(last[-1]["T"]), w1), used
            nxt = http_get({"fromId": int(last[-1]["a"]) + 1, "limit": 1000})
            used += 1
            if nxt is None:
                return trades, min(int(last[-1]["T"]), w1), used
            trades.extend(r for r in nxt if t0_ms <= int(r["T"]) < w1)
            if not nxt:
                break
            last = nxt
        t = w1
    return trades, int(t1_ms), used


def heal_pass(stores: "dict[str, dict]", tf_secs: "dict[str, int]", http_get,
              budget: int = 600, now: "float | None" = None,
              min_start: "float | None" = None) -> dict:
    """One budgeted pass over every tf's gaps, newest 4h-block first. Mutates the store dicts IN PLACE
    (per-key assignment — atomic under the GIL, same pattern as the daemon's kline heal). Returns stats.

    `min_start`: exclude gaps starting before this epoch — the aggTrades REST endpoint is HARD-RESTRICTED to
    the most recent ~2 days (error -4166, probed 2026-08-31: BOTH time-window and fromId queries refuse older
    data), so without the clamp every pass would burn its whole budget on permanently-dead requests. Older
    gaps are counted `unreachable` and belong to the Binance-Vision dump backfiller (ops/)."""
    from .time_candles import build_time_candles, to_bucket_wire
    now = float(now if now is not None else time.time())
    gaps = {tf: find_gaps(stores.get(tf) or {}, tf_secs[tf], now) for tf in stores}
    unreachable = 0
    if min_start is not None:
        for tf in list(gaps):
            keep = [(s, e) for (s, e) in gaps[tf] if s >= min_start]
            unreachable += len(gaps[tf]) - len(keep)
            gaps[tf] = keep
    blocks = sorted({(s // _BLOCK) * _BLOCK for tf in gaps for (s, _e) in gaps[tf]}, reverse=True)
    stats = {"healed": {tf: 0 for tf in stores}, "no_tape": 0, "requests": 0, "blocks": 0,
             "gaps_left": 0, "vol_mismatch": 0, "unreachable": unreachable}
    for b0 in blocks:
        if stats["requests"] >= budget:
            break
        b1 = b0 + _BLOCK
        need = set()                                     # hours in this block that intersect ANY gap
        for tf, gl in gaps.items():
            for (s, e) in gl:
                if s < b1 and e > b0:
                    h = (max(s, b0) // _HOUR) * _HOUR
                    while h < min(e, b1):
                        need.add(h); h += _HOUR
        if not need:
            continue
        stats["blocks"] += 1
        trades = []; covered = {}
        for h in sorted(need):
            tr, cov_ms, used = fetch_window(http_get, h * 1000, (h + _HOUR) * 1000,
                                            budget - stats["requests"])
            stats["requests"] += used
            trades.extend({"p": r["p"], "q": r["q"], "m": r["m"], "T": r["T"]} for r in tr)
            covered[h] = cov_ms >= (h + _HOUR) * 1000
            if not covered[h]:
                break                                    # budget / network stop — later hours stay unfetched
        for tf, store in stores.items():
            secs = tf_secs[tf]; reb = None
            for (s, e) in gaps[tf]:
                if not (s >= b0 and e <= b1):
                    continue                             # candles never cross a 4h-aligned block
                if not all(covered.get(h, False) for h in range(( s // _HOUR) * _HOUR, e, _HOUR)):
                    continue                             # not fully covered -> do NOT half-heal
                if reb is None:                          # bin the block's tape once per tf, on demand
                    reb = {int(c["start_time"]): to_bucket_wire(c)
                           for c in build_time_candles(trades, secs, gap_fill=False)}
                new = reb.get(s)
                old = store.get(s, store.get(str(s)))
                if new is None:
                    stats["no_tape"] += 1                # kline says it traded, tape says silent -> leave it
                    continue
                if old:
                    for f in ("open", "high", "low", "close"):   # official kline OHLC stays authoritative
                        if old.get(f) is not None:
                            new[f] = old[f]
                    try:                                 # sanity: tape volume vs kline volume (report only)
                        ov = float(old.get("curr_vol", 0.0) or 0.0); nv = float(new.get("curr_vol", 0.0) or 0.0)
                        if ov > 0 and abs(nv - ov) > 0.05 * ov:
                            stats["vol_mismatch"] += 1
                    except (TypeError, ValueError):
                        pass
                key = s if s in store else (str(s) if str(s) in store else s)
                store[key] = new
                stats["healed"][tf] += 1
    stats["gaps_left"] = sum(len(g) for g in gaps.values()) - sum(stats["healed"].values()) - stats["no_tape"]
    stats["healed_total"] = sum(stats["healed"].values())
    return stats
