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


class RoundedTextItem(pg.TextItem):
    """A pg.TextItem whose background is a PADDED, ROUNDED pill (modern card look) instead of the default sharp box.
    Pass fill=/border= like pg.TextItem; the text (a child QGraphicsTextItem) paints itself on top. `pad`/`radius`
    size the pill; when there is no fill AND no border it draws exactly like a plain TextItem (just the text)."""

    def __init__(self, *args, pad: float = 5.0, radius: float = 7.0, **kwargs):
        super().__init__(*args, **kwargs)
        self._pad = float(pad)
        self._radius = float(radius)

    def boundingRect(self):
        return super().boundingRect().adjusted(-self._pad, -self._pad, self._pad, self._pad)

    def paint(self, p, *args):
        if self.fill.style() != QtCore.Qt.NoBrush or self.border.style() != QtCore.Qt.NoPen:
            p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            p.setPen(self.border)
            p.setBrush(self.fill)
            p.drawRoundedRect(self.boundingRect(), self._radius, self._radius)


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
            ti = RoundedTextItem(anchor=self.anchor, pad=2.5, radius=4.5)   # imbalance highlights render as rounded pills
            ti.textItem.setFont(self.font)
            ti.setZValue(self.z)
            self._plot.addItem(ti, ignoreBounds=True)
            self.items.append(ti)
            self._last.append(None)
        for i, it in enumerate(self.items):
            if i < len(specs):
                spec = specs[i]
                if spec != self._last[i]:        # only re-layout/reposition on an actual change
                    x, y, text, color = spec[0], spec[1], spec[2], spec[3]
                    bg = spec[4] if len(spec) > 4 else None   # optional per-cell background brush
                    it.setText(text, color=color)
                    it.fill = pg.mkBrush(bg) if bg is not None else pg.mkBrush(None)
                    it.setPos(x, y)
                    it.update()
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
        self._tc_map = None            # TIME-CANDLE mode: (t0, tf_secs) mapping x=index -> real clock (None = off)

    def set_scanner_active(self, on: bool) -> None:
        """Toggle between bucket-ordinal labels and chronological time labels.

        No layout rebuild — flipping the flag and invalidating the cached picture
        is enough for the next paint to re-format the visible ticks (Phase 2 §2).
        """
        if self._scanner_active != on:
            self._scanner_active = on
            self.picture = None
            self.update()

    def set_time_candle_map(self, t0: float, tf_secs: int) -> None:
        """TIME-CANDLE mode: candles are drawn at x = INDEX (0..N-1), but because clock candles are GAP-FILLED
        (one per interval) index k maps LINEARLY to clock time t0 + k*tf. Store that map so the ticks print the
        true host-local clock on an index coordinate. Overrides the scanner 'Idx: N' labels while set."""
        m = (float(t0), int(tf_secs))
        if self._tc_map != m:
            self._tc_map = m
            self.picture = None
            self.update()

    def clear_time_candle_map(self) -> None:
        if self._tc_map is not None:
            self._tc_map = None
            self.picture = None
            self.update()

    def tickStrings(self, values, scale, spacing):
        # Time-candle mode: x is a bucket INDEX; map index -> clock via the stored (t0, tf).
        if self._tc_map is not None:
            t0, tf = self._tc_map
            out = []
            for v in values:
                try:
                    dt = datetime.fromtimestamp(t0 + v * tf)
                    out.append(dt.strftime("%m-%d %H:%M" if tf >= 3600 else "%H:%M"))
                except (OSError, ValueError, OverflowError):
                    out.append("")
            return out
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


MIN_OB_PX = 7.0   # min DRAWN height (px) for an OB zone — thin bands stay visible at any zoom


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

    def update_data_indexed(self, obs: list, x_right: float, ts_to_idx, x_view, px_per_y: float) -> None:
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
            # min-render-height: a thin zone is sub-pixel at wide zoom -> floor the DRAWN height to
            # MIN_OB_PX, expanding symmetrically around the zone center (true top/bottom unchanged).
            dt, db = top, bottom
            if px_per_y > 0 and (top - bottom) * px_per_y < MIN_OB_PX:
                mid = (top + bottom) / 2.0; half = (MIN_OB_PX / px_per_y) / 2.0
                dt, db = mid + half, mid - half
            p.drawRect(QtCore.QRectF(x0, db, max(1.0, x1 - x0), dt - db))
            if self.show_tiers:
                tier_specs.append((x0, dt, f"x{vel:.1f}", col))
            xs += [x0, x1]
            ys += [dt, db]
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
_ABSORB_BAND_ALPHA = 0.08          # faint plo->phi break-range band BEHIND the POC line; additive
                                    # (overlaps darken ONLY where price-bands cluster = the signal).
                                    # One-line tune: drop to ~0.05 if 6× fully-overlapping reads muddy.


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
        """Index-space render — a faint filled break-range band (plo->phi) BEHIND a full-strength
        POC level line per mark. Two-pass: ALL bands first, then ALL lines, so every line stays
        crisp no matter how many bands stack behind it. See the class docstring."""
        vx0, vx1 = x_view
        self.picture = QtGui.QPicture()
        if not marks:
            self.label_pool.update([])
            self.prepareGeometryChange(); self.update()
            return
        p = QtGui.QPainter(self.picture)
        xs, ys, labels, line_draws = [], [], [], []
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
            plo = float(m.get("plo", price)); phi = float(m.get("phi", price))
            rgb = RGB_ABSORB_BUY if m.get("side") == "BUY" else RGB_ABSORB_SELL
            col = QtGui.QColor(rgb[0], rgb[1], rgb[2])
            kap = float(m.get("kappa", 1.0))
            th = _absorb_thickness(kap)                        # LINE THICKNESS = relative strength
            line_col = QtGui.QColor(col)
            if not active:
                line_col.setAlphaF(0.85)                       # dead slightly dimmer; the line ENDING is the death
            pen = QtGui.QPen(line_col); pen.setCosmetic(True); pen.setWidthF(th)
            # PASS 1: faint filled break-range band (plo->phi) drawn NOW, BEHIND every line. Additive
            # (semi-transparent fills blend) -> overlaps darken ONLY where price-bands actually
            # overlap = the absorption-clustering cue. Skip a zero-range (single-tick) cluster:
            # point detection draws as just the line, no band.
            if phi > plo:
                band = QtGui.QColor(rgb[0], rgb[1], rgb[2])
                band.setAlphaF(_ABSORB_BAND_ALPHA)
                p.fillRect(QtCore.QRectF(x0c, plo, x1c - x0c, phi - plo), band)
            line_draws.append((x0c, x1c, price, pen))          # POC line deferred to PASS 2 (on top)
            usd = float(m.get("absorbed_usd", 0.0))
            dollar = f"${usd / 1e6:.1f}M" if usd >= 1e6 else f"${usd / 1e3:.0f}k"
            txt = f"{dollar} (κ{kap:.1f})" if kap > 0 else dollar
            labels.append((x0c, price, txt, col))
            xs += [x0c, x1c]; ys += [price, plo, phi]
        # PASS 2: every POC line ON TOP of every band, full strength (render unchanged).
        for (lx0, lx1, lpy, lpen) in line_draws:
            p.setPen(lpen)
            p.drawLine(QtCore.QLineF(lx0, lpy, lx1, lpy))       # the iceberg = a horizontal LEVEL line at its POC
        p.end()
        self.label_pool.update(labels)
        if xs:
            ylo, yhi = min(ys) - _ABSORB_CAP_H, max(ys) + _ABSORB_CAP_H
            self._rect = QtCore.QRectF(min(xs), ylo, max(xs) - min(xs), max(0.02, yhi - ylo))
        self.prepareGeometryChange(); self.update()


