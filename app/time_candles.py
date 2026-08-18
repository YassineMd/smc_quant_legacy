"""CLOCK (time) candles with per-price-level footprints — the data core of the time-candle feature.

Bins a raw aggTrade stream into fixed clock intervals (1m/5m/15m/30m/1h/4h) using the daemon's OWN primitives
(`aggtrade.trade_to_tick` + `candle_open`), so a time candle is byte-identical to what the server-side footprint node
already accumulates. Output dicts use the SAME field names as the volume-bucket dicts, so every overlay / footprint /
stats reader consumes them unchanged — only the source (clock vs volume) and the x-axis (time vs bucket index) differ.

HONESTY by construction:
  * Every trade is binned by its true `T` (execution ms) at its true price — nothing lost, nothing off-level
    (proven to floating-point precision by `parity()` / the __main__ self-test).
  * GAP-FILL: every interval in [first, last] gets exactly one candle; an interval with no trades becomes a flat,
    zero-volume candle carrying the prior close. So `start_time = t0 + index*tf` EXACTLY -> the index axis is a perfect
    linear proxy for real time and a real data gap is VISIBLE (a flat run), never silently compressed.

`target_vol` is deliberately 0.0: it is a volume-bucket concept with no honest meaning for a clock candle (do NOT let a
consumer divide by it as if these were volume buckets)."""
from __future__ import annotations

from typing import Iterable

from .aggtrade import candle_open, trade_to_tick


def _new(t0: int, tf_secs: int, price: float, empty: bool = False) -> dict:
    return {"start_time": float(t0), "end_time": float(t0 + tf_secs),
            "open_price": price, "close_price": price, "high": price, "low": price,
            "buy_vol": 0.0, "sell_vol": 0.0, "curr_vol": 0.0, "target_vol": 0.0,
            "poc_price": price, "levels": {}, "n_trades": 0, "empty": bool(empty)}


def build_time_candles(trades: Iterable[dict], tf_secs: int, gap_fill: bool = True) -> list:
    """Raw aggTrades ({p,q,m,T}) -> list of clock-candle dicts (bucket-shaped), sorted by time. gap_fill inserts a flat
    candle for every empty interval so the series is contiguous in clock time."""
    by = {}
    for d in trades:
        vol = float(d.get("q", 0.0) or 0.0)
        if vol <= 0.0:
            continue
        t_ms = int(d["T"]); k = candle_open(t_ms, tf_secs)
        ta = trade_to_tick(d); price = ta.price; is_buy = ta.taker_buy > 0.0
        c = by.get(k)
        if c is None:
            c = by[k] = _new(k, tf_secs, price)
            c["open_price"] = price
        c["high"] = price if price > c["high"] else c["high"]
        c["low"] = price if price < c["low"] else c["low"]
        c["close_price"] = price
        c["curr_vol"] += vol; c["n_trades"] += 1
        c["buy_vol" if is_buy else "sell_vol"] += vol
        lv = c["levels"].get("%.2f" % price)
        if lv is None:
            lv = c["levels"]["%.2f" % price] = {"b": 0.0, "s": 0.0}
        lv["b" if is_buy else "s"] += vol
    if not by:
        return []
    # POC = heaviest-volume level per candle (the footprint node price)
    for c in by.values():
        best_p = None; best_v = -1.0
        for ps, lv in c["levels"].items():
            v = lv["b"] + lv["s"]
            if v > best_v:
                best_v = v; best_p = float(ps)
        if best_p is not None:
            c["poc_price"] = best_p
    ks = sorted(by)
    if not gap_fill:
        return [by[k] for k in ks]
    out = []; prev_c = by[ks[0]]["open_price"]; k = ks[0]
    while k <= ks[-1]:
        if k in by:
            out.append(by[k]); prev_c = by[k]["close_price"]
        else:
            out.append(_new(k, tf_secs, prev_c, empty=True))
        k += tf_secs
    return out


