"""Tier-3 vector drawing toolbar + controller (spec §8.3).

A floating tool palette plus a controller that turns chart clicks into persistent
vector shapes. Two-click placement (click A -> click B) with a live rubber-band
preview between. Pressing ``V`` cancels drawing and restores native pan
(spec §8.3 keybind). Drawings persist to ``data/drawings.json`` keyed by symbol,
replacing the legacy browser localStorage.

Position tools (Long/Short) are full TradingView-parity brackets: Entry, Stop and
Take-Profit each have an independent draggable handle, and the R:R ratio + risk/
reward zones recalculate live as any line is dragged, color-graded teal/orange/
red by ratio (spec §8.3).

Other tools: Trend Line, Extended Ray, Horizontal, Vertical, Rectangle, Ellipse,
Measure %, Eraser, Delete All.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import List, Optional

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from . import config
from .chart_widgets import RoundedTextItem

TOOLS = ["select", "magic_select", "trend", "ray", "hline", "vline", "rect", "ellipse",
         "measure", "long", "short", "eraser", "delete_all", "undo", "redo"]
_LABELS = {
    "select": "↖️", "magic_select": "🪄", "trend": "📏", "ray": "↗️", "hline": "➖", "vline": "｜",
    "rect": "🔲", "ellipse": "⭕", "measure": "📐", "long": "📈", "short": "📉",
    "eraser": "🧽", "delete_all": "🗑️", "undo": "↩️", "redo": "↪️",
}
_TOOLTIPS = {
    "select": "Select / Edit",
    "magic_select": "Magic Selection — box a Mode-10 region for aggregated stats",
    "trend": "Trend Line", "ray": "Extended Ray",
    "hline": "Horizontal Line", "vline": "Vertical Line", "rect": "Rectangle",
    "ellipse": "Ellipse", "measure": "Measure %", "long": "Long Position",
    "short": "Short Position", "eraser": "Eraser", "delete_all": "Delete All",
    "undo": "Undo — restore the last draw / move / delete (Ctrl+Z is the Selection VP)",
    "redo": "Redo the undone step",
}
# One-shot ACTIONS, not drawing modes: they fire and must never stay latched like a tool.
_ACTION_TOOLS = {"delete_all", "undo", "redo"}
_SHAPE_TWO_POINT = {"trend", "ray", "rect", "ellipse", "measure"}
_POSITION_TOOLS = {"long", "short"}
_PRESET_COLORS = ["#ffffff", "#000000", "#2962ff", "#e74c3c", "#1abc9c", "#f1c40f", "#9b59b6", "#ff7f0e"]
_DRAW_FILE = os.path.join(config.DATA_DIR, "drawings.json")


def _afrac(f: float) -> float:
    """Clamp an anchor's fraction to +-0.5 — its only meaningful range, since it is an offset WITHIN
    the anchored bucket.

    _anchor_pts clamps the bucket INDEX for a point drawn past the data (the empty right margin, easy
    to hit zoomed out) but left the fraction unbounded: `frac = px - j` reached +491 on real saved
    drawings, i.e. an anchor meaning "the last bucket, plus 491 buckets". That is unplaceable, so on
    reload the render gate (which needs the WHOLE shape in-frame) rejected it forever — the shape drew
    fine all session, then vanished at the next restart.

    A no-op for any in-range point (`px - round(px)` is already within +-0.5), so the normal path is
    unchanged; an out-of-range edge simply pins to the bucket it was clamped to. Applied on READ as
    well as write, so drawings ALREADY saved with a broken fraction heal rather than staying invisible.
    """
    return -0.5 if f < -0.5 else (0.5 if f > 0.5 else f)


def _rr_color(rr: float) -> str:
    if rr >= 1.5:
        return "#1abc9c"   # teal — high quality
    if rr >= 1.0:
        return "#e67e22"   # orange — median
    return "#e74c3c"       # red — sub-optimal


# ---------------------------------------------------------------------------
# Static vector shapes (QPicture-backed)
# ---------------------------------------------------------------------------
class DrawnShape(pg.GraphicsObject):
    def __init__(self, kind: str, pts: List[list], color: str = "#ffffff", width: int = 2,
                 fill_color: str = "#3498db", fill_opacity: float = 0.0, dashed: bool = False):
        super().__init__()
        self.kind = kind
        self.pts = [list(p) for p in pts]
        self.color = color
        self.width = width
        self.fill_color = fill_color          # rect/ellipse interior fill colour
        self.fill_opacity = fill_opacity      # 0.0 = outline-only (default)
        self.dashed = dashed                  # dashed border (the Magic-Selection rectangle)
        self.picture = QtGui.QPicture()
        self._rect = QtCore.QRectF()
        self.setAcceptHoverEvents(True)   # pointing-hand cursor on hover (patch §18)
        # Trend line carries an always-positive %-change label — a fixed-pixel-size child TextItem (anchored to the
        # end point in data coords) so it never distorts/scales with zoom the way an in-picture drawText would.
        self._pct_label = None
        if kind == "trend":
            self._pct_label = pg.TextItem(color=color, anchor=(-0.15, 0.5))
            try:
                self._pct_label.textItem.setFont(QtGui.QFont("Consolas", 9))
            except Exception:
                pass
            self._pct_label.setParentItem(self)   # moves + deletes with the shape; renders at fixed pixel size
        self.rebuild()

    def hoverEnterEvent(self, ev):   # noqa: N802
        self.setCursor(QtCore.Qt.PointingHandCursor)

    def hoverLeaveEvent(self, ev):   # noqa: N802
        self.unsetCursor()

    def to_dict(self) -> dict:
        return {"kind": self.kind, "pts": self.pts, "color": self.color, "width": self.width,
                "fill_color": self.fill_color, "fill_opacity": self.fill_opacity}

    def rebuild(self) -> None:
        self.picture = QtGui.QPicture()
        need = 2 if self.kind in _SHAPE_TWO_POINT else 1
        if len(self.pts) < need:
            self._rect = QtCore.QRectF(); self.prepareGeometryChange(); self.update(); return
        p = QtGui.QPainter(self.picture)
        if self.width <= 0:                              # width 0 = NO border (e.g. a filled rect/ellipse)
            p.setPen(QtCore.Qt.NoPen)
        else:
            pen = QtGui.QPen(QtGui.QColor(self.color)); pen.setCosmetic(True); pen.setWidth(self.width)
            if self.dashed:
                pen.setStyle(QtCore.Qt.DashLine)
            p.setPen(pen)
        p.setFont(QtGui.QFont("Consolas", 8))
        # interior fill for closed shapes (rect/ellipse); outline-only when opacity is 0
        if self.kind in ("rect", "ellipse") and self.fill_opacity > 0:
            fc = QtGui.QColor(self.fill_color); fc.setAlphaF(self.fill_opacity)
            p.setBrush(fc)
        else:
            p.setBrush(QtCore.Qt.NoBrush)
        x0, y0 = self.pts[0]
        if self.kind == "hline":
            p.drawLine(QtCore.QPointF(x0 - 1e7, y0), QtCore.QPointF(x0 + 1e7, y0))
        elif self.kind == "vline":
            p.drawLine(QtCore.QPointF(x0, -1e7), QtCore.QPointF(x0, 1e7))
        else:
            x1, y1 = self.pts[1]
            if self.kind == "ray" and x1 != x0:
                slope = (y1 - y0) / (x1 - x0)
                x1 = x0 + (x1 - x0) * 1e6
                y1 = y0 + slope * (x1 - x0)
                p.drawLine(QtCore.QPointF(x0, y0), QtCore.QPointF(x1, y1))
            elif self.kind in ("trend", "measure"):
                p.drawLine(QtCore.QPointF(x0, y0), QtCore.QPointF(self.pts[1][0], self.pts[1][1]))
                if self.kind == "measure":
                    dp = self.pts[1][1] - y0
                    pct = (dp / y0 * 100) if y0 else 0.0
                    p.drawText(QtCore.QPointF(self.pts[1][0], self.pts[1][1]), f"{dp:+.2f} ({pct:+.2f}%)")
                elif self._pct_label is not None:   # trend: always-POSITIVE % change in a fixed-size child label
                    pct = (abs(self.pts[1][1] - y0) / y0 * 100) if y0 else 0.0
                    self._pct_label.setColor(self.color)
                    self._pct_label.setText(f"{pct:.2f}%")
                    self._pct_label.setPos(self.pts[1][0], self.pts[1][1])
            elif self.kind == "rect":
                p.drawRect(QtCore.QRectF(x0, y0, self.pts[1][0] - x0, self.pts[1][1] - y0))
            elif self.kind == "ellipse":
                p.drawEllipse(QtCore.QRectF(x0, y0, self.pts[1][0] - x0, self.pts[1][1] - y0))
        p.end()
        xs = [pt[0] for pt in self.pts]; ys = [pt[1] for pt in self.pts]
        if self.kind == "ray" and len(self.pts) >= 2 and self.pts[1][0] != self.pts[0][0]:
            # culling rect must cover the PAINTED extension, or the ray vanishes once both
            # anchor points scroll off-screen (autorange stays sane via dataBounds below)
            _sl = (self.pts[1][1] - self.pts[0][1]) / (self.pts[1][0] - self.pts[0][0])
            _xe = self.pts[0][0] + (self.pts[1][0] - self.pts[0][0]) * 1e6
            xs.append(_xe); ys.append(self.pts[0][1] + _sl * (_xe - self.pts[0][0]))
        self._rect = QtCore.QRectF(min(xs), min(ys), max(1.0, max(xs) - min(xs)),
                                   max(1e-6, max(ys) - min(ys)))
        self.prepareGeometryChange(); self.update()

    def near(self, x, y, tol_x, tol_y) -> bool:
        """True when (x, y) is ON the drawing (anywhere along a line / inside a rect or ellipse),
        within the viewport-scaled tolerance — not just near the endpoints."""
        tol_x = max(tol_x, 1e-12); tol_y = max(tol_y, 1e-12)
        x0, y0 = self.pts[0]
        if self.kind == "hline":
            return abs(y - y0) <= tol_y
        if self.kind == "vline":
            return abs(x - x0) <= tol_x
        if len(self.pts) < 2:
            return abs(x - x0) <= tol_x and abs(y - y0) <= tol_y
        x1, y1 = self.pts[1]
        if self.kind == "rect":
            return (min(x0, x1) - tol_x <= x <= max(x0, x1) + tol_x
                    and min(y0, y1) - tol_y <= y <= max(y0, y1) + tol_y)
        if self.kind == "ellipse":
            cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            rx = abs(x1 - x0) / 2.0 + tol_x; ry = abs(y1 - y0) / 2.0 + tol_y
            return ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1.0
        # trend / measure / ray: distance to the segment in TOLERANCE-normalized space (so the
        # hit band is tol_x wide horizontally and tol_y vertically regardless of zoom/aspect)
        ax, ay = (x - x0) / tol_x, (y - y0) / tol_y
        bx, by = (x1 - x0) / tol_x, (y1 - y0) / tol_y
        bb = bx * bx + by * by
        if bb <= 1e-18:
            return ax * ax + ay * ay <= 1.0
        t = (ax * bx + ay * by) / bb
        if self.kind == "ray":
            t = max(0.0, t)                       # extends indefinitely past the second point
        else:
            t = max(0.0, min(1.0, t))
        dx, dy = ax - t * bx, ay - t * by
        return dx * dx + dy * dy <= 1.0

    def paint(self, p, *a): p.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        # Infinite lines paint +/-1e7 but a point-sized rect got them CULLED as soon as the anchor
        # left the viewport (they "disappeared on zoom"). Culling rect = the current view span along
        # the infinite axis (pg.InfiniteLine idiom); autorange is protected by dataBounds().
        if self.kind in ("hline", "vline") and self.pts:
            vb = self.getViewBox()
            if vb is not None:
                try:
                    vr = vb.viewRect()
                    if self.kind == "hline":
                        y0 = self.pts[0][1]; pad = abs(vr.height()) * 0.01 + 1e-9
                        return QtCore.QRectF(vr.left(), y0 - pad, vr.width(), 2 * pad)
                    x0 = self.pts[0][0]; pad = abs(vr.width()) * 0.01 + 1e-9
                    return QtCore.QRectF(x0 - pad, vr.top(), 2 * pad, vr.height())
                except Exception:
                    pass
        return self._rect

    def dataBounds(self, ax, frac=1.0, orthoRange=None):
        # autorange/Y-fit contribution: only the FINITE axis of infinite lines (else a view-sized or
        # +/-1e6 boundingRect would explode auto-fit); normal shapes contribute their point extents.
        if not self.pts:
            return None
        if self.kind == "hline":
            return None if ax == 0 else [self.pts[0][1], self.pts[0][1]]
        if self.kind == "vline":
            return [self.pts[0][0], self.pts[0][0]] if ax == 0 else None
        vals = [pt[ax] for pt in self.pts]
        return [min(vals), max(vals)]

    def viewTransformChanged(self):
        if self.kind in ("hline", "vline"):
            self.prepareGeometryChange()          # view moved -> the culling rect must follow
        super().viewTransformChanged()


# ---------------------------------------------------------------------------
# Interactive position bracket (TradingView parity)
# ---------------------------------------------------------------------------
class _PositionFill(pg.GraphicsObject):
    """Green profit / red risk zones bounded to the bracket's x-window."""

    def __init__(self, x0: float, x1: float):
        super().__init__()
        self.x0, self.x1 = x0, x1
        self.entry = self.stop = self.target = 0.0
        self.picture = QtGui.QPicture()
        self._rect = QtCore.QRectF()

    def update_levels(self, entry, stop, target) -> None:
        self.entry, self.stop, self.target = entry, stop, target
        self.picture = QtGui.QPicture()
        p = QtGui.QPainter(self.picture)
        green = QtGui.QColor(46, 204, 113, 26)   # ~10% opacity
        red = QtGui.QColor(231, 76, 60, 26)      # ~10% opacity
        w = self.x1 - self.x0
        for y_a, y_b, col in ((entry, target, green), (entry, stop, red)):
            top, bot = max(y_a, y_b), min(y_a, y_b)
            p.fillRect(QtCore.QRectF(self.x0, bot, w, top - bot), col)
        p.end()
        lo = min(entry, stop, target); hi = max(entry, stop, target)
        self._rect = QtCore.QRectF(self.x0, lo, w, max(1e-6, hi - lo))
        self.prepareGeometryChange(); self.update()

    def paint(self, p, *a): p.drawPicture(0, 0, self.picture)
    def boundingRect(self): return self._rect


