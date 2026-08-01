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
from .date_picker import DateTimeField


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

def _fmt_tf_secs(secs: float) -> str:
    """Compact 'effective timeframe' label for the Keltner-scale slider: seconds -> '5m' / '1h' / '1h30m' / '4h'."""
    m = secs / 60.0
    if m < 60:
        return ("%dm" % round(m)) if abs(m - round(m)) < 0.05 else ("%.1fm" % m)
    h = m / 60.0
    if abs(h - round(h)) < 0.03:
        return "%dh" % round(h)
    return "%dh%02dm" % (int(h), int(round((h - int(h)) * 60)))

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
# Tuple everywhere below: (key, label, default_on, enabled). Disabled rows are Phase-3 placeholders.
# All four groups share the SAME ``m10_`` key namespace + layer_state() + layer_checks dict; they are split
# only for the menu grouping (persistence iterates layer_checks, so a key's SECTION is transparent to save/load).

# "Mode 10 Overlays" — the render-layer overlays that aren't candles / indicators / strategies.
_M10_LAYERS = [
    ("m10_footprint", "Footprint Ladder", False, True),   # default OFF — footprint NUMBERS ('f'); heavy overlay, opt-in
    ("m10_bubbles", "Candle Bubbles", False, True),       # default OFF — per-level volume BUBBLES ('b' in Mode 10)
    ("m10_icebergs", "Absorption", False, True),          # whale-defense bands (calc_absorption); default OFF, 'o' toggles
    ("m10_dom", "Depth / DOM Walls", True, True),         # live order-book walls on the bucket canvas (Phase A)
    ("m10_imbalance", "Imbalance Gaps (Phase 3)", False, False),
]

# "Indicator" — structure / zones / separators.
_M10_INDICATORS = [
    ("m10_engulf1m", "Absorption Candle indicator", False, True),   # ALL tf: absorption-tiered losanges (cyan/magenta engulf |A|>=2, blue/orange same-side pair, green/red engulf |A|>=1)
    ("m10_sr", "Support & Resistance", False, True),      # neon-blue support / neon-red resistance (pivot fractals)
    ("m10_swinglvn", "Price & CVD Swings", False, True),   # ALL tf: ZigZag swing lines + swing absorb-A + retracement verdict; LVN zones sub-toggle
    ("m10_obs", "Order Blocks", False, True),             # default OFF — toggle with Order Blocks + Iceberg via 'o'
    ("m10_structure", "Market Structure — scalp ZigZag", False, True),   # fine ZigZag (ZIGZAG_PCT, app/structure.py)
    ("m10_structure_swing", "Market Structure — swing ZigZag", False, True),   # coarse ZigZag (+ its sensitivity slider)
    ("m10_choch", "Change of Character (CHoCH)", False, True),   # dashed break-lines on the scalp ZigZag
    ("m10_4hzone", "4h Buy/Sell Zones (wicks)", False, True),   # last completed 4h bucket buyer/seller wick bands
    ("m10_4hsep", "4h Bucket Separators", True, True),          # dashed vline at each completed 4h bucket's start
    ("m10_prevday_vp", "Prev. Day VP", False, True),            # per-previous-UTC-day Volume Profile (style = 'Volume Profile Mode' dropdown)
    ("m10_breakout5m", "5m Breakout", False, True),             # 5m ONLY: green/red 'Br' badges on S/R-breakout (mitigation) candles
]

# "Candles" — per-candle marks on the canvas.
_M10_CANDLES = [
    ("m10_poc", "POC Dot", False, True),                  # default OFF (operator preference)
    ("m10_liq", "Liquidation Marks", False, True),        # default OFF; in-session toggles persist
    ("m10_stats", "Stats Box", False, True),              # default OFF ('s' toggles)
]

