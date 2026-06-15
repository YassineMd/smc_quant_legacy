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

    def update_data(self, bids: list, asks: list) -> None:
        self.picture = QtGui.QPicture()
        p = QtGui.QPainter(self.picture)
        p.setFont(QtGui.QFont("Consolas", 7))
        rows = []
        for side, rgba in (("bid", config.RGBA_COB_BID), ("ask", config.RGBA_COB_ASK)):
            for lvl in (bids if side == "bid" else asks):
                try:
                    price, qty = float(lvl[0]), float(lvl[1])
                except (ValueError, IndexError):
                    continue
                rows.append((price, qty, rgba))
        if not rows:
            p.end(); self.prepareGeometryChange(); self.update(); return

        self.max_vol = max(q for _, q, _ in rows)
        bin_h = config.DOM_BIN_STEP
        prices = [pr for pr, _, _ in rows]
        for price, qty, rgba in rows:
            col = QtGui.QColor(int(rgba[0]), int(rgba[1]), int(rgba[2])); col.setAlphaF(rgba[3])
            p.setBrush(QtGui.QBrush(col)); p.setPen(QtCore.Qt.NoPen)
            p.drawRect(QtCore.QRectF(0, price - bin_h / 2, qty, bin_h))
            if qty >= self.max_vol * 0.5:  # significant zone marker
                p.setPen(QtGui.QPen(QtGui.QColor("#cccccc")))
                p.drawText(QtCore.QPointF(qty * 0.4, price), _kfmt(qty))
                p.setPen(QtCore.Qt.NoPen)
        p.end()
        # fix #12: boundingRect must span the FULL price range of the bars, else
        # PyQtGraph culls the QPicture the moment the zoomed view exceeds a tiny
        # rect and the histograms vanish.
        lo, hi = min(prices), max(prices)
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
