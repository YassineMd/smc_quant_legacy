"""Build a historical CLOCK-candle archive (study/clock_archive/) so the terminal can REPLAY time candles across the
pre-daemon era, matching study/recon_archive (volume buckets) in on-disk format.

WHY this exists: clock candles cannot be derived from the volume recon_archive (volume buckets span variable time and
lose intrabar order). They must be rebuilt from the raw tape. STREAMING + low-disk: one month at a time, the raw dump
is deleted right after it is processed, so peak scratch is ~one month (~250 MB) while the compact output (~260 MB for
18 months) is all that persists.

HONESTY — the recon reuses the PRODUCTION primitives, so a historical clock candle is built the SAME way as a live one:
  * aggTrades (data.binance.vision monthly) -> app.aggtrade.trade_to_tick -> exact price/qty/taker side. Everything
    volume-derived (OHLC, per-price footprint, buy/sell, buyer/seller-ER, cvd wicks, up/dn ticks, POC, size hist) is
    100% faithful to the tape.
  * metrics (5-min sum_open_interest) -> the SAME app.aggtrade.OiAttributor the daemon uses -> per-trade delta_oi ->
    real opL/opS/clL/clS. The ONE honest caveat: historical OI is 5-MINUTE resolution (vs the live daemon's 5 s poll),
    so the intent bodies are attributed from a coarser OI signal — real, just coarser. (Liquidations aren't in these
    dumps, so liq_short/liq_long are 0 for history — an honest absence, not a fabricated value.)
  * app.quant_engine.ClockEngine closes on clock boundaries; each closed candle is streamed to disk via its _on_close
    sink in the recon_replay wire format ({"bid","data":<full_snapshot>} jsonl.gz chunks).

Engines + the OiAttributor are SHARED across the whole run (one continuous stream), so candles that span a month
boundary (e.g. a 4h bar) are never split. Idempotent full build: clears clock_archive/ at start.

Usage:
  python study/clock_recon.py --month 2026-05                 # one month (validation)
  python study/clock_recon.py --start 2025-01 --end 2026-06   # full pre-daemon range
  python study/clock_recon.py --start 2026-05 --end 2026-05 --keep-raw   # keep the raw dump (debug)
"""
from __future__ import annotations

import argparse
import csv
import glob
import gzip
import io
import json
import os
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import config
from app.aggtrade import OiAttributor, median_target_vol, trade_to_tick
from app.quant_engine import ClockEngine

SYMBOL = "SOLUSDT"
BASE_ROOT = "https://data.binance.vision/data/futures/um"   # /monthly/aggTrades + /daily/metrics under here
TFS = list(config.TIMEFRAMES)                       # 1m 5m 15m 30m 1h 4h
OUT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clock_archive")
CHUNK = 10000                                       # candles per gzip chunk (recon_replay loads a chunk at a time)
_SCRATCH = os.environ.get("CLOCK_RECON_SCRATCH") or os.path.join(OUT_ROOT, "_raw")


# ----------------------------------------------------------------------------- download / parse
def _dl(url: str, dest: str, quiet: bool = False) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=120) as r, open(dest, "wb") as f:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
        return True
    except Exception as e:
        if not quiet:
            print("  download FAILED %s (%s)" % (url, e), flush=True)
        return False


def _unzip_one_csv(zip_path: str) -> "list[str]":
    """Return the CSV lines from the single-member Binance zip (header stripped)."""
    with zipfile.ZipFile(zip_path) as z:
        name = z.namelist()[0]
        with z.open(name) as fh:
            text = io.TextIOWrapper(fh, encoding="utf-8")
            lines = text.read().splitlines()
    if lines and lines[0].startswith(("agg_trade_id", "create_time")):
        lines = lines[1:]
    return lines


