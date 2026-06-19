"""Tier-3 consolidated control hub (spec §7).

Every interactive control lives inside the sliding top-right ``[☰]`` panel so the
white canvas stays nude (spec §5.1.3). Controls emit Qt signals the window wires
to render-layer toggles and the socket client.

Excluded by Purge Protocol (§10.1): no EMA / Keltner toggles, no Market Replay,
no Quant Sniper panel, no "Copy AI Data" export.
"""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from . import config

_BTN_QSS = """
QPushButton {
  background-color: rgba(30, 34, 45, 0.85);
  color: #ffffff;
  border: 1px solid #2a2e39;
  border-radius: 4px;
  font-size: 16px;
  font-weight: bold;
}
QPushButton:hover { background-color: #3498db; }
"""

_PANEL_QSS = """
QFrame#overlay { background-color: rgba(20, 22, 28, 0.96); border-left: 1px solid #2a2e39; }
QLabel { color: #cfd3dc; font-family: "Consolas", monospace; font-size: 11px; }
QLabel#header { color: #ffffff; font-weight: bold; font-size: 12px; padding-top: 6px; }
QCheckBox { color: #cfd3dc; font-family: "Consolas", monospace; font-size: 11px; }
QComboBox { background:#11131a; color:#fff; border:1px solid #2a2e39; padding:2px; }
/* Fix #1: the popup list rendered white-on-white — style the view + items. */
QComboBox QAbstractItemView {
  background:#11131a; color:#ffffff;
  selection-background-color:#3498db; selection-color:#ffffff;
  border:1px solid #2a2e39; outline:0;
}
QComboBox QAbstractItemView::item { color:#ffffff; padding:3px 6px; }
/* Scan Start Time anchor — keep text/popup legible on the dark panel. */
QDateTimeEdit { background:#11131a; color:#ffffff; border:1px solid #2a2e39; padding:2px; }
QDateTimeEdit::drop-down { border:0; }
QDateTimeEdit QAbstractItemView { background:#11131a; color:#ffffff; selection-background-color:#3498db; }
QCalendarWidget QWidget { alternate-background-color:#1a1d26; color:#ffffff; }
QCalendarWidget QAbstractItemView { background:#11131a; color:#ffffff; selection-background-color:#3498db; }
QCalendarWidget QToolButton { color:#ffffff; background:#23262f; }
QPushButton#section {
  background:#23262f; color:#fff; border:none; text-align:left;
  padding:5px 8px; font-family:Consolas; font-size:11px; font-weight:bold;
}
QPushButton#section:hover { background:#2d313c; }
"""

_LAYERS = [
    ("order_blocks", "Order Blocks (OBs)", True),
    ("footprints", "Footprint Bubbles", False),
    ("imbalances", "Delta Imbalances (Delta FP)", False),
    ("icebergs", "Institutional Icebergs", False),
    ("stats", "3D Statistical Overlays (Stats)", True),
    ("liquidations", "Liquidation Marks (Liqs)", True),
    ("sessions", "Session Markers", True),
    ("velocity_tiers", "Velocity Tier Rankings", False),
]

# Mode 10 (bucket canvas) overlay toggles (A4). DISTINCT ``m10_`` keys — never the
# shared time-chart keys above — so greying a Phase-3 overlay here cannot grey its
# working time-chart twin, and Mode 10's toggles carry zero time-chart dependency.
# Tuple: (key, label, default_on, enabled). Disabled rows are Phase-3 placeholders:
# shown so the full control panel is visible, but non-clickable until their logic lands.
_M10_LAYERS = [
    ("m10_poc", "POC Dot", True, True),
    ("m10_footprint", "Footprint Ladder", False, True),   # default OFF — heavy overlay, opt-in
    ("m10_obs", "Order Blocks", True, True),              # default ON — OB zones show on a fresh terminal
    ("m10_dead_obs", "Dead OBs", True, True),             # default ON — mitigated OBs as faded lifespan boxes
    ("m10_liq", "Liquidation Marks", False, True),        # default OFF. In-session toggles persist as
    ("m10_stats", "Stats Box", True, True),               # checkbox state; only a restart resets to these.
    ("m10_statedebug", "State Debug (calib)", False, True),  # top-3 states + winner factors in the box
    ("m10_icebergs", "Absorption", True, True),             # whale-defense bands (calc_absorption, default ON)
    ("m10_dom", "Depth / DOM Walls", True, True),           # live order-book walls on the bucket canvas (Phase A)
    ("m10_imbalance", "Imbalance Gaps (Phase 3)", False, False),
]

# Order-flow scanner — the authoritative 10-mode bucket architecture (time chart removed, Phase B).
# The combo displays the human label but emits the KEY (via currentData).
SCANNER_MODES = [
    "bucket_canvas",   # Mode 10 — the only candle surface + default on open (A5)
    "open_pos",
    "close_pos",
    "exhaustion",
    "kinetic",
    "volume",
    "vpin",
    "bucket_open_pos",
    "bucket_close_pos",
    "effort_result",
]
SCANNER_LABELS = {
    "open_pos": "Cumulative Open Positions",
    "close_pos": "Cumulative Close Positions",
    "exhaustion": "Bull & Bear Exhaustion",
    "kinetic": "Kinetic Strength & Forecast",
    "volume": "Buyer vs Seller Volume",
    "vpin": "VPIN Flow Toxicity",
    "bucket_open_pos": "Micro-Bucket Open Intents",
    "bucket_close_pos": "Micro-Bucket Close Intents",
    "effort_result": "Effort vs Result",
    "bucket_canvas": "Bucket Candlestick Canvas",
}


