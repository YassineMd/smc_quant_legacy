# -*- coding: utf-8 -*-
"""HLH Volume Profile -- the CANDLE FEED: Binance REST klines on a background thread.

Why REST and not the daemon: the day profile needs two full days of 1-minute candles (2,880), the week profile up
to a week of 5-minute ones (2,016). The daemon's clock-candle store serves at most 2,000 per request and the
terminal's own time feed keeps 1,000, so neither reaches. Binance's public klines endpoint does, in three
requests, with the volume in CONTRACTS -- exactly the series TradingView plots for this symbol, i.e. what the
user validated the indicator against. The terminal already uses this endpoint (time_feed._heal_from_klines).

Cost: one page of 1,500 rows is ~150 KB and weight 5 (the futures limit is 2,400/min); after the first pull the
thread polls `limit=5` (weight 1) every HLH_POLL_SECS for the forming candle. Nothing here touches Qt; the GUI
thread calls snapshot(), which rebuilds its arrays only when `rev` moved.
"""
from __future__ import annotations

import threading
import time
from typing import Dict, Optional, Tuple

import numpy as np
import requests

from . import config
from .hlh_profile import Candles

_PAGE = 1500                          # rows per request (the endpoint's maximum)
_BACKOFF_MAX = 60.0


class HlhFeed:
    def __init__(self, tf: str, start_ts: float, poll_secs: float = 10.0, symbol: Optional[str] = None):
        self.tf = str(tf)
        self.tf_secs = int(config.TF_SECONDS.get(self.tf, 60))
        self.symbol = symbol or config.SYMBOL
        self.poll_secs = float(poll_secs)
        self._start = float(start_ts)
        self._lock = threading.Lock()
        self._cands: Dict[int, Tuple[float, float, float, float, float]] = {}   # start -> (o, h, l, c, v)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.rev = 0
        self.loaded = False           # the first full pull landed
        self.error: Optional[str] = None
        self.last_ok = 0.0
        self.requests = 0
        self._snap: Tuple[int, Optional[Candles]] = (-1, None)
        self._keys: Optional[np.ndarray] = None       # the dict as sorted arrays (start, [o h l c v]) ...
        self._rows: Optional[np.ndarray] = None
        self._full = True                             # ... rebuilt from the dict when a key was added / removed,
        self._upd: Dict[int, Tuple[float, float, float, float, float]] = {}   # else patched in place from these

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="hlh-feed-%s" % self.tf, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def set_start(self, start_ts: float) -> None:
        """Move the history floor. Earlier -> the thread pulls the missing prefix; later -> old rows are pruned."""
        start_ts = float(start_ts)
        with self._lock:
            moved_back = start_ts < self._start - 1.0
            self._start = start_ts
            if not moved_back:
                # prune from the front (a day rolled over)
                drop = [k for k in self._cands if k < start_ts]
                for k in drop:
                    del self._cands[k]
                if drop:
                    self._full = True
                    self.rev += 1
        if moved_back:
            self.loaded = False
            self._wake.set()

    @property
    def start_ts(self) -> float:
        return self._start

    # ------------------------------------------------------------------ reads (GUI thread)
    def snapshot(self) -> Optional[Candles]:
        """The retained candles as sorted arrays (t, h, l, c, v, m). Rebuilt only when rev moved; the forming
        candle is included (the Pine's forming period includes the forming bar). A poll that only changed
        candles (the usual case: the forming one and a late correction) patches the arrays in place; a NEW candle
        (once per timeframe step) or a pruned floor rebuilds them from the dict -- 7,200 candles sorted into
        arrays is ~4 ms of Python, the in-place patch ~0.1 ms."""
        if self._snap[0] == self.rev:
            return self._snap[1]
        with self._lock:
            rev = self.rev
            if not self._cands:
                self._snap = (rev, None)
                self._keys = self._rows = None
                self._full = True
                self._upd = {}
                return None
            if self._full or self._keys is None or self._rows is None:
                keys = sorted(self._cands)
                self._keys = np.asarray(keys, dtype=np.float64)
                self._rows = np.asarray([self._cands[k] for k in keys], dtype=np.float64)
                self._full = False
            elif self._upd:
                ks = self._keys
                for st, row in self._upd.items():
                    i = int(np.searchsorted(ks, float(st)))
                    if i < ks.shape[0] and ks[i] == float(st):
                        self._rows[i] = row
            self._upd = {}
            t = self._keys.copy()
            arr = self._rows.copy()
        # The profile still spreads over high..low and tests the close; the OPEN rides along because the
        # POC-run highlight needs open AND close on one side of the bloc's POC (2026-09-16).
        cd = Candles(t, arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4], np.full(t.shape[0], self.tf_secs / 60.0),
                     o=arr[:, 0])
        self._snap = (rev, cd)
        return cd

    def last_closed_start(self) -> Optional[float]:
        with self._lock:
            if not self._cands:
                return None
            k = max(self._cands)
        now = time.time()
        return float(k if k + self.tf_secs <= now else k - self.tf_secs)

    # ------------------------------------------------------------------ the thread
    def _fetch(self, start_ms: int, end_ms: Optional[int], limit: int):
        params = {"symbol": self.symbol, "interval": self.tf, "limit": int(limit), "startTime": int(start_ms)}
        if end_ms is not None:
            params["endTime"] = int(end_ms)
        self.requests += 1
        res = requests.get(config.REST_KLINES, params=params, timeout=8)
        res.raise_for_status()
        return res.json()

    def _merge(self, rows) -> int:
        n = 0
        with self._lock:
            floor = self._start
            for k in rows:
                try:
                    st = int(k[0]) // 1000
                    if st < floor:
                        continue
                    o, h, l, c, v = float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])
                except (TypeError, ValueError, IndexError):
                    continue
                cur = self._cands.get(st)
                new = (o, h, l, c, v)
                if cur != new:
                    self._cands[st] = new
                    if cur is None:
                        self._full = True             # a new candle: the arrays are rebuilt
                    else:
                        self._upd[st] = new           # a changed one: patched in place
                    n += 1
            if n:
                self.rev += 1
        return n

    def _pull_full(self) -> bool:
        """Page from the floor to now. Returns True when the pull reached the present."""
        with self._lock:
            cur = float(self._start)
        now_ms = int(time.time() * 1000)
        step = self.tf_secs * 1000
        while not self._stop.is_set():
            rows = self._fetch(int(cur * 1000), None, _PAGE)
            if not rows:
                return True
            self._merge(rows)
            last_ms = int(rows[-1][0])
            if len(rows) < _PAGE or last_ms + step >= now_ms:
                return True
            cur = (last_ms + step) / 1000.0
        return False

    def _run(self) -> None:
        backoff = 2.0
        while not self._stop.is_set():
            try:
                if not self.loaded:
                    if self._pull_full():
                        self.loaded = True
                else:
                    # the forming candle + the last closed ones (a late close correction lands too)
                    with self._lock:
                        k = max(self._cands) if self._cands else None
                    if k is None:
                        self.loaded = False
                        continue
                    self._merge(self._fetch((int(k) - 2 * self.tf_secs) * 1000, None, 5))
                self.error = None
                self.last_ok = time.time()
                backoff = 2.0
                self._wake.wait(self.poll_secs)
                self._wake.clear()
            except Exception as ex:                          # network / HTTP / JSON -- keep what we have, retry
                self.error = "%s: %s" % (type(ex).__name__, str(ex)[:80])
                self._wake.wait(backoff)
                self._wake.clear()
                backoff = min(_BACKOFF_MAX, backoff * 2.0)
