"""DOM scanner mode — DeepChart-style Depth-of-Market ladder + Volume Profile (no trading sim).

Full-pane price ladder, newest book every 0.4s pulse (top 200 levels/side off the PULSE):
    [ BID SIZE | SOLD | PRICE | BOUGHT | ASK SIZE | VOLUME PROFILE ]
Asks stack above the spread (red, growing right), bids below (green, growing left). SOLD/BOUGHT
(DeepChart's B.T/A.T) are the EXECUTED taker sell / taker buy volumes at each price over the
selected window — numbers beside the price, max-per-column bolded. The VP column sits on the
RIGHT, neutral GRAY (user 2026-09-01: no buy/sell split), POC row gold. Book walls (>= P90 of
visible levels) render brighter + bold. A book-imbalance strip (whole 200-level book, SOL) sits
on top. GROUP re-bins 0.01/0.02/0.05/0.10 (trades stored at TICK resolution — regroup is free,
and the ladder HOLDS its price position). The ladder NEVER auto-scrolls: it centers ONCE on
entry, then stays put while price moves through it — wheel OR click-drag pans, the ⟲ CENTER
pill re-centers on demand (user 2026-09-01). The VP histogram is REVERSED: right-anchored,
growing left. Sizes are SOL; the last trade tags its row with a side chip.
"""

from __future__ import annotations

import time

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from . import config

# ── palette (shared design language with the Trades tape) ───────────────────────────────────
_BG       = QtGui.QColor(10, 13, 18)
_BG_TOOL  = "#0e1218"
_PRICE_BG = QtGui.QColor(13, 17, 23)
_RULE     = QtGui.QColor(27, 34, 45)
_GRID     = QtGui.QColor(255, 255, 255, 7)
_HDR_TXT  = QtGui.QColor(122, 132, 150)
_TXT      = QtGui.QColor(212, 220, 232)
_DIM_TXT  = QtGui.QColor(212, 220, 232, 120)
_BUY      = QtGui.QColor(46, 189, 133)
_SELL     = QtGui.QColor(246, 70, 93)
_GOLD     = QtGui.QColor(240, 185, 11)
_VP_GRAY  = QtGui.QColor(150, 160, 175)
_WAIT_TXT = QtGui.QColor(120, 130, 148, 160)

_ROW_H, _HDR_H, _STRIP_H = 18, 24, 34
_TICK = 0.01                       # storage resolution: trades histogrammed at 1 tick, regroup-free
_GROUPS = (0.01, 0.02, 0.05, 0.10)
_VP_SECS = ((300, "5M"), (900, "15M"), (3600, "1H"), (7200, "2H"), (14400, "4H"), (21600, "6H"))
_PRUNE_SLACK = 300.0               # keep trades a bit past the largest VP window before dropping


def _kfmt(v: float) -> str:
    if v >= 1_000_000:
        return f"{v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"{v/1_000:.1f}K"
    return f"{v:.0f}" if v >= 10 else f"{v:.1f}"


