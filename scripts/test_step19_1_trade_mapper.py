"""Step 19.1 authoritative gate: the pure aggTrade -> process_tick-args mapper.

Two layers (rule 0.7):
  [1] HAND-BUILT known vectors -> assert the EXACT TickArgs. History-independent,
      the authoritative proof that the m->side flip and tick_time=T/1000 are exact.
  [2] DETERMINISTIC REPLAY of the FROZEN 19.0 tape (scripts/fixtures/
      aggtrade_tape.jsonl): over every real aggTrade, assert the DISCRIMINATING
      property -- the taker split is per-trade EXACT (taker_buy in {0, vol}; b_ratio
      in {0,1}), NOT a kline-aggregate fraction -- plus sub-second tick_time
      resolution and aggregate side reconciliation. Frozen-tape shape is pinned
      (n / buy / sell counts) so a drifted fixture fails loudly.

Run:
    python scripts/test_step19_1_trade_mapper.py
Exit 0 = all assertions passed.
"""

from __future__ import annotations

import json
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.aggtrade import TickArgs, trade_to_tick   # noqa: E402

_FIX = os.path.join(_ROOT, "scripts", "fixtures", "aggtrade_tape.jsonl")
_FAILS: list[str] = []

# Frozen-tape anchors (capture 20260615T174547Z; immutable per fixtures/README).
_EXP_TRADES, _EXP_BUYS, _EXP_SELLS = 904, 555, 349


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not cond:
        _FAILS.append(label)


def approx(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol


def main() -> int:
    # ===================================================================
    # [1] HAND-BUILT known vectors (authoritative, tape-independent)
    # ===================================================================
    buy = trade_to_tick({"p": "100.50", "q": "2.0", "m": False, "T": 1500})
    sell = trade_to_tick({"p": "100.50", "q": "2.0", "m": True, "T": 1500})
    print(f"\n[1] known vectors:  buy={buy}  sell={sell}")
    check("buy: exact TickArgs(price, vol, taker_buy=vol, tick_time=T/1000)",
          buy == TickArgs(100.5, 2.0, 2.0, 1.5), f"{buy}")
    check("sell: same trade with m=True flips taker_buy to 0 (seller is aggressor)",
          sell == TickArgs(100.5, 2.0, 0.0, 1.5), f"{sell}")
    check("buy b_ratio == 1.0 exactly (taker_buy is ASSIGNED vol, not computed)",
          buy.taker_buy == buy.vol)
    check("sell b_ratio == 0.0 exactly",
          sell.taker_buy == 0.0)
    # sub-second / ms precision + tiny qty edge
    edge = trade_to_tick({"p": "75.13", "q": "0.01", "m": False, "T": 1781545548673})
    check("ms precision: tick_time == T/1000 with sub-second fraction",
          approx(edge.tick_time, 1781545548.673) and edge.tick_time != math.floor(edge.tick_time),
          f"tick_time={edge.tick_time}")
    check("tiny qty maps verbatim (no rounding)", edge.vol == 0.01 and edge.taker_buy == 0.01)

    # ===================================================================
    # [2] DETERMINISTIC REPLAY of the frozen tape
    # ===================================================================
    if not os.path.exists(_FIX):
        check("frozen fixture present", False, _FIX)
        print("\n" + "=" * 60 + "\nRESULT: FAILED (missing fixture)")
        return 1

    n = buys = sells = 0
    bad_split = bad_tt = nonpos = subsec = 0
    buy_vol = sell_vol = total_taker_buy = 0.0
    prev_T = -1
    nonmono = 0
    with open(_FIX, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("type") != "aggTrade":
                continue
            d = rec["data"]
            ta = trade_to_tick(d)
            n += 1
            # discriminating property: per-trade EXACT split (not a fraction)
            if not (ta.taker_buy == 0.0 or ta.taker_buy == ta.vol):
                bad_split += 1
            # event-time clock: exact T/1000
            if not approx(ta.tick_time, float(d["T"]) / 1000.0):
                bad_tt += 1
            if ta.tick_time != math.floor(ta.tick_time):
                subsec += 1
            if ta.vol <= 0:
                nonpos += 1
            T = int(d["T"])
            if T < prev_T:
                nonmono += 1
            prev_T = T
            total_taker_buy += ta.taker_buy
            if d["m"]:
                sells += 1
                sell_vol += ta.vol
            else:
                buys += 1
                buy_vol += ta.vol

    print(f"\n[2] tape replay:  {n} aggTrades  (buys {buys} / sells {sells})  "
          f"buy_vol {buy_vol:.1f} / sell_vol {sell_vol:.1f}")
    print(f"    sub-second tick_times: {subsec}/{n}   non-monotonic T: {nonmono}")

    check("tape shape pinned: trade count == frozen 904", n == _EXP_TRADES, f"n={n}")
    check("tape shape pinned: buys == 555 / sells == 349",
          buys == _EXP_BUYS and sells == _EXP_SELLS, f"buys={buys} sells={sells}")
    check("DISCRIMINATING: every trade taker_buy in {0, vol} (per-trade EXACT split)",
          bad_split == 0, f"violations={bad_split}")
    check("every tick_time == T/1000 (event-time clock, no quantization)",
          bad_tt == 0, f"mismatches={bad_tt}")
    check("every vol > 0", nonpos == 0, f"nonpos={nonpos}")
    check("sub-second resolution is pervasive (>= half of trades) -- the clock upgrade",
          subsec >= n // 2, f"subsec={subsec}/{n}")
    check("trade times non-decreasing across the tape", nonmono == 0, f"inversions={nonmono}")
    check("aggregate side maps right: sum(taker_buy) == buy_vol (sells contribute 0)",
          approx(total_taker_buy, buy_vol),
          f"sum_taker_buy={total_taker_buy:.1f} buy_vol={buy_vol:.1f}")
    check("both sides represented", buys > 0 and sells > 0, f"buys={buys} sells={sells}")

    print("\n" + "=" * 60)
    if _FAILS:
        print(f"RESULT: FAILED ({len(_FAILS)}): {_FAILS}")
        return 1
    print("RESULT: ALL ASSERTIONS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
