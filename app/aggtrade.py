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

import math
import statistics
from typing import NamedTuple

from . import config


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


# ---------------------------------------------------------------------------
# 19.2 — global OI pending-balance attributor
# ---------------------------------------------------------------------------
# EWMA smoothing for Vw, derived from the interval-count memory so the only knob is
# the intuitive OI_VW_EWMA_N (a fixed alpha would hide the memory length).
_VW_ALPHA = 2.0 / (config.OI_VW_EWMA_N + 1.0)


def median_target_vol(target_vols) -> float:
    """Global floor reference for the OI cap: the MEDIAN engine ``target_vol``.

    The attributor holds ONE pending balance for all five engines, so the cap floor
    must not privilege any single engine — the median is the symmetric global
    reference (vs reaching into the 1m engine, an awkward asymmetry). Empty / cold
    boot → ``DEFAULT_TARGET_VOL``.
    """
    vols = [float(v) for v in target_vols if v and v > 0]
    return statistics.median(vols) if vols else config.DEFAULT_TARGET_VOL


class OiAttributor:
    """One global open-interest pending-balance attributor (sub-step 19.2).

    OI is a 5 s REST poll while aggTrades are sub-second, so OI cannot be made
    per-trade exact. Each polled OI delta is bled across the trades that follow it
    through one global signed balance, clamped per trade to ``±q`` (the Step-2 clamp,
    now per-trade — its most important home). A scale-free cap ``K·Vw`` bounds the
    balance via **cap-and-hold**, which bounds the attribution lag to ``≤ K`` poll
    intervals. The refused excess (``dropped_oi`` — a market fact: OI no volume could
    carry) and any reconnect-gap discard (``resync_discarded_oi`` — an ops signal:
    lost data) are surfaced as **separate** cumulative counters, so a discard is
    never silent and the two causes are never blurred.

    Pure + inert: 19.3 wires feeds' OI poll / aggtrade / reconnect to
    :meth:`on_poll` / :meth:`on_trade` / :meth:`on_reconnect`, passing
    ``median_target_vol(<engine target_vols>)`` as ``target_vol_ref``.

    Accounting identity (the rigorous "nothing lost"), exact at all times::

        Σ(shares) + pending_oi + dropped_oi + resync_discarded_oi == Σ(ΔOI injected)
    """

    def __init__(self) -> None:
        self.pending_oi = 0.0
        self.last_oi: float | None = None   # None until the first poll seeds the baseline
        self.vw: float | None = None        # None until the first FULL interval completes
        self.interval_vol = 0.0
        self.dropped_oi = 0.0               # cumulative |OI| the cap refused (market fact)
        self.resync_discarded_oi = 0.0      # cumulative |pending| dropped on reconnect (ops)

    def on_trade(self, q: float) -> float:
        """Bleed the balance by one trade of volume ``q``; return its ``delta_oi`` share.

        ``share = clamp(pending_oi, -q, +q)`` — never exceeds the trade's own volume,
        so ``process_tick`` sees ``|delta_oi| ≤ vol`` (the per-trade Step-2 clamp). The
        bleed only moves ``pending_oi`` toward 0; it can never grow it.
        """
        self.interval_vol += q
        share = math.copysign(min(abs(self.pending_oi), q), self.pending_oi)
        self.pending_oi -= share
        return share

    def on_poll(self, oi_new: float, target_vol_ref: float) -> None:
        """Fold the finished interval into ``Vw``, inject the OI delta, cap-and-hold.

        The first poll seeds the baseline only (no pre-boot OI history), so
        ``pending_oi`` is 0 until the second poll — by which point the first full
        interval has seeded ``Vw``. The cap is therefore never consulted while ``Vw``
        is undefined: the cold-start edge is designed out, not guarded.
        """
        if self.last_oi is None:                       # FIRST poll: seed baseline only
            self.last_oi = oi_new
            self.interval_vol = 0.0
            return
        self.vw = (self.interval_vol if self.vw is None
                   else _VW_ALPHA * self.interval_vol + (1.0 - _VW_ALPHA) * self.vw)
        self.interval_vol = 0.0
        self.pending_oi += (oi_new - self.last_oi)     # the ONLY place |pending_oi| grows
        self.last_oi = oi_new
        cap = self.current_cap(target_vol_ref)
        if abs(self.pending_oi) > cap:                 # CAP-AND-HOLD: drop + count the excess
            self.dropped_oi += abs(self.pending_oi) - cap
            self.pending_oi = math.copysign(cap, self.pending_oi)

    def on_reconnect(self, current_oi: float) -> None:
        """Resync the OI baseline after an aggTrade gap; discard (and count) the balance.

        The missed-gap trades are unreconstructable, so the pending balance is dropped
        rather than dumped onto resumed trades. Counted in ``resync_discarded_oi`` —
        kept separate from ``dropped_oi`` because a rising value flags a flaky
        connection, a distinct signal from the market-driven cap drop.
        """
        self.resync_discarded_oi += abs(self.pending_oi)
        self.pending_oi = 0.0
        self.last_oi = current_oi
        self.interval_vol = 0.0

    def current_cap(self, target_vol_ref: float) -> float:
        """Scale-free cap ``C = K·max(Vw, FLOOR_FRAC·ref)`` (cold ``Vw`` → floor only).

        Floored at a fraction of the (median) engine ``target_vol`` so a dead-volume
        patch (``Vw → 0``) cannot collapse the cap to 0 and silently nuke a legitimate
        balance (rule 0.6/1).
        """
        vw = self.vw if self.vw is not None else 0.0
        return config.OI_CAP_K * max(vw, config.OI_VW_FLOOR_FRAC * target_vol_ref)
