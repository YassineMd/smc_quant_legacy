"""Step 4 authoritative synthetic gate (rule 0.7): adaptive Effort/Result.

History-independent, deterministic. Builds buckets with crafted level
distributions and asserts the new volume-weighted dispersion effort denominator
(a) reads er = vol on a pure-absorption bucket (1.0-tick floor, BOUNDED, not the
~1e7 epsilon explosion), (b) discriminates absorption from a clean run of equal
volume, and (c) is wick-robust vs the legacy |high-low| range. Exercises the real
code path through QuantBucket.live_snapshot and QuantEngine._close_active_bucket
(both share _effort_ticks).

Run:
    python scripts/test_step4_effort_result.py
Exit 0 = all assertions passed.
"""

from __future__ import annotations

import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app import config                                              # noqa: E402
from app.quant_engine import QuantBucket, QuantEngine, _effort_ticks  # noqa: E402

_FAILS: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not cond:
        _FAILS.append(label)


def approx(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol


def build_bucket(price_vols: dict, buy: float, sell: float) -> QuantBucket:
    """A bucket whose level volumes follow `price_vols`; buy/sell are the totals."""
    b = QuantBucket(target_vol=1e12, start_time=1000.0)
    b.buy_vol, b.sell_vol = float(buy), float(sell)
    b.curr_vol = float(buy + sell)
    bfrac = (buy / (buy + sell)) if (buy + sell) else 0.0
    for p, vol in price_vols.items():
        b.levels[f"{p:.2f}"] = {"b": vol * bfrac, "s": vol * (1.0 - bfrac)}
        b.high = max(b.high, p)
        b.low = min(b.low, p)
    b.close_price = next(iter(price_vols))
    return b


def live_buyer_er(b: QuantBucket) -> float:
    return b.live_snapshot(2000.0, 1.0)["buyer_er"]


def old_range_er(b: QuantBucket, vol: float) -> float:
    ticks = max(1.0, abs(b.high - b.low) / config.TICK_SIZE)
    return vol / ticks


def main() -> int:
    V = 10000.0

    # --- CASE 1: pure absorption (all volume at ONE price) -------------------
    b1 = build_bucket({150.00: V}, buy=V, sell=0.0)
    t1 = _effort_ticks(b1.levels)
    er1 = live_buyer_er(b1)
    print(f"[1] absorption : ticks={t1:.4f}  new_er={er1:.2f}  old_range_er={old_range_er(b1, V):.2f}")
    check("absorption: dispersion 0 -> ticks floors to 1.0", approx(t1, 1.0))
    check("absorption: buyer_er == buy_vol (BOUNDED, not exploded)",
          approx(er1, V) and math.isfinite(er1), f"{er1:.2f}")
    check("absorption: identical to legacy range path on the single-price case",
          approx(er1, old_range_er(b1, V)))

    # --- CASE 2: wide clean run, SAME total volume across many levels --------
    levels2 = {round(149.0 + i * 0.10, 2): V / 21 for i in range(21)}  # 149.00..151.00
    b2 = build_bucket(levels2, buy=V, sell=0.0)
    t2 = _effort_ticks(b2.levels)
    er2 = live_buyer_er(b2)
    print(f"[2] clean-run  : ticks={t2:.4f}  new_er={er2:.2f}  old_range_er={old_range_er(b2, V):.2f}")
    check("clean-run: larger dispersion -> larger ticks -> SMALLER er", t2 > 1.0 and er2 < er1)
    check("DISCRIMINATION: er(absorption) >> er(clean-run) for EQUAL volume",
          er1 > 10.0 * er2, f"{er1:.0f} vs {er2:.0f}  ({er1 / er2:.1f}x)")

    # --- CASE 3: single wick (mass at one price + tiny far outlier) ----------
    b3 = build_bucket({150.00: 9900.0, 155.00: 100.0}, buy=V, sell=0.0)
    er3 = live_buyer_er(b3)
    er3_old = old_range_er(b3, V)
    print(f"[3] single-wick: new_er={er3:.2f}  old_range_er={er3_old:.2f}  ({er3 / er3_old:.1f}x more robust)")
    check("WICK-ROBUST: new dispersion er >> old range er (wick collapses range, not dispersion)",
          er3 > 5.0 * er3_old, f"new {er3:.0f} vs old {er3_old:.0f}")
    check("wick: new er stays finite and bounded", math.isfinite(er3) and er3 > 0)

    # --- degenerate guard + closed/live consistency -------------------------
    check("empty levels -> ticks 1.0 (degenerate guard, no div-by-zero)",
          approx(_effort_ticks({}), 1.0))

    eng = QuantEngine(target_vol=1e12)
    eng.active_bucket = build_bucket({150.00: 9900.0, 155.00: 100.0}, buy=V, sell=0.0)
    eng.active_bucket.start_time = 1000.0
    eng._close_active_bucket(1100.0, {})   # real close path
    closed_er = eng.closed_buckets[-1].buyer_er
    print(f"[=] closed-path buyer_er={closed_er:.2f}  (live-path {er3:.2f})")
    check("closed-path er == live-path er (both use _effort_ticks)", approx(closed_er, er3))

    print("\n" + "=" * 60)
    if _FAILS:
        print(f"RESULT: FAILED ({len(_FAILS)}): {_FAILS}")
        return 1
    print("RESULT: ALL ASSERTIONS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
