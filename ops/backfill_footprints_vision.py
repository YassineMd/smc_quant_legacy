"""Deep FOOTPRINT backfill from Binance Vision daily aggTrades dumps (data.binance.vision).

WHY: the aggTrades REST endpoint is hard-restricted to the last ~2 days (error -4166, probed 2026-08-31), so
the daemon's rolling REST healer (app/aggtrade_backfill) can only cover recent gaps. The DEEP backlog — the
pre-recording-fix store where ~90% of clock candles are kline-only (OHLC, no footprint), reaching back ~139
days on 4h — is only reachable through the official Vision archives: one zip of EVERY aggTrade per UTC day
(~2-4MB/day for SOLUSDT), full history.

WHAT IT DOES: reads the daemon's clock store (READ-ONLY), finds every gap candle (traded, empty levels),
groups them into UTC days, downloads each day's dump newest-first, STREAM-bins the rows into all six tfs
(one pass, O(1) memory — the e2-small can't hold a day of trade dicts), and writes the rebuilt candles to a
SIDECAR file (data/tc_backfill_healed.json.gz). It NEVER touches the live store — the daemon merges the
sidecar at its next restart (feeds._tc_load step 1b), filling only still-empty candles and keeping official
kline OHLC. Resumable: intervals already in the sidecar are skipped; --max-days budgets a run.

Binning fidelity: same primitives as the live engine (aggtrade.candle_open + "%.2f" level keys + buyer-maker
semantics), verified in tests against time_candles.build_time_candles output exactly.

Usage (VM, alongside the running daemon):
    cd ~/OrderFlowPlatform && ./venv/bin/python -m ops.backfill_footprints_vision [--max-days 200]
    sudo systemctl restart orderflow          # merges the sidecar
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import sys
import time
import zipfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import config                                    # noqa: E402
from app.aggtrade import candle_open                      # noqa: E402
from app.time_candles import to_bucket_wire               # noqa: E402
from app.aggtrade_backfill import find_gaps               # noqa: E402

VISION = "https://data.binance.vision/data/futures/um/daily/aggTrades/{sym}/{sym}-aggTrades-{d}.zip"


def _stream_bin(rows, tf_secs_map: dict) -> dict:
    """One pass over (price, qty, is_buyer_maker, t_ms) rows -> {tf: {start:int -> wire dict}}. Replicates
    time_candles.build_time_candles field-for-field (same candle_open binning, same '%.2f' level keys, same
    m-semantics: buyer-maker True = taker SELL), but streaming: O(candles) memory, never O(trades)."""
    acc = {tf: {} for tf in tf_secs_map}
    for price, qty, maker, t_ms in rows:
        if qty <= 0.0:
            continue
        for tf, secs in tf_secs_map.items():
            k = candle_open(t_ms, secs)
            c = acc[tf].get(k)
            if c is None:
                c = acc[tf][k] = {"start_time": float(k), "end_time": float(k + secs),
                                  "open_price": price, "close_price": price, "high": price, "low": price,
                                  "buy_vol": 0.0, "sell_vol": 0.0, "curr_vol": 0.0, "target_vol": 0.0,
                                  "poc_price": price, "levels": {}, "n_trades": 0, "empty": False}
            if price > c["high"]:
                c["high"] = price
            if price < c["low"]:
                c["low"] = price
            c["close_price"] = price
            c["curr_vol"] += qty; c["n_trades"] += 1
            c["buy_vol" if not maker else "sell_vol"] += qty
            lk = "%.2f" % price
            lv = c["levels"].get(lk)
            if lv is None:
                lv = c["levels"][lk] = {"b": 0.0, "s": 0.0}
            lv["b" if not maker else "s"] += qty
    for tf in acc:                                        # POC = heaviest level, exactly like build_time_candles
        for c in acc[tf].values():
            bp, bv = None, -1.0
            for ps, lv in c["levels"].items():
                v = lv["b"] + lv["s"]
                if v > bv:
                    bv, bp = v, float(ps)
            if bp is not None:
                c["poc_price"] = bp
    return {tf: {int(c["start_time"]): to_bucket_wire(c) for c in cands.values()} for tf, cands in acc.items()}


def _day_rows(blob: bytes):
    """Yield (price, qty, maker, t_ms) from a Vision daily zip (header tolerated)."""
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        with z.open(z.namelist()[0]) as f:
            rdr = csv.reader(io.TextIOWrapper(f, encoding="utf-8"))
            for row in rdr:
                try:
                    yield float(row[1]), float(row[2]), row[6].strip().lower() == "true", int(row[5])
                except (ValueError, IndexError):
                    continue                              # header / malformed row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default=os.path.join(config.DATA_DIR, "time_candles.json.gz"))
    ap.add_argument("--out", default=os.path.join(config.DATA_DIR, "tc_backfill_healed.json.gz"))
    ap.add_argument("--max-days", type=int, default=0, help="0 = all gap days")
    ap.add_argument("--pace", type=float, default=2.0, help="seconds between day downloads")
    args = ap.parse_args()
    import requests

    with gzip.open(args.store, "rt", encoding="utf-8") as f:
        store = {tf: {int(k): v for k, v in c.items()} for tf, c in (json.load(f) or {}).items()}
    healed = {}
    if os.path.exists(args.out):
        with gzip.open(args.out, "rt", encoding="utf-8") as f:
            healed = {tf: {int(k): v for k, v in c.items()} for tf, c in (json.load(f) or {}).items()}
    now = time.time()
    gaps = {}
    for tf, secs in config.TF_SECONDS.items():
        gl = find_gaps(store.get(tf) or {}, secs, now)
        gl = [(s, e) for (s, e) in gl if s not in (healed.get(tf) or {})]     # resumable: skip already-healed
        if gl:
            gaps[tf] = gl
    days = sorted({datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
                   for tf, gl in gaps.items() for (s, e) in gl for t in (s, e - 1)}, reverse=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    days = [d for d in days if d < today]                 # today's dump doesn't exist yet (REST healer owns it)
    if args.max_days:
        days = days[:args.max_days]
    total_g = sum(len(g) for g in gaps.values())
    print(f"gaps: { {tf: len(g) for tf, g in gaps.items()} } across {len(days)} UTC days (newest first)", flush=True)

    n_done = 0; n_healed = 0; n_mismatch = 0
    for d in days:
        url = VISION.format(sym=config.SYMBOL, d=d)
        try:
            r = requests.get(url, timeout=120)
            if r.status_code != 200:
                print(f"  {d}: HTTP {r.status_code} — skipped", flush=True); continue
            reb = _stream_bin(_day_rows(r.content), dict(config.TF_SECONDS))
        except Exception as e:
            print(f"  {d}: {e} — skipped", flush=True); continue
        day_healed = 0
        for tf, gl in gaps.items():
            hd = healed.setdefault(tf, {})
            for (s, e) in gl:
                if s in hd or s not in reb.get(tf, {}):
                    continue
                w = reb[tf][s]
                old = (store.get(tf) or {}).get(s)
                if old:
                    for fld in ("open", "high", "low", "close"):   # official kline OHLC stays authoritative
                        if old.get(fld) is not None:
                            w[fld] = old[fld]
                    try:
                        ov = float(old.get("curr_vol", 0.0) or 0.0); nv = float(w.get("curr_vol", 0.0) or 0.0)
                        if ov > 0 and abs(nv - ov) > 0.05 * ov:
                            n_mismatch += 1
                    except (TypeError, ValueError):
                        pass
                hd[s] = w; day_healed += 1
        n_done += 1; n_healed += day_healed
        # save the sidecar incrementally (atomic) so a kill loses at most one day
        tmp = args.out + ".tmp"
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump({tf: {str(k): v for k, v in c.items()} for tf, c in healed.items()}, f)
        os.replace(tmp, args.out)
        print(f"  {d}: healed {day_healed}  (total {n_healed}, {n_done}/{len(days)} days)", flush=True)
        time.sleep(args.pace)
    print(f"DONE: {n_healed} candles rebuilt into {args.out} ({n_mismatch} vol-mismatch >5%).", flush=True)
    print("Restart the daemon to merge:  sudo systemctl restart orderflow", flush=True)


if __name__ == "__main__":
    main()