class _DomCanvas(QtWidgets.QWidget):
    """The painted ladder: imbalance strip + header + rows. All state lives on the parent panel."""

    def __init__(self, panel: "DomPanel") -> None:
        super().__init__(panel)
        self._p = panel
        self.setMinimumHeight(160)
        self.setMouseTracking(True)                # hover row highlight needs button-less move events
        self._hover_y = None                       # cursor y inside the canvas; None = cursor off-canvas
        self._font = QtGui.QFont("Consolas", 9)
        fb = QtGui.QFont("Consolas", 9)
        fb.setBold(True)
        self._font_b = fb
        fh = QtGui.QFont("Consolas", 8)
        fh.setBold(True)
        fh.setLetterSpacing(QtGui.QFont.SpacingType.AbsoluteSpacing, 1.2)
        self._font_hdr = fh

    def wheelEvent(self, ev) -> None:
        # wheel UP pans the ladder to HIGHER prices; the ladder is manual-only (no auto-follow)
        self._p.pan_by(3 if ev.angleDelta().y() > 0 else -3)
        ev.accept()

    # click-and-drag panning (user 2026-09-01): grab the ladder and pull — dragging DOWN pulls the
    # content down, so higher prices scroll into view (same direction convention as the wheel).
    def _drag_start(self, y: float) -> None:
        base = self._p._anchor_px if self._p._anchor_px is not None else (self._p._mid or 0.0)
        self._drag = (y, base) if base > 0 else None

    def _drag_to(self, y: float) -> None:
        if getattr(self, "_drag", None) is None:
            return
        y0, a0 = self._drag
        self._p._anchor_px = a0 + ((y - y0) / _ROW_H) * self._p.group
        self.update()

    def _drag_end(self) -> None:
        self._drag = None

    def mousePressEvent(self, ev) -> None:
        if ev.button() == QtCore.Qt.LeftButton:
            self._drag_start(ev.position().y())
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            ev.accept()

    def mouseMoveEvent(self, ev) -> None:
        self._drag_to(ev.position().y())
        self._hover_y = ev.position().y()          # hover row highlight follows the cursor
        self.update()
        ev.accept()

    def mouseReleaseEvent(self, ev) -> None:
        self._drag_end()
        self.unsetCursor()
        ev.accept()

    def leaveEvent(self, ev) -> None:              # cursor off the ladder -> highlight fully removed
        self._hover_y = None
        self.update()
        super().leaveEvent(ev)

    # ------------------------------------------------------------------
    def paintEvent(self, _ev) -> None:
        p = QtGui.QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, _BG)
        pad = 12
        P = self._p

        bids, asks = P._bids, P._asks              # [(price, qty)] best-first, parsed floats
        if not bids and not asks:
            p.setFont(self._font)
            p.setPen(_WAIT_TXT)
            p.drawText(QtCore.QRect(0, 0, w, h), QtCore.Qt.AlignCenter, "waiting for order book…")
            p.end()
            return

        # ── whole-book imbalance strip (all 200 levels/side, SOL) ──────────────────────────
        tb = sum(q for _pr, q in bids)
        ta = sum(q for _pr, q in asks)
        tot = tb + ta
        y0 = 0
        if tot > 0:
            bar_y, bar_h = y0 + 19, 5
            bw = int((w - 2 * pad) * (tb / tot))
            p.setPen(QtCore.Qt.NoPen)
            path = QtGui.QPainterPath()
            path.addRoundedRect(pad, bar_y, max(2, bw), bar_h, 2.5, 2.5)
            p.fillPath(path, _BUY)
            path = QtGui.QPainterPath()
            path.addRoundedRect(pad + bw + 2, bar_y, max(2, w - 2 * pad - bw - 2), bar_h, 2.5, 2.5)
            p.fillPath(path, _SELL)
            p.setFont(self._font_hdr)
            p.setPen(_BUY)
            p.drawText(QtCore.QRect(pad, y0 + 2, w // 2, 14), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                       f"BIDS {_kfmt(tb)}  {tb / tot * 100:.0f}%")
            p.setPen(_SELL)
            p.drawText(QtCore.QRect(w // 2, y0 + 2, w // 2 - pad, 14), QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                       f"{ta / tot * 100:.0f}%  {_kfmt(ta)} ASKS")
        y0 += _STRIP_H

        # ── column geometry: [BID | SOLD | PRICE | BOUGHT | ASK | VP(right)] ───────────────
        g = P.group
        traded_w = 62
        price_w = 92
        vp_w = max(120, int(w * 0.20))
        c_vp0 = w - pad - vp_w                     # VP zone, far right (gray)
        span = max(60, (c_vp0 - 2 * traded_w - price_w - 2 * pad - 40) // 2)
        c_bid1 = pad + span                        # bid bars grow LEFT from here
        c_sold0 = c_bid1 + 10
        c_price0 = c_sold0 + traded_w + 4
        c_bought0 = c_price0 + price_w + 4
        c_ask0 = c_bought0 + traded_w + 10         # ask bars grow RIGHT from here
        ask_span = max(60, c_vp0 - 14 - c_ask0)
        bid_span = span

        # ── header ─────────────────────────────────────────────────────────────────────────
        p.setFont(self._font_hdr)
        p.setPen(_HDR_TXT)
        p.drawText(QtCore.QRect(pad, y0, span, _HDR_H), QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, "BIDS")
        p.drawText(QtCore.QRect(c_sold0, y0, traded_w, _HDR_H), QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter,
                   "SOLD")
        p.drawText(QtCore.QRect(c_price0, y0, price_w, _HDR_H), QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter,
                   "PRICE")
        p.drawText(QtCore.QRect(c_bought0, y0, traded_w, _HDR_H), QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter,
                   "BOUGHT")
        p.drawText(QtCore.QRect(c_ask0, y0, 120, _HDR_H), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, "ASKS")
        p.drawText(QtCore.QRect(c_vp0, y0, vp_w, _HDR_H), QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                   f"VOLUME · {P.vp_label()}")
        p.setPen(QtGui.QPen(_RULE, 1))
        p.drawLine(pad, y0 + _HDR_H - 1, w - pad, y0 + _HDR_H - 1)
        y0 += _HDR_H

        # ── rows: bin the book + traded volumes to the current group ───────────────────────
        mid = P._mid if P._mid > 0 else (bids[0][0] + asks[0][0]) / 2.0 if (bids and asks) else \
            (bids[0][0] if bids else asks[0][0])
        if P._anchor_px is None:
            P._anchor_px = mid                     # center ONCE (entry / ⟲ CENTER) — never auto-follow
        n_rows = max(1, (h - y0) // _ROW_H)
        # ALL binning happens in integer TICK space then floors to the group — price/g floats off-by-one
        # at the 4th decimal (235.67//0.01 -> 23566), int ticks never do. A bin = [b*g, b*g+g).
        tpg = max(1, int(round(g / _TICK)))
        center_bin = int(round(P._anchor_px / _TICK)) // tpg
        top_bin = center_bin + n_rows // 2

        bid_bins: dict = {}
        for pr, q in bids:
            b = int(round(pr / _TICK)) // tpg
            bid_bins[b] = bid_bins.get(b, 0.0) + q
        ask_bins: dict = {}
        for pr, q in asks:
            b = int(round(pr / _TICK)) // tpg
            ask_bins[b] = ask_bins.get(b, 0.0) + q
        vp = P.vp_bins(g, top_bin - n_rows + 1, top_bin)   # {bin: (boughtQ, soldQ)}

        vis_b = [bid_bins.get(top_bin - i, 0.0) for i in range(n_rows)]
        vis_a = [ask_bins.get(top_bin - i, 0.0) for i in range(n_rows)]
        max_sz = max(max(vis_b, default=0.0), max(vis_a, default=0.0)) or 1.0
        max_vp = max((bq + sq for bq, sq in vp.values()), default=0.0) or 1.0
        max_bought = max((v[0] for v in vp.values()), default=0.0)
        max_sold = max((v[1] for v in vp.values()), default=0.0)
        nz = sorted([v for v in vis_b + vis_a if v > 0])
        p90 = nz[int(len(nz) * 0.9)] if nz else float("inf")   # wall emphasis threshold (visible P90)
        poc_bin = max(vp, key=lambda b: vp[b][0] + vp[b][1]) if vp else None

        best_bid_bin = int(round(bids[0][0] / _TICK)) // tpg if bids else None
        best_ask_bin = int(round(asks[0][0] / _TICK)) // tpg if asks else None
        last_bin = int(round(P._last_px / _TICK)) // tpg if P._last_px > 0 else None

        p.setFont(self._font)
        for i in range(n_rows):
            b = top_bin - i
            ry = y0 + i * _ROW_H
            p.setPen(QtGui.QPen(_GRID, 1))
            p.drawLine(pad, ry + _ROW_H - 1, w - pad, ry + _ROW_H - 1)

            # BID bar (grows left from the SOLD column)
            q = vis_b[i]
            if q > 0 and (best_bid_bin is None or b <= best_bid_bin):
                bw_ = q / max_sz * bid_span
                col = QtGui.QColor(_BUY)
                col.setAlpha(210 if q >= p90 else 130)
                p.setPen(QtCore.Qt.NoPen)
                path = QtGui.QPainterPath()
                path.addRoundedRect(QtCore.QRectF(c_bid1 - bw_, ry + 2.5, bw_, _ROW_H - 5), 2.0, 2.0)
                p.fillPath(path, col)
                p.setPen(_TXT if q >= p90 else _DIM_TXT)
                p.setFont(self._font_b if q >= p90 else self._font)
                p.drawText(QtCore.QRectF(c_bid1 - bid_span, ry, bid_span - 6, _ROW_H),
                           QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, _kfmt(q))
                p.setFont(self._font)

            # ASK bar (grows right from the BOUGHT column)
            q = vis_a[i]
            if q > 0 and (best_ask_bin is None or b >= best_ask_bin):
                bw_ = q / max_sz * ask_span
                col = QtGui.QColor(_SELL)
                col.setAlpha(210 if q >= p90 else 130)
                p.setPen(QtCore.Qt.NoPen)
                path = QtGui.QPainterPath()
                path.addRoundedRect(QtCore.QRectF(c_ask0, ry + 2.5, bw_, _ROW_H - 5), 2.0, 2.0)
                p.fillPath(path, col)
                p.setPen(_TXT if q >= p90 else _DIM_TXT)
                p.setFont(self._font_b if q >= p90 else self._font)
                p.drawText(QtCore.QRectF(c_ask0 + 6, ry, ask_span - 6, _ROW_H),
                           QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, _kfmt(q))
                p.setFont(self._font)

            # SOLD / BOUGHT (executed taker volumes at this price, window-scoped; column max bolded)
            bq, sq = vp.get(b, (0.0, 0.0))
            if sq > 0:
                hot = sq >= max_sold and max_sold > 0
                col = QtGui.QColor(_SELL)
                col.setAlpha(235 if hot else 150)
                p.setPen(col)
                p.setFont(self._font_b if hot else self._font)
                p.drawText(QtCore.QRect(c_sold0, ry, traded_w - 4, _ROW_H),
                           QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, _kfmt(sq))
            if bq > 0:
                hot = bq >= max_bought and max_bought > 0
                col = QtGui.QColor(_BUY)
                col.setAlpha(235 if hot else 150)
                p.setPen(col)
                p.setFont(self._font_b if hot else self._font)
                p.drawText(QtCore.QRect(c_bought0 + 4, ry, traded_w - 4, _ROW_H),
                           QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, _kfmt(bq))
            p.setFont(self._font)

            # VP (far right, neutral GRAY — total executed volume; POC row gold). REVERSED (user
            # 2026-09-01): bars anchor on the RIGHT edge and grow LEFT, label to the left of the tip.
            if bq + sq > 0:
                bw_ = (bq + sq) / max_vp * (vp_w - 44)
                p.setPen(QtCore.Qt.NoPen)
                if b == poc_bin:
                    col = QtGui.QColor(_GOLD)
                    col.setAlpha(200)
                else:
                    col = QtGui.QColor(_VP_GRAY)
                    col.setAlpha(90)
                p.fillRect(QtCore.QRectF(w - pad - max(1.5, bw_), ry + 3.5, max(1.5, bw_), _ROW_H - 7), col)
                p.setPen(_GOLD if b == poc_bin else _DIM_TXT)
                p.setFont(self._font_b if b == poc_bin else self._font)
                p.drawText(QtCore.QRectF(c_vp0, ry, vp_w - bw_ - 9, _ROW_H),
                           QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, _kfmt(bq + sq))
                p.setFont(self._font)

        # ── price column LAST so chips overlay the grid cleanly ────────────────────────────
        p.setPen(QtCore.Qt.NoPen)
        p.fillRect(QtCore.QRect(c_price0, y0, price_w, h - y0), _PRICE_BG)
        for i in range(n_rows):
            b = top_bin - i
            ry = y0 + i * _ROW_H
            price = b * g
            is_edge = b in (best_bid_bin, best_ask_bin)
            if b == last_bin and P._last_side is not None:      # last-trade chip, side-colored
                chip = QtGui.QColor(_BUY if P._last_side else _SELL)
                chip.setAlpha(60)
                path = QtGui.QPainterPath()
                path.addRoundedRect(QtCore.QRectF(c_price0 + 3, ry + 1.5, price_w - 6, _ROW_H - 3), 3.0, 3.0)
                p.fillPath(path, chip)
            p.setPen(_TXT if is_edge else _DIM_TXT)
            p.setFont(self._font_b if is_edge else self._font)
            p.drawText(QtCore.QRect(c_price0, ry, price_w, _ROW_H), QtCore.Qt.AlignCenter,
                       f"{price:,.2f}")
        p.setFont(self._font)

        # spread marker: dashed BRIGHT-GRAY line on the ask/bid boundary, line ONLY — no text label
        # (user 2026-09-01). Skipped when grouping merges best bid/ask into one row.
        if best_bid_bin is not None and best_ask_bin is not None and bids and asks:
            i_ask = top_bin - best_ask_bin           # best-ask row index (bid row sits below: bigger i)
            i_bid = top_bin - best_bid_bin
            if i_bid > i_ask and -1 <= i_ask and i_bid <= n_rows:
                ymid = y0 + ((i_ask + 1 + i_bid) * _ROW_H) // 2
                p.setPen(QtGui.QPen(QtGui.QColor(225, 230, 240, 220), 1, QtCore.Qt.DashLine))
                p.drawLine(c_sold0 - 20, ymid, c_bought0 + traded_w + 20, ymid)

        # hover row highlight (user 2026-09-01): thin light-gray box around the FULL row under the
        # cursor; _hover_y is None the moment the cursor leaves the canvas -> nothing drawn.
        if self._hover_y is not None and self._hover_y >= y0:
            i = int((self._hover_y - y0) // _ROW_H)
            if 0 <= i < n_rows:
                ry = y0 + i * _ROW_H
                p.setPen(QtGui.QPen(QtGui.QColor(200, 208, 220, 150), 1))
                p.setBrush(QtCore.Qt.NoBrush)
                p.drawRoundedRect(QtCore.QRectF(pad - 4, ry + 0.5, w - 2 * pad + 8, _ROW_H - 1.5), 3.0, 3.0)
        p.end()


class DomPanel(QtWidgets.QWidget):
    """Toolbar (GROUP + VP window + ⟲ CENTER pill) over the painted DOM ladder."""

    settingsChanged = QtCore.Signal()

    def __init__(self) -> None:
        super().__init__()
        self.group: float = _GROUPS[0]
        self.vp_secs: int = 3600                   # default 1H (NOT _VP_SECS[-1] — 6H exists now)
        self._anchor_px: float | None = None       # ladder anchor PRICE; None = center on next paint.
        #                                            Set once, then FIXED — the ladder never auto-scrolls;
        #                                            price-based so it survives a GROUP change in place.
        self._bids: list = []                      # [(price, qty)] best-first, parsed floats
        self._asks: list = []
        self._mid: float = 0.0
        self._last_px: float = 0.0
        self._last_side = None                     # 1 taker buy / 0 taker sell
        # executed trades at TICK resolution: parallel arrays in append chunks (concat cached)
        self._chunks: list = []                    # [(ts s, tickbin i8, buyQ, sellQ)]
        self._cat = None
        self._live_t0: float = 0.0

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        bar = QtWidgets.QWidget()
        bar.setFixedHeight(44)
        bar.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        bar.setStyleSheet(f"background:{_BG_TOOL}; border-bottom:1px solid #1b222d;")
        lay = QtWidgets.QHBoxLayout(bar)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(10)

        def cap(text):
            c = QtWidgets.QLabel(text)
            c.setStyleSheet("color:#7a8496; font-family:Consolas; font-size:9px; font-weight:bold;"
                            "letter-spacing:1px; border:none;")
            return c

        _SEG = ("QPushButton { color:#aeb4c0; background:#161b24; border:1px solid #242c3a;"
                " border-radius:10px; padding:2px 10px; font-family:Consolas; font-size:10px;"
                " font-weight:bold; }"
                "QPushButton:checked { color:#0e1218; background:#f0b90b; border-color:#f0b90b; }")

        lay.addWidget(cap("GROUP"))
        self._grp_btns = []
        for gv in _GROUPS:
            b = QtWidgets.QPushButton(f"{gv:.2f}")
            b.setCheckable(True)
            b.setChecked(gv == self.group)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setStyleSheet(_SEG)
            b.clicked.connect(lambda _c=False, v=gv: self.set_group(v))
            self._grp_btns.append(b)
            lay.addWidget(b)

        lay.addSpacing(10)
        lay.addWidget(cap("VP"))
        self._vp_btns = []
        for secs, label in _VP_SECS:
            b = QtWidgets.QPushButton(label)
            b.setCheckable(True)
            b.setChecked(secs == self.vp_secs)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setStyleSheet(_SEG)
            b.clicked.connect(lambda _c=False, v=secs: self.set_vp_secs(v))
            self._vp_btns.append(b)
            lay.addWidget(b)

        lay.addStretch(1)
        self.pill = QtWidgets.QPushButton("⟲ CENTER")
        self.pill.setCursor(QtCore.Qt.PointingHandCursor)
        self.pill.setFixedHeight(24)
        self.pill.setToolTip("Re-center the ladder on the current price (it never follows on its own)")
        self.pill.setStyleSheet(
            "QPushButton { color:#f0b90b; background:rgba(240,185,11,0.10); border:1px solid"
            " rgba(240,185,11,0.45); border-radius:12px; padding:0 14px; font-family:Consolas;"
            " font-size:10px; font-weight:bold; }"
            "QPushButton:hover { background:rgba(240,185,11,0.22); }")
        self.pill.clicked.connect(self.recenter)
        lay.addWidget(self.pill)
        lay.addSpacing(112)                        # keep clear of the window's floating 🔄🔔☰ buttons
        #                                            (three 32px icons overlay the top-right corner)

        root.addWidget(bar)
        self.canvas = _DomCanvas(self)
        root.addWidget(self.canvas, 1)

    # ── settings ───────────────────────────────────────────────────────────────────────────
    def vp_label(self) -> str:
        return next(lbl for s, lbl in _VP_SECS if s == self.vp_secs)

    def set_group(self, g: float) -> None:
        self.group = g                              # the price anchor is group-independent: no jump
        for b, gv in zip(self._grp_btns, _GROUPS):
            b.blockSignals(True); b.setChecked(abs(gv - g) < 1e-9); b.blockSignals(False)
        self.canvas.update()
        self.settingsChanged.emit()

    def set_vp_secs(self, secs: int) -> None:
        self.vp_secs = int(secs)
        for b, (sv, _l) in zip(self._vp_btns, _VP_SECS):
            b.blockSignals(True); b.setChecked(sv == secs); b.blockSignals(False)
        self.canvas.update()
        self.settingsChanged.emit()

    def pan_by(self, rows: int) -> None:
        base = self._anchor_px if self._anchor_px is not None else (self._mid or 0.0)
        if base <= 0:
            return
        self._anchor_px = base + rows * self.group
        self.canvas.update()

    def recenter(self) -> None:
        self._anchor_px = None                      # next paint pins the anchor to the CURRENT mid
        self.canvas.update()

    # ── book + trades ingestion ────────────────────────────────────────────────────────────
    def set_book(self, bids, asks, mid: float) -> None:
        """Per-pulse book: [[price, qty] as str] best-first (worker.depth_book())."""
        try:
            self._bids = [(float(pr), float(q)) for pr, q in bids]
            self._asks = [(float(pr), float(q)) for pr, q in asks]
        except (TypeError, ValueError):
            return
        if mid and mid > 0:
            self._mid = float(mid)

    def _pack(self, ts, pr, qt, sd):
        tick = np.rint(np.asarray(pr, dtype=np.float64) / _TICK).astype(np.int64)
        buy = np.where(np.asarray(sd) > 0, np.asarray(qt, dtype=np.float64), 0.0)
        sell = np.where(np.asarray(sd) > 0, 0.0, np.asarray(qt, dtype=np.float64))
        return (np.asarray(ts, dtype=np.float64) / 1000.0, tick, buy, sell)

    def ingest_live(self, ts, pr, qt, sd) -> bool:
        n = len(ts)
        if not n:
            return False
        i0 = 0
        if self._live_t0 == 0.0:
            if self._chunks:                       # window landed first -> cut the <=1-pulse overlap
                last = max(float(c[0][-1]) for c in self._chunks if len(c[0]))
                while i0 < n and float(ts[i0]) / 1000.0 <= last:
                    i0 += 1
                if i0 >= n:
                    return False
            self._live_t0 = float(ts[i0])
        pk = self._pack(ts[i0:], pr[i0:], qt[i0:], sd[i0:])
        if len(pk[0]):
            self._chunks.append(pk)
            self._cat = None
            self._last_px = float(pr[-1])
            self._last_side = int(sd[-1])
        return True

    def ingest_window(self, ts, pr, qt, sd) -> bool:
        n = len(ts)
        if not n:
            return False
        cut = (self._live_t0 or float("inf")) / 1000.0
        tsf = np.asarray(ts, dtype=np.float64) / 1000.0
        keep = tsf < cut
        if not keep.any():
            return False
        pk = self._pack(np.asarray(ts)[keep], np.asarray(pr)[keep], np.asarray(qt)[keep],
                        np.asarray(sd)[keep])
        self._chunks.insert(0, pk)                 # history prepends (arrays stay time-ordered)
        self._cat = None
        return True

    def reset(self) -> None:
        self._chunks = []
        self._cat = None
        self._live_t0 = 0.0
        self._bids = []
        self._asks = []
        self._anchor_px = None
        self.canvas.update()

    def _trades_cat(self):
        """Concatenated (ts, tickbin, buyQ, sellQ), pruned past the largest VP window; cached."""
        if self._cat is None:
            if not self._chunks:
                self._cat = (np.empty(0), np.empty(0, dtype=np.int64), np.empty(0), np.empty(0))
            else:
                ts = np.concatenate([c[0] for c in self._chunks])
                tk = np.concatenate([c[1] for c in self._chunks])
                bq = np.concatenate([c[2] for c in self._chunks])
                sq = np.concatenate([c[3] for c in self._chunks])
                lo = np.searchsorted(ts, time.time() - _VP_SECS[-1][0] - _PRUNE_SLACK)
                if lo > 0:
                    ts, tk, bq, sq = ts[lo:], tk[lo:], bq[lo:], sq[lo:]
                self._cat = (ts, tk, bq, sq)
                if len(self._chunks) > 64 or lo > 20000:      # re-chunk so concat stays cheap
                    self._chunks = [self._cat]
        return self._cat

    def mark_dirty(self) -> None:
        self._cat = None                           # time moved on -> re-prune on the next frame

    def vp_bins(self, g: float, lo_bin: int, hi_bin: int) -> dict:
        """{group-bin: (boughtQ, soldQ)} for the visible rows over the selected window — feeds the
        BOUGHT/SOLD columns and the gray VP (total)."""
        ts, tk, bq, sq = self._trades_cat()
        if not len(ts):
            return {}
        i0 = np.searchsorted(ts, time.time() - self.vp_secs)
        if i0 >= len(ts):
            return {}
        ticks_per = max(1, int(round(g / _TICK)))
        gb = tk[i0:] // ticks_per
        m = (gb >= lo_bin) & (gb <= hi_bin)
        if not m.any():
            return {}
        gbm = gb[m].astype(np.int64)
        off = gbm - lo_bin
        nbins = hi_bin - lo_bin + 1
        buys = np.bincount(off, weights=bq[i0:][m], minlength=nbins)
        sells = np.bincount(off, weights=sq[i0:][m], minlength=nbins)
        out = {}
        for j in np.nonzero(buys + sells)[0]:
            out[int(lo_bin + j)] = (float(buys[j]), float(sells[j]))
        return out
