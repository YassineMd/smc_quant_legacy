"""Aggressor size-distribution popup (user 2026-09-02).

Clicking the MIN SIZE caption on the Trades tape or the DOM toolbar opens this floating tool
window: the distribution of AGGRESSOR trade sizes (USD, log-x — sizes are fat-tailed, so the
log axis is what makes them bell-shaped), BUYERS as a green filled curve and SELLERS as a red
one, on a shared count scale. Interactive: hover reads the bin (size, buy/sell counts, and the
cumulative share of volume at-or-above that size); CLICK sets the owner panel's MIN SIZE filter
right there (the gold dashed line marks the current filter and tracks the slider both ways).
Refreshes once a second from the owner's live trade store while open.
"""

from __future__ import annotations

import math
import time

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

_BG      = QtGui.QColor(10, 13, 18)
_GRID    = QtGui.QColor(255, 255, 255, 14)
_HDR_TXT = QtGui.QColor(122, 132, 150)
_TXT     = QtGui.QColor(212, 220, 232)
_BUY     = QtGui.QColor(46, 189, 133)
_SELL    = QtGui.QColor(246, 70, 93)
_GOLD    = QtGui.QColor(240, 185, 11)
_HOVER   = QtGui.QColor(200, 208, 220, 150)

_LOG_LO, _LOG_HI = 1.0, math.log10(500_000.0)     # $10 .. $500K — the SAME domain as the MIN SIZE slider
_NBINS = 56
_PAD_L, _PAD_R, _PAD_T, _PAD_B = 16, 16, 66, 34


def _fmt(v: float) -> str:
    if v >= 1_000_000:
        return f"${v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v/1_000:.1f}K"
    return f"${v:.0f}"


class ClickableLabel(QtWidgets.QLabel):
    """A caption that reports clicks (Qt virtuals resolve on the TYPE — instance monkey-patching
    of mousePressEvent silently does nothing in PySide6, hence this tiny subclass)."""
    clicked = QtCore.Signal()

    def mousePressEvent(self, ev) -> None:
        if ev.button() == QtCore.Qt.LeftButton:
            self.clicked.emit()
            ev.accept()
            return
        super().mousePressEvent(ev)


