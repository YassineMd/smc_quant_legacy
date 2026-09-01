"""Live Market Trades tape (scanner mode "trades") — the Binance-style Time / Price / Amount table.

The daemon already captures every aggTrade (feeds._capture_trade -> depth_store.trade_tape + the
per-pulse live batches), so this panel only STORES and DRAWS: the terminal feeds it a one-shot
history window on mode entry (trades_window) plus the 0.4s live batches that ride the depth
subscription. Newest trade on top, price colored by aggressor side (taker buy green / taker
sell red), amount in USD (price x qty). A log-scale MIN SIZE slider filters the tape; big
prints get progressively stronger styling (tint -> accent bar -> whale glow). Scrolling back
freezes the tape (the LIVE pill turns to PAUSED); click the pill or scroll back to the top to
resume following. A 60s buy-vs-sell pressure strip sits above the header — raw USD magnitudes,
never filtered by the slider (factual market pressure, not a view of the filtered subset).
"""

from __future__ import annotations

import math
import time
from collections import deque
from datetime import datetime

from PySide6 import QtCore, QtGui, QtWidgets

# ── palette (Binance-inspired, tuned to the terminal's dark scanner theme) ──────────────────
_BG       = QtGui.QColor(10, 13, 18)
_BG_TOOL  = "#0e1218"
_ZEBRA    = QtGui.QColor(255, 255, 255, 5)
_RULE     = QtGui.QColor(27, 34, 45)
_HDR_TXT  = QtGui.QColor(122, 132, 150)
_TIME_TXT = QtGui.QColor(148, 158, 175, 200)
_AMT_TXT  = QtGui.QColor(212, 220, 232)
_DIM_TXT  = QtGui.QColor(212, 220, 232, 135)
_BUY      = QtGui.QColor(46, 189, 133)          # Binance buy green
_SELL     = QtGui.QColor(246, 70, 93)           # Binance sell red
_GOLD     = QtGui.QColor(240, 185, 11)          # Binance gold — whale amounts + slider accent
_WAIT_TXT = QtGui.QColor(120, 130, 148, 160)

_ROW_H, _HDR_H, _PRESS_H = 21, 24, 34
_PRESS_SECS = 60.0                               # pressure-strip lookback
_RAW_CAP = 30000                                 # raw trades retained (deque; ~10-50/s on SOL -> >10 min)

# USD styling tiers: >=T1 normal, >=T2 tinted, >=T3 accent bar + bold, >=T4 whale glow + gold
_T1, _T2, _T3, _T4 = 1_000.0, 10_000.0, 50_000.0, 100_000.0

# MIN SIZE slider: position 0 = ALL, else log-spaced $10 -> $500K over _SLIDER_STEPS
_SLIDER_STEPS = 1000
_USD_LO, _USD_HI = 10.0, 500_000.0


def _slider_to_usd(v: int) -> float:
    if v <= 0:
        return 0.0
    t = v / float(_SLIDER_STEPS)
    return 10.0 ** (math.log10(_USD_LO) + t * (math.log10(_USD_HI) - math.log10(_USD_LO)))


def _usd_to_slider(usd: float) -> int:
    if usd <= 0.0:
        return 0
    t = (math.log10(max(_USD_LO, min(_USD_HI, usd))) - math.log10(_USD_LO)) \
        / (math.log10(_USD_HI) - math.log10(_USD_LO))
    return int(round(t * _SLIDER_STEPS))


def _fmt_usd(a: float) -> str:
    if a >= 1_000_000:
        return f"${a/1_000_000:.2f}M"
    if a >= 100_000:
        return f"${a/1_000:.0f}K"
    if a >= 1_000:
        return f"${a/1_000:.1f}K"
    return f"${a:,.0f}"


