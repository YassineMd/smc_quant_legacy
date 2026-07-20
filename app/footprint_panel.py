"""Live FOOTPRINT side pane — the currently FORMING bucket's footprint ladder, refreshed as it develops.

A narrow PyQtGraph pane docked to the RIGHT of the main chart (resizable by dragging the splitter handle, the
same feel as the VPIN pane). One row per price level (binned for readability) of the forming bucket: SELL volume
(red) extends LEFT of the centre line, BUY volume (green) extends RIGHT, with the volume as WHITE-chip / black-text
labels at the centre and imbalance / POC highlighting. A dashed line tracks the current price (like the chart), the
right axis is 2-decimal price, the bottom axis is |volume|. It keeps its OWN price (Y) scale, auto-fit to the
forming candle's range, so the whole developing footprint is readable WITHOUT zooming the main chart. It shares the
chart's crosshair by PRICE (horizontal line + right-axis price tag) plus its own bottom-axis volume tag. Double-click
toggles the number chips. Fed once per frame from snapshot['active_bucket'].
"""
from __future__ import annotations

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from .chart_widgets import RoundedTextItem

_MAX_ROWS = 30            # cap the drawn rows (bin finer ladders) so the number chips never crowd
_NUM_FONT_PT = 12         # bigger, readable volume numbers
_IMB_BUY_BG = (0, 153, 255)     # electric-blue pill for an imbalanced BUY level (black text)
_IMB_SELL_BG = (255, 140, 0)    # orange pill for an imbalanced SELL level (black text)


def _kfmt(v: float) -> str:
    if v >= 1_000_000:
        return f"{v / 1e6:.1f}M"
    if v >= 1000:
        return f"{v / 1000:.1f}K"
    return f"{v:.0f}"


def profile_skewness(levels) -> "float | None":
    """Volume-weighted skewness of a bucket's volume-by-price profile, in PROFILE-READ convention.

    Each price level contributes weight = buy+sell volume at that price; we take the population
    skewness m3 / m2**1.5 of the volume-weighted price distribution and FLIP ITS SIGN so the number
    reads the way you look at the ladder:
        >0  volume mass sits at the HIGHER prices, thin tail reaching DOWN  (top-heavy);
        <0  volume mass sits at the LOWER prices,  thin tail reaching UP    (bottom-heavy);
        ~0  the profile is roughly symmetric about its volume-weighted mean price.
    (NOTE: this is the NEGATIVE of the textbook Fisher-Pearson coefficient, on purpose — textbook >0
    means the tail is up / mass is down, which is the opposite of how the desk reads a profile.)
    None when there are fewer than 3 priced levels (a 2-bin histogram has no meaningful skew shape —
    its coefficient is a degenerate function of the weight ratio and explodes) or the profile has no
    price dispersion (all volume at one level). SHARED by the live footprint pane and the bucket stats
    box so both read the exact same number off the exact same ``levels`` dict."""
    pts = []
    W = 0.0
    for ps, v in (levels or {}).items():
        try:
            p = float(ps)
        except (TypeError, ValueError):
            continue
        w = float(v.get("b", 0.0)) + float(v.get("s", 0.0))
        if w > 0.0:
            pts.append((p, w)); W += w
    if len(pts) < 3 or W <= 0.0:
        return None
    mean = sum(p * w for p, w in pts) / W
    m2 = sum(w * (p - mean) ** 2 for p, w in pts) / W
    if m2 <= 0.0:
        return None
    m3 = sum(w * (p - mean) ** 3 for p, w in pts) / W
    return -(m3 / (m2 ** 1.5))          # sign flipped -> +ve = mass HIGH (see docstring)


def skew_read(sk) -> tuple:
    """Plain-language read of a profile-convention skew (from ``profile_skewness``):
    returns (word, notable). 'high' = mass at higher prices, 'low' = mass at lower prices, 'flat' =
    roughly symmetric (|skew| < 0.5); '--' when undefined. ``notable`` is True for high/low (|skew|
    >= 0.5) so callers can highlight only a genuinely lopsided profile. Keeps both readouts identical."""
    if sk is None:
        return ("--", False)
    if sk >= 0.5:
        return ("high", True)
    if sk <= -0.5:
        return ("low", True)
    return ("flat", False)


