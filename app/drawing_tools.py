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
from typing import List, Optional

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from . import config

TOOLS = ["select", "trend", "ray", "hline", "vline", "rect", "ellipse",
         "measure", "long", "short", "eraser", "delete_all"]
_LABELS = {
    "select": "↖️", "trend": "📏", "ray": "↗️", "hline": "➖", "vline": "｜",
    "rect": "🔲", "ellipse": "⭕", "measure": "📐", "long": "📈", "short": "📉",
    "eraser": "🧽", "delete_all": "🗑️",
}
_TOOLTIPS = {
    "select": "Select / Edit", "trend": "Trend Line", "ray": "Extended Ray",
    "hline": "Horizontal Line", "vline": "Vertical Line", "rect": "Rectangle",
    "ellipse": "Ellipse", "measure": "Measure %", "long": "Long Position",
    "short": "Short Position", "eraser": "Eraser", "delete_all": "Delete All",
}
_SHAPE_TWO_POINT = {"trend", "ray", "rect", "ellipse", "measure"}
_POSITION_TOOLS = {"long", "short"}
_PRESET_COLORS = ["#ffffff", "#000000", "#2962ff", "#e74c3c", "#1abc9c", "#f1c40f", "#9b59b6", "#ff7f0e"]
_DRAW_FILE = os.path.join(config.DATA_DIR, "drawings.json")


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
                 fill_color: str = "#3498db", fill_opacity: float = 0.0):
        super().__init__()
        self.kind = kind
        self.pts = [list(p) for p in pts]
        self.color = color
        self.width = width
        self.fill_color = fill_color          # rect/ellipse interior fill colour
        self.fill_opacity = fill_opacity      # 0.0 = outline-only (default)
        self.picture = QtGui.QPicture()
        self._rect = QtCore.QRectF()
        self.setAcceptHoverEvents(True)   # pointing-hand cursor on hover (patch §18)
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
        pen = QtGui.QPen(QtGui.QColor(self.color)); pen.setCosmetic(True); pen.setWidth(self.width)
        p.setPen(pen); p.setFont(QtGui.QFont("Consolas", 8))
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
            elif self.kind == "rect":
                p.drawRect(QtCore.QRectF(x0, y0, self.pts[1][0] - x0, self.pts[1][1] - y0))
            elif self.kind == "ellipse":
                p.drawEllipse(QtCore.QRectF(x0, y0, self.pts[1][0] - x0, self.pts[1][1] - y0))
        p.end()
        xs = [pt[0] for pt in self.pts]; ys = [pt[1] for pt in self.pts]
        self._rect = QtCore.QRectF(min(xs), min(ys), max(1.0, max(xs) - min(xs)),
                                   max(1e-6, max(ys) - min(ys)))
        self.prepareGeometryChange(); self.update()

    def near(self, x, y, tol_x, tol_y) -> bool:
        return any(abs(px - x) <= tol_x and abs(py - y) <= tol_y for px, py in self.pts)

    def paint(self, p, *a): p.drawPicture(0, 0, self.picture)
    def boundingRect(self): return self._rect


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
        green = QtGui.QColor(46, 204, 113, 70)
        red = QtGui.QColor(231, 76, 60, 70)
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
        # right Y-axis margin when label_x tracks the view edge (patch §17).
        self.label = pg.TextItem(anchor=(1, 1))
        self.label.setZValue(70)
        plot.addItem(self.label, ignoreBounds=True)

        # Right-anchored per-line value labels (bold value; SL/TP also show % vs entry).
        # Replaces the InfiniteLine built-in left-side labels.
        self._val_labels = {}
        for nm in ("SL", "Entry", "TP"):
            t = pg.TextItem(anchor=(0.5, 0.5), fill=pg.mkBrush(255, 255, 255))
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
        # centered value labels — black bold text on a white bg; SL always shows a
        # negative % and TP a positive % (risk vs reward), regardless of long/short
        xc = (self.x0 + self.x1) / 2.0
        sl_pct = abs(s - e) / e * 100.0 if e else 0.0
        tp_pct = abs(t - e) / e * 100.0 if e else 0.0
        _st = "color:#000000;font-size:14px"
        self._val_labels["SL"].setHtml(f"<span style='{_st}'><b>{s:.2f}</b> (-{sl_pct:.2f}%)</span>")
        self._val_labels["Entry"].setHtml(f"<span style='{_st}'><b>{e:.2f}</b></span>")
        self._val_labels["TP"].setHtml(f"<span style='{_st}'><b>{t:.2f}</b> (+{tp_pct:.2f}%)</span>")
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
            if t != "delete_all":
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
        self.spin = QtWidgets.QSpinBox(); self.spin.setRange(1, 8)
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
        self.spin.blockSignals(True)
        self.spin.setValue(shape.width)
        self.spin.blockSignals(False)
        fillable = shape.kind in ("rect", "ellipse")     # fill only meaningful for closed shapes
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

    def __init__(self, plot: pg.PlotWidget):
        super().__init__()
        self.plot = plot
        self.vb = plot.getViewBox()
        self.shape: Optional[DrawnShape] = None
        self.handles: list = []      # [{"item": TargetItem, "role": str}]

    def attach(self, shape: "DrawnShape") -> None:
        self.clear()
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
            mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            return [(x0, y0, "c_tl"), (x1, y0, "c_tr"), (x0, y1, "c_bl"), (x1, y1, "c_br"),
                    (mx, y0, "e_t"), (mx, y1, "e_b"), (x0, my, "e_l"), (x1, my, "e_r")]
        return []

    def _on_drag(self, item, role: str) -> None:
        s = self.shape
        if s is None:
            return
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
        self.edit_panel.changed.connect(self.handles.reposition)  # panel geometry edits re-sync the dots

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

    def _begin_draw(self, x: float, y: float) -> None:
        self._cancel_live()
        self._drag_start = [x, y]
        tool = self.active_tool
        if tool in _POSITION_TOOLS:
            # translucent rubber-band rect; the real bracket is built on release
            self._live = DrawnShape("rect", [[x, y], [x, y]], color="#888888", width=1)
        elif tool in _SHAPE_TWO_POINT:
            self._live = DrawnShape(tool, [[x, y], [x, y]])
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
        if tool in _POSITION_TOOLS:
            self._make_bracket(tool, [x0, y0], [x1, y1])
        elif tool in ("hline", "vline"):
            self._commit_shape(DrawnShape(tool, [[x0, y0]]))
        else:
            self._commit_shape(DrawnShape(tool, [[x0, y0], [x1, y1]]))
        self._drag_start = None
        # §7.3 — auto-revert to the cursor tool (programmatic check, no signal loop)
        self.set_tool("select")
        if self.toolbar is not None:
            self.toolbar.select_tool("select")

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
            self._idx_brackets.append(bracket)   # session-only, not persisted
        else:
            bracket.changed.connect(self._save)
            self.brackets.append(bracket)
            self._save()
        return bracket

    def _commit_shape(self, shape: DrawnShape) -> None:
        self.plot.addItem(shape)
        if self.index_mode:
            self._idx_shapes.append(shape)       # session-only (Mode 10 index space)
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
                self.edit_panel.bind(s)
                self.handles.attach(s)        # show draggable edit handles on the picked shape
                return
        self.edit_panel.hide()   # clicked empty space
        self.handles.clear()

    def _erase_at(self, x, y) -> None:
        (x0, x1), (y0, y1) = self.vb.viewRange()
        tol_x = (x1 - x0) * 0.01
        tol_y = (y1 - y0) * 0.01
        for store in (self.shapes, self._idx_shapes):
            for s in list(store):
                if s.near(x, y, tol_x, tol_y):
                    self.plot.removeItem(s); store.remove(s)
        for store in (self.brackets, self._idx_brackets):
            for br in list(store):
                if br.near(x, y, tol_x, tol_y):
                    br.remove(); store.remove(br)
        self.handles.clear()   # a selected shape may have been erased -> drop its handles
        self._save()

    def clear_all(self) -> None:
        for s in self.shapes + self._idx_shapes:
            self.plot.removeItem(s)
        for br in self.brackets + self._idx_brackets:
            br.remove()
        self.shapes.clear(); self.brackets.clear()
        self._idx_shapes.clear(); self._idx_brackets.clear()
        self.handles.clear()
        self._cancel_live()
        self._save()

    def flush_index_drawings(self) -> None:
        """Wipe the session-only index-space drawings (Mode 10 teardown, §6.2).

        Touches ONLY the index lists — the persisted time-space shapes/brackets and
        ``drawings.json`` are untouched.
        """
        for s in self._idx_shapes:
            self.plot.removeItem(s)
        for br in self._idx_brackets:
            br.remove()
        self._idx_shapes.clear()
        self._idx_brackets.clear()
        self.handles.clear()   # a selected Mode-10 index shape may be going away
        self._cancel_live()

    # ------------------------------------------------------------------
    # Persistence (replaces browser localStorage, spec §8.3)
    # ------------------------------------------------------------------
    def _save(self) -> None:
        try:
            config.ensure_data_dir()
            payload = {config.SYMBOL: {
                "shapes": [s.to_dict() for s in self.shapes],
                "brackets": [b.to_dict() for b in self.brackets],
            }}
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
