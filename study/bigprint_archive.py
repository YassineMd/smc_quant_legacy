"""BIG-PRINT ARCHIVE — every SOLUSDT aggTrade print >= config.BIGPLAYER_STORE_FLOOR_USD (price x qty),
one gz-jsonl per month, from the Binance public dumps (data.binance.vision). The REPLAY source for the
Big Player Levels overlay: the daemon keeps only 72h of raw tape, and the bucket archives hold
aggregated footprints, not prints. Output is tiny (a few thousand prints/month).

  monthly dumps for completed months; DAILY dumps for the current month (up to yesterday, UTC).
  Idempotent: an existing month file is skipped unless --force (the current month is always rebuilt).
  Rows: {"t": epoch_ms, "p": price, "q": qty, "u": usd, "s": 1 taker-buy / 0 taker-sell}, sorted by t.

python study/bigprint_archive.py --start 2025-01 --end 2026-09
"""
import argparse
import gzip
import io
import json
import os
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import config

SYMBOL = "SOLUSDT"
BASE = "https://data.binance.vision/data/futures/um"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bigprint_archive")
FLOOR = float(config.BIGPLAYER_STORE_FLOOR_USD)


def month_path(month: str) -> str:
    return os.path.join(OUT, "%s-bigprints-%s.jsonl.gz" % (SYMBOL, month))


def _dl(url: str, dest: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=180) as r, open(dest, "wb") as f:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
        return True
    except Exception as e:
        code = getattr(e, "code", None)
        if code != 404:
            print("  download FAILED %s (%s)" % (url, e), flush=True)
        return False


def scan_zip(path: str):
    """Stream the single-member Binance CSV; yield big prints only (never materialize the month)."""
    with zipfile.ZipFile(path) as z:
        name = z.namelist()[0]
        with z.open(name) as fh:
            for ln in io.TextIOWrapper(fh, encoding="utf-8"):
                f = ln.rstrip("\n").split(",")
                # agg_trade_id, price, quantity, first_trade_id, last_trade_id, transact_time, is_buyer_maker
                try:
                    p = float(f[1]); q = float(f[2]); T = int(f[5])
                except (ValueError, IndexError):
                    continue                             # header / malformed line
                u = p * q
                if u >= FLOOR:
                    yield {"t": T, "p": p, "q": q, "u": round(u, 2), "s": 0 if f[6].strip() == "true" else 1}


def _write(month: str, rows: list) -> None:
    rows.sort(key=lambda r: r["t"])
    os.makedirs(OUT, exist_ok=True)
    tmp = month_path(month) + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as g:
        for r in rows:
            g.write(json.dumps(r, separators=(",", ":")) + "\n")
    os.replace(tmp, month_path(month))


def build_month(month: str, scratch: str) -> int:
    url = "%s/monthly/aggTrades/%s/%s-aggTrades-%s.zip" % (BASE, SYMBOL, SYMBOL, month)
    zp = os.path.join(scratch, "agg-%s.zip" % month)
    if not _dl(url, zp):
        return -1
    rows = list(scan_zip(zp))
    os.remove(zp)
    _write(month, rows)
    return len(rows)


def build_current_month(month: str, scratch: str) -> int:
    """DAILY dumps, day 1 .. yesterday (UTC), merged into the month file (rebuilt each run)."""
    y, m = int(month[:4]), int(month[5:7])
    today = datetime.now(timezone.utc).date()
    d = datetime(y, m, 1, tzinfo=timezone.utc).date()
    rows = []
    while d < today and d.month == m:
        url = "%s/daily/aggTrades/%s/%s-aggTrades-%s.zip" % (BASE, SYMBOL, SYMBOL, d.isoformat())
        zp = os.path.join(scratch, "agg-%s.zip" % d.isoformat())
        if _dl(url, zp):
            rows.extend(scan_zip(zp))
            os.remove(zp)
        else:
            print("  (no daily dump for %s yet)" % d, flush=True)
        d += timedelta(days=1)
    _write(month, rows)
    return len(rows)


def months(start: str, end: str):
    y, m = int(start[:4]), int(start[5:7])
    while "%04d-%02d" % (y, m) <= end:
        yield "%04d-%02d" % (y, m)
        m += 1
        if m > 12:
            m = 1; y += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    scratch = os.path.join(OUT, "_raw")
    os.makedirs(scratch, exist_ok=True)
    cur = datetime.now(timezone.utc).strftime("%Y-%m")
    t0 = time.time()
    for mo in months(a.start, a.end):
        if mo > cur:
            break
        if mo != cur and os.path.exists(month_path(mo)) and not a.force:
            print("[%s] exists, skip" % mo, flush=True); continue
        print("[%s] building ..." % mo, flush=True)
        n = build_current_month(mo, scratch) if mo == cur else build_month(mo, scratch)
        if n < 0:
            print("  [%s] no monthly dump (skipped)" % mo, flush=True)
        else:
            print("  [%s] %d big prints (>= $%.0fK)  %.0fs" % (mo, n, FLOOR / 1000, time.time() - t0), flush=True)
    print("DONE in %.0fs -> %s" % (time.time() - t0, OUT), flush=True)


if __name__ == "__main__":
    main()
