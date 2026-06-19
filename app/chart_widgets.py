"""Tier-3 PyQtGraph rendering items.

Native GPU-accelerated chart primitives drawn on the nude white canvas
(spec §5.1). This batch covers the critical render path:

    * LocalTimeAxis    — Unix-second -> host-local clock string (spec §2.3.1)
    * PriceAxis        — bold monospace price labels (spec §5.1.1)
    * OrderBlockLayer  — Otsu-calculus OB bands (Mode-10 index render via update_data_indexed)
    * AbsorptionLayer  — Mode-10 whale-absorption LINES
    * BucketCandleItem — Mode-10 volume-bucket candles (Neon Engine V2)

(The time-chart render items CandlestickItem / SessionLayer / LiquidationLayer were removed with
the time chart — Mode 10 is the only candle surface now.)
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
        self._last: list = []   # last applied (x,y,text,color) per slot — skip-unchanged guard
        self._plot = None
        self._enabled = True

    def attach(self, plot) -> None:
        self._plot = plot

    def hide_all(self) -> None:
        for it in self.items:
            it.hide()
        self._last = [None] * len(self.items)   # force a full re-apply (re-show) on the next update

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
        self._last = []

    def set_enabled(self, on: bool) -> None:
        self._enabled = on
        if not on:
            self.hide_all()

    def update(self, specs: list) -> None:
        """specs: list of (x, y, text, color).

        Skip-unchanged: each pooled label caches its last applied ``(x, y, text, color)``;
        a slot whose spec is identical is left untouched — no ``setText`` (HTML/font re-layout)
        and no ``setPos`` — so it is neither re-laid-out NOR dirtied, and Qt doesn't repaint it.
        On a stationary tick only the live edge's labels change; the immutable closed labels are
        skipped. (Visual is identical — a skipped slot keeps the exact text/pos it displays.)
        """
        if self._plot is None or not self._enabled:
            return
        while len(self.items) < len(specs):
            ti = pg.TextItem(anchor=self.anchor)
            ti.textItem.setFont(self.font)
            ti.setZValue(self.z)
            self._plot.addItem(ti, ignoreBounds=True)
            self.items.append(ti)
            self._last.append(None)
        for i, it in enumerate(self.items):
            if i < len(specs):
                spec = specs[i]
                if spec != self._last[i]:        # only re-layout/reposition on an actual change
                    x, y, text, color = spec
                    it.setText(text, color=color)
                    it.setPos(x, y)
                    it.show()
                    self._last[i] = spec
            elif self._last[i] is not None:      # surplus slot that was showing -> hide once
                it.hide()
                self._last[i] = None


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

    def update_data_indexed(self, obs: list, x_right: float, ts_to_idx, x_view) -> None:
        """Index-space twin of :meth:`update_data` for the Mode-10 volume canvas.

        Every block is a BOX showing its lifespan, both edges mapped by EXACT bucket epochs
        embedded by the daemon (no minute-floor resolution loss, no sub-second +epsilon drift):
        formation from ``start_epoch`` (the confirming bucket's start), consumption from
        ``end_epoch`` (the candle that body-engulfed it).
          • ALIVE — solid box ``[formation -> live edge]`` (still active).
          • DEAD  — faded box ``[formation -> mitigation+1]`` (drawn THROUGH the consuming candle),
            ALWAYS shown (mitigated OBs stay faded; the single "Order Blocks" toggle hides alive + dead together).
        A block whose FORMATION precedes the anchor (``confirm_idx == -1``) is out of scope and
        skipped — no bands, no floating. The drawn span is clamped to ``x_view = (vx0, vx1)``.
        Falls back to the old ``ob_id`` int-epoch (+1.0) / minute-floored ``end`` only for OBs
        missing the exact epochs (malformed / pre-upgrade).
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
            # Formation edge from the EXACT confirming-bucket epoch (start_epoch, a full-float
            # bucket start) — no epsilon, so sub-second buckets can't drift the map. Fall back to
            # the int-truncated ob_id epoch (needs +1.0 to clear the truncation) only for OBs that
            # predate start_epoch; final fallback is the minute-floored `confirm`.
            _se = ob.get("start_epoch")
            if _se is not None:
                confirm_idx = ts_to_idx(_se)
            else:
                _parts = str(ob.get("ob_id", "")).split("_")
                _id_start = float(_parts[2]) if len(_parts) >= 4 and _parts[2].isdigit() else None
                try:
                    confirm_idx = (ts_to_idx(_id_start + 1.0) if _id_start is not None
                                   else ts_to_idx(parse_ts(ob["confirm"])))   # fallback: malformed id
                except (ValueError, KeyError):
                    continue
            if confirm_idx == -1:
                continue   # formation precedes the anchor -> out of scope (anchor-forward only)
            active = ob.get("active", True)   # dead boxes always draw (faded); gated only by m10_obs visibility

            # Box span: formation -> live edge if alive, else the EXACT consuming bucket + 1
            # (end_epoch is a full-float bucket start; +1 draws THROUGH the candle so the kill
            # shows — no +1.0 epsilon, which would overshoot a sub-second consuming bucket).
            confirm_x = float(confirm_idx)
            if active:
                end_x = x_right
            else:
                _ee = ob.get("end_epoch")
                if _ee is not None:
                    end_x = float(ts_to_idx(_ee) + 1)
                else:                              # fallback: pre-end_epoch data -> minute-floored end
                    _es = ob.get("end")
                    end_x = float(ts_to_idx(parse_ts(_es))) if _es else x_right
            if end_x <= vx0 or confirm_x >= vx1:
                continue                           # span entirely off the visible view
            x0 = max(confirm_x, vx0)
            x1 = min(end_x, vx1)
            if x1 <= x0:
                x1 = x0 + 1.0                      # keep a visible minimum width

            top = float(ob["top"])
            bottom = float(ob["bottom"])
            bullish = ob["type"] == "bullish"
            vel = ob.get("vol_mult", 1.0)
            neon = vel >= config.VELOCITY_NEON_RATIO
            if bullish:
                rgb = config.RGB_GREEN_NEON if neon else config.RGB_GREEN_STD
            else:
                rgb = config.RGB_RED_NEON if neon else config.RGB_RED_STD
            col = QtGui.QColor(rgb[0], rgb[1], rgb[2])
            alpha = _bucket_alpha(vel)
            if not active:
                alpha *= 0.4                       # fade mitigated blocks
            fill = QtGui.QColor(col); fill.setAlphaF(alpha)
            pen = QtGui.QPen(col); pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(fill)
            p.drawRect(QtCore.QRectF(x0, bottom, max(1.0, x1 - x0), top - bottom))
            if self.show_tiers:
                tier_specs.append((x0, top, f"x{vel:.1f}", col))
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
# Whale-absorption LINES (bucket-native) — one horizontal line at the POC price per standing
# mark from calc_absorption: color = side (blue buy / orange sell), LINE THICKNESS = kappa
# (relative strength, continuous — replaces the old discrete $-tiers), label = $peak (kappa).
# The line spans bucket CELLS [birth-0.5 -> death+0.5 | live-edge] (candles are centered at index
# i with half-width 0.5); active runs OPEN to the live edge, dead stops (slightly dimmer) at its
# death bucket's right cell-edge — the line ENDING is the death signal, no vertical end-cap.
# An iceberg is a price LEVEL, not a zone — a line at the POC is the honest
# "this is the price the whale defended".
# ---------------------------------------------------------------------------
RGB_ABSORB_BUY = (41, 121, 255)     # blue — buyers absorbed (a buy wall held)
RGB_ABSORB_SELL = (255, 145, 0)     # orange — sellers absorbed (a sell wall held)
_ABSORB_CAP_H = 0.03                # dead end-cap half-height (price) — a small "died here" tick


def _absorb_thickness(kappa: float) -> float:
    """kappa (relative strength) -> line width in px. Floor kappa=0.80 -> 1px (thin/weak);
    kappa~4.6 -> 6px (bold/strong). Continuous — replaces the old discrete $-tier opacity.
    (Thin-end can be bumped to ~1.5 if 1px reads too faint on screen.)"""
    return max(1.0, min(6.0, 1.0 + (kappa - 0.80) * 1.3))


class AbsorptionLayer(pg.GraphicsObject):
    """Mode-10 whale-absorption LINES. Each standing mark from ``calc_absorption`` draws a
    horizontal line at its POC ``price`` spanning the bucket CELLS ``[birth-0.5 -> death+0.5 |
    live-edge]`` (candles are centered at index i, half-width 0.5). Color = side; LINE THICKNESS
    = ``kappa`` (relative strength, continuous); label = ``$peak (kappa)``. Active -> runs OPEN
    to the live edge; dead -> stops (slightly dimmer) at its death bucket's right cell-edge —
    the line ENDING is the death signal (no vertical end-cap).
    """

    def __init__(self, plot=None):
        super().__init__()
        self.picture = QtGui.QPicture()
        self._rect = QtCore.QRectF()
        self.label_pool = TextPool(anchor=(0, 1), font_size=8, z=27)
        if plot is not None:
            self.label_pool.attach(plot)

    def attach_text(self, plot) -> None:
        self.label_pool.attach(plot)

    def setVisible(self, v: bool) -> None:   # noqa: N802 (Qt override)
        super().setVisible(v)
        self.label_pool.set_enabled(v)

    def paint(self, p, *args):
        p.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        return self._rect

    def update_data_indexed(self, marks: list, x_right: float, ts_to_idx, x_view) -> None:
        """Index-space LINE render — one horizontal level line per mark; see the class docstring."""
        vx0, vx1 = x_view
        self.picture = QtGui.QPicture()
        if not marks:
            self.label_pool.update([])
            self.prepareGeometryChange(); self.update()
            return
        p = QtGui.QPainter(self.picture)
        xs, ys, labels = [], [], []
        for m in marks:
            try:
                bi = ts_to_idx(float(m["birth"]))
            except (TypeError, ValueError, KeyError):
                continue
            if bi == -1:
                continue   # formed before the anchor -> out of scope
            active = m.get("active", True)
            end = m.get("end")
            # Bucket-CELL span: candles are centered at index i (half-width 0.5), so the line runs
            # from the birth bucket's LEFT cell-edge to the death bucket's RIGHT cell-edge — no
            # half-bucket float, no +1 overshoot. Active -> open to the live edge.
            x0 = float(bi) - 0.5
            if active or end is None:
                x1 = x_right + 0.5
            else:
                x1 = float(ts_to_idx(float(end))) + 0.5    # death bucket RIGHT cell-edge (stops AT death)
            if x1 <= vx0 or x0 >= vx1:
                continue
            x0c = max(x0, vx0); x1c = min(x1, vx1)
            if x1c <= x0c:
                x1c = x0c + 0.5
            price = float(m.get("price", m.get("plo", 0.0)))   # POC (fallback to plo for pre-redesign marks)
            rgb = RGB_ABSORB_BUY if m.get("side") == "BUY" else RGB_ABSORB_SELL
            col = QtGui.QColor(rgb[0], rgb[1], rgb[2])
            kap = float(m.get("kappa", 1.0))
            th = _absorb_thickness(kap)                        # LINE THICKNESS = relative strength
            line_col = QtGui.QColor(col)
            if not active:
                line_col.setAlphaF(0.85)                       # dead slightly dimmer; the line ENDING is the death
            pen = QtGui.QPen(line_col); pen.setCosmetic(True); pen.setWidthF(th)
            p.setPen(pen)
            p.drawLine(QtCore.QLineF(x0c, price, x1c, price))  # the iceberg = a horizontal LEVEL line at its POC
            # (Bug-1 fix) NO vertical "died here" end-cap — on a LINE the ending itself IS the death
            # signal; the line simply stops at the death bucket's right cell-edge.
            usd = float(m.get("absorbed_usd", 0.0))
            dollar = f"${usd / 1e6:.1f}M" if usd >= 1e6 else f"${usd / 1e3:.0f}k"
            txt = f"{dollar} (κ{kap:.1f})" if kap > 0 else dollar
            labels.append((x0c, price, txt, col))
            xs += [x0c, x1c]; ys += [price]
        p.end()
        self.label_pool.update(labels)
        if xs:
            ylo, yhi = min(ys) - _ABSORB_CAP_H, max(ys) + _ABSORB_CAP_H
            self._rect = QtCore.QRectF(min(xs), ylo, max(xs) - min(xs), max(0.02, yhi - ylo))
        self.prepareGeometryChange(); self.update()


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
        # Cached series for the cheap viewport re-cull (set_view) on manual pan/zoom:
        # rebuild ONLY the visible candles' QPicture from these without recomputing data.
        self._x, self._o, self._h, self._l, self._c, self._brushes = [], [], [], [], [], []
        self._pens = []          # per-candle flow-colored wick/border pens
        self._width = 0.8
        self._vx0, self._vx1 = float("-inf"), float("inf")

    def update_data(self, x: list, opens: list, highs: list, lows: list,
                    closes: list, brushes: list, pens: list, width: float = 0.8,
                    x0: float = None, x1: float = None) -> None:
        # Cache the series so set_view() can re-cull on pan/zoom without a recompute.
        self._x, self._o, self._h, self._l, self._c = x, opens, highs, lows, closes
        self._brushes, self._pens, self._width = brushes, pens, width
        if not x:
            self.picture = QtGui.QPicture(); self._rect = QtCore.QRectF()
            self.prepareGeometryChange(); self.update(); return
        # Bounds = FULL data extent (UNCHANGED behavior): Y-fit / autorange / follow see
        # every bucket exactly as before — ONLY the painted picture is culled (in _build_picture).
        half = width / 2.0
        lo_all, hi_all = min(lows), max(highs)
        span = (hi_all - lo_all) if hi_all > lo_all else 1.0
        self._rect = QtCore.QRectF(float(x[0]) - half, lo_all,
                                   float(x[-1]) - float(x[0]) + width, span)
        self._vx0 = float("-inf") if x0 is None else float(x0)
        self._vx1 = float("inf")  if x1 is None else float(x1)
        self._build_picture()
        self.prepareGeometryChange()
        self.informViewBoundsChanged()
        self.update()

    def set_view(self, x0: float, x1: float) -> None:
        """Cheap viewport re-cull (manual pan/zoom): rebuild ONLY the visible-range QPicture
        from the cached series — O(visible), no data recompute. The data bounds are unchanged,
        so we must NOT prepareGeometryChange / informViewBoundsChanged here (that would
        re-trigger autorange and fight the manual pan)."""
        if not self._x:
            return
        self._vx0, self._vx1 = float(x0), float(x1)
        self._build_picture()
        self.update()

    def _build_picture(self) -> None:
        x, o, h, l, c = self._x, self._o, self._h, self._l, self._c
        brushes, width = self._brushes, self._width
        half = width / 2.0
        x0, x1 = self._vx0, self._vx1
        self.picture = QtGui.QPicture()
        p = QtGui.QPainter(self.picture)
        for i in range(len(x)):
            xi = float(x[i])
            if xi < x0 - width or xi > x1 + width:   # CULL to the visible X viewport (+1 width margin)
                continue
            oo, hh, ll, cc = o[i], h[i], l[i], c[i]
            # Zero-range bucket (high==low -> O=H=L=C): ALL volume traded at one tick. The
            # honest mark is a flat NEUTRAL line at that price — the forced TICK/2 body would
            # imply a range that never existed (the §0.6 degenerate sibling of the zero-vector
            # churn lie). Vector/flow reads from the stats box + footprint; the POC dot
            # (separate, z6) sits at center. DIVERGES FROM the ranged doji below.
            if abs(hh - ll) < config.TICK_SIZE / 2.0:
                p.setPen(self._flat_pen)
                p.drawLine(QtCore.QPointF(xi - half, ll), QtCore.QPointF(xi + half, ll))
                continue
            # body bounds first, so the wicks stop AT the body (no line through the fill).
            top, bot = max(oo, cc), min(oo, cc)
            if top == bot:
                top += config.TICK_SIZE / 2.0   # ranged doji (open==close): sliver shows the level
            p.setPen(self._pens[i] if i < len(self._pens) else self._pen)   # flow-colored wick + border
            # wicks ONLY outside the body — upper (body top -> high) + lower (low -> body bottom).
            # The old single low->high wick crossed the body and showed through the semi-transparent
            # fill as an ugly center midline; splitting it keeps the wicks but clears the body.
            if hh > top:
                p.drawLine(QtCore.QPointF(xi, top), QtCore.QPointF(xi, hh))
            if bot > ll:
                p.drawLine(QtCore.QPointF(xi, ll), QtCore.QPointF(xi, bot))
            # body (per-candle dominance brush, neutral border)
            p.setBrush(brushes[i] if i < len(brushes) else QtCore.Qt.NoBrush)
            p.drawRect(QtCore.QRectF(xi - half, bot, width, top - bot))
        p.end()

    def paint(self, p, *args):
        p.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        return self._rect
