"""Tier-3 PyQtGraph rendering items.

Native GPU-accelerated chart primitives drawn on the nude white canvas
(spec §5.1). This batch covers the critical render path:

    * LocalTimeAxis    — Unix-second -> host-local clock string (spec §2.3.1)
    * PriceAxis        — bold monospace price labels (spec §5.1.1)
    * CandlestickItem  — B&W candles; built once, refreshed via update_data
                         (spec §1.4.3 — never re-instantiated)
    * OrderBlockLayer  — Otsu-calculus bands with mitigation + velocity tint
    * SessionLayer     — localized 00:00 / 08:00 UTC dividers (spec §2.3.2)
    * LiquidationLayer — cyan/magenta forced-order marks (spec §7.3)

Footprint bubbles, delta imbalances, the COB depth panel and the vector drawing
toolbar are deferred to the next batch and slot in as sibling layers.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui

from . import config
from .quant_engine import parse_ts

# Global PyQtGraph theme — pure light mode (spec §5.1.1)
pg.setConfigOption("background", config.COLOR_CANVAS)
pg.setConfigOption("foreground", config.COLOR_AXIS_TEXT)
pg.setConfigOptions(antialias=True)

_MONO = QtGui.QFont("Consolas", 9)
_MONO.setBold(True)


# ---------------------------------------------------------------------------
# Pooled text labels — the ONLY correct way to draw chart text
# ---------------------------------------------------------------------------
class TextPool:
    """A recycled pool of ``pg.TextItem`` for in-chart labels.

    Text painted into a QPicture inside a GraphicsObject is flipped and
    zoom-scaled by the inverted price-axis ViewBox transform (the root cause of
    the missing footprint/iceberg/OB-multiplier text). ``pg.TextItem`` renders at
    a fixed pixel size at a data coordinate, immune to that transform. Items are
    reused across frames; surplus items are hidden, never destroyed.
    """

    def __init__(self, anchor=(0.5, 0.5), font_size: int = 8, bold: bool = True, z: int = 25):
        self.anchor = anchor
        self.font = QtGui.QFont("Consolas", font_size)
        self.font.setBold(bold)
        self.z = z
        self.items: list[pg.TextItem] = []
        self._plot = None
        self._enabled = True

    def attach(self, plot) -> None:
        self._plot = plot

    def hide_all(self) -> None:
        for it in self.items:
            it.hide()

    def clear(self, plot) -> None:
        """Permanently remove every pooled TextItem from ``plot`` and empty the
        cache. Used by scanner teardown — these items are added by the pool, not
        via active_scanner_items, so the normal sweep can't reach them (leak guard).
        """
        for it in self.items:
            try:
                plot.removeItem(it)
            except Exception:
                pass
        self.items = []

    def set_enabled(self, on: bool) -> None:
        self._enabled = on
        if not on:
            self.hide_all()

    def update(self, specs: list) -> None:
        """specs: list of (x, y, text, color)."""
        if self._plot is None or not self._enabled:
            return
        while len(self.items) < len(specs):
            ti = pg.TextItem(anchor=self.anchor)
            ti.textItem.setFont(self.font)
            ti.setZValue(self.z)
            self._plot.addItem(ti, ignoreBounds=True)
            self.items.append(ti)
        for i, it in enumerate(self.items):
            if i < len(specs):
                x, y, text, color = specs[i]
                it.setText(text, color=color)
                it.setPos(x, y)
                it.show()
            else:
                it.hide()


# ---------------------------------------------------------------------------
# Axes (spec §2.3.1, §5.1.1)
# ---------------------------------------------------------------------------
class LocalTimeAxis(pg.AxisItem):
    """Maps float Unix-second keys to the host machine's local clock.

    ``datetime.fromtimestamp`` (no tz arg) converts an epoch to the host OS local
    timezone automatically, so the displayed clock follows the machine without
    any hardcoded offset (spec §2.1.1). Label granularity adapts to zoom.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setTickFont(_MONO)
        self._scanner_active = False   # bucket-index mode vs chronological mode

    def set_scanner_active(self, on: bool) -> None:
        """Toggle between bucket-ordinal labels and chronological time labels.

        No layout rebuild — flipping the flag and invalidating the cached picture
        is enough for the next paint to re-format the visible ticks (Phase 2 §2).
        """
        if self._scanner_active != on:
            self._scanner_active = on
            self.picture = None
            self.update()

    def tickStrings(self, values, scale, spacing):
        # Scanner active: pure bucket-ordinal axis (Idx: N), no timestamps.
        if self._scanner_active:
            return [f"Idx: {int(round(v))}" for v in values]
        # Off: standard chronological labels in the host-local timezone.
        out = []
        for v in values:
            try:
                dt = datetime.fromtimestamp(v)
            except (OSError, ValueError, OverflowError):
                out.append("")
                continue
            if spacing >= 86400:
                out.append(dt.strftime("%m-%d"))
            elif spacing >= 3600:
                out.append(dt.strftime("%m-%d %H:%M"))
            else:
                out.append(dt.strftime("%H:%M:%S"))
        return out


