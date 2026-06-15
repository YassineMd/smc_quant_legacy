"""Tier-0 IPC wire contract.

The daemon and every terminal window speak this and only this. Frames are
single-line JSON terminated by ``\\n`` (spec §1.3.1 line-buffering). Each frame
carries a ``type`` discriminator; :func:`parse_line` dispatches on it.

Design notes
------------
* Frames are plain ``dataclass`` objects -> ``dict`` -> ``json`` so the schema is
  introspectable and testable without a running socket.
* ``CATCHUP`` deliberately does NOT carry the 100-candle REST baseline. Per the
  decoupling decision in the architecture blueprint, each terminal pulls its own
  REST baseline on boot (spec §9.1.3); the daemon stays pure and ships only the
  quant state (closed buckets + order blocks) the client cannot reconstruct
  itself. This keeps Binance REST logic in exactly one place.
* Timestamps on the wire are integer Unix **seconds** (spec §2.1.2). The client
  localizes them to the host OS timezone (spec §2.2.1).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, TypedDict

# ---------------------------------------------------------------------------
# Frame type discriminators
# ---------------------------------------------------------------------------
TYPE_CATCHUP = "CATCHUP"      # legacy one-shot snapshot (back-compat; no longer emitted)
TYPE_CATCHUP_START = "CATCHUP_START"   # chunked catch-up #1: clear + bounded metadata
TYPE_CATCHUP_CHUNK = "CATCHUP_CHUNK"   # chunked catch-up #2: one bounded bucket batch
TYPE_CATCHUP_END = "CATCHUP_END"       # chunked catch-up #3: live edge + completion
TYPE_TICK = "TICK"            # high-frequency candle/bucket update (§1.3.2 #2)
TYPE_OB = "NEW_QUANT_OB"      # fresh order-block matrix on bucket close (main.py:804)
TYPE_LIQ = "LIQUIDATION"      # forced order event (main.py:661)
TYPE_PULSE = "PULSE"          # DOM depth + open interest pulse (main.py:887)

NEWLINE = "\n"


# ---------------------------------------------------------------------------
# Bucket wire schema (Phase 0 — Pure Quant scanner data pipeline)
# ---------------------------------------------------------------------------
class BucketSnapshot(TypedDict):
    """Full per-bucket payload consumed by the 10 bucket-based scanner modes.

    Every numeric is a JSON-safe ``float``. Times are Unix **seconds**. Closed
    buckets carry their finalized scalars; the live ``active_bucket`` carries the
    same schema with ``poc_price`` / ``buyer_er`` / ``seller_er`` / ``vol_mult``
    computed on the fly and ``end_time`` proxied to "now".
    """

    start_time: float    # bucket open (unix seconds)
    end_time: float      # bucket close (unix seconds; "now" for the active bucket)
    open: float          # first tick price in the bucket
    high: float          # bucket high
    low: float           # bucket low
    close: float         # last tick price in the bucket
    poc_price: float     # price row with the highest accumulated volume
    buy_vol: float       # taker buy volume
    sell_vol: float      # taker sell volume
    curr_vol: float      # total volume accumulated in the bucket
    opL: float           # open longs (4-vector)
    opS: float           # open shorts
    clL: float           # close longs
    clS: float           # close shorts
    buyer_er: float      # buyer effort/result (volume / ticks)
    seller_er: float     # seller effort/result
    vol_mult: float      # velocity ratio vs the rolling average (vel_ratio)


# ---------------------------------------------------------------------------
# Packets
# ---------------------------------------------------------------------------
@dataclass
class TickPacket:
    """Continuous high-frequency market update (up to 60Hz), tagged by timeframe.

    ``footprint`` carries the forming candle's full node (price-level b/s volumes,
    oi_open/oi_close, liquidations) so the terminal can render footprint bubbles,
    delta imbalances, icebergs and the 12-line stats live (spec §4).
    """

    tf: str
    price: float
    candle: Dict[str, Any]                # time(s), open, high, low, close, volume, taker_buy
    active_bucket: "BucketSnapshot"       # full live bucket (Phase 0): pulsing right edge
    footprint: Dict[str, Any] = field(default_factory=dict)
    is_closed: bool = False
    type: str = TYPE_TICK

    def to_line(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":")) + NEWLINE


@dataclass
class CatchupPacket:
    """Quant state snapshot dispatched per client subscription (spec §1.3.2 #1).

    ``footprints`` is {utime_str: node} for the most recent observed candles so a
    freshly subscribed window can paint historical footprints immediately.
    ``closed_buckets`` seeds the scanner's bucket array; ``active_bucket`` is the
    live right-edge bucket so the scanner has a pulsing edge from the first frame.
    """

    tf: str
    target_vol: float
    closed_buckets: List["BucketSnapshot"] = field(default_factory=list)
    active_bucket: "BucketSnapshot" = field(default_factory=dict)
    order_blocks: List[Dict[str, Any]] = field(default_factory=list)
    footprints: Dict[str, Any] = field(default_factory=dict)
    vpin: float = 0.0
    type: str = TYPE_CATCHUP

    def to_line(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":")) + NEWLINE


@dataclass
class CatchupStartPacket:
    """First frame of a chunked catch-up stream (spec §1.3.2 #1, paginated).

    Tells the client to CLEAR its catch-up arrays, then paint the bounded
    metadata — ``target_vol``, the current order-block set, recent ``footprints``
    — immediately, while the bucket chunks stream in behind it. ``total_buckets``
    lets the client show download progress. OBs (dozens) and footprints (~200)
    are bounded, so they ride here un-paginated.
    """

    tf: str
    target_vol: float
    order_blocks: List[Dict[str, Any]] = field(default_factory=list)
    footprints: Dict[str, Any] = field(default_factory=dict)
    total_buckets: int = 0
    type: str = TYPE_CATCHUP_START

    def to_line(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":")) + NEWLINE


@dataclass
class CatchupChunkPacket:
    """One bounded batch of historical closed buckets (``CATCHUP_CHUNK_SIZE`` per
    frame). ``seq`` is the 0-based chunk ordinal for client-side sanity checks;
    TCP already guarantees in-order delivery so the client simply ``extend``s.
    """

    tf: str
    seq: int
    closed_buckets: List["BucketSnapshot"] = field(default_factory=list)
    type: str = TYPE_CATCHUP_CHUNK

    def to_line(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":")) + NEWLINE


@dataclass
class CatchupEndPacket:
    """Final frame of a chunked catch-up stream: the live pulsing ``active_bucket``
    + the rolling ``vpin`` scalar. Signals the client the stream is complete and
    the scanner's right edge can go live.
    """

    tf: str
    active_bucket: "BucketSnapshot" = field(default_factory=dict)
    vpin: float = 0.0
    type: str = TYPE_CATCHUP_END

    def to_line(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":")) + NEWLINE


@dataclass
class ObPacket:
    """Recomputed order-block matrix for one timeframe, on bucket close.

    Phase 0 piggyback (Option A): ``new_buckets`` carries every bucket that closed
    on this trigger (usually 1; >1 when a single tick cascades through multiple
    ``target_vol`` boundaries). The client appends them to its closed-bucket list,
    growing the scanner's history without a separate frame type.
    """

    tf: str
    order_blocks: List[Dict[str, Any]] = field(default_factory=list)
    new_buckets: List["BucketSnapshot"] = field(default_factory=list)
    vpin: float = 0.0
    type: str = TYPE_OB

    def to_line(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":")) + NEWLINE


@dataclass
class LiquidationPacket:
    """Forced liquidation event (spec §8.4 feeds 3 & 4)."""

    side: str          # "BUY" (short liquidated) / "SELL" (long liquidated)
    price: float
    qty: float
    time: int          # Unix seconds
    type: str = TYPE_LIQ

    def to_line(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":")) + NEWLINE


@dataclass
class PulsePacket:
    """Order-book depth + open interest pulse (spec §8.1)."""

    bids: List[List[str]] = field(default_factory=list)  # [[price, qty], ...]
    asks: List[List[str]] = field(default_factory=list)
    oi: float = 0.0
    type: str = TYPE_PULSE

    def to_line(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":")) + NEWLINE


# ---------------------------------------------------------------------------
# Decode
# ---------------------------------------------------------------------------
_PARSERS = {
    TYPE_TICK: lambda d: TickPacket(
        tf=d["tf"], price=d["price"], candle=d["candle"],
        active_bucket=d["active_bucket"], footprint=d.get("footprint", {}),
        is_closed=d.get("is_closed", False),
    ),
    TYPE_CATCHUP: lambda d: CatchupPacket(
        tf=d["tf"], target_vol=d["target_vol"],
        closed_buckets=d.get("closed_buckets", []),
        active_bucket=d.get("active_bucket", {}),
        order_blocks=d.get("order_blocks", []),
        footprints=d.get("footprints", {}), vpin=d.get("vpin", 0.0),
    ),
    TYPE_CATCHUP_START: lambda d: CatchupStartPacket(
        tf=d["tf"], target_vol=d.get("target_vol", 0.0),
        order_blocks=d.get("order_blocks", []),
        footprints=d.get("footprints", {}),
        total_buckets=d.get("total_buckets", 0),
    ),
    TYPE_CATCHUP_CHUNK: lambda d: CatchupChunkPacket(
        tf=d["tf"], seq=d.get("seq", 0),
        closed_buckets=d.get("closed_buckets", []),
    ),
    TYPE_CATCHUP_END: lambda d: CatchupEndPacket(
        tf=d["tf"], active_bucket=d.get("active_bucket", {}),
        vpin=d.get("vpin", 0.0),
    ),
    TYPE_OB: lambda d: ObPacket(
        tf=d["tf"], order_blocks=d.get("order_blocks", []),
        new_buckets=d.get("new_buckets", []), vpin=d.get("vpin", 0.0),
    ),
    TYPE_LIQ: lambda d: LiquidationPacket(
        side=d["side"], price=d["price"], qty=d["qty"], time=d["time"],
    ),
    TYPE_PULSE: lambda d: PulsePacket(
        bids=d.get("bids", []), asks=d.get("asks", []), oi=d.get("oi", 0.0),
    ),
}


def parse_line(line: str) -> Optional[object]:
    """Decode one newline-stripped JSON frame into its packet dataclass.

    Returns ``None`` for blank lines or frames with an unknown/missing type, so
    a malformed frame can never crash the client receive loop.
    """
    line = line.strip()
    if not line:
        return None
    try:
        d = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    parser = _PARSERS.get(d.get("type"))
    if parser is None:
        return None
    try:
        return parser(d)
    except (KeyError, TypeError):
        return None