# "Strategies" — the three ENGULF S/R signal overlays (own draw path, each self-gated / fail-safe).
_M10_STRATEGIES = [
    ("m10_mmx_sound", "\U0001F514", False, True),                       # bell icon — audible confirmation when enabled
    ("m10_engulfsr", "1h Engulf S/R Reversal (L / S triangles)", False, True),  # 1h: engulf at VA/S/R zone; fwd candidate
    ("m10_momentum", "15m Engulfing S/R (L / S losanges)", False, True),  # 15m: last-mit engulf; gold/blue tiers, tier-dependent skew
    ("m10_engulf5m", "5m Absorption S/R", False, True),  # 5m: continuation bias only, all triangles; engulf green/red/gold + absorb2 blue/orange
]

# Order-flow scanner — the authoritative 10-mode bucket architecture (time chart removed, Phase B).
# The combo displays the human label but emits the KEY (via currentData).
SCANNER_MODES = [
    "bucket_canvas",   # Mode 10 — the only candle surface + default on open (A5)
    "depth_heatmap",   # Phase 2b — Bookmap-style resting-liquidity heatmap (own canvas, scanner-gated); 2nd in the list
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
    "depth_heatmap": "Heatmap",
}

# Candle render modes (index = _candle_mode; also cycled by 'W').
CANDLE_MODE_LABELS = ["Normal candles", "Whisker bars", "Footprint", "Delta", "Force", "Delta-Force"]
# Volume-profile render modes (index = _vp_mode; drives the Mode-10 selection VP + the 4h 'V' overlay).
VP_MODE_LABELS = ["Basic", "Force", "Split Basic", "Split Basic Delta", "Split Force", "Split Force Delta",
                  "Basic Bulls", "Basic Bears"]


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
    replayToggled = QtCore.Signal(bool)   # Replay Mode on/off (default OFF; chart replays from the Start Date)
    swingSensitivityChanged = QtCore.Signal(float)   # swing-ZigZag threshold slider, in PERCENT
    keltnerScaleChanged = QtCore.Signal(float)   # 1m-KC smooth-approx effective-TF scale (1.0 = native 1m)
    candleModeChanged = QtCore.Signal(int)   # candle render mode 0..5 (also cycled by 'W')
    vpModeChanged = QtCore.Signal(int)       # volume-profile render mode 0..7 (selection VP + 4h 'V')
    helpRequested = QtCore.Signal()       # the top-right '?' — show the keyboard-shortcuts cheatsheet

    PANEL_WIDTH = 308

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

        # top-right '?' — floats above the scroll; opens the keyboard-shortcuts cheatsheet
        self.help_btn = QtWidgets.QPushButton("?", self)
        self.help_btn.setObjectName("helpbtn")
        self.help_btn.setFixedSize(22, 22)
        self.help_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.help_btn.setToolTip("Keyboard shortcuts")
        self.help_btn.setStyleSheet(
            "QPushButton#helpbtn { background:#2a2e39; color:#8fd6ff; border:1px solid #3a4150;"
            " border-radius:11px; font-weight:bold; font-family:Consolas; font-size:13px; }"
            "QPushButton#helpbtn:hover { background:#3a4150; color:#bfe8ff; }")
        self.help_btn.move(self.PANEL_WIDTH - 30, 8)
        self.help_btn.clicked.connect(self.helpRequested.emit)
        self.help_btn.raise_()

        root = QtWidgets.QVBoxLayout(content)
        # right margin is generous (clears the ~9px vertical scrollbar + leaves visible padding at the panel edge);
        # PANEL_WIDTH is wide enough that the longest labels/combos ("Market Structure — swing ZigZag") fit inside it.
        root.setContentsMargins(14, 40, 26, 12); root.setSpacing(6)

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
        self.tf_combo.setCurrentIndex(0)   # 1m = the 1× base scale (set_tf() overrides on a restored session)
        self.tf_combo.setToolTip(
            "Volume-bucket scale. N× is the structural multiple of the 1× (1-minute) base; the "
            "~volume is the live target per bucket. (Bucket scales, not time candles.)")
        self.tf_combo.currentIndexChanged.connect(
            lambda _i: self.tfChanged.emit(self.tf_combo.currentData()))

        def _set_tf(tf: str) -> None:
            """Point the selector at `tf` WITHOUT emitting tfChanged — used to sync the combo to a restored
            session's timeframe. Emitting here would re-enter _change_tf during construction."""
            for _i in range(self.tf_combo.count()):
                if self.tf_combo.itemData(_i) == tf:
                    _b = self.tf_combo.blockSignals(True)
                    self.tf_combo.setCurrentIndex(_i)
                    self.tf_combo.blockSignals(_b)
                    return
        self.set_tf = _set_tf
        root.addWidget(self.tf_combo)

        # --- candle render mode (mirrors the 'W' key cycle; either changes the other) ---
        root.addWidget(self._header("Candle Mode"))
        self.candle_combo = QtWidgets.QComboBox()
        for i, lbl in enumerate(CANDLE_MODE_LABELS):
            self.candle_combo.addItem(lbl, i)
        self.candle_combo.setToolTip("How each bucket candle is drawn. Also cycled with the 'W' key.")
        self.candle_combo.currentIndexChanged.connect(
            lambda _i: self.candleModeChanged.emit(int(self.candle_combo.currentData())))
        root.addWidget(self.candle_combo)

        # --- volume-profile render mode (drives BOTH the Mode-10 selection VP and the 4h 'V' overlay) ---
        root.addWidget(self._header("Volume Profile Mode"))
        self.vp_combo = QtWidgets.QComboBox()
        for i, lbl in enumerate(VP_MODE_LABELS):
            self.vp_combo.addItem(lbl, i)
        self.vp_combo.setToolTip("How volume profiles are drawn — applies to the Mode-10 selection VP and the 4h 'V' overlay.")
        self.vp_combo.currentIndexChanged.connect(
            lambda _i: self.vpModeChanged.emit(int(self.vp_combo.currentData())))
        root.addWidget(self.vp_combo)

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
        # Flutter/Material-style date+time picker (drop-in: same dateTime()/setDateTime()/dateTimeChanged interface).
        self.scan_time_edit = DateTimeField()
        # default the anchor to exactly 24 hours before the host clock
        self.scan_time_edit.setDateTime(QtCore.QDateTime.currentDateTime().addSecs(-86400))
        self.scan_time_edit.dateTimeChanged.connect(lambda _dt: self.scan_time_changed.emit())
        root.addWidget(self.scan_time_edit)

        # --- Replay Mode toggle (default OFF). ON => the chart replays FROM the Start Date, causal; Right arrow
        #     steps one candle instead of moving the selection. ---
        self.replay_btn = QtWidgets.QPushButton("▶  Replay Mode  ·  OFF")
        self.replay_btn.setCheckable(True); self.replay_btn.setChecked(False)
        self.replay_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.replay_btn.setStyleSheet(
            "QPushButton{background:#11131a; color:#8891a3; border:1px solid #2a2e39; border-radius:5px;"
            " padding:7px 10px; text-align:left; font-family:Consolas; font-size:11px; font-weight:bold;}"
            "QPushButton:hover{border-color:#3b82f6;}"
            "QPushButton:checked{background:#16324f; color:#7ec2ff; border-color:#3b82f6;}")
        self.replay_btn.toggled.connect(self._on_replay_btn)
        root.addWidget(self.replay_btn)
        self.replay_hint = QtWidgets.QLabel("→ Right arrow steps one candle")
        self.replay_hint.setStyleSheet("color:#6b7280; font-family:Consolas; font-size:10px; padding:0 2px;")
        self.replay_hint.setVisible(False)
        root.addWidget(self.replay_hint)

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

        # --- Keltner smooth-approx scale (UNDER Depth Wall): stretch the 1m KC + POC baseline toward a higher-TF
        #     channel (EMA/ATR period ×scale, band ×sqrt(scale)). Label shows the ≈ effective timeframe. ---
        self.kc_label = QtWidgets.QLabel()
        root.addWidget(self.kc_label)
        self.kc_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.kc_slider.setRange(10, int(round(config.KELTNER_SCALE_MAX * 10)))   # ×1.0 .. ×MAX in 0.1 steps
        self.kc_slider.setSingleStep(1); self.kc_slider.setPageStep(10)
        self.kc_slider.setValue(int(round(config.KELTNER_SCALE_DEFAULT * 10)))
        self.kc_slider.valueChanged.connect(self._on_kc_slider)
        root.addWidget(self.kc_slider)
        self.tf_combo.currentIndexChanged.connect(lambda _i: self._render_kc_lbl())   # base tf changed -> refresh ≈eff-TF
        self._render_kc_lbl()

        # --- sub-widgets accordion (patch §14) ---
        self.sub_section = CollapsibleSection("Sub-Widgets", expanded=False)
        # Alerts moved to a dedicated floating 🔔 button (fix #8) — not here.
        for key, label in [("drawing", "Vector Drawing Toolbar"),
                           ("cob", "Order Book DOM Ladder"),
                           ("fp_pane", "Live Footprint Pane"),   # right-docked forming-candle footprint (Mode 10)
                           ("cvd_pane", "CVD Pane (1D anchored)"),   # cumulative volume delta, resets each UTC midnight
                           ("audio", "OB/Iceberg Alert")]:
            cb = QtWidgets.QCheckBox(label)
            # First-launch DEFAULT (before connect): Vector Drawing ON, OB/Iceberg Alert OFF.
            # Persistence (terminal saved toggles) overrides this on every later launch.
            cb.setChecked(key == "drawing")
            cb.toggled.connect(lambda on, k=key: self.subWidgetToggled.emit(k, on))
            self.sub_checks[key] = cb
            self.sub_section.addWidget(cb)
        root.addWidget(self.sub_section)

        # --- m10_ toggle accordions (A4) — same layer_state framework across four grouped sections. setChecked
        # runs BEFORE connect so the build-time toggled signal never reaches the not-yet-wired window slot. Every
        # checkbox lands in self.layer_checks regardless of section, so persistence + layer_state stay uniform.
        self.m10_section = self._build_layer_section("Mode 10 Overlays", _M10_LAYERS, expanded=False)
        self.indicator_section = self._build_layer_section("Indicator", _M10_INDICATORS, expanded=False)
        self.candles_section = self._build_layer_section("Candles", _M10_CANDLES, expanded=False)
        self.strat_section = self._build_layer_section("Strategies", _M10_STRATEGIES, expanded=False)
        # Strategies dropdown FIRST, then Indicator, then the base overlays / candles.
        for _sec in (self.strat_section, self.indicator_section, self.m10_section, self.candles_section):
            root.addWidget(_sec)

        root.addStretch(1)

    def _build_layer_section(self, title: str, items, expanded: bool = True) -> "CollapsibleSection":
        """Build one m10_ toggle accordion from `items`. Checkboxes go into self.layer_checks (section-agnostic),
        the swing-sensitivity slider is placed under its own toggle in THIS section, and the bell gets a tooltip."""
        sec = CollapsibleSection(title, expanded=expanded)
        for key, label, default, enabled in items:
            cb = QtWidgets.QCheckBox(label)
            cb.setChecked(default)
            cb.setEnabled(enabled)                       # Phase-3 placeholders: visible but non-clickable
            if key == "m10_mmx_sound":
                cb.setToolTip("Entry sound — beeps on a new L/S print (1h) for the ENABLED signal strategies "
                              "(MMXSKEW / DA2 / Skew Divergence / Flow Flip). Buy = high tone, sell = low. "
                              "Plays a confirmation chime whenever it is enabled.")
            cb.toggled.connect(lambda on, k=key: self.layerToggled.emit(k, on))
            self.layer_checks[key] = cb
            sec.addWidget(cb)
            if key == "m10_structure_swing":
                self._build_swing_slider(sec)            # sensitivity slider directly under its toggle
            if key == "m10_swinglvn":
                self._build_svl_subtoggles(sec)          # RCLI sub-toggles: LVA zones / swing lines
            if key == "m10_stats":
                self._build_stats_substats(sec)          # per-stat on/off for the Mode-10 stats box
        return sec

    def _build_stats_substats(self, section) -> None:
        """Per-stat toggles under the Stats Box (Candles) — each row of the Mode-10 hover/forming stats readout on/off
        independently. Registered as layer_ keys so _hover_context reads them via layer_state (and they persist)."""
        # One unified set gating BOTH the candle stats box (_hover_context) and the footprint pane (_fp_top_html).
        # Shared concepts share a key (toggle once → hides in both); footprint-only rows (Δ↑/Δ↓, ½dom, 1m Eff) get
        # their own keys here. Nothing from either box is left without a toggle.
        for key, label in (("st_ohlc", "· OHLC"), ("st_poc", "· Elapsed/POC"), ("st_volume", "· Volume"),
                           ("st_buysell", "· Buy/Sell"), ("st_delta", "· Delta"), ("st_deltaud", "· Δ↑ / Δ↓"),
                           ("st_daccel", "· Δ-accel"), ("st_absorb", "· Absorb R"), ("st_ease", "· Ease"),
                           ("st_halfdom", "· ½dom"), ("st_rhalves", "· R h1/h2"), ("st_dp", "· ΔP"),
                           ("st_oi", "· OI Δ"), ("st_costtick", "· Cost/tick"), ("st_vel", "· Buy/Sell-vel"),
                           ("st_tape", "· Tape B/S"), ("st_ker", "· KER"), ("st_movmag", "· Mov.Magnitude"),
                           ("st_skew", "· Skew"), ("st_mmxskew", "· MM×Skew"), ("st_openpos", "· Open pos"),
                           ("st_closepos", "· Close pos"), ("st_er", "· E/R (bER/sER)"), ("st_er30", "· 30b E/R"),
                           ("st_absorpvol", "· Absorption"), ("st_effagg", "· Eff-agg"), ("st_1meff", "· 1m Eff"),
                           ("st_velread", "· VEL"), ("st_vel30", "· 30b VEL"), ("st_tau", "· τ-ratio")):
            cb = QtWidgets.QCheckBox(label)
            cb.setChecked(True)
            cb.setStyleSheet("QCheckBox{ padding-left:26px; color:#aeb4c0; font-size:10px; }")   # indented, sub-level
            cb.toggled.connect(lambda on, k=key: self.layerToggled.emit(k, on))
            self.layer_checks[key] = cb
            section.addWidget(cb)

    def _build_svl_subtoggles(self, section) -> None:
        """RCLI (Recent Swing LVA) sub-toggles under its master toggle — the LVA zones and the swing lines,
        each on/off independently. Both are m10_ keys, so they persist + read via layer_state like every other
        layer; the master m10_swinglvn still gates the whole indicator."""
        for key, label, default in (("m10_svl_zones", "· VA Zones", True),
                                     ("m10_svl_lines", "· Swing lines", True),
                                     ("m10_svl_bias", "· Bias badge", True),
                                     ("m10_svl_lock", "· Lock swing stats", False),
                                     ("m10_svl_proj", "· Projections", False)):
            cb = QtWidgets.QCheckBox(label)
            cb.setChecked(default)
            cb.setStyleSheet("QCheckBox{ padding-left:18px; color:#aeb4c0; font-size:10px; }")   # indented, sub-level
            cb.toggled.connect(lambda on, k=key: self.layerToggled.emit(k, on))
            self.layer_checks[key] = cb
            section.addWidget(cb)

    # ------------------------------------------------------------------
    def _build_swing_slider(self, section) -> None:
        """Compact slider under the 'swing ZigZag' toggle (placed in `section`): live-adjust the swing sensitivity
        (percent retrace that confirms a leg). Lower = more/smaller swings, higher = only major turns."""
        from . import structure
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w); lay.setContentsMargins(26, 1, 8, 5); lay.setSpacing(2)
        self.swing_lbl = QtWidgets.QLabel()
        self.swing_lbl.setStyleSheet("color:#c8cdd6; background:transparent; font-family:Consolas; font-size:10px;")
        lay.addWidget(self.swing_lbl)
        self.swing_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.swing_slider.setRange(20, 250)      # 0.20% .. 2.50% in 0.01% steps
        self.swing_slider.setValue(int(round(structure.ZIGZAG_SWING_PCT * 100)))   # default from the constant (0.60%)
        self.swing_slider.setFixedHeight(16)
        self.swing_slider.valueChanged.connect(self._on_swing_slider)
        lay.addWidget(self.swing_slider)
        section.addWidget(w)
        self._render_swing_lbl()

    def _on_swing_slider(self, _v: int) -> None:
        self._render_swing_lbl()
        self.swingSensitivityChanged.emit(self.swing_pct())

    def _render_swing_lbl(self) -> None:
        self.swing_lbl.setText("Swing sensitivity · %.2f%%" % self.swing_pct())

    def swing_pct(self) -> float:
        """Current swing-ZigZag threshold in PERCENT."""
        return self.swing_slider.value() / 100.0

    def set_swing_pct(self, pct: float) -> None:
        """Restore a persisted swing sensitivity WITHOUT emitting (clamped to the slider range)."""
        self.swing_slider.blockSignals(True)
        self.swing_slider.setValue(int(round(max(0.20, min(2.50, float(pct))) * 100)))
        self.swing_slider.blockSignals(False)
        self._render_swing_lbl()

    # ------------------------------------------------------------------
    def _on_kc_slider(self, _v: int) -> None:
        self._render_kc_lbl()
        self.keltnerScaleChanged.emit(self.kc_scale())

    def _render_kc_lbl(self) -> None:
        s = self.kc_scale()
        base = config.TF_SECONDS.get(self.tf_combo.currentData() or config.DEFAULT_TF, 60)
        self.kc_label.setText("Keltner ~ %s  ·  %.1fx" % (_fmt_tf_secs(base * s), s))

    def kc_scale(self) -> float:
        """Current Keltner smooth-approx effective-TF scale (1.0 = native)."""
        return self.kc_slider.value() / 10.0

    def set_kc_scale(self, s: float) -> None:
        """Restore a persisted Keltner scale WITHOUT emitting (clamped to the slider range)."""
        self.kc_slider.blockSignals(True)
        self.kc_slider.setValue(int(round(max(1.0, min(config.KELTNER_SCALE_MAX, float(s))) * 10)))
        self.kc_slider.blockSignals(False)
        self._render_kc_lbl()

    def set_candle_mode(self, m: int) -> None:
        """Sync the Candle-Mode dropdown to `m` WITHOUT emitting (used when 'W' cycles it or on restore)."""
        self.candle_combo.blockSignals(True)
        self.candle_combo.setCurrentIndex(int(m) % self.candle_combo.count())
        self.candle_combo.blockSignals(False)

    def set_vp_mode(self, m: int) -> None:
        """Sync the Volume-Profile-Mode dropdown to `m` WITHOUT emitting (on restore)."""
        self.vp_combo.blockSignals(True)
        self.vp_combo.setCurrentIndex(int(m) % self.vp_combo.count())
        self.vp_combo.blockSignals(False)

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

    def _on_replay_btn(self, on: bool) -> None:
        self.replay_btn.setText("▶  Replay Mode  ·  ON" if on else "▶  Replay Mode  ·  OFF")
        self.replay_hint.setVisible(on)
        self.replayToggled.emit(on)

    def is_replay(self) -> bool:
        return self.replay_btn.isChecked()

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