# ---------------------------------------------------------------------------
# Mode-10 ABSORPTION ZONES — within a Magic Selection, a sustained consecutive run of heavy-bull (or
# heavy-bear) absorption buckets draws a filled price x time band: GREEN = bulls defended (soaked up
# selling), RED = bears defended; 15% opacity, no border. Label = side + absorbed volume. DESCRIPTIVE
# (where a side absorbed over a sustained run), NOT a prediction the level holds.
# ---------------------------------------------------------------------------
_RGB_ZONE_BULL = (46, 204, 113)    # absorption green (muted)
_RGB_ZONE_BEAR = (231, 76, 60)     # absorption red (muted)
_RGB_EFF_BULL = (0, 255, 128)      # effective-aggression NEON green (buyers forced up) — distinct from above
_RGB_EFF_BEAR = (255, 0, 96)       # effective-aggression NEON red (sellers forced down)
_ZONE_ALPHA = 0.15


class AbsorptionZoneLayer(pg.GraphicsObject):
    """Filled absorption-zone bands for the active selection. ``update_zones`` takes pre-computed bands
    ``(x0, x1, plo, phi, side, label)`` (run-detection done in region_state) and paints each as a 15%
    rect, no border, green (bull) / red (bear), labelled with the absorbed volume at its top-left."""

    def __init__(self, plot=None, rgb_bull=_RGB_ZONE_BULL, rgb_bear=_RGB_ZONE_BEAR):
        super().__init__()
        self.picture = QtGui.QPicture()
        self._rect = QtCore.QRectF()
        self._rgb_bull = rgb_bull     # parametrised so a parallel layer (e.g. effective-aggression) can
        self._rgb_bear = rgb_bear     # paint NEON green/red, visually distinct from the absorption bands
        self.label_pool = TextPool(anchor=(0, 1), font_size=8, z=72)
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

    def update_zones(self, zones: list) -> None:
        """zones: list of ``(x0, x1, x_right, plo, phi, side, label)`` in bucket-canvas coords. The REAL
        run [x0,x1] is a solid 15% fill; the PROJECTION [x1, x_right] (to the selection's right edge) is a
        lighter 6% fill + a dashed mid-price line — a visual projection of the defended level, NOT a claim
        the absorption happened at the right edge."""
        self.picture = QtGui.QPicture()
        if not zones:
            self.label_pool.update([])
            self._rect = QtCore.QRectF()
            self.prepareGeometryChange(); self.update()
            return
        p = QtGui.QPainter(self.picture)
        xs, ys, labels = [], [], []
        for (x0, x1, xr, plo, phi, side, label) in zones:
            rgb = self._rgb_bull if side == "bull" else self._rgb_bear
            h = max(phi - plo, 0.012)
            fill = QtGui.QColor(rgb[0], rgb[1], rgb[2]); fill.setAlphaF(_ZONE_ALPHA)
            p.fillRect(QtCore.QRectF(x0, plo, x1 - x0, h), fill)            # real run — solid, NO border
            if xr > x1 + 1e-6:                                             # rightward projection
                proj = QtGui.QColor(rgb[0], rgb[1], rgb[2]); proj.setAlphaF(_ZONE_ALPHA * 0.4)
                p.fillRect(QtCore.QRectF(x1, plo, xr - x1, h), proj)       # lighter band
                pen = QtGui.QPen(QtGui.QColor(rgb[0], rgb[1], rgb[2]))
                pen.setStyle(QtCore.Qt.DashLine); pen.setCosmetic(True); pen.setWidthF(1.0)
                p.setPen(pen)
                ymid = (plo + phi) / 2.0
                p.drawLine(QtCore.QPointF(x1, ymid), QtCore.QPointF(xr, ymid))   # dashed projected level
            labels.append((x0, phi, label, QtGui.QColor(rgb[0], rgb[1], rgb[2])))
            xs += [x0, xr]; ys += [plo, phi]
        p.end()
        self.label_pool.update(labels)
        self._rect = QtCore.QRectF(min(xs), min(ys), max(xs) - min(xs), max(0.02, max(ys) - min(ys)))
        self.prepareGeometryChange(); self.update()


