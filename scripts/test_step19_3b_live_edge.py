"""Step 19.3b authoritative gate: the 150ms live-edge refresh, decoupled from trades.

Drives the REAL wired MarketDataCore with a capturing broadcast. Asserts:
  * the per-trade path (_process_aggtrade) emits NO live-edge TickPacket — the live
    edge is NOT driven by trade rate (only bucket-close ObPackets come from trades);
  * a kline caches last_candle and emits the 1/s heartbeat;
  * _broadcast_live_edge (the 150ms body) re-emits a TickPacket per tf-with-a-candle,
    carrying the FRESH forming bucket (curr_vol reflects accumulated trades);
  * the live edge is TIMER-driven: N calls -> N x (tfs) frames, independent of how
    many trades occurred; and LIVE_EDGE_SECS is sub-second.

Run:  python scripts/test_step19_3b_live_edge.py     (exit 0 = pass)
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app import config, protocol                       # noqa: E402
from app.feeds import MarketDataCore                     # noqa: E402

_FAILS: list[str] = []
_BASE_T = 1781545548673   # a realistic epoch-ms (matches the fixture's first trade)


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not cond:
        _FAILS.append(label)


def _kline(tf: str, t_ms: int) -> dict:
    return {"t": t_ms, "o": "75.10", "h": "75.20", "l": "75.00", "c": "75.15",
            "v": "1000", "V": "600", "i": tf, "x": False}


def main() -> int:
    cap: list[str] = []
    core = MarketDataCore({}, lambda tf, line: cap.append(line), lambda line: None)

    check("LIVE_EDGE_SECS is sub-second + decoupled config knob",
          0.0 < config.LIVE_EDGE_SECS < 1.0, f"{config.LIVE_EDGE_SECS}")

    # --- 150 aggTrades: per-trade path must NOT emit any live-edge TickPacket ---
    for i in range(150):
        core._process_aggtrade({"e": "aggTrade", "p": "75.10", "q": "50",
                                "m": (i % 2 == 0), "T": _BASE_T + i})
    pkts = [protocol.parse_line(l) for l in cap]
    ticks = [p for p in pkts if isinstance(p, protocol.TickPacket)]
    obs = [p for p in pkts if isinstance(p, protocol.ObPacket)]
    print(f"\nafter 150 trades: {len(ticks)} TickPackets, {len(obs)} ObPackets (closes)")
    check("per-trade path emits NO live-edge TickPacket (live edge is NOT trade-driven)",
          len(ticks) == 0, f"ticks={len(ticks)}")
    check("trades still emit close ObPackets (real pulse stays event-driven)", len(obs) > 0,
          f"obs={len(obs)}")

    # --- a kline caches last_candle + emits the 1/s heartbeat ---
    cap.clear()
    candle_open_ms = (_BASE_T // 60000) * 60000
    core._process_kline(_kline("1m", candle_open_ms), "1m", _BASE_T)
    check("kline caches last_candle for the live edge", "1m" in core.last_candle)
    hb = [protocol.parse_line(l) for l in cap]
    check("kline emits the 1/s heartbeat TickPacket", any(isinstance(p, protocol.TickPacket) for p in hb))

    # --- the 150ms body: re-emits the forming edge with the FRESH active bucket ---
    cap.clear()
    core._broadcast_live_edge()
    le = [protocol.parse_line(l) for l in cap]
    le_ticks = [p for p in le if isinstance(p, protocol.TickPacket) and p.tf == "1m"]
    check("live-edge emits a TickPacket for the tf with a cached candle", len(le_ticks) == 1,
          f"n={len(le_ticks)}")
    cv = le_ticks[0].active_bucket.get("curr_vol", 0.0) if le_ticks else 0.0
    print(f"live-edge active_bucket curr_vol = {cv}")
    check("live-edge carries the FORMING bucket (curr_vol > 0 after trades)", cv > 0.0, f"curr_vol={cv}")
    check("live-edge TickPacket is the forming edge (is_closed False)",
          le_ticks and le_ticks[0].is_closed is False)

    # --- timer-driven: N calls -> N x (tfs with a candle), independent of trades ---
    cap.clear()
    for _ in range(4):
        core._broadcast_live_edge()
    le4 = [p for p in (protocol.parse_line(l) for l in cap) if isinstance(p, protocol.TickPacket)]
    expected = 4 * len(core.last_candle)
    check("live edge is TIMER-driven: N calls -> N x (tfs), independent of trade count",
          len(le4) == expected, f"got {len(le4)} expected {expected}")

    print("\n" + "=" * 60)
    if _FAILS:
        print(f"RESULT: FAILED ({len(_FAILS)}): {_FAILS}")
        return 1
    print("RESULT: ALL ASSERTIONS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
