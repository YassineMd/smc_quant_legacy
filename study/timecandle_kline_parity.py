"""TIME-CANDLE OHLC ground-truth check: my aggTrade-reconstructed clock candles vs Binance's OWN 1m klines.

Volume/footprint parity (app.time_candles self-test) proves nothing is lost; THIS proves the OHLC is honest against an
independent source — Binance's official kline for the same minute. A candle whose whole minute is inside the captured
tape must match Binance O/H/L/C to the tick; an EDGE candle (the capture starts/ends mid-minute) legitimately differs
because it saw only part of the minute -> flagged 'partial', not a failure. Downloads the kline zip if absent.
python study/timecandle_kline_parity.py"""
import os, sys, io, csv, json, zipfile, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from app import config
from app.time_candles import build_time_candles

TAPE = os.path.join(config.PROJECT_DIR, "data", "aggtrade_tape_20260615T174547Z.jsonl")
KDIR = os.path.join(config.PROJECT_DIR, "data", "klines")


def ensure_kline_csv(date_str):
    csvp = os.path.join(KDIR, "SOLUSDT-1m-%s.csv" % date_str)
    if os.path.exists(csvp):
        return csvp
    os.makedirs(KDIR, exist_ok=True)
    url = config.DEPTH_HIST_URL.format(tf="1m", date=date_str)
    print("downloading %s ..." % url, flush=True)
    with urllib.request.urlopen(url, timeout=60) as r:
        zf = zipfile.ZipFile(io.BytesIO(r.read()))
    zf.extractall(KDIR)
    return csvp


def load_klines(csvp):
    out = {}
    with open(csvp) as f:
        for row in csv.reader(f):
            try:
                t = int(row[0])
            except (ValueError, IndexError):
                continue
            out[t // 1000] = (float(row[1]), float(row[2]), float(row[3]), float(row[4]))
    return out


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
    candles = build_time_candles(trades, 60)
    date_str = datetime.fromtimestamp(candles[0]["start_time"], tz=timezone.utc).strftime("%Y-%m-%d")
    kl = load_klines(ensure_kline_csv(date_str))
    first_min = candles[0]["start_time"]; last_min = candles[-1]["start_time"]
    hm = lambda s: datetime.fromtimestamp(s, tz=timezone.utc).strftime("%H:%M")
    print("\n  %-6s %-4s %-26s %-26s %s" % ("min", "kind", "mine (O/H/L/C)", "binance (O/H/L/C)", "match"), flush=True)
    exact = complete = 0
    for c in candles:
        t = int(c["start_time"]); k = kl.get(t)
        partial = (t == int(first_min) or t == int(last_min))       # capture edges = incomplete minute
        mine = (c["open_price"], c["high"], c["low"], c["close_price"])
        if k is None:
            print("  %-6s %-4s  (no kline)" % (hm(t), "?")); continue
        ok = all(abs(mine[i] - k[i]) < 1e-9 for i in range(4))
        if not partial:
            complete += 1; exact += int(ok)
        tag = "OK" if ok else ("partial (capture edge)" if partial else "MISMATCH")
        print("  %-6s %-4s %-26s %-26s %s" % (
            hm(t), "edge" if partial else "full",
            "%.2f/%.2f/%.2f/%.2f" % mine, "%.2f/%.2f/%.2f/%.2f" % k, tag), flush=True)
    print("\n  COMPLETE candles matching Binance exactly: %d / %d" % (exact, complete), flush=True)
    print("  VERDICT: %s" % ("OHLC HONEST — every complete candle == Binance to the tick"
                             if exact == complete and complete > 0 else "REVIEW — a complete candle diverged"), flush=True)


if __name__ == "__main__":
    main()
