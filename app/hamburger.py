"""Tier-3 consolidated control hub (spec §7).

Every interactive control lives inside the sliding top-right ``[☰]`` panel so the
white canvas stays nude (spec §5.1.3). Controls emit Qt signals the window wires
to render-layer toggles and the socket client.

Excluded by Purge Protocol (§10.1): no Market Replay, no Quant Sniper panel, no "Copy AI Data" export.
(The Keltner channel / POC baseline / VWAP are on/off Indicator toggles — the old EMA/Keltner *scale slider*
stays purged; only simple visibility toggles were re-added.)
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
    ("m10_crazywall", "Wall Absorption", False, True),   # ALL tf: opposite-side volume bubble absorbed+rejected at a wall; Crazy(✪ outlier) + Big(★ non-crazy) sub-tiers, green(support)/red(resistance)
    ("m10_engulf1m", "Absorption Candle indicator", False, True),   # ALL tf: absorption-tiered losanges (cyan/magenta engulf |A|>=2, blue/orange same-side pair, green/red engulf |A|>=1)
    ("m10_sr", "Support & Resistance", False, True),      # neon-blue support / neon-red resistance (pivot fractals)
    ("m10_keltner", "Keltner Channel", True, True),        # EMA(close)±ATR band (light gray); was always-on, now toggleable
    ("m10_poc_baseline", "POC Baseline", True, True),      # gray dashed POC-center EMA line; was always-on, now toggleable
    ("m10_daysep", "Day Separators (dashed)", True, True),   # default ON: dashed gray-blue vline at every UTC-midnight day boundary (all tf)
    ("m10_vwap", "VWAP", False, True),                     # daily-anchored (UTC-midnight reset) volume-weighted avg price — BLUE line
    ("m10_swinglvn", "Price & CVD Swings", False, True),   # ALL tf: ZigZag swing lines + swing absorb-A + retracement verdict; LVN zones sub-toggle
    ("m10_reversal", "Reversal Point (R/G ▲▼)", False, True),   # ALL tf: early/predictive swing reversal (candle-3 hammer + choppy approach + capitulation); green ▲ swing-low / red ▼ swing-high; strong = bigger
    ("m10_absorblvl", "Order-Flow Walls (red/green)", False, True),   # EYEBALL-ONLY: absorption + aggression walls — resistance (red) at highs where buyers were absorbed / sellers dumped; support (green) at lows where sellers were absorbed / buyers lifted; clustered = brighter. Barely a signal — ~+3pp over a random line, not tradeable. Includes the bottom-right Wall Regime HUD (TREND/RANGE + bias, coincident) — sub-toggle 'Regime table'
    ("m10_nowickwall", "No-Wick Bar Wall (red/green)", False, True),   # EYEBALL-ONLY: same wall design as Order-Flow Walls but band = the ENTIRE candle (low..high), NO radar. Bullish bar with no lower wick -> support (green); bearish bar with no upper wick -> resistance (red). Fades a wall once a body closes beyond it. app/nowick_wall_detect
    ("m10_reward", "Reward / effort (table)", False, True),   # BOTTOM-LEFT HUD: buy vs sell share of reward-per-effort (reward = price moved that side's way, effort = its taker vol) for yesterday / today / last 30 candles / current candle. DESCRIPTIVE + COINCIDENT
    ("m10_daycompass", "★ Day Compass (bias table · 30m-based)", False, True),   # BOTTOM-LEFT HUD: price vs YESTERDAY's 70% value area (ABOVE/INSIDE/BELOW) + TODAY's wall ledger (S/R created - mitigated) -> one needle: UP BIAS / DOWN BIAS / ROTATION / MIXED. BOTH axes always read the 30m BUCKET series regardless of chart tf/source (study-identical); price = the chart's last close. DESCRIPTIVE + COINCIDENT — the RadarRun study REJECTED alignment (inverts on daemon OOS). app/day_compass
    ("m10_obs", "Order Blocks", False, True),             # default OFF — toggle with Order Blocks + Iceberg via 'o'
    ("m10_structure", "Market Structure — scalp ZigZag", False, True),   # fine ZigZag (ZIGZAG_PCT, app/structure.py)
    ("m10_structure_swing", "Market Structure — swing ZigZag", False, True),   # coarse ZigZag (+ its sensitivity slider)
    ("m10_prevday_vp", "Prev. Day VP", False, True),            # per-previous-UTC-day Volume Profile (style = 'Volume Profile Mode' dropdown)
    ("m10_session", "Session Filter", False, True),            # per-UTC-day Tokyo/London/New-York boxes: range + avg (VWAP) + high/low
    ("m10_erange", "Expected Range", False, True),             # per-session dashed range envelope from YESTERDAY's same-session range (NY/Tokyo/London/Whole Day sub-toggles)
    ("m10_nyanchor", "★ NY Anchor (far-side hold · 15:00→21:00 UTC)", False, True),   # from 15:00Z ONE amber line at the NY-session extreme FARTHER from price — holds to the close ~67-73% (+12-17pp over the shuffle null, recent eras; study/session_side_fix_15m). 18:30Z→ both extremes (range typically complete). DESCRIPTIVE level persistence, NOT an entry signal; side can flip if price crosses the session midpoint
    ("m10_breakout5m", "5m Breakout", False, True),             # 5m ONLY: green/red 'Br' badges on S/R-breakout (mitigation) candles
]

# "Candles" — per-candle marks on the canvas.
_M10_CANDLES = [
    ("m10_poc", "POC Dot", False, True),                  # default OFF (operator preference)
    ("m10_candle_va", "VP Lines", False, True),           # per-candle buy-POC green / sell-POC red / LVN purple stub, right of each candle (Ctrl+A)
    ("m10_imb", "Abnormal Volume", True, True),           # blue(buy)/orange(sell) abnormal-volume level lines inside each candle; default ON (was always-on)
    ("m10_liq", "Liquidation Marks", False, True),        # default OFF; in-session toggles persist
    ("m10_stats", "Stats Box", False, True),              # default OFF ('s' toggles)
]

# "Strategies" — the three ENGULF S/R signal overlays (own draw path, each self-gated / fail-safe).
_M10_STRATEGIES = [
    ("m10_mmx_sound", "\U0001F514", False, True),                       # bell icon — audible confirmation when enabled
    ("m10_engulfsr", "1h Engulf S/R Reversal (L / S triangles)", False, True),  # 1h: engulf at VA/S/R zone; fwd candidate
    ("m10_momentum", "15m Engulfing Wall (L / S losanges)", False, True),  # 15m: engulf rejection off a wall's radar (bounce)
    ("m10_engulf5m", "5m Absorption Wall", False, True),  # 5m: absorption/engulf rejection off a wall's radar (bounce); engulf green/red/gold + absorb2 blue/orange
    ("m10_easy1h", "1h Easy 0.5% (L / S triangles)", False, True),  # 1h: absorption+vw+swing scale-out; neon green/purple; fwd candidate
    ("m10_radarrun", "★ Radar Runner (L / S triangles · 1m·5m·15m·1h)", False, True),  # resisted-wall radar BREAKOUT + tiered TP1/2/3; the one recon-validated edge (net+ both yrs, causal-checked). ⚠ 1m sub-fee/eyeball-only
    ("m10_radarwick", "★ Radar Diamond (cyan ♦ · click→scale-out · 5m·15m·30m·1h)", False, True),  # TRADEABLE SD+big-wick breakouts the RR skips (body beyond radar, wick retests); click → TP1/TP2 scale-out bracket; validated additive to RR
    ("m10_wallsurge", "Wall Surge (▲▼ · surge + absorb @ 30m·1h wall · 1m·5m clock)", False, True),
    ("m10_longwick", "Long Wick (♦ · wick rejection @ wall · all tf)", False, True),
    ("m10_longwick_combo", "LW · Failed Push (gold ♦ · 2-bar + break · no wall · all tf)", False, True),  # gold ♦ ABOVE a BEARISH bar + long-UPPER-wick BEARISH bar (v2 wick geometry) CLOSING BELOW the prev bar's low — buyers pushed, completely failed, bar still broke down; mirrored bullish pair closing ABOVE the prev high -> gold ♦ BELOW. NOT bound to walls. Descriptive/eyeball (both variants honest-tested: no mechanical edge)
    ("m10_longwick_reclaim", "LW · Wick Reclaim (cyan/magenta ♦ · 2-bar · no wall · all tf)", False, True),  # LONG cyan ♦ BELOW: two consecutive BULLISH bars — bar 1 upper wick >= 1/3 of its range, bar 2 lower wick >= 1/3 of its range AND closing ABOVE bar 1's high (the rejected area reclaimed); SHORT magenta ♦ ABOVE = mirror. NOT bound to walls. Descriptive/eyeball  # red ♦ ABOVE a BEARISH bar at a SELL wall whose upper wick > body AND >= 2x the lower wick (the lower wick may be any size, even > body, as long as it's doubled); green ♦ BELOW the bullish mirror at a BUY wall. Walls = the chart's own current-tf Order-Flow Walls. Descriptive/eyeball — the v1 geometry's mechanical bracket FAILED the honest test on 15m/30m/1h; v2 untested  # 1m/5m CLOCK only, pane-STRONG |delta| AND volume (each trailing-50 pct ≥ P80) at a 30m OR 1h BUCKET wall CORE. TWO classes: SURGE = aggressor wins at its own wall (kept≥80% · buying on support ▲ / selling on resistance ▼); ABSORB = aggressor loses at the opposing wall (strong selling on support + GREEN close ▲ / strong buying on resistance + RED close ▼). Descriptive/eyeball, no tested edge
    ("m10_nyrangebreak", "NY Range-break (brB / brS · 1h·15m)", False, True),  # 1h/15m: 2-5pm range box + first close-break label; SHORT side alpha in-sample (P=0.003)
    ("m10_9amfade", "★ 09:00-UTC Fade · 5m clock (= 10:00 local, UTC+1)", False, True),  # 5m clock: FADE the 09:00 UTC bar (bull→short/bear→long), enter 09:05 UTC, SL 0.8%, TP 0.5× Tokyo range. ⚠ 09:00 UTC = 10:00 on a UTC+1 (Morocco) chart — the badge on the 10:00-local bar IS the validated 09:00-UTC edge (08:00 UTC / local-9am was tested = null). Robustness-cleared recon candidate (~61% prop pass), NOT live-confirmed. click → entry/SL/TP
    ("m10_wallstrat", "Wall Strategy (L / S triangles)", False, True),  # 5m: wall visit w/ Big|Crazy absorption + favourable tally + Easy Gold/Pure Aggression entry; NOT backtested
    ("m10_kcovershoot", "KC Overshoot 2nd-Entry (L / S triangles)", False, True),  # ALL tf: close beyond a Keltner extreme -> pullback (re-enter band) -> 1st entry (skip) -> 2nd entry = continuation signal; overshoot diamond + 1st-entry ring + 2nd-entry triangle. UNTESTED
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
# "VP Zones" (8) is line-only: the Price&CVD-Swings VA Zones — buy-POC green / sell-POC red / LVN purple, plus
# VAH/VAL white solid, no histogram.
VP_MODE_LABELS = ["Basic", "Force", "Split Basic", "Split Basic Delta", "Split Force", "Split Force Delta",
                  "Basic Bulls", "Basic Bears", "VP Zones", "Basic Delta"]
# Dropdown DISPLAY order (mode VALUES, not positions). "Basic Delta" (9) is a right-only net-delta histogram, so it is
# shown with the other right-only delta modes (Bulls/Bears) instead of last. Appending 9 to the label list keeps every
# existing index — and the persisted _vp_mode — stable; the combo carries the VALUE as userData, so order != value.
VP_MODE_ORDER = [0, 1, 2, 3, 4, 5, 9, 6, 7, 8]


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
    wallFloorChanged = QtCore.Signal(float)          # Order-Flow Walls min-strength draw floor (0.05..0.90)
    rewardStrengthChanged = QtCore.Signal(float)     # Reward-switch zones min-strength filter (0..70)
    bubbleVolChanged = QtCore.Signal(float)          # Heatmap trade-bubble min volume filter (SOL; 0 = show all)
    keltnerScaleChanged = QtCore.Signal(float)   # 1m-KC smooth-approx effective-TF scale (1.0 = native 1m)
    candleModeChanged = QtCore.Signal(int)   # candle render mode 0..5 (also cycled by 'W')
    vpModeChanged = QtCore.Signal(int)       # volume-profile render mode 0..8 (selection VP + 4h 'V' + prev-day VP)
    chartSourceChanged = QtCore.Signal(str)  # chart data source: "bucket" (volume buckets) | "time" (clock candles)
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

        # --- CHART SOURCE: Volume Buckets <-> Time Candles. Same window, same footprint/bubbles/stats — only the
        # data source (clock vs volume) and the x-axis (clock time vs bucket index) differ. The Bucket Scale combo
        # above doubles as the tf selector for BOTH: on 5m it's the 5x volume scale in Bucket mode, and 5-minute
        # clock candles in Time mode. (Time candles: exact OHLC/footprint/bubbles/VPIN; engine-only overlays are
        # blank because the daemon computes them for volume buckets only.) ---
        root.addWidget(self._header("Chart Source"))
        self.source_combo = QtWidgets.QComboBox()
        self.source_combo.addItem("Volume Buckets", "bucket")
        self.source_combo.addItem("Time Candles", "time")
        self.source_combo.setCurrentIndex(0)
        self.source_combo.setToolTip(
            "Volume Buckets = the native order-flow chart (default). Time Candles = clock 1m/5m/15m/30m/1h/4h with the "
            "SAME footprint/bubbles/stats on a TIME x-axis. The Bucket Scale above picks the timeframe for both.")
        self.source_combo.currentIndexChanged.connect(
            lambda _i: self.chartSourceChanged.emit(self.source_combo.currentData()))

        def _set_chart_source(src: str) -> None:
            """Sync the selector to `src` WITHOUT emitting (session restore); mirrors set_tf."""
            for _i in range(self.source_combo.count()):
                if self.source_combo.itemData(_i) == src:
                    _b = self.source_combo.blockSignals(True)
                    self.source_combo.setCurrentIndex(_i)
                    self.source_combo.blockSignals(_b)
                    return
        self.set_chart_source = _set_chart_source
        root.addWidget(self.source_combo)

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
        for m in VP_MODE_ORDER:
            self.vp_combo.addItem(VP_MODE_LABELS[m], m)          # userData = mode VALUE (display order != value)
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
        self._build_heatmap_section(root)                # 'Heatmap' dropdown (contrast + bubble vol) — Heatmap-mode only

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

        # (Keltner smooth-approx scale SLIDER removed — the Keltner channel is now a plain on/off toggle in the
        #  Indicator group. The scale is pinned to the persisted/default value; set_kc_scale() still stores a
        #  restored value, and kc_scale() feeds the terminal's KC fold exactly as before.)
        self._kc_scale_val = float(config.KELTNER_SCALE_DEFAULT)

        # --- sub-widgets accordion (patch §14) ---
        self.sub_section = CollapsibleSection("Sub-Widgets", expanded=False)
        # Alerts moved to a dedicated floating 🔔 button (fix #8) — not here.
        for key, label in [("drawing", "Vector Drawing Toolbar"),
                           ("cob", "Order Book DOM Ladder"),
                           ("fp_pane", "Live Footprint Pane"),   # right-docked forming-candle footprint (Mode 10)
                           ("cvd_pane", "CVD Pane (1D anchored)"),   # cumulative volume delta, resets each UTC midnight
                           ("vol_pane", "Volume Pane (Basic/Delta/Buy/Sell)"),  # per-bar histogram; top-right dropdown: basic|delta|buy|sell|buy+sell
                           ("vpin_pane", "VPIN Pane"),               # lower VPIN toxicity heatmap + flow line (default OFF — heavy paint)
                           ("ema20", "20 EMA Line"),                 # 20-period EMA of closes on the chart series (amber line; live-extended on the forming bar)
                           ("ema50", "50 EMA Line"),                 # 50-period EMA (blue line; same engine)
                           ("ema100", "100 EMA Line"),               # 100-period EMA (purple line; same engine)
                           ("ema_ext", "High/Low Lines + readout"),  # per toggled EMA: dotted lines at the p-bar window's high/low + SIGNED dist-to-EMA readout (hi / lo / net delta) at the live edge
                           ("ema_stack", "Stack Flip Lines"),        # dashed vline at the 20/50 EMA cross, delta-validated: green (up-cross) only if BOTH 20/50 HL deltas POSITIVE, red (down-cross) only if both NEGATIVE (100 EMA omitted)
                           ("market_pos", "Market Position"),        # Buy/Sell buttons at chart bottom -> default sim market entry
                           ("audio", "OB/Iceberg Alert")]:
            _ema_grp = key in ("ema20", "ema50", "ema100", "ema_ext", "ema_stack")
            if key == "ema20":                       # group header — the EMA entries render as indented sub-toggles
                _hdr = QtWidgets.QLabel("EMA")
                _hdr.setStyleSheet("color:#8b93a3; font-size:10px; padding-left:2px; padding-top:3px;")
                self.sub_section.addWidget(_hdr)
            cb = QtWidgets.QCheckBox(("· " + label) if _ema_grp else label)
            if _ema_grp:
                cb.setStyleSheet("QCheckBox{ padding-left:18px; color:#aeb4c0; font-size:10px; }")
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
            if key == "m10_absorblvl":
                self._build_wallfloor_slider(sec)        # strength draw floor directly under the Walls toggle
                self._build_wall_regime_subtoggle(sec)   # bottom-right Wall Regime table on/off
                self._build_wall_match_subtoggle(sec)    # keep only walls near a Reward-Switch zone
                self._build_wall_4h_subtoggle(sec)       # overlay the 4h absorption walls (neon violet/green)
                self._build_wall_1h_subtoggle(sec)       # overlay the 1h absorption walls (orange/blue), lower tfs
                self._build_wall_30m_subtoggle(sec)      # overlay the 30m absorption walls (pink/teal), 1m/5m/15m only
                self._build_wall_hidecur_subtoggle(sec)  # hide current-tf walls -> higher-timeframe-only view
                self._build_wall_sess_subtoggle(sec)     # only walls BORN in the current session (Tokyo/London/NY)
            if key == "m10_crazywall":
                self._build_wallabs_subtoggles(sec)      # Wall Absorption sub-tiers: Crazy (✪) / Big (★)
            if key == "m10_sr":
                self._build_sr_subtoggles(sec)           # S/R sub-toggle: Area (bands) vs lines-only
            if key == "m10_swinglvn":
                self._build_svl_subtoggles(sec)          # RCLI sub-toggles: LVA zones / swing lines
            if key == "m10_session":
                self._build_session_subtoggles(sec)      # Session sub-toggle: VP lines (VAH/VAL/POC/LVN) on/off
            if key == "m10_vwap":
                self._build_vwap_subtoggles(sec)         # VWAP sub-toggles: ±1σ / ±2σ / ±3σ std-dev bands
            if key == "m10_erange":
                self._build_erange_subtoggles(sec)       # Expected Range sub-toggles: NY / Tokyo / London / Whole Day
            if key == "m10_reward":
                self._build_reward_subtoggle(sec)        # Reward/effort sub-toggle: horizontal zones where the rewarded side flips
                self._build_reward_strength_slider(sec)  # + a strength filter for those zones
            if key == "m10_radarrun":
                self._build_radarrun_subtoggle(sec)      # Radar Runner sub-toggle: high-conviction order-flow filter (gold ring)
                self._build_radarrun_absorb_subtoggle(sec)  # + 'absorbed only' filter (A>=0, drop the easy fizzles)
                self._build_radarrun_hld_subtoggle(sec)  # + 'Filter EMA HL delta' (long iff delta>0 / short iff delta<0)
                self._build_radarrun_htf_subtoggles(sec)  # + 1h / 4h signals on lower tfs (colour-matched to the htf walls)
            if key == "m10_stats":
                self._build_stats_substats(sec)          # per-stat on/off for the Mode-10 stats box
        return sec

    def _build_radarrun_subtoggle(self, section) -> None:
        """Radar Runner sub-toggle: show ONLY 'high-conviction' breakouts — the breakout bar's STRENGTH is forceful
        (effort z >= 0.5) AND the recent reward/eff (last 50, aligned to the break) favours it. High-conviction
        breakouts always get a GOLD RING; this toggle HIDES the rest. ⚠ Underpowered tilt: it lifts win% on 1h but is
        flat/mixed on 5m/15m (higher n) -- a fewer-trades/higher-conviction VIEW, not a proven upgrade
        (study/wall_breakout_filtered.py). m10_radarrun_hc, default OFF."""
        cb = QtWidgets.QCheckBox("· High conviction only (order-flow)")
        cb.setChecked(False)                             # default OFF (the gold rings show either way)
        cb.setStyleSheet("QCheckBox{ padding-left:18px; color:#aeb4c0; font-size:10px; }")   # indented, sub-level
        cb.toggled.connect(lambda on, k="m10_radarrun_hc": self.layerToggled.emit(k, on))
        self.layer_checks["m10_radarrun_hc"] = cb
        section.addWidget(cb)

    def _build_radarrun_absorb_subtoggle(self, section) -> None:
        """Radar Runner sub-toggle: show ONLY breakouts whose bar was ABSORBED (Absorption R A >= 0) -- i.e. price
        fought its way out of the radar through opposing volume, NOT an easy unopposed drift. ⚠ Underpowered tilt:
        'avoid the very-easy breakout' is robust on 5m/15m, and 'absorbed is best' is clean on 5m (both yrs) but flat
        on 15m-2026 / noisy on 1h -- a fewer-trades VIEW, not a proven upgrade (study/wall_breakout_absorb*.py).
        m10_radarrun_abs, default OFF; composes with the high-conviction toggle (both apply)."""
        cb = QtWidgets.QCheckBox("· Absorbed only (A≥0, drop easy)")
        cb.setChecked(False)
        cb.setStyleSheet("QCheckBox{ padding-left:18px; color:#aeb4c0; font-size:10px; }")   # indented, sub-level
        cb.toggled.connect(lambda on, k="m10_radarrun_abs": self.layerToggled.emit(k, on))
        self.layer_checks["m10_radarrun_abs"] = cb
        section.addWidget(cb)

    def _build_radarrun_hld_subtoggle(self, section) -> None:
        """Radar Runner sub-toggle: show only badges that agree with the EMA-HL DELTA (the ema_ext readout's
        signed net: window high/low of the last 20 bars, each measured vertically to EMA20 AT its own bar) —
        LONG kept iff delta > 0, SHORT kept iff delta < 0, computed at the badge's bar. ⚠ HONEST TEST FAILED
        HARD: daemon OOS INVERTS (WITH −0.160%/trade RR1:1 vs AGAINST +0.007%; study/radarrun_hldelta.py) —
        an eyeball VIEW only, NOT an upgrade. A badge whose delta can't be computed (fewer than 20 bars) is
        KEPT. m10_radarrun_hld, default OFF; composes with the other filters (all apply)."""
        cb = QtWidgets.QCheckBox("· Filter EMA HL delta (Δ-aligned)")
        cb.setChecked(False)
        cb.setStyleSheet("QCheckBox{ padding-left:18px; color:#aeb4c0; font-size:10px; }")   # indented, sub-level
        cb.toggled.connect(lambda on, k="m10_radarrun_hld": self.layerToggled.emit(k, on))
        self.layer_checks["m10_radarrun_hld"] = cb
        section.addWidget(cb)

    def _build_radarrun_htf_subtoggles(self, section) -> None:
        """Radar Runner sub-toggles: overlay the HIGHER-timeframe Radar Runner SIGNALS on the current lower-tf chart —
        1h signals (on 1m/5m/15m/30m) and 4h signals (on 1m/5m/15m/30m/1h) — each coloured to MATCH that htf's walls
        (4h neon violet/green, 1h orange/blue), so an htf breakout is visible while you trade a lower tf. Each an m10_
        key (persists + reads via layer_state); default OFF. Badge only (no click bracket)."""
        for key, label in (("m10_radarrun_30m", "· 30m Signals (on lower tfs)"),
                           ("m10_radarrun_1h", "· 1h Signals (on lower tfs)"),
                           ("m10_radarrun_4h", "· 4h Signals (on lower tfs)")):
            cb = QtWidgets.QCheckBox(label)
            cb.setChecked(False)                             # default OFF
            cb.setStyleSheet("QCheckBox{ padding-left:18px; color:#aeb4c0; font-size:10px; }")   # indented, sub-level
            cb.toggled.connect(lambda on, k=key: self.layerToggled.emit(k, on))
            self.layer_checks[key] = cb
            section.addWidget(cb)

    def _build_reward_subtoggle(self, section) -> None:
        """Reward/effort sub-toggle: draw a horizontal ZONE on the chart where the rolling reward-per-effort side FLIPS
        — a GREEN band (support) where buyers take over (sellers stopped being rewarded, buyers started), a RED band
        (resistance) for the mirror, each extending forward like an S/R zone. Rides the master m10_reward; an m10_ key
        (persists + reads via layer_state). Default OFF (extra to the table)."""
        cb = QtWidgets.QCheckBox("· Reward-switch zones")
        cb.setChecked(False)                             # default OFF
        cb.setStyleSheet("QCheckBox{ padding-left:18px; color:#aeb4c0; font-size:10px; }")   # indented, sub-level
        cb.toggled.connect(lambda on, k="m10_reward_switch": self.layerToggled.emit(k, on))
        self.layer_checks["m10_reward_switch"] = cb
        section.addWidget(cb)

    def _build_reward_strength_slider(self, section) -> None:
        """Slider under the Reward-switch zones sub-toggle: hide zones whose STRENGTH (depth of the regime being
        reversed) is below the value. Higher = fewer (only the switches that overturned a strongly one-sided tape).
        Display-only filter, no re-detection. Persists via terminal_ui.json."""
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w); lay.setContentsMargins(26, 1, 8, 5); lay.setSpacing(2)
        self.rwstr_lbl = QtWidgets.QLabel()
        self.rwstr_lbl.setStyleSheet("color:#c8cdd6; background:transparent; font-family:Consolas; font-size:10px;")
        lay.addWidget(self.rwstr_lbl)
        self.rwstr_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.rwstr_slider.setRange(0, 70)                # 0 = show all .. 70 = only the strongest reversals
        self.rwstr_slider.setValue(0)
        self.rwstr_slider.setFixedHeight(16)
        self.rwstr_slider.valueChanged.connect(self._on_rwstr_slider)
        lay.addWidget(self.rwstr_slider)
        section.addWidget(w)
        self._render_rwstr_lbl()

    def _on_rwstr_slider(self, _v: int) -> None:
        self._render_rwstr_lbl()
        self.rewardStrengthChanged.emit(self.reward_strength())

    def _render_rwstr_lbl(self) -> None:
        self.rwstr_lbl.setText("Switch strength >= %d  (higher = fewer)" % int(self.reward_strength()))

    def reward_strength(self) -> float:
        return float(self.rwstr_slider.value())

    def set_reward_strength(self, v: float) -> None:
        self.rwstr_slider.blockSignals(True)
        self.rwstr_slider.setValue(int(round(max(0.0, min(70.0, float(v))))))
        self.rwstr_slider.blockSignals(False)
        self._render_rwstr_lbl()

    def _build_wall_match_subtoggle(self, section) -> None:
        """Toggle under Order-Flow Walls: keep only walls whose price zone overlaps a Reward-Switch zone (confluence
        filter, honours the switch strength slider). Rides m10_absorblvl; an m10_ key (persists + reads via
        layer_state). Default OFF (shows all walls)."""
        cb = QtWidgets.QCheckBox("· Match Reward/eff")
        cb.setChecked(False)                             # default OFF
        cb.setStyleSheet("QCheckBox{ padding-left:18px; color:#aeb4c0; font-size:10px; }")   # indented, sub-level
        cb.toggled.connect(lambda on, k="m10_wall_match": self.layerToggled.emit(k, on))
        self.layer_checks["m10_wall_match"] = cb
        section.addWidget(cb)

    def _build_wall_4h_subtoggle(self, section) -> None:
        """Toggle under Order-Flow Walls: overlay the higher-timeframe (4h) absorption walls on the current chart as
        NEON violet(resistance)/green(support) bands (bold core = wall ±band, faint = radar ±3·band) — so the HTF
        structure is visible at any tf. Rides m10_absorblvl; an m10_ key (persists + reads via layer_state). Default OFF."""
        cb = QtWidgets.QCheckBox("· 4h Walls (neon)")
        cb.setChecked(False)                             # default OFF
        cb.setStyleSheet("QCheckBox{ padding-left:18px; color:#aeb4c0; font-size:10px; }")   # indented, sub-level
        cb.toggled.connect(lambda on, k="m10_absorblvl_4h": self.layerToggled.emit(k, on))
        self.layer_checks["m10_absorblvl_4h"] = cb
        section.addWidget(cb)

    def _build_wall_1h_subtoggle(self, section) -> None:
        """Toggle under Order-Flow Walls: overlay the higher-timeframe (1h) absorption walls on the current chart as
        ORANGE(resistance)/BLUE(support) bands (bold core = wall ±band; radar ±3·band shows as dashed lines on hover) —
        so 1h structure is visible while trading a lower tf. Only draws on tfs BELOW 1h (redundant on 1h/4h). Rides
        m10_absorblvl; an m10_ key (persists + reads via layer_state). Default OFF."""
        cb = QtWidgets.QCheckBox("· 1h Walls (orange/blue)")
        cb.setChecked(False)                             # default OFF
        cb.setStyleSheet("QCheckBox{ padding-left:18px; color:#aeb4c0; font-size:10px; }")   # indented, sub-level
        cb.toggled.connect(lambda on, k="m10_absorblvl_1h": self.layerToggled.emit(k, on))
        self.layer_checks["m10_absorblvl_1h"] = cb
        section.addWidget(cb)

    def _build_wall_30m_subtoggle(self, section) -> None:
        """Toggle under Order-Flow Walls: overlay the higher-timeframe (30m) absorption walls on the current chart as
        PINK(resistance)/TEAL(support) bands (bold core = wall ±band; radar ±3·band dashed on hover) — 30m structure
        visible while trading 1m/5m/15m. Only draws on tfs BELOW 30m. Source is ALWAYS 30m VOLUME BUCKETS (recon replay /
        daemon worker / daemon archive), never clock candles — same guarantee as the 1h/4h overlays (user 2026-08-24).
        Rides m10_absorblvl; an m10_ key (persists + reads via layer_state). Default OFF."""
        cb = QtWidgets.QCheckBox("· 30m Walls (pink/teal)")
        cb.setChecked(False)                             # default OFF
        cb.setStyleSheet("QCheckBox{ padding-left:18px; color:#aeb4c0; font-size:10px; }")   # indented, sub-level
        cb.toggled.connect(lambda on, k="m10_absorblvl_30m": self.layerToggled.emit(k, on))
        self.layer_checks["m10_absorblvl_30m"] = cb
        section.addWidget(cb)

    def _build_wall_hidecur_subtoggle(self, section) -> None:
        """Toggle under Order-Flow Walls: HIDE the current-timeframe wall bands so only the higher-timeframe (1h/4h)
        wall overlays show — open a lower tf and see just the HTF structure. Rides m10_absorblvl; an m10_ key (persists +
        reads via layer_state). Default OFF (current-tf walls shown)."""
        cb = QtWidgets.QCheckBox("· Hide current-tf walls")
        cb.setChecked(False)                             # default OFF
        cb.setStyleSheet("QCheckBox{ padding-left:18px; color:#aeb4c0; font-size:10px; }")   # indented, sub-level
        cb.toggled.connect(lambda on, k="m10_absorblvl_hidecur": self.layerToggled.emit(k, on))
        self.layer_checks["m10_absorblvl_hidecur"] = cb
        section.addWidget(cb)

    def _build_wall_sess_subtoggle(self, section) -> None:
        """Toggle under Order-Flow Walls: show ONLY walls BORN in the CURRENT canonical session (Tokyo 00-08 /
        London 08-13 / New York 13-24 UTC — the m10_session windows; post-21:00 counts as extended NY). Applies to
        the current-tf bands AND the 30m/1h/4h HTF overlays. Rides m10_absorblvl; an m10_ key (persists + reads via
        layer_state). Default OFF (all walls shown)."""
        cb = QtWidgets.QCheckBox("· This session's walls only")
        cb.setChecked(False)                             # default OFF
        cb.setStyleSheet("QCheckBox{ padding-left:18px; color:#aeb4c0; font-size:10px; }")   # indented, sub-level
        cb.setToolTip("Show only walls created since the current session opened (Tokyo 00:00 / London 08:00 / "
                      "New York 13:00 UTC; after 21:00 counts as extended NY). Earlier walls are hidden, not deleted.")
        cb.toggled.connect(lambda on, k="m10_absorblvl_sess": self.layerToggled.emit(k, on))
        self.layer_checks["m10_absorblvl_sess"] = cb
        section.addWidget(cb)

    def _build_wall_regime_subtoggle(self, section) -> None:
        """Toggle for the bottom-right WALL REGIME table (TREND/RANGE + bias, coincident). Rides the Order-Flow
        Walls layer (m10_absorblvl); default ON so the table shows exactly as before until the user hides it."""
        cb = QtWidgets.QCheckBox("· Regime table")
        cb.setChecked(True)                              # default ON — preserves the current behaviour
        cb.setStyleSheet("QCheckBox{ padding-left:18px; color:#aeb4c0; font-size:10px; }")   # indented, sub-level
        cb.toggled.connect(lambda on, k="m10_wall_regime": self.layerToggled.emit(k, on))
        self.layer_checks["m10_wall_regime"] = cb
        section.addWidget(cb)

    def _build_wallabs_subtoggles(self, section) -> None:
        """Wall Absorption sub-tiers: Crazy (✪ = statistical-outlier bubble), Big (★ = big-but-not-crazy), Easy Gold
        (⛊/☗ = gold label on the tape/candle-divergence candle in a wall radar), and Pure Aggression (gold ▍ =
        label on a one-sided-aggression candle whose bubbles ALL agree with it + tape leans against it, same-side wall). Each an m10_ key
        (persists + reads via layer_state); the master m10_crazywall gates the whole indicator."""
        for key, label, default in (("m10_wallabs_crazy", "· Crazy (✪)", True), ("m10_wallabs_big", "· Big (★)", False),
                                    ("m10_wallabs_easygold", "· Easy Gold (⛊)", False),
                                    ("m10_wallabs_pureagg", "· Pure Aggression (▍)", False)):
            cb = QtWidgets.QCheckBox(label)
            cb.setChecked(default)
            cb.setStyleSheet("QCheckBox{ padding-left:18px; color:#aeb4c0; font-size:10px; }")   # indented, sub-level
            cb.toggled.connect(lambda on, k=key: self.layerToggled.emit(k, on))
            self.layer_checks[key] = cb
            section.addWidget(cb)

    def _build_erange_subtoggles(self, section) -> None:
        """Expected Range sub-toggles: one DASHED high/low range envelope per session (NY / Tokyo / London / Whole Day),
        each forecasting that session's range from yesterday's same-session range. Each an m10_ key (persists + reads
        via layer_state); the master m10_erange gates the whole indicator. NY default ON (preserves the old behaviour)."""
        for key, label, default in (("m10_erange_ny", "· NY Session", True),
                                    ("m10_erange_tokyo", "· Tokyo Session", False),
                                    ("m10_erange_london", "· London Session", False),
                                    ("m10_erange_wholeday", "· Whole Day", False)):
            cb = QtWidgets.QCheckBox(label)
            cb.setChecked(default)
            cb.setStyleSheet("QCheckBox{ padding-left:18px; color:#aeb4c0; font-size:10px; }")   # indented, sub-level
            cb.toggled.connect(lambda on, k=key: self.layerToggled.emit(k, on))
            self.layer_checks[key] = cb
            section.addWidget(cb)

    def _build_vwap_subtoggles(self, section) -> None:
        """VWAP σ-band sub-toggles: ±1σ / ±2σ / ±3σ volume-weighted std-dev channels around the VWAP. Each an m10_
        key (persists + reads via layer_state); the master m10_vwap gates the whole indicator (bands ride it)."""
        for key, label in (("m10_vwap_sd1", "· ±1σ band"), ("m10_vwap_sd2", "· ±2σ band"), ("m10_vwap_sd3", "· ±3σ band")):
            cb = QtWidgets.QCheckBox(label)
            cb.setChecked(False)                             # default OFF
            cb.setStyleSheet("QCheckBox{ padding-left:18px; color:#aeb4c0; font-size:10px; }")   # indented, sub-level
            cb.toggled.connect(lambda on, k=key: self.layerToggled.emit(k, on))
            self.layer_checks[key] = cb
            section.addWidget(cb)

    def _build_session_subtoggles(self, section) -> None:
        """Session Filter sub-toggle: 'VP lines' ON -> draw the per-session VAH/VAL + buy/sell-POC + LVN volume-profile
        lines; OFF -> just the faint session box + name label. An m10_ key (persists + reads via layer_state); the
        master m10_session still gates the whole indicator."""
        cb = QtWidgets.QCheckBox("· VP lines")
        cb.setChecked(True)                              # default ON -> the current VP-line look
        cb.setStyleSheet("QCheckBox{ padding-left:18px; color:#aeb4c0; font-size:10px; }")   # indented, sub-level
        cb.toggled.connect(lambda on, k="m10_sess_vp": self.layerToggled.emit(k, on))
        self.layer_checks["m10_sess_vp"] = cb
        section.addWidget(cb)

    def _build_stats_substats(self, section) -> None:
        """Per-stat toggles under the Stats Box (Candles) — each row of the Mode-10 hover/forming stats readout on/off
        independently. Registered as layer_ keys so _hover_context reads them via layer_state (and they persist)."""
        # One unified set gating BOTH the candle stats box (_hover_context) and the footprint pane (_fp_top_html).
        # Shared concepts share a key (toggle once → hides in both); footprint-only rows (Δ↑/Δ↓, ½dom, 1m Eff) get
        # their own keys here. Nothing from either box is left without a toggle.
        for key, label in (("st_ohlc", "· OHLC"), ("st_poc", "· Elapsed/POC"), ("st_volume", "· Volume"),
                           ("st_buysell", "· Buy/Sell"), ("st_delta", "· Delta"), ("st_deltaud", "· Δ↑ / Δ↓"),
                           ("st_daccel", "· Δ-accel"), ("st_absorb", "· Absorb R"), ("st_effres", "· Eff/Res (effort→result)"),
                           ("st_reward", "· Reward/eff"),
                           ("st_strength", "· Strength"), ("st_ease", "· Ease"),
                           ("st_halfdom", "· ½dom"), ("st_rhalves", "· R h1/h2"), ("st_dp", "· ΔP"),
                           ("st_oi", "· OI Δ"), ("st_costtick", "· Cost/tick"), ("st_vel", "· Buy/Sell-vel"),
                           ("st_tape", "· Tape B/S"), ("st_ker", "· KER"), ("st_movmag", "· Mov.Magnitude"),
                           ("st_skew", "· Skew"), ("st_mmxskew", "· MM×Skew"), ("st_effaggsp", "· eff-agg (spread)"),
                           ("st_openpos", "· Open pos"), ("st_closepos", "· Close pos"), ("st_er", "· E/R (bER/sER)"),
                           ("st_er30", "· 30b E/R"), ("st_absorpvol", "· Absorption"), ("st_effagg", "· Eff-agg VOL"),
                           ("st_1meff", "· 1m Eff"), ("st_velread", "· VEL"), ("st_vel30", "· 30b VEL"),
                           ("st_tau", "· τ-ratio")):
            cb = QtWidgets.QCheckBox(label)
            cb.setChecked(True)
            cb.setStyleSheet("QCheckBox{ padding-left:26px; color:#aeb4c0; font-size:10px; }")   # indented, sub-level
            cb.toggled.connect(lambda on, k=key: self.layerToggled.emit(k, on))
            self.layer_checks[key] = cb
            section.addWidget(cb)

    def _build_sr_subtoggles(self, section) -> None:
        """Support & Resistance sub-toggles: 'Area' ON -> ACTIVE levels draw as filled zones (bands); OFF -> every
        level (active + mitigated) draws as a plain LINE. '1h S/R' / '4h S/R' overlay that higher timeframe's
        pivot-fractal S/R levels on the current chart as DASHED neon lines tagged '1h'/'4h' at the live edge
        (active levels only; draws only on tfs BELOW the htf; htf source = volume buckets, same guarantee as the
        HTF walls). All m10_ keys, so they persist + read via layer_state; the master m10_sr still gates all."""
        for key, label, default in (("m10_sr_area", "· Area", True),
                                     ("m10_sr_showcur", "· Show current-tf S/R", True),
                                     ("m10_sr_1h", "· 1h S/R (dashed · lower tfs)", False),
                                     ("m10_sr_4h", "· 4h S/R (dash-dot · lower tfs)", False),
                                     ("m10_sr_match", "· Match (only levels at HTF S/R)", False)):
            cb = QtWidgets.QCheckBox(label)
            cb.setChecked(default)
            cb.setStyleSheet("QCheckBox{ padding-left:18px; color:#aeb4c0; font-size:10px; }")   # indented, sub-level
            cb.toggled.connect(lambda on, k=key: self.layerToggled.emit(k, on))
            self.layer_checks[key] = cb
            section.addWidget(cb)

    def _build_svl_subtoggles(self, section) -> None:
        """RCLI (Recent Swing LVA) sub-toggles under its master toggle — the LVA zones and the swing lines,
        each on/off independently. Both are m10_ keys, so they persist + read via layer_state like every other
        layer; the master m10_swinglvn still gates the whole indicator."""
        for key, label, default in (("m10_svl_zones", "· VA Zones", True),
                                     ("m10_svl_lines", "· Trend channel", True),
                                     ("m10_svl_zigzag", "· Swing lines", False),
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
    def _build_wallfloor_slider(self, section) -> None:
        """Slider under 'Order-Flow Walls': hide walls whose STRENGTH (ejection) is below the value. Pure display
        filter, no re-detection. Higher = fewer (only strong-ejection walls); lower = more. Persists via terminal_ui.json."""
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w); lay.setContentsMargins(26, 1, 8, 5); lay.setSpacing(2)
        self.wallfloor_lbl = QtWidgets.QLabel()
        self.wallfloor_lbl.setStyleSheet("color:#c8cdd6; background:transparent; font-family:Consolas; font-size:10px;")
        lay.addWidget(self.wallfloor_lbl)
        self.wallfloor_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.wallfloor_slider.setRange(5, 90)            # 0.05 .. 0.90 strength, in 0.01 steps
        self.wallfloor_slider.setValue(12)               # default 0.12
        self.wallfloor_slider.setFixedHeight(16)
        self.wallfloor_slider.valueChanged.connect(self._on_wallfloor_slider)
        lay.addWidget(self.wallfloor_slider)
        section.addWidget(w)
        self._render_wallfloor_lbl()

    def _on_wallfloor_slider(self, _v: int) -> None:
        self._render_wallfloor_lbl()
        self.wallFloorChanged.emit(self.wall_floor())

    def _render_wallfloor_lbl(self) -> None:
        self.wallfloor_lbl.setText("Wall strength >= %.2f  (higher = fewer)" % self.wall_floor())

    def wall_floor(self) -> float:
        return self.wallfloor_slider.value() / 100.0

    def set_wall_floor(self, v: float) -> None:
        self.wallfloor_slider.blockSignals(True)
        self.wallfloor_slider.setValue(int(round(max(0.05, min(0.90, float(v))) * 100)))
        self.wallfloor_slider.blockSignals(False)
        self._render_wallfloor_lbl()

    def _build_heatmap_section(self, root) -> None:
        """The 'Heatmap' dropdown: a CollapsibleSection holding the Liquidity-Contrast cutoff sliders (moved off the
        chart) + the trade-bubble volume filter. Hidden unless the Heatmap scanner mode is active — the terminal
        toggles `heatmap_sec` visibility on _hm_enter / _hm_exit."""
        from .stats_overlay import HeatmapContrastBar
        self.heatmap_sec = CollapsibleSection("Heatmap", expanded=True)
        self.hm_contrast = HeatmapContrastBar(self, config.HEATMAP_LO_PCT, config.HEATMAP_HI_PCT)
        self.heatmap_sec.addWidget(self.hm_contrast)     # Liquidity Contrast (lower/upper cutoff + Reset)
        self._build_bubblevol_slider(self.heatmap_sec)   # trade-bubble min-volume filter (SOL)
        root.addWidget(self.heatmap_sec)
        self.heatmap_sec.setVisible(False)               # shown only in Heatmap mode (driven by the terminal)

    def _build_bubblevol_slider(self, root) -> None:
        """Trade-bubble volume filter (inside the Heatmap dropdown): show only trade bubbles whose aggregated cell
        volume is >= the value (in SOL). Higher = fewer/bigger bubbles only; 0 = show all. Also lightens the render. Persists."""
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w); lay.setContentsMargins(2, 1, 8, 5); lay.setSpacing(2)
        self.bubblevol_lbl = QtWidgets.QLabel()
        self.bubblevol_lbl.setStyleSheet("color:#c8cdd6; background:transparent; font-family:Consolas; font-size:10px;")
        lay.addWidget(self.bubblevol_lbl)
        self.bubblevol_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.bubblevol_slider.setRange(0, 1000)          # 0 .. 1000 SOL aggregated cell volume, 1-SOL steps
        self.bubblevol_slider.setValue(0)                # default 0 = show all
        self.bubblevol_slider.setFixedHeight(16)
        self.bubblevol_slider.valueChanged.connect(self._on_bubblevol_slider)
        lay.addWidget(self.bubblevol_slider)
        root.addWidget(w)
        self._render_bubblevol_lbl()

    def _on_bubblevol_slider(self, _v: int) -> None:
        self._render_bubblevol_lbl()
        self.bubbleVolChanged.emit(self.bubble_vol())

    def _render_bubblevol_lbl(self) -> None:
        self.bubblevol_lbl.setText("Heatmap bubble vol >= %d SOL  (higher = fewer)" % int(self.bubble_vol()))

    def bubble_vol(self) -> float:
        return float(self.bubblevol_slider.value())

    def set_bubble_vol(self, v: float) -> None:
        self.bubblevol_slider.blockSignals(True)
        self.bubblevol_slider.setValue(int(round(max(0.0, min(1000.0, float(v))))))
        self.bubblevol_slider.blockSignals(False)
        self._render_bubblevol_lbl()

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
    def kc_scale(self) -> float:
        """Keltner smooth-approx effective-TF scale (1.0 = native). The live slider was removed; the value is
        pinned to the persisted/default scale and only changes via set_kc_scale() on session restore."""
        return self._kc_scale_val

    def set_kc_scale(self, s: float) -> None:
        """Restore a persisted Keltner scale (slider removed — just store it, clamped to the valid range)."""
        self._kc_scale_val = max(1.0, min(float(config.KELTNER_SCALE_MAX), float(s)))

    def set_candle_mode(self, m: int) -> None:
        """Sync the Candle-Mode dropdown to `m` WITHOUT emitting (used when 'W' cycles it or on restore)."""
        self.candle_combo.blockSignals(True)
        self.candle_combo.setCurrentIndex(int(m) % self.candle_combo.count())
        self.candle_combo.blockSignals(False)

    def set_vp_mode(self, m: int) -> None:
        """Sync the Volume-Profile-Mode dropdown to `m` WITHOUT emitting (on restore)."""
        self.vp_combo.blockSignals(True)
        _i = self.vp_combo.findData(int(m))                      # select by mode VALUE (display order != value)
        self.vp_combo.setCurrentIndex(_i if _i >= 0 else 0)
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
