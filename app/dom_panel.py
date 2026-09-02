"""DOM scanner mode — DeepChart-style Depth-of-Market ladder + Volume Profile (no trading sim).

Full-pane price ladder, newest book every 0.4s pulse (top 200 levels/side off the PULSE):
    [ BID SIZE | SOLD | PRICE | BOUGHT | ASK SIZE | VOLUME PROFILE ]
Asks stack above the spread (red, growing right), bids below (green, growing left). SOLD/BOUGHT
(DeepChart's B.T/A.T) are the EXECUTED taker sell / taker buy volumes at each price over the
selected window — numbers beside the price, max-per-column bolded. The VP column sits on the
RIGHT, neutral GRAY (user 2026-09-01: no buy/sell split), POC row gold. Book walls (>= P90 of
visible levels) render brighter + bold. A book-imbalance strip (whole 200-level book, SOL) sits
on top. GROUP re-bins 0.01/0.02/0.05/0.10 (trades stored at TICK resolution — regroup is free,
and the ladder HOLDS its price position). Centering (entry / ⟲ CENTER / double-click) arms
FOLLOW: the ladder stays put while the live price is on-screen and snaps back to center only
when price crosses the visible extremes; ANY manual pan (wheel or click-drag) disarms follow
until the next centering (user 2026-09-01). The VP histogram is REVERSED: right-anchored,
growing left. Sizes are SOL; the last trade highlights its price cell (bold, red/green bg).
"""

from __future__ import annotations

