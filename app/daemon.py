"""Tier-2 headless data core — TCP loopback server (entry point #1).

    python -m app.daemon

Owns the client sockets and the broadcast fan-out. Market data lives in
:class:`app.feeds.MarketDataCore`; this module is the IPC transport (spec §1.3).

Concurrency model (spec §1.3.1, §1.4.1)
---------------------------------------
* ``asyncio.start_server`` on 127.0.0.1:9999.
* Each client gets a bounded ``asyncio.Queue``, a dedicated sender task, and a
  subscribed timeframe. Broadcasting is ``put_nowait`` (fire-and-forget): a
  frozen client drops its own frames on ``QueueFull`` and never stalls the core.
* TICK / NEW_QUANT_OB frames are timeframe-tagged and delivered only to clients
  subscribed to that timeframe (``broadcast_tf``); LIQUIDATION / PULSE go to all
  (``broadcast_all``).
* A client subscribes by sending ``{"action":"set_tf","tf":"5m"}``; the daemon
  switches its subscription and immediately hands it a fresh CATCHUP for that tf.
"""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from typing import Optional

from . import config, persistence
from .depth_store import DepthStore, bin_live_book
from .feeds import MarketDataCore
from .protocol import CatchupChunkPacket, DepthColumnPacket, DepthWindowPacket


@dataclass
class _Client:
    queue: asyncio.Queue
    writer: asyncio.StreamWriter
    tf: Optional[str] = None   # no subscription until the client sends set_tf
    heatmap: Optional[tuple] = None   # Phase 2a: (ylo, yhi, ybins) while the heatmap mode is open, else None