# ---------------------------------------------------------------------------
# EXHAUSTION STRIP (Mode 10, SELECTION-scoped) — TWO smoothed lines (BLUE bull / RED bear gated exhaustion)
# in a PANEL at the bottom of the selection, on a fixed 0/50/100% scale, with GOLD diamonds where the lines
# CROSS (the exhausted side swaps). DESCRIPTIVE — where exhaustion sits and shifts within the selection,
# NOT a forecast. The cursor tooltip gives the raw per-bucket %.
# ---------------------------------------------------------------------------
_RGB_EXH_BULL = (41, 160, 255)     # blue  — bull exhaustion (buyers worn out)
_RGB_EXH_BEAR = (244, 67, 54)      # red   — bear exhaustion (sellers worn out)
_RGB_EXH_CROSS = (241, 196, 15)    # gold  — crossover marker (exhausted party changes)
_RGB_ER_BULL = (46, 204, 113)      # green — buyer effort/result  (effort-strip panel, was the E/R sparkline)
_RGB_ER_BEAR = (231, 76, 60)       # red   — seller effort/result
_RGB_ABS_BULL = (57, 255, 20)      # NEON green  — bull absorption (buyers soaking up sells)
_RGB_ABS_BEAR = (191, 0, 255)      # NEON purple — bear absorption (sellers soaking up buys)


class ExhaustionStripLayer(pg.GraphicsObject):
    """Selection-scoped two-line panel hanging below the box. ``update_data`` takes the bucket x-positions and
    the already-mapped y of each line (price coords inside the panel), the panel's x/y bounds, and the
    crossover points; it paints faint 0/50/100% guides, the bull + bear lines, and crossover diamonds. All
    geometry is pre-computed by the caller (selection-scoped, bounded) — the layer only draws. Colours are
    parametrised so the SAME layer serves the exhaustion panel (blue/red) and the eff-agg evolution panel
    (NEON green/red), exactly as :class:`AbsorptionZoneLayer` serves both the absorption and eff-agg zones."""

    def __init__(self, plot=None, rgb_bull=_RGB_EXH_BULL, rgb_bear=_RGB_EXH_BEAR, rgb_cross=_RGB_EXH_CROSS):
        super().__init__()
        self._rgb_bull = rgb_bull
        self._rgb_bear = rgb_bear
        self._rgb_cross = rgb_cross
        self.picture = QtGui.QPicture()
        self._rect = QtCore.QRectF()

    def paint(self, p, *args):
        p.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        return self._rect

    def clear(self):
        self.picture = QtGui.QPicture()
        self._rect = QtCore.QRectF()
        self.prepareGeometryChange(); self.update()

    def update_data(self, xs, bull_y, bear_y, x0, x1, y_bot, y_top, crosses, lock_idx=None):
        """xs: bucket-index x's; bull_y/bear_y: per-bucket panel-y (price); [x0,x1]x[y_bot,y_top]: panel
        bounds (y_bot=0%, y_top=100%); crosses: list of (x, y) gold crossover points. ``lock_idx`` (optional):
        index of the last fully-locked bucket — the line is SOLID up to it and DASHED + lower-opacity after
        (the still-settling tail); None = all solid."""
        self.picture = QtGui.QPicture()
        if not xs:
            self._rect = QtCore.QRectF(); self.prepareGeometryChange(); self.update(); return
        p = QtGui.QPainter(self.picture)
        p.setRenderHint(QtGui.QPainter.Antialiasing)

        def _path(ys, a, b):
            path = QtGui.QPainterPath()
            path.moveTo(QtCore.QPointF(float(xs[a]), ys[a]))
            for k in range(a + 1, b + 1):
                path.lineTo(QtCore.QPointF(float(xs[k]), ys[k]))
            return path

        def polyline(ys, rgb):
            li = (len(xs) - 1) if (lock_idx is None or lock_idx >= len(xs) - 1) else max(0, lock_idx)
            pen = QtGui.QPen(QtGui.QColor(*rgb)); pen.setCosmetic(True); pen.setWidthF(1.8)
            p.setPen(pen); p.setBrush(QtCore.Qt.NoBrush)
            p.drawPath(_path(ys, 0, li))                          # solid LOCKED part
            if li < len(xs) - 1:                                 # non-locked tail: dashed + lower opacity
                tpen = QtGui.QPen(QtGui.QColor(rgb[0], rgb[1], rgb[2], 110))
                tpen.setCosmetic(True); tpen.setWidthF(1.8); tpen.setStyle(QtCore.Qt.DashLine)
                p.setPen(tpen); p.drawPath(_path(ys, li, len(xs) - 1))
        polyline(bull_y, self._rgb_bull)
        polyline(bear_y, self._rgb_bear)
        dh = 0.11 * (y_top - y_bot); dw = 0.38
        gold = QtGui.QColor(*self._rgb_cross)
        tick = QtGui.QPen(gold); tick.setCosmetic(True); tick.setWidthF(1.0); tick.setStyle(QtCore.Qt.DashLine)
        for cx, cy in crosses:
            p.setPen(tick); p.drawLine(QtCore.QPointF(cx, y_bot), QtCore.QPointF(cx, cy))
            dia = QtGui.QPolygonF([QtCore.QPointF(cx, cy + dh), QtCore.QPointF(cx + dw, cy),
                                   QtCore.QPointF(cx, cy - dh), QtCore.QPointF(cx - dw, cy)])
            p.setPen(QtCore.Qt.NoPen); p.setBrush(gold); p.drawPolygon(dia)
        p.end()
        self._rect = QtCore.QRectF(x0, y_bot, max(1.0, x1 - x0), max(1e-6, y_top - y_bot))
        self.prepareGeometryChange(); self.update()


