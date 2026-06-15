"""Phase 5 (aggTrade) — pure per-trade → ``process_tick``-args mapping.

Sub-step 19.1. **Pure and inert:** nothing in the app imports this yet — the daemon
wiring (a dedicated ``aggtrade_stream`` routing each trade into all 5 engines) lands
in 19.3. This module owns only the transform.

Under aggTrade a "tick" is ONE real trade — true price, true qty, and EXACT aggressor
side from the buyer-maker flag — so the taker split is *per-trade exact* (``taker_buy``
is the whole qty on a buy, ``0`` on a sell), not the kline-aggregate ratio the legacy
pipeline derived from cumulative-frame deltas. ``tick_time`` is the true execution
time in float seconds, feeding the Step-1 event-time clock at real sub-second
resolution (vs the legacy candle-open second).

Deliberately NOT produced here:
  * ``delta_oi`` — OI stays a 5s poll and is attributed by the pending-balance bleed
    in 19.2 (one global balance bled per trade under the per-trade Step-2 clamp).
  * ``footprints_dict`` — daemon wiring (19.3).
"""

from __future__ import annotations

from typing import NamedTuple


class TickArgs(NamedTuple):
    """The four :meth:`QuantEngine.process_tick` args an aggTrade makes exact."""
    price: float
    vol: float
    taker_buy: float
    tick_time: float


def trade_to_tick(agg: dict) -> TickArgs:
    """Map a Binance USD-M ``@aggTrade`` payload to engine ``process_tick`` args.

    ``agg`` is the raw trade object (the tape's ``data``): ``p``=price, ``q``=qty,
    ``m``=buyer-maker flag, ``T``=trade time (epoch ms).

    ``m`` semantics (Binance): "is the buyer the market maker?" ``True`` => the buyer
    is the passive maker, so the aggressor (taker) is the SELLER → a sell, taker-buy
    volume is ``0``. ``False`` => the aggressor is the BUYER → ``taker_buy`` is the
    full qty. Per-trade exact: ``b_ratio`` is exactly ``1.0`` or ``0.0``, never a
    fraction (``taker_buy`` is *assigned* ``vol`` or ``0.0``, not computed, so the
    equality is bit-exact downstream).
    """
    vol = float(agg["q"])
    return TickArgs(
        price=float(agg["p"]),
        vol=vol,
        taker_buy=0.0 if agg["m"] else vol,
        tick_time=float(agg["T"]) / 1000.0,
    )