class PriceAxis(pg.AxisItem):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setTickFont(_MONO)

    def tickStrings(self, values, scale, spacing):
        return [f"{v:.{config.PRICE_DECIMALS}f}" for v in values]


# ---------------------------------------------------------------------------
# Candlesticks (spec §5.1.2) — built once, refreshed with update_data
# ---------------------------------------------------------------------------
class CandlestickItem(pg.GraphicsObject):
    """High-contrast B&W candles rendered into a cached QPicture.

    Per spec §1.4.3 this object is instantiated exactly once per window; live
    updates call :meth:`update_data` which only rebuilds the internal picture —
    the item is never removed from or re-added to the scene, eliminating the C++
    layout resets that drop plot items mid-stream.
    """

    def __init__(self):
        super().__init__()
        self.picture = QtGui.QPicture()
        self._rect = QtCore.QRectF()
        self._border = QtGui.QPen(QtGui.QColor(config.COLOR_CANDLE_BORDER))
        self._border.setCosmetic(True)
        self._bull = QtGui.QBrush(QtGui.QColor(config.COLOR_BULL_BODY))
        self._bear = QtGui.QBrush(QtGui.QColor(config.COLOR_BEAR_BODY))
        self._flat_pen = QtGui.QPen(QtGui.QColor("#888888"))  # zero-range doji -> flat neutral line
        self._flat_pen.setCosmetic(True); self._flat_pen.setWidth(2)

    def update_data(self, times: np.ndarray, ohlcv: np.ndarray, width: float) -> None:
        self.picture = QtGui.QPicture()
        if len(times) == 0:
            self.prepareGeometryChange()
            self.update()
            return

        p = QtGui.QPainter(self.picture)
        p.setPen(self._border)
        half = width / 2.0
        lo_all = float(np.min(ohlcv[:, 2]))
        hi_all = float(np.max(ohlcv[:, 1]))

        for i in range(len(times)):
            x = float(times[i])
            o, h, l, c, _ = ohlcv[i]
            # Zero-range bucket (high==low): flat NEUTRAL line at the one price — no forced
            # TICK/2 body (would imply a range that didn't exist). Mirror of BucketCandleItem
            # (Mode 10); §0.6 degenerate-input contract. DIVERGES FROM the ranged doji below.
            if abs(h - l) < config.TICK_SIZE / 2.0:
                p.setPen(self._flat_pen)
                p.drawLine(QtCore.QPointF(x - half, l), QtCore.QPointF(x + half, l))
                p.setPen(self._border)   # restore for subsequent wicks/bodies
                continue
            # wick
            p.drawLine(QtCore.QPointF(x, l), QtCore.QPointF(x, h))
            # body
            p.setBrush(self._bull if c >= o else self._bear)
            top = max(o, c)
            bot = min(o, c)
            if top == bot:
                top += config.TICK_SIZE / 2.0  # ranged doji (open==close): sliver shows the level
            p.drawRect(QtCore.QRectF(x - half, bot, width, top - bot))
        p.end()

        self._rect = QtCore.QRectF(
            float(times[0]) - half, lo_all,
            float(times[-1]) - float(times[0]) + width, hi_all - lo_all,
        )
        self.prepareGeometryChange()
        self.informViewBoundsChanged()
        self.update()

    def paint(self, p, *args):
        p.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        return self._rect