class SizeDistPopup(QtWidgets.QWidget):
    """Floating (Qt.Tool) window. samples_fn() -> (usd float array, is_buy bool array) over the
    owner's current window; scope_fn() -> the header scope text; get/set_min wire the filter."""

    def __init__(self, samples_fn, scope_fn, get_min, set_min, parent=None) -> None:
        super().__init__(parent, QtCore.Qt.Tool)
        self.setWindowTitle("Aggressor Size Distribution")
        self.resize(700, 430)
        self.setMouseTracking(True)
        self._samples_fn = samples_fn
        self._scope_fn = scope_fn
        self._get_min = get_min
        self._set_min = set_min
        self._hover_x = None
        self._cache = None                        # (stamp, usd, is_buy) — 1s sample cache
        self._font = QtGui.QFont("Consolas", 9)
        fb = QtGui.QFont("Consolas", 9)
        fb.setBold(True)
        self._font_b = fb
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.update)
        self._timer.start()

    # ── geometry / data ────────────────────────────────────────────────────────────────────
    def _plot_rect(self) -> QtCore.QRectF:
        return QtCore.QRectF(_PAD_L, _PAD_T, self.width() - _PAD_L - _PAD_R,
                             self.height() - _PAD_T - _PAD_B)

    def _usd_at(self, x: float) -> float:
        r = self._plot_rect()
        t = min(1.0, max(0.0, (x - r.left()) / (r.width() or 1.0)))
        return 10.0 ** (_LOG_LO + t * (_LOG_HI - _LOG_LO))

    def _x_of(self, usd: float) -> float:
        r = self._plot_rect()
        t = (math.log10(max(10.0, min(500_000.0, usd))) - _LOG_LO) / (_LOG_HI - _LOG_LO)
        return r.left() + t * r.width()

    def _samples(self):
        now = time.monotonic()
        if self._cache is None or now - self._cache[0] > 1.0:
            try:
                usd, is_buy = self._samples_fn()
            except Exception:
                usd, is_buy = np.empty(0), np.empty(0, dtype=bool)
            self._cache = (now, np.asarray(usd, dtype=np.float64), np.asarray(is_buy, dtype=bool))
        return self._cache[1], self._cache[2]

    # ── interaction ────────────────────────────────────────────────────────────────────────
    def mouseMoveEvent(self, ev) -> None:
        self._hover_x = ev.position().x()
        self.update()
        ev.accept()

    def leaveEvent(self, ev) -> None:
        self._hover_x = None
        self.update()
        super().leaveEvent(ev)

    def mousePressEvent(self, ev) -> None:
        if ev.button() == QtCore.Qt.LeftButton:
            self._click_at(ev.position().x())
            ev.accept()
            return
        super().mousePressEvent(ev)

    def _click_at(self, x: float) -> None:
        try:
            self._set_min(float(self._usd_at(x)))  # snaps the owner's slider; gold line follows
        except Exception:
            pass
        self.update()

    # ── painting ───────────────────────────────────────────────────────────────────────────
    def paintEvent(self, _ev) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, _BG)
        r = self._plot_rect()
        usd, is_buy = self._samples()

        # header: scope + per-side stats (n / median / p90 — factual, no fit claimed)
        p.setFont(self._font_b)
        p.setPen(_TXT)
        p.drawText(QtCore.QRect(_PAD_L, 8, w - 2 * _PAD_L, 16), QtCore.Qt.AlignLeft,
                   f"AGGRESSOR TRADE SIZES · {self._scope_fn()}")
        p.setFont(self._font)
        if len(usd):
            for side, col, y in ((True, _BUY, 26), (False, _SELL, 42)):
                v = usd[is_buy] if side else usd[~is_buy]
                if len(v):
                    med = float(np.median(v)); p90 = float(np.percentile(v, 90))
                    txt = (f"{'BUYERS ' if side else 'SELLERS'}  n {len(v):,}   median {_fmt(med)}   "
                           f"p90 {_fmt(p90)}   vol {_fmt(float(v.sum()))}")
                else:
                    txt = ("BUYERS " if side else "SELLERS") + "  (none in window)"
                p.setPen(col)
                p.drawText(QtCore.QRect(_PAD_L, y, w - 2 * _PAD_L, 15), QtCore.Qt.AlignLeft, txt)
        else:
            p.setPen(_HDR_TXT)
            p.drawText(r.toRect(), QtCore.Qt.AlignCenter, "no trades in the window yet…")

        # axes: decade gridlines + labels
        p.setFont(self._font)
        for dec in (10, 100, 1_000, 10_000, 100_000):
            x = self._x_of(dec)
            p.setPen(QtGui.QPen(_GRID, 1))
            p.drawLine(QtCore.QPointF(x, r.top()), QtCore.QPointF(x, r.bottom()))
            p.setPen(_HDR_TXT)
            p.drawText(QtCore.QRectF(x - 40, r.bottom() + 6, 80, 16), QtCore.Qt.AlignHCenter, _fmt(dec))
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 30), 1))
        p.drawLine(QtCore.QPointF(r.left(), r.bottom()), QtCore.QPointF(r.right(), r.bottom()))

        if len(usd):
            # log-binned counts per side, lightly smoothed -> the two distribution curves
            lg = np.log10(np.clip(usd, 10.0, 500_000.0))
            edges = np.linspace(_LOG_LO, _LOG_HI, _NBINS + 1)
            hb, _ = np.histogram(lg[is_buy], bins=edges)
            hs, _ = np.histogram(lg[~is_buy], bins=edges)
            k = np.array([1.0, 2.0, 3.0, 2.0, 1.0]); k /= k.sum()
            hb = np.convolve(hb, k, mode="same")
            hs = np.convolve(hs, k, mode="same")
            top = max(hb.max(), hs.max()) or 1.0
            centers = (edges[:-1] + edges[1:]) / 2.0
            xs = r.left() + (centers - _LOG_LO) / (_LOG_HI - _LOG_LO) * r.width()

            for hist, col in ((hs, _SELL), (hb, _BUY)):       # sellers first, buyers on top
                path = QtGui.QPainterPath()
                path.moveTo(xs[0], r.bottom())
                for x, v in zip(xs, hist):
                    path.lineTo(x, r.bottom() - (v / top) * (r.height() - 8))
                path.lineTo(xs[-1], r.bottom())
                path.closeSubpath()
                fill = QtGui.QColor(col); fill.setAlpha(60)
                p.fillPath(path, fill)
                p.setPen(QtGui.QPen(col, 1.6))
                p.drawPath(path)

            # current MIN SIZE filter — gold dashed, tracks the slider both ways
            cur = float(self._get_min() or 0.0)
            if cur > 0:
                x = self._x_of(cur)
                p.setPen(QtGui.QPen(_GOLD, 1, QtCore.Qt.DashLine))
                p.drawLine(QtCore.QPointF(x, r.top()), QtCore.QPointF(x, r.bottom()))
                p.setFont(self._font_b)
                p.setPen(_GOLD)
                p.drawText(QtCore.QRectF(x - 70, r.top() + 4, 140, 14), QtCore.Qt.AlignHCenter,
                           f"min {_fmt(cur)}")

            # hover: vline + bin/cumulative readout; a CLICK sets the filter here
            if self._hover_x is not None and r.left() <= self._hover_x <= r.right():
                hx = float(self._hover_x)
                husd = self._usd_at(hx)
                p.setPen(QtGui.QPen(_HOVER, 1, QtCore.Qt.DashLine))
                p.drawLine(QtCore.QPointF(hx, r.top()), QtCore.QPointF(hx, r.bottom()))
                bi = min(_NBINS - 1, max(0, int((math.log10(max(10.0, husd)) - _LOG_LO)
                                                / (_LOG_HI - _LOG_LO) * _NBINS)))
                above = usd >= husd
                volshare = float(usd[above].sum()) / (float(usd.sum()) or 1.0) * 100.0
                txt = (f"{_fmt(husd)}   bin: buy {hb[bi]:.0f} / sell {hs[bi]:.0f}   "
                       f"≥ here: {int(above.sum()):,} trades · {volshare:.0f}% of volume   (click = set filter)")
                p.setFont(self._font)
                p.setPen(_TXT)
                p.drawText(QtCore.QRect(_PAD_L, h - 22, w - 2 * _PAD_L, 16),
                           QtCore.Qt.AlignLeft, txt)
        p.end()