def _parse_metrics_zip(zip_path: str) -> "list[tuple[int, float]]":
    """(epoch_ms, sum_open_interest) at 5-min cadence from one DAILY metrics zip."""
    if not os.path.exists(zip_path):
        return []
    out = []
    for ln in _unzip_one_csv(zip_path):
        f = ln.split(",")
        # create_time, symbol, sum_open_interest, ...
        try:
            ts = int(datetime.strptime(f[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)
            oi = float(f[2])
        except (ValueError, IndexError):
            continue
        out.append((ts, oi))
    return out


def _interp_metrics(metrics: "list[tuple[int, float]]", step: int = 60000) -> "list[tuple[int, float]]":
    """LINEARLY interpolate the 5-min OI series to `step`-MILLISECOND points (timestamps are epoch ms), so the OI
    attribution has a cadence close to the live daemon's 5 s poll (the raw 5-min series front-loads all intent into the
    first minute of each window). This is a documented RECONSTRUCTION: the intra-window OI path is estimated (linear),
    not measured. Only bridges small contiguous gaps (<=15x step); a real data hole (missing day) is left as a gap."""
    if len(metrics) < 2:
        return metrics
    out = []
    for i in range(len(metrics) - 1):
        t0, o0 = metrics[i]; t1, o1 = metrics[i + 1]
        out.append((t0, o0))
        dt = t1 - t0
        if step < dt <= 15 * step:
            j = t0 + step
            while j < t1:
                out.append((j, o0 + (o1 - o0) * ((j - t0) / dt)))
                j += step
    out.append(metrics[-1])
    return out


def _days_in(month: str) -> "list[str]":
    y, m = (int(x) for x in month.split("-"))
    nm_y, nm_m = (y + 1, 1) if m == 12 else (y, m + 1)
    d0 = datetime(y, m, 1, tzinfo=timezone.utc)
    d1 = datetime(nm_y, nm_m, 1, tzinfo=timezone.utc)
    out = []; d = d0
    while d < d1:
        out.append(d.strftime("%Y-%m-%d")); d = datetime.fromtimestamp(d.timestamp() + 86400, tz=timezone.utc)
    return out


def _load_klines_1m(month: str, scratch: str) -> "dict[int, tuple]":
    """{open_sec: (o,h,l,c)} from the monthly 1m kline dump — Binance's AUTHORITATIVE OHLC. The clock candle's OHLC
    is taken from here (aggregated per interval), matching what every chart shows; aggTrades supply footprint/vol/OI.
    Binance klines exist for every minute (flat when no trades), so every real clock candle's interval is covered."""
    url = "%s/monthly/klines/%s/1m/%s-1m-%s.zip" % (BASE_ROOT, SYMBOL, SYMBOL, month)
    dz = os.path.join(scratch, "kl-%s.zip" % month)
    if not _dl(url, dz):
        return {}
    out = {}
    for ln in _unzip_one_csv(dz):
        f = ln.split(",")
        try:
            t = int(f[0]) // 1000
            out[t] = (float(f[1]), float(f[2]), float(f[3]), float(f[4]))
        except (ValueError, IndexError):
            continue
    try:
        os.remove(dz)
    except OSError:
        pass
    return out


def _override_ohlc(snap: dict, k1m: "dict[int, tuple]") -> dict:
    """Replace the clock candle's trade-derived OHLC with Binance's authoritative kline OHLC (aggregated over the
    candle's minutes): open = first minute's open, high/low = extremes, close = last minute's close. Footprint /
    volume / OI / cvd / ticks stay trade-derived. No-op if the interval's klines are missing."""
    st = int(snap["start_time"]); et = int(snap["end_time"])
    o = h = l = c = None
    t = st
    while t < et:
        k = k1m.get(t)
        if k is not None:
            if o is None:
                o = k[0]
            h = k[1] if h is None else (k[1] if k[1] > h else h)
            l = k[2] if l is None else (k[2] if k[2] < l else l)
            c = k[3]
        t += 60
    if o is not None:
        snap["open"] = o; snap["high"] = h; snap["low"] = l; snap["close"] = c
    return snap


def _load_month_metrics(month: str, scratch: str) -> "list[tuple[int, float]]":
    """5-min OI for the whole month, merged from the DAILY metrics dumps (Binance publishes metrics daily, not
    monthly). Ascending; days that 404 are skipped (that window falls back to flow-only intent)."""
    out = []
    for day in _days_in(month):
        url = "%s/daily/metrics/%s/%s-metrics-%s.zip" % (BASE_ROOT, SYMBOL, SYMBOL, day)
        dz = os.path.join(scratch, "met-%s.zip" % day)
        if _dl(url, dz, quiet=True):
            out.extend(_parse_metrics_zip(dz))
            try:
                os.remove(dz)
            except OSError:
                pass
    out.sort()
    return _interp_metrics(out, step=60000)   # linear 5-min -> 1-min (ms) so intent attributes like the live 5s cadence


# ----------------------------------------------------------------------------- per-tf archive writer
class _Writer:
    """Buffers closed candles for one tf and flushes CHUNK-sized {"bid","data"} jsonl.gz files, matching
    study/recon_archive so app.recon_replay / archive_loader read it unchanged."""

    def __init__(self, tf: str):
        self.tf = tf
        self.dir = os.path.join(OUT_ROOT, tf)
        os.makedirs(self.dir, exist_ok=True)
        self._buf: "list[dict]" = []
        self._bid = 0
        self._nchunk = 0
        self.count = 0

    def add(self, bucket) -> None:
        self._buf.append(bucket.full_snapshot())
        self.count += 1
        if len(self._buf) >= CHUNK:
            self._flush()

    def add_snapshot(self, snap: dict) -> None:
        self._buf.append(snap); self.count += 1
        if len(self._buf) >= CHUNK:
            self._flush()

    def _flush(self) -> None:
        if not self._buf:
            return
        lo = self._bid + 1
        path = os.path.join(self.dir, "%s_%d_%d.jsonl.gz" % (self.tf, lo, self._bid + len(self._buf)))
        with gzip.open(path, "wt", encoding="utf-8") as gz:
            for snap in self._buf:
                self._bid += 1
                gz.write(json.dumps({"bid": self._bid, "data": snap}, separators=(",", ":")) + "\n")
        self._nchunk += 1
        self._buf = []

    def close(self) -> None:
        self._flush()


# ----------------------------------------------------------------------------- main recon
def month_iter(start: str, end: str):
    y, m = (int(x) for x in start.split("-"))
    ey, em = (int(x) for x in end.split("-"))
    while (y, m) <= (ey, em):
        yield "%04d-%02d" % (y, m)
        m += 1
        if m > 12:
            m = 1; y += 1


def run(months: "list[str]", keep_raw: bool = False) -> None:
    os.makedirs(_SCRATCH, exist_ok=True)
    engines = {tf: ClockEngine(config.TF_SECONDS[tf], tf, cap=2000) for tf in TFS}
    writers = {tf: _Writer(tf) for tf in TFS}
    k1m: "dict[int, tuple]" = {}          # authoritative 1m kline OHLC, accumulated across months (cross-month candles)
    for tf in TFS:                        # sink: override each closed candle's OHLC with the kline before writing
        w = writers[tf]
        engines[tf]._on_close = (lambda _w: (lambda b: _w.add_snapshot(_override_ohlc(b.full_snapshot(), k1m))))(w)
    oi = OiAttributor()
    t0 = time.time(); n_trades = 0

    for mi, month in enumerate(months):
        agg_url = "%s/monthly/aggTrades/%s/%s-aggTrades-%s.zip" % (BASE_ROOT, SYMBOL, SYMBOL, month)
        agg_zip = os.path.join(_SCRATCH, "agg-%s.zip" % month)
        print("[%s] (%d/%d) downloading aggTrades + 1m klines + daily OI metrics ..." % (month, mi + 1, len(months)), flush=True)
        k1m.update(_load_klines_1m(month, _SCRATCH))       # authoritative OHLC for this month (kept for cross-month bars)
        if not _dl(agg_url, agg_zip):
            print("  SKIP %s (no aggTrades)" % month, flush=True); continue
        metrics = _load_month_metrics(month, _SCRATCH)         # 5-min OI, merged from the daily dumps
        mp = 0                                                  # metrics pointer
        ref = median_target_vol([e.target_vol for e in engines.values()])
        agg_lines = _unzip_one_csv(agg_zip)
        mt = 0
        for ln in agg_lines:
            f = ln.split(",")
            # agg_trade_id, price, quantity, first_trade_id, last_trade_id, transact_time, is_buyer_maker
            try:
                T = int(f[5])
            except (ValueError, IndexError):
                continue
            while mp < len(metrics) and metrics[mp][0] <= T:   # advance OI polls up to this trade's time
                oi.on_poll(metrics[mp][1], ref); mp += 1
            agg = {"p": f[1], "q": f[2], "m": (f[6] == "true"), "T": T}
            targs = trade_to_tick(agg)
            share = oi.on_trade(targs.vol)
            _sb = config.size_bin(targs.vol)
            for tf in TFS:
                engines[tf].process_tick(price=targs.price, vol=targs.vol, taker_buy=targs.taker_buy,
                                         delta_oi=share, footprints_dict={}, tick_time=targs.tick_time, size_bin=_sb)
            mt += 1
        n_trades += mt
        ref = median_target_vol([e.target_vol for e in engines.values()])
        el = time.time() - t0
        print("  %s: %d trades  (%d total, %.0fk/s)  closed so far: %s" % (
            month, mt, n_trades, (n_trades / el / 1000.0) if el > 0 else 0.0,
            " ".join("%s=%d" % (tf, writers[tf].count) for tf in TFS)), flush=True)
        if not keep_raw:
            try:
                os.remove(agg_zip)
            except OSError:
                pass

    # flush the still-open final candle of each tf (its live_snapshot) so the newest bar isn't lost
    for tf in TFS:
        eng = engines[tf]; ab = eng.active_bucket
        if ab.start_time is not None and ab.curr_vol > 0:
            snap = ab.live_snapshot(float(ab.start_time) + eng.tf_secs, eng.avg_velocity)
            writers[tf].add_snapshot(_override_ohlc(snap, k1m))
        writers[tf].close()
    el = time.time() - t0
    print("\nDONE: %d trades in %.0fs. clock_archive candles: %s" % (
        n_trades, el, " ".join("%s=%d" % (tf, writers[tf].count) for tf in TFS)), flush=True)
    print("  dropped_oi=%.0f resync_discarded=%.0f (attribution accounting)" % (oi.dropped_oi, oi.resync_discarded_oi))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", help="single YYYY-MM")
    ap.add_argument("--start", help="YYYY-MM inclusive")
    ap.add_argument("--end", help="YYYY-MM inclusive")
    ap.add_argument("--keep-raw", action="store_true")
    ap.add_argument("--no-clear", action="store_true", help="append to an existing clock_archive (default clears it)")
    a = ap.parse_args()
    if a.month:
        months = [a.month]
    elif a.start and a.end:
        months = list(month_iter(a.start, a.end))
    else:
        ap.error("pass --month YYYY-MM or --start YYYY-MM --end YYYY-MM")
    if not a.no_clear:
        for tf in TFS:
            for old in glob.glob(os.path.join(OUT_ROOT, tf, "%s_*.jsonl.gz" % tf)):
                os.remove(old)
    print("clock recon: %d month(s) %s -> %s" % (len(months), months[0], months[-1]), flush=True)
    run(months, keep_raw=a.keep_raw)


if __name__ == "__main__":
    main()