class PanelSeparatorLayer(pg.GraphicsObject):
    """Minimalist dividers between the stacked selection panels: one thin hairline per gap, FADING to
    transparent at both ends (centre-weighted) so it reads as a clean separator on the dark canvas and stays
    visually distinct from the panels' dotted internal guides. The caller passes the gap-midline y's."""

    def __init__(self, plot=None):
        super().__init__()
        self.picture = QtGui.QPicture()
        self._rect = QtCore.QRectF()

    def paint(self, p, *args):
        p.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        return self._rect

    def update_data(self, x0: float, x1: float, ys: list) -> None:
        self.picture = QtGui.QPicture()
        if not ys:
            self._rect = QtCore.QRectF(); self.prepareGeometryChange(); self.update(); return
        p = QtGui.QPainter(self.picture)
        p.setRenderHint(QtGui.QPainter.Antialiasing, False)   # crisp horizontal hairline; no AA-seam between segs
        # Draw each divider as N solid segments whose alpha fades edge->centre->edge (a cosmetic pen with a
        # gradient BRUSH renders unreliably in Qt, so we approximate the fade with constant-alpha segments).
        SEG = 64
        span = x1 - x0
        for y in ys:
            for s in range(SEG):
                tm = (s + 0.5) / SEG
                a = int(round(45 + 165 * (1.0 - abs(2.0 * tm - 1.0))))   # edge ~45 -> centre ~210
                pen = QtGui.QPen(QtGui.QColor(176, 184, 199, a)); pen.setCosmetic(True); pen.setWidthF(1.0)
                p.setPen(pen)
                xa = x0 + (s / SEG) * span; xb = x0 + ((s + 1) / SEG) * span
                p.drawLine(QtCore.QPointF(xa, y), QtCore.QPointF(xb, y))
        p.end()
        self._rect = QtCore.QRectF(x0, min(ys), max(1.0, span), max(1e-6, max(ys) - min(ys)))
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
                # Span the FULL interval (not just the body width) so a run of no-trade (flat) candles — common on 1m
                # clock candles in a quiet stretch — connects into a CONTINUOUS carry-forward line instead of
                # disconnected ticks that read as "gaps".
                p.drawLine(QtCore.QPointF(xi - 0.5, ll), QtCore.QPointF(xi + 0.5, ll))
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


class WhiskerBarItem(pg.GraphicsObject):
    """Volume-quantile whisker bars ('W' render mode) — ONE batched QPicture for all visible bars,
    mirroring BucketCandleItem's architecture (cached series + viewport cull + cheap set_view re-cull;
    no per-bar QGraphicsItems). Encoding per bar: box = the ladder's volume acceptance zone
    [C>=25%V, C>=75%V] with the volume-weighted median line inside; thin whiskers to high/low
    (rejection tails); OHLC-style OPEN tick on the left edge / CLOSE tick on the right (~40% width);
    box fill = the SAME per-candle dominance brush and border/whiskers/notches = the SAME flow-colored
    wick pens the normal candle chart uses (visual language identical across render modes)."""

    def __init__(self):
        super().__init__()
        self.picture = QtGui.QPicture()
        self._rect = QtCore.QRectF()
        self._wpen = QtGui.QPen(QtGui.QColor("#888888")); self._wpen.setCosmetic(True)   # fallback pen
        self._mpen = QtGui.QPen(QtGui.QColor("#ffffff")); self._mpen.setCosmetic(True); self._mpen.setWidth(2)
        # open/close notches: fixed GREEN/RED 2px by bar direction (operator pref — consistent everywhere)
        self._ocpen_g = QtGui.QPen(QtGui.QColor(*config.RGB_GREEN_STD)); self._ocpen_g.setCosmetic(True); self._ocpen_g.setWidth(2)
        self._ocpen_r = QtGui.QPen(QtGui.QColor(*config.RGB_RED_STD)); self._ocpen_r.setCosmetic(True); self._ocpen_r.setWidth(2)
        self._x = []; self._qlo = []; self._qmed = []; self._qhi = []
        self._h = []; self._l = []; self._o = []; self._c = []
        self._brushes = []; self._pens = []
        self._width = 0.8
        self._vx0, self._vx1 = float("-inf"), float("inf")

    def update_data(self, x, qlo, qmed, qhi, highs, lows, opens, closes,
                    brushes=None, pens=None, width=0.8, x0=None, x1=None, show_med=True):
        self._x, self._qlo, self._qmed, self._qhi = x, qlo, qmed, qhi
        self._h, self._l, self._o, self._c = highs, lows, opens, closes
        self._brushes = brushes or []; self._pens = pens or []
        self._show_med = show_med                 # median follows the POC toggle ('P'), like the gold dot
        self._width = width
        if not x:
            self.picture = QtGui.QPicture(); self._rect = QtCore.QRectF()
            self.prepareGeometryChange(); self.update(); return
        half = width / 2.0
        lo_all, hi_all = min(lows), max(highs)
        span = (hi_all - lo_all) if hi_all > lo_all else 1.0
        self._rect = QtCore.QRectF(float(x[0]) - half, lo_all,
                                   float(x[-1]) - float(x[0]) + width, span)
        self._vx0 = float("-inf") if x0 is None else float(x0)
        self._vx1 = float("inf") if x1 is None else float(x1)
        self._build_picture()
        self.prepareGeometryChange()
        self.informViewBoundsChanged()
        self.update()

    def set_view(self, x0, x1):
        """Cheap viewport re-cull on manual pan/zoom (no geometry-change signals — see BucketCandleItem)."""
        if not self._x:
            return
        self._vx0, self._vx1 = float(x0), float(x1)
        self._build_picture()
        self.update()

    def _build_picture(self):
        x, qlo, qmed, qhi = self._x, self._qlo, self._qmed, self._qhi
        h, l, o, c = self._h, self._l, self._o, self._c
        width = self._width; half = width / 2.0; tick_w = width * 0.4
        x0v, x1v = self._vx0, self._vx1
        self.picture = QtGui.QPicture()
        p = QtGui.QPainter(self.picture)
        for i in range(len(x)):
            xi = float(x[i])
            if xi < x0v - width or xi > x1v + width:
                continue
            blo, bmed, bhi = qlo[i], qmed[i], qhi[i]
            if blo != blo or bmed != bmed or bhi != bhi:      # NaN = degenerate ladder -> candle item draws it
                continue
            bar_pen = self._pens[i] if i < len(self._pens) else self._wpen  # SAME flow pen as candles
            # whiskers: thin lines beyond the box only (rejection tails)
            p.setPen(bar_pen)
            if h[i] > bhi:
                p.drawLine(QtCore.QPointF(xi, bhi), QtCore.QPointF(xi, h[i]))
            if blo > l[i]:
                p.drawLine(QtCore.QPointF(xi, l[i]), QtCore.QPointF(xi, blo))
            # acceptance box (25-75% cumulative volume): SAME dominance brush as the candle body
            p.setBrush(self._brushes[i] if i < len(self._brushes) else QtCore.Qt.NoBrush)
            bot, top = min(blo, bhi), max(blo, bhi)
            if top - bot < config.TICK_SIZE / 2.0:            # one-tick acceptance -> sliver stays visible
                top = bot + config.TICK_SIZE / 2.0
            p.drawRect(QtCore.QRectF(xi - half, bot, width, top - bot))
            # volume-weighted median line inside the box — only when the POC layer ('P') is on
            if getattr(self, "_show_med", True):
                p.setPen(self._mpen)
                p.drawLine(QtCore.QPointF(xi - half, bmed), QtCore.QPointF(xi + half, bmed))
            # OHLC notches ATTACHED to the center wick line: OPEN tick extends LEFT from center,
            # CLOSE tick extends RIGHT from center (~40% width each). Fixed GREEN/RED 3px by direction.
            p.setPen(self._ocpen_g if c[i] >= o[i] else self._ocpen_r)
            p.drawLine(QtCore.QPointF(xi - tick_w, o[i]), QtCore.QPointF(xi, o[i]))
            p.drawLine(QtCore.QPointF(xi, c[i]), QtCore.QPointF(xi + tick_w, c[i]))
        p.end()

    def paint(self, p, *args):
        p.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        return self._rect