class _WheelSlider(QtWidgets.QSlider):
    """Slider that steps a fixed amount per wheel notch (patch §15)."""

    def __init__(self, orientation, step: int):
        super().__init__(orientation)
        self._wheel_step = step

    def wheelEvent(self, ev) -> None:
        delta = ev.angleDelta().y()
        if delta == 0:
            return
        steps = 1 if delta > 0 else -1
        self.setValue(self.value() + steps * self._wheel_step)
        ev.accept()


class CollapsibleSection(QtWidgets.QWidget):
    """Accordion: a header button toggling a body layout's visibility (patch §14)."""

    def __init__(self, title: str, expanded: bool = True):
        super().__init__()
        self._title = title
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(2)
        self.btn = QtWidgets.QPushButton()
        self.btn.setObjectName("section")
        self.btn.setCheckable(True); self.btn.setChecked(expanded)
        self.btn.clicked.connect(self._toggle)
        self.body = QtWidgets.QWidget()
        self.body_lay = QtWidgets.QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(6, 2, 0, 4); self.body_lay.setSpacing(3)
        outer.addWidget(self.btn); outer.addWidget(self.body)
        self.body.setVisible(expanded)
        self._render_label()

    def _render_label(self) -> None:
        self.btn.setText(("▼ " if self.btn.isChecked() else "▶ ") + self._title)

    def _toggle(self) -> None:
        self.body.setVisible(self.btn.isChecked())
        self._render_label()

    def addWidget(self, w) -> None:
        self.body_lay.addWidget(w)

    def addLayout(self, lay) -> None:
        self.body_lay.addLayout(lay)


class HamburgerButton(QtWidgets.QPushButton):
    def __init__(self, parent: QtWidgets.QWidget):
        super().__init__("☰", parent)
        self.setFixedSize(32, 32)
        self.setStyleSheet(_BTN_QSS)
        self.setCursor(QtCore.Qt.PointingHandCursor)


