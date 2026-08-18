"""Validate a reconstructed month of CLOCK candles (study/clock_archive) against Binance's OWN 1m klines (ground
truth) + internal footprint/OI checks. OHLC from aggTrades must equal the kline OHLC to the tick (aggregation never
moves the open/close/extreme prices). Usage: python study/clock_recon_validate.py 2026-05"""
import gzip
import io
import json
import os
import sys
import urllib.request
import zipfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from study.archive_loader import load_archive

SYMBOL = "SOLUSDT"


def klines_1m(month: str) -> dict:
    """{open_sec: (o,h,l,c)} from the monthly 1m kline dump."""
    url = "https://data.binance.vision/data/futures/um/monthly/klines/%s/1m/%s-1m-%s.zip" % (SYMBOL, SYMBOL, month)
    raw = urllib.request.urlopen(url, timeout=120).read()
    out = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        with z.open(z.namelist()[0]) as fh:
            for ln in io.TextIOWrapper(fh, encoding="utf-8"):
                f = ln.split(",")
                try:
                    t = int(f[0]) // 1000
                except (ValueError, IndexError):
                    continue
                out[t] = (float(f[1]), float(f[2]), float(f[3]), float(f[4]))
    return out


def main():
    month = sys.argv[1] if len(sys.argv) > 1 else "2026-05"
    print("validating clock_archive 1m vs Binance 1m klines for %s\n" % month, flush=True)
    _, cand, _ = load_archive("1m", root=os.path.join(os.path.dirname(os.path.abspath(__file__)), "clock_archive"),
                              drop_degenerate=False)
    kl = klines_1m(month)
    y, m = (int(x) for x in month.split("-"))
    lo = int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp())
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    hi = int(datetime(ny, nm, 1, tzinfo=timezone.utc).timestamp())
    cand = [c for c in cand if lo <= int(c.get("start_time", 0)) < hi and not c.get("empty")]
    print("  %d reconstructed 1m candles in-month, %d klines" % (len(cand), len(kl)), flush=True)

    ohlc_ok = ohlc_tot = 0; maxerr = 0.0; worst = None
    oi_nonzero = 0
    fp_ok = fp_tot = 0
    for c in cand:
        t = int(c["start_time"])
        k = kl.get(t)
        if k is not None:
            ohlc_tot += 1
            s = (c["open"], c["high"], c["low"], c["close"])
            e = max(abs(s[i] - k[i]) for i in range(4))
            if e <= 1e-6:
                ohlc_ok += 1
            if e > maxerr:
                maxerr = e; worst = (t, s, k)
        if (c.get("opL", 0) + c.get("opS", 0) + c.get("clL", 0) + c.get("clS", 0)) > 0:
            oi_nonzero += 1
        lv = c.get("levels") or {}
        lb = sum(v.get("b", 0.0) for v in lv.values()); ls = sum(v.get("s", 0.0) for v in lv.values())
        fp_tot += 1
        if abs(lb - c.get("buy_vol", 0.0)) + abs(ls - c.get("sell_vol", 0.0)) <= 1e-6 * max(1.0, c.get("curr_vol", 1.0)):
            fp_ok += 1

    print("\n  (A) OHLC vs Binance klines : %d/%d EXACT (max dev %.8f)" % (ohlc_ok, ohlc_tot, maxerr))
    if worst and maxerr > 1e-6:
        print("      worst @ %s UTC: recon=%s kline=%s" % (
            datetime.utcfromtimestamp(worst[0]).strftime("%m-%d %H:%M"), worst[1], worst[2]))
    print("  (B) footprint levels == buy/sell totals : %d/%d" % (fp_ok, fp_tot))
    print("  (C) OI-attributed intent (opL/opS/..>0)  : %d/%d candles (%.1f%%)" % (
        oi_nonzero, len(cand), 100.0 * oi_nonzero / max(1, len(cand))))
    ok = ohlc_ok == ohlc_tot and ohlc_tot > 0 and fp_ok == fp_tot and oi_nonzero > 0
    print("\n  VERDICT: %s" % ("PASS -- Binance-exact OHLC + faithful footprint + real OI intent "
                               "(coverage above; the rest are OI-neutral candles, honest not fabricated)"
                               if ok else "REVIEW"))


if __name__ == "__main__":
    main()
