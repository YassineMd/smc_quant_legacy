"""Tier-3 consolidated control hub (spec §7).

Every interactive control lives inside the sliding top-right ``[☰]`` panel so the
white canvas stays nude (spec §5.1.3). Controls emit Qt signals the window wires
to render-layer toggles and the socket client.

Excluded by Purge Protocol (§10.1): no EMA / Keltner toggles, no Market Replay,
no Quant Sniper panel, no "Copy AI Data" export.
"""

from __future__ import annotations

import math

from PySide6 import QtCore, QtWidgets

from . import config


def _fmt_vol_1sig(v: float) -> str:
    """Volume at 1 significant figure with a K/M suffix — honest precision for the drifting
    median anchor (e.g. 8030 -> '~8K', 1_927_291 -> '~2M'). '~--' before data arrives."""
    if not v or v <= 0:
        return "~--"
    mag = math.floor(math.log10(v))
    r = round(v / 10 ** mag) * 10 ** mag
    if r >= 1e6:
        return f"~{r / 1e6:g}M"
    if r >= 1e3:
        return f"~{r / 1e3:g}K"
    return f"~{int(r)}"


def scale_label(tf: str, vol: float) -> str:
    """Bucket-scale display label 'N× (~vol)': N× is the exact structural multiple
    (tf_seconds / 60), ~vol the live 1-sig-fig magnitude the sizing produces."""
    nx = config.TF_SECONDS.get(tf, 60) // 60
    return f"{nx}× ({_fmt_vol_1sig(vol)})"

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
/* Thin scrollbar for the panel when a short window makes the controls overflow. */
QScrollBar:vertical { background:transparent; width:9px; margin:2px 0; }
QScrollBar::handle:vertical { background:#3a3f4b; border-radius:4px; min-height:24px; }
QScrollBar::handle:vertical:hover { background:#4a5160; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }
"""

# (The time-chart "Technical Layers" toggles (_LAYERS) were removed with the time chart —
# Phase B / hamburger cleanup. Mode 10's overlay toggles live in _M10_LAYERS below.)

# Mode 10 (bucket canvas) overlay toggles (A4) — the only layer toggles now (time chart removed).
# ``m10_`` keys, read via layer_state() by the Mode-10 draw path.
# Tuple: (key, label, default_on, enabled). Disabled rows are Phase-3 placeholders:
# shown so the full control panel is visible, but non-clickable until their logic lands.
_M10_LAYERS = [
    ("m10_poc", "POC Dot", True, True),
    ("m10_footprint", "Footprint Ladder", False, True),   # default OFF — heavy overlay, opt-in
    ("m10_obs", "Order Blocks", True, True),              # default ON — shows alive (solid) + dead (faded) together
    ("m10_liq", "Liquidation Marks", False, True),        # default OFF. In-session toggles persist as
    ("m10_stats", "Stats Box", True, True),               # checkbox state; only a restart resets to these.
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
        self.sub_checks: dict[str, QtWidgets.QCheckBox] = {}
        self.toggle_button: QtWidgets.QWidget | None = None   # set by the window: the [☰] that opens us
        self._scale_labels: dict[str, str] = {}   # last-rendered "N× (~vol)" per tf (flicker-free)
        self._build()

    # ------------------------------------------------------------------
    def _header(self, text: str) -> QtWidgets.QLabel:
        lbl = QtWidgets.QLabel(text); lbl.setObjectName("header")
        return lbl

    def _build(self) -> None:
        # The panel is sized to the full window height (window resizeEvent); on a short window a
        # plain layout compresses every control into an unclickable jumble. Host them in a scroll
        # area instead so they keep their natural size and the panel scrolls vertically.
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        scroll = QtWidgets.QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet(                       # keep the dark panel bg showing through
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
            "QScrollArea { border: none; }")
        outer.addWidget(scroll)
        content = QtWidgets.QWidget()
        scroll.setWidget(content)

        root = QtWidgets.QVBoxLayout(content)
        root.setContentsMargins(12, 40, 12, 12); root.setSpacing(6)

        # --- bucket scale (formerly "Timeframe") — selects which order-flow window sizes the
        # volume buckets. NO number here: the buckets drawn on the chart ARE the honest scale
        # display (a menu number would restate the chart, abstract the distribution, or risk
        # showing poisoned persisted state). The tf is the stable, honest INPUT (the flow window). ---
        root.addWidget(self._header("Bucket Scale"))
        self.tf_combo = QtWidgets.QComboBox()
        # Honest scale ladder: DISPLAY "N× (~vol)" (the volume multiple the sizing produces), but
        # keep the tf string as the item KEY (userData) and emit currentData() — so the daemon
        # still receives "1m"/"5m"/... unchanged. Mirrors the scanner_combo pattern below.
        for tf in config.TIMEFRAMES:
            self.tf_combo.addItem(scale_label(tf, 0.0), tf)
        self.tf_combo.setCurrentIndex(0)   # 1m = the 1× base scale
        self.tf_combo.setToolTip(
            "Volume-bucket scale. N× is the structural multiple of the 1× (1-minute) base; the "
            "~volume is the live target per bucket. (Bucket scales, not time candles.)")
        self.tf_combo.currentIndexChanged.connect(
            lambda _i: self.tfChanged.emit(self.tf_combo.currentData()))
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
        # default the anchor to exactly 24 hours before the host clock
        self.scan_time_edit.setDateTime(QtCore.QDateTime.currentDateTime().addSecs(-86400))
        self.scan_time_edit.dateTimeChanged.connect(lambda _dt: self.scan_time_changed.emit())
        root.addWidget(self.scan_time_edit)

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
            cb.setChecked(key == "audio")    # Audio Feed ON by default (set before connect: no build-time emit)
            cb.toggled.connect(lambda on, k=key: self.subWidgetToggled.emit(k, on))
            self.sub_checks[key] = cb
            self.sub_section.addWidget(cb)
        root.addWidget(self.sub_section)

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
    def _emit_multiplier(self, raw: int) -> None:
        val = raw * config.MULT_FILTER_STEP
        self.mult_label.setText(f"Min Multiplier: x{val:.1f}")
        self.multiplierChanged.emit(val)

    def _emit_chart_filter(self, val: int) -> None:
        self.chart_label.setText(f"Depth Wall Min: {val}")
        self.chartFilterChanged.emit(val)

    def update_scale_volumes(self, vols: dict) -> None:
        """Refresh the Bucket Scale ~volumes from the live sizing. Flicker-free: re-render an
        item only when its rounded 'N× (~vol)' string actually changes (the 1-sig-fig median
        shifts a handful of times a day). Display-only — never touches the item key (userData)."""
        for i in range(self.tf_combo.count()):
            tf = self.tf_combo.itemData(i)
            new = scale_label(tf, vols.get(tf, 0.0))
            if self._scale_labels.get(tf) != new:
                self._scale_labels[tf] = new
                self.tf_combo.setItemText(i, new)

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

    def showEvent(self, event) -> None:
        # Close only on an outside CLICK (not on cursor-leave): watch app-wide mouse presses while
        # open; the filter is removed again on hide.
        super().showEvent(event)
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def hideEvent(self, event) -> None:
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        super().hideEvent(event)

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QtCore.QEvent.Type.MouseButtonPress and self.isVisible():
            # A combo/calendar dropdown is open: the press dismisses that popup, not the menu.
            if QtWidgets.QApplication.activePopupWidget() is not None:
                return False
            gp = event.globalPosition().toPoint()
            inside_menu = self.rect().contains(self.mapFromGlobal(gp))
            btn = self.toggle_button
            on_button = btn is not None and btn.rect().contains(btn.mapFromGlobal(gp))
            if not inside_menu and not on_button:
                self.hide()                          # click landed outside -> close
        return False