class FloatingOverlayMenu(QtWidgets.QFrame):
    tfChanged = QtCore.Signal(str)
    obTfsChanged = QtCore.Signal(list)
    multiplierChanged = QtCore.Signal(float)
    chartFilterChanged = QtCore.Signal(int)
    layerToggled = QtCore.Signal(str, bool)
    subWidgetToggled = QtCore.Signal(str, bool)
    scannerChanged = QtCore.Signal(str)
    scan_time_changed = QtCore.Signal()   # user moved the scanner "Zero Point"

    PANEL_WIDTH = 240

    def __init__(self, parent: QtWidgets.QWidget):
        super().__init__(parent)
        self.setObjectName("overlay")
        self.setStyleSheet(_PANEL_QSS)
        self.setFixedWidth(self.PANEL_WIDTH)
        self.hide()
        self.layer_checks: dict[str, QtWidgets.QCheckBox] = {}
        self._build()

    # ------------------------------------------------------------------
    def _header(self, text: str) -> QtWidgets.QLabel:
        lbl = QtWidgets.QLabel(text); lbl.setObjectName("header")
        return lbl

    def _build(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 40, 12, 12); root.setSpacing(6)

        # --- timeframe (spec §7.2.1) ---
        root.addWidget(self._header("Timeframe"))
        self.tf_combo = QtWidgets.QComboBox()
        self.tf_combo.addItems(config.TIMEFRAMES)
        self.tf_combo.setCurrentText(config.DEFAULT_TF)
        self.tf_combo.currentTextChanged.connect(self.tfChanged.emit)
        root.addWidget(self.tf_combo)

        # --- order-flow scanner mode (patch §12) ---
        root.addWidget(self._header("Scanner Mode"))
        self.scanner_combo = QtWidgets.QComboBox()
        for key in SCANNER_MODES:
            self.scanner_combo.addItem(SCANNER_LABELS[key], key)   # label shown, key = userData
        self.scanner_combo.currentIndexChanged.connect(
            lambda _i: self.scannerChanged.emit(self.scanner_combo.currentData()))
        root.addWidget(self.scanner_combo)

        # --- scanner "Zero Point" anchor (Phase 1) ---
        root.addWidget(self._header("Scan Start Time"))
        self.scan_time_edit = QtWidgets.QDateTimeEdit()
        self.scan_time_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.scan_time_edit.setCalendarPopup(True)
        # default the anchor to exactly 2 hours before the host clock
        self.scan_time_edit.setDateTime(QtCore.QDateTime.currentDateTime().addSecs(-7200))
        self.scan_time_edit.dateTimeChanged.connect(lambda _dt: self.scan_time_changed.emit())
        root.addWidget(self.scan_time_edit)

        # --- multi-TF OB overlay checklist (spec §7.2.2) ---
        root.addWidget(self._header("OB Overlay Timeframes"))
        self.ob_tf_checks: dict[str, QtWidgets.QCheckBox] = {}
        row = QtWidgets.QHBoxLayout()
        for tf in config.TIMEFRAMES:
            cb = QtWidgets.QCheckBox(tf)
            cb.toggled.connect(self._emit_ob_tfs)
            self.ob_tf_checks[tf] = cb
            row.addWidget(cb)
        root.addLayout(row)

        # --- min multiplier filter (spec §7.2.3) ---
        self.mult_label = QtWidgets.QLabel("Min Multiplier: x0.0")
        root.addWidget(self.mult_label)
        self.mult_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.mult_slider.setRange(0, int(config.MULT_FILTER_MAX / config.MULT_FILTER_STEP))
        self.mult_slider.valueChanged.connect(self._emit_multiplier)
        root.addWidget(self.mult_slider)

        # --- depth-map liquidity threshold (spec §7.3.3 / §8.2) ---
        self.chart_label = QtWidgets.QLabel("Depth Wall Min: 1000")
        root.addWidget(self.chart_label)
        self.chart_slider = _WheelSlider(QtCore.Qt.Horizontal, config.CHART_FILTER_STEP)
        self.chart_slider.setRange(config.CHART_FILTER_MIN, config.CHART_FILTER_MAX)
        self.chart_slider.setSingleStep(config.CHART_FILTER_STEP)
        self.chart_slider.setPageStep(config.CHART_FILTER_STEP)
        self.chart_slider.setValue(1000)
        self.chart_slider.valueChanged.connect(self._emit_chart_filter)
        root.addWidget(self.chart_slider)

        # --- sub-widgets accordion (patch §14) ---
        self.sub_section = CollapsibleSection("Sub-Widgets", expanded=False)
        # Alerts moved to a dedicated floating 🔔 button (fix #8) — not here.
        for key, label in [("drawing", "Vector Drawing Toolbar"),
                           ("cob", "Order Book DOM Ladder"),
                           ("audio", "Audio Feed")]:
            cb = QtWidgets.QCheckBox(label)
            cb.toggled.connect(lambda on, k=key: self.subWidgetToggled.emit(k, on))
            self.sub_section.addWidget(cb)
        root.addWidget(self.sub_section)

        # --- technical layers accordion (patch §14) ---
        self.layer_section = CollapsibleSection("Technical Layers", expanded=True)
        for key, label, default in _LAYERS:
            cb = QtWidgets.QCheckBox(label)
            cb.setChecked(default)
            cb.toggled.connect(lambda on, k=key: self.layerToggled.emit(k, on))
            self.layer_checks[key] = cb
            self.layer_section.addWidget(cb)
        root.addWidget(self.layer_section)

        # --- Mode 10 overlay toggles accordion (A4) — same layer_state framework,
        # distinct m10_ keys. setChecked runs BEFORE connect (matching the loop above)
        # so the build-time toggled signal never reaches the not-yet-wired window slot.
        self.m10_section = CollapsibleSection("Mode 10 Overlays", expanded=True)
        for key, label, default, enabled in _M10_LAYERS:
            cb = QtWidgets.QCheckBox(label)
            cb.setChecked(default)
            cb.setEnabled(enabled)   # Phase-3 placeholders: visible but non-clickable
            cb.toggled.connect(lambda on, k=key: self.layerToggled.emit(k, on))
            self.layer_checks[key] = cb
            self.m10_section.addWidget(cb)
        root.addWidget(self.m10_section)

        root.addStretch(1)

    # ------------------------------------------------------------------
    def _emit_ob_tfs(self) -> None:
        self.obTfsChanged.emit([tf for tf, cb in self.ob_tf_checks.items() if cb.isChecked()])

    def _emit_multiplier(self, raw: int) -> None:
        val = raw * config.MULT_FILTER_STEP
        self.mult_label.setText(f"Min Multiplier: x{val:.1f}")
        self.multiplierChanged.emit(val)

    def _emit_chart_filter(self, val: int) -> None:
        self.chart_label.setText(f"Depth Wall Min: {val}")
        self.chartFilterChanged.emit(val)

    # ------------------------------------------------------------------
    def layer_state(self, key: str) -> bool:
        cb = self.layer_checks.get(key)
        return cb.isChecked() if cb else False

    def scan_start_unix(self) -> int:
        """The scanner 'Zero Point' as Unix epoch seconds (host-local interpreted).

        ``toSecsSinceEpoch`` accounts for the widget's local time spec, so it lines
        up directly with the buckets' UTC-epoch ``start_time`` values.
        """
        return int(self.scan_time_edit.dateTime().toSecsSinceEpoch())

    def toggle_panel(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show(); self.raise_()

    def leaveEvent(self, event) -> None:
        # Don't retract while a dropdown popup is open — the popup steals the
        # cursor and would otherwise instantly close the whole menu (patch §4).
        for combo in (self.tf_combo, self.scanner_combo):
            if combo.view().isVisible():
                return
        self.hide()
        super().leaveEvent(event)
