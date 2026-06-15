"""Tier-3 visualization client — MinimalTerminalWindow (entry point #2).

    python -m app.terminal

An independent PySide6 window driving a PyQtGraph canvas at 20Hz off the
thread-safe :class:`app.pipe_client.PipeClientWorker` cache. The central widget
is a QSplitter holding the chart and the COB depth pane; every other widget
(HUD, hamburger, drawing toolbar, alerts ledger, stats overlay) floats as a
top-level child of the window so canvas resets never drop it (spec §6.1.2).

Only the candlestick item drives auto-range; all analytics layers are added with
``ignoreBounds=True``. Picture-backed layers rebuild only on a data-signature
change, so a quiet market is nearly free while a tick rush still redraws at full
rate (Section 11).
"""

from __future__ import annotations

import bisect
import math
import os
import shutil
import socket
import subprocess
import sys
from datetime import datetime
from typing import List, Optional

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from . import config
from .alerts import AlertsLedger
from .chart_widgets import (
    BucketCandleItem, CandlestickItem, LiquidationLayer, LocalTimeAxis,
    OrderBlockLayer, PriceAxis, SessionLayer,
)
from .cob_panel import CobPanel
from .drawing_tools import DrawingController, DrawingToolbar
from .footprint_layers import DepthWallLayer, FootprintLayer, IcebergLayer, ImbalanceLayer
from .hamburger import FloatingOverlayMenu, HamburgerButton
from .hud_overlay import PriceHud
from .pipe_client import PipeClientWorker
from .stats_overlay import StatsOverlay, compute_stats

_OPEN_WINDOWS: List["MinimalTerminalWindow"] = []


# ---------------------------------------------------------------------------
# Step 5 — adaptive exhaustion (Mode 3): scale-free, no rigid E/R tiers.
# Exhaustion intensity scales with how anomalous a bucket's E/R is vs its OWN
# rolling window (a z-score), not against absolute cutoffs. exp(K*tanh(z/Zs)) is
# monotonic in z, centred at 1.0 (z=0 -> neutral) and BOUNDED to [exp(-K),exp(K)],
# so a degenerate/flat window can never explode it. The params below are
# statistical-hygiene knobs (window sizes, a CoV floor), not market thresholds.
# ---------------------------------------------------------------------------
EXH_WINDOW = 30           # rolling E/R baseline window (buckets)
EXH_MIN_WINDOW = 10       # 0.6/2 warm-up: below this the E/R multiplier is neutral (1.0)
EXH_CV_FLOOR = 0.10       # 0.6/1 coefficient-of-variation floor on the z denominator
EXH_K = math.log(2.0)     # E/R multiplier range exp(+/-K * tanh) = [0.5, 2.0]
EXH_Z_SCALE = 2.0         # z-score (sigma) scale of the smooth tanh ramp
EXH_OI_K = math.log(1.5)  # OI-direction term range [0.667, 1.5]
EXH_OI_SCALE = 0.5        # scale of the (delta_oi / curr_vol) tanh ramp


def _exh_z_mult(window_vals: list, val: float) -> float:
    """Smooth, bounded exhaustion multiplier from the z-score of ``val`` vs a
    rolling ``window_vals`` of recent same-side E/R (DIVERGES FROM LEGACY, Step 5).

    * Cold start (rule 0.6/2): a window shorter than ``EXH_MIN_WINDOW`` returns the
      NEUTRAL multiplier 1.0 — no z-score against an under-filled window.
    * Degenerate denominator (rule 0.6/1): std is floored at a coefficient-of-
      variation fraction of the mean (``EXH_CV_FLOOR*|mean|`` — scale-free, NOT a
      fixed absolute), and an all-zero window falls back to neutral, not div-by-0.
    * ``tanh`` bounds the exponent, so a noisy/flat window can never spike the
      multiplier beyond ``exp(EXH_K)``.
    """
    if len(window_vals) < EXH_MIN_WINDOW:
        return 1.0
    mean = sum(window_vals) / len(window_vals)
    var = sum((v - mean) ** 2 for v in window_vals) / len(window_vals)
    denom = max(var ** 0.5, EXH_CV_FLOOR * abs(mean))
    if denom <= 0:
        return 1.0
    z = (val - mean) / denom
    return math.exp(EXH_K * math.tanh(z / EXH_Z_SCALE))


def _exhaustion_mults(buckets: list, i: int) -> "tuple[float, float, float]":
    """(buyer_er_mult, seller_er_mult, oi_mult) for bucket ``i`` (Step 5).

    The two E/R multipliers are the z-score smooth multiplier of each side's E/R
    against the PRECEDING rolling window. The OI-direction term is a smooth,
    bounded function of the Step-3 net ``delta_oi`` ( = (opL+opS) - (clL+clS) )
    normalised by volume: exhaustion is AMPLIFIED when OI is contracting (positions
    closing, delta_oi < 0) and DAMPENED when expanding, neutral at delta_oi = 0.
    """
    b = buckets[i]
    win = buckets[max(0, i - EXH_WINDOW):i]
    b_mult = _exh_z_mult([w.get("buyer_er", 0.0) for w in win], b.get("buyer_er", 0.0))
    s_mult = _exh_z_mult([w.get("seller_er", 0.0) for w in win], b.get("seller_er", 0.0))
    delta_oi = (b.get("opL", 0.0) + b.get("opS", 0.0)) - (b.get("clL", 0.0) + b.get("clS", 0.0))
    r_oi = delta_oi / max(1.0, b.get("curr_vol", 0.0))
    oi_mult = math.exp(-EXH_OI_K * math.tanh(r_oi / EXH_OI_SCALE))
    return b_mult, s_mult, oi_mult


