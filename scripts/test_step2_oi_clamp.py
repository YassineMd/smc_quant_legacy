"""Step 2 authoritative synthetic gate (rule 0.7): the dOI / taker-buy clamp.

History-independent, deterministic. Drives the REAL clamp in
`MarketDataCore._process_kline` (not a reimplementation) with crafted kline
frames + OI state, and asserts the engine's resulting 4-vectors. Each clamped
case is paired with a raw-`QuantEngine` counterfactual fed the UNCLAMPED values,
to prove the clamp is what restores conservation (the counterfactual is the
pre-fix bug).

Run:
    python scripts/test_step2_oi_clamp.py
Exit 0 = all assertions passed.
"""

from __future__ import annotations

import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.feeds import MarketDataCore        # noqa: E402
from app.quant_engine import QuantEngine    # noqa: E402

_OPEN_MS = 1_700_000_000_000   # arbitrary candle-open (ms); both frames share it
_FAILS: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not cond:
        _FAILS.append(label)


def approx(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol


def _kline(v: float, V: float, c: float = 150.0) -> dict:
    """Minimal kline payload `k` for _process_kline (cumulative v / taker-buy V)."""
    return {"t": _OPEN_MS, "v": v, "V": V, "c": c,
            "o": 150.0, "h": 151.0, "l": 149.0, "i": "1m", "x": False}


def _core() -> MarketDataCore:
    return MarketDataCore({}, lambda tf, line: None, lambda line: None)


def _vec_sum(b) -> float:
    return b.opL + b.opS + b.clL + b.clS


def main() -> int:
    tf = "1m"

    # ===================================================================
    # CASE 1 — |delta_oi| > deltaVol must clamp -> exact conservation.
    # Frame 1 establishes lastVol/oi_close; then OI jumps +100 (a 5s poll)
    # against a deltaVol of only 5, the exact sampling artifact Step 2 targets.
    # ===================================================================
    core = _core()
    core.pulse_state["oi"] = 1000.0
    core._process_kline(_kline(10.0, 6.0), tf, event_ms=_OPEN_MS + 1000)   # deltaVol 10, dOI 0
    core.pulse_state["oi"] = 1100.0                                        # +100 OI jump
    core._process_kline(_kline(15.0, 9.0), tf, event_ms=_OPEN_MS + 2000)   # deltaVol 5, raw dOI 100

    b = core.engines[tf].active_bucket
    residual = (_vec_sum(b) - b.curr_vol) / b.curr_vol
    print(f"\nCASE 1 (clamped):  curr_vol={b.curr_vol}  "
          f"opL/opS/clL/clS={b.opL:.3f}/{b.opS:.3f}/{b.clL:.3f}/{b.clS:.3f}  "
          f"residual={residual:.2e}")
    check("|dOI|>vol: post-clamp 4-vector sum == curr_vol (residual ~0)",
          abs(residual) < 1e-9, f"residual={residual:.2e}")
    check("|dOI|>vol: no single vector exceeds curr_vol",
          all(v <= b.curr_vol + 1e-9 for v in (b.opL, b.opS, b.clL, b.clS)),
          f"max vec={max(b.opL, b.opS, b.clL, b.clS):.3f} vs curr_vol={b.curr_vol}")

    # Counterfactual: same raw numbers WITHOUT the clamp (delta_oi=100, vol=5).
    cf = QuantEngine()
    cf.process_tick(price=150.0, vol=5.0, taker_buy=3.0, delta_oi=100.0,
                    footprints_dict={}, tick_time=float(_OPEN_MS) / 1000.0)
    cfb = cf.active_bucket
    cf_res = (_vec_sum(cfb) - cfb.curr_vol) / cfb.curr_vol
    print(f"CASE 1 (UNCLAMPED counterfactual): residual={cf_res:.2f}  "
          f"max vec={max(cfb.opL, cfb.opS, cfb.clL, cfb.clS):.1f} vs curr_vol={cfb.curr_vol}")
    check("counterfactual proves the bug: unclamped residual >> 0",
          cf_res > 1.0, f"residual={cf_res:.2f}")
    check("counterfactual proves the bug: unclamped vector exceeds curr_vol",
          max(cfb.opL, cfb.opS, cfb.clL, cfb.clS) > cfb.curr_vol)

    # ===================================================================
    # CASE 2 — taker_buy > deltaVol (non-monotonic frame) must clamp into
    # [0, deltaVol] so b_ratio / s_ratio stay in [0, 1] (sell_vol never < 0).
    # ===================================================================
    core2 = _core()
    core2.pulse_state["oi"] = 1000.0   # constant OI -> dOI 0, isolate the taker clamp
    core2._process_kline(_kline(10.0, 5.0), tf, event_ms=_OPEN_MS + 1000)
    b2 = core2.engines[tf].active_bucket
    buy_a, sell_a = b2.buy_vol, b2.sell_vol
    core2._process_kline(_kline(12.0, 10.0), tf, event_ms=_OPEN_MS + 2000)  # deltaVol 2, deltaBuy 5
    b2 = core2.engines[tf].active_bucket
    buy_d, sell_d = b2.buy_vol - buy_a, b2.sell_vol - sell_a   # this frame's contribution
    vol_d = 2.0
    b_ratio = buy_d / vol_d
    s_ratio = sell_d / vol_d
    print(f"\nCASE 2 (clamped):  frame deltaVol={vol_d} deltaBuy=5 -> "
          f"b_ratio={b_ratio:.3f} s_ratio={s_ratio:.3f}  "
          f"bucket buy/sell={b2.buy_vol}/{b2.sell_vol}")
    check("taker>vol: derived b_ratio in [0,1]", 0.0 <= b_ratio <= 1.0 + 1e-9, f"{b_ratio:.3f}")
    check("taker>vol: derived s_ratio in [0,1]", 0.0 <= s_ratio <= 1.0 + 1e-9, f"{s_ratio:.3f}")
    check("taker>vol: bucket sell_vol stays >= 0 (no negative volume)",
          b2.sell_vol >= -1e-9, f"sell_vol={b2.sell_vol}")
    check("taker>vol: buy_vol + sell_vol == curr_vol", approx(b2.buy_vol + b2.sell_vol, b2.curr_vol))

    # Counterfactual: unclamped taker_buy=5 on vol=2 -> b_ratio 2.5, s_ratio -1.5.
    cf2 = QuantEngine()
    cf2.process_tick(price=150.0, vol=2.0, taker_buy=5.0, delta_oi=0.0,
                     footprints_dict={}, tick_time=float(_OPEN_MS) / 1000.0)
    cf2b = cf2.active_bucket
    print(f"CASE 2 (UNCLAMPED counterfactual): buy/sell={cf2b.buy_vol}/{cf2b.sell_vol}  "
          f"(s_ratio={cf2b.sell_vol / cf2b.curr_vol:.2f})")
    check("counterfactual proves the bug: unclamped sell_vol < 0",
          cf2b.sell_vol < 0, f"sell_vol={cf2b.sell_vol}")

    # No NaN/inf anywhere in the clamped buckets
    check("clamped buckets carry only finite vectors",
          all(math.isfinite(x) for x in (b.opL, b.opS, b.clL, b.clS, b2.buy_vol, b2.sell_vol)))

    print("\n" + "=" * 60)
    if _FAILS:
        print(f"RESULT: FAILED ({len(_FAILS)}): {_FAILS}")
        return 1
    print("RESULT: ALL ASSERTIONS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