# ---------------------------------------------------------------------------
# Order blocks (spec §3.3) — bands with mitigation + velocity tint
# ---------------------------------------------------------------------------
def _bucket_alpha(vol_mult: float) -> float:
    a = config.BUCKET_ALPHA_FLOOR + max(0.0, vol_mult - 1.0) * 0.18
    return min(0.55, a)


class OrderBlockLayer(pg.GraphicsObject):
    """Draws active/mitigated order-block rectangles projected to the right edge."""

    def __init__(self, plot=None, show_tiers: bool = False):
        super().__init__()
        self.picture = QtGui.QPicture()
        self._rect = QtCore.QRectF()
        self.visible_filter = 0.0  # min vol_mult (hamburger Min-Multiplier slider)
        self.show_tiers = show_tiers    # velocity tier rankings (spec §7.3.4)
        self.tier_pool = TextPool(anchor=(0, 1), font_size=8, z=26)
        if plot is not None:
            self.tier_pool.attach(plot)

    def attach_text(self, plot) -> None:
        self.tier_pool.attach(plot)

    def setVisible(self, v: bool) -> None:   # noqa: N802 (Qt override)
        super().setVisible(v)
        self.tier_pool.set_enabled(v)

    def update_data(self, obs: list, x_right: float) -> None:
        self.picture = QtGui.QPicture()
        if not obs:
            self.tier_pool.update([])
            self.prepareGeometryChange()
            self.update()
            return

        p = QtGui.QPainter(self.picture)
        xs, ys = [], []
        tier_specs = []
        for ob in obs:
            if ob.get("vol_mult", 0.0) < self.visible_filter:
                continue
            top = float(ob["top"])
            bottom = float(ob["bottom"])
            try:
                x0 = parse_ts(ob["confirm"])
            except (ValueError, KeyError):
                continue
            x1 = parse_ts(ob["end"]) if ob.get("end") else x_right

            bullish = ob["type"] == "bullish"
            vel = ob.get("vol_mult", 1.0)
            neon = vel >= config.VELOCITY_NEON_RATIO
            if bullish:
                rgb = config.RGB_GREEN_NEON if neon else config.RGB_GREEN_STD
            else:
                rgb = config.RGB_RED_NEON if neon else config.RGB_RED_STD
            alpha = _bucket_alpha(vel)
            if not ob.get("active", True):
                alpha *= 0.4  # fade mitigated blocks

            fill = QtGui.QColor(rgb[0], rgb[1], rgb[2])
            fill.setAlphaF(alpha)
            pen = QtGui.QPen(QtGui.QColor(rgb[0], rgb[1], rgb[2]))
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(fill)
            p.drawRect(QtCore.QRectF(x0, bottom, max(1.0, x1 - x0), top - bottom))

            if self.show_tiers:
                tier_specs.append((x0, top, f"x{vel:.1f}", QtGui.QColor(rgb[0], rgb[1], rgb[2])))

            xs += [x0, x1]
            ys += [top, bottom]
        p.end()
        self.tier_pool.update(tier_specs)

        if xs:
            self._rect = QtCore.QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
        self.prepareGeometryChange()
        self.update()

    def update_data_indexed(self, obs: list, x_right: float, ts_to_idx, x_view) -> None:
        """Index-space twin of :meth:`update_data` for the Mode-10 volume canvas.

        Identical color grading / neon / opacity-fade / tier logic; only the X mapping
        differs. Each block is anchored to its EXACT forming bucket via the epoch baked into
        ``ob_id`` (``qob_{type}_{int(b0.start)}_{poc}``) — NOT the minute-floored ``confirm``
        string, which with sub-minute buckets bisects to the minute-boundary bucket and strands
        the block off its candle. The drawn span ``[b0, end-or-live]`` is clamped to the visible
        view ``x_view = (vx0, vx1)``. A block whose forming bucket is pre-anchor (filtered out of
        the scanner window, ``ts_to_idx`` → −1) or whose span is entirely off the window is SKIPPED
        here — Step 2 renders those as price-level bands.
        """
        vx0, vx1 = x_view                      # visible view X-range -> clamp OB spans into it
        self.picture = QtGui.QPicture()
        if not obs:
            self.tier_pool.update([])
            self.prepareGeometryChange()
            self.update()
            return

        p = QtGui.QPainter(self.picture)
        xs, ys = [], []
        tier_specs = []
        for ob in obs:
            if ob.get("vol_mult", 0.0) < self.visible_filter:
                continue
            # Map by the EXACT forming-bucket epoch baked into ob_id (qob_{type}_{int(b0.start)}_{poc}),
            # NOT the minute-floored `confirm`: buckets are sub-minute, so a floored confirm bisects to the
            # minute-boundary bucket and strands the block off its candle. b0's POC IS the band, so anchoring
            # to b0 makes X and price agree. +1.0 lands the bisect inside b0's span past the int() truncation.
            _parts = str(ob.get("ob_id", "")).split("_")
            _id_start = float(_parts[2]) if len(_parts) >= 4 and _parts[2].isdigit() else None
            try:
                confirm_idx = (ts_to_idx(_id_start + 1.0) if _id_start is not None
                               else ts_to_idx(parse_ts(ob["confirm"])))   # fallback: malformed id
            except (ValueError, KeyError):
                continue
            if confirm_idx == -1:
                continue   # forming bucket is pre-anchor (filtered out of the scanner window) -> no valid
                           # X; skip until Step 2 renders it as a price-level band (else it piles at index 0)

            end_str = ob.get("end")
            end_idx = ts_to_idx(parse_ts(end_str)) if end_str else None

            # clamp the drawn span [b0, end-or-live] to the visible view so a block confirmed before the
            # window projects from the LEFT EDGE (vx0); skip blocks whose span is entirely off the window.
            confirm_x = float(confirm_idx)
            end_x = x_right if end_idx is None else float(end_idx)
            if end_x <= vx0 or confirm_x >= vx1:
                continue
            x0 = max(confirm_x, vx0)
            x1 = min(end_x, vx1)
            if x1 <= x0:
                x1 = x0 + 1.0                  # keep a visible minimum width

            top = float(ob["top"])
            bottom = float(ob["bottom"])
            bullish = ob["type"] == "bullish"
            vel = ob.get("vol_mult", 1.0)
            neon = vel >= config.VELOCITY_NEON_RATIO
            if bullish:
                rgb = config.RGB_GREEN_NEON if neon else config.RGB_GREEN_STD
            else:
                rgb = config.RGB_RED_NEON if neon else config.RGB_RED_STD
            alpha = _bucket_alpha(vel)
            if not ob.get("active", True):
                alpha *= 0.4                   # fade mitigated blocks

            fill = QtGui.QColor(rgb[0], rgb[1], rgb[2])
            fill.setAlphaF(alpha)
            pen = QtGui.QPen(QtGui.QColor(rgb[0], rgb[1], rgb[2]))
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(fill)
            p.drawRect(QtCore.QRectF(x0, bottom, max(1.0, x1 - x0), top - bottom))

            if self.show_tiers:
                tier_specs.append((x0, top, f"x{vel:.1f}", QtGui.QColor(rgb[0], rgb[1], rgb[2])))

            xs += [x0, x1]
            ys += [top, bottom]
        p.end()
        self.tier_pool.update(tier_specs)

        if xs:
            self._rect = QtCore.QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
        self.prepareGeometryChange()
        self.update()

    def paint(self, p, *args):
        p.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        return self._rect