class PositionBracket(QtCore.QObject):
    """Entry/Stop/Target with independent drag handles + live R:R (spec §8.3)."""

    changed = QtCore.Signal()

    def __init__(self, plot: pg.PlotWidget, kind: str, x0: float, x1: float,
                 entry: float, stop: float, target: float):
        super().__init__()
        self.plot = plot
        self.kind = kind
        self.x0, self.x1 = x0, x1

        self.label_x = x1  # x at which the data label is anchored (right-aligned)

        self.fill = _PositionFill(x0, x1)
        plot.addItem(self.fill, ignoreBounds=True)

        self.entry_line = self._mk_line(entry, "#2962ff", "Entry")
        self.stop_line = self._mk_line(stop, "#e74c3c", "SL")
        self.target_line = self._mk_line(target, "#1abc9c", "TP")

        # §7.2 — full-height vertical edge handles for horizontal (span) adjustment
        self.left_line = self._mk_edge(x0)
        self.right_line = self._mk_edge(x1)

        # anchor (1,1): text bottom-right pinned to label_x -> flush against the
        # right Y-axis margin when label_x tracks the view edge (patch §17). Rounded dark pill (modern card look).
        self.label = RoundedTextItem(anchor=(1, 1), fill=pg.mkBrush(22, 24, 30, 240),
                                      border=pg.mkPen((90, 96, 110), width=1))
        self.label.setZValue(70)
        plot.addItem(self.label, ignoreBounds=True)

        # Per-line value labels as rounded dark PILLS with the line's own colour (SL red / Entry blue / TP green),
        # replacing the old plain white boxes. SL/TP also show % vs entry.
        self._val_labels = {}
        _bord = {"SL": (231, 76, 60), "Entry": (41, 98, 255), "TP": (26, 188, 156)}
        for nm in ("SL", "Entry", "TP"):
            t = RoundedTextItem(anchor=(0.5, 0.5), fill=pg.mkBrush(22, 24, 30, 240),
                                 border=pg.mkPen(_bord[nm], width=1))
            t.setZValue(70)
            plot.addItem(t, ignoreBounds=True)
            self._val_labels[nm] = t

        for ln in (self.entry_line, self.stop_line, self.target_line):
            ln.sigPositionChanged.connect(self._recalc)
        for vln in (self.left_line, self.right_line):
            vln.sigPositionChanged.connect(self._recalc_span)
        self._recalc()

    def _mk_edge(self, x: float) -> pg.InfiniteLine:
        """Vertical span handle — subtle thin muted-gray dashed (§7.2)."""
        pen = QtGui.QPen(QtGui.QColor(150, 150, 150, 130))
        pen.setWidth(1); pen.setCosmetic(True); pen.setStyle(QtCore.Qt.DashLine)
        hover = QtGui.QPen(QtGui.QColor(200, 200, 200)); hover.setWidth(2); hover.setCosmetic(True)
        vln = pg.InfiniteLine(pos=x, angle=90, movable=True, pen=pen, hoverPen=hover)
        vln.setZValue(71)
        vln.setCursor(QtCore.Qt.SizeHorCursor)
        self.plot.addItem(vln, ignoreBounds=True)
        return vln

    def _recalc_span(self) -> None:
        """Vertical handles moved -> update the bracket's x-window + fill geometry."""
        self.x0 = min(self.left_line.value(), self.right_line.value())
        self.x1 = max(self.left_line.value(), self.right_line.value())
        self.fill.x0, self.fill.x1 = self.x0, self.x1
        self._recalc()

    def _mk_line(self, price: float, color: str, name: str) -> pg.InfiniteLine:
        pen = QtGui.QPen(QtGui.QColor(color))
        pen.setWidth(1); pen.setCosmetic(True); pen.setStyle(QtCore.Qt.DashLine)
        hover = QtGui.QPen(QtGui.QColor(color)); hover.setWidth(2); hover.setCosmetic(True)
        ln = pg.InfiniteLine(
            pos=price, angle=0, movable=True, pen=pen, hoverPen=hover,
        )  # value labels are right-anchored TextItems (see _recalc), not the line's own label
        ln.setZValue(72)
        ln.setCursor(QtCore.Qt.PointingHandCursor)  # hand cursor on the handle (patch §18)
        self.plot.addItem(ln, ignoreBounds=True)
        return ln

    def set_label_x(self, x: float) -> None:
        """Pin the data label to a view x-coordinate (e.g. the right edge)."""
        self.label_x = x
        self._recalc()

    def _recalc(self) -> None:
        e = self.entry_line.value()
        s = self.stop_line.value()
        t = self.target_line.value()
        risk = abs(e - s)
        reward = abs(t - e)
        rr = reward / risk if risk > 1e-9 else 0.0
        self.fill.update_levels(e, s, t)
        col = _rr_color(rr)
        # top label: the R:R ratio only (E/T/S values now live in the right-side labels)
        self.label.setText(f"1 : {rr:.2f}", color=col)
        self.label.setPos(self.label_x, max(e, s, t))
        # centered value labels — line-coloured bold text on the rounded dark pill; SL always shows a
        # negative % and TP a positive % (risk vs reward), regardless of long/short
        xc = (self.x0 + self.x1) / 2.0
        sl_pct = abs(s - e) / e * 100.0 if e else 0.0
        tp_pct = abs(t - e) / e * 100.0 if e else 0.0
        _f = "font-size:13px"
        self._val_labels["SL"].setHtml(f"<span style='color:#ff8a80;{_f}'><b>{s:.2f}</b> (-{sl_pct:.2f}%)</span>")
        self._val_labels["Entry"].setHtml(f"<span style='color:#82b1ff;{_f}'><b>{e:.2f}</b></span>")
        self._val_labels["TP"].setHtml(f"<span style='color:#69f0ae;{_f}'><b>{t:.2f}</b> (+{tp_pct:.2f}%)</span>")
        self._val_labels["SL"].setPos(xc, s)
        self._val_labels["Entry"].setPos(xc, e)
        self._val_labels["TP"].setPos(xc, t)
        self.changed.emit()

    @property
    def rr(self) -> float:
        risk = abs(self.entry_line.value() - self.stop_line.value())
        return abs(self.target_line.value() - self.entry_line.value()) / risk if risk > 1e-9 else 0.0

    def to_dict(self) -> dict:
        return {"kind": self.kind, "x0": self.x0, "x1": self.x1,
                "entry": self.entry_line.value(), "stop": self.stop_line.value(),
                "target": self.target_line.value()}

    def near(self, x, y, tol_x, tol_y) -> bool:
        if not (self.x0 - tol_x <= x <= self.x1 + tol_x):
            return False
        return any(abs(ln.value() - y) <= tol_y
                   for ln in (self.entry_line, self.stop_line, self.target_line))

    def set_visible(self, on: bool) -> None:
        for it in (self.fill, self.entry_line, self.stop_line, self.target_line,
                   self.left_line, self.right_line, self.label,
                   *self._val_labels.values()):
            it.setVisible(on)

    def remove(self) -> None:
        for it in (self.fill, self.entry_line, self.stop_line, self.target_line,
                   self.left_line, self.right_line, self.label,
                   *self._val_labels.values()):
            self.plot.removeItem(it)


