"""Tier-3 Continuous Order Book (COB) depth panel (spec §8.1).

A narrow PyQtGraph pane docked to the right of the main chart inside a QSplitter
(the splitter handle is the ``#cob-resizer``). It draws resting limit liquidity
as translucent horizontal bars aligned to the shared price (Y) axis — asks red,
bids green (§10.2.3) — with K-formatted size markers on large zones and a live
hover tooltip. The owning window keeps the COB's Y-range locked to the chart's.
"""

from __future__ import annotations

import pyqtgraph as pg
from PySide6 import QtCore, QtGui

from . import config


def _kfmt(v: float) -> str:
    return f"{v/1000:.1f}K" if v >= 1000 else f"{v:.0f}"


class _CobBars(pg.GraphicsObject):
    def __init__(self):
        super().__init__()
        self.picture = QtGui.QPicture()
        self._rect = QtCore.QRectF()
        self.max_vol = 1.0
        self.bin_h = config.DOM_BIN_STEP                 # price aggregation bin (set to the heatmap's row height)
        self.bid_rgba = config.RGBA_COB_BID
        self.ask_rgba = config.RGBA_COB_ASK
        self.agg = {"bid": {}, "ask": {}}                # {bucket_center: summed_qty} per side (for mark_price)

    def update_data(self, bids: list, asks: list) -> None:
        self.picture = QtGui.QPicture()
        p = QtGui.QPainter(self.picture)
        p.setFont(QtGui.QFont("Consolas", 7))
        bin_h = max(config.DOM_BIN_STEP, float(self.bin_h))
        # AGGREGATE raw tick levels into bin_h buckets (so the ladder matches the heatmap's row resolution and
        # coarsens as you zoom out): bucket center = round(price/bin_h)*bin_h, qty summed per side.
        agg = {"bid": {}, "ask": {}}
        for side, src in (("bid", bids), ("ask", asks)):
            d = agg[side]
            for lvl in src:
                try:
                    price, qty = float(lvl[0]), float(lvl[1])
                except (ValueError, IndexError):
                    continue
                c = round(price / bin_h) * bin_h
                d[c] = d.get(c, 0.0) + qty
        self.agg = agg
        cells = [(c, q, side) for side in ("bid", "ask") for c, q in agg[side].items()]
        if not cells:
            p.end(); self.prepareGeometryChange(); self.update(); return
        self.max_vol = max(q for _, q, _ in cells)
        p.setPen(QtCore.Qt.NoPen)
        for center, qty, side in cells:
            rgba = self.bid_rgba if side == "bid" else self.ask_rgba
            col = QtGui.QColor(int(rgba[0]), int(rgba[1]), int(rgba[2])); col.setAlphaF(rgba[3])
            p.setBrush(QtGui.QBrush(col)); p.setPen(QtCore.Qt.NoPen)
            p.drawRect(QtCore.QRectF(0, center - bin_h / 2, qty, bin_h))
            if qty >= self.max_vol * 0.5:  # significant zone marker
                p.setPen(QtGui.QPen(QtGui.QColor("#cccccc")))
                p.drawText(QtCore.QPointF(qty * 0.4, center), _kfmt(qty))
                p.setPen(QtCore.Qt.NoPen)
        p.end()
        # fix #12: boundingRect must span the FULL price range of the bars, else PyQtGraph culls the QPicture
        # the moment the zoomed view exceeds a tiny rect and the histograms vanish.
        centers = [c for c, _, _ in cells]
        lo, hi = min(centers), max(centers)
        self._rect = QtCore.QRectF(0, lo - bin_h, self.max_vol, (hi - lo) + 2 * bin_h)
        self.prepareGeometryChange(); self.update()

    def paint(self, p, *a): p.drawPicture(0, 0, self.picture)
    def boundingRect(self): return self._rect


class CobPanel(pg.PlotWidget):
    def __init__(self):
        super().__init__()
        self.setBackground(config.COLOR_CANVAS)
        self.setMaximumWidth(220)
        self.setMinimumWidth(60)
        self.hideAxis("left")
        self.hideAxis("bottom")
        self.setMenuEnabled(False)
        self.getViewBox().setMouseEnabled(x=False, y=False)
        self.getViewBox().disableAutoRange()  # Y is driven by sync_y (fix #12)

        self.bars = _CobBars()
        self.addItem(self.bars)

        self._tooltip = pg.TextItem(color="#000000", anchor=(0, 1),
                                    fill=pg.mkBrush(255, 255, 255, 220))
        self._tooltip.setZValue(100)
        self.addItem(self._tooltip)
        self._tooltip.hide()
        self._depth = {"bids": [], "asks": []}
        self._proxy = pg.SignalProxy(self.scene().sigMouseMoved, rateLimit=30,
                                     slot=self._on_hover)

    def update_depth(self, depth: dict) -> None:
        self._depth = depth
        self.bars.update_data(depth.get("bids", []), depth.get("asks", []))
        self.setXRange(0, max(1.0, self.bars.max_vol), padding=0.02)

    def set_bin(self, bin_h: float) -> None:
        """Set the price-aggregation bin (= the heatmap's current row height) so the ladder aggregates as you
        zoom out and refines as you zoom in. Re-renders from the last depth (no new fetch)."""
        bin_h = max(config.DOM_BIN_STEP, float(bin_h))
        if abs(bin_h - self.bars.bin_h) > 1e-9:
            self.bars.bin_h = bin_h
            if self._depth:
                self.update_depth(self._depth)

    def set_palette(self, bid_rgba, ask_rgba) -> None:
        """Override the bar colors (heatmap mode: neon green bids / neon purple asks); re-renders."""
        self.bars.bid_rgba = bid_rgba; self.bars.ask_rgba = ask_rgba
        if self._depth:
            self.update_depth(self._depth)

    def mark_price(self, price: float) -> None:
        """Highlight the ladder bucket at ``price`` (driven by the heatmap crosshair): show its side + summed
        size as the tooltip, anchored at that price. Hidden when there's no liquidity near the cursor."""
        bin_h = max(config.DOM_BIN_STEP, float(self.bars.bin_h))
        best = None
        for side in ("bid", "ask"):
            for center, qty in self.bars.agg.get(side, {}).items():
                if abs(center - price) <= bin_h and (best is None or abs(center - price) < abs(best[0] - price)):
                    best = (center, qty, "Bid" if side == "bid" else "Ask")
        if best is None:
            self._tooltip.hide(); return
        center, qty, lbl = best
        self._tooltip.setText(f"{lbl} {center:.2f}\n{_kfmt(qty)}")
        self._tooltip.setPos(0.0, center)
        self._tooltip.show()

    def sync_y(self, y0: float, y1: float) -> None:
        self.setYRange(y0, y1, padding=0)

    def _on_hover(self, evt):
        pos = evt[0]
        vb = self.getViewBox()
        if not self.sceneBoundingRect().contains(pos):
            self._tooltip.hide(); return
        pt = vb.mapSceneToView(pos)
        price = pt.y()
        # nearest level
        best, side = None, ""
        for s in ("bids", "asks"):
            for lvl in self._depth.get(s, []):
                try:
                    pr, qty = float(lvl[0]), float(lvl[1])
                except (ValueError, IndexError):
                    continue
                if best is None or abs(pr - price) < abs(best[0] - price):
                    best, side = (pr, qty), ("Bid" if s == "bids" else "Ask")
        if best:
            self._tooltip.setText(f"{side} {best[0]:.2f}\n{_kfmt(best[1])}")
            self._tooltip.setPos(pt.x(), best[0])
            self._tooltip.show()