# ---------------------------------------------------------------------------
# Session dividers (spec §2.3.2) — localized 00:00 / 08:00 UTC boundaries
# ---------------------------------------------------------------------------
class SessionLayer(pg.GraphicsObject):
    def __init__(self):
        super().__init__()
        self.picture = QtGui.QPicture()
        self._rect = QtCore.QRectF()

    def update_data(self, t_start: float, t_end: float, y0: float, y1: float) -> None:
        self.picture = QtGui.QPicture()
        if t_end <= t_start:
            self.prepareGeometryChange(); self.update(); return

        pen = QtGui.QPen(QtGui.QColor(180, 180, 180))
        pen.setCosmetic(True)
        pen.setStyle(QtCore.Qt.DashLine)
        p = QtGui.QPainter(self.picture)
        p.setPen(pen)

        # Walk each UTC day in range; mark its 00:00 and 08:00 UTC instants.
        day = 86400
        first = int(t_start // day) * day
        t = first
        while t <= t_end + day:
            for utc_hour in (0, 8):
                mark = t + utc_hour * 3600
                if t_start <= mark <= t_end:
                    p.drawLine(QtCore.QPointF(mark, y0), QtCore.QPointF(mark, y1))
            t += day
        p.end()
        self._rect = QtCore.QRectF(t_start, y0, t_end - t_start, y1 - y0)
        self.prepareGeometryChange(); self.update()

    def paint(self, p, *args):
        p.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        return self._rect


# ---------------------------------------------------------------------------
# Liquidation marks (spec §7.3) — cyan shorts / magenta longs
# ---------------------------------------------------------------------------
class LiquidationLayer(pg.ScatterPlotItem):
    def __init__(self):
        super().__init__(size=9, pxMode=True)

    def update_data(self, liqs: list) -> None:
        spots = []
        for lq in liqs[-200:]:
            # SELL side = long liquidated (magenta); BUY side = short liquidated (cyan)
            is_long_liq = lq["side"] == "SELL"
            color = config.COLOR_LIQ_LONG if is_long_liq else config.COLOR_LIQ_SHORT
            spots.append({
                "pos": (lq["time"], lq["price"]),
                "brush": pg.mkBrush(color),
                "pen": pg.mkPen(color),
                "symbol": "t" if is_long_liq else "t1",
                "size": 8 + min(14, lq["qty"] / 200.0),
            })
        self.setData(spots)


# ---------------------------------------------------------------------------
# Bucket candlesticks (Mode 10) — constant-volume candles at integer X,
# per-candle body brush (Neon Engine V2); wicks/borders use a neutral pen.
# ---------------------------------------------------------------------------
class BucketCandleItem(pg.GraphicsObject):
    def __init__(self):
        super().__init__()
        self.picture = QtGui.QPicture()
        self._rect = QtCore.QRectF()
        self._pen = QtGui.QPen(QtGui.QColor("#888888"))   # uniform neutral wick/border
        self._pen.setCosmetic(True)
        self._flat_pen = QtGui.QPen(QtGui.QColor("#888888"))  # zero-range doji -> flat neutral line
        self._flat_pen.setCosmetic(True); self._flat_pen.setWidth(2)

    def update_data(self, x: list, opens: list, highs: list, lows: list,
                    closes: list, brushes: list, width: float = 0.8) -> None:
        self.picture = QtGui.QPicture()
        if not x:
            self.prepareGeometryChange(); self.update(); return

        p = QtGui.QPainter(self.picture)
        half = width / 2.0
        lo_all = min(lows)
        hi_all = max(highs)
        for i in range(len(x)):
            xi = float(x[i])
            o, h, l, c = opens[i], highs[i], lows[i], closes[i]
            # Zero-range bucket (high==low -> O=H=L=C): ALL volume traded at one tick. The
            # honest mark is a flat NEUTRAL line at that price — the forced TICK/2 body would
            # imply a range that never existed (the §0.6 degenerate sibling of the zero-vector
            # churn lie). Vector/flow reads from the stats box + footprint; the POC dot
            # (separate, z6) sits at center. DIVERGES FROM the ranged doji below.
            if abs(h - l) < config.TICK_SIZE / 2.0:
                p.setPen(self._flat_pen)
                p.drawLine(QtCore.QPointF(xi - half, l), QtCore.QPointF(xi + half, l))
                continue
            p.setPen(self._pen)
            # wick (neutral)
            p.drawLine(QtCore.QPointF(xi, l), QtCore.QPointF(xi, h))
            # body (per-candle dominance brush, neutral border)
            top, bot = max(o, c), min(o, c)
            if top == bot:
                top += config.TICK_SIZE / 2.0   # ranged doji (open==close): sliver shows the level
            p.setBrush(brushes[i] if i < len(brushes) else QtCore.Qt.NoBrush)
            p.drawRect(QtCore.QRectF(xi - half, bot, width, top - bot))
        p.end()

        span = (hi_all - lo_all) if hi_all > lo_all else 1.0
        self._rect = QtCore.QRectF(float(x[0]) - half, lo_all,
                                   float(x[-1]) - float(x[0]) + width, span)
        self.prepareGeometryChange()
        self.informViewBoundsChanged()
        self.update()

    def paint(self, p, *args):
        p.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        return self._rect