class _TapeCanvas(QtWidgets.QWidget):
    """The painted tape body: pressure strip + header + rows. All state lives on the parent panel."""

    def __init__(self, panel: "TradesTapePanel") -> None:
        super().__init__(panel)
        self._p = panel
        self.setMinimumHeight(120)
        f = QtGui.QFont("Consolas", 10)
        self._font = f
        fb = QtGui.QFont("Consolas", 10)
        fb.setBold(True)
        self._font_b = fb
        fh = QtGui.QFont("Consolas", 8)
        fh.setBold(True)
        fh.setLetterSpacing(QtGui.QFont.SpacingType.AbsoluteSpacing, 1.2)
        self._font_hdr = fh

    def wheelEvent(self, ev) -> None:
        # natural list scrolling: wheel DOWN = further down the list = OLDER trades (pauses the tape);
        # wheel UP walks back toward the live edge (0 = follow live again).
        step = 3 if ev.angleDelta().y() < 0 else -3
        self._p.scroll_by(step)
        ev.accept()

    def paintEvent(self, _ev) -> None:
        p = QtGui.QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, _BG)
        pad = 12

        raw = self._p._raw
        # ── 60s pressure strip (raw magnitudes, unfiltered) ────────────────────────────────
        y0 = 0
        cutoff = (time.time() - _PRESS_SECS) * 1000.0
        buy_usd = sell_usd = 0.0
        for i in range(len(raw) - 1, -1, -1):            # newest -> oldest, early exit
            ts, price, qty, side = raw[i]
            if ts < cutoff:
                break
            if side:
                buy_usd += price * qty
            else:
                sell_usd += price * qty
        tot = buy_usd + sell_usd
        if tot > 0:
            bar_y, bar_h = y0 + 19, 5
            bw = int((w - 2 * pad) * (buy_usd / tot))
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
                       f"BUY {_fmt_usd(buy_usd)}  {buy_usd / tot * 100:.0f}%")
            p.setPen(_SELL)
            p.drawText(QtCore.QRect(w // 2, y0 + 2, w // 2 - pad, 14), QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                       f"{sell_usd / tot * 100:.0f}%  {_fmt_usd(sell_usd)} SELL")
        y0 += _PRESS_H

        # ── header ─────────────────────────────────────────────────────────────────────────
        c_time = pad
        c_amt_r = w - pad
        c_price = int(w * 0.40)
        p.setFont(self._font_hdr)
        p.setPen(_HDR_TXT)
        p.drawText(QtCore.QRect(c_time, y0, 120, _HDR_H), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, "TIME")
        p.drawText(QtCore.QRect(c_price - 60, y0, 140, _HDR_H), QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter,
                   "PRICE (USDT)")
        p.drawText(QtCore.QRect(c_amt_r - 160, y0, 160, _HDR_H), QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                   "AMOUNT (USD)")
        p.setPen(QtGui.QPen(_RULE, 1))
        p.drawLine(pad, y0 + _HDR_H - 1, w - pad, y0 + _HDR_H - 1)
        y0 += _HDR_H

        # ── rows: newest first, slider-filtered, offset by the scroll position ─────────────
        min_usd = self._p.min_usd
        skip = self._p._scroll
        n_fit = max(0, (h - y0) // _ROW_H)
        rows = []
        for i in range(len(raw) - 1, -1, -1):
            ts, price, qty, side = raw[i]
            usd = price * qty
            if usd < min_usd:
                continue
            if skip > 0:
                skip -= 1
                continue
            rows.append((ts, price, usd, side))
            if len(rows) >= n_fit:
                break

        if not rows:
            p.setFont(self._font)
            p.setPen(_WAIT_TXT)
            msg = "waiting for trades…" if not len(raw) else "no trades ≥ filter — lower MIN SIZE"
            p.drawText(QtCore.QRect(0, y0, w, 60), QtCore.Qt.AlignCenter, msg)
            p.end()
            return

        for k, (ts, price, usd, side) in enumerate(rows):
            ry = y0 + k * _ROW_H
            side_col = _BUY if side else _SELL
            if k % 2:
                p.fillRect(0, ry, w, _ROW_H, _ZEBRA)
            # tier emphasis: tint (T2) -> accent bar + bold (T3) -> whale glow + gold amount (T4)
            if usd >= _T2:
                tint = QtGui.QColor(side_col)
                tint.setAlpha(16 if usd < _T3 else (30 if usd < _T4 else 46))
                path = QtGui.QPainterPath()
                path.addRoundedRect(3.0, ry + 1.0, w - 6.0, _ROW_H - 2.0, 4.0, 4.0)
                p.fillPath(path, tint)
            if usd >= _T3:
                bar = QtGui.QColor(side_col)
                bar.setAlpha(230)
                p.fillRect(3, ry + 3, 3, _ROW_H - 6, bar)
            if usd >= _T4:
                edge = QtGui.QColor(side_col)
                edge.setAlpha(90)
                p.setPen(QtGui.QPen(edge, 1))
                p.setBrush(QtCore.Qt.NoBrush)
                p.drawRoundedRect(QtCore.QRectF(3.0, ry + 1.0, w - 6.0, _ROW_H - 2.0), 4.0, 4.0)

            p.setFont(self._font)
            p.setPen(_TIME_TXT)
            p.drawText(QtCore.QRect(c_time + (4 if usd >= _T3 else 0), ry, 120, _ROW_H),
                       QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                       datetime.fromtimestamp(ts / 1000.0).strftime("%H:%M:%S"))
            p.setFont(self._font_b if usd >= _T3 else self._font)
            p.setPen(side_col)
            p.drawText(QtCore.QRect(c_price - 70, ry, 160, _ROW_H),
                       QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, f"{price:,.2f}")
            if usd >= _T4:
                p.setPen(_GOLD)
            elif usd >= _T1:
                p.setPen(_AMT_TXT)
            else:
                p.setPen(_DIM_TXT)
            p.drawText(QtCore.QRect(c_amt_r - 180, ry, 180, _ROW_H),
                       QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, _fmt_usd(usd))
        p.end()


class TradesTapePanel(QtWidgets.QWidget):
    """Toolbar (MIN SIZE log slider + LIVE/PAUSED pill) over the painted tape canvas."""

    minUsdChanged = QtCore.Signal(float)

    def __init__(self) -> None:
        super().__init__()
        self._raw: deque = deque(maxlen=_RAW_CAP)    # (ts_ms, price, qty, side) — side 1=taker buy
        self._live_t0: float = 0.0                   # ts of the first LIVE trade (window rows dedupe against it)
        self.min_usd: float = 0.0
        self._scroll: int = 0                        # rows scrolled back (0 = follow live)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QtWidgets.QWidget()
        bar.setFixedHeight(44)
        bar.setAttribute(QtCore.Qt.WA_StyledBackground, True)   # plain QWidget ignores QSS bg without this
        bar.setStyleSheet(f"background:{_BG_TOOL}; border-bottom:1px solid #1b222d;")
        lay = QtWidgets.QHBoxLayout(bar)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(12)

        cap = QtWidgets.QLabel("MIN SIZE")
        cap.setStyleSheet("color:#7a8496; font-family:Consolas; font-size:9px; font-weight:bold;"
                          "letter-spacing:1px; border:none;")
        lay.addWidget(cap)

        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setRange(0, _SLIDER_STEPS)
        self.slider.setValue(0)
        self.slider.setFixedWidth(260)
        self.slider.setStyleSheet("""
            QSlider { border:none; }
            QSlider::groove:horizontal { height:4px; border-radius:2px; background:#1d2632; }
            QSlider::sub-page:horizontal { height:4px; border-radius:2px;
                background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #2ebd85, stop:1 #f0b90b); }
            QSlider::handle:horizontal { width:14px; height:14px; margin:-5px 0; border-radius:8px;
                background:#e6ecf4; border:2px solid #f0b90b; }
        """)
        self.slider.valueChanged.connect(self._on_slider)
        lay.addWidget(self.slider)

        self.val_lbl = QtWidgets.QLabel("ALL")
        self.val_lbl.setFixedWidth(84)
        self.val_lbl.setStyleSheet("color:#f0b90b; font-family:Consolas; font-size:11px;"
                                   "font-weight:bold; border:none;")
        lay.addWidget(self.val_lbl)
        lay.addStretch(1)

        self.pill = QtWidgets.QPushButton("● LIVE")
        self.pill.setCursor(QtCore.Qt.PointingHandCursor)
        self.pill.setFixedHeight(24)
        self.pill.clicked.connect(self.resume_live)
        lay.addWidget(self.pill)
        self._style_pill(live=True)

        root.addWidget(bar)
        self.canvas = _TapeCanvas(self)
        root.addWidget(self.canvas, 1)

    # ── data ingestion (numpy arrays from heatmap.decode_trades) ───────────────────────────
    def ingest_live(self, ts, pr, qt, sd) -> bool:
        """Append one live batch (already time-ordered). Returns True if anything landed. If the history
        window landed BEFORE the first live batch, the batch can overlap it by up to one pulse (~0.4s) —
        cut that boundary by timestamp (the tape carries no aggTrade ids)."""
        n = len(ts)
        if not n:
            return False
        i0 = 0
        if self._live_t0 == 0.0:
            if len(self._raw):
                last = self._raw[-1][0]
                while i0 < n and float(ts[i0]) <= last:
                    i0 += 1
                if i0 >= n:
                    return False
            self._live_t0 = float(ts[i0])
        for i in range(i0, n):
            self._raw.append((float(ts[i]), float(pr[i]), float(qt[i]), int(sd[i])))
        return True

    def ingest_window(self, ts, pr, qt, sd) -> bool:
        """Prepend the history backfill, keeping only rows OLDER than the first live trade (exact
        dedupe — the tape rows carry no aggTrade id, so overlap is cut by timestamp instead)."""
        n = len(ts)
        if not n:
            return False
        cut = self._live_t0 or float("inf")
        older = [(float(ts[i]), float(pr[i]), float(qt[i]), int(sd[i]))
                 for i in range(n) if float(ts[i]) < cut]
        if not older:
            return False
        live = [r for r in self._raw if r[0] >= cut]
        self._raw.clear()
        self._raw.extend(older)
        self._raw.extend(live)
        return True

    def reset(self) -> None:
        """Full wipe (daemon/tunnel reconnect recovery): the live subscription died with the old
        connection, so the terminal re-backfills from scratch — duplicate-free by construction."""
        self._raw.clear()
        self._live_t0 = 0.0
        self._scroll = 0
        self._style_pill(live=True)
        self.canvas.update()

    # ── interaction ────────────────────────────────────────────────────────────────────────
    def scroll_by(self, step: int) -> None:
        was = self._scroll
        self._scroll = max(0, self._scroll + step)
        if self._scroll != was:
            self._style_pill(live=self._scroll == 0)
            self.canvas.update()

    def resume_live(self) -> None:
        self._scroll = 0
        self._style_pill(live=True)
        self.canvas.update()

    def set_min_usd(self, usd: float) -> None:
        """Programmatic restore (saved UI state) — moves the slider, which re-derives everything."""
        self.slider.setValue(_usd_to_slider(usd))

    def _on_slider(self, v: int) -> None:
        self.min_usd = _slider_to_usd(v)
        self.val_lbl.setText("ALL" if self.min_usd <= 0 else f"≥ {_fmt_usd(self.min_usd)}")
        self._scroll = 0                              # a new filter re-anchors to the live edge
        self._style_pill(live=True)
        self.canvas.update()
        self.minUsdChanged.emit(self.min_usd)

    def _style_pill(self, live: bool) -> None:
        if live:
            self.pill.setText("● LIVE")
            self.pill.setStyleSheet(
                "QPushButton { color:#2ebd85; background:rgba(46,189,133,0.10); border:1px solid"
                " rgba(46,189,133,0.45); border-radius:12px; padding:0 14px; font-family:Consolas;"
                " font-size:10px; font-weight:bold; }")
        else:
            self.pill.setText("⏸ PAUSED — click to follow")
            self.pill.setStyleSheet(
                "QPushButton { color:#f0b90b; background:rgba(240,185,11,0.10); border:1px solid"
                " rgba(240,185,11,0.45); border-radius:12px; padding:0 14px; font-family:Consolas;"
                " font-size:10px; font-weight:bold; }")