# skew colour tiers — GREEN/RED for a mild lean (mass slightly high/low), escalating to CYAN/MAGENTA
# once the profile is strongly lopsided (skew >= +0.5 / <= -0.5). Positive (mass HIGH) = green->cyan,
# negative (mass LOW) = red->magenta. Gray when undefined. Shared so the pane and the stats box match.
_SK_CYAN, _SK_MAGENTA = "#00f3ff", "#ff00a2"
_SK_GREEN, _SK_RED, _SK_GRAY = "#2ecc71", "#e74c3c", "#9aa0aa"


def skew_color(sk) -> str:
    """Hex colour for a profile-convention skew: cyan (>=+0.5) / green (0..+0.5) / red (-0.5..0) /
    magenta (<=-0.5) / gray (undefined). SHARED by the footprint pane and the bucket stats box."""
    if sk is None:
        return _SK_GRAY
    if sk >= 0.5:
        return _SK_CYAN
    if sk <= -0.5:
        return _SK_MAGENTA
    return _SK_GREEN if sk >= 0.0 else _SK_RED


_BUY = (46, 204, 113); _SELL = (231, 76, 60)          # normal bar colours
_BUY_HOT = (0, 255, 127); _SELL_HOT = (255, 45, 70)   # imbalanced (>= mult x per-level avg) — neon
_POC = (255, 215, 0)


class _PriceAxis(pg.AxisItem):
    """Right price axis: 2-decimal labels, tick spacing floored to 0.01 so no two labels round to the same value."""

    def tickStrings(self, values, scale, spacing):
        return [f"{v:.2f}" for v in values]

    def tickSpacing(self, minVal, maxVal, size):
        lv = super().tickSpacing(minVal, maxVal, size)
        return [(max(0.01, s), o) for (s, o) in lv] if lv else lv


class _VolAxis(pg.AxisItem):
    """Bottom volume axis: labels the ABSOLUTE volume (both sides of centre are positive; sell is just drawn left)."""

    def tickStrings(self, values, scale, spacing):
        return [_kfmt(abs(v)) for v in values]


class _FpBars(pg.GraphicsObject):
    """Batched footprint bars for the forming bucket, painted once per update into a QPicture (fast; rects
    transform cleanly with the view). Sell drawn at x in [-sell, 0], buy at [0, +buy], height = one price bin."""

    def __init__(self):
        super().__init__()
        self.picture = QtGui.QPicture()
        self._rect = QtCore.QRectF(-1.0, 0.0, 2.0, 1.0)
        self.max_vol = 1.0

    def update_data(self, rows, bin_h, buy_thr, sell_thr) -> None:
        self.picture = QtGui.QPicture()
        p = QtGui.QPainter(self.picture)
        p.setPen(QtCore.Qt.NoPen)
        for price, buy, sell in rows:
            y = price - bin_h / 2.0
            if sell > 0:
                hot = sell_thr is not None and sell >= sell_thr
                col = QtGui.QColor(*(_SELL_HOT if hot else _SELL)); col.setAlphaF(0.95 if hot else 0.6)
                p.setBrush(col); p.drawRect(QtCore.QRectF(-sell, y, sell, bin_h))
            if buy > 0:
                hot = buy_thr is not None and buy >= buy_thr
                col = QtGui.QColor(*(_BUY_HOT if hot else _BUY)); col.setAlphaF(0.95 if hot else 0.6)
                p.setBrush(col); p.drawRect(QtCore.QRectF(0.0, y, buy, bin_h))
        p.end()
        self.prepareGeometryChange()
        if rows:
            mx = self.max_vol
            lo, hi = rows[0][0], rows[-1][0]
            self._rect = QtCore.QRectF(-mx, lo - bin_h, 2.0 * mx, (hi - lo) + 2.0 * bin_h)
        else:
            self._rect = QtCore.QRectF(-1.0, 0.0, 2.0, 1.0)
        self.update()

    def paint(self, p, *a):
        p.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        return self._rect


