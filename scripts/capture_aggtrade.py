#!/usr/bin/env python3
"""19.0 - aggTrade capture harness (Phase 5, THROWAWAY recorder).

Standalone. NOT imported by the app and it touches no app state. Connects
directly to Binance USD-M futures (same URL scheme `feeds.py` uses) and records
ONE time-ordered JSONL tape of:

  * solusdt@aggTrade    every aggressor trade      (a, p, q, m, T, E)
  * solusdt@kline_{tf}  all 5 tf candle frames     (OHLC framing + vol reconcile)
  * openInterest        REST poll every OI_POLL_SECS (for the 19.2 OI-bleed gate)

so the gates below it -- 19.1 (trade->args), 19.2 (OI attribution, normal case),
19.3 (hot-path latency replay), 19.6 (kline-vs-aggTrade side-by-side) -- are
DETERMINISTIC REPLAYS OF REAL TAPE rather than hand-built sequences. This is the
first real raw-tick fixture this project has had (the kline path never stored
frame deltas, only accumulated levels).

Each tape line is one JSON object:
    {"type": "aggTrade"|"kline"|"oi", "recv": <local_epoch_s>, "data": {...}}
with the exchange's raw payload preserved verbatim under "data" (replay-faithful).

Usage:
    python scripts/capture_aggtrade.py [seconds] [out_path]

Defaults: 300 s -> data/aggtrade_tape_<UTC>.jsonl. Prints fixture stats on exit.
Read-only on the network; writes exactly one file inside the project data dir.
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import requests          # noqa: E402  (project dependency, also used by app.feeds)
import websockets        # noqa: E402

from app import config   # noqa: E402  (tier-0 constants only: urls, symbol, tfs)

_SYM = config.SYMBOL.lower()


def _combined_url() -> str:
    """aggTrade + all 5 kline tfs on one combined socket (feeds.py URL scheme)."""
    streams = [f"{_SYM}@aggTrade"] + [f"{_SYM}@kline_{tf}" for tf in config.TIMEFRAMES]
    return "wss://fstream.binance.com/market/stream?streams=" + "/".join(streams)


def _write(out, etype: str, data: dict, counters: Counter) -> None:
    out.write(json.dumps({"type": etype, "recv": time.time(), "data": data},
                         separators=(",", ":")) + "\n")
    counters[etype] += 1


async def _ws_task(out, counters: Counter, stop: asyncio.Event) -> None:
    url = _combined_url()
    while not stop.is_set():
        try:
            async with websockets.connect(url, ping_interval=20, max_queue=None) as ws:
                counters["ws_connects"] += 1
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue   # also the wind-down check: re-tests stop every <=1s
                    payload = json.loads(raw)
                    payload = payload.get("data", payload)
                    etype = payload.get("e")
                    if etype == "aggTrade":
                        _write(out, "aggTrade", payload, counters)
                    elif etype == "kline":
                        _write(out, "kline", payload, counters)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            counters["ws_errors"] += 1
            counters["last_ws_error"] = repr(e)   # surfaced in stats if nothing arrives
            await asyncio.sleep(1.0)


async def _oi_task(out, counters: Counter, stop: asyncio.Event) -> None:
    loop = asyncio.get_event_loop()
    sess = requests.Session()

    def _poll():
        r = sess.get(config.REST_OPEN_INTEREST, timeout=3)
        return r.json() if r.status_code == 200 else None

    while not stop.is_set():
        try:
            data = await loop.run_in_executor(None, _poll)   # off-loop: don't stall recv
            if data:
                _write(out, "oi", data, counters)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            counters["oi_errors"] += 1
            counters["last_oi_error"] = repr(e)
        for _ in range(int(config.OI_POLL_SECS * 10)):   # responsive sleep
            if stop.is_set():
                break
            await asyncio.sleep(0.1)


async def _run(seconds: float, out_path: str) -> Counter:
    counters: Counter = Counter()
    stop = asyncio.Event()
    with open(out_path, "w", encoding="utf-8") as out:
        tasks = [asyncio.create_task(_ws_task(out, counters, stop)),
                 asyncio.create_task(_oi_task(out, counters, stop))]
        await asyncio.sleep(seconds)            # capture window (tasks run concurrently)
        stop.set()                              # ask tasks to wind down (recv timeout <=1s)
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), timeout=4.0)
        except asyncio.TimeoutError:            # straggler (mid-connect): cancel fallback
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0.25)               # let SSL transports finish closing (Win)
        out.flush()
    return counters


def _print_stats(path: str, counters: Counter) -> None:
    by_type: Counter = Counter()
    trades = []                      # (T_ms, price, qty, is_sell)
    kline_by_tf: Counter = Counter()
    ois = []                         # oi float, in arrival order
    n_lines = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            n_lines += 1
            try:
                rec = json.loads(line)
            except Exception:
                continue
            t, d = rec.get("type"), rec.get("data", {})
            by_type[t] += 1
            if t == "aggTrade":
                try:
                    trades.append((int(d["T"]), float(d["p"]), float(d["q"]), bool(d["m"])))
                except Exception:
                    pass
            elif t == "kline":
                kline_by_tf[d.get("k", {}).get("i", "?")] += 1
            elif t == "oi":
                try:
                    ois.append(float(d.get("openInterest", 0.0)))
                except Exception:
                    pass

    size = os.path.getsize(path)
    print("=" * 66)
    print(f"FIXTURE  {path}")
    print(f"  size {size / 1e6:.2f} MB   lines {n_lines}   "
          f"types: " + ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())))
    if not trades:
        print("  !! NO aggTrade ROWS CAPTURED -- likely no network egress to Binance.")
        if counters.get("last_ws_error"):
            print(f"     last ws error: {counters['last_ws_error']}")
        print("=" * 66)
        return

    Ts = [t[0] for t in trades]
    span = (max(Ts) - min(Ts)) / 1000.0
    prices = [t[1] for t in trades]
    qtys = [t[2] for t in trades]
    sells = [t for t in trades if t[3]]
    buys = [t for t in trades if not t[3]]
    buy_vol = sum(t[2] for t in buys)
    sell_vol = sum(t[2] for t in sells)
    persec = Counter(t // 1000 for t in Ts)
    peak = max(persec.values())
    mean_rate = sum(persec.values()) / len(persec)
    levels = {f"{t[1]:.2f}" for t in trades}
    tot_vol = buy_vol + sell_vol

    print(f"  aggTrade  {len(trades)} trades over {span:.1f}s "
          f"({min(Ts) // 1000} .. {max(Ts) // 1000} epoch)")
    print(f"    price   {min(prices):.2f} .. {max(prices):.2f}    "
          f"distinct tick-levels {len(levels)}  (fidelity preview)")
    print(f"    qty     min {min(qtys):.3f}  median {statistics.median(qtys):.3f}  "
          f"max {max(qtys):.3f}")
    print(f"    side    buys {len(buys)} (vol {buy_vol:.1f}) / "
          f"sells {len(sells)} (vol {sell_vol:.1f})   total vol {tot_vol:.1f}")
    print(f"    rate    peak {peak} tr/s, mean {mean_rate:.1f} tr/s   "
          f"<- 19.3 per-message latency-gate target")
    print(f"    ~closes per engine at DEFAULT_TARGET_VOL={config.DEFAULT_TARGET_VOL:.0f}: "
          f"~{tot_vol / config.DEFAULT_TARGET_VOL:.1f}")
    if kline_by_tf:
        print(f"  kline     " + ", ".join(
            f"{tf}={kline_by_tf[tf]}" for tf in config.TIMEFRAMES if tf in kline_by_tf))
    if ois:
        rises = sum(1 for a, b in zip(ois, ois[1:]) if b > a)
        falls = sum(1 for a, b in zip(ois, ois[1:]) if b < a)
        print(f"  OI        {len(ois)} polls   {min(ois):.0f} .. {max(ois):.0f} "
              f"(range {max(ois) - min(ois):.0f})   rises {rises} / falls {falls}")
    print("=" * 66)


def main() -> None:
    # Windows ProactorEventLoop has a known SSL-transport __del__ bug that prints a
    # noisy "Event loop is closed" traceback AFTER an otherwise-clean run. The
    # selector loop has no such bug and is plenty for a couple of client sockets.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 300.0
    if len(sys.argv) > 2:
        out_path = sys.argv[2]
    else:
        config.ensure_data_dir()
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        out_path = os.path.join(config.DATA_DIR, f"aggtrade_tape_{stamp}.jsonl")

    print(f"CAPTURE  {seconds:.0f}s -> {out_path}")
    print(f"  streams: {_SYM}@aggTrade + kline_{{{','.join(config.TIMEFRAMES)}}} "
          f"+ OI poll/{config.OI_POLL_SECS}s")
    counters = asyncio.run(_run(seconds, out_path))
    _print_stats(out_path, counters)


if __name__ == "__main__":
    main()