class MinimalTerminalWindow(QtWidgets.QMainWindow):
    def __init__(self, tf: str = config.DEFAULT_TF):
        super().__init__()
        self.setWindowTitle(f"Order Flow Terminal — {config.SYMBOL} {tf}")
        self.resize(1280, 760)

        self._sig_candles = None
        self._sig_obs = None
        self._sig_fp = None
        self._autoranged = False
        self._stats_enabled = True
        self._last_snap: dict = {}

        # --- chart + COB split (the splitter handle is the COB resizer) ---
        self.plot = pg.PlotWidget(axisItems={"bottom": LocalTimeAxis(orientation="bottom"),
                                             "right": PriceAxis(orientation="right")})
        self.plot.showAxis("right"); self.plot.hideAxis("left")
        self.plot.showGrid(x=True, y=True, alpha=0.12)
        self.plot.setMenuEnabled(False)
        self.vb = self.plot.getViewBox()
        self.vb.setMouseMode(pg.ViewBox.PanMode)
        self.vb.setLimits(xMin=None, xMax=None, yMin=None, yMax=None)

        self.cob = CobPanel()
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.splitter.setHandleWidth(6)
        self.splitter.addWidget(self.plot)
        self.splitter.addWidget(self.cob)
        self.splitter.setStretchFactor(0, 1)
        self.cob.hide()
        self.setCentralWidget(self.splitter)
        self.vb.sigYRangeChanged.connect(self._sync_cob)

        # --- render layers (only candles drive auto-range) ---
        self.session_item = SessionLayer()
        self.depthwall_item = DepthWallLayer()
        self.imbalance_item = ImbalanceLayer()
        self.footprint_item = FootprintLayer()
        self.iceberg_item = IcebergLayer()
        self.ob_item = OrderBlockLayer()
        self.candle_item = CandlestickItem()
        self.liq_item = LiquidationLayer()
        for it in (self.session_item, self.depthwall_item, self.imbalance_item,
                   self.footprint_item, self.iceberg_item, self.ob_item, self.liq_item):
            self.plot.addItem(it, ignoreBounds=True)
        self.plot.addItem(self.candle_item)   # the only bounds-affecting item
        self.footprint_item.hide(); self.imbalance_item.hide(); self.iceberg_item.hide()

        # Z-ORDER (patch §3): analytics layers must render ABOVE the candles/grid.
        #   depth walls + OB zones sit behind candles; footprints/imbalances/
        #   icebergs/sessions/liqs sit on top so they are never hidden.
        self.ob_item.setZValue(3)
        self.candle_item.setZValue(5)
        self.depthwall_item.setZValue(9)   # fix #11: walls above candles, were hidden behind
        self.session_item.setZValue(10)
        self.imbalance_item.setZValue(11)
        self.footprint_item.setZValue(12)
        self.iceberg_item.setZValue(13)
        self.liq_item.setZValue(14)

        # Attach pooled TextItems for in-chart labels (footprint rows, OB
        # multipliers, iceberg marks, imbalance tags) — fixes flipped text.
        self.ob_item.attach_text(self.plot)
        self.footprint_item.attach_text(self.plot)
        self.imbalance_item.attach_text(self.plot)
        self.iceberg_item.attach_text(self.plot)

        # --- live price dashed line (patch §1) ---
        self.price_line = pg.InfiniteLine(
            angle=0, movable=False,
            pen=pg.mkPen(color="#000000", style=QtCore.Qt.DashLine, width=1))
        self.price_line.setZValue(6)
        self.plot.addItem(self.price_line, ignoreBounds=True)

        # --- scanner overlay (patch §12) — hidden until a mode is picked ---
        self.scanner_bars = pg.BarGraphItem(x=[], height=[], width=0.6)
        self.scanner_bars.setZValue(4)
        self.plot.addItem(self.scanner_bars)   # NOT ignoreBounds — must drive Y fit (fix #14)
        self.scanner_bars.hide()
        # VPIN 0.85 institutional alert baseline (HTML #ff073a dashed)
        self.scanner_baseline = pg.InfiniteLine(
            angle=0, movable=False, pen=pg.mkPen("#ff073a", style=QtCore.Qt.DashLine, width=2))
        self.scanner_baseline.setZValue(5)
        self.plot.addItem(self.scanner_baseline, ignoreBounds=True)
        self.scanner_baseline.hide()
        self.scanner_mode = "Off"
        self._scanner_fitted = False
        # Phase 1: bucket pipeline state. active_scanner_items is the GC list the
        # Phase 2 teardown will sweep; the sig/cache make _build_scanner_buckets
        # signature-gated so a quiet market costs nothing.
        self.active_scanner_items: List[object] = []
        self._scan_handles: dict = {}   # per-mode item refs: create-once, update-after
        self._scan_trackers: dict = {}  # tracker key -> redock record (line/text/x/vb)
        self._scanner_bucket_sig: Optional[tuple] = None
        self._scanner_bucket_cache: tuple = ([], [], 0)
        self._last_scanner_sig: Optional[tuple] = None   # render-skip gate (Phase 7 perf)
        self._cob_prev_visible: bool = False             # COB state across scanner toggles
        self._scanner_needs_autofit: bool = True         # one-shot Y/X fit (frees manual zoom)
        self._depth_needs_calibration: bool = True       # one-shot depth-slider 20% baseline (§1)
        # Handles for the heavy modes' extra scene objects (built in Phase 5/6,
        # torn down here). Pre-declared so teardown checks are always safe.
        self.axis_bottom = self.plot.getAxis("bottom")
        self.vb_kinetic_price = None   # Mode 4 secondary linked price ViewBox
        self.vb_pulse_churn = None     # Modes 7/8 secondary churn-scale ViewBox
        self.lower_plot = None         # Mode 10 lower VPIN sub-pane
        self.splitter_v = None         # Mode 10 vertical splitter (upper/lower panes)
        # Mode 10 order-block layer (index-space). Persistent object; added to the
        # plot lazily in _scan_bucket_canvas and swept on teardown. Tiers forced on.
        self.bc_obs = OrderBlockLayer(self.plot, show_tiers=True)

        # --- crosshair (patch §13): light-gray dashed ---
        pen = pg.mkPen(color="#aaaaaa", style=QtCore.Qt.DashLine, width=1)
        self.vline = pg.InfiniteLine(angle=90, movable=False, pen=pen)
        self.hline = pg.InfiniteLine(angle=0, movable=False, pen=pen)
        self.vline.setZValue(15); self.hline.setZValue(15)
        self.plot.addItem(self.vline, ignoreBounds=True)
        self.plot.addItem(self.hline, ignoreBounds=True)
        self._proxy = pg.SignalProxy(self.plot.scene().sigMouseMoved,
                                     rateLimit=60, slot=self._on_mouse_move)

        # --- floating overlays (top-level children) ---
        self.hud = PriceHud(self)
        self.stats = StatsOverlay(self)
        self.alerts = AlertsLedger(self)
        self.drawbar = DrawingToolbar(self)
        self.menu = FloatingOverlayMenu(self)
        self.menu_btn = HamburgerButton(self)
        self.menu_btn.clicked.connect(self.menu.toggle_panel)

        # fix #8: dedicated floating 🔔 button next to the hamburger
        self.bell_btn = HamburgerButton(self)
        self.bell_btn.setText("🔔")
        self.bell_btn.clicked.connect(self.alerts.toggle)

        # fix #10: double-click anywhere on the chart resets/auto-fits the view
        self.plot.scene().sigMouseClicked.connect(self._on_scene_click)

        # --- drawing controller ---
        self.drawer = DrawingController(self.plot)
        self.drawer.toolbar = self.drawbar         # §7.3 — enables auto-revert
        self.drawbar.toolSelected.connect(self.drawer.set_tool)
        QtGui.QShortcut(QtGui.QKeySequence("V"), self, activated=self.drawer.cancel)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+N"), self, activated=spawn_window)

        # §7.4 — yellow follow-spot shown on the cursor while a draw tool is armed
        self.cursor_spot = pg.ScatterPlotItem(size=10, pxMode=True,
                                              brush=pg.mkBrush("#ffeb3b"),
                                              pen=pg.mkPen("#000000", width=1))
        self.cursor_spot.setZValue(60)
        self.plot.addItem(self.cursor_spot, ignoreBounds=True)
        self.cursor_spot.hide()

        self._wire_menu()

        # --- data worker (baseline before show, spec §9.1.3 / §9.2.2) ---
        self.worker = PipeClientWorker(tf=tf)
        self.worker.load_baseline(tf)
        self.worker.start()

        # --- 20Hz master loop ---
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._on_timer)
        self.timer.start(config.GUI_TIMER_MS)

    # ------------------------------------------------------------------
    def _wire_menu(self) -> None:
        self.menu.tfChanged.connect(self._change_tf)
        self.menu.multiplierChanged.connect(lambda v: setattr(self.ob_item, "visible_filter", v))
        self.menu.chartFilterChanged.connect(lambda v: setattr(self.depthwall_item, "threshold", float(v)))
        self.menu.layerToggled.connect(self._toggle_layer)
        self.menu.subWidgetToggled.connect(self._toggle_subwidget)
        self.menu.scannerChanged.connect(self._set_scanner)
        self.menu.scan_time_changed.connect(self._on_scan_time_changed)

    def _set_scanner(self, mode: str) -> None:
        """Scanner state machine: route between time space and the 10 bucket modes.

        Invariant order on every transition: set mode -> teardown (clears items and,
        on Off, reverts time components) -> if scanning, hide time components and
        flip the axis to bucket-index. Per-mode geometry is drawn by the 50ms loop
        via :meth:`_draw_scanner` (populated in Phases 3-6).
        """
        self.scanner_mode = mode
        scanning = mode != "Off"

        # Teardown first — clear_scanner_canvas reads self.scanner_mode and, when
        # it is "Off", re-shows the time-based components for us.
        self.clear_scanner_canvas()

        self.axis_bottom.set_scanner_active(scanning)
        # §6.2 — drawing is LOCKED on every scanner mode EXCEPT bucket_canvas, where
        # it's re-enabled in index space (session-only via DrawingController.index_mode).
        is_canvas = mode == "bucket_canvas"
        self.drawer.locked = scanning and not is_canvas
        self.drawer.index_mode = is_canvas
        if scanning:
            if not is_canvas:
                self.drawer.cancel()   # drop any armed tool + hide its edit panel
            self._hide_time_components()
            self._apply_scanner_theme(dark=True)     # enhancement §3
            self._scanner_needs_autofit = True       # one-shot fit for the new mode
            self._scanner_bucket_sig = None
            self._last_scanner_sig = None
            self._hide_time_components()
            self._apply_scanner_theme(dark=True)     # enhancement §3
            self._scanner_needs_autofit = True       # one-shot fit for the new mode
            self._scanner_bucket_sig = None
            self._last_scanner_sig = None
            self._on_timer()   # immediate first draw from the current Zero Point

    def _hide_time_components(self) -> None:
        """Hide every time-based layer/overlay so the bucket canvas is clean."""
        for it in (self.candle_item, self.price_line, self.ob_item, self.footprint_item,
                   self.imbalance_item, self.iceberg_item, self.liq_item,
                   self.depthwall_item, self.session_item):
            it.setVisible(False)
        self.hud.hide()          # price/countdown HUD is meaningless on an index axis
        self.stats.hide()
        # COB ladder is price-axis-bound; remember its state and hide it
        self._cob_prev_visible = self.cob.isVisible()
        self.cob.hide()
        # legacy persistent scanner items are unused by the bucket modes
        self.scanner_bars.hide()
        self.scanner_baseline.hide()

    def _show_time_components(self) -> None:
        """Restore the standard time chart, honoring each layer's toggle state."""
        self._apply_scanner_theme(dark=False)   # back to the light candlestick theme
        self.candle_item.setVisible(True)
        self.price_line.setVisible(True)
        self.depthwall_item.setVisible(True)
        self.hud.show()
        self.cob.setVisible(self._cob_prev_visible)
        self.ob_item.setVisible(self.menu.layer_state("order_blocks"))
        self.footprint_item.setVisible(self.menu.layer_state("footprints"))
        self.imbalance_item.setVisible(self.menu.layer_state("imbalances"))
        self.iceberg_item.setVisible(self.menu.layer_state("icebergs"))
        self.liq_item.setVisible(self.menu.layer_state("liquidations"))
        self.session_item.setVisible(self.menu.layer_state("sessions"))
        # hand the vertical viewport back to the time-candle bounds
        self._autoranged = False
        self._sig_candles = self._sig_obs = self._sig_fp = None

    def _change_tf(self, tf: str) -> None:
        self.setWindowTitle(f"Order Flow Terminal — {config.SYMBOL} {tf}")
        self._sig_candles = self._sig_obs = self._sig_fp = None
        self._autoranged = False
        self._scanner_needs_autofit = True    # new tf -> refit the scanner once
        self._scanner_bucket_sig = self._last_scanner_sig = None
        self._depth_needs_calibration = True  # new tf -> re-baseline the depth slider (§1)
        self.worker.request_timeframe(tf)

    def _toggle_layer(self, key: str, on: bool) -> None:
        if key == "order_blocks":
            self.ob_item.setVisible(on)
        elif key == "footprints":
            self.footprint_item.setVisible(on)
        elif key == "icebergs":
            self.iceberg_item.setVisible(on)
            self._sig_fp = None  # force iceberg rebuild on next frame
        elif key == "imbalances":
            self.imbalance_item.setVisible(on)
        elif key == "stats":
            self._stats_enabled = on
            if not on:
                self.stats.hide()
        elif key == "liquidations":
            self.liq_item.setVisible(on)
        elif key == "sessions":
            self.session_item.setVisible(on)
        elif key == "velocity_tiers":
            self.ob_item.show_tiers = on
            self._sig_obs = None  # force OB redraw

    def _toggle_subwidget(self, key: str, on: bool) -> None:
        if key == "drawing":
            self.drawbar.setVisible(on)
            if not on:
                self.drawer.cancel()
        elif key == "cob":
            self.cob.setVisible(on)
        elif key == "audio":
            self.alerts.audio.set_armed(on)

    def _on_scene_click(self, ev) -> None:
        """Double-click resets + auto-fits the view (fix #10, TradingView parity)."""
        if ev.double():
            self._autoranged = False
            self.plot.enableAutoRange()
            self.plot.autoRange()
            self.plot.disableAutoRange()
            self._autoranged = True

    # ------------------------------------------------------------------
    def _sync_cob(self) -> None:
        if self.cob.isVisible():
            (_, _), (y0, y1) = self.vb.viewRange()
            self.cob.sync_y(y0, y1)

    def _calibrate_depth_slider(self, depth: dict) -> None:
        """§1 — one-shot: default the depth-wall slider to 20% of the largest
        resting order on the first valid book payload after connect / tf-change.

        ``setValue`` propagates through ``valueChanged -> _emit_chart_filter ->
        chartFilterChanged`` and updates the wall threshold for free. The flag
        flips off afterward so manual slider drags are never overridden.
        """
        if not self._depth_needs_calibration:
            return
        qtys = []
        for side in ("bids", "asks"):
            for lvl in depth.get(side, []):
                try:
                    qtys.append(float(lvl[1]))
                except (ValueError, IndexError, TypeError):
                    continue
        if not qtys:
            return   # no order-book payload yet — keep waiting (flag stays True)
        target_default = int(max(qtys) * 0.20)
        target_default = max(config.CHART_FILTER_MIN,
                             min(config.CHART_FILTER_MAX, target_default))
        self.menu.chart_slider.setValue(target_default)
        self._depth_needs_calibration = False

    def _on_mouse_move(self, evt) -> None:
        pos = evt[0]
        if not self.plot.sceneBoundingRect().contains(pos):
            self.stats.hide()
            return
        pt = self.vb.mapSceneToView(pos)
        self.vline.setPos(pt.x()); self.hline.setPos(pt.y())

        # §7.4 — yellow follow-spot tracks the cursor only while a drawing tool is
        # armed (anything other than the cursor/select pointer); hidden otherwise.
        if self.drawer.active_tool not in (None, "select"):
            self.cursor_spot.setData([pt.x()], [pt.y()])
            self.cursor_spot.show()
        else:
            self.cursor_spot.hide()

        # Scanner active: translate the cursor's integer index -> that bucket's
        # real end_time and surface it in the HUD overlay (Phase 2 §4).
        if self.scanner_mode != "Off":
            self._hover_scanner(pt.x(), pos)
            return

        if self._stats_enabled and len(self._last_snap.get("times", [])):
            self._hover_stats(pt.x(), pos)
        else:
            self.stats.hide()

    def _hover_scanner(self, x: float, scene_pos) -> None:
        """Rich, mode-specific HUD readout for the hovered volume bucket (§4)."""
        filtered, _x, _a = self._build_scanner_buckets()
        idx = int(round(x))
        if not (0 <= idx < len(filtered)):
            self.stats.hide()
            return
        end_time = filtered[idx].get("end_time", 0.0)
        try:
            clock = datetime.fromtimestamp(end_time).strftime("%Y-%m-%d %H:%M:%S")
        except (OSError, ValueError, OverflowError):
            clock = "--"
        lines = [f"<b>Idx: {idx}</b>"] + self._hover_context(self.scanner_mode, filtered, idx)
        gp = self.plot.mapToGlobal(self.plot.mapFromScene(scene_pos))
        wp = self.mapFromGlobal(gp)
        self.stats.show_stats(lines, clock, wp.x(), wp.y())

    def _hover_context(self, mode: str, buckets: list, idx: int) -> "list[str]":
        """Color-coded, mode-specific HUD lines for the hovered bucket (§4)."""
        b = buckets[idx]
        K = self._fmt_k
        g, r, bl, pu = "#2ecc71", "#e74c3c", "#3498db", "#9b59b6"
        teal, gold, gray = "#26a69a", "#f1c40f", "#9aa0aa"

        def span(text, col):
            return f"<span style='color:{col}'>{text}</span>"

        if mode == "open_pos" or mode == "bucket_open_pos":
            return [f"{span('+'+K(b.get('opL',0))+' Longs', g)} | "
                    f"{span('-'+K(b.get('opS',0))+' Shorts', r)}"]
        if mode == "close_pos" or mode == "bucket_close_pos":
            return [f"{span('+'+K(b.get('clS',0))+' Close-Sh', bl)} | "
                    f"{span('-'+K(b.get('clL',0))+' Close-Lng', pu)}"]
        if mode == "volume":
            return [f"{span('+'+K(b.get('buy_vol',0))+' Buy', teal)} | "
                    f"{span('-'+K(b.get('sell_vol',0))+' Sell', r)}"]
        if mode == "exhaustion":
            cvd = mx = mn = 0.0
            for j in range(idx + 1):
                cvd += buckets[j].get("buy_vol", 0.0) - buckets[j].get("sell_vol", 0.0)
                mx = max(mx, cvd); mn = min(mn, cvd)
            span_v = max(0.001, mx - mn)
            b_base = (mx - cvd) / span_v * 100.0
            s_base = (cvd - mn) / span_v * 100.0
            # DIVERGES FROM LEGACY (Step 5): the same smooth z-score E/R + delta_oi
            # multipliers the chart uses, so hover and chart can't disagree.
            bm, sm, oi_mult = _exhaustion_mults(buckets, idx)
            return [f"Exh Base {span(f'B:{b_base:.0f}%', bl)} {span(f'S:{s_base:.0f}%', r)}",
                    f"OI {span(f'{oi_mult:.2f}x', gold)} | "
                    f"ER-z {span(f'B {bm:.2f}x', g)}/{span(f'S {sm:.2f}x', r)}"]
        if mode in ("kinetic", "bucket_canvas"):
            dur = max(1.0, b.get("end_time", 0.0) - b.get("start_time", 0.0))
            v_bull = b.get("buy_vol", 0.0) / dur
            v_bear = b.get("sell_vol", 0.0) / dur
            baseline = bull_st = bear_st = 0.0
            for j in range(idx + 1):
                bb = buckets[j]
                d = max(1.0, bb.get("end_time", 0.0) - bb.get("start_time", 0.0))
                vb, vs = bb.get("buy_vol", 0.0) / d, bb.get("sell_vol", 0.0) / d
                bk, sk = vb * (bb.get("buyer_er", 0.0) / 100.0), vs * (bb.get("seller_er", 0.0) / 100.0)
                den = max(1.0, vb + vs)
                bsr, ssr = (bk / den) * 0.5, (sk / den) * 0.5
                if j == 0:
                    baseline, bull_st, bear_st = bb.get("poc_price", 0.0), bsr, ssr
                else:
                    baseline = bb.get("poc_price", 0.0) * 0.05 + baseline * 0.95
                    bull_st = bsr * 0.1 + bull_st * 0.9
                    bear_st = ssr * 0.1 + bear_st * 0.9
            return [f"{span(f'vBull {v_bull:.1f}/s', teal)} | {span(f'vBear {v_bear:.1f}/s', r)}",
                    f"Fcast {span(f'+{baseline + bull_st:.2f}', g)} / "
                    f"{span(f'-{baseline - bear_st:.2f}', r)}"]
        if mode == "vpin":
            window = buckets[max(0, idx - 49): idx + 1]
            ti = sum(abs(x.get("buy_vol", 0.0) - x.get("sell_vol", 0.0)) for x in window)
            tv = sum(x.get("curr_vol", 0.0) for x in window)
            v = ti / tv if tv > 0 else 0.0
            if v >= 0.85:
                cls, col = "HFT Liquidity Trap", r
            elif v >= 0.50:
                cls, col = "Institutional Accumulation", gold
            else:
                cls, col = "Normal Balancing", gray
            return [f"Imbalance {K(ti)} | VPIN {v:.2f}", span(cls, col)]
        if mode == "effort_result":
            ber, ser = b.get("buyer_er", 0.0), b.get("seller_er", 0.0)
            return [f"{span(f'Buyer ER {ber:.1f}', g)} | {span(f'Seller ER {ser:.1f}', r)}",
                    span("friction = vol / ticks traveled", gray)]
        return []

    def _hover_stats(self, x: float, scene_pos) -> None:
        times = self._last_snap["times"]
        tf_secs = config.TF_SECONDS.get(self._last_snap["tf"], 60)
        # nearest candle within half a bar
        best_t, best_d = None, tf_secs
        for t in times:
            d = abs(t - x)
            if d < best_d:
                best_d, best_t = d, int(t)
        if best_t is None:
            self.stats.hide(); return
        result = compute_stats(self._last_snap, best_t)
        if result is None:
            self.stats.hide(); return
        lines, verdict = result
        gp = self.plot.mapToGlobal(self.plot.mapFromScene(scene_pos))
        wp = self.mapFromGlobal(gp)
        self.stats.show_stats(lines, verdict, wp.x(), wp.y())

    # ------------------------------------------------------------------
    def _on_timer(self) -> None:
        snap = self.worker.snapshot()
        self._last_snap = snap

        # --- SCANNER MODE: bucket-based metric replaces the time candle view ---
        # Branch first so NO time-based work (bracket labels, candle/OB/footprint/
        # depth/session/liq/HUD updates) runs while a scanner mode owns the canvas.
        if self.scanner_mode != "Off":
            self._draw_scanner()
            self._redock_trackers()   # keep badges pinned to the axis under pan/zoom
            return

        times = snap["times"]
        if len(times) < 1:
            return

        tf = snap["tf"]
        tf_secs = config.TF_SECONDS.get(tf, 60)
        width = tf_secs * 0.7
        ohlcv = snap["ohlcv"]
        (x0, x1), (y0, y1) = self.vb.viewRange()
        x_right = float(times[-1]) + tf_secs * 8

        # right-align position-bracket data labels to the live view edge (§17)
        self.drawer.update_view(x_right)

        # candles (signature-gated)
        sig = (len(times), float(times[-1]), float(ohlcv[-1, 1]),
               float(ohlcv[-1, 2]), float(ohlcv[-1, 3]))
        if sig != self._sig_candles:
            self.candle_item.update_data(times, ohlcv, width)
            self._sig_candles = sig
            if not self._autoranged:
                self.plot.enableAutoRange(); self.plot.autoRange()
                self.plot.disableAutoRange(); self._autoranged = True

        # live price dashed line (patch §1)
        if snap["latest_price"]:
            self.price_line.setPos(snap["latest_price"])

        # order blocks (signature-gated)
        obs = snap["order_blocks"]
        ob_sig = (len(obs), tuple(o.get("ob_id") for o in obs[:8]),
                  tuple(o.get("active") for o in obs[:8]), self.ob_item.show_tiers)
        if ob_sig != self._sig_obs:
            self.ob_item.update_data(obs, x_right)
            self._sig_obs = ob_sig

        # footprint-derived layers (signature-gated; only if visible)
        fps = snap["footprints"]
        forming = fps.get(str(snap.get("forming_time")), {})
        fp_vol = sum(v.get("b", 0) + v.get("s", 0) for v in forming.get("levels", {}).values())
        fp_sig = (len(fps), round(fp_vol, 1), round(x0), round(x1))
        if fp_sig != self._sig_fp:
            self._sig_fp = fp_sig
            yr = max(1e-9, y1 - y0)
            xr = max(1e-9, x1 - x0)
            px_per_x = self.vb.width() / xr
            px_per_y = self.vb.height() / yr
            px_per_candle = px_per_x * tf_secs
            candles_by_t = {int(times[i]): list(ohlcv[i]) for i in range(len(times))}
            proj_x = max(x_right, x1)   # project to the live right edge (fix #9)
            if self.footprint_item.isVisible():
                self.footprint_item.update_data(fps, x0, x1, width, px_per_x, px_per_y)
            if self.imbalance_item.isVisible():
                self.imbalance_item.update_data(fps, candles_by_t, x0, x1, proj_x)
            if self.iceberg_item.isVisible():
                # fix #13: hide 🧊 icons when candles get too narrow (zoomed out)
                show_icons = px_per_candle >= 22.0
                self.iceberg_item.update_data(fps, candles_by_t, x0, x1, proj_x, show_icons)

        # depth walls + COB (depth changes each pulse)
        self._calibrate_depth_slider(snap["depth"])   # §1: one-shot 20% baseline
        self.depthwall_item.update_data(snap["depth"], x0, x1)
        if self.cob.isVisible():
            self.cob.update_depth(snap["depth"]); self._sync_cob()

        # sessions + liquidations
        if self.session_item.isVisible():
            self.session_item.update_data(x0, x1, y0, y1)
        if self.liq_item.isVisible():
            self.liq_item.update_data(snap["liquidations"])

        # alerts + HUD
        self.alerts.feed(snap)
        self.hud.update_values(snap["latest_price"], snap["forming_time"], tf)
        self._reposition_hud(snap["latest_price"])

    # ------------------------------------------------------------------
    # Phase 1: bucket pipeline + Zero-Point anchor
    # ------------------------------------------------------------------
    def _on_scan_time_changed(self) -> None:
        """User moved the Zero Point: flush geometry and redraw from the new anchor."""
        self.clear_scanner_canvas()
        self._scanner_bucket_sig = None       # force a fresh bucket rebuild
        self._scanner_needs_autofit = True    # re-fit once to the new window
        if self.scanner_mode != "Off":
            self._on_timer()                  # immediate manual redraw

    def clear_scanner_canvas(self) -> None:
        """Aggressive teardown of all scanner geometry + heavy-mode scene objects.

        Safe to call in any state. Steps: (1) sweep tracked items; (2) Mode 4
        secondary ViewBox teardown; (3) Mode 10 lower-pane teardown; (4) if the
        active mode is now "Off", revert to the standard time chart.
        """
        # 1. sweep every tracked scanner item off the plot
        for item in self.active_scanner_items:
            try:
                self.plot.removeItem(item)
            except Exception:
                pass
        self.active_scanner_items.clear()
        self._scan_handles = {}   # stale after removeItem — modes recreate on next draw
        self._scan_trackers = {}  # drop redock records (their items were just swept)
        try:
            self.scanner_bars.setOpts(x=[], height=[])
        except Exception:
            pass
        self._scanner_bucket_sig = None
        self._last_scanner_sig = None   # force a fresh render after any teardown

        # 2. Mode 4 teardown — destroy the secondary linked price ViewBox
        if self.vb_kinetic_price is not None:
            try:
                self.vb_kinetic_price.setXLink(None)
                self.plot.scene().removeItem(self.vb_kinetic_price)
                try:
                    self.plot.getViewBox().sigResized.disconnect(self._sync_kinetic_vb)
                except (TypeError, RuntimeError):
                    pass
            except Exception:
                pass
            self.vb_kinetic_price = None

        # 2b. Modes 7/8 teardown — destroy the secondary churn ViewBox so it never
        #     orphans/leaks between modes (same pattern as the Mode-4 vb above).
        if self.vb_pulse_churn is not None:
            try:
                self.vb_pulse_churn.setXLink(None)
                self.plot.scene().removeItem(self.vb_pulse_churn)
                try:
                    self.plot.getViewBox().sigResized.disconnect(self._sync_pulse_churn_vb)
                except (TypeError, RuntimeError):
                    pass
            except Exception:
                pass
            self.vb_pulse_churn = None

        # 3. Mode 10 teardown — unlink, restore the primary plot to its native row
        #    in the horizontal splitter, then destroy the vertical splitter + pane.
        if self.lower_plot is not None:
            try:
                # leak guard: the OB layer's tier labels are pool-managed (not in
                # active_scanner_items), so sweep them explicitly off the plot (§6.1)
                self.bc_obs.tier_pool.clear(self.plot)
                # §6.2 — index-space drawings are session-only; wipe them on exit
                self.drawer.flush_index_drawings()
                self.lower_plot.getViewBox().setXLink(None)
                # reparent self.plot back to the horizontal splitter at index 0,
                # i.e. restore [self.plot, self.cob]
                self.splitter.insertWidget(0, self.plot)
                self.splitter.setStretchFactor(0, 1)
                self.lower_plot.setParent(None)
                if self.splitter_v is not None:
                    self.splitter_v.setParent(None)
                    self.splitter_v.deleteLater()
                self.lower_plot.deleteLater()
            except Exception:
                pass
            self.lower_plot = None
            self.splitter_v = None

        # 4. reversion: if we've landed on "Off", restore the time chart
        if self.scanner_mode == "Off":
            self._show_time_components()

    def _sync_kinetic_vb(self) -> None:
        """Keep the Mode 4 secondary price ViewBox glued to the main viewport.

        Defined here so the teardown disconnect in clear_scanner_canvas always has
        a stable slot reference; the geometry binding itself is wired in Phase 5.
        """
        if self.vb_kinetic_price is not None:
            self.vb_kinetic_price.setGeometry(self.plot.getViewBox().sceneBoundingRect())
            self.vb_kinetic_price.linkedViewChanged(self.plot.getViewBox(),
                                                    self.vb_kinetic_price.XAxis)

    def _ensure_pulse_churn_vb(self) -> None:
        """Lazily build the Modes 7/8 secondary, X-linked ViewBox that carries the
        per-bucket churn line on its OWN y-scale, so churn (~= full bucket volume)
        can never crush the heartbeat bars. Standard pyqtgraph second-ViewBox
        pattern (mirrors the Mode-4 ``vb_kinetic_price``); torn down on every
        mode-switch by ``clear_scanner_canvas`` so it can't orphan/leak."""
        if self.vb_pulse_churn is not None:
            return
        self.vb_pulse_churn = pg.ViewBox()
        self.plot.scene().addItem(self.vb_pulse_churn)
        self.vb_pulse_churn.setXLink(self.plot.getViewBox())   # share the bucket X-axis
        self.vb_pulse_churn.setMenuEnabled(False)
        self.vb_pulse_churn.setMouseEnabled(x=False, y=False)
        self.vb_pulse_churn.disableAutoRange()                 # Y is set explicitly per render
        self.plot.getViewBox().sigResized.connect(self._sync_pulse_churn_vb)
        self._sync_pulse_churn_vb()

    def _sync_pulse_churn_vb(self) -> None:
        """Keep the Modes 7/8 secondary churn ViewBox glued to the main viewport."""
        if self.vb_pulse_churn is not None:
            self.vb_pulse_churn.setGeometry(self.plot.getViewBox().sceneBoundingRect())
            self.vb_pulse_churn.linkedViewChanged(self.plot.getViewBox(),
                                                  self.vb_pulse_churn.XAxis)

    def _build_scanner_buckets(self) -> "tuple[list[dict], list[int], int]":
        """Single source of truth for every scanner mode (signature-gated).

        Returns ``(filtered_buckets, x_indices, anchor_idx)``:
          * ``filtered_buckets`` — BucketSnapshot dicts at/after the Zero Point,
            with the live ``active_bucket`` appended as the pulsing right edge.
          * ``x_indices`` — ``[0, 1, ..., len(filtered_buckets)-1]`` (the new X-axis).
          * ``anchor_idx`` — index in the *combined* raw array where Index 0 landed.

        (The spec says "four arrays"; there are three documented returns —
        ``anchor_idx`` is a scalar pointer, not an array. Implemented as listed.)
        """
        snap = self._last_snap or self.worker.snapshot()
        closed_list: list[dict] = snap.get("closed_buckets", []) or []
        active: dict = snap.get("active_bucket") or {}

        combined: list[dict] = list(closed_list)
        # Append the live edge — but guard the ~1-frame window right after a close
        # where the just-closed bucket is in closed_list AND still the stale active
        # (until the next TICK ships a fresh active), which would double-count it.
        #
        # NOTE: start_time is NOT unique — several buckets fill within one busy
        # minute and share it — so the fingerprint must include curr_vol. The
        # stale active is identical to closed[-1] (start_time AND a full curr_vol);
        # a fresh same-minute bucket has a smaller, differing curr_vol and is kept.
        if active and active.get("curr_vol", 0.0) > 0:
            last = closed_list[-1] if closed_list else None
            is_stale_dup = (
                last is not None
                and active.get("start_time") == last.get("start_time")
                and active.get("curr_vol") == last.get("curr_vol")
            )
            if not is_stale_dup:
                combined.append(active)

        anchor_unix = self.menu.scan_start_unix()

        # signature gate: rebuild only when the bucket set, the live edge volume,
        # or the anchor actually changes.
        sig = (len(combined), round(active.get("curr_vol", 0.0), 1), anchor_unix)
        if sig == self._scanner_bucket_sig:
            return self._scanner_bucket_cache

        anchor_idx: Optional[int] = None
        filtered: list[dict] = []
        for i, b in enumerate(combined):
            if float(b.get("start_time", 0.0)) >= anchor_unix:
                if anchor_idx is None:
                    anchor_idx = i
                filtered.append(b)
        if anchor_idx is None:
            anchor_idx = len(combined)

        x_indices: list[int] = list(range(len(filtered)))
        result = (filtered, x_indices, anchor_idx)
        self._scanner_bucket_sig = sig
        self._scanner_bucket_cache = result
        return result

    # ------------------------------------------------------------------
    # Phase 2: scanner draw dispatcher (per-mode bodies land in Phases 3-6)
    # ------------------------------------------------------------------
    def _draw_scanner(self) -> None:
        """Build the bucket array and dispatch to the active mode's renderer.

        Signature-gated (Phase 7): the heavy O(N)/O(N·50) recompute runs only when
        the bucket set, the live-edge volume, the Zero-Point anchor, or the mode
        actually change. In a thin market with no new trades the active bucket's
        curr_vol is unchanged, the sig matches, and we skip the entire render loop
        — idle CPU overhead drops to zero while depth/OI pulses keep flowing.
        """
        snap = self._last_snap or self.worker.snapshot()
        closed = snap.get("closed_buckets", []) or []
        active = snap.get("active_bucket") or {}
        current_sig = (len(closed), active.get("curr_vol", 0.0),
                       self.menu.scan_start_unix(), self.scanner_mode)
        if current_sig == self._last_scanner_sig:
            return   # nothing changed — skip the heavy recompute
        self._last_scanner_sig = current_sig

        filtered, x_indices, _anchor = self._build_scanner_buckets()
        if not filtered:
            # nothing past the Zero Point yet — leave a clean empty canvas
            return
        renderer = getattr(self, f"_scan_{self.scanner_mode}", None)
        if callable(renderer):
            renderer(filtered, x_indices)

    def _add_scanner_item(self, item: object) -> object:
        """Add a plot item and track it for teardown. All modes route through here."""
        self.plot.addItem(item)
        self.active_scanner_items.append(item)
        return item

    def _fit_scanner_y(self, x_len: int, lo: "Optional[float]" = None,
                       hi: "Optional[float]" = None,
                       clamp: "Optional[tuple]" = None) -> None:
        """One-shot fit of the scanner viewport (enhancement §1).

        Runs only when ``_scanner_needs_autofit`` is set (mode switch / anchor
        change), then flips the flag off so the 20Hz loop never snaps the canvas
        back — the user can zoom/pan/drag freely afterward. Ranges are computed
        explicitly from the scanner's own data (not enableAutoRange) so the hidden
        time-based items can't pollute the bounds.
        """
        if not self._scanner_needs_autofit:
            return
        if x_len > 0:
            self.vb.setXRange(-0.5, max(0.5, x_len - 0.5), padding=0.02)
        if clamp is not None:
            self.vb.setYRange(clamp[0], clamp[1], padding=0)
        elif lo is not None and hi is not None:
            if not (hi > lo):
                hi = lo + 1.0
            pad = (hi - lo) * 0.08
            self.vb.setYRange(lo - pad, hi + pad, padding=0)
        self._scanner_needs_autofit = False

    # ------------------------------------------------------------------
    # Polish-pass shared helpers (theme, value trackers, formatting)
    # ------------------------------------------------------------------
    def _apply_scanner_theme(self, dark: bool) -> None:
        """Morph the canvas between the dark scanner theme and light chart theme (§3)."""
        if dark:
            self.plot.setBackground("#141414")
            ax_pen = pg.mkPen("#dcdcdc", width=1)
            txt_pen = pg.mkPen("#dcdcdc")
        else:
            self.plot.setBackground(config.COLOR_CANVAS)
            ax_pen = pg.mkPen(config.COLOR_AXIS_TEXT, width=1)
            txt_pen = pg.mkPen(config.COLOR_AXIS_TEXT)
        for ax in ("bottom", "right", "left"):
            a = self.plot.getAxis(ax)
            a.setPen(ax_pen)
            a.setTextPen(txt_pen)
        # faint grid: white-ish on dark, gray on light (alpha ~30/255)
        self.plot.showGrid(x=True, y=True, alpha=0.12)

    @staticmethod
    def _fmt_k(v: float) -> str:
        """Compact thousands formatting for HUD/axis badges (e.g. 148K, -1.8K)."""
        if abs(v) >= 1000:
            return f"{v / 1000:.1f}K"
        return f"{v:.1f}"

    def _active_fill_pct(self) -> float:
        """Volume saturation of the pulsing active bucket: curr_vol / target_vol %."""
        snap = self._last_snap or {}
        tv = snap.get("target_vol") or config.DEFAULT_TARGET_VOL
        ab = snap.get("active_bucket") or {}
        cv = ab.get("curr_vol", 0.0)
        return (cv / tv * 100.0) if tv > 0 else 0.0

    # Right-docked anchors: x≈1.0 pins the badge's right edge against the Y-axis;
    # the y-fraction stacks converging values (up above, down below, mid centered).
    _TRACK_ANCHORS = {"up": (1.02, 1.0), "down": (1.02, 0.0), "mid": (1.02, 0.5)}

    def _scanner_tracker(self, key: str, value: float, color: str, text: str,
                         x_data: float, direction: str = "mid",
                         target_vb=None, line: bool = True,
                         span: bool = False) -> None:
        """Right-docked, TradingView-style axis tracker: a bold color-coded
        ``pg.TextItem`` badge pinned to the **right Y-axis edge** plus, optionally,
        a **finite** dashed rule that bridges only the gap from the live data point
        rightward to that edge — it never clutters the historical series to the left.

        * ``value`` — the metric Y (data coord); badge and rule sit at this Y.
        * ``x_data`` — the active (last) data point's X = the rule's LEFT endpoint.
          The rule's right end and the badge X are both the live viewport right edge
          (``x_max``), kept current under pan/zoom by :meth:`_redock_trackers`.
        * ``text`` — caller-built inner HTML (already ``<br>``-stacked); wrapped
          bold @13px in ``color`` here.
        * ``direction`` — vertical anchor: "up" floats the badge above the point,
          "down" below, "mid" centered, so converging vectors don't overlap.
        * ``span`` — True ⇒ a full-width ``pg.InfiniteLine`` instead of a finite
          segment (ONLY the Mode-10 live spot price line is allowed to cross the
          whole canvas; every other mode uses finite right-extended rules).
        * ``target_vb`` — route onto a secondary ViewBox (Mode-4 kinetic price);
          ``None`` ⇒ main plot + ``active_scanner_items`` for zero-leak teardown.
        """
        vb = target_vb if target_vb is not None else self.vb
        x_max = vb.viewRange()[0][1]          # live right-axis edge (viewport X-max)
        anchor = self._TRACK_ANCHORS.get(direction, self._TRACK_ANCHORS["mid"])
        html = (f"<div style='color:{color}; font-family:Consolas; font-size:13px; "
                f"font-weight:bold; white-space:nowrap'>{text}</div>")
        ln_key, tag_key = key + "_ln", key + "_tag"

        def _rule_pen():
            pen = pg.mkPen(color, width=1.6, style=QtCore.Qt.DashLine)
            pen.setCosmetic(True)             # crisp dashes regardless of zoom
            return pen

        if tag_key not in self._scan_handles:
            if line:
                if span:
                    rule = pg.InfiniteLine(
                        angle=0, movable=False, pos=value,
                        pen=pg.mkPen(color, width=1.5, style=QtCore.Qt.DashLine))
                else:
                    rule = pg.PlotCurveItem(x=[x_data, x_max], y=[value, value],
                                            pen=_rule_pen())
                rule.setZValue(55)
                if target_vb is not None:
                    target_vb.addItem(rule)
                else:
                    self._add_scanner_item(rule)
                self._scan_handles[ln_key] = rule
            tag = pg.TextItem(anchor=anchor)
            tag.setHtml(html)
            tag.setZValue(60)
            if target_vb is not None:
                target_vb.addItem(tag)
            else:
                self._add_scanner_item(tag)
            self._scan_handles[tag_key] = tag
        else:
            rule = self._scan_handles.get(ln_key)
            if line and rule is not None:
                if span:
                    rule.setPos(value)
                    rule.setPen(pg.mkPen(color, width=1.5, style=QtCore.Qt.DashLine))
                else:
                    rule.setData(x=[x_data, x_max], y=[value, value])
                    rule.setPen(_rule_pen())
            self._scan_handles[tag_key].setHtml(html)
        # dock the badge hard against the right Y-axis edge
        self._scan_handles[tag_key].setPos(x_max, value)

        # register for per-frame right-edge re-docking (so pan/zoom keeps the badge
        # and finite rule pinned even when the gated metric recompute is skipped).
        self._scan_trackers[key] = {
            "line": self._scan_handles.get(ln_key),
            "text": self._scan_handles[tag_key],
            "x_data": x_data, "value": value, "vb": vb, "span": span}

    def _redock_trackers(self) -> None:
        """Slide every active tracker badge + finite rule back onto the live right
        Y-axis edge each frame.

        :meth:`_draw_scanner` is signature-gated, so on a quiet feed a manual
        pan/zoom would otherwise strand the badges mid-canvas. This cheap pass
        re-reads each tracker's ViewBox right edge (``x_max``) and re-pins the badge
        (and the finite rule's right endpoint) to it. Mode-10's spanning spot line
        and tag-only trackers need no rule update.
        """
        if not self._scan_trackers:
            return
        for rec in self._scan_trackers.values():
            vb = rec.get("vb")
            if vb is None:
                continue
            try:
                x_max = vb.viewRange()[0][1]
            except Exception:
                continue
            y = rec["value"]
            tag = rec.get("text")
            if tag is not None:
                tag.setPos(x_max, y)
            rule = rec.get("line")
            if rule is not None and not rec.get("span"):
                try:
                    rule.setData(x=[rec["x_data"], x_max], y=[y, y])
                except Exception:
                    pass

    # ==================================================================
    # Phase 3: the six single-ViewBox scanner modes
    # ==================================================================
    def _scan_open_pos(self, buckets: list, x: list) -> None:
        """Mode 1 — cumulative Open Longs (green) / Open Shorts (red)."""
        cum_opL = cum_opS = cum_churn = 0.0
        opL_arr, opS_arr, churn_arr = [], [], []
        for b in buckets:
            cum_opL += b.get("opL", 0.0)
            cum_opS += b.get("opS", 0.0)
            cum_churn += b.get("churn", 0.0)             # Step 3: unattributed transfer
            opL_arr.append(cum_opL)
            opS_arr.append(cum_opS)
            churn_arr.append(cum_churn)

        if "opL" not in self._scan_handles:
            self._scan_handles["opL"] = self._add_scanner_item(
                pg.PlotCurveItem(pen=pg.mkPen("#2ecc71", width=2.0)))
            self._scan_handles["opS"] = self._add_scanner_item(
                pg.PlotCurveItem(pen=pg.mkPen("#e74c3c", width=2.0)))
            # Step 3: cumulative churn (unattributed transfer) as neutral context
            self._scan_handles["op_churn"] = self._add_scanner_item(
                pg.PlotCurveItem(pen=pg.mkPen("#9aa0aa", width=1.5, style=QtCore.Qt.DashLine)))
        self._scan_handles["opL"].setData(x, opL_arr)
        self._scan_handles["opS"].setData(x, opS_arr)
        self._scan_handles["op_churn"].setData(x, churn_arr)

        vals = [0.0] + opL_arr + opS_arr + churn_arr
        self._fit_scanner_y(len(x), min(vals), max(vals))
        xr = x[-1]
        tot = max(0.1, opL_arr[-1] + opS_arr[-1])
        self._scanner_tracker("t_opL", opL_arr[-1], "#2ecc71",
            f"opL {self._fmt_k(opL_arr[-1])}<br>({opL_arr[-1] / tot * 100:.0f}%)", xr, "up")
        self._scanner_tracker("t_opS", opS_arr[-1], "#e74c3c",
            f"opS {self._fmt_k(opS_arr[-1])}<br>({opS_arr[-1] / tot * 100:.0f}%)", xr, "down")
        self._scanner_tracker("t_op_churn", churn_arr[-1], "#9aa0aa",
            f"churn {self._fmt_k(churn_arr[-1])}", xr, "mid")

    def _scan_close_pos(self, buckets: list, x: list) -> None:
        """Mode 2 — cumulative Close Shorts (blue) / Close Longs (purple)."""
        cum_clS = cum_clL = cum_churn = 0.0
        clS_arr, clL_arr, churn_arr = [], [], []
        for b in buckets:
            cum_clS += b.get("clS", 0.0)
            cum_clL += b.get("clL", 0.0)
            cum_churn += b.get("churn", 0.0)             # Step 3: unattributed transfer
            clS_arr.append(cum_clS)
            clL_arr.append(cum_clL)
            churn_arr.append(cum_churn)

        if "clS" not in self._scan_handles:
            self._scan_handles["clS"] = self._add_scanner_item(
                pg.PlotCurveItem(pen=pg.mkPen("#3498db", width=2.0)))
            self._scan_handles["clL"] = self._add_scanner_item(
                pg.PlotCurveItem(pen=pg.mkPen("#9b59b6", width=2.0)))
            # Step 3: cumulative churn (unattributed transfer) as neutral context
            self._scan_handles["cl_churn"] = self._add_scanner_item(
                pg.PlotCurveItem(pen=pg.mkPen("#9aa0aa", width=1.5, style=QtCore.Qt.DashLine)))
        self._scan_handles["clS"].setData(x, clS_arr)
        self._scan_handles["clL"].setData(x, clL_arr)
        self._scan_handles["cl_churn"].setData(x, churn_arr)

        vals = [0.0] + clS_arr + clL_arr + churn_arr
        self._fit_scanner_y(len(x), min(vals), max(vals))
        xr = x[-1]
        tot = max(0.1, clS_arr[-1] + clL_arr[-1])
        self._scanner_tracker("t_clS", clS_arr[-1], "#3498db",
            f"clS {self._fmt_k(clS_arr[-1])}<br>({clS_arr[-1] / tot * 100:.0f}%)", xr, "up")
        self._scanner_tracker("t_clL", clL_arr[-1], "#9b59b6",
            f"clL {self._fmt_k(clL_arr[-1])}<br>({clL_arr[-1] / tot * 100:.0f}%)", xr, "down")
        self._scanner_tracker("t_cl_churn", churn_arr[-1], "#9aa0aa",
            f"churn {self._fmt_k(churn_arr[-1])}", xr, "mid")

    def _scan_exhaustion(self, buckets: list, x: list) -> None:
        """Mode 3 — CVD-extreme exhaustion x smooth z-score E/R + delta_oi
        multipliers (Step 5), clamped 0..100."""
        cvd = 0.0
        max_cvd = 0.0
        min_cvd = 0.0
        bExh_arr, sExh_arr = [], []
        for i, b in enumerate(buckets):
            cvd += b.get("buy_vol", 0.0) - b.get("sell_vol", 0.0)
            max_cvd = max(max_cvd, cvd)
            min_cvd = min(min_cvd, cvd)
            cvd_span = max(0.001, max_cvd - min_cvd)
            b_base = (max_cvd - cvd) / cvd_span * 100.0
            s_base = (cvd - min_cvd) / cvd_span * 100.0

            # DIVERGES FROM LEGACY (Step 5): smooth, scale-free z-score E/R
            # multipliers + a delta_oi-direction term replace the rigid absolute
            # er>150/300 tiers and the 1.8/1.3/0.8 / 1.5/0.7 ladders.
            b_mult, s_mult, oi_mult = _exhaustion_mults(buckets, i)

            bExh_arr.append(max(0.0, min(100.0, b_base * oi_mult * b_mult)))
            sExh_arr.append(max(0.0, min(100.0, s_base * oi_mult * s_mult)))

        if "bExh" not in self._scan_handles:
            self._scan_handles["bExh"] = self._add_scanner_item(
                pg.PlotCurveItem(pen=pg.mkPen("#3498db", width=2.0)))
            self._scan_handles["sExh"] = self._add_scanner_item(
                pg.PlotCurveItem(pen=pg.mkPen("#ef5350", width=2.0)))
        self._scan_handles["bExh"].setData(x, bExh_arr)
        self._scan_handles["sExh"].setData(x, sExh_arr)

        self._fit_scanner_y(len(x), clamp=(-5.0, 105.0))
        xr = x[-1]
        # exhaustion values are natively percentages (0..100)
        self._scanner_tracker("t_bExh", bExh_arr[-1], "#3498db",
            f"BExh<br>{bExh_arr[-1]:.0f}%", xr, "up")
        self._scanner_tracker("t_sExh", sExh_arr[-1], "#ef5350",
            f"SExh<br>{sExh_arr[-1]:.0f}%", xr, "down")

    def _scan_volume(self, buckets: list, x: list) -> None:
        """Mode 5 — cumulative buyer (teal) vs seller (red) raw volume."""
        cum_bVol = cum_sVol = 0.0
        bVol_arr, sVol_arr = [], []
        for b in buckets:
            cum_bVol += b.get("buy_vol", 0.0)
            cum_sVol += b.get("sell_vol", 0.0)
            bVol_arr.append(cum_bVol)
            sVol_arr.append(cum_sVol)

        if "bVol" not in self._scan_handles:
            self._scan_handles["bVol"] = self._add_scanner_item(
                pg.PlotCurveItem(pen=pg.mkPen("#26a69a", width=2.0)))
            self._scan_handles["sVol"] = self._add_scanner_item(
                pg.PlotCurveItem(pen=pg.mkPen("#ef5350", width=2.0)))
        self._scan_handles["bVol"].setData(x, bVol_arr)
        self._scan_handles["sVol"].setData(x, sVol_arr)

        vals = [0.0] + bVol_arr + sVol_arr
        self._fit_scanner_y(len(x), min(vals), max(vals))
        xr = x[-1]
        tot = max(0.1, bVol_arr[-1] + sVol_arr[-1])
        self._scanner_tracker("t_bVol", bVol_arr[-1], "#26a69a",
            f"Buy {self._fmt_k(bVol_arr[-1])}<br>({bVol_arr[-1] / tot * 100:.0f}%)", xr, "up")
        self._scanner_tracker("t_sVol", sVol_arr[-1], "#ef5350",
            f"Sell {self._fmt_k(sVol_arr[-1])}<br>({sVol_arr[-1] / tot * 100:.0f}%)", xr, "down")

    def _scan_vpin(self, buckets: list, x: list) -> None:
        """Mode 6 — true rolling N=50 VPIN, color-shifting bars + 0.85 risk line."""
        vpin_arr = []
        n = len(buckets)
        for i in range(n):
            window = buckets[max(0, i - 49): i + 1]
            total_imbalance = sum(abs(b.get("buy_vol", 0.0) - b.get("sell_vol", 0.0))
                                  for b in window)
            total_volume = sum(b.get("curr_vol", 0.0) for b in window)
            vpin_arr.append(total_imbalance / total_volume if total_volume > 0 else 0.0)

        brushes = []
        for v in vpin_arr:
            if v >= 0.85:
                brushes.append(pg.mkBrush("#ff073a"))    # toxic crimson
            elif v >= 0.50:
                brushes.append(pg.mkBrush("#f1c40f"))    # warning gold
            else:
                brushes.append(pg.mkBrush("#555555"))    # muted charcoal

        if "vpin" not in self._scan_handles:
            self._scan_handles["vpin"] = self._add_scanner_item(
                pg.BarGraphItem(x=x, height=vpin_arr, width=0.8, brushes=brushes, pen=None))
            self._scan_handles["vpin_line"] = self._add_scanner_item(
                pg.InfiniteLine(pos=0.85, angle=0,
                                pen=pg.mkPen("#ff073a", style=QtCore.Qt.DashLine, width=2)))
        else:
            self._scan_handles["vpin"].setOpts(x=x, height=vpin_arr, width=0.8,
                                               brushes=brushes, pen=None)
        self._fit_scanner_y(len(x), clamp=(0.0, 1.05))
        v = vpin_arr[-1]
        col = "#ff073a" if v >= 0.85 else ("#f1c40f" if v >= 0.50 else "#999999")
        self._scanner_tracker("t_vpin", v, col, f"VPIN {v:.2f}<br>({v * 100:.0f}%)",
                              x[-1], "mid")

    def _scan_effort_result(self, buckets: list, x: list) -> None:
        """Mode 9 — mirrored friction: buyer E/R up (green), seller E/R down (red)."""
        bER_arr = [b.get("buyer_er", 0.0) for b in buckets]
        sER_arr = [-b.get("seller_er", 0.0) for b in buckets]   # mirror downward

        if "bER" not in self._scan_handles:
            self._scan_handles["bER"] = self._add_scanner_item(
                pg.BarGraphItem(x=x, height=bER_arr, width=0.8,
                                brush=pg.mkBrush("#2ecc71"), pen=None))
            self._scan_handles["sER"] = self._add_scanner_item(
                pg.BarGraphItem(x=x, height=sER_arr, width=0.8,
                                brush=pg.mkBrush("#e74c3c"), pen=None))
        else:
            self._scan_handles["bER"].setOpts(x=x, height=bER_arr, width=0.8, pen=None)
            self._scan_handles["sER"].setOpts(x=x, height=sER_arr, width=0.8, pen=None)

        vals = [0.0] + bER_arr + sER_arr
        self._fit_scanner_y(len(x), min(vals), max(vals))
        xr = x[-1]
        ber, ser = bER_arr[-1], abs(sER_arr[-1])
        tot = max(0.1, ber + ser)
        self._scanner_tracker("t_bER", bER_arr[-1], "#2ecc71",
            f"BuyER {self._fmt_k(ber)}<br>({ber / tot * 100:.0f}%)", xr, "up")
        self._scanner_tracker("t_sER", sER_arr[-1], "#e74c3c",
            f"SellER {self._fmt_k(ser)}<br>({ser / tot * 100:.0f}%)", xr, "down")

    # ==================================================================
    # Phase 4: Neon engine + mirrored micro-intent histograms
    # ==================================================================
    def _bucket_vel_ratios(self, buckets: list) -> "list[float]":
        """Per-bucket velocity ratio vs a rolling 20-bucket SMA (feeds the neon engine).

        current_vel = (buy_vol + sell_vol) / duration; baseline_vel = mean of the
        last 20 current_vel values (inclusive); vel_ratio = current_vel / max(0.1,
        baseline_vel). A vel_ratio >= 2.5 marks an HFT cascade -> neon override.
        """
        vels: list[float] = []
        for b in buckets:
            duration = max(1.0, b.get("end_time", 0.0) - b.get("start_time", 0.0))
            vels.append((b.get("buy_vol", 0.0) + b.get("sell_vol", 0.0)) / duration)

        ratios: list[float] = []
        for i in range(len(vels)):
            window = vels[max(0, i - 19): i + 1]
            baseline = (sum(window) / len(window)) if window else 1.0
            ratios.append(vels[i] / max(0.1, baseline))
        return ratios

    def _neon_brush(self, main_val: float, opp_val: float,
                    color_type: str, vel_ratio: float) -> "pg.QtGui.QBrush":
        """Dominance-opacity brush with an HFT neon override (vel_ratio >= 2.5).

        Standard tone alpha scales with directional dominance (0.15..1.0). On a
        velocity cascade, alpha snaps to 1.0 and the tone swaps to raw neon. The
        opening vs closing branches are now SEPARATED (§4) so opening intents read
        distinctly from short-covering / long-liquidation cycles:
            green  (opL) -> neon green   (0, 255, 127)
            red    (opS) -> neon red     (255, 7, 58)
            blue   (clS) -> neon cyan    (0, 255, 255)   [unchanged]
            purple (clL) -> hot magenta  (255, 0, 255)   [unchanged]
        """
        total = main_val + opp_val
        dom = (main_val - opp_val) / total if total > 0 else 0.0
        alpha = 0.15 + max(0.0, dom) * 0.85
        alpha = max(0.15, min(1.0, alpha))

        if vel_ratio >= 2.5:   # HFT overload — neon override (per-type, not grouped)
            neon = {
                "green": (0, 255, 127),    # opL  -> neon green
                "red": (255, 7, 58),       # opS  -> neon red / crimson
                "blue": (0, 255, 255),     # clS  -> neon cyan
                "purple": (255, 0, 255),   # clL  -> hot magenta
            }[color_type]
            return pg.mkBrush((neon[0], neon[1], neon[2], 255))

        std = {
            "green": (46, 204, 113),
            "red": (231, 76, 60),
            "blue": (52, 152, 219),
            "purple": (155, 89, 182),
        }[color_type]
        a = int(round(alpha * 255))
        return pg.mkBrush((std[0], std[1], std[2], a))

    def _scan_bucket_open_pos(self, buckets: list, x: list) -> None:
        """Mode 7 — mirrored open intents: opL up (green/cyan), opS down (red/magenta)."""
        self._ensure_pulse_churn_vb()
        ratios = self._bucket_vel_ratios(buckets)
        opL_arr, neg_opS_arr, churn_arr = [], [], []
        opL_brushes, opS_brushes = [], []
        for i, b in enumerate(buckets):
            opL = b.get("opL", 0.0)
            opS = b.get("opS", 0.0)
            vr = ratios[i]
            opL_arr.append(opL)
            neg_opS_arr.append(-opS)                       # mirror downward
            churn_arr.append(b.get("churn", 0.0))          # Step 3: per-bucket unattributed
            opL_brushes.append(self._neon_brush(opL, opS, "green", vr))
            opS_brushes.append(self._neon_brush(opS, opL, "red", vr))

        if "b_opL" not in self._scan_handles:
            self._scan_handles["b_opL"] = self._add_scanner_item(
                pg.BarGraphItem(x=x, height=opL_arr, width=0.8, brushes=opL_brushes, pen=None))
            self._scan_handles["b_opS"] = self._add_scanner_item(
                pg.BarGraphItem(x=x, height=neg_opS_arr, width=0.8, brushes=opS_brushes, pen=None))
            # Step 3: per-bucket churn lives on the SECONDARY vb (own scale) so it
            # can never crush the heartbeat bars; dies with that vb on teardown.
            churn_line = pg.PlotCurveItem(
                pen=pg.mkPen("#9aa0aa", width=1.5, style=QtCore.Qt.DashLine))
            self.vb_pulse_churn.addItem(churn_line)
            self._scan_handles["b_op_churn"] = churn_line
        else:
            self._scan_handles["b_opL"].setOpts(x=x, height=opL_arr, width=0.8,
                                                brushes=opL_brushes, pen=None)
            self._scan_handles["b_opS"].setOpts(x=x, height=neg_opS_arr, width=0.8,
                                                brushes=opS_brushes, pen=None)
        self._scan_handles["b_op_churn"].setData(x, churn_arr)

        # bars fit to THEIR own (symmetric) scale; churn excluded so it can't
        # dominate. churn then rides the secondary vb with 0 aligned to the
        # baseline, so it visibly collapses toward the bars when real flow ignites.
        bar_vals = opL_arr + neg_opS_arr
        m = max(0.1, abs(min(bar_vals)), abs(max(bar_vals)))
        self._fit_scanner_y(len(x), clamp=(-m, m))
        cmax = max(1.0, max(churn_arr) if churn_arr else 1.0)
        self.vb_pulse_churn.setYRange(-cmax * 1.08, cmax * 1.08, padding=0)
        self._sync_pulse_churn_vb()

        xr = x[-1]
        opL_last, opS_last = opL_arr[-1], -neg_opS_arr[-1]
        tot = max(0.1, opL_last + opS_last)
        self._scanner_tracker("t_opL", opL_arr[-1], "#2ecc71",
            f"opL {self._fmt_k(opL_last)}<br>({opL_last / tot * 100:.0f}%)", xr, "up")
        self._scanner_tracker("t_opS", neg_opS_arr[-1], "#e74c3c",
            f"opS {self._fmt_k(opS_last)}<br>({opS_last / tot * 100:.0f}%)", xr, "down")
        self._scanner_tracker("t_b_op_churn", churn_arr[-1], "#9aa0aa",
            f"churn {self._fmt_k(churn_arr[-1])}", xr, "mid",
            target_vb=self.vb_pulse_churn, line=False)

    def _scan_bucket_close_pos(self, buckets: list, x: list) -> None:
        """Mode 8 — mirrored close intents: clS up (blue/cyan), clL down (purple/magenta)."""
        self._ensure_pulse_churn_vb()
        ratios = self._bucket_vel_ratios(buckets)
        clS_arr, neg_clL_arr, churn_arr = [], [], []
        clS_brushes, clL_brushes = [], []
        for i, b in enumerate(buckets):
            clS = b.get("clS", 0.0)
            clL = b.get("clL", 0.0)
            vr = ratios[i]
            clS_arr.append(clS)
            neg_clL_arr.append(-clL)                       # mirror downward
            churn_arr.append(b.get("churn", 0.0))          # Step 3: per-bucket unattributed
            clS_brushes.append(self._neon_brush(clS, clL, "blue", vr))
            clL_brushes.append(self._neon_brush(clL, clS, "purple", vr))

        if "b_clS" not in self._scan_handles:
            self._scan_handles["b_clS"] = self._add_scanner_item(
                pg.BarGraphItem(x=x, height=clS_arr, width=0.8, brushes=clS_brushes, pen=None))
            self._scan_handles["b_clL"] = self._add_scanner_item(
                pg.BarGraphItem(x=x, height=neg_clL_arr, width=0.8, brushes=clL_brushes, pen=None))
            # Step 3: per-bucket churn lives on the SECONDARY vb (own scale) so it
            # can never crush the heartbeat bars; dies with that vb on teardown.
            churn_line = pg.PlotCurveItem(
                pen=pg.mkPen("#9aa0aa", width=1.5, style=QtCore.Qt.DashLine))
            self.vb_pulse_churn.addItem(churn_line)
            self._scan_handles["b_cl_churn"] = churn_line
        else:
            self._scan_handles["b_clS"].setOpts(x=x, height=clS_arr, width=0.8,
                                                brushes=clS_brushes, pen=None)
            self._scan_handles["b_clL"].setOpts(x=x, height=neg_clL_arr, width=0.8,
                                                brushes=clL_brushes, pen=None)
        self._scan_handles["b_cl_churn"].setData(x, churn_arr)

        # bars fit to THEIR own (symmetric) scale; churn excluded so it can't
        # dominate. churn then rides the secondary vb with 0 aligned to the
        # baseline, so it visibly collapses toward the bars when real flow ignites.
        bar_vals = clS_arr + neg_clL_arr
        m = max(0.1, abs(min(bar_vals)), abs(max(bar_vals)))
        self._fit_scanner_y(len(x), clamp=(-m, m))
        cmax = max(1.0, max(churn_arr) if churn_arr else 1.0)
        self.vb_pulse_churn.setYRange(-cmax * 1.08, cmax * 1.08, padding=0)
        self._sync_pulse_churn_vb()

        xr = x[-1]
        clS_last, clL_last = clS_arr[-1], -neg_clL_arr[-1]
        tot = max(0.1, clS_last + clL_last)
        self._scanner_tracker("t_clS", clS_arr[-1], "#3498db",
            f"clS {self._fmt_k(clS_last)}<br>({clS_last / tot * 100:.0f}%)", xr, "up")
        self._scanner_tracker("t_clL", neg_clL_arr[-1], "#9b59b6",
            f"clL {self._fmt_k(clL_last)}<br>({clL_last / tot * 100:.0f}%)", xr, "down")
        self._scanner_tracker("t_b_cl_churn", churn_arr[-1], "#9aa0aa",
            f"churn {self._fmt_k(churn_arr[-1])}", xr, "mid",
            target_vb=self.vb_pulse_churn, line=False)

    # ==================================================================
    # Phase 5: Mode 4 — Kinetic Strength & Price Forecast (dual-axis)
    # ==================================================================
    def _ensure_kinetic_vb(self) -> None:
        """Lazily build the secondary, X-linked price ViewBox for the forecast cloud."""
        if self.vb_kinetic_price is not None:
            return
        self.vb_kinetic_price = pg.ViewBox()
        self.plot.scene().addItem(self.vb_kinetic_price)
        self.vb_kinetic_price.setXLink(self.plot.getViewBox())   # share the bucket X-axis
        self.vb_kinetic_price.setMenuEnabled(False)              # (spec said setEnableMenu — real API)
        # mouse events fall through to the primary viewport
        self.vb_kinetic_price.setMouseEnabled(x=False, y=False)
        self.plot.getViewBox().sigResized.connect(self._sync_kinetic_vb)
        self._sync_kinetic_vb()

    def _scan_kinetic(self, buckets: list, x: list) -> None:
        """Mode 4 — kinetic energy histograms (primary vb) + EMA price-forecast
        cloud lines (secondary X-linked price vb). Two coordinate spaces, one canvas.
        """
        self._ensure_kinetic_vb()
        ratios = self._bucket_vel_ratios(buckets)

        bull_arr, neg_bear_arr = [], []
        bull_brushes, bear_brushes = [], []
        baseline_arr, bull_fc_arr, bear_fc_arr = [], [], []
        baseline = 0.0
        bull_stretch = 0.0
        bear_stretch = 0.0

        for i, b in enumerate(buckets):
            duration = max(1.0, b.get("end_time", 0.0) - b.get("start_time", 0.0))
            v_bull = b.get("buy_vol", 0.0) / duration
            v_bear = b.get("sell_vol", 0.0) / duration
            bull_kinetic = v_bull * (b.get("buyer_er", 0.0) / 100.0)
            bear_kinetic = v_bear * (b.get("seller_er", 0.0) / 100.0)

            denom = max(1.0, v_bull + v_bear)
            bull_stretch_raw = (bull_kinetic / denom) * 0.5
            bear_stretch_raw = (bear_kinetic / denom) * 0.5

            if i == 0:   # hard-anchor the EMA trackers to the first bucket
                baseline = b.get("poc_price", 0.0)
                bull_stretch = bull_stretch_raw
                bear_stretch = bear_stretch_raw
            else:
                baseline = (b.get("poc_price", 0.0) * 0.05) + (baseline * 0.95)
                bull_stretch = (bull_stretch_raw * 0.1) + (bull_stretch * 0.9)
                bear_stretch = (bear_stretch_raw * 0.1) + (bear_stretch * 0.9)

            bull_arr.append(bull_kinetic)
            neg_bear_arr.append(-bear_kinetic)
            baseline_arr.append(baseline)
            bull_fc_arr.append(baseline + bull_stretch)
            bear_fc_arr.append(baseline - bear_stretch)

            # velocity-scaled bar opacity (independent of the neon dominance engine)
            alpha = int(min(1.0, max(0.2, ratios[i] / 2.5)) * 255)
            bull_brushes.append(pg.mkBrush((38, 166, 154, alpha)))
            bear_brushes.append(pg.mkBrush((239, 83, 80, alpha)))

        # --- primary ViewBox: kinetic energy histograms ---
        if "k_bull" not in self._scan_handles:
            self._scan_handles["k_bull"] = self._add_scanner_item(
                pg.BarGraphItem(x=x, height=bull_arr, width=0.8, brushes=bull_brushes, pen=None))
            self._scan_handles["k_bear"] = self._add_scanner_item(
                pg.BarGraphItem(x=x, height=neg_bear_arr, width=0.8, brushes=bear_brushes, pen=None))
        else:
            self._scan_handles["k_bull"].setOpts(x=x, height=bull_arr, width=0.8,
                                                 brushes=bull_brushes, pen=None)
            self._scan_handles["k_bear"].setOpts(x=x, height=neg_bear_arr, width=0.8,
                                                 brushes=bear_brushes, pen=None)

        # --- secondary ViewBox: price forecast cloud lines (added to vb directly) ---
        if "k_baseline" not in self._scan_handles:
            # light gray, legible on the dark scanner canvas (§3), dashed w1.5
            base_pen = pg.mkPen((180, 180, 180, 150), width=1.5, style=QtCore.Qt.DashLine)
            c_base = pg.PlotCurveItem(pen=base_pen)
            c_bull = pg.PlotCurveItem(pen=pg.mkPen("#2ecc71", width=2.5))
            c_bear = pg.PlotCurveItem(pen=pg.mkPen("#e74c3c", width=2.5))
            # NOT via _add_scanner_item — these live on the secondary canvas node and
            # are destroyed with it during teardown; tracked in _scan_handles only.
            self.vb_kinetic_price.addItem(c_base)
            self.vb_kinetic_price.addItem(c_bull)
            self.vb_kinetic_price.addItem(c_bear)
            self._scan_handles["k_baseline"] = c_base
            self._scan_handles["k_bull_fc"] = c_bull
            self._scan_handles["k_bear_fc"] = c_bear
        self._scan_handles["k_baseline"].setData(x, baseline_arr)
        self._scan_handles["k_bull_fc"].setData(x, bull_fc_arr)
        self._scan_handles["k_bear_fc"].setData(x, bear_fc_arr)

        # --- deterministic dual-Y scaling ---
        kin_vals = [0.0] + bull_arr + neg_bear_arr
        self._fit_scanner_y(len(x), min(kin_vals), max(kin_vals))
        xr = x[-1]
        # v_bull / v_bear hold the LAST bucket's raw velocities after the loop
        tot_v = max(0.1, v_bull + v_bear)
        self._scanner_tracker("t_kbull", bull_arr[-1], "#26a69a",
            f"vBull {self._fmt_k(bull_arr[-1])}<br>({v_bull / tot_v * 100:.0f}%)", xr, "up")
        self._scanner_tracker("t_kbear", neg_bear_arr[-1], "#ef5350",
            f"vBear {self._fmt_k(abs(neg_bear_arr[-1]))}<br>({v_bear / tot_v * 100:.0f}%)", xr, "down")

        price_vals = baseline_arr + bull_fc_arr + bear_fc_arr
        if price_vals:
            self.vb_kinetic_price.setYRange(min(price_vals), max(price_vals), padding=0.05)
        # forecast value tags on the SECONDARY price vb (tag-only; the cloud curves
        # already supply the lines). Directional anchors keep them from colliding.
        self._scanner_tracker("t_fbase", baseline_arr[-1], "#b4b4b4",
            f"Base ${baseline_arr[-1]:.2f}", xr, "mid",
            target_vb=self.vb_kinetic_price, line=False)
        self._scanner_tracker("t_fbull", bull_fc_arr[-1], "#2ecc71",
            f"Bull ${bull_fc_arr[-1]:.2f}", xr, "up",
            target_vb=self.vb_kinetic_price, line=False)
        self._scanner_tracker("t_fbear", bear_fc_arr[-1], "#e74c3c",
            f"Bear ${bear_fc_arr[-1]:.2f}", xr, "down",
            target_vb=self.vb_kinetic_price, line=False)
        self._sync_kinetic_vb()   # keep geometry glued to the primary viewport

    # ==================================================================
    # Phase 6: Mode 10 — Bucket Candlestick & Unified Flow Canvas (dual pane)
    # ==================================================================
    def _ensure_canvas_panes(self) -> None:
        """Build the stacked dual-pane workspace: reparent the main plot into a
        vertical splitter and add the lower VPIN sub-pane, X-linked to the chart."""
        if self.lower_plot is not None:
            return
        self.splitter_v = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        # reparent the primary chart out of the horizontal splitter into the upper track
        self.splitter_v.addWidget(self.plot)

        self.lower_plot = pg.PlotWidget(axisItems={"bottom": LocalTimeAxis(orientation="bottom"),
                                                   "right": PriceAxis(orientation="right")})
        self.lower_plot.setBackground("#141414")   # dark scanner theme (§3)
        self.lower_plot.showAxis("right"); self.lower_plot.hideAxis("left")
        for _ax in ("bottom", "right"):
            self.lower_plot.getAxis(_ax).setPen(pg.mkPen("#dcdcdc", width=1))
            self.lower_plot.getAxis(_ax).setTextPen(pg.mkPen("#dcdcdc"))
        self.lower_plot.showGrid(x=True, y=True, alpha=0.12)
        self.lower_plot.setMenuEnabled(False)
        self.lower_plot.getAxis("bottom").set_scanner_active(True)
        self.lower_plot.getViewBox().setMouseEnabled(x=True, y=False)
        # autorange OFF so the X-link (not the VPIN bars' own bounds) drives X,
        # and the hard Y-clamp below isn't overridden.
        self.lower_plot.getViewBox().disableAutoRange()
        self.lower_plot.getViewBox().setYRange(0.0, 1.05, padding=0)
        self.splitter_v.addWidget(self.lower_plot)
        self.splitter_v.setStretchFactor(0, 3)   # 75% upper price space
        self.splitter_v.setStretchFactor(1, 1)   # 25% lower toxicity space

        # re-inject the vertical splitter into the horizontal splitter at index 0
        self.splitter.insertWidget(0, self.splitter_v)
        self.splitter.setStretchFactor(0, 1)

        # Horizontal lock is enforced deterministically every frame in
        # _scan_bucket_canvas (mirror main X -> lower X). We deliberately do NOT
        # use setXLink here: combined with the per-frame mirror it double-controls
        # the range and leaves a padding mismatch, and its propagation is unreliable
        # under offscreen/deferred-paint conditions. The explicit mirror is exact.

    def _neon_v2_brush(self, opL: float, opS: float, clL: float, clS: float,
                       vel_ratio: float) -> "pg.QtGui.QBrush":
        """Candle-body brush from the dominant 4-vector + dominance opacity, with
        the HFT (vel_ratio >= 2.5) neon override (Neon Engine V2)."""
        vectors = {"opL": opL, "opS": opS, "clL": clL, "clS": clS}
        pair = {"opL": "opS", "opS": "opL", "clS": "clL", "clL": "clS"}
        main_key = max(vectors, key=vectors.get)
        main_val = vectors[main_key]
        opp_val = vectors[pair[main_key]]
        total = main_val + opp_val
        dom = (main_val - opp_val) / total if total > 0 else 0.0
        alpha = int((0.15 + max(0.0, dom) * 0.85) * 255)
        neon = vel_ratio >= 2.5
        if neon:
            alpha = 255
        palette = {
            "opL": ((46, 204, 113), (0, 255, 127)),     # green / NEON GREEN (§4)
            "opS": ((231, 76, 60), (255, 7, 58)),       # red / NEON RED/CRIMSON (§4)
            "clS": ((52, 152, 219), (0, 255, 255)),     # blue / neon cyan (unchanged)
            "clL": ((155, 89, 182), (255, 0, 255)),     # purple / hot magenta (unchanged)
        }
        std, neon_col = palette[main_key]
        if neon:
            return pg.mkBrush((neon_col[0], neon_col[1], neon_col[2], 255))
        return pg.mkBrush((std[0], std[1], std[2], alpha))

    def _scan_bucket_canvas(self, buckets: list, x: list) -> None:
        """Mode 10 — neon-graded bucket candles + kinetic forecast (upper pane)
        synchronized with a rolling-50 VPIN toxicity heatmap (lower pane)."""
        self._ensure_canvas_panes()
        ratios = self._bucket_vel_ratios(buckets)

        opens, highs, lows, closes, brushes = [], [], [], [], []
        baseline_arr, bull_fc_arr, bear_fc_arr = [], [], []
        baseline = 0.0
        bull_stretch = 0.0
        bear_stretch = 0.0

        for i, b in enumerate(buckets):
            opens.append(b.get("open", 0.0))
            highs.append(b.get("high", 0.0))
            lows.append(b.get("low", 0.0))
            closes.append(b.get("close", 0.0))
            brushes.append(self._neon_v2_brush(
                b.get("opL", 0.0), b.get("opS", 0.0), b.get("clL", 0.0),
                b.get("clS", 0.0), ratios[i]))

            # kinetic forecast (identical EMA matrix to Mode 4)
            duration = max(1.0, b.get("end_time", 0.0) - b.get("start_time", 0.0))
            v_bull = b.get("buy_vol", 0.0) / duration
            v_bear = b.get("sell_vol", 0.0) / duration
            bull_kinetic = v_bull * (b.get("buyer_er", 0.0) / 100.0)
            bear_kinetic = v_bear * (b.get("seller_er", 0.0) / 100.0)
            denom = max(1.0, v_bull + v_bear)
            bull_stretch_raw = (bull_kinetic / denom) * 0.5
            bear_stretch_raw = (bear_kinetic / denom) * 0.5
            if i == 0:
                baseline = b.get("poc_price", 0.0)
                bull_stretch = bull_stretch_raw
                bear_stretch = bear_stretch_raw
            else:
                baseline = (b.get("poc_price", 0.0) * 0.05) + (baseline * 0.95)
                bull_stretch = (bull_stretch_raw * 0.1) + (bull_stretch * 0.9)
                bear_stretch = (bear_stretch_raw * 0.1) + (bear_stretch * 0.9)
            baseline_arr.append(baseline)
            bull_fc_arr.append(baseline + bull_stretch)
            bear_fc_arr.append(baseline - bear_stretch)

        # rolling N=50 VPIN for the lower pane
        vpin_arr = []
        for i in range(len(buckets)):
            window = buckets[max(0, i - 49): i + 1]
            ti = sum(abs(bb.get("buy_vol", 0.0) - bb.get("sell_vol", 0.0)) for bb in window)
            tv = sum(bb.get("curr_vol", 0.0) for bb in window)
            vpin_arr.append(ti / tv if tv > 0 else 0.0)
        vbrushes = []
        for v in vpin_arr:
            if v >= 0.85:
                vbrushes.append(pg.mkBrush("#ff073a"))
            elif v >= 0.50:
                vbrushes.append(pg.mkBrush("#f1c40f"))
            else:
                vbrushes.append(pg.mkBrush("#555555"))

        # --- upper pane: candles + forecast cloud (create-once / update-after) ---
        if "bc_candles" not in self._scan_handles:
            self._scan_handles["bc_candles"] = self._add_scanner_item(BucketCandleItem())
            self._scan_handles["bc_baseline"] = self._add_scanner_item(
                pg.PlotCurveItem(pen=pg.mkPen((180, 180, 180, 150), width=1.5,
                                              style=QtCore.Qt.DashLine)))
            self._scan_handles["bc_bull"] = self._add_scanner_item(
                pg.PlotCurveItem(pen=pg.mkPen("#2ecc71", width=2.5)))
            self._scan_handles["bc_bear"] = self._add_scanner_item(
                pg.PlotCurveItem(pen=pg.mkPen("#e74c3c", width=2.5)))
        self._scan_handles["bc_candles"].update_data(x, opens, highs, lows, closes, brushes, 0.8)
        self._scan_handles["bc_baseline"].setData(x, baseline_arr)
        self._scan_handles["bc_bull"].setData(x, bull_fc_arr)
        self._scan_handles["bc_bear"].setData(x, bear_fc_arr)

        # --- order blocks mapped onto the integer bucket grid (§6.1) ---
        if "bc_obs" not in self._scan_handles:
            self.bc_obs.setZValue(-5)          # zones render behind the candles
            self._add_scanner_item(self.bc_obs)
            self._scan_handles["bc_obs"] = self.bc_obs
        start_times = [b.get("start_time", 0.0) for b in buckets]

        def _ts_to_idx(ts: float) -> int:
            # nearest bucket ordinal active at `ts`; -1 if before the first bucket
            i = bisect.bisect_right(start_times, ts) - 1
            return -1 if i < 0 else min(i, len(start_times) - 1)

        self.bc_obs.visible_filter = self.ob_item.visible_filter   # honor the Min-Mult slider
        self.bc_obs.update_data_indexed(
            self._last_snap.get("order_blocks", []), float(x[-1]), _ts_to_idx)

        # --- lower pane: VPIN heatmap + 0.85 risk line (live on lower_plot) ---
        if "bc_vpin" not in self._scan_handles:
            self._scan_handles["bc_vpin"] = pg.BarGraphItem(
                x=x, height=vpin_arr, width=0.8, brushes=vbrushes, pen=None)
            self.lower_plot.addItem(self._scan_handles["bc_vpin"])
            line = pg.InfiniteLine(pos=0.85, angle=0,
                                   pen=pg.mkPen("#ff073a", style=QtCore.Qt.DashLine, width=2))
            self.lower_plot.addItem(line)
            self._scan_handles["bc_vpin_line"] = line
        else:
            self._scan_handles["bc_vpin"].setOpts(x=x, height=vpin_arr, width=0.8,
                                                  brushes=vbrushes, pen=None)

        # --- rigid dual scaling ---
        lo = min(lows + bear_fc_arr)
        hi = max(highs + bull_fc_arr)
        self._fit_scanner_y(len(x), lo, hi)
        self.lower_plot.getViewBox().setYRange(0.0, 1.05, padding=0)

        # §5 right-edge spot price + active-bucket fill badge, plus forecast tags
        # (all on the upper price pane; stacked + left-padded to avoid clipping).
        x_edge = x[-1]
        fill = self._active_fill_pct()
        spot = closes[-1]
        # §5.2 — spot price and EMA baseline both sit near the POC, so two
        # separate "mid"-anchored badges overlapped whenever close ≈ baseline.
        # Fold the baseline readout into the spot badge (one element can't self-
        # overlap); the gray dashed baseline curve still shows its position.
        self._scanner_tracker("t_spot", spot, "#dcdcdc",
            f"Price ${spot:.2f}<br>({fill:.0f}% Fill)<br>"
            f"<span style='color:#b4b4b4'>Base ${baseline_arr[-1]:.2f}</span>",
            x_edge, "mid", span=True)
        self._scanner_tracker("t_bc_bull", bull_fc_arr[-1], "#2ecc71",
            f"Bull ${bull_fc_arr[-1]:.2f}", x_edge, "up", line=False)
        self._scanner_tracker("t_bc_bear", bear_fc_arr[-1], "#e74c3c",
            f"Bear ${bear_fc_arr[-1]:.2f}", x_edge, "down", line=False)

        # Deterministic horizontal lock: mirror the main X range onto the lower
        # pane every frame so the dual panes stay in pixel-perfect lock-step.
        main_xr = self.plot.getViewBox().viewRange()[0]
        self.lower_plot.getViewBox().setXRange(main_xr[0], main_xr[1], padding=0)

    def _reposition_hud(self, price: float) -> None:
        if not price:
            return
        scene_pt = self.vb.mapViewToScene(QtCore.QPointF(0.0, price))
        view_pt = self.plot.mapFromScene(scene_pt)
        gp = self.plot.mapToGlobal(view_pt)
        wp = self.mapFromGlobal(gp)
        y = max(10, min(self.height() - 45, wp.y() - self.hud.height() // 2))
        x = self.plot.x() + self.plot.width() - self.hud.width() - 5
        self.hud.move(x, y)

    # ------------------------------------------------------------------
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.menu_btn.move(self.width() - self.menu_btn.width() - 8, 8)
        # bell sits just left of the hamburger (fix #8)
        self.bell_btn.move(self.width() - self.menu_btn.width() - self.bell_btn.width() - 14, 8)
        self.menu.setGeometry(self.width() - self.menu.PANEL_WIDTH, 0,
                              self.menu.PANEL_WIDTH, self.height())
        self.drawbar.move((self.width() - self.drawbar.width()) // 2, 8)
        self.alerts.setGeometry(self.width() - self.alerts.width() - self.menu.PANEL_WIDTH,
                                40, self.alerts.width(), min(420, self.height() - 80))
        # edit panel (shape color/thickness) sits just under the drawing toolbar
        self.drawer.edit_panel.move((self.width() - self.drawer.edit_panel.width()) // 2,
                                    8 + self.drawbar.height() + 4)

    def closeEvent(self, event) -> None:
        self.timer.stop()
        self.worker.stop()
        if self in _OPEN_WINDOWS:
            _OPEN_WINDOWS.remove(self)
        super().closeEvent(event)


def spawn_window(tf: str = config.DEFAULT_TF) -> "MinimalTerminalWindow":
    win = MinimalTerminalWindow(tf=tf)
    _OPEN_WINDOWS.append(win)
    win.show()
    return win


# ---------------------------------------------------------------------------
# Automatic SSH tunnel to the cloud daemon (terminal-side quality-of-life)
# ---------------------------------------------------------------------------
# Deployment-specific — edit if the VM identity changes. The terminal dials
# config.IPC_HOST:IPC_PORT locally; this forwards that to the VM's loopback daemon
# over a gcloud SSH session so the tunnel never has to be opened by hand.
_TUNNEL_SSH_TARGET = "yassine.mdouari@smc-quant-eu"
_TUNNEL_GCLOUD_ARGS = [
    "compute", "ssh", _TUNNEL_SSH_TARGET,
    "--project=yass-chart", "--zone=europe-west9-b",
    "--ssh-flag=-N",
    f"--ssh-flag=-L {config.IPC_PORT}:127.0.0.1:{config.IPC_PORT}",
]


def _ipc_port_open() -> bool:
    """True if something already serves IPC_HOST:IPC_PORT — a live tunnel from a
    previous run, a second terminal's tunnel, or a local testing daemon."""
    try:
        with socket.create_connection((config.IPC_HOST, config.IPC_PORT), timeout=0.4):
            return True
    except OSError:
        return False


class SSHTunnelManager:
    """Boots the gcloud SSH tunnel on app start (unless the port is already live)
    and tears down the WHOLE process tree on app quit.

    Only the process that launched the tunnel ever kills it, so a second terminal
    that reused the live tunnel won't drop it out from under the first.
    """

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None

    def ensure(self) -> None:
        """Requirement 1 (port check) + 2 (invisible background launch with fallback)."""
        if _ipc_port_open():
            print(f"[tunnel] {config.IPC_HOST}:{config.IPC_PORT} already live — reusing it.")
            return
            
        gcloud = shutil.which("gcloud")   # resolves gcloud.cmd on Windows via standard PATH
        
        # --- HARDCODED WIN-ENVIRONMENT PATH FALLBACK FOR STANDALONE EXECUTABLE BUBBLE ---
        if not gcloud and os.name == "nt":
            fallback_path = r"C:\Users\Yassine Mdouari\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
            if os.path.exists(fallback_path):
                gcloud = fallback_path
                
        if not gcloud:
            print("[tunnel] gcloud not on PATH — open the tunnel manually or install "
                  "the Cloud SDK. The terminal will keep retrying to connect.")
            return
            
        if os.name == "nt":
            # Safely quote arguments containing spaces, then wrap the entire string 
            # in outer quotes to force cmd.exe to parse paths with spaces correctly.
            args_str = " ".join(f'"{arg}"' if " " in arg else arg for arg in _TUNNEL_GCLOUD_ARGS)
            cmd = f'cmd.exe /c ""{gcloud}" {args_str}"'
            flags = subprocess.CREATE_NO_WINDOW
        else:
            cmd = [gcloud, *_TUNNEL_GCLOUD_ARGS]
            flags = 0
        try:
            self._proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=flags)
            print(f"[tunnel] SSH tunnel launching (pid {self._proc.pid}); the terminal "
                  "auto-connects once it is up.")
        except Exception as exc:
            self._proc = None
            print(f"[tunnel] launch failed: {exc}")

    def stop(self) -> None:
        """Requirement 3 (lifecycle teardown) — kill the tunnel's whole tree. Idempotent."""
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        try:
            if os.name == "nt":
                # Kill the tree (cmd.exe -> gcloud/python -> ssh.exe). A bare
                # terminate() on the parent would orphan the ssh.exe tunnel.
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception as exc:
            print(f"[tunnel] stop error: {exc}")


def main() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    # Bring the SSH tunnel up BEFORE the first window so the worker thread connects
    # as soon as it is live, and bind teardown to the app lifecycle so we never leak
    # a tunnel. aboutToQuit fires on a normal close; the finally is the backstop if
    # app.exec() never runs (e.g. a window build raises).
    tunnel = SSHTunnelManager()
    tunnel.ensure()
    app.aboutToQuit.connect(tunnel.stop)

    exit_code = 0
    try:
        spawn_window(config.DEFAULT_TF)
        exit_code = app.exec()
    finally:
        tunnel.stop()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