_FPC_BUY = (46, 204, 113); _FPC_SELL = (231, 76, 60)          # footprint-candle bar colours (match the live pane)
_FPC_BUY_HOT = (0, 255, 127); _FPC_SELL_HOT = (255, 45, 70)   # imbalanced rows -> neon


class FootprintCandleItem(pg.GraphicsObject):
    """Per-candle FOOTPRINT render mode (the 3rd 'W' stage): each bucket is drawn as a mini centred volume profile
    — SELL (red) bars extend LEFT of the candle centre, BUY (green) RIGHT, one row per price level, each scaled to
    fill the candle's half-width (so every candle's profile is legible). Rows whose buy/sell volume is >= mult x the
    candle's AVERAGE per-level volume go NEON (imbalance), exactly like the live footprint pane. A faint grey wick
    marks the high-low range. Same batched QPicture + viewport-cull + set_view architecture as BucketCandleItem, so
    a ladder-less bucket paints nothing here and is drawn by the normal candle item as a fallback."""

    def __init__(self):
        super().__init__()
        self.picture = QtGui.QPicture()
        self._rect = QtCore.QRectF()
        self._wick = QtGui.QPen(QtGui.QColor(136, 136, 136, 130)); self._wick.setCosmetic(True)
        self._x = []; self._levels = []; self._h = []; self._l = []
        self._width = 0.8; self._mult = 1.0
        self._vx0, self._vx1 = float("-inf"), float("inf")

    def update_data(self, x, levels_list, highs, lows, mult=1.0, width=0.8, x0=None, x1=None):
        self._x = x; self._levels = levels_list; self._h = highs; self._l = lows
        self._mult = mult; self._width = width
        if not x:
            self.picture = QtGui.QPicture(); self._rect = QtCore.QRectF()
            self.prepareGeometryChange(); self.update(); return
        half = width / 2.0
        lo_all, hi_all = min(lows), max(highs)
        span = (hi_all - lo_all) if hi_all > lo_all else 1.0
        self._rect = QtCore.QRectF(float(x[0]) - half, lo_all,
                                   float(x[-1]) - float(x[0]) + width, span)
        self._vx0 = float("-inf") if x0 is None else float(x0)
        self._vx1 = float("inf") if x1 is None else float(x1)
        self._build_picture()
        self.prepareGeometryChange()
        self.informViewBoundsChanged()
        self.update()

    def set_view(self, x0, x1):
        """Cheap viewport re-cull on manual pan/zoom (no geometry-change signals — see BucketCandleItem)."""
        if not self._x:
            return
        self._vx0, self._vx1 = float(x0), float(x1)
        self._build_picture()
        self.update()

    def _build_picture(self):
        x, levels, h, l = self._x, self._levels, self._h, self._l
        width = self._width; half = width / 2.0; mult = self._mult
        x0v, x1v = self._vx0, self._vx1
        self.picture = QtGui.QPicture()
        p = QtGui.QPainter(self.picture)
        for i in range(len(x)):
            xi = float(x[i])
            if xi < x0v - width or xi > x1v + width:      # CULL to the visible X viewport (+1 width margin)
                continue
            p.setPen(self._wick)                          # faint high-low range wick at the candle centre
            p.drawLine(QtCore.QPointF(xi, float(l[i])), QtCore.QPointF(xi, float(h[i])))
            p.setPen(QtCore.Qt.NoPen)
            lv = levels[i] if i < len(levels) else None
            if not lv:
                continue
            rows = []
            for ps, v in lv.items():
                try:
                    pr = float(ps)
                except (TypeError, ValueError):
                    continue
                bb = float(v.get("b", 0.0)); ss = float(v.get("s", 0.0))
                if bb + ss > 0:
                    rows.append((pr, bb, ss))
            if not rows:
                continue
            rows.sort(key=lambda r: r[0])
            n = len(rows)
            mx = max(max(bb, ss) for _, bb, ss in rows) or 1.0
            tb = sum(bb for _, bb, _ in rows); ts = sum(ss for _, _, ss in rows)
            bthr = mult * (tb / n) if tb > 0 else None
            sthr = mult * (ts / n) if ts > 0 else None
            if n > 1:
                gaps = sorted(rows[k + 1][0] - rows[k][0] for k in range(n - 1))
                bh = gaps[len(gaps) // 2] or ((rows[-1][0] - rows[0][0]) / n)
            else:
                bh = config.TICK_SIZE
            for pr, bb, ss in rows:
                y = pr - bh / 2.0
                if ss > 0:
                    hot = sthr is not None and ss >= sthr
                    col = QtGui.QColor(*(_FPC_SELL_HOT if hot else _FPC_SELL)); col.setAlphaF(0.92 if hot else 0.58)
                    w = (ss / mx) * half
                    p.setBrush(col); p.drawRect(QtCore.QRectF(xi - w, y, w, bh))
                if bb > 0:
                    hot = bthr is not None and bb >= bthr
                    col = QtGui.QColor(*(_FPC_BUY_HOT if hot else _FPC_BUY)); col.setAlphaF(0.92 if hot else 0.58)
                    w = (bb / mx) * half
                    p.setBrush(col); p.drawRect(QtCore.QRectF(xi, y, w, bh))
        p.end()

    def paint(self, p, *args):
        p.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        return self._rect


class DeltaCandleItem(pg.GraphicsObject):
    """Per-candle DELTA-FOOTPRINT render mode (the 4th 'W' stage): each bucket is a mini centred DELTA profile —
    one bar per price level showing the NET delta (buy - sell). Net-buy levels draw GREEN to the RIGHT of centre,
    net-sell RED to the LEFT, each scaled to the candle's largest |delta| so the profile fills its width. Levels
    whose |delta| is >= mult x the candle's AVERAGE |delta| go NEON (the dominant aggression). A faint grey wick
    marks the high-low range. Same batched QPicture + viewport-cull + set_view architecture as FootprintCandleItem;
    a ladder-less bucket paints nothing here (the normal candle item draws it as a fallback)."""

    def __init__(self):
        super().__init__()
        self.picture = QtGui.QPicture()
        self._rect = QtCore.QRectF()
        self._wick = QtGui.QPen(QtGui.QColor(136, 136, 136, 130)); self._wick.setCosmetic(True)
        self._x = []; self._levels = []; self._h = []; self._l = []
        self._width = 0.8; self._mult = 1.0
        self._vx0, self._vx1 = float("-inf"), float("inf")

    def update_data(self, x, levels_list, highs, lows, mult=1.0, width=0.8, x0=None, x1=None):
        self._x = x; self._levels = levels_list; self._h = highs; self._l = lows
        self._mult = mult; self._width = width
        if not x:
            self.picture = QtGui.QPicture(); self._rect = QtCore.QRectF()
            self.prepareGeometryChange(); self.update(); return
        half = width / 2.0
        lo_all, hi_all = min(lows), max(highs)
        span = (hi_all - lo_all) if hi_all > lo_all else 1.0
        self._rect = QtCore.QRectF(float(x[0]) - half, lo_all,
                                   float(x[-1]) - float(x[0]) + width, span)
        self._vx0 = float("-inf") if x0 is None else float(x0)
        self._vx1 = float("inf") if x1 is None else float(x1)
        self._build_picture()
        self.prepareGeometryChange()
        self.informViewBoundsChanged()
        self.update()

    def set_view(self, x0, x1):
        """Cheap viewport re-cull on manual pan/zoom (no geometry-change signals — see BucketCandleItem)."""
        if not self._x:
            return
        self._vx0, self._vx1 = float(x0), float(x1)
        self._build_picture()
        self.update()

    def _build_picture(self):
        x, levels, h, l = self._x, self._levels, self._h, self._l
        width = self._width; half = width / 2.0; mult = self._mult
        x0v, x1v = self._vx0, self._vx1
        self.picture = QtGui.QPicture()
        p = QtGui.QPainter(self.picture)
        for i in range(len(x)):
            xi = float(x[i])
            if xi < x0v - width or xi > x1v + width:      # CULL to the visible X viewport (+1 width margin)
                continue
            p.setPen(self._wick)                          # faint high-low range wick at the candle centre
            p.drawLine(QtCore.QPointF(xi, float(l[i])), QtCore.QPointF(xi, float(h[i])))
            p.setPen(QtCore.Qt.NoPen)
            lv = levels[i] if i < len(levels) else None
            if not lv:
                continue
            rows = []          # (price, delta) per level; skip balanced levels (delta == 0)
            for ps, v in lv.items():
                try:
                    pr = float(ps)
                except (TypeError, ValueError):
                    continue
                d = float(v.get("b", 0.0)) - float(v.get("s", 0.0))
                if d != 0.0:
                    rows.append((pr, d))
            if not rows:
                continue
            rows.sort(key=lambda r: r[0])
            n = len(rows)
            mx = max(abs(d) for _, d in rows) or 1.0
            avg = (sum(abs(d) for _, d in rows) / n) if n else 0.0
            thr = mult * avg if avg > 0 else None
            prices = [r[0] for r in rows]
            if n > 1:
                gaps = sorted(prices[k + 1] - prices[k] for k in range(n - 1))
                bh = gaps[len(gaps) // 2] or ((prices[-1] - prices[0]) / n)
            else:
                bh = config.TICK_SIZE
            for pr, d in rows:
                y = pr - bh / 2.0
                hot = thr is not None and abs(d) >= thr
                w = (abs(d) / mx) * half
                if d > 0:
                    col = QtGui.QColor(*(_FPC_BUY_HOT if hot else _FPC_BUY)); col.setAlphaF(0.92 if hot else 0.58)
                    p.setBrush(col); p.drawRect(QtCore.QRectF(xi, y, w, bh))
                else:
                    col = QtGui.QColor(*(_FPC_SELL_HOT if hot else _FPC_SELL)); col.setAlphaF(0.92 if hot else 0.58)
                    p.setBrush(col); p.drawRect(QtCore.QRectF(xi - w, y, w, bh))
        p.end()

    def paint(self, p, *args):
        p.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        return self._rect


# 4-vector force colours (match the 4h VP force histogram): opL green / opS red / clL magenta / clS cyan
_FC_OPL = (40, 230, 90); _FC_OPS = (255, 55, 70); _FC_CLL = (235, 70, 255); _FC_CLS = (0, 210, 255)


class ForceCandleItem(pg.GraphicsObject):
    """Per-candle FORCE-FOOTPRINT render mode (the 5th 'W' stage): the footprint candle, but each side split into
    its two order-flow FORCES via the bucket's own ratios (exactly as the 4h VP force histogram does) so a plain
    red/green bar becomes four colours telling you WHAT happened at each level. A level's BUY volume splits into
    opL (OPEN-long, GREEN) + clS (short-COVER, CYAN), stacked RIGHT; its SELL volume into opS (OPEN-short, RED) +
    clL (long-LIQUIDATION, MAGENTA), stacked LEFT — opening force nearest the centre, closing force outward. Each
    candle is scaled to its largest level so the profile fills its width; a faint grey wick marks the range. Same
    batched QPicture + viewport-cull + set_view architecture; a ladder-less bucket paints nothing (candle fallback)."""

    def __init__(self):
        super().__init__()
        self.picture = QtGui.QPicture()
        self._rect = QtCore.QRectF()
        self._wick = QtGui.QPen(QtGui.QColor(136, 136, 136, 130)); self._wick.setCosmetic(True)
        self._x = []; self._levels = []; self._forces = []; self._h = []; self._l = []
        self._width = 0.8
        self._vx0, self._vx1 = float("-inf"), float("inf")

    def update_data(self, x, levels_list, forces_list, highs, lows, width=0.8, x0=None, x1=None):
        self._x = x; self._levels = levels_list; self._forces = forces_list; self._h = highs; self._l = lows
        self._width = width
        if not x:
            self.picture = QtGui.QPicture(); self._rect = QtCore.QRectF()
            self.prepareGeometryChange(); self.update(); return
        half = width / 2.0
        lo_all, hi_all = min(lows), max(highs)
        span = (hi_all - lo_all) if hi_all > lo_all else 1.0
        self._rect = QtCore.QRectF(float(x[0]) - half, lo_all,
                                   float(x[-1]) - float(x[0]) + width, span)
        self._vx0 = float("-inf") if x0 is None else float(x0)
        self._vx1 = float("inf") if x1 is None else float(x1)
        self._build_picture()
        self.prepareGeometryChange()
        self.informViewBoundsChanged()
        self.update()

    def set_view(self, x0, x1):
        """Cheap viewport re-cull on manual pan/zoom (no geometry-change signals — see BucketCandleItem)."""
        if not self._x:
            return
        self._vx0, self._vx1 = float(x0), float(x1)
        self._build_picture()
        self.update()

    def _build_picture(self):
        x, levels, forces, h, l = self._x, self._levels, self._forces, self._h, self._l
        width = self._width; half = width / 2.0
        x0v, x1v = self._vx0, self._vx1
        _c_opl = QtGui.QColor(*_FC_OPL); _c_ops = QtGui.QColor(*_FC_OPS)
        _c_cll = QtGui.QColor(*_FC_CLL); _c_cls = QtGui.QColor(*_FC_CLS)
        for _c in (_c_opl, _c_ops, _c_cll, _c_cls):
            _c.setAlphaF(0.72)
        self.picture = QtGui.QPicture()
        p = QtGui.QPainter(self.picture)
        for i in range(len(x)):
            xi = float(x[i])
            if xi < x0v - width or xi > x1v + width:      # CULL to the visible X viewport (+1 width margin)
                continue
            p.setPen(self._wick)                          # faint high-low range wick at the candle centre
            p.drawLine(QtCore.QPointF(xi, float(l[i])), QtCore.QPointF(xi, float(h[i])))
            p.setPen(QtCore.Qt.NoPen)
            lv = levels[i] if i < len(levels) else None
            if not lv:
                continue
            oL, oS, cL, cS = forces[i] if i < len(forces) else (0.0, 0.0, 0.0, 0.0)
            bd = oL + cS; sd = oS + cL                    # buy drivers (open-long + short-cover) / sell drivers
            fOL = (oL / bd) if bd > 0 else 0.5; fCS = 1.0 - fOL   # buy volume splits opL / clS
            fOS = (oS / sd) if sd > 0 else 0.5; fCL = 1.0 - fOS   # sell volume splits opS / clL
            rows = []
            for ps, v in lv.items():
                try:
                    pr = float(ps)
                except (TypeError, ValueError):
                    continue
                bb = float(v.get("b", 0.0)); ss = float(v.get("s", 0.0))
                if bb + ss > 0:
                    rows.append((pr, bb, ss))
            if not rows:
                continue
            rows.sort(key=lambda r: r[0])
            n = len(rows)
            mx = max(max(bb, ss) for _, bb, ss in rows) or 1.0
            if n > 1:
                gaps = sorted(rows[k + 1][0] - rows[k][0] for k in range(n - 1))
                bh = gaps[len(gaps) // 2] or ((rows[-1][0] - rows[0][0]) / n)
            else:
                bh = config.TICK_SIZE
            sc = half / mx                                # volume -> half-width scale
            for pr, bb, ss in rows:
                y = pr - bh / 2.0
                if bb > 0:                                # BUY -> opL (green, inner) + clS (cyan, outer), rightward
                    w1 = bb * fOL * sc; w2 = bb * fCS * sc
                    if w1 > 0:
                        p.setBrush(_c_opl); p.drawRect(QtCore.QRectF(xi, y, w1, bh))
                    if w2 > 0:
                        p.setBrush(_c_cls); p.drawRect(QtCore.QRectF(xi + w1, y, w2, bh))
                if ss > 0:                                # SELL -> opS (red, inner) + clL (magenta, outer), leftward
                    w1 = ss * fOS * sc; w2 = ss * fCL * sc
                    if w1 > 0:
                        p.setBrush(_c_ops); p.drawRect(QtCore.QRectF(xi - w1, y, w1, bh))
                    if w2 > 0:
                        p.setBrush(_c_cll); p.drawRect(QtCore.QRectF(xi - w1 - w2, y, w2, bh))
        p.end()

    def paint(self, p, *args):
        p.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        return self._rect


class DeltaForceCandleItem(pg.GraphicsObject):
    """Per-candle DELTA-BY-FORCE render mode (the 6th 'W' stage): the delta candle's shape (one signed net buy-sell
    bar per level, right = net buy / left = net sell) but each bar is COLOURED by the single force that dominated at
    that level — opL green (new longs) / opS red (new shorts) / clL magenta (long liquidation) / clS cyan (short
    cover). A level's buy volume is split opL/clS and its sell volume opS/clL by the bucket's own force ratios (as in
    the force candle); the largest of those four picks the colour. Net-flat levels draw nothing. Faint grey wick for
    range. Same batched QPicture + viewport-cull + set_view; ladder-less buckets fall back to normal candles."""

    def __init__(self):
        super().__init__()
        self.picture = QtGui.QPicture()
        self._rect = QtCore.QRectF()
        self._wick = QtGui.QPen(QtGui.QColor(136, 136, 136, 130)); self._wick.setCosmetic(True)
        self._x = []; self._levels = []; self._forces = []; self._h = []; self._l = []
        self._width = 0.8
        self._vx0, self._vx1 = float("-inf"), float("inf")

    def update_data(self, x, levels_list, forces_list, highs, lows, width=0.8, x0=None, x1=None):
        self._x = x; self._levels = levels_list; self._forces = forces_list; self._h = highs; self._l = lows
        self._width = width
        if not x:
            self.picture = QtGui.QPicture(); self._rect = QtCore.QRectF()
            self.prepareGeometryChange(); self.update(); return
        half = width / 2.0
        lo_all, hi_all = min(lows), max(highs)
        span = (hi_all - lo_all) if hi_all > lo_all else 1.0
        self._rect = QtCore.QRectF(float(x[0]) - half, lo_all,
                                   float(x[-1]) - float(x[0]) + width, span)
        self._vx0 = float("-inf") if x0 is None else float(x0)
        self._vx1 = float("inf") if x1 is None else float(x1)
        self._build_picture()
        self.prepareGeometryChange()
        self.informViewBoundsChanged()
        self.update()

    def set_view(self, x0, x1):
        """Cheap viewport re-cull on manual pan/zoom (no geometry-change signals — see BucketCandleItem)."""
        if not self._x:
            return
        self._vx0, self._vx1 = float(x0), float(x1)
        self._build_picture()
        self.update()

    def _build_picture(self):
        x, levels, forces, h, l = self._x, self._levels, self._forces, self._h, self._l
        width = self._width; half = width / 2.0
        x0v, x1v = self._vx0, self._vx1
        _cols = (QtGui.QColor(*_FC_OPL), QtGui.QColor(*_FC_OPS), QtGui.QColor(*_FC_CLL), QtGui.QColor(*_FC_CLS))
        for _c in _cols:
            _c.setAlphaF(0.82)
        self.picture = QtGui.QPicture()
        p = QtGui.QPainter(self.picture)
        for i in range(len(x)):
            xi = float(x[i])
            if xi < x0v - width or xi > x1v + width:      # CULL to the visible X viewport (+1 width margin)
                continue
            p.setPen(self._wick)                          # faint high-low range wick at the candle centre
            p.drawLine(QtCore.QPointF(xi, float(l[i])), QtCore.QPointF(xi, float(h[i])))
            p.setPen(QtCore.Qt.NoPen)
            lv = levels[i] if i < len(levels) else None
            if not lv:
                continue
            oL, oS, cL, cS = forces[i] if i < len(forces) else (0.0, 0.0, 0.0, 0.0)
            bd = oL + cS; sd = oS + cL
            fOL = (oL / bd) if bd > 0 else 0.5; fCS = 1.0 - fOL   # buy volume splits opL / clS
            fOS = (oS / sd) if sd > 0 else 0.5; fCL = 1.0 - fOS   # sell volume splits opS / clL
            rows = []          # (price, delta, dom_colour_index) per level; skip net-flat levels
            for ps, v in lv.items():
                try:
                    pr = float(ps)
                except (TypeError, ValueError):
                    continue
                bb = float(v.get("b", 0.0)); ss = float(v.get("s", 0.0))
                d = bb - ss
                if d == 0.0:
                    continue
                # colour = the dominant of ALL FOUR forces AT THIS LEVEL (bb/ss vary per level, so this varies too):
                # a net-buy bar can still be opS-red if opS is the biggest force at that price. 0 opL/1 opS/2 clL/3 clS
                comp = (bb * fOL, ss * fOS, ss * fCL, bb * fCS)
                rows.append((pr, d, max(range(4), key=lambda k: comp[k])))
            if not rows:
                continue
            rows.sort(key=lambda r: r[0])
            n = len(rows)
            mx = max(abs(d) for _, d, _ in rows) or 1.0
            prices = [r[0] for r in rows]
            if n > 1:
                gaps = sorted(prices[k + 1] - prices[k] for k in range(n - 1))
                bh = gaps[len(gaps) // 2] or ((prices[-1] - prices[0]) / n)
            else:
                bh = config.TICK_SIZE
            sc = half / mx
            for pr, d, dom in rows:
                y = pr - bh / 2.0
                w = abs(d) * sc
                p.setBrush(_cols[dom])
                if d > 0:
                    p.drawRect(QtCore.QRectF(xi, y, w, bh))       # net buy -> right
                else:
                    p.drawRect(QtCore.QRectF(xi - w, y, w, bh))   # net sell -> left
        p.end()

    def paint(self, p, *args):
        p.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        return self._rect