# ---------------------------------------------------------------------------
# Toolbar
# ---------------------------------------------------------------------------
_TOOLBAR_QSS = """
QFrame#drawbar { background-color: rgba(20,22,28,0.95); border:1px solid #2a2e39; border-radius:4px; }
QPushButton { background:#2a2e39; color:#fff; border:none; padding:4px 8px; font-family:Consolas; font-size:11px; }
QPushButton:checked { background:#3498db; }
QPushButton:hover { background:#3d4350; }
"""


class DrawingToolbar(QtWidgets.QFrame):
    toolSelected = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget):
        super().__init__(parent)
        self.setObjectName("drawbar")
        self.setStyleSheet(_TOOLBAR_QSS)
        self.hide()
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4); lay.setSpacing(2)
        self._group = QtWidgets.QButtonGroup(self); self._group.setExclusive(True)
        self.buttons: dict[str, QtWidgets.QPushButton] = {}
        for t in TOOLS:
            b = QtWidgets.QPushButton(_LABELS[t])
            b.setToolTip(_TOOLTIPS[t])
            b.setCursor(QtCore.Qt.PointingHandCursor)
            if t not in _ACTION_TOOLS:
                b.setCheckable(True); self._group.addButton(b)
            b.clicked.connect(lambda _=False, tool=t: self.toolSelected.emit(tool))
            self.buttons[t] = b
            lay.addWidget(b)
        self.adjustSize()

    def select_tool(self, tool: str) -> None:
        """Programmatically check a tool's button WITHOUT re-emitting toolSelected.

        ``setChecked`` does not fire ``clicked``, so the controller can snap the UI
        back to the cursor after a commit with zero circular signal fires (§7.3).
        """
        btn = self.buttons.get(tool)
        if btn is not None and btn.isCheckable():
            btn.setChecked(True)


class ShapeEditPanel(QtWidgets.QFrame):
    """Floating color + thickness editor for the selected shape (patch §16)."""

    changed = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget):
        super().__init__(parent)
        self.setObjectName("drawbar")
        self.setStyleSheet(_TOOLBAR_QSS)
        self.target: Optional[DrawnShape] = None
        self.hide()

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4); lay.setSpacing(4)
        lay.addWidget(QtWidgets.QLabel("Color"))
        for hexc in _PRESET_COLORS:
            sw = QtWidgets.QPushButton()
            sw.setFixedSize(16, 16)
            sw.setStyleSheet(f"background:{hexc};border:1px solid #888;")
            sw.setCursor(QtCore.Qt.PointingHandCursor)
            sw.clicked.connect(lambda _=False, c=hexc: self._set_color(c))
            lay.addWidget(sw)
        more = QtWidgets.QPushButton("…")
        more.setCursor(QtCore.Qt.PointingHandCursor)
        more.clicked.connect(self._pick_color)
        lay.addWidget(more)
        lay.addWidget(QtWidgets.QLabel("W"))
        self.spin = QtWidgets.QSpinBox(); self.spin.setRange(0, 8)
        self.spin.valueChanged.connect(self._set_width)
        lay.addWidget(self.spin)

        # Fill controls — shown ONLY for rect/ellipse (see bind()): colour presets + opacity slider.
        self.fill_widgets: list = []
        fl = QtWidgets.QLabel("Fill"); lay.addWidget(fl); self.fill_widgets.append(fl)
        for hexc in _PRESET_COLORS:
            sw = QtWidgets.QPushButton(); sw.setFixedSize(16, 16)
            sw.setStyleSheet(f"background:{hexc};border:1px solid #888;")
            sw.setCursor(QtCore.Qt.PointingHandCursor)
            sw.clicked.connect(lambda _=False, c=hexc: self._set_fill_color(c))
            lay.addWidget(sw); self.fill_widgets.append(sw)
        fm = QtWidgets.QPushButton("…"); fm.setCursor(QtCore.Qt.PointingHandCursor)
        fm.clicked.connect(self._pick_fill_color); lay.addWidget(fm); self.fill_widgets.append(fm)
        ol = QtWidgets.QLabel("Op"); lay.addWidget(ol); self.fill_widgets.append(ol)
        self.op_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.op_slider.setRange(0, 100); self.op_slider.setFixedWidth(64)
        self.op_slider.valueChanged.connect(self._set_fill_opacity)
        lay.addWidget(self.op_slider); self.fill_widgets.append(self.op_slider)

        # Rectangle-only: step-wider buttons (each click grows that side by ~half the width).
        self.rect_widgets: list = []
        xl = QtWidgets.QLabel("Exp"); lay.addWidget(xl); self.rect_widgets.append(xl)
        exl = QtWidgets.QPushButton("◀"); exl.setToolTip("Expand left (wider by half)")
        exl.setCursor(QtCore.Qt.PointingHandCursor); exl.clicked.connect(lambda: self._expand("left"))
        lay.addWidget(exl); self.rect_widgets.append(exl)
        exr = QtWidgets.QPushButton("▶"); exr.setToolTip("Expand right (wider by half)")
        exr.setCursor(QtCore.Qt.PointingHandCursor); exr.clicked.connect(lambda: self._expand("right"))
        lay.addWidget(exr); self.rect_widgets.append(exr)
        self.adjustSize()

    def bind(self, shape: "DrawnShape") -> None:
        self.target = shape
        fillable = shape.kind in ("rect", "ellipse")     # fill + 0-border only for closed shapes
        self.spin.blockSignals(True)
        self.spin.setMinimum(0 if fillable else 1)        # rect/ellipse may have NO border (width 0)
        self.spin.setValue(shape.width)
        self.spin.blockSignals(False)
        for w in self.fill_widgets:
            w.setVisible(fillable)
        if fillable:
            self.op_slider.blockSignals(True)
            self.op_slider.setValue(int(shape.fill_opacity * 100))
            self.op_slider.blockSignals(False)
        for w in self.rect_widgets:
            w.setVisible(shape.kind == "rect")
        self.adjustSize()
        self.show(); self.raise_()

    def _set_color(self, hexc: str) -> None:
        if self.target is not None:
            self.target.color = hexc
            self.target.rebuild()
            self.changed.emit()

    def _pick_color(self) -> None:
        if self.target is None:
            return
        col = QtWidgets.QColorDialog.getColor(QtGui.QColor(self.target.color), self)
        if col.isValid():
            self._set_color(col.name())

    def _set_width(self, w: int) -> None:
        if self.target is not None:
            self.target.width = w
            self.target.rebuild()
            self.changed.emit()

    def _set_fill_color(self, hexc: str) -> None:
        if self.target is not None:
            self.target.fill_color = hexc
            if self.target.fill_opacity <= 0:        # picking a fill at 0 opacity -> nudge to visible
                self.target.fill_opacity = 0.2
                self.op_slider.blockSignals(True); self.op_slider.setValue(20); self.op_slider.blockSignals(False)
            self.target.rebuild()
            self.changed.emit()

    def _pick_fill_color(self) -> None:
        if self.target is None:
            return
        col = QtWidgets.QColorDialog.getColor(QtGui.QColor(self.target.fill_color), self)
        if col.isValid():
            self._set_fill_color(col.name())

    def _set_fill_opacity(self, v: int) -> None:
        if self.target is not None:
            self.target.fill_opacity = v / 100.0
            self.target.rebuild()
            self.changed.emit()

    def _expand(self, direction: str) -> None:
        """Grow the selected rectangle by ~half its current width on one side (view-independent)."""
        s = self.target
        if s is None or s.kind != "rect" or len(s.pts) < 2:
            return
        x0, x1 = s.pts[0][0], s.pts[1][0]
        step = abs(x1 - x0) * 0.5
        if step <= 0:
            return
        if direction == "right":                 # push the larger-x edge further right
            if x1 >= x0: s.pts[1][0] = x1 + step
            else:        s.pts[0][0] = x0 + step
        else:                                     # "left": push the smaller-x edge further left
            if x0 <= x1: s.pts[0][0] = x0 - step
            else:        s.pts[1][0] = x1 - step
        s.rebuild()
        self.changed.emit()


