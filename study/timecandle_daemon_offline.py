"""OFFLINE proof of the daemon clock-candle serving (before any deploy). Drives the REAL MarketDataCore:
  1. replay Binance 1m klines through core._process_kline   -> frames OHLC on the footprint nodes
  2. replay the captured aggTrade tape through _process_aggtrade -> adds per-level footprints
  3. call core.catchup_time_candles('1m')                   -> the exact bytes the daemon would ship
Then assert: (A) served OHLC == Binance kline O/H/L/C for each COMPLETE candle, and (B) served footprint totals ==
the tape's buy/sell (100% parity). Additive + read-only: catchup_buckets and the volume-bucket engines are untouched.
python study/timecandle_daemon_offline.py"""
import os, sys, json, csv
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from app import config
from app.feeds import MarketDataCore

TAPE = os.path.join(config.PROJECT_DIR, "data", "aggtrade_tape_20260615T174547Z.jsonl")
KCSV = os.path.join(config.PROJECT_DIR, "data", "klines", "SOLUSDT-1m-2026-06-15.csv")


def main():
    trades = []
    with open(TAPE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("type") == "aggTrade":
                    trades.append(r["data"])
    t0 = int(trades[0]["T"]); t1 = int(trades[-1]["T"])
    lo_min = (t0 // 60000) * 60; hi_min = (t1 // 60000) * 60          # tape's minute span (open secs)

    klines = {}                                                       # open_sec -> kline dict (Binance ground truth)
    with open(KCSV) as f:
        for row in csv.reader(f):
            try:
                tms = int(row[0])
            except (ValueError, IndexError):
                continue
            osec = tms // 1000
            if lo_min <= osec <= hi_min:
                klines[osec] = {"t": tms, "o": float(row[1]), "h": float(row[2]), "l": float(row[3]),
                                "c": float(row[4]), "v": float(row[5]), "V": float(row[9]), "x": True}

    core = MarketDataCore(footprints_db={}, broadcast_tf=lambda *a: None,
                          broadcast_all=lambda *a: None, tf_has_subscribers=lambda _t: False)
    for osec in sorted(klines):                                       # 1) frame OHLC via the REAL kline path
        core._process_kline(klines[osec], "1m")
    for d in trades:                                                  # 2) add footprints via the REAL aggtrade path
        core._process_aggtrade(d)
    served = core.catchup_time_candles("1m")                          # 3) exactly what the daemon would ship

    hm = lambda s: datetime.fromtimestamp(s, tz=timezone.utc).strftime("%H:%M")
    tape_buy = sum(float(d["q"]) for d in trades if not d["m"])
    tape_sell = sum(float(d["q"]) for d in trades if d["m"])
    sb = sum(c["buy_vol"] for c in served); ss = sum(c["sell_vol"] for c in served)
    ohlc_ok = ohlc_tot = 0
    print("served %d clock candles from the real core:\n" % len(served), flush=True)
    print("  %-6s %-26s %-26s %s" % ("min", "served O/H/L/C", "binance O/H/L/C", "match"), flush=True)
    for c in served:
        osec = int(c["start_time"]); k = klines.get(osec)
        partial = (osec == lo_min or osec == hi_min)
        s_ohlc = (c["open_price"], c["high"], c["low"], c["close_price"])
        if k is None:
            print("  %-6s (no kline)" % hm(osec)); continue
        b_ohlc = (k["o"], k["h"], k["l"], k["c"])
        ok = all(abs(s_ohlc[i] - b_ohlc[i]) < 1e-9 for i in range(4))
        if not partial:
            ohlc_tot += 1; ohlc_ok += int(ok)
        print("  %-6s %-26s %-26s %s" % (hm(osec), "%.2f/%.2f/%.2f/%.2f" % s_ohlc, "%.2f/%.2f/%.2f/%.2f" % b_ohlc,
                                         "OK" if ok else ("partial" if partial else "MISMATCH")), flush=True)
    honB = 100.0 * (1 - (abs(sb - tape_buy) + abs(ss - tape_sell)) / (tape_buy + tape_sell))
    print("\n  (A) OHLC vs Binance (complete candles): %d/%d exact" % (ohlc_ok, ohlc_tot), flush=True)
    print("  (B) footprint totals vs tape: served %.4f/%.4f  tape %.4f/%.4f -> honesty %.4f%%" % (
        sb, ss, tape_buy, tape_sell, honB), flush=True)
    ok = ohlc_ok == ohlc_tot and ohlc_tot > 0 and honB > 99.9999
    print("\n  DAEMON SERVING VALID: %s" % ("YES — served candles are Binance-exact OHLC + tape-faithful footprint" if ok
                                            else "REVIEW"), flush=True)


if __name__ == "__main__":
    main()
