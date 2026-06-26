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
import json
from dataclasses import dataclass
from typing import Optional

from . import config, persistence
from .depth_store import DepthStore
from .feeds import MarketDataCore
from .protocol import CatchupChunkPacket


@dataclass
class _Client:
    queue: asyncio.Queue
    writer: asyncio.StreamWriter
    tf: Optional[str] = None   # no subscription until the client sends set_tf


class DaemonServer:
    def __init__(self):
        self.clients: dict[asyncio.StreamWriter, _Client] = {}
        self.store = persistence.HistoryStore()
        self.footprints_db = self.store.bootstrap()
        self.core = MarketDataCore(self.footprints_db, self.broadcast_tf, self.broadcast_all)
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
        if cmd.get("action") == "set_tf":
            tf = cmd.get("tf")
            if tf in config.TIMEFRAMES:
                client.tf = tf
                # Stream a fresh chunked CATCHUP for the newly subscribed timeframe
                asyncio.create_task(self._send_catchup(client, tf))
                print(f"CLIENT {client.writer.get_extra_info('peername')} -> {tf}")

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