# ---------------------------------------------------------------------------
# Edit handles — draggable dots that resize/move the selected shape
# ---------------------------------------------------------------------------
class ShapeHandles(QtCore.QObject):
    """Draggable dot handles for the selected :class:`DrawnShape`. Per kind:
    rect -> 4 corners + 4 edge-midpoints (corners = diagonal resize, edges = one side);
    ellipse -> center (move) + corner (resize); trend/ray/measure -> 2 endpoints;
    h/v-line -> 1 position handle. Drag updates the shape live; persists on release."""

    changed = QtCore.Signal()   # emitted on drag-release -> controller persists
    started = QtCore.Signal()   # emitted ONCE at the start of a drag, BEFORE pts move -> controller snapshots for undo

    def __init__(self, plot: pg.PlotWidget):
        super().__init__()
        self.plot = plot
        self.vb = plot.getViewBox()
        self.shape: Optional[DrawnShape] = None
        self.handles: list = []      # [{"item": TargetItem, "role": str}]
        self.corners_only = False    # the Magic-Selection rect uses 4 corners only
        self._dragging = False       # gesture guard so `started` fires once per drag, not per move event

    def attach(self, shape: "DrawnShape", corners_only: bool = False) -> None:
        self.clear()
        self.corners_only = corners_only
        self.shape = shape
        for (x, y, role) in self._specs(shape):
            h = pg.TargetItem(pos=(x, y), size=11, symbol="s",
                              pen=pg.mkPen("#ffffff", width=1.0),
                              brush=pg.mkBrush("#3498db"), movable=True)
            h.setZValue(90)
            h.sigPositionChanged.connect(lambda _h=h, r=role: self._on_drag(_h, r))
            h.sigPositionChangeFinished.connect(self._on_finish)
            self.plot.addItem(h)
            self.handles.append({"item": h, "role": role})

    def clear(self) -> None:
        for hd in self.handles:
            try:
                self.plot.removeItem(hd["item"])
            except Exception:
                pass
        self.handles = []
        self.shape = None

    # ------------------------------------------------------------------
    def _specs(self, s: "DrawnShape") -> list:
        """[(x, y, role), ...] — the shape's key edit points in data coords."""
        k = s.kind
        if k == "hline":
            (vx0, vx1), _ = self.vb.viewRange()
            return [((vx0 + vx1) / 2.0, s.pts[0][1], "hline")]
        if k == "vline":
            _, (vy0, vy1) = self.vb.viewRange()
            return [(s.pts[0][0], (vy0 + vy1) / 2.0, "vline")]
        if len(s.pts) < 2:
            return []
        (x0, y0), (x1, y1) = s.pts[0], s.pts[1]
        if k in ("trend", "ray", "measure"):
            return [(x0, y0, "p0"), (x1, y1, "p1")]
        if k == "ellipse":
            return [((x0 + x1) / 2.0, (y0 + y1) / 2.0, "center"), (x1, y1, "corner")]
        if k == "rect":
            corners = [(x0, y0, "c_tl"), (x1, y0, "c_tr"), (x0, y1, "c_bl"), (x1, y1, "c_br")]
            if self.corners_only:
                return corners
            mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            return corners + [(mx, y0, "e_t"), (mx, y1, "e_b"), (x0, my, "e_l"), (x1, my, "e_r")]
        return []

    def _on_drag(self, item, role: str) -> None:
        s = self.shape
        if s is None:
            return
        if not self._dragging:          # gesture start — pts are still PRE-drag, so the snapshot is exact
            self._dragging = True
            self.started.emit()
        nx, ny = item.pos().x(), item.pos().y()
        if role == "hline":
            s.pts[0][1] = ny
        elif role == "vline":
            s.pts[0][0] = nx
        elif role == "p0":
            s.pts[0] = [nx, ny]
        elif role == "p1":
            s.pts[1] = [nx, ny]
        elif role == "corner":                       # ellipse resize (bottom-right)
            s.pts[1] = [nx, ny]
        elif role == "center":                       # ellipse move = translate both points
            ox = (s.pts[0][0] + s.pts[1][0]) / 2.0; oy = (s.pts[0][1] + s.pts[1][1]) / 2.0
            dx, dy = nx - ox, ny - oy
            s.pts[0][0] += dx; s.pts[0][1] += dy; s.pts[1][0] += dx; s.pts[1][1] += dy
        elif role == "c_tl": s.pts[0][0] = nx; s.pts[0][1] = ny
        elif role == "c_tr": s.pts[1][0] = nx; s.pts[0][1] = ny
        elif role == "c_bl": s.pts[0][0] = nx; s.pts[1][1] = ny
        elif role == "c_br": s.pts[1][0] = nx; s.pts[1][1] = ny
        elif role == "e_t": s.pts[0][1] = ny         # top edge
        elif role == "e_b": s.pts[1][1] = ny         # bottom edge
        elif role == "e_l": s.pts[0][0] = nx         # left edge
        elif role == "e_r": s.pts[1][0] = nx         # right edge
        s.rebuild()
        self._reposition(exclude=item)               # keep the OTHER handles glued to the new geometry

    def _on_finish(self, *args) -> None:
        self._dragging = False                       # gesture over -> the next drag snapshots again
        self._reposition(exclude=None)               # snap the dragged (edge) handle back onto the shape
        self.changed.emit()                          # -> controller _save (persist on release)

    def _reposition(self, exclude) -> None:
        if self.shape is None:
            return
        by_role = {role: (x, y) for (x, y, role) in self._specs(self.shape)}
        for hd in self.handles:
            if hd["item"] is exclude:
                continue
            pos = by_role.get(hd["role"])
            if pos is not None:
                hd["item"].blockSignals(True)
                hd["item"].setPos(QtCore.QPointF(pos[0], pos[1]))
                hd["item"].blockSignals(False)

    def reposition(self) -> None:
        """Re-sync every handle to the shape's current geometry (after a panel-driven edit
        like Expand changes the rect without going through a handle drag)."""
        self._reposition(exclude=None)


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------
class DrawingController(QtCore.QObject):
    selectionChanged = QtCore.Signal()   # Magic-Selection rect created / resized / cleared

    def __init__(self, plot: pg.PlotWidget):
        super().__init__()
        self.plot = plot
        self.vb = plot.getViewBox()
        self.active_tool: Optional[str] = None
        self.locked: bool = False      # True while a NON-canvas scanner mode owns the view
        self.index_mode: bool = False  # True on Mode 10 (bucket_canvas): session-only drawings
        self.toolbar = None            # set by terminal -> enables auto-revert (§7.3)

        # time-space drawings (persisted to drawings.json)
        self.shapes: List[DrawnShape] = []
        self.brackets: List[PositionBracket] = []
        # index-space drawings (Mode 10): session-only, flushed on teardown (§6.2)
        self._idx_shapes: List[DrawnShape] = []
        self._idx_brackets: List[PositionBracket] = []

        # live press-drag-release state
        self._live: Optional[DrawnShape] = None   # in-progress shape during a drag
        self._drag_start: Optional[list] = None

        self.edit_panel = ShapeEditPanel(plot.window())
        self.edit_panel.changed.connect(self._save)
        self.handles = ShapeHandles(plot)   # draggable edit dots on the selected shape
        self.handles.changed.connect(self._save)
        self.handles.started.connect(self._push_undo)             # snapshot the PRE-drag geometry for undo
        self.handles.changed.connect(self._reanchor_edited)       # a move/resize must re-anchor + persist,
        self.edit_panel.changed.connect(self.handles.reposition)  # panel geometry edits re-sync the dots
        self.edit_panel.changed.connect(self._reanchor_edited)    # else the next Idx shift snaps it back
        # Magic Selection — a transient dashed rect (NOT in shapes/idx_shapes) with 4 corner handles.
        self._selection: Optional[DrawnShape] = None
        self.sel_handles = ShapeHandles(plot)
        self.sel_handles.changed.connect(self.selectionChanged.emit)   # corner-resize -> recompute stats
        # Mode-10 Idx-anchored persistence state (see set_idx_frame): drawings stored in GLOBAL bucket-Idx
        # coords per (symbol, tf); rendered only while their Idx range is in the displayed window.
        self._idx_ctx = None; self._idx_off = None; self._idx_n = 0
        self._idx_pending: List[dict] = []
        self._idx_save = QtCore.QTimer(); self._idx_save.setSingleShot(True); self._idx_save.setInterval(400)
        self._idx_save.timeout.connect(self._save_idx)
        self.selectionChanged.connect(self._schedule_idx_save)         # set/resize/clear/arrow-extend
        # last-used style memory (per shape KIND: color/width/fill), persisted in drawings.json ->
        # a reopened session creates new shapes with the same style you last set in the edit panel.
        self._styles: dict = self._load_styles()
        self.edit_panel.changed.connect(self._remember_style)
        self._idx_deleted: set = set()   # ids erased THIS session (merge tombstones, multi-window safe)
        self._undo: List[dict] = []      # snapshots of the Idx drawing set, newest last (toolbar ↩️ / ↪️)
        self._redo: List[dict] = []
        self._picked = None              # the shape last selected by clicking it (Delete key target)
        self._idx_busy = False           # re-entrancy guard: selectionChanged -> terminal refresh -> build
                                         # -> set_idx_frame would otherwise RECURSE during restore/shift
        self._idx_bks = None             # reference to the terminal's current filtered bucket list

        # §7.1 — press-drag-release: override the ViewBox drag handler while a tool
        # is armed; the captured original still drives native pan/zoom otherwise.
        self._orig_drag = self.vb.mouseDragEvent
        self.vb.mouseDragEvent = self._vb_drag

        # single-click actions (select / eraser) still ride sigMouseClicked
        plot.scene().sigMouseClicked.connect(self._on_click)
        self._load()

    def update_view(self, x_right: float) -> None:
        """Right-align every bracket's data label to the current view edge (§17)."""
        for br in self.brackets:
            br.set_label_x(x_right)

    # ------------------------------------------------------------------
    def set_tool(self, tool: Optional[str]) -> None:
        if tool == "delete_all":
            self.clear_all(); return
        if tool == "undo":                 # one-shot actions: run and leave the armed tool untouched
            self.undo(); return
        if tool == "redo":
            self.redo(); return
        self.active_tool = tool
        self._cancel_live()
        self.handles.clear()              # changing tool -> drop any edit handles
        if tool != "select":
            self.edit_panel.hide()
        # No setMouseEnabled toggling: _vb_drag gates by active_tool and delegates
        # to the native handler (pan/zoom) for None / select / eraser.

    def cancel(self) -> None:
        self.set_tool(None)
        self._cancel_live()
        self.edit_panel.hide()
        self.handles.clear()
        # Auto toggle the toolbar back to the cursor so a cancelled tool (mode switch, Escape, lock) never
        # lingers visually highlighted as if still armed.
        if self.toolbar is not None:
            self.toolbar.select_tool("select")

    # ------------------------------------------------------------------
    def _on_click(self, ev) -> None:
        # Single-click actions only: select + eraser. Drawing is press-drag-release
        # via _vb_drag. (`locked` blocks everything on non-canvas scanner modes.)
        if self.locked or self.active_tool not in ("select", "eraser"):
            return
        pt = self.vb.mapSceneToView(ev.scenePos())
        x, y = pt.x(), pt.y()
        if self.active_tool == "select":
            self._select_at(x, y)
        else:
            self._erase_at(x, y)

    # ------------------------------------------------------------------
    # §7.1 — press-drag-release engine (overrides ViewBox.mouseDragEvent)
    # ------------------------------------------------------------------
    def _vb_drag(self, ev, axis=None):
        """Route a left-drag to live shape drawing while a draw tool is armed;
        otherwise hand the event straight back to the native pan/zoom handler.

        Wrapped in try/except so any drawing fault can NEVER break the chart's
        native view interaction — it degrades to a normal pan.
        """
        try:
            if (self.locked or self.active_tool in (None, "select", "eraser")
                    or ev.button() != QtCore.Qt.LeftButton):
                return self._orig_drag(ev, axis)
            ev.accept()
            p0 = self.vb.mapSceneToView(ev.buttonDownScenePos())
            p1 = self.vb.mapSceneToView(ev.scenePos())
            if ev.isStart():
                self._begin_draw(p0.x(), p0.y())
            self._update_draw(p1.x(), p1.y())
            if ev.isFinish():
                self._finish_draw(p0.x(), p0.y(), p1.x(), p1.y())
        except Exception:
            try:
                return self._orig_drag(ev, axis)
            except Exception:
                pass

    def _remember_style(self) -> None:
        """Edit-panel change -> remember that KIND's style as the default for future shapes (persisted)."""
        t = self.edit_panel.target
        if t is None:
            return
        self._styles[t.kind] = {"color": t.color, "width": t.width,
                                "fill_color": t.fill_color, "fill_opacity": t.fill_opacity}
        self._save_styles()

    def _save_styles(self) -> None:
        try:
            config.ensure_data_dir()
            data = {}
            if os.path.exists(_DRAW_FILE):
                try:
                    with open(_DRAW_FILE) as f:
                        data = json.load(f)
                except (OSError, json.JSONDecodeError):
                    data = {}
            data["styles"] = self._styles
            with open(_DRAW_FILE, "w") as f:
                json.dump(data, f)
        except OSError:
            pass

    @staticmethod
    def _load_styles() -> dict:
        if not os.path.exists(_DRAW_FILE):
            return {}
        try:
            with open(_DRAW_FILE) as f:
                return dict(json.load(f).get("styles") or {})
        except (OSError, json.JSONDecodeError):
            return {}

    def _styled(self, kind: str, pts: list) -> "DrawnShape":
        """Construct a shape with the REMEMBERED style for its kind (falls back to the built-in
        defaults: rect = borderless white 10% highlight box, others = plain white 2px)."""
        st = self._styles.get(kind)
        if st:
            return DrawnShape(kind, pts, color=st.get("color", "#ffffff"),
                              width=int(st.get("width", 2)),
                              fill_color=st.get("fill_color", "#3498db"),
                              fill_opacity=float(st.get("fill_opacity", 0.0)))
        if kind == "rect":
            return DrawnShape(kind, pts, color="#ffffff", width=0,
                              fill_color="#ffffff", fill_opacity=0.1)
        return DrawnShape(kind, pts)

    def _new_two_point(self, kind: str, pts: list) -> "DrawnShape":
        """Build a two-point shape with the remembered (or default) style for its kind."""
        return self._styled(kind, pts)

    def _begin_draw(self, x: float, y: float) -> None:
        self._cancel_live()
        self._drag_start = [x, y]
        tool = self.active_tool
        if tool == "magic_select":
            self.clear_selection()   # a new drag replaces any prior selection
            self._live = DrawnShape("rect", [[x, y], [x, y]], color="#ffffff", width=1, dashed=True)
        elif tool in _POSITION_TOOLS:
            # translucent rubber-band rect; the real bracket is built on release
            self._live = DrawnShape("rect", [[x, y], [x, y]], color="#888888", width=1)
        elif tool in _SHAPE_TWO_POINT:
            self._live = self._new_two_point(tool, [[x, y], [x, y]])
        else:  # hline / vline
            self._live = DrawnShape(tool, [[x, y]])
        self.plot.addItem(self._live)

    def _update_draw(self, x: float, y: float) -> None:
        if self._live is None or self._drag_start is None:
            return
        if self._live.kind == "hline":
            self._live.pts = [[self._drag_start[0], y]]
        elif self._live.kind == "vline":
            self._live.pts = [[x, self._drag_start[1]]]
        else:
            self._live.pts = [self._drag_start, [x, y]]
        self._live.rebuild()

    def _finish_draw(self, x0: float, y0: float, x1: float, y1: float) -> None:
        tool = self.active_tool
        self._cancel_live()   # remove the rubber-band preview
        if tool == "magic_select":
            self._set_selection([x0, y0], [x1, y1])
            self._drag_start = None
            self.set_tool("select")
            if self.toolbar is not None:
                self.toolbar.select_tool("select")
            return
        self._push_undo()          # the ONE user-creation site (internal _make_bracket calls must not push)
        if tool in _POSITION_TOOLS:
            self._make_bracket(tool, [x0, y0], [x1, y1])
        elif tool in ("hline", "vline"):
            self._commit_shape(self._styled(tool, [[x0, y0]]))
        else:
            self._commit_shape(self._new_two_point(tool, [[x0, y0], [x1, y1]]))
        self._drag_start = None
        # §7.3 — auto-revert to the cursor tool (programmatic check, no signal loop)
        self.set_tool("select")
        if self.toolbar is not None:
            self.toolbar.select_tool("select")

    # ------------------------------------------------------------------
    # Magic Selection (Mode 10) — a transient dashed rect for region stats
    # ------------------------------------------------------------------
    def _set_selection(self, a: list, b: list) -> None:
        self.clear_selection()
        if abs(b[0] - a[0]) < 1e-9 or abs(b[1] - a[1]) < 1e-9:
            return   # zero-area drag -> ignore
        self._selection = DrawnShape("rect", [a, b], color="#ffffff", width=1, dashed=True)
        self._selection.setZValue(85)
        self.plot.addItem(self._selection)
        self.sel_handles.attach(self._selection, corners_only=True)
        self.selectionChanged.emit()

    def clear_selection(self) -> None:
        self.sel_handles.clear()
        if self._selection is not None:
            self.plot.removeItem(self._selection)
            self._selection = None
            self.selectionChanged.emit()

    def selection_rect(self) -> "Optional[tuple]":
        """(x0, y0, x1, y1) of the active Magic Selection in DATA coords (x = bucket index, y =
        price), normalized so x0<=x1 and y0<=y1; None if no selection is active."""
        if self._selection is None or len(self._selection.pts) < 2:
            return None
        (ax, ay), (bx, by) = self._selection.pts[0], self._selection.pts[1]
        return (min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))

    def extend_selection(self, edge: str, dx: float) -> None:
        """Move ONE x-edge of the active Magic Selection by ``dx`` buckets; the OTHER edge stays put. The
        terminal binds BOTH arrows to the RIGHT edge — Right = +1 (extend), Left = -1 (pull back) — so the
        selection grows/shrinks from its right side only. Clamped to keep >= 1 bucket of width (the moved
        edge can't cross the fixed one). Y unchanged; rect + handles move in place; one ``selectionChanged``.
        No-op without a selection (or when the clamp leaves it unmoved)."""
        s = self._selection
        if s is None or len(s.pts) < 2:
            return
        i_right = 0 if s.pts[0][0] >= s.pts[1][0] else 1   # stored point that is the RIGHT (max-x) edge
        i = i_right if edge == "right" else 1 - i_right    # "left" -> the MIN-x point
        j = 1 - i                                          # the FIXED (opposite) edge
        new_x = s.pts[i][0] + dx
        if i == i_right:                                   # right edge must stay >= left + 1 bucket
            new_x = max(new_x, s.pts[j][0] + 1.0)
        else:                                              # left edge must stay <= right - 1 bucket
            new_x = min(new_x, s.pts[j][0] - 1.0)
        if abs(new_x - s.pts[i][0]) < 1e-9:
            return                                         # clamped to the same spot -> no-op
        s.pts[i][0] = new_x
        s.rebuild()
        self.sel_handles.attach(s, corners_only=True)      # re-glue the corner handles to the new geometry
        self.selectionChanged.emit()

    # ------------------------------------------------------------------
    def _make_bracket(self, kind, a, b, entry=None, stop=None, target=None) -> PositionBracket:
        x0, x1 = sorted((a[0], b[0]))
        if entry is None:
            entry = a[1]
            risk = max(config.TICK_SIZE, abs(b[1] - entry))
            if kind == "long":
                stop, target = entry - risk, entry + risk * 1.5
            else:
                stop, target = entry + risk, entry - risk * 1.5
        bracket = PositionBracket(self.plot, kind, x0, x1, entry, stop, target)
        if self.index_mode:
            if not hasattr(bracket, "uid"):
                bracket.uid = uuid.uuid4().hex
            self._idx_brackets.append(bracket)   # Mode 10 index space (Idx-anchored, persisted)
            bracket.changed.connect(self._schedule_idx_save)
            self._schedule_idx_save()
        else:
            bracket.changed.connect(self._save)
            self.brackets.append(bracket)
            self._save()
        return bracket

    def _commit_shape(self, shape: DrawnShape) -> None:
        self.plot.addItem(shape)
        if self.index_mode:
            if not hasattr(shape, "uid"):
                shape.uid = uuid.uuid4().hex     # identity for multi-window merge-by-id saves
            if self._idx_bks and shape.kind != "hline":      # hlines are PRICE-only (tf-universal)
                shape.anchors = self._anchor_pts(shape.pts)
            self._idx_shapes.append(shape)       # Mode 10 index space (Idx-anchored, persisted)
            self._schedule_idx_save()
        else:
            self.shapes.append(shape)
            self._save()

    def _cancel_live(self) -> None:
        if self._live is not None:
            self.plot.removeItem(self._live)
            self._live = None
        self._drag_start = None

    def _select_at(self, x, y) -> None:
        (x0, x1), (y0, y1) = self.vb.viewRange()
        tol_x = (x1 - x0) * 0.015
        tol_y = (y1 - y0) * 0.015
        # scan both coordinate spaces so editing is unified (§6.2)
        for s in reversed(self.shapes + self._idx_shapes):   # topmost first
            if s.near(x, y, tol_x, tol_y):
                self._picked = s
                self.edit_panel.bind(s)
                self.handles.attach(s)        # show draggable edit handles on the picked shape
                return
        self._picked = None
        self.edit_panel.hide()   # clicked empty space
        self.handles.clear()

    def delete_selected(self) -> None:
        """Delete/Backspace: remove the clicked-selected shape (tombstoned + persisted); with no shape
        picked, an active Magic Selection is cleared instead."""
        s = self._picked
        if s is not None:
            self._push_undo()                       # ↩️ can bring an accidental delete back
            for store in (self.shapes, self._idx_shapes):
                if s in store:
                    self.plot.removeItem(s); store.remove(s)
                    if store is self._idx_shapes and hasattr(s, "uid"):
                        self._idx_deleted.add(s.uid)
            self._picked = None
            self.edit_panel.hide(); self.handles.clear()
            self._save(); self._schedule_idx_save()
            return
        if self._selection is not None:
            self.clear_selection()

    def _erase_at(self, x, y) -> None:
        self._push_undo()            # ↩️ can bring an erased shape back
        sr = self.selection_rect()   # eraser inside the Magic Selection box -> remove it
        if sr is not None and sr[0] <= x <= sr[2] and sr[1] <= y <= sr[3]:
            self.clear_selection()
        (x0, x1), (y0, y1) = self.vb.viewRange()
        tol_x = (x1 - x0) * 0.01
        tol_y = (y1 - y0) * 0.01
        for store in (self.shapes, self._idx_shapes):
            for s in list(store):
                if s.near(x, y, tol_x, tol_y):
                    self.plot.removeItem(s); store.remove(s)
                    if store is self._idx_shapes and hasattr(s, "uid"):
                        self._idx_deleted.add(s.uid)          # tombstone: don't resurrect from disk
        for store in (self.brackets, self._idx_brackets):
            for br in list(store):
                if br.near(x, y, tol_x, tol_y):
                    br.remove(); store.remove(br)
                    if store is self._idx_brackets and hasattr(br, "uid"):
                        self._idx_deleted.add(br.uid)
        self.handles.clear()   # a selected shape may have been erased -> drop its handles
        self._save()
        self._schedule_idx_save()

    def clear_all(self) -> None:
        self._push_undo()            # 🗑️ Delete All is recoverable with a single ↩️
        for s in self._idx_shapes:
            if hasattr(s, "uid"):
                self._idx_deleted.add(s.uid)
        for br in self._idx_brackets:
            if hasattr(br, "uid"):
                self._idx_deleted.add(br.uid)
        self._idx_deleted.update(d.get("id") for d in self._idx_pending if d.get("id"))
        self._idx_pending = []
        for s in self.shapes + self._idx_shapes:
            self.plot.removeItem(s)
        for br in self.brackets + self._idx_brackets:
            br.remove()
        self.shapes.clear(); self.brackets.clear()
        self._idx_shapes.clear(); self._idx_brackets.clear()
        self.handles.clear()
        self.clear_selection()   # the trash tool wipes the Magic Selection too
        self._cancel_live()
        self._save()

    def set_index_visible(self, on: bool) -> None:
        """Show/hide the session-only index-space (Mode 10) drawings WITHOUT destroying them, so they
        survive mode switches: shown on Mode 10, hidden on the metric scanners (where a price-anchored
        shape is off-axis), restored on return. Edit handles drop when hidden."""
        for s in self._idx_shapes:
            s.setVisible(on)
        for br in self._idx_brackets:
            br.set_visible(on)
        if not on:
            self.handles.clear()

    def flush_index_drawings(self) -> None:
        """Mode-10 teardown (§6.2): PERSIST the index-space drawings (Idx-anchored, see set_idx_frame),
        then clear the rendered items. They reload on the next Mode-10 entry via set_idx_frame; the
        time-space shapes/brackets are untouched."""
        if self._idx_ctx is not None:
            self._save_idx()
        for s in self._idx_shapes:
            self.plot.removeItem(s)
        for br in self._idx_brackets:
            br.remove()
        self._idx_shapes.clear()
        self._idx_brackets.clear()
        self.clear_selection()               # the Magic Selection is idx-space too (persisted just above)
        self._idx_pending = []; self._idx_ctx = None; self._idx_off = None
        self.handles.clear()   # a selected Mode-10 index shape may be going away
        self._cancel_live()

    # ------------------------------------------------------------------
    # Mode-10 Idx-anchored persistence: drawings are stored in GLOBAL bucket-Idx coordinates (the
    # monotonic per-tf counter — stable across the 10k cap and restarts) and re-rendered only while
    # their full Idx range is inside the displayed window; otherwise they stay in the file, hidden.
    # ------------------------------------------------------------------
    def _schedule_idx_save(self) -> None:
        if self.index_mode and self._idx_ctx is not None:
            self._idx_save.start()

    def _find_ts(self, ts: float):
        """Binary-search the current bucket frame for the bucket at end_time ``ts`` -> array index, or
        None when that bucket is genuinely outside the loaded frame. end_time is strictly increasing.

        NEAREST-BUCKET SNAP (not exact match): a bucket's end_time MOVES. Whatever bucket is still
        forming when the daemon restarts gets re-closed at a different end_time, so an anchor taken
        against it no longer matches to 1e-6 — and since _render_idx_pending drops a shape when ANY of
        its anchors miss, one drifted corner stranded the whole drawing in _idx_pending forever: saved
        perfectly, invisible after every restart. Measured on real anchors: the drift was 4-104s while
        the local bucket spacing was ~155s, i.e. ALWAYS less than one bucket.

        So: if ``ts`` is BRACKETED by two buckets it lies inside the frame's span -> snap to the nearer
        one (at most half a bucket away — the same bucket its author clicked). Only when it falls off
        either END is it really outside the frame, and it stays pending exactly as before, so a window
        that later grows to cover it still renders it. No tolerance constant: the bracketing IS the
        tolerance, which self-scales across 1m..4h instead of baking in a magic number."""
        bks = self._idx_bks
        if not bks or ts is None:
            return None
        lo, hi = 0, len(bks) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            v = float(bks[mid].get("end_time", 0.0))
            if abs(v - ts) < 1e-6:
                return mid                      # exact hit (the common case)
            if v < ts:
                lo = mid + 1
            else:
                hi = mid - 1
        # loop exit invariant: bks[hi] < ts < bks[lo], with hi == lo - 1.
        n = len(bks)
        if 0 <= hi < n and 0 <= lo < n:         # bracketed -> inside the frame; take the closer bucket
            return hi if (ts - float(bks[hi].get("end_time", 0.0))) <= \
                         (float(bks[lo].get("end_time", 0.0)) - ts) else lo
        return None                             # before the first / after the last -> outside the frame

    def _anchor_pts(self, pts):
        """[(ts, frac), ...] time-anchors for array-coord points (ts = the bucket's end_time)."""
        bks = self._idx_bks
        if not bks:
            return None
        out = []
        for px, _py in pts:
            j = max(0, min(len(bks) - 1, int(round(px))))
            out.append([float(bks[j].get("end_time", 0.0)), _afrac(px - j)])   # j is clamped -> so must the frac be
        return out

    # ------------------------------------------------------------------
    # Undo / redo (toolbar ↩️ / ↪️ — Ctrl+Z is already the Selection VP). Snapshot-based: the whole
    # Mode-10 Idx drawing set is serialized to the SAME dicts the persisted-render path consumes, so
    # a restore is just "swap the pending set + re-render" through machinery that is already proven.
    # ------------------------------------------------------------------
    _UNDO_MAX = 60

    def _snapshot(self) -> Optional[dict]:
        """The full Idx drawing set in GLOBAL coords + the delete-tombstones, or None when unavailable."""
        if not self.index_mode or self._idx_off is None:
            return None
        off = self._idx_off
        idx = [self._shape_dict_global(s, off) for s in self._idx_shapes if s.kind != "hline"]
        idx += [self._shape_dict_global(s, 0.0) for s in self._idx_shapes if s.kind == "hline"]
        for b in self._idx_brackets:
            bd = b.to_dict()
            anch = (self._anchor_pts([[bd["x0"], 0], [bd["x1"], 0]])
                    if self._idx_bks else getattr(b, "anchors", None))
            idx.append(dict(bd, t="bracket", id=getattr(b, "uid", None) or uuid.uuid4().hex,
                            anch=anch, **{"x0": bd["x0"] + off, "x1": bd["x1"] + off}))
        idx += [dict(d) for d in self._idx_pending]      # offscreen items are part of the set too
        return {"idx": idx, "deleted": set(self._idx_deleted)}

    def _push_undo(self) -> None:
        """Snapshot BEFORE a mutation. Any new edit invalidates the redo branch (standard undo semantics)."""
        st = self._snapshot()
        if st is None:
            return
        self._undo.append(st)
        del self._undo[:-self._UNDO_MAX]                  # bounded history
        self._redo.clear()

    def _restore(self, st: dict) -> None:
        """Rebuild the Idx set from a snapshot: drop the rendered items, swap in the snapshot as the
        pending set, and let _render_idx_pending re-materialise whatever is inside the window."""
        self._idx_busy = True          # a rebuild must not re-enter set_idx_frame via selectionChanged
        try:
            for s in list(self._idx_shapes):
                self.plot.removeItem(s)
            for br in list(self._idx_brackets):
                br.remove()
            self._idx_shapes.clear(); self._idx_brackets.clear()
            self.handles.clear(); self._picked = None
            self.edit_panel.hide()
            self._idx_deleted = set(st["deleted"])
            self._idx_pending = [dict(d) for d in st["idx"]]
        finally:
            self._idx_busy = False
        self._render_idx_pending()
        self._schedule_idx_save()

    def undo(self) -> None:
        """Toolbar ↩️ — step back one draw / move / delete."""
        if not self._undo:
            return
        cur = self._snapshot()
        st = self._undo.pop()
        if cur is not None:
            self._redo.append(cur)
        self._restore(st)

    def redo(self) -> None:
        """Toolbar ↪️ — re-apply the step that undo took back."""
        if not self._redo:
            return
        cur = self._snapshot()
        st = self._redo.pop()
        if cur is not None:
            self._undo.append(cur)
        self._restore(st)

    def _reanchor_edited(self) -> None:
        """A handle drag / edit-panel geometry change MOVED or RESIZED the picked shape, so its pts are now
        the authoritative position -> re-derive its time-anchors from them, and persist.

        anchors are otherwise set ONLY at creation (see the _idx_shapes append), while _set_idx_frame_inner
        rebuilds x FROM the anchors every time the global Idx offset shifts. So without this an edited shape
        snapped straight back to where it was first drawn on the next offset change (e.g. a REPLAY step),
        silently dropping the edit. Re-anchoring is only safe HERE, where the shape is correctly placed in the
        current frame — _shape_dict_global deliberately does NOT re-anchor, since a shape being demoted after
        its bucket left the window is NOT correctly placed and would corrupt its anchors."""
        s = self._picked
        if s is None or not self.index_mode or s.kind == "hline":
            return                                    # hline is price-only: x is meaningless, so is its anchor
        if s in self._idx_shapes and self._idx_bks:
            s.anchors = self._anchor_pts(s.pts)       # pts are authoritative here -> anchors follow the edit
            self._schedule_idx_save()                 # persist the move (handles.changed alone only ran _save)

    def set_idx_frame(self, offset: float, n: int, tf: str, bks=None) -> None:
        """Per-draw hook from the terminal (Mode 10): keeps idx drawings glued to their BUCKETS —
        when the cap/anchor shifts the array under them the rendered items shift by the offset delta
        (so a selection stays on the same candles instead of silently sliding) — and renders any
        persisted drawing whose bucket range has entered the window. Loads the (symbol, tf) set on
        first call / tf switch."""
        if not self.index_mode or self._idx_busy:
            return
        self._idx_busy = True
        try:
            self._idx_bks = bks
            self._set_idx_frame_inner(offset, n, tf)
        finally:
            self._idx_busy = False

    def _set_idx_frame_inner(self, offset: float, n: int, tf: str) -> None:
        ctx = (config.SYMBOL, tf)
        if ctx != self._idx_ctx:
            if self._idx_ctx is not None:                    # tf/symbol switch: persist + CLEAR the old
                self._save_idx()                             # set so 1m drawings never leak into 5m
                for s in self._idx_shapes:
                    self.plot.removeItem(s)
                for br in self._idx_brackets:
                    br.remove()
                self._idx_shapes.clear(); self._idx_brackets.clear()
                self.clear_selection()
                self.handles.clear()
            self._idx_ctx = ctx
            self._idx_pending = self._load_idx()
            self._idx_off = None
            self._idx_deleted = set()
        if self._idx_off is not None and offset != self._idx_off:
            d = self._idx_off - offset                      # array coords slide by (old − new)
            for s in list(self._idx_shapes):
                if s.kind == "hline":
                    continue                                  # price-only: x is meaningless, never moves
                anch = getattr(s, "anchors", None)
                if anch and self._idx_bks:
                    js = [self._find_ts(ts) for ts, _f in anch]
                    if any(j is None for j in js):          # its bucket left the window -> back to hidden
                        self.plot.removeItem(s); self._idx_shapes.remove(s)
                        self._idx_pending.append(self._shape_dict_global(s, self._idx_off))
                        continue
                    s.pts = [[j + _afrac(f), py] for (j, (_t, f), (_px, py)) in zip(js, anch, s.pts)]
                else:
                    s.pts = [[px + d, py] for px, py in s.pts]
                s.rebuild()
            if self._selection is not None and len(self._selection.pts) == 2:
                anch = getattr(self._selection, "anchors", None)
                if anch and self._idx_bks:
                    js = [self._find_ts(ts) for ts, _f in anch]
                    if all(j is not None for j in js):
                        self._selection.pts = [[j + f, py] for (j, (_t, f), (_px, py))
                                               in zip(js, anch, self._selection.pts)]
                    else:
                        self._selection.pts = [[px + d, py] for px, py in self._selection.pts]
                else:
                    self._selection.pts = [[px + d, py] for px, py in self._selection.pts]
                self._selection.rebuild()
                self.sel_handles.attach(self._selection, corners_only=True)
                QtCore.QTimer.singleShot(0, self.selectionChanged.emit)   # DEFERRED: after this draw pass
            for br in list(self._idx_brackets):
                dd = br.to_dict()
                anch = getattr(br, "anchors", None)
                self._idx_brackets.remove(br); br.remove()
                if anch and self._idx_bks:
                    j0 = self._find_ts(anch[0][0]); j1 = self._find_ts(anch[1][0])
                else:
                    j0 = j1 = None
                if j0 is not None and j1 is not None:
                    nx0, nx1 = j0 + anch[0][1], j1 + anch[1][1]
                else:
                    nx0, nx1 = dd["x0"] + d, dd["x1"] + d
                nb = self._make_bracket(dd["kind"], [nx0, dd["entry"]], [nx1, dd["stop"]],
                                        entry=dd["entry"], stop=dd["stop"], target=dd["target"])
                nb.uid = getattr(br, "uid", nb.uid)     # SAME identity — else every shift duplicates the
                if anch:                                # bracket in the file (merge keeps the orphaned id)
                    nb.anchors = anch
        self._idx_off = offset; self._idx_n = int(n)
        self._render_idx_pending()

    def _render_idx_pending(self) -> None:
        if not self._idx_pending or self._idx_off is None:
            return
        off = self._idx_off; keep = []
        todo, self._idx_pending = self._idx_pending, []      # swap first: re-entrant calls see empty
        sel_restored = False
        for d in todo:
            anch = d.get("anch")
            if d.get("kind") == "hline":                     # tf-universal price level: always renderable
                axs = [0.0]
                anch = None
            elif anch and self._idx_bks:                     # TIME anchor: exact as soon as the bucket loads
                js = [self._find_ts(ts) for ts, _f in anch]
                if any(j is None for j in js):
                    keep.append(d); continue
                axs = [j + _afrac(f) for j, (_t, f) in zip(js, anch)]   # heal a frac saved out of range
            elif d["t"] == "bracket":
                axs = [d["x0"] - off, d["x1"] - off]
            else:
                axs = [p[0] - off for p in d["pts"]]
            if all(-0.5 <= x <= self._idx_n - 0.5 for x in axs):    # bucket Idx visible -> render
                ys = ([d["entry"], d["stop"]] if d["t"] == "bracket" else [p[1] for p in d["pts"]])
                if d["t"] == "shape":
                    s = DrawnShape(d["kind"], [[ax, py] for ax, py in zip(axs, ys)],
                                   d.get("color", "#ffffff"), d.get("width", 2),
                                   d.get("fill_color", "#3498db"), d.get("fill_opacity", 0.0))
                    s.uid = d.get("id") or uuid.uuid4().hex
                    if anch:
                        s.anchors = anch
                    self.plot.addItem(s); self._idx_shapes.append(s)
                elif d["t"] == "bracket":
                    _nb = self._make_bracket(d["kind"], [axs[0], d["entry"]], [axs[1], d["stop"]],
                                             entry=d["entry"], stop=d["stop"], target=d["target"])
                    _nb.uid = d.get("id") or _nb.uid
                    if anch:
                        _nb.anchors = anch
                else:                                       # the Magic Selection
                    a, b = d["pts"]
                    blocker = QtCore.QSignalBlocker(self)   # no synchronous re-entrancy mid-restore
                    try:
                        self._set_selection([axs[0], a[1]], [axs[1], b[1]])
                    finally:
                        del blocker
                    if self._selection is not None and anch:
                        self._selection.anchors = anch
                    sel_restored = True
            else:
                keep.append(d)                              # hidden until its bucket Idx shows
        self._idx_pending = keep + self._idx_pending
        if sel_restored:
            QtCore.QTimer.singleShot(0, self.selectionChanged.emit)   # panels anchor AFTER the draw pass

    def _shape_dict_global(self, s, off) -> dict:
        # EXISTING anchors are authoritative (re-placement keeps pts in sync with them); only anchor
        # from pts for legacy shapes that never had them. Re-anchoring from pts against a frame the
        # shape is NOT correctly placed in (e.g. demotion after its bucket left) would corrupt them.
        anch = getattr(s, "anchors", None) or (self._anchor_pts(s.pts) if self._idx_bks else None)
        return dict(s.to_dict(), t="shape", id=getattr(s, "uid", None) or uuid.uuid4().hex,
                    anch=anch, pts=[[p[0] + off, p[1]] for p in s.pts])

    def _save_idx(self) -> None:
        if self._idx_ctx is None or self._idx_off is None:
            return
        off = self._idx_off
        mine_shapes = [self._shape_dict_global(s, off) for s in self._idx_shapes
                       if s.kind != "hline"]
        mine_hl = [self._shape_dict_global(s, 0.0) for s in self._idx_shapes
                   if s.kind == "hline"]                     # x meaningless -> stored as-is
        mine_brs = []
        for b in self._idx_brackets:
            bd = b.to_dict()
            anch = (self._anchor_pts([[bd["x0"], 0], [bd["x1"], 0]])
                    if self._idx_bks else getattr(b, "anchors", None))
            mine_brs.append(dict(bd, id=getattr(b, "uid", None) or uuid.uuid4().hex,
                                 anch=anch, **{"x0": bd["x0"] + off, "x1": bd["x1"] + off}))
        mine_ids = ({d["id"] for d in mine_shapes} | {d["id"] for d in mine_brs}
                    | {d["id"] for d in mine_hl}
                    | {d.get("id") for d in self._idx_pending if d.get("id")})
        # MERGE with disk (multi-window): keep other windows' items (ids we neither own nor deleted)
        disk = {}
        if os.path.exists(_DRAW_FILE):
            try:
                with open(_DRAW_FILE) as f:
                    disk = (json.load(f).get("idx") or {}).get("|".join(self._idx_ctx), {})
            except (OSError, json.JSONDecodeError):
                disk = {}
        def _foreign(items):
            return [d for d in items
                    if d.get("id") and d["id"] not in mine_ids and d["id"] not in self._idx_deleted]
        entry = {
            "shapes": mine_shapes + _foreign(disk.get("shapes", [])),
            "brackets": mine_brs + _foreign(disk.get("brackets", [])),
            "selection": ([[p[0] + off, p[1]] for p in self._selection.pts]
                          if (self._selection is not None and len(self._selection.pts) == 2) else None),
            "sel_anch": (self._anchor_pts(self._selection.pts)
                         if (self._selection is not None and len(self._selection.pts) == 2
                             and self._idx_bks) else None),
            "pending": self._idx_pending + _foreign(disk.get("pending", [])),
        }
        # GLOBAL horizontal price levels (tf-universal), merged by id like everything else
        hdisk = {}
        if os.path.exists(_DRAW_FILE):
            try:
                with open(_DRAW_FILE) as f:
                    hdisk = (json.load(f).get("idx") or {}).get(config.SYMBOL + "|hlines", {})
            except (OSError, json.JSONDecodeError):
                hdisk = {}
        hentry = {"shapes": mine_hl + _foreign(hdisk.get("shapes", []))}
        try:
            config.ensure_data_dir()
            data = {}
            if os.path.exists(_DRAW_FILE):
                try:
                    with open(_DRAW_FILE) as f:
                        data = json.load(f)
                except (OSError, json.JSONDecodeError):
                    data = {}
            data.setdefault("idx", {})["|".join(self._idx_ctx)] = entry
            data["idx"][config.SYMBOL + "|hlines"] = hentry
            with open(_DRAW_FILE, "w") as f:
                json.dump(data, f)
        except OSError:
            pass

    def _load_idx(self) -> list:
        if not os.path.exists(_DRAW_FILE):
            return []
        try:
            with open(_DRAW_FILE) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return []
        idxd = data.get("idx") or {}
        e = idxd.get("|".join(self._idx_ctx), {})
        out = [dict(d, t="shape") for d in e.get("shapes", [])]
        out += [dict(d, t="shape") for d in idxd.get(config.SYMBOL + "|hlines", {}).get("shapes", [])]
        out += [dict(d, t="bracket") for d in e.get("brackets", [])]
        if e.get("selection"):
            out.append({"t": "selection", "pts": e["selection"], "anch": e.get("sel_anch")})
        out += e.get("pending", [])
        return out

    def earliest_drawing_ts(self, tf: str):
        """Oldest saved-drawing TIME-anchor (a bucket end_time) for (SYMBOL, tf), or None when this
        tf has no anchored drawings. The terminal reads this to pull the scan Zero Point back far
        enough that persisted drawings are never stranded outside the frame. hlines (price-only, no
        time anchor) are ignored; un-anchored legacy items contribute nothing."""
        if not os.path.exists(_DRAW_FILE):
            return None
        try:
            with open(_DRAW_FILE) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
        e = (data.get("idx") or {}).get("%s|%s" % (config.SYMBOL, tf), {})
        tss = []
        for d in (e.get("shapes", []) + e.get("brackets", []) + e.get("pending", [])):
            if d.get("kind") == "hline":
                continue
            for a in (d.get("anch") or []):
                try:
                    tss.append(float(a[0]))
                except (TypeError, ValueError, IndexError):
                    pass
        return min(tss) if tss else None

    # ------------------------------------------------------------------
    # Persistence (replaces browser localStorage, spec §8.3)
    # ------------------------------------------------------------------
    def _save(self) -> None:
        try:
            config.ensure_data_dir()
            payload = {}
            if os.path.exists(_DRAW_FILE):
                try:
                    with open(_DRAW_FILE) as f:
                        payload = json.load(f)
                except (OSError, json.JSONDecodeError):
                    payload = {}
            payload[config.SYMBOL] = {
                "shapes": [s.to_dict() for s in self.shapes],
                "brackets": [b.to_dict() for b in self.brackets],
            }
            with open(_DRAW_FILE, "w") as f:
                json.dump(payload, f)
        except OSError:
            pass

    def _load(self) -> None:
        if not os.path.exists(_DRAW_FILE):
            return
        try:
            with open(_DRAW_FILE) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        entry = data.get(config.SYMBOL, {})
        shapes = entry.get("shapes", entry) if isinstance(entry, dict) else entry  # back-compat
        for d in (shapes or []):
            shape = DrawnShape(d["kind"], d["pts"], d.get("color", "#ffffff"), d.get("width", 2),
                               d.get("fill_color", "#3498db"), d.get("fill_opacity", 0.0))
            self.shapes.append(shape)
            self.plot.addItem(shape)
        for d in (entry.get("brackets", []) if isinstance(entry, dict) else []):
            self._make_bracket(d["kind"], [d["x0"], d["entry"]], [d["x1"], d["stop"]],
                               entry=d["entry"], stop=d["stop"], target=d["target"])