class FootprintPanel(pg.PlotWidget):
    """Right-docked live footprint of the forming candle. Call update_footprint(active_bucket, mult, price) each
    frame. Crosshair driven by the owning window: set_crosshair (cursor over this pane) / show_price_line (cursor
    over the chart, mirrored here by price) / hide_crosshair. Double-click toggles the number chips."""

    def __init__(self):
        super().__init__(axisItems={"right": _PriceAxis(orientation="right"),
                                    "bottom": _VolAxis(orientation="bottom")})
        self.setBackground("#141414")
        self.setMinimumWidth(140)
        self.setMaximumWidth(620)
        self.showAxis("right"); self.showAxis("bottom"); self.hideAxis("left")
        for _ax in ("right", "bottom"):
            self.getAxis(_ax).setPen(pg.mkPen("#dcdcdc", width=1))
            self.getAxis(_ax).setTextPen(pg.mkPen("#dcdcdc"))
        self.setMenuEnabled(False)
        self.setViewportUpdateMode(QtWidgets.QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)
        vb = self.getViewBox()
        vb.setMouseEnabled(x=False, y=False)   # Y is auto-fit to the forming candle; X is symmetric volume
        vb.disableAutoRange()
        self.showGrid(x=False, y=True, alpha=0.10)
        self._mid = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen((150, 150, 150, 120), width=1))
        self._mid.setZValue(1); self.addItem(self._mid, ignoreBounds=True); self._mid.setPos(0.0)
        self.bars = _FpBars(); self.bars.setZValue(2); self.addItem(self.bars)
        self._poc = pg.ScatterPlotItem(size=11, symbol="o", pen=pg.mkPen("#141414", width=0.6),
                                       brush=pg.mkBrush(*_POC))    # POC = gold DOT on the centre line (was a dashed line)
        self._poc.setZValue(6); self.addItem(self._poc, ignoreBounds=True); self._poc.setVisible(False)
        # current-price line — faint GRAY, thin, small dashes with WIDE gaps, and BEHIND the number chips (z below
        # the chips) so it marks the live price without covering the numbers.
        _pl = pg.mkPen((175, 175, 185, 190), width=1.8); _pl.setDashPattern([3.0, 9.0])
        self._price_line = pg.InfiniteLine(angle=0, movable=False, pen=_pl)
        self._price_line.setZValue(3); self.addItem(self._price_line, ignoreBounds=True); self._price_line.hide()
        # crosshair — shared with the chart by PRICE (vertical = volume / own; horizontal = price + right-axis tag)
        _xc = pg.mkPen((170, 170, 170, 150), width=1); _xc.setDashPattern([4.0, 8.0])
        self.xhair_v = pg.InfiniteLine(angle=90, movable=False, pen=_xc)
        self.xhair_h = pg.InfiniteLine(angle=0, movable=False, pen=_xc)
        self.xhair_v.setZValue(15); self.xhair_h.setZValue(15)
        self.addItem(self.xhair_v, ignoreBounds=True); self.addItem(self.xhair_h, ignoreBounds=True)
        self.xhair_v.hide(); self.xhair_h.hide()
        self.price_tag = pg.TextItem(anchor=(1, 0.5), color="#141414", fill=pg.mkBrush("#dcdcdc"))
        self.vol_tag = pg.TextItem(anchor=(0.5, 1.0), color="#141414", fill=pg.mkBrush("#dcdcdc"))
        for _t in (self.price_tag, self.vol_tag):
            _f = QtGui.QFont("Consolas", 9); _f.setBold(True); _t.textItem.setFont(_f)
            _t.setZValue(16); self.addItem(_t, ignoreBounds=True); _t.hide()
        # Volume-profile SKEWNESS readout, pinned top-left of the pane (repositioned each frame to the
        # current view corner so it rides the auto-fit Y range). Gold when notably lopsided (|skew| >= 0.5),
        # muted otherwise; the sign lives in the number.
        self._skew_label = pg.TextItem(anchor=(0.0, 0.0), color="#9aa0a6")
        _sf = QtGui.QFont("Consolas", 9); _sf.setBold(True); self._skew_label.textItem.setFont(_sf)
        self._skew_label.setZValue(16); self.addItem(self._skew_label, ignoreBounds=True)
        self._skew_label.hide()
        self._sell_pool = []; self._buy_pool = []
        self._show_numbers = True                 # double-click toggles this
        self._last = None                         # (active, mult, price) cache -> re-render on a numbers toggle
        self.scene().sigMouseClicked.connect(self._on_click)

    def _on_click(self, ev):
        if ev.double():                           # double-click -> show/hide the volume number chips
            self._show_numbers = not self._show_numbers
            if self._last is not None:
                self.update_footprint(*self._last)
            else:
                for t in self._sell_pool + self._buy_pool:
                    t.setVisible(False)
            ev.accept()

    def _chip(self, pool, i, anchor):
        """A rounded 'pill' number chip; fill/border/text colour are set per frame by _chip_style (imbalance-aware)."""
        if i >= len(pool):
            t = RoundedTextItem(anchor=anchor, pad=4.0, radius=6.0)
            _f = QtGui.QFont("Consolas", _NUM_FONT_PT); _f.setBold(True); t.textItem.setFont(_f)
            t.setZValue(5); self.addItem(t, ignoreBounds=True); pool.append(t)
        return pool[i]

    @staticmethod
    def _chip_style(t, base_rgb, imb_bg):
        """Style a chip and return its TEXT colour. Imbalanced level -> SOLID coloured pill (electric-blue buy /
        orange sell) + BLACK text; otherwise a dark pill with the side's own colour text."""
        if imb_bg is not None:
            t.fill = pg.mkBrush(*imb_bg); t.border = pg.mkPen(imb_bg, width=1)
            return (0, 0, 0)
        t.fill = pg.mkBrush(22, 24, 30, 235); t.border = pg.mkPen(base_rgb, width=1)
        return base_rgb

    @staticmethod
    def _bin_rows(rows, lo, hi):
        """Aggregate a fine ladder into <= _MAX_ROWS equal price bins (returns (rows, bin_h)); pass-through when
        already coarse enough. Keeps the number chips from crowding on a deep footprint."""
        n = len(rows)
        if n <= _MAX_ROWS:
            if n > 1:
                gaps = sorted(rows[k + 1][0] - rows[k][0] for k in range(n - 1))
                bh = gaps[len(gaps) // 2] or ((hi - lo) / n)
            else:
                bh = max(1e-6, (lo or 1.0) * 1e-4)
            return rows, bh
        span = (hi - lo) or 1e-9
        bw = span / _MAX_ROWS
        agg = {}
        for pr, b, s in rows:
            bi = min(_MAX_ROWS - 1, int((pr - lo) / bw))
            a = agg.setdefault(bi, [0.0, 0.0]); a[0] += b; a[1] += s
        out = [(lo + (bi + 0.5) * bw, a[0], a[1]) for bi, a in sorted(agg.items())]
        return out, bw

    def clear_panel(self) -> None:
        self.bars.update_data([], 1.0, None, None)
        self._poc.hide(); self._price_line.hide(); self._skew_label.hide()
        for t in self._sell_pool + self._buy_pool:
            t.setVisible(False)

    def update_footprint(self, active: dict, mult: float, price=None, ber30=None, ser30=None) -> None:
        self._last = (active, mult, price, ber30, ser30)
        levels = (active or {}).get("levels") or {}
        rows = []
        for ps, v in levels.items():
            try:
                pr = float(ps)
            except (TypeError, ValueError):
                continue
            b = float(v.get("b", 0.0)); s = float(v.get("s", 0.0))
            if b + s > 0:
                rows.append((pr, b, s))
        if not rows:
            self.clear_panel(); return
        rows.sort(key=lambda r: r[0])
        lo, hi = rows[0][0], rows[-1][0]
        rows, bin_h = self._bin_rows(rows, lo, hi)
        n = len(rows)
        max_vol = max((max(b, s) for _, b, s in rows), default=1.0) or 1.0
        self.bars.max_vol = max_vol
        # ABNORMAL-order threshold — IDENTICAL to the chart's blue/orange imbalance lines: a level is abnormal when
        # its buy (or sell) volume >= mult x the bucket's trailing-30 buyer (or seller) E/R (buyer_er = buy_vol/ticks),
        # passed in from the render. Fall back to the candle's own per-level average only if it wasn't provided.
        if ber30 and ber30 > 0:
            buy_thr = mult * ber30
        else:
            _tb = sum(b for _, b, _ in rows); buy_thr = mult * (_tb / n) if _tb > 0 else None
        if ser30 and ser30 > 0:
            sell_thr = mult * ser30
        else:
            _ts = sum(s for _, _, s in rows); sell_thr = mult * (_ts / n) if _ts > 0 else None
        poc_price = max(rows, key=lambda r: r[1] + r[2])[0]
        self.bars.update_data(rows, bin_h, buy_thr, sell_thr)
        self._poc.setData([0.0], [poc_price]); self._poc.setVisible(True)   # gold dot on the centre line at the POC
        # current-price dashed line (from the caller; falls back to the bucket close)
        if price is None:
            price = (active or {}).get("close")
        try:
            price = float(price)
        except (TypeError, ValueError):
            price = None
        if price and price == price:
            self._price_line.setPos(price); self._price_line.show()
        else:
            self._price_line.hide()
        # centre-column numbers as WHITE chips (black text) — sell right-anchored just left of centre, buy left-
        # anchored just right, so the two never touch; only meaningful rows get a chip. Hidden via double-click.
        us = ub = 0
        if self._show_numbers:
            show_thr = 0.10 * max_vol
            gap = max_vol * 0.03
            for pr, b, s in rows:
                if max(b, s) < show_thr:
                    continue
                if s > 0:
                    t = self._chip(self._sell_pool, us, (1.0, 0.5)); us += 1
                    _c = self._chip_style(t, _SELL, _IMB_SELL_BG if (sell_thr and s >= sell_thr) else None)
                    t.setText(_kfmt(s), color=_c); t.setPos(-gap, pr); t.setVisible(True)
                if b > 0:
                    t = self._chip(self._buy_pool, ub, (0.0, 0.5)); ub += 1
                    _c = self._chip_style(t, _BUY, _IMB_BUY_BG if (buy_thr and b >= buy_thr) else None)
                    t.setText(_kfmt(b), color=_c); t.setPos(gap, pr); t.setVisible(True)
        for j in range(us, len(self._sell_pool)):
            self._sell_pool[j].setVisible(False)
        for j in range(ub, len(self._buy_pool)):
            self._buy_pool[j].setVisible(False)
        pad = max(bin_h, (hi - lo) * 0.05, hi * 1e-5)
        self.setYRange(lo - pad, hi + pad, padding=0)
        x_lim = max_vol * 1.16
        self.setXRange(-x_lim, x_lim, padding=0)
        # skewness of the FORMING bucket's volume profile — top-left corner of the view. Plain-language
        # read (high/low/flat) so it's legible at a glance; the signed number rides along for magnitude.
        # Colour: green/red for a mild lean, cyan/magenta once strongly lopsided (|skew| >= 0.5).
        sk = profile_skewness(levels)
        _word, _notable = skew_read(sk)
        _txt = f"Skew {_word}" if sk is None else f"Skew {_word} {sk:+.2f}"
        self._skew_label.setText(_txt, color=skew_color(sk))
        self._skew_label.setPos(-x_lim + x_lim * 0.03, hi + pad)
        self._skew_label.show()

    # ---- crosshair (driven by the owning window) ----
    def set_crosshair(self, x: float, price: float) -> None:
        """Cursor is over THIS pane: full crosshair + price tag (right axis) + volume tag (bottom axis)."""
        (x0, x1), (y0, y1) = self.getViewBox().viewRange()
        self.xhair_v.setPos(x); self.xhair_v.show()
        self.xhair_h.setPos(price); self.xhair_h.show()
        self.price_tag.setText(f"{price:.2f}"); self.price_tag.setPos(x1, price); self.price_tag.show()
        self.vol_tag.setText(_kfmt(abs(x))); self.vol_tag.setPos(x, y0); self.vol_tag.show()

    def show_price_line(self, price: float) -> None:
        """Cursor is over the CHART: mirror the shared PRICE line here (only if within the forming candle's range);
        the vertical (volume) line + tags belong to this pane's own hover, so hide them."""
        y0, y1 = self.getViewBox().viewRange()[1]
        if y0 <= price <= y1:
            self.xhair_h.setPos(price); self.xhair_h.show()
        else:
            self.xhair_h.hide()
        self.xhair_v.hide(); self.price_tag.hide(); self.vol_tag.hide()

    def hide_crosshair(self) -> None:
        self.xhair_v.hide(); self.xhair_h.hide(); self.price_tag.hide(); self.vol_tag.hide()