class DaemonServer:
    def __init__(self):
        self.clients: dict[asyncio.StreamWriter, _Client] = {}
        self.store = persistence.HistoryStore()
        self.footprints_db = self.store.bootstrap()
        self.core = MarketDataCore(self.footprints_db, self.broadcast_tf, self.broadcast_all,
                                   self.tf_has_subscribers)
        # Phase 1: SEPARATE depth.db store — own connection/sync/prune, decoupled from the bucket history.
        self.depth_store = DepthStore() if config.DEPTH_CAPTURE_ENABLED else None

    # ------------------------------------------------------------------
    # Broadcast fan-out (fire-and-forget)
    # ------------------------------------------------------------------
    def _enqueue(self, client: _Client, line: str) -> None:
        try:
            client.queue.put_nowait(line)
        except asyncio.QueueFull:
            pass

    def broadcast_tf(self, tf: str, line: str) -> None:
        """Deliver a timeframe-tagged frame only to subscribers of ``tf``.

        Clients with no subscription yet (``tf is None``) match nothing, so no
        frame leaks before a client picks its timeframe.
        """
        for client in list(self.clients.values()):
            if client.tf == tf:
                self._enqueue(client, line)

    def broadcast_all(self, line: str) -> None:
        """Deliver a timeframe-agnostic frame to every client."""
        for client in list(self.clients.values()):
            self._enqueue(client, line)

    def tf_has_subscribers(self, tf: str) -> bool:
        """True if any connected client is subscribed to ``tf`` — lets the core skip the heavy per-tf
        recompute/serialize for timeframes nobody is watching (the live-price-lag fix)."""
        return any(client.tf == tf for client in self.clients.values())

    # ------------------------------------------------------------------
    # Per-client lifecycle
    # ------------------------------------------------------------------
    async def _sender(self, writer: asyncio.StreamWriter, queue: asyncio.Queue) -> None:
        try:
            while True:
                line = await queue.get()
                writer.write(line.encode("utf-8"))
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        except Exception:
            pass

    async def handle_client(self, reader: asyncio.StreamReader,
                            writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        queue: asyncio.Queue = asyncio.Queue(maxsize=config.SOCKET_QUEUE_MAX)
        client = _Client(queue=queue, writer=writer, tf=None)
        self.clients[writer] = client
        print(f"CLIENT CONNECTED {peer} (total={len(self.clients)})")

        # No CATCHUP yet — the client's set_tf selects a timeframe and triggers
        # exactly one CATCHUP for it, with zero leaked ticks beforehand.
        sender_task = asyncio.create_task(self._sender(writer, queue))
        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                self._handle_control(client, raw)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            sender_task.cancel()
            self.clients.pop(writer, None)
            try:
                writer.close()
            except Exception:
                pass
            print(f"CLIENT DISCONNECTED {peer} (total={len(self.clients)})")

    def _handle_control(self, client: _Client, raw: bytes) -> None:
        try:
            cmd = json.loads(raw.decode("utf-8").strip())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        action = cmd.get("action")
        if action == "set_tf":
            tf = cmd.get("tf")
            if tf in config.TIMEFRAMES:
                client.tf = tf
                # Stream a fresh chunked CATCHUP for the newly subscribed timeframe
                asyncio.create_task(self._send_catchup(client, tf))
                print(f"CLIENT {client.writer.get_extra_info('peername')} -> {tf}")
        elif action == "depth_window" and self.depth_store is not None:
            # Phase 2a: serve a heatmap window (built OFF-loop) + subscribe this client to live columns.
            try:
                ylo = float(cmd["ylo"]); yhi = float(cmd["yhi"]); ybins = int(cmd["ybins"])
                params = (int(cmd["t0"]), int(cmd["t1"]), int(cmd["cols"]), ylo, yhi, ybins)
            except (KeyError, ValueError, TypeError):
                return
            client.heatmap = (ylo, yhi, ybins)
            asyncio.create_task(self._send_depth_window(client, params))
        elif action == "depth_window_stop":
            client.heatmap = None   # leave the heatmap mode -> stop live-column pushes

    # ------------------------------------------------------------------
    async def _send_catchup(self, client: _Client, tf: str) -> None:
        """Stream a paginated catch-up: CATCHUP_START -> N×CATCHUP_CHUNK -> END.

        ``await asyncio.sleep(0)`` between chunks yields the event loop so live
        ticks and other clients keep flowing during a large (up to 10k-bucket)
        dump. The client filters every frame by ``tf``, so a mid-stream tf switch
        can't corrupt its cache — the stale stream's frames are simply dropped.
        """
        try:
            self._enqueue(client, self.core.catchup_start(tf).to_line())
            buckets = self.core.catchup_buckets(tf)
            size = config.CATCHUP_CHUNK_SIZE
            for seq, i in enumerate(range(0, len(buckets), size)):
                self._enqueue(client, CatchupChunkPacket(
                    tf=tf, seq=seq, closed_buckets=buckets[i:i + size]).to_line())
                await asyncio.sleep(0)
            self._enqueue(client, self.core.catchup_end(tf).to_line())
        except Exception as e:
            print(f"CATCHUP SEND ERROR ({tf}): {e}")

    # ------------------------------------------------------------------
    # Phase 2a: heatmap window (off-loop build) + live-column push
    # ------------------------------------------------------------------
    async def _send_depth_window(self, client: _Client, params: tuple) -> None:
        """Build the heatmap window in an executor (the forward-walk is O(deltas), can be ~100s of ms on a
        big window) so the event loop / feeds / broadcast are NEVER blocked, then enqueue one frame."""
        loop = asyncio.get_event_loop()
        try:
            grid, bbo_bid, bbo_ask = await loop.run_in_executor(
                None, self.depth_store.build_window, *params)
        except Exception as e:
            print(f"DEPTH WINDOW BUILD ERROR: {e}")
            return
        t0, t1, cols, ylo, yhi, ybins = params
        self._enqueue(client, DepthWindowPacket(
            t0=t0, t1=t1, cols=cols, ylo=ylo, yhi=yhi, ybins=ybins,
            grid_b64=base64.b64encode(grid).decode("ascii"),
            bbo_bid=bbo_bid, bbo_ask=bbo_ask).to_line())

    async def depth_live_loop(self) -> None:
        """Push ONE live heatmap column per pulse to each heatmap-subscribed client — binned from the in-RAM
        local_ob (no depth.db). Read-only; fully separate from the bucket/close path and the capture writer."""
        while True:
            await asyncio.sleep(config.PULSE_BROADCAST_SECS)
            subs = [c for c in self.clients.values() if c.heatmap is not None]
            if not subs:
                continue
            ob = self.core.pulse_state["local_ob"]
            bids = ob.get("bids", {}); asks = ob.get("asks", {})
            import time as _t
            ts = int(_t.time() * 1000)
            for client in subs:
                ylo, yhi, ybins = client.heatmap
                col, bid, ask = bin_live_book(bids, asks, ylo, yhi, ybins)
                self._enqueue(client, DepthColumnPacket(
                    ts=ts, ylo=ylo, yhi=yhi, ybins=ybins,
                    col_b64=base64.b64encode(col).decode("ascii"), bid=bid, ask=ask).to_line())

    # ------------------------------------------------------------------
    async def serve(self) -> None:
        self.store.rehydrate_engines(self.core.engines, self.footprints_db)

        server = await asyncio.start_server(
            self.handle_client, host=config.IPC_HOST, port=config.IPC_PORT
        )
        addr = ", ".join(str(s.getsockname()) for s in server.sockets)
        print(f"DAEMON LISTENING ON {addr} - streaming {', '.join(config.TIMEFRAMES)}")

        self.core.start_tasks()
        asyncio.create_task(self.store.sync_loop(self.core))
        if self.depth_store is not None:   # Phase 1: depth/trade rolling-window writer, its own loop + db
            asyncio.create_task(self.depth_store.sync_loop(self.core))
            asyncio.create_task(self.depth_live_loop())   # Phase 2a: live heatmap columns to subscribers

        async with server:
            await server.serve_forever()


def main() -> None:
    config.ensure_data_dir()
    server = DaemonServer()
    try:
        asyncio.run(server.serve())
    except KeyboardInterrupt:
        print("\nDAEMON SHUTDOWN")
    finally:
        server.store.close(server.core)
        if server.depth_store is not None:
            server.depth_store.close()
        print("HISTORY DB FLUSHED + CLOSED.")


if __name__ == "__main__":
    main()