import time

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from . import config
from .size_dist import ClickableLabel, SizeDistPopup   # MIN SIZE caption -> size distribution
from .trades_tape import _slider_to_usd, _usd_to_slider, _fmt_usd   # the SAME log MIN SIZE mapping
#                                                                     as the Trades scanner mode

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
_LVN      = QtGui.QColor(168, 110, 240)    # Low-Volume Node — the app-wide LVN purple
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
        self._drag = None                          # pan drag state (press y, anchor price)
        self._area_drag = None                     # Ctrl+drag area-in-progress [y_press, y_now]
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

    # ── Ctrl+drag AREAS (user 2026-09-01): mark a band of levels; Ctrl+double-click deletes ──
    def _geom(self):
        """(y0, n_rows, top_bin, g) of the CURRENT ladder rows — same math as paintEvent; None pre-paint."""
        P = self._p
        if P._anchor_px is None:
            return None
        g = P.group
        tpg = max(1, int(round(g / _TICK)))
        y0 = _STRIP_H + _HDR_H
        n_rows = max(1, (self.height() - y0) // _ROW_H)
        return y0, n_rows, int(round(P._anchor_px / _TICK)) // tpg + n_rows // 2, g

    def _band_from_ys(self, ya: float, yb: float):
        """Snap two canvas ys to whole rows -> (lo_price, hi_price) covering every level dragged over."""
        geo = self._geom()
        if geo is None:
            return None
        y0, _n, top_bin, g = geo
        b1 = top_bin - int((ya - y0) // _ROW_H)
        b2 = top_bin - int((yb - y0) // _ROW_H)
        lo, hi = min(b1, b2), max(b1, b2)
        return (lo * g, (hi + 1) * g)              # PRICE-anchored: pans/regroups leave it on its levels

    def _area_begin(self, y: float) -> None:
        self._area_drag = [y, y]
        self.update()

    def _area_update(self, y: float) -> None:
        if getattr(self, "_area_drag", None) is not None:
            self._area_drag[1] = y
            self.update()

    def _area_commit(self) -> None:
        ad = getattr(self, "_area_drag", None)
        self._area_drag = None
        if ad is None or abs(ad[1] - ad[0]) < 3:   # zero-movement Ctrl+click is NOT an area — keeps the
            self.update()                          # Ctrl+double-click delete from first minting a band
            return
        band = self._band_from_ys(ad[0], ad[1])
        if band is not None:
            self._p._areas.append(band)
        self.update()

    def _area_delete_at(self, y: float) -> bool:
        """Delete the most recent area covering the clicked level. True if one was removed."""
        geo = self._geom()
        if geo is None:
            return False
        y0, _n, top_bin, g = geo
        px = (top_bin - (y - y0) / _ROW_H + 0.5) * g          # continuous price at the click
        for k in range(len(self._p._areas) - 1, -1, -1):
            lo, hi = self._p._areas[k]
            if lo <= px < hi:
                del self._p._areas[k]
                self.update()
                return True
        return False

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
        self._p._follow = False                    # going off-center deactivates auto-follow
        self.update()

    def _drag_end(self) -> None:
        self._drag = None

    def mousePressEvent(self, ev) -> None:
        if ev.button() == QtCore.Qt.LeftButton:
            if ev.modifiers() & QtCore.Qt.ControlModifier:
                self._area_begin(ev.position().y())        # Ctrl+drag = mark an area, no panning
                self.setCursor(QtCore.Qt.CrossCursor)
            else:
                self._drag_start(ev.position().y())
                self.setCursor(QtCore.Qt.ClosedHandCursor)
            ev.accept()

    def mouseMoveEvent(self, ev) -> None:
        if getattr(self, "_area_drag", None) is not None:
            self._area_update(ev.position().y())
        else:
            self._drag_to(ev.position().y())
        self._hover_y = ev.position().y()          # hover row highlight follows the cursor
        self.update()
        ev.accept()

    def mouseReleaseEvent(self, ev) -> None:
        self._area_commit()
        self._drag_end()
        self.unsetCursor()
        ev.accept()

    def mouseDoubleClickEvent(self, ev) -> None:
        if ev.button() == QtCore.Qt.LeftButton and (ev.modifiers() & QtCore.Qt.ControlModifier):
            self._area_drag = None                 # the double-click's press armed a zero-drag; drop it
            self._area_delete_at(ev.position().y())
            ev.accept()
            return
        if ev.button() == QtCore.Qt.LeftButton:    # plain double-click = re-center on the current price
            self._drag = None                      # drop the pan the double-click's press armed (its old
            self._p.recenter()                     # anchor would undo the recenter on a stray move)
            ev.accept()
            return
        super().mouseDoubleClickEvent(ev)

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
        traded_w = 118                             # fits "1.5M (45%, 12)" — the player-filter readout
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
        _fon = P.min_usd > 0                       # filter ON -> USD player readouts; ALL -> plain SOL
        p.drawText(QtCore.QRect(c_sold0, y0, traded_w, _HDR_H), QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter,
                   "SOLD $" if _fon else "SOLD")
        p.drawText(QtCore.QRect(c_price0, y0, price_w, _HDR_H), QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter,
                   "PRICE")
        p.drawText(QtCore.QRect(c_bought0, y0, traded_w, _HDR_H), QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter,
                   "BOUGHT $" if _fon else "BOUGHT")
        p.drawText(QtCore.QRect(c_ask0, y0, 120, _HDR_H), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, "ASKS")
        p.drawText(QtCore.QRect(c_vp0, y0, vp_w, _HDR_H), QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                   f"VOLUME · {P.vp_label()}")
        p.setPen(QtGui.QPen(_RULE, 1))
        p.drawLine(pad, y0 + _HDR_H - 1, w - pad, y0 + _HDR_H - 1)
        y0 += _HDR_H

        # ── rows: bin the book + traded volumes to the current group ───────────────────────
        mid = P._mid if P._mid > 0 else (bids[0][0] + asks[0][0]) / 2.0 if (bids and asks) else \
            (bids[0][0] if bids else asks[0][0])
        n_rows = max(1, (h - y0) // _ROW_H)
        # ALL binning happens in integer TICK space then floors to the group — price/g floats off-by-one
        # at the 4th decimal (235.67//0.01 -> 23566), int ticks never do. A bin = [b*g, b*g+g).
        tpg = max(1, int(round(g / _TICK)))
        if P._anchor_px is None:
            P._anchor_px = mid                     # center ONCE (entry / ⟲ CENTER / double-click)
        elif P._follow:
            # FOLLOW (user 2026-09-01): armed by centering, disarmed by any manual pan. The ladder
            # stays put while the live price is on-screen; the moment it crosses the visible
            # upper/lower extreme, snap back to center on it.
            _tb = int(round(P._anchor_px / _TICK)) // tpg + n_rows // 2
            _mb = int(round(mid / _TICK)) // tpg
            if _mb > _tb or _mb < _tb - n_rows + 1:
                P._anchor_px = mid
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

        stats = P.trade_stats(g, top_bin - n_rows + 1, top_bin, P.min_usd) if _fon else {}

        vis_b = [bid_bins.get(top_bin - i, 0.0) for i in range(n_rows)]
        vis_a = [ask_bins.get(top_bin - i, 0.0) for i in range(n_rows)]
        max_sz = max(max(vis_b, default=0.0), max(vis_a, default=0.0)) or 1.0
        max_vp = max((bq + sq for bq, sq in vp.values()), default=0.0) or 1.0
        # SOLD/BOUGHT emphasis: same rule as the VP gold (user 2026-09-02) — per-SIDE nearest-rank
        # P70 over the WHOLE window's levels, never the visible rows (scroll can't re-crown anything).
        thr_b, thr_s = P.side_gold_thresholds(g, P.min_usd)
        lvn_thr = P.vp_lvn_threshold(g)                # LVN = bottom decile of the same population
        nz = sorted([v for v in vis_b + vis_a if v > 0])
        p90 = nz[int(len(nz) * 0.9)] if nz else float("inf")   # wall emphasis threshold (visible P90)
        # VP gold = levels >= P70 of the WHOLE window's per-level volumes (user 2026-09-02): the old
        # single POC was elected among the VISIBLE rows only, so scrolling re-crowned it constantly.
        gold_thr = P.vp_gold_threshold(g)

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

            # SOLD / BOUGHT — window-scoped per level (user 2026-09-01).
            # MIN SIZE at ALL:   plain SOL volume, nothing else (no usd, no parentheses)
            # MIN SIZE filtered: "7.0K (45%, 3)" = the level's TOTAL usd that side (INVARIANT —
            #                     the filter never changes it), then the share contributed by
            #                     trades >= min and how many such trades (these two vary).
            # Rows with no QUALIFYING trade hide entirely (user 2026-09-02: no '(0%, 0)' noise);
            # P70+ levels (whole-window, per side) are bolded.
            if _fon:
                stt = stats.get(b)
                if stt is not None:
                    _tbu, _tsu, _fbu, _fsu, _cb, _cs = stt
                    if _tsu > 0 and _cs > 0:
                        _pct = _fsu / _tsu * 100.0
                        hot = _fsu >= thr_s
                        col = QtGui.QColor(_SELL)
                        col.setAlpha(235 if hot else 150)
                        p.setPen(col)
                        p.setFont(self._font_b if hot else self._font)
                        p.drawText(QtCore.QRect(c_sold0, ry, traded_w - 4, _ROW_H),
                                   QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                                   f"{_kfmt(_tsu)} ({_pct:.0f}%, {_cs})")
                    if _tbu > 0 and _cb > 0:
                        _pct = _fbu / _tbu * 100.0
                        hot = _fbu >= thr_b
                        col = QtGui.QColor(_BUY)
                        col.setAlpha(235 if hot else 150)
                        p.setPen(col)
                        p.setFont(self._font_b if hot else self._font)
                        p.drawText(QtCore.QRect(c_bought0 + 4, ry, traded_w - 4, _ROW_H),
                                   QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                                   f"{_kfmt(_tbu)} ({_pct:.0f}%, {_cb})")
            else:
                _vb, _vs = vp.get(b, (0.0, 0.0))               # ALL -> the SOL volumes, straight up
                if _vs > 0:
                    hot = _vs >= thr_s
                    col = QtGui.QColor(_SELL)
                    col.setAlpha(235 if hot else 150)
                    p.setPen(col)
                    p.setFont(self._font_b if hot else self._font)
                    p.drawText(QtCore.QRect(c_sold0, ry, traded_w - 4, _ROW_H),
                               QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, _kfmt(_vs))
                if _vb > 0:
                    hot = _vb >= thr_b
                    col = QtGui.QColor(_BUY)
                    col.setAlpha(235 if hot else 150)
                    p.setPen(col)
                    p.setFont(self._font_b if hot else self._font)
                    p.drawText(QtCore.QRect(c_bought0 + 4, ry, traded_w - 4, _ROW_H),
                               QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, _kfmt(_vb))
            p.setFont(self._font)

            # VP (far right, neutral GRAY — total executed volume in SOL; POC row gold). REVERSED
            # (user 2026-09-01): bars anchor on the RIGHT edge and grow LEFT, label left of the tip.
            bq, sq = vp.get(b, (0.0, 0.0))
            if bq + sq > 0:
                bw_ = (bq + sq) / max_vp * (vp_w - 44)
                p.setPen(QtCore.Qt.NoPen)
                _gold = (bq + sq) >= gold_thr
                _lvn = (not _gold) and (bq + sq) <= lvn_thr   # gold wins a tiny-n overlap
                if _gold:
                    col = QtGui.QColor(_GOLD)
                    col.setAlpha(200)
                elif _lvn:
                    col = QtGui.QColor(_LVN)
                    col.setAlpha(150)
                else:
                    col = QtGui.QColor(_VP_GRAY)
                    col.setAlpha(90)
                p.fillRect(QtCore.QRectF(w - pad - max(1.5, bw_), ry + 3.5, max(1.5, bw_), _ROW_H - 7), col)
                p.setPen(_GOLD if _gold else (_LVN if _lvn else _DIM_TXT))
                p.setFont(self._font_b if (_gold or _lvn) else self._font)
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
            # ONLY the current (last-traded) level is highlighted (user 2026-09-01): bold on a solid
            # red/green background by aggressor side. Best bid/ask get no emphasis, no spread line.
            is_last = b == last_bin and P._last_side is not None
            if is_last:
                chip = QtGui.QColor(_BUY if P._last_side else _SELL)
                chip.setAlpha(175)
                path = QtGui.QPainterPath()
                path.addRoundedRect(QtCore.QRectF(c_price0 + 3, ry + 1.5, price_w - 6, _ROW_H - 3), 3.0, 3.0)
                p.fillPath(path, chip)
            p.setPen(QtGui.QColor(255, 255, 255) if is_last else _DIM_TXT)
            p.setFont(self._font_b if is_last else self._font)
            p.drawText(QtCore.QRect(c_price0, ry, price_w, _ROW_H), QtCore.Qt.AlignCenter,
                       f"{price:,.2f}")
        p.setFont(self._font)

        # Ctrl+drag AREAS: translucent bands over the marked levels (price-anchored -> they pan and
        # regroup WITH their levels). The in-progress drag renders the same way as a live preview.
        bands = list(P._areas)
        ad = getattr(self, "_area_drag", None)
        if ad is not None and abs(ad[1] - ad[0]) >= 3:
            bp = self._band_from_ys(ad[0], ad[1])
            if bp is not None:
                bands.append(bp)
        for lo, hi in bands:
            ytop = y0 + (top_bin + 1 - hi / g) * _ROW_H       # y of the band's top edge (hi is exclusive)
            ybot = y0 + (top_bin + 1 - lo / g) * _ROW_H
            ytop_c = max(float(y0), ytop)
            ybot_c = min(float(y0 + n_rows * _ROW_H), ybot)
            if ybot_c <= ytop_c:
                continue                                       # panned fully out of view
            p.setPen(QtGui.QPen(QtGui.QColor(225, 230, 240, 130), 1))
            p.setBrush(QtGui.QColor(200, 210, 225, 26))
            p.drawRoundedRect(QtCore.QRectF(pad - 4, ytop_c + 0.5, w - 2 * pad + 8,
                                            ybot_c - ytop_c - 1.0), 3.0, 3.0)
            p.setBrush(QtCore.Qt.NoBrush)

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
    """Toolbar (GROUP + VP dropdowns, MIN SIZE slider) over the painted DOM ladder. No center
    button — double-click re-centers (user 2026-09-01)."""

    settingsChanged = QtCore.Signal()
    customVpRequested = QtCore.Signal(float)       # custom VP start (epoch s) — the terminal fetches
    #                                                any tape history older than what's already stored

    def __init__(self) -> None:
        super().__init__()
        self.group: float = _GROUPS[0]
        self.vp_secs: int = 3600                   # default 1H (NOT _VP_SECS[-1] — 6H exists now)
        self.vp_custom_t0 = None                   # custom VP start (epoch s); None = preset window
        self.min_usd: float = 0.0                  # MIN SIZE player filter (USD/trade) for SOLD/BOUGHT
        self._dist = None                          # lazily-built aggressor size-distribution popup
        self._anchor_px: float | None = None       # ladder anchor PRICE; None = center on next paint.
        #                                            Set once, then FIXED — the ladder never auto-scrolls;
        #                                            price-based so it survives a GROUP change in place.
        self._areas: list = []                     # Ctrl+drag level bands [(lo_price, hi_price)); session-
        #                                            lifetime user annotations — reset() deliberately keeps them
        self._follow: bool = True                  # armed by centering: re-center when the live price leaves
        #                                            the visible rows; ANY manual pan (wheel/drag) disarms it
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

        _COMBO = ("QComboBox { color:#e6ecf4; background:#161b24; border:1px solid #242c3a;"
                  " border-radius:10px; padding:2px 10px; font-family:Consolas; font-size:10px;"
                  " font-weight:bold; } QComboBox::drop-down { border:none; width:16px; }"
                  " QComboBox QAbstractItemView { color:#e6ecf4; background:#161b24;"
                  " border:1px solid #242c3a; selection-background-color:#f0b90b;"
                  " selection-color:#0e1218; font-family:Consolas; font-size:10px; }")

        lay.addWidget(cap("GROUP"))
        self.grp_combo = QtWidgets.QComboBox()
        for gv in _GROUPS:
            self.grp_combo.addItem(f"{gv:.2f}", gv)
        self.grp_combo.setCurrentIndex(0)
        self.grp_combo.setCursor(QtCore.Qt.PointingHandCursor)
        self.grp_combo.setStyleSheet(_COMBO)
        self.grp_combo.currentIndexChanged.connect(
            lambda i: self.set_group(self.grp_combo.itemData(i)))
        lay.addWidget(self.grp_combo)

        lay.addSpacing(10)
        lay.addWidget(cap("VP"))
        self.vp_combo = QtWidgets.QComboBox()
        for secs, label in _VP_SECS:
            self.vp_combo.addItem(label, secs)
        self.vp_combo.addItem("Custom…", -1)       # VP measured from a user-picked date+hour to now
        self.vp_combo.setCurrentIndex(2)           # 1H default
        self.vp_combo.setCursor(QtCore.Qt.PointingHandCursor)
        self.vp_combo.setStyleSheet(_COMBO)
        self.vp_combo.currentIndexChanged.connect(self._on_vp_combo)
        lay.addWidget(self.vp_combo)

        # custom-start picker — visible only while the VP combo is on "Custom…"
        self.vp_custom_edit = QtWidgets.QDateTimeEdit(
            QtCore.QDateTime.currentDateTime().addSecs(-3600))
        self.vp_custom_edit.setDisplayFormat("dd/MM HH:mm")
        self.vp_custom_edit.setCalendarPopup(True)
        self.vp_custom_edit.setStyleSheet(
            "QDateTimeEdit { color:#f0b90b; background:#161b24; border:1px solid #242c3a;"
            " border-radius:10px; padding:2px 8px; font-family:Consolas; font-size:10px;"
            " font-weight:bold; }")
        self.vp_custom_edit.setVisible(False)
        self._custom_debounce = QtCore.QTimer(self)      # spinning the hour arrows fires per step —
        self._custom_debounce.setSingleShot(True)        # debounce so deep-history fetches don't spam
        self._custom_debounce.setInterval(600)
        self._custom_debounce.timeout.connect(self._apply_custom_edit)
        self.vp_custom_edit.dateTimeChanged.connect(
            lambda _dt: self._custom_debounce.start())
        lay.addWidget(self.vp_custom_edit)

        lay.addSpacing(10)
        _ms = ClickableLabel("MIN SIZE")               # click -> aggressor size-distribution popup
        _ms.setStyleSheet("color:#7a8496; font-family:Consolas; font-size:9px; font-weight:bold;"
                          "letter-spacing:1px; border:none; text-decoration:underline;")
        _ms.setCursor(QtCore.Qt.PointingHandCursor)
        _ms.setToolTip("Click: buyer/seller aggressor size distribution (click the curve to set the filter)")
        _ms.clicked.connect(self._open_size_dist)
        lay.addWidget(_ms)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setValue(0)
        self.slider.setFixedWidth(170)
        self.slider.setStyleSheet("""
            QSlider { border:none; }
            QSlider::groove:horizontal { height:4px; border-radius:2px; background:#1d2632; }
            QSlider::sub-page:horizontal { height:4px; border-radius:2px;
                background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #2ebd85, stop:1 #f0b90b); }
            QSlider::handle:horizontal { width:14px; height:14px; margin:-5px 0; border-radius:8px;
                background:#e6ecf4; border:2px solid #f0b90b; }
        """)
        self.slider.valueChanged.connect(self._on_min_slider)
        lay.addWidget(self.slider)
        self.min_lbl = QtWidgets.QLabel("ALL")
        self.min_lbl.setFixedWidth(76)
        self.min_lbl.setStyleSheet("color:#f0b90b; font-family:Consolas; font-size:11px;"
                                   "font-weight:bold; border:none;")
        lay.addWidget(self.min_lbl)

        lay.addStretch(1)                          # no CENTER button — double-click re-centers

        root.addWidget(bar)
        self.canvas = _DomCanvas(self)
        root.addWidget(self.canvas, 1)

    # ── settings ───────────────────────────────────────────────────────────────────────────
    def vp_label(self) -> str:
        if self.vp_custom_t0 is not None:          # (arrow OUTSIDE strftime — Windows' locale codec
            return time.strftime("%d/%m %H:%M", time.localtime(self.vp_custom_t0)) + " →"
        return next(lbl for s, lbl in _VP_SECS if s == self.vp_secs)

    def _vp_cutoff(self) -> float:
        """The VP/player window's left edge (epoch s): a preset trails 'now'; custom is a fixed start."""
        if self.vp_custom_t0 is not None:
            return self.vp_custom_t0
        return time.time() - self.vp_secs

    def set_group(self, g: float) -> None:
        self.group = g                              # the price anchor is group-independent: no jump
        for i in range(self.grp_combo.count()):
            if abs(self.grp_combo.itemData(i) - g) < 1e-9:
                self.grp_combo.blockSignals(True)
                self.grp_combo.setCurrentIndex(i)
                self.grp_combo.blockSignals(False)
                break
        self.canvas.update()
        self.settingsChanged.emit()

    def set_vp_secs(self, secs: int) -> None:
        self.vp_secs = int(secs)
        self.vp_custom_t0 = None                    # a preset always clears the custom start
        for i in range(self.vp_combo.count()):
            if self.vp_combo.itemData(i) == secs:
                self.vp_combo.blockSignals(True)
                self.vp_combo.setCurrentIndex(i)
                self.vp_combo.blockSignals(False)
                break
        self.vp_custom_edit.setVisible(False)
        self.canvas.update()
        self.settingsChanged.emit()

    def set_vp_custom(self, t0: float) -> None:
        """VP/player window from a user-picked date+hour to now. Emits customVpRequested so the
        terminal can fetch tape history older than what's already stored (72h retention cap)."""
        t0 = min(float(t0), time.time() - 60.0)     # a future start would be an empty window
        self.vp_custom_t0 = t0
        self.vp_combo.blockSignals(True)
        self.vp_combo.setCurrentIndex(self.vp_combo.count() - 1)   # "Custom…"
        self.vp_combo.blockSignals(False)
        self.vp_custom_edit.blockSignals(True)
        self.vp_custom_edit.setDateTime(QtCore.QDateTime.fromSecsSinceEpoch(int(t0)))
        self.vp_custom_edit.blockSignals(False)
        self.vp_custom_edit.setVisible(True)
        self._cat = None                            # the prune horizon just moved — rebuild
        self.canvas.update()
        self.settingsChanged.emit()
        self.customVpRequested.emit(t0)

    def _on_vp_combo(self, i: int) -> None:
        data = self.vp_combo.itemData(i)
        if data == -1:
            self.set_vp_custom(self.vp_custom_edit.dateTime().toSecsSinceEpoch())
        else:
            self.set_vp_secs(int(data))

    def _apply_custom_edit(self) -> None:
        if self.vp_combo.currentData() == -1:       # debounced picker change while on Custom…
            self.set_vp_custom(self.vp_custom_edit.dateTime().toSecsSinceEpoch())

    def size_samples(self):
        """(usd, is_buy) per trade over the CURRENT VP window — feeds the size-distribution popup.
        A SOL trade's price IS its tick, so usd = qty * tickbin * TICK is exact."""
        ts, tk, bq, sq = self._trades_cat()
        if not len(ts):
            return np.empty(0), np.empty(0, dtype=bool)
        i0 = np.searchsorted(ts, self._vp_cutoff())
        px = tk[i0:] * _TICK
        return (bq[i0:] + sq[i0:]) * px, bq[i0:] > 0

    def _open_size_dist(self) -> None:
        if self._dist is None:
            self._dist = SizeDistPopup(self.size_samples, lambda: f"DOM · {self.vp_label()}",
                                       lambda: self.min_usd, self.set_min_usd, parent=self.window())
        self._dist.show()
        self._dist.raise_()
        self._dist.activateWindow()

    def set_min_usd(self, usd: float) -> None:
        """Programmatic restore (saved UI state) — moves the slider, which re-derives everything."""
        self.slider.setValue(_usd_to_slider(usd))

    def _on_min_slider(self, v: int) -> None:
        self.min_usd = _slider_to_usd(v)
        self.min_lbl.setText("ALL" if self.min_usd <= 0 else f"≥ {_fmt_usd(self.min_usd)}")
        self.canvas.update()
        self.settingsChanged.emit()

    def pan_by(self, rows: int) -> None:
        base = self._anchor_px if self._anchor_px is not None else (self._mid or 0.0)
        if base <= 0:
            return
        self._anchor_px = base + rows * self.group
        self._follow = False                        # going off-center deactivates auto-follow
        self.canvas.update()

    def recenter(self) -> None:
        self._anchor_px = None                      # next paint pins the anchor to the CURRENT mid
        self._follow = True                         # ...and re-arms beyond-the-extremes auto-follow
        self.canvas.update()

    # ── book + trades ingestion ────────────────────────────────────────────────────────────
    def set_book(self, bids, asks, mid: float) -> None:
        """Per-pulse book: [[price, qty] as str] best-first (worker.depth_book())."""
        try:
            self._bids = [(float(pr), float(q)) for pr, q in bids]
            self._asks = [(float(pr), float(q)) for pr, q in asks]
        except (TypeError, ValueError):
            return
        # mid from the BOOK, not the passed latest_price: on a LITE worker (DOM/Trades start windows)
        # latest_price rides the never-subscribed bucket stream and stays FROZEN at the boot price —
        # double-click "center" then snapped to a stale level (user 2026-09-02). The pulse book is
        # always fresh; the passed mid is only the empty-book fallback.
        if self._bids and self._asks:
            self._mid = (self._bids[0][0] + self._asks[0][0]) / 2.0
        elif mid and mid > 0:
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
        self._follow = True
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
                _hz = time.time() - _VP_SECS[-1][0] - _PRUNE_SLACK
                if self.vp_custom_t0 is not None:        # a custom start older than 6h needs its data kept
                    _hz = min(_hz, self.vp_custom_t0 - _PRUNE_SLACK)
                lo = np.searchsorted(ts, _hz)
                if lo > 0:
                    ts, tk, bq, sq = ts[lo:], tk[lo:], bq[lo:], sq[lo:]
                self._cat = (ts, tk, bq, sq)
                if len(self._chunks) > 64 or lo > 20000:      # re-chunk so concat stays cheap
                    self._chunks = [self._cat]
        return self._cat

    def mark_dirty(self) -> None:
        self._cat = None                           # time moved on -> re-prune on the next frame

    def trade_stats(self, g: float, lo_bin: int, hi_bin: int, min_usd: float) -> dict:
        """Per-level PLAYER stats over the selected window for the SOLD/BOUGHT columns, in USDT:
        {group-bin: (tot_buy_usd, tot_sell_usd, flt_buy_usd, flt_sell_usd, cnt_buy, cnt_sell)} where
        flt_*/cnt_* cover only trades whose OWN size is >= min_usd (min_usd=0 -> everything counts).
        A SOL trade's exact price IS its tick (tick 0.01), so usd = qty * tickbin * TICK is exact."""
        ts, tk, bq, sq = self._trades_cat()
        if not len(ts):
            return {}
        i0 = np.searchsorted(ts, self._vp_cutoff())
        if i0 >= len(ts):
            return {}
        ticks_per = max(1, int(round(g / _TICK)))
        tk2 = tk[i0:]
        gb = tk2 // ticks_per
        m = (gb >= lo_bin) & (gb <= hi_bin)
        if not m.any():
            return {}
        px = tk2[m] * _TICK
        ub = bq[i0:][m] * px                       # per-trade USD, buy rows (0 on sell rows)
        us = sq[i0:][m] * px
        off = (gb[m] - lo_bin).astype(np.int64)
        n = hi_bin - lo_bin + 1
        tot_b = np.bincount(off, weights=ub, minlength=n)
        tot_s = np.bincount(off, weights=us, minlength=n)
        if min_usd > 0:
            big = (ub + us) >= min_usd             # one side is always 0 -> this IS the trade's size
            flt_b = np.bincount(off[big], weights=ub[big], minlength=n)
            flt_s = np.bincount(off[big], weights=us[big], minlength=n)
            cnt_b = np.bincount(off[big & (ub > 0)], minlength=n)
            cnt_s = np.bincount(off[big & (us > 0)], minlength=n)
        else:
            flt_b, flt_s = tot_b, tot_s
            cnt_b = np.bincount(off[ub > 0], minlength=n)
            cnt_s = np.bincount(off[us > 0], minlength=n)
        out = {}
        for j in np.nonzero(tot_b + tot_s)[0]:
            out[int(lo_bin + j)] = (float(tot_b[j]), float(tot_s[j]), float(flt_b[j]),
                                    float(flt_s[j]), int(cnt_b[j]), int(cnt_s[j]))
        return out

    def side_gold_thresholds(self, g: float, min_usd: float):
        """(bought_thr, sold_thr): per-SIDE nearest-rank P70 of per-level values across the WHOLE
        selected window — the SOLD/BOUGHT bolding rule (same spirit as vp_gold_threshold; user
        2026-09-02). min_usd == 0 -> SOL volumes (the ALL display); filtered -> USD of trades >= min
        only (matching what the cells show). inf when a side has nothing -> nothing bolds."""
        ts, tk, bq, sq = self._trades_cat()
        if not len(ts):
            return float("inf"), float("inf")
        i0 = np.searchsorted(ts, self._vp_cutoff())
        if i0 >= len(ts):
            return float("inf"), float("inf")
        ticks_per = max(1, int(round(g / _TICK)))
        gb = tk[i0:] // ticks_per
        if min_usd > 0:
            px = tk[i0:] * _TICK
            vb_r = bq[i0:] * px
            vs_r = sq[i0:] * px
            big = (vb_r + vs_r) >= min_usd
            gb, vb_r, vs_r = gb[big], vb_r[big], vs_r[big]
        else:
            vb_r = bq[i0:]
            vs_r = sq[i0:]
        if not len(gb):
            return float("inf"), float("inf")
        _uniq, inv = np.unique(gb, return_inverse=True)

        def _p70(w):
            tot = np.bincount(inv, weights=w)
            nzv = np.sort(tot[tot > 0.0])
            if not len(nzv):
                return float("inf")
            k = min(len(nzv) - 1, max(0, int(np.ceil(0.70 * len(nzv))) - 1))
            return float(nzv[k])

        return _p70(vb_r), _p70(vs_r)

    def vp_gold_threshold(self, g: float) -> float:
        """The VP's GOLD threshold: nearest-rank P70 of per-level total volumes across the WHOLE
        selected window at the current grouping — view-independent (never re-elected by scrolling;
        user 2026-09-02). Nearest-rank, not mean/range — level volumes are fat-tailed."""
        ts, tk, bq, sq = self._trades_cat()
        if not len(ts):
            return float("inf")
        i0 = np.searchsorted(ts, self._vp_cutoff())
        if i0 >= len(ts):
            return float("inf")
        ticks_per = max(1, int(round(g / _TICK)))
        gb = tk[i0:] // ticks_per
        vol = bq[i0:] + sq[i0:]
        _uniq, inv = np.unique(gb, return_inverse=True)
        tot = np.bincount(inv, weights=vol)
        nzv = np.sort(tot[tot > 0.0])
        if not len(nzv):
            return float("inf")
        k = min(len(nzv) - 1, max(0, int(np.ceil(0.70 * len(nzv))) - 1))
        return float(nzv[k])

    def vp_lvn_threshold(self, g: float) -> float:
        """LVN threshold: nearest-rank BOTTOM-decile cut (the P90 rule mirrored — user 2026-09-02)
        of per-level total volumes across the WHOLE window. Levels with volume <= this render
        purple; view-independent like the gold rule. -inf when there is nothing to rank."""
        ts, tk, bq, sq = self._trades_cat()
        if not len(ts):
            return float("-inf")
        i0 = np.searchsorted(ts, self._vp_cutoff())
        if i0 >= len(ts):
            return float("-inf")
        ticks_per = max(1, int(round(g / _TICK)))
        gb = tk[i0:] // ticks_per
        vol = bq[i0:] + sq[i0:]
        _uniq, inv = np.unique(gb, return_inverse=True)
        tot = np.bincount(inv, weights=vol)
        nzv = np.sort(tot[tot > 0.0])
        if len(nzv) < 3:
            return float("-inf")                      # too few levels to call anything "low"
        k = min(len(nzv) - 1, max(0, int(np.ceil(0.10 * len(nzv))) - 1))
        return float(nzv[k])

    def vp_bins(self, g: float, lo_bin: int, hi_bin: int) -> dict:
        """{group-bin: (boughtQ, soldQ)} for the visible rows over the selected window — feeds the
        BOUGHT/SOLD columns and the gray VP (total)."""
        ts, tk, bq, sq = self._trades_cat()
        if not len(ts):
            return {}
        i0 = np.searchsorted(ts, self._vp_cutoff())
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
