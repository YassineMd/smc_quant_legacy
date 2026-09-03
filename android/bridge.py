"""Tablet bridge: re-serves the daemon feed as JSON lines for the Android app.

Reuses the terminal's tested ``PipeClientWorker`` (plain thread, no Qt) exactly like the DOM/Trades
scanner modes do: a degenerate depth_window subscribe (1s x 1col x 1bin) arms the live TradeBatch
pushes, a trades_window request backfills the VP history, and the 0.4s pulses carry the book.

Two deployments, one file:
  PC (USB path):   python android/bridge.py
                   listens on 127.0.0.1:8765; tablet reaches it via `adb reverse tcp:8765 tcp:8765`.
  VM (Wi-Fi/4G):   python android/bridge.py --listen 0.0.0.0 --auth <token> --compress
                   runs next to the daemon (systemd `smcbridge`); the app connects to the VM's
                   public IP. --auth requires the client's FIRST line to be
                   {"t":"auth","k":"<token>","z":1} within 6s (else the socket closes); with
                   --compress and z=1 every server->client byte after that is ONE zlib stream
                   (Z_SYNC_FLUSH per send — java.util.zip.Inflater-friendly). Client->server
                   stays plain either way.

Wire (newline-delimited JSON, one object per line):
  -> {"t":"hello","sym":"SOLUSDT","tick":0.01,"now":<epoch_s>}
  -> {"t":"tw","n":N,"ts":b64,"px":b64,"q":b64,"sd":b64}   backfill window (int64 LE ms / f64 / f64 / u8)
  -> {"t":"tb","n":N,"ts":b64,"px":b64,"q":b64,"sd":b64}   live batch (same arrays)
  -> {"t":"book","px":<last>,"b":[[p,q]..],"a":[[p,q]..]}  ~0.4s, top-200 per side (floats)
  -> {"t":"thr","v":[p50,p90,p95,p99,p99.5]}               rolling trade-size percentiles (contracts)
  <- {"t":"fetch","t0":ms}                                  custom-VP deep-history request
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import sys
import threading
import time
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from app import config
from app.pipe_client import PipeClientWorker


def decode_trades(ts_b64: str, price_b64: str, qty_b64: str, side_b64: str):
    """app.heatmap.decode_trades, inlined: that module drags Qt imports the headless VM lacks."""
    ts = np.frombuffer(base64.b64decode(ts_b64 or ""), dtype="<i8")
    pr = np.frombuffer(base64.b64decode(price_b64 or ""), dtype="<f8")
    qt = np.frombuffer(base64.b64decode(qty_b64 or ""), dtype="<f8")
    sd = np.frombuffer(base64.b64decode(side_b64 or ""), dtype="u1")
    n = min(len(ts), len(pr), len(qt), len(sd))
    return (ts[:n].astype(np.float64), pr[:n].astype(np.float64),
            qt[:n].astype(np.float64), sd[:n].copy())

BOOK_PERIOD = 0.4
RESUB_SECS = 10.0
# getattr fallbacks: the VM runs the daemon-era config.py, which predates these terminal constants
STORE_SECS = getattr(config, "DOM_VP_BACKFILL_SECS", 21600)   # rolling in-bridge tape (6h)
RETENTION_H = getattr(config, "DEPTH_RETENTION_HOURS", 72)    # daemon trade_tape retention


def b64(a: np.ndarray) -> str:
    return base64.b64encode(a.tobytes()).decode("ascii")


class TradeStore:
    """Rolling trade history so a (re)joining tablet gets the full VP backfill from RAM instantly."""

    def __init__(self):
        self.lock = threading.Lock()
        self.keep_t0_ms = None                     # deep-fetch floor (custom VP): prune keeps >= this
        self.ts = np.empty(0, np.float64)          # epoch ms
        self.px = np.empty(0, np.float64)
        self.q = np.empty(0, np.float64)
        self.sd = np.empty(0, np.uint8)

    def add(self, ts, px, q, sd, prepend: bool = False) -> None:
        if not len(ts):
            return
        with self.lock:
            if prepend:                            # older window: cut overlap against the live store's start
                if len(self.ts):
                    keep = ts < self.ts[0]
                    ts, px, q, sd = ts[keep], px[keep], q[keep], sd[keep]
                self.ts = np.concatenate([ts, self.ts]); self.px = np.concatenate([px, self.px])
                self.q = np.concatenate([q, self.q]); self.sd = np.concatenate([sd, self.sd])
            else:                                  # live batch: drop anything at/before the store's end
                if len(self.ts):
                    keep = ts > self.ts[-1]
                    ts, px, q, sd = ts[keep], px[keep], q[keep], sd[keep]
                self.ts = np.concatenate([self.ts, ts]); self.px = np.concatenate([self.px, px])
                self.q = np.concatenate([self.q, q]); self.sd = np.concatenate([self.sd, sd])
            cut = (time.time() - STORE_SECS) * 1000.0
            if self.keep_t0_ms is not None:        # a custom VP start older than 6h keeps its data
                cut = min(cut, self.keep_t0_ms - 300_000.0)
            i = int(np.searchsorted(self.ts, cut))
            if i > 0:
                self.ts = self.ts[i:]; self.px = self.px[i:]; self.q = self.q[i:]; self.sd = self.sd[i:]

    def window_msg(self) -> str:
        with self.lock:
            n = len(self.ts)
            return json.dumps({"t": "tw", "n": n, "ts": b64(self.ts.astype("<i8")),
                               "px": b64(self.px.astype("<f8")), "q": b64(self.q.astype("<f8")),
                               "sd": b64(self.sd.astype("u1"))}) + "\n"


class Client:
    """One connected tablet: socket + optional per-client zlib stream, sends serialized by a lock."""

    def __init__(self, sock: socket.socket, compress: bool):
        self.sock = sock
        self.lock = threading.Lock()
        self.comp = zlib.compressobj() if compress else None

    def send(self, line: str) -> None:
        data = line.encode("utf-8")
        with self.lock:
            if self.comp is not None:
                data = self.comp.compress(data) + self.comp.flush(zlib.Z_SYNC_FLUSH)
            self.sock.sendall(data)


class Bridge:
    def __init__(self, listen: str, port: int, auth: str | None, compress: bool):
        self.listen = listen
        self.port = port
        self.auth = auth
        self.compress = compress
        self.worker = PipeClientWorker(lite=True)
        self.store = TradeStore()
        self.clients: list[Client] = []
        self.clients_lock = threading.Lock()
        self._last_resub = 0.0
        self._conn_was = False

    # ---- daemon side ----------------------------------------------------
    def _subscribe(self, backfill: bool) -> None:
        t1 = int(time.time() * 1000)
        self.worker.request_depth_window(t1 - 1000, t1, 1, 0.0, 1e9, 1)
        if backfill:
            t0 = t1 - int(STORE_SECS) * 1000
            with self.store.lock:                   # only re-fetch what the RAM store doesn't hold
                if len(self.store.ts):
                    t0 = max(t0, int(self.store.ts[-1]) + 1)
            self.worker.request_trades_window(t0, t1, 0.0, 1e9)
        self._last_resub = time.time()

    def deep_fetch(self, t0_ms: int) -> None:
        """Custom-VP history: fetch the tape OLDER than what the store holds, down to t0 (clamped to
        the daemon's 72h retention) — the tablet's _dom_custom_vp. The reply broadcasts as a tw."""
        t0_ms = max(int(t0_ms), int((time.time() - RETENTION_H * 3600 + 60) * 1000))
        with self.store.lock:
            self.store.keep_t0_ms = (t0_ms if self.store.keep_t0_ms is None
                                     else min(self.store.keep_t0_ms, t0_ms))
            oldest = int(self.store.ts[0]) if len(self.store.ts) else int(time.time() * 1000)
        if t0_ms < oldest - 1000:
            self.worker.request_trades_window(t0_ms, oldest - 1, 0.0, 1e9)
            print("bridge: deep fetch %d .. %d" % (t0_ms, oldest - 1), flush=True)

    def pump(self) -> None:
        """Main loop: heal subscriptions, drain trades, broadcast book + batches."""
        next_book = 0.0
        while True:
            conn = bool(self.worker.connected)
            if conn and not self._conn_was:
                self._subscribe(backfill=True)      # fresh socket -> re-arm + gap re-fetch
            self._conn_was = conn
            if conn and time.time() - self._last_resub > RESUB_SECS:
                self._subscribe(backfill=False)     # keep-alive re-arm (no-op daemon-side if armed)
            _tv, tws, batches = self.worker.trades_state()
            for tw in (tws or ()):
                ts, px, q, sd = decode_trades(tw.ts_b64, tw.price_b64, tw.qty_b64, tw.side_b64)
                prepend = bool(len(ts)) and bool(len(self.store.ts)) and ts[0] < self.store.ts[0]
                self.store.add(ts, px, q, sd, prepend=prepend)
                self.broadcast(json.dumps({"t": "tw", "n": int(len(ts)), "ts": tw.ts_b64,
                                           "px": tw.price_b64, "q": tw.qty_b64,
                                           "sd": tw.side_b64}) + "\n")
            for tb in batches:
                ts, px, q, sd = decode_trades(tb.ts_b64, tb.price_b64, tb.qty_b64, tb.side_b64)
                self.store.add(ts, px, q, sd)
                self.broadcast(json.dumps({"t": "tb", "n": int(len(ts)), "ts": tb.ts_b64,
                                           "px": tb.price_b64, "q": tb.qty_b64,
                                           "sd": tb.side_b64}) + "\n")
            now = time.time()
            if now >= next_book:
                next_book = now + BOOK_PERIOD
                bids, asks, px = self.worker.depth_book()
                if bids or asks:
                    msg = json.dumps({
                        "t": "book", "px": float(px or 0.0),
                        "b": [[float(p), float(q)] for p, q in bids],
                        "a": [[float(p), float(q)] for p, q in asks]}) + "\n"
                    self.broadcast(msg)
                with self.worker.data_lock:
                    thr = list(self.worker.size_thr)
                if thr:
                    self.broadcast(json.dumps({"t": "thr", "v": thr}) + "\n")
            time.sleep(0.1)

    # ---- tablet side ----------------------------------------------------
    def broadcast(self, line: str) -> None:
        with self.clients_lock:
            targets = list(self.clients)
        dead = []
        for c in targets:
            try:
                c.send(line)
            except OSError:
                dead.append(c)
        if dead:
            with self.clients_lock:
                for c in dead:
                    if c in self.clients:
                        self.clients.remove(c)
                    try:
                        c.sock.close()
                    except OSError:
                        pass

    def _drop(self, cl: Client) -> None:
        with self.clients_lock:
            if cl in self.clients:
                self.clients.remove(cl)
        try:
            cl.sock.close()
        except OSError:
            pass

    def _client_handler(self, sock: socket.socket, addr) -> None:
        """Per-connection: (auth handshake ->) hello + backfill -> register -> read control lines."""
        buf = b""
        compress = False
        try:
            if self.auth:
                sock.settimeout(6.0)               # unauthenticated sockets don't get to linger
                while b"\n" not in buf:
                    d = sock.recv(4096)
                    if not d or len(buf) > 4096:
                        raise OSError("auth eof")
                    buf += d
                line, buf = buf.split(b"\n", 1)
                m = json.loads(line)
                if m.get("t") != "auth" or str(m.get("k", "")) != self.auth:
                    print("bridge: bad auth from %s" % addr[0], flush=True)
                    raise OSError("bad auth")
                compress = self.compress and bool(m.get("z"))
                sock.settimeout(None)
            cl = Client(sock, compress)
            cl.send(json.dumps({"t": "hello", "sym": config.SYMBOL, "tick": config.TICK_SIZE,
                                "now": time.time()}) + "\n")
            cl.send(self.store.window_msg())        # instant full VP backfill from RAM
            with self.clients_lock:
                self.clients.append(cl)
            print("bridge: client %s connected (store %d trades%s)"
                  % (addr[0], len(self.store.ts), ", zlib" if compress else ""), flush=True)
        except (OSError, ValueError, json.JSONDecodeError):
            try:
                sock.close()
            except OSError:
                pass
            return
        try:
            while True:                             # control channel (plain text both deployments)
                d = sock.recv(4096)
                if not d:
                    break
                buf += d
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    try:
                        m = json.loads(line)
                    except ValueError:
                        continue
                    if m.get("t") == "fetch":
                        self.deep_fetch(int(m.get("t0", 0)))
        except OSError:
            pass
        self._drop(cl)

    def serve(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.listen, self.port))
        srv.listen(8)
        print("bridge: listening on %s:%d%s%s" % (self.listen, self.port,
                                                  " (auth)" if self.auth else "",
                                                  " (zlib offered)" if self.compress else ""), flush=True)
        while True:
            c, addr = srv.accept()
            c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            threading.Thread(target=self._client_handler, args=(c, addr), daemon=True).start()


def main() -> None:
    ap = argparse.ArgumentParser(description="SMC tablet feed bridge")
    ap.add_argument("--listen", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--auth", default=None, help="require this token in the client's first line")
    ap.add_argument("--compress", action="store_true", help="offer zlib downstream (client opts in)")
    args = ap.parse_args()
    br = Bridge(args.listen, args.port, args.auth, args.compress)
    br.worker.start()
    threading.Thread(target=br.serve, daemon=True).start()
    try:
        br.pump()
    except KeyboardInterrupt:
        print("bridge: bye")


if __name__ == "__main__":
    main()