def candles_from_footprint_nodes(nodes: dict, tf_secs: int) -> list:
    """Serve the daemon's footprint nodes as gap-filled clock candles. Each node carries OHLC from the Binance kline
    framing (open/high/low/close, EXACT) + per-price-level footprint from the aggTrade path. Nodes without OHLC (kline
    not yet framed, i.e. the sub-second forming edge) are skipped. Same bucket-shaped output as build_time_candles."""
    keyed = {}
    for ut, n in (nodes or {}).items():
        try:
            t = int(ut)
        except (TypeError, ValueError):
            continue
        if "open" not in n:                                   # kline framing hasn't landed yet -> not a servable candle
            continue
        lv = n.get("levels") or {}
        bv = sum(float(v.get("b", 0.0)) for v in lv.values())
        sv = sum(float(v.get("s", 0.0)) for v in lv.values())
        keyed[t] = {"start_time": float(t), "end_time": float(t + tf_secs),
                    "open_price": float(n["open"]), "close_price": float(n["close"]),
                    "high": float(n["high"]), "low": float(n["low"]),
                    "buy_vol": bv, "sell_vol": sv, "curr_vol": bv + sv, "target_vol": 0.0,
                    "poc_price": float(n["close"]), "n_trades": 0, "empty": False,
                    "levels": {p: {"b": float(v.get("b", 0.0)), "s": float(v.get("s", 0.0))} for p, v in lv.items()}}
    if not keyed:
        return []
    ks = sorted(keyed); out = []; prev = keyed[ks[0]]["open_price"]; t = ks[0]
    while t <= ks[-1]:
        if t in keyed:
            out.append(keyed[t]); prev = keyed[t]["close_price"]
        else:
            out.append(_new(t, tf_secs, prev, empty=True))
        t += tf_secs
    return out


def parity(trades: Iterable[dict], candles: list) -> dict:
    """Exact accounting identity check. Returns honesty % (100 = perfect) + worst per-candle footprint discrepancy.
    (A) tape total == candle buy/sell totals; (B) each candle's footprint levels sum to its buy/sell totals."""
    trades = list(trades)
    tb = sum(float(d["q"]) for d in trades if not d.get("m") and float(d.get("q", 0)) > 0)
    ts = sum(float(d["q"]) for d in trades if d.get("m") and float(d.get("q", 0)) > 0)
    cb = sum(c["buy_vol"] for c in candles); cs = sum(c["sell_vol"] for c in candles)
    lb = sum(lv["b"] for c in candles for lv in c["levels"].values())
    ls = sum(lv["s"] for c in candles for lv in c["levels"].values())
    tot = (tb + ts) or 1.0
    pcmax = max((abs(sum(lv["b"] for lv in c["levels"].values()) - c["buy_vol"]) +
                 abs(sum(lv["s"] for lv in c["levels"].values()) - c["sell_vol"]))
                for c in candles if not c.get("empty")) if any(not c.get("empty") for c in candles) else 0.0
    return {"tape_buy": tb, "tape_sell": ts, "cand_buy": cb, "cand_sell": cs,
            "honesty_A": 100.0 * (1 - (abs(cb - tb) + abs(cs - ts)) / tot),
            "honesty_B": 100.0 * (1 - (abs(lb - cb) + abs(ls - cs)) / tot),
            "worst_candle_delta": pcmax}


if __name__ == "__main__":
    import os, sys, json
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app import config
    from datetime import datetime, timezone
    path = os.path.join(config.PROJECT_DIR, "data", "aggtrade_tape_20260615T174547Z.jsonl")
    trades = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("type") == "aggTrade":
                    trades.append(r["data"])
    dt = lambda s: datetime.fromtimestamp(s, tz=timezone.utc).strftime("%H:%M")
    print("self-test tape: %d aggTrades\n" % len(trades))
    for tf, secs in (("1m", 60), ("5m", 300)):
        cs = build_time_candles(trades, secs)
        p = parity(trades, cs)
        print("%s: %d candles  honesty A=%.4f%% B=%.4f%% (worst d=%.1e)" % (
            tf, len(cs), p["honesty_A"], p["honesty_B"], p["worst_candle_delta"]))
    # ---- INDUCED-GAP TEST: drop the 2nd 1m interval's trades, confirm a flat gap-filled candle appears there ----
    secs = 60; base = build_time_candles(trades, secs)
    drop_open = int(base[1]["start_time"])                                   # the interval we will empty
    kept = [d for d in trades if candle_open(int(d["T"]), secs) != drop_open]
    gapped = build_time_candles(kept, secs)
    filled = next((c for c in gapped if int(c["start_time"]) == drop_open), None)
    ok_time = filled is not None and int(filled["start_time"]) == drop_open   # right slot at the right clock time
    ok_flat = filled is not None and filled.get("empty") and filled["curr_vol"] == 0.0 and \
              filled["open_price"] == filled["close_price"]                   # flat, zero-vol
    ok_len = len(gapped) == len(base)                                        # same #slots -> gap NOT compressed
    ok_par = parity(kept, gapped)["honesty_A"] > 99.9999                     # remaining trades still 100% accounted
    print("\nINDUCED-GAP TEST (emptied %s UTC):" % dt(drop_open))
    print("  gap slot present at exact clock time : %s" % ok_time)
    print("  gap candle is flat + zero-volume     : %s" % ok_flat)
    print("  series NOT compressed (len == full)  : %s (%d==%d)" % (ok_len, len(gapped), len(base)))
    print("  remaining trades still 100%% honest   : %s" % ok_par)
    print("\nALL PASS" if all((ok_time, ok_flat, ok_len, ok_par)) else "\nFAIL")
