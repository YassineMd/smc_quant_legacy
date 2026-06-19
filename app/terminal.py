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

from . import bucket_state, config
from .alerts import AlertsLedger
from .chart_widgets import (
    AbsorptionLayer, BucketCandleItem, LocalTimeAxis, OrderBlockLayer, PriceAxis,
)
from .cob_panel import CobPanel
from .drawing_tools import DrawingController, DrawingToolbar
from .footprint_layers import BucketFootprintItem, DepthWallLayer
from .hamburger import FloatingOverlayMenu, HamburgerButton
from .pipe_client import PipeClientWorker
from .stats_overlay import StatsOverlay

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

# View-follow (Mode 10) — the live-edge tracking that replaces the one-shot fit.
# All tunable by eye. The FOLLOW_*_PER_TICK pair picks the refit cadence: both True =
# per-tick (truest follow), both False = per-close, X-True/Y-False = track-X / stable-Y.
FOLLOW_WINDOW = 100       # buckets shown in the live window
FOLLOW_MARGIN = 8         # buckets of right padding so the live edge isn't flush to the axis
FOLLOW_PAD_FRAC = 0.08    # Y padding as a fraction of the visible candle range
FOLLOW_AXIS_TOL_FRAC = 0.01  # per-axis "did it move?" threshold as a fraction of that axis's span —
                             # absorbs float noise + off-axis drift on a wobbly horizontal drag (tunable)
FOLLOW_X_PER_TICK = True  # roll X every draw (vs only on a bucket close)
FOLLOW_Y_PER_TICK = True  # refit Y every draw (vs only on a bucket close) — flip False if price-whip jitters

# Churn / no-conviction candle treatment (correctness — a bucket with no dominant 4-vector
# must NOT borrow a conviction color). Both tunable; the BEAUTIFUL churn identity is Phase 3.
# NOTE: CHURN_VOL_FRAC is a FIXED threshold in a relative world — a known limitation (same class
# of mistake as a fixed px_per_y). It kills the egregious lie (a rounding-error vector is churn
# under ANY threshold) and unblocks calibration; whether it should become ADAPTIVE (net-fraction
# vs a rolling baseline, like the Step-5 exhaustion z) is a deferred post-calibration refinement
# — see MASTER_FIX_PLAN.md.
CHURN_VOL_FRAC = 0.05              # net positioning (main-opp) as a fraction of VOLUME, below
                                   # which a candle reads "no conviction" / churn -> muted neutral
CHURN_RGBA = (110, 112, 120, 115)  # deliberate muted slate (~45% alpha) — neutral, legible, not a lie


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
        self._last_snap: dict = {}

        # --- chart + COB split (the splitter handle is the COB resizer) ---
        self.plot = pg.PlotWidget(axisItems={"bottom": LocalTimeAxis(orientation="bottom"),
                                             "right": PriceAxis(orientation="right")})
        self.plot.showAxis("right"); self.plot.hideAxis("left")
        self.plot.showGrid(x=True, y=True, alpha=0.12)
        self.plot.setMenuEnabled(False)
        # Group C fix: the crosshair (60Hz, full-span, antialiased InfiniteLine) trails
        # under the PlotWidget's default MinimalViewportUpdate + CacheBackground — the
        # vacated column isn't invalidated, so old pixels smear until a pan/zoom forces a
        # full repaint. BoundingRectViewportUpdate repaints the bounding rect of ALL
        # changes (which includes the crosshair's old+new band), clearing the trail; it
        # stays a BAND, not a full-viewport repaint, so the busy-canvas cost stays bounded.
        self.plot.setViewportUpdateMode(
            QtWidgets.QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)
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

        # --- DOM depth walls — the only price-axis overlay ITEM surviving the time-chart removal
        # (the COB ladder lives in cob_col; all other time-chart scene items deleted in Phase C).
        # Mode 10 drives it per-frame via _update_m10_dom. ---
        self.depthwall_item = DepthWallLayer()
        self.plot.addItem(self.depthwall_item, ignoreBounds=True)
        self.depthwall_item.setZValue(9)

        self.scanner_mode = "bucket_canvas"   # opens on Mode 10; never the (removed) time chart
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
        self._scanner_needs_autofit: bool = True         # one-shot Y/X fit (frees manual zoom)
        # View-follow (Mode 10), per-axis lock model. follow_x / follow_y track each axis to
        # the live edge independently; _follow_last_n drives the per-tick/per-close cadence.
        # A manual pan/zoom unlocks the axis that actually MOVED (diffed vs _follow_prev_range)
        # — pyqtgraph's manual signal can't be trusted for the axis: its payload carries the
        # axis for wheel-zoom but not for the three drag gestures. Re-lock is an axis
        # double-click (_on_scene_click). The code-driven roll emits only the generic
        # sigRangeChanged, so it never self-trips the manual handler.
        self._follow_x: bool = True
        self._follow_y: bool = True
        self._follow_last_n: int = -1
        self._follow_prev_range = None
        self.vb.sigRangeChangedManually.connect(self._on_scanner_manual_range)
        self._depth_needs_calibration: bool = True       # one-shot depth-slider 50%-of-max baseline (§1)
        # Handles for the heavy modes' extra scene objects (built in Phase 5/6,
        # torn down here). Pre-declared so teardown checks are always safe.
        self.axis_bottom = self.plot.getAxis("bottom")
        self.vb_kinetic_price = None   # Mode 4 secondary linked price ViewBox
        self.vb_pulse_churn = None     # Modes 7/8 secondary churn-scale ViewBox
        self.lower_plot = None         # Mode 10 lower VPIN sub-pane
        self.splitter_v = None         # Mode 10 vertical splitter (upper/lower panes)
        self.cob_col = None            # Mode 10 COB column (cob + spacer), height-matched to the price pane
        self._cob_want = False         # user's COB-toggle intent (drives cob_col visibility in Mode 10)
        self._syncing_split = False    # reentrancy guard for the linked splitter-divider sync
        # Mode 10 order-block layer (index-space). Persistent object; added to the
        # plot lazily in _scan_bucket_canvas and swept on teardown. Tiers forced on.
        self.bc_obs = OrderBlockLayer(self.plot, show_tiers=True)
        # Mode 10 whale-absorption bands (phase c; index-space). Persistent; added lazily in
        # _scan_bucket_canvas, swept on teardown; its $-label pool attached here, cleared there.
        self.bc_absorption = AbsorptionLayer(self.plot)
        # Mode 10 per-bucket footprint ladder (Stage 1; index-space twin of
        # FootprintLayer). Persistent object; added to the plot lazily in
        # _scan_bucket_canvas, swept on teardown; its TextPools are attached here and
        # cleared in clear_scanner_canvas (leak guard — pool items aren't tracked).
        self.bc_fp = BucketFootprintItem()
        self.bc_fp.attach_text(self.plot)

        # --- crosshair (patch §13): light-gray dashed ---
        pen = pg.mkPen(color="#aaaaaa", style=QtCore.Qt.DashLine, width=1)
        self.vline = pg.InfiniteLine(angle=90, movable=False, pen=pen)
        self.hline = pg.InfiniteLine(angle=0, movable=False, pen=pen)
        self.vline.setZValue(15); self.hline.setZValue(15)
        self.plot.addItem(self.vline, ignoreBounds=True)
        self.plot.addItem(self.hline, ignoreBounds=True)
        # --- A2: cursor Y-axis price tag — a right-axis badge tracking the hline's Y,
        # shown in ALL modes. Reads the cursor price via mapSceneToView and formats with
        # config.PRICE_DECIMALS so it matches PriceAxis exactly. On a full cursor-leave it
        # LINGERS at the last price (like the crosshair — Qt emits no sigMouseMoved once the
        # cursor is off the viewport, so the _on_mouse_move hide can't fire). The real
        # orphan-guard is the mode-switch hide in clear_scanner_canvas, which drops it so a
        # stale position never carries across modes.
        self.price_tag = pg.TextItem(anchor=(1, 0.5), color="#141414",
                                     fill=pg.mkBrush("#dcdcdc"))
        _ptf = QtGui.QFont("Consolas", 9); _ptf.setBold(True)
        self.price_tag.textItem.setFont(_ptf)
        self.price_tag.setZValue(16)            # above the crosshair (z=15)
        self.plot.addItem(self.price_tag, ignoreBounds=True)
        self.price_tag.hide()
        # Mode-10 DOM hover-volume tooltip: the color-matched size of the depth wall
        # nearest the cursor (green=bid / red=ask). Driven by _hover_dom_wall. Anchor
        # (0, 1.0) = bottom-left at the point, so the text sits ABOVE the wall line it
        # labels (never straddling it).
        self.dom_tooltip = pg.TextItem(anchor=(0, 1.0))
        self.dom_tooltip.setZValue(60)
        self.plot.addItem(self.dom_tooltip, ignoreBounds=True)
        self.dom_tooltip.hide()
        self._proxy = pg.SignalProxy(self.plot.scene().sigMouseMoved,
                                     rateLimit=60, slot=self._on_mouse_move)
        # last cursor scene pos while inside the plot — drives the A3a live-breathe
        # re-fire so a hovered forming bucket updates each frame, not just on motion.
        self._last_hover_pos = None

        # --- floating overlays (top-level children) ---
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

        # A5 — open straight onto Mode 10 (the primary surface), never the time chart. Same
        # path the combo uses: hides time components, applies the dark scanner theme, and
        # _on_timer paints Mode 10 directly. The reordered combo already shows it at index 0.
        self._set_scanner("bucket_canvas")

    # ------------------------------------------------------------------
    def _wire_menu(self) -> None:
        self.menu.tfChanged.connect(self._change_tf)
        self.menu.multiplierChanged.connect(lambda v: setattr(self.bc_obs, "visible_filter", v))
        self.menu.chartFilterChanged.connect(lambda v: setattr(self.depthwall_item, "threshold", float(v)))
        self.menu.layerToggled.connect(self._toggle_layer)
        self.menu.subWidgetToggled.connect(self._toggle_subwidget)
        self.menu.scannerChanged.connect(self._set_scanner)
        self.menu.scan_time_changed.connect(self._on_scan_time_changed)

    def _set_scanner(self, mode: str) -> None:
        """Route between the bucket-native modes (Mode 10 canvas + the 9 metric scanners). Order:
        set mode -> teardown -> hide the (dormant) time-scene items + flip the axis to bucket-index.
        Per-mode geometry is drawn by the 50ms loop via :meth:`_draw_scanner`. (Time chart removed
        in Phase B — every mode is a scanner mode now.)
        """
        self.scanner_mode = mode
        self.clear_scanner_canvas()   # teardown first

        self.axis_bottom.set_scanner_active(True)
        # §6.2 — drawing is LOCKED on every scanner mode EXCEPT bucket_canvas, where it's
        # re-enabled in index space (session-only via DrawingController.index_mode).
        is_canvas = mode == "bucket_canvas"
        self.drawer.locked = not is_canvas
        self.drawer.index_mode = is_canvas
        if not is_canvas:
            self.drawer.cancel()   # drop any armed tool + hide its edit panel
        self._hide_price_overlays()
        self._apply_scanner_theme(dark=True)     # enhancement §3
        self._scanner_needs_autofit = True       # one-shot fit for the new mode
        self._scanner_bucket_sig = None
        self._last_scanner_sig = None
        # (Mode 10 COB lives in cob_col, built + shown by _ensure_canvas_panes from _cob_want.)
        self._on_timer()   # immediate first draw from the current Zero Point

    def _hide_price_overlays(self) -> None:
        """Reset the price-axis overlays on scanner entry: hide the DOM walls + COB ladder + the
        hover readout. bucket_canvas re-shows the DOM (_update_m10_dom / cob_col); the metric modes
        (non-price Y) leave them hidden. (Formerly _hide_time_components — the time-chart scene
        items it also hid were deleted in Phase C.)"""
        self.depthwall_item.setVisible(False)
        self.stats.hide()
        self.cob.hide()

    def _change_tf(self, tf: str) -> None:
        self.setWindowTitle(f"Order Flow Terminal — {config.SYMBOL} {tf}")
        self._sig_candles = self._sig_obs = self._sig_fp = None
        self._autoranged = False
        self._scanner_needs_autofit = True    # new tf -> refit the scanner once
        self._scanner_bucket_sig = self._last_scanner_sig = None
        self._depth_needs_calibration = True  # new tf -> re-baseline the depth slider (§1)
        self.worker.request_timeframe(tf)

    def _toggle_layer(self, key: str, on: bool) -> None:
        # Only Mode-10 overlays carry toggles now — the time-chart "Technical Layers" section was
        # removed with the time chart, so every layer key is an m10_ key -> the overlay dispatch.
        if key.startswith("m10_"):
            self._set_scanner_overlay(key, on)

    def _set_scanner_overlay(self, key: str, on: bool) -> None:
        """A4 — toggle one Mode 10 overlay. IMMEDIATE teardown here (the signature-gated
        ``_draw_scanner`` may skip the next redraw, so we can't wait for it to hide an
        item), then invalidate the scanner sig so the next tick repaints a toggled-ON
        overlay with fresh data. The toggle STATE lives in the menu checkbox and is
        re-read by the draw-gate every draw, so it survives redraws AND mode-switches;
        this only syncs the live scene. No-op when the item isn't on the canvas (not in
        Mode 10, or never created while toggled off — the draw-gate will make it)."""
        if key == "m10_poc":
            h = self._scan_handles.get("bc_poc")
            if h is not None:
                h.setVisible(on)
        elif key == "m10_footprint":
            # Sub-pool teardown: bc_fp's bubble QPicture hides via setVisible, but its
            # number TextPools are NOT in active_scanner_items — clear them explicitly
            # (same call clear_scanner_canvas uses) or they orphan as floating numbers.
            if "bc_fp" in self._scan_handles:
                self.bc_fp.setVisible(on)
                if not on:
                    self.bc_fp.clear_text(self.plot)
        elif key == "m10_obs":
            # Same trap: zone bands hide via setVisible, but the tier_pool labels are
            # not tracked — clear them so no tier artifacts are left behind.
            if "bc_obs" in self._scan_handles:
                self.bc_obs.setVisible(on)
                if not on:
                    self.bc_obs.tier_pool.clear(self.plot)
        elif key == "m10_stats":
            # The stats box is a floating QLabel, not a scene item: hide it now on OFF.
            # ON is handled by the gate in _hover_scanner (the next hover / live-breathe
            # re-fire re-shows it), so there is nothing to re-add to the scene here.
            if not on:
                self.stats.hide()
        elif key == "m10_liq":
            h = self._scan_handles.get("bc_liq")   # scatter, no sub-pools -> plain setVisible
            if h is not None:
                h.setVisible(on)
        elif key == "m10_statedebug":
            # Calibration instrument: extra stats-box lines produced at hover time
            # (_hover_context reads layer_state). No scene item to manage here; the
            # live-breathe / next hover re-renders the box with or without the block.
            pass
        elif key == "m10_dead_obs":
            # Sub-filter of m10_obs, read by update_data_indexed at draw time (show_dead).
            # No scene item to manage; the forced redraw below repaints with/without dead boxes.
            pass
        elif key == "m10_icebergs":
            # Whale-absorption bands: setVisible toggles the zones; the $-labels are pool-managed
            # (not in active_scanner_items) -> clear them on OFF so no label artifacts linger.
            if "bc_absorption" in self._scan_handles:
                self.bc_absorption.setVisible(on)
                if not on:
                    self.bc_absorption.label_pool.clear(self.plot)
        elif key == "m10_dom":
            # DOM depth walls (Phase A): persistent plot item (not in _scan_handles), so toggle
            # it directly now; _update_m10_dom re-shows + refreshes it every frame while ON.
            self.depthwall_item.setVisible(on)
        self._last_scanner_sig = None   # force _draw_scanner to re-run -> repaint

    def _toggle_subwidget(self, key: str, on: bool) -> None:
        if key == "drawing":
            self.drawbar.setVisible(on)
            if not on:
                self.drawer.cancel()
        elif key == "cob":
            # Remember the intent; in Mode 10 toggle the whole COB column so OFF reclaims its width
            # (the spacer goes too).
            self._cob_want = on
            (self.cob_col if self.cob_col is not None else self.cob).setVisible(on)
        elif key == "audio":
            self.alerts.audio.set_armed(on)

    def _on_scene_click(self, ev) -> None:
        """Double-click handling. Mode 10: per-axis follow LOCK — double-clicking the X axis
        locks X, the Y axis locks Y, the plot body locks both (snap to full follow). The
        bottom/right axis strips are distinct scene rects, so the hit-test is unambiguous.
        Every other mode keeps the reset + auto-fit (fix #10, TradingView parity)."""
        if not ev.double():
            return
        if self.scanner_mode == "bucket_canvas":
            sp = ev.scenePos()
            if self.plot.getAxis("bottom").sceneBoundingRect().contains(sp):
                self._follow_x = True                  # X axis -> lock horizontal follow
            elif self.plot.getAxis("right").sceneBoundingRect().contains(sp):
                self._follow_y = True                  # Y axis -> lock price auto-fit
            else:
                self._follow_x = self._follow_y = True  # plot body -> snap to full follow
            self._last_scanner_sig = None              # force an immediate roll so the lock takes effect
            return
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

    def _update_m10_dom(self, snap: dict) -> None:
        """Mode-10 DOM (Phase A): live order-book depth on the bucket canvas, refreshed EVERY
        frame (ungated). Depth pulses independently of bucket closes, so this sits OUTSIDE the
        signature-gated _draw_scanner — gating it would freeze the book in a quiet market.
        bucket_canvas-only (the metric modes have a non-price Y where price walls are meaningless).
        On-plot walls follow the m10_dom toggle; the COB side ladder follows its own 'cob' toggle;
        both read snap['depth']. depthwall_item is ignoreBounds, so it never drives the price fit."""
        depth = snap.get("depth") or {}
        self._calibrate_depth_slider(depth)        # one-shot 50%-of-max default (no-op after first book)
        walls_on = self.menu.layer_state("m10_dom")
        self.depthwall_item.setVisible(walls_on)
        if walls_on:
            vx0, vx1 = self.vb.viewRange()[0]
            self.depthwall_item.update_data(depth, vx0, vx1)
        if self.cob.isVisible():
            self.cob.update_depth(depth)
            self._sync_cob()

    def _calibrate_depth_slider(self, depth: dict) -> None:
        """§1 — one-shot: default the depth-wall slider to an absolute-SOL value = 50% of the
        largest resting order, on the first valid book payload after connect / tf-change. So only
        walls >= half the biggest current wall show by default (the significant ones), while the
        slider itself stays absolute-SOL and draggable — a manual drag overrides this and the flag
        keeps it from being re-imposed. Filters WHICH literal-price walls draw; never re-clusters."""
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
        target_default = int(max(qtys) * 0.50)   # default: only walls >= 50% of the largest
        target_default = max(config.CHART_FILTER_MIN,
                             min(config.CHART_FILTER_MAX, target_default))
        self.menu.chart_slider.setValue(target_default)
        self._depth_needs_calibration = False

    def _on_mouse_move(self, evt) -> None:
        pos = evt[0]
        if not self.plot.sceneBoundingRect().contains(pos):
            self.stats.hide()
            self.price_tag.hide()
            self.dom_tooltip.hide()
            self._last_hover_pos = None      # left the plot -> stop the live-breathe
            return
        self._last_hover_pos = pos           # park here for the live-breathe re-fire
        pt = self.vb.mapSceneToView(pos)
        self.vline.setPos(pt.x()); self.hline.setPos(pt.y())
        # A2: right-axis price tag tracks the cursor Y (all modes); PRICE_DECIMALS
        # matches PriceAxis so the badge value lines up with the axis ticks.
        self.price_tag.setText(f"{pt.y():.{config.PRICE_DECIMALS}f}")
        self.price_tag.setPos(self.vb.viewRange()[0][1], pt.y())
        self.price_tag.show()

        # §7.4 — yellow follow-spot tracks the cursor only while a drawing tool is
        # armed (anything other than the cursor/select pointer); hidden otherwise.
        if self.drawer.active_tool not in (None, "select"):
            self.cursor_spot.setData([pt.x()], [pt.y()])
            self.cursor_spot.show()
        else:
            self.cursor_spot.hide()

        # Every mode is bucket-native now: surface the hovered bucket's readout + (Mode 10) the DOM
        # wall volume. (The time-chart _hover_stats path went with the Off mode in Phase B.)
        self._hover_scanner(pt.x(), pos)
        self._hover_dom_wall(pt.x(), pt.y())   # DOM wall hover-volume (bucket_canvas + m10_dom)

    def _hover_scanner(self, x: float, scene_pos) -> None:
        """Rich, mode-specific HUD readout for the hovered volume bucket (§4)."""
        if not self.menu.layer_state("m10_stats"):   # A4 step 3: stats-box toggle
            self.stats.hide()                         # gates mouse-move AND live-breathe paths
            return
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

    def _refresh_parked_hover(self) -> None:
        """A3a live-breathe — re-run the scanner hover for the parked cursor each
        redraw frame so a hovered FORMING bucket updates tick-by-tick, not only on
        cursor motion. No-op once the cursor leaves the plot (_last_hover_pos is
        cleared). Cheap: _build_scanner_buckets is signature-gated on the live edge's
        volume, so a frame with no new trade just re-emits the cached readout."""
        pos = self._last_hover_pos
        if pos is None:
            return
        pt = self.vb.mapSceneToView(pos)
        self._hover_scanner(pt.x(), pos)
        self._hover_dom_wall(pt.x(), pt.y())   # live-update the hovered wall's volume as the book pulses

    def _hover_dom_wall(self, cursor_x: float, price: float) -> None:
        """Mode-10 DOM hover: show the nearest depth wall's volume as color-matched text
        (green=bid / red=ask). Only on bucket_canvas with m10_dom on (the walls aren't drawn
        elsewhere); hidden otherwise. Tolerance scales with zoom (~1% of the visible price range,
        floored at a tick) so the cursor needn't land pixel-perfect on the line."""
        if self.scanner_mode != "bucket_canvas" or not self.menu.layer_state("m10_dom"):
            self.dom_tooltip.hide()
            return
        y0, y1 = self.vb.viewRange()[1]
        tol = max(config.TICK_SIZE, (y1 - y0) * 0.01)
        w = self.depthwall_item.nearest_wall(price, tol)
        if w is None:
            self.dom_tooltip.hide()
            return
        wp, qty, side = w
        color = "#2ea043" if side == "bid" else "#f85149"
        vol = f"{qty / 1000:.1f}K" if qty >= 1000 else f"{qty:.0f}"
        self.dom_tooltip.setHtml(
            f"<span style='color:{color}; font-family:Consolas; font-weight:bold; "
            f"font-size:14px'>{vol}</span>")
        self.dom_tooltip.setPos(cursor_x, wp)
        self.dom_tooltip.show()

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
        if mode == "kinetic":
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
        if mode == "bucket_canvas":
            # A3a — full order-flow readout from the bucket scalars, grouped into four
            # sections (Flow / Positioning / Effort / Read). STATE is a placeholder until
            # A3b. Anomaly % = the EXACT Step-5/Mode-3 multiplier (read-only call to
            # _exhaustion_mults), shown as % off 1.0 so it reconciles with the future
            # STATE verdict; the 30-bucket rolling E/R uses the SAME window (EXH_WINDOW)
            # the anomaly is measured against.
            PD = config.PRICE_DECIMALS
            def pf(v): return f"{v:.{PD}f}"                         # price -> axis precision
            def sk(v): return ("+" if v >= 0 else "-") + K(abs(v))  # signed K (e.g. +1.2k)
            def sep(t):   # subtle section header: dim, small, letter-spaced; recedes below data
                return f"<span style='color:#5a6170;font-size:9px;letter-spacing:2px'>{t}</span>"
            o, h, l, c = (b.get("open", 0.0), b.get("high", 0.0),
                          b.get("low", 0.0), b.get("close", 0.0))
            poc = b.get("poc_price", 0.0)
            cv, bv, sv = b.get("curr_vol", 0.0), b.get("buy_vol", 0.0), b.get("sell_vol", 0.0)
            opL, opS = b.get("opL", 0.0), b.get("opS", 0.0)
            clL, clS = b.get("clL", 0.0), b.get("clS", 0.0)
            ber, ser = b.get("buyer_er", 0.0), b.get("seller_er", 0.0)
            delta = bv - sv
            dpct = (delta / cv * 100.0) if cv > 0 else 0.0
            oi_d = (opL + opS) - (clL + clS)
            dur = b.get("end_time", 0.0) - b.get("start_time", 0.0)
            vel = b.get("vol_mult", 1.0)
            bm, sm, _om = _exhaustion_mults(buckets, idx)
            win = buckets[max(0, idx - EXH_WINDOW):idx]
            b30 = (sum(w.get("buyer_er", 0.0) for w in win) / len(win)) if win else 0.0
            s30 = (sum(w.get("seller_er", 0.0) for w in win) / len(win)) if win else 0.0
            # color ONLY the two dominant 4-vectors (the ones that drove the move); the
            # other two render dim. A zero vector never lights up even if it lands "top 2".
            vmag = {"opL": opL, "opS": opS, "clS": clS, "clL": clL}
            vclr = {"opL": g, "opS": r, "clS": bl, "clL": pu}
            top2 = set(sorted(vmag, key=lambda k: vmag[k], reverse=True)[:2])
            def vc(name): return vclr[name] if (name in top2 and vmag[name] > 0) else gray
            # A3b — the one interpretive line: best-scoring state + calibrated confidence.
            state, conf = bucket_state.classify_bucket(buckets, idx, bm, sm)
            lines = [
                f"O {pf(o)}  H {pf(h)}  L {pf(l)}  {span('C '+pf(c), g if c >= o else r)}",
                f"Elapsed {dur:.1f}s   {span('POC '+pf(poc), gold)}",
                sep("FLOW"),
                f"Volume {K(cv)}",
                f"{span('Sell '+K(sv), r)} | {span('Buy '+K(bv), g)}",
                f"Delta {span(sk(delta)+f' ({dpct:+.0f}%)', g if delta >= 0 else r)}",
                f"OI Δ {span(sk(oi_d), g if oi_d >= 0 else r)}",
                sep("POSITIONING"),
                f"{span('OpL '+K(opL), vc('opL'))} | {span('OpS '+K(opS), vc('opS'))}",
                f"{span('ClS '+K(clS), vc('clS'))} | {span('ClL '+K(clL), vc('clL'))}",
                sep("EFFORT"),
                span(f"Buyer E/R {ber:.1f} [{(bm - 1.0) * 100:+.0f}%]", g),
                span(f"Seller E/R {ser:.1f} [{(sm - 1.0) * 100:+.0f}%]", r),
                span(f"30b Buyer E/R {b30:.1f}", g),
                span(f"30b Seller E/R {s30:.1f}", r),
                sep("READ"),
                f"VEL {span(f'{vel:.2f}x', gold)}",
                f"STATE {bucket_state.render_state_line(state, conf)}",
            ]
            if self.menu.layer_state("m10_statedebug"):   # calibration: top-3 states + winner factors
                lines += bucket_state.render_debug_lines(buckets, idx, bm, sm)
            return lines
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

    # ------------------------------------------------------------------
    def _on_timer(self) -> None:
        snap = self.worker.snapshot()
        self._last_snap = snap

        # Every mode is bucket-native now (time chart removed, Phase B): draw the scanner, refresh
        # Mode-10 DOM (ungated, bucket_canvas-only — depth pulses independently of the sig-gated
        # _draw_scanner), re-dock the axis badges, breathe the hovered bucket.
        self._draw_scanner()
        if self.scanner_mode == "bucket_canvas":
            self._update_m10_dom(snap)
        self._redock_trackers()
        self._refresh_parked_hover()

    # ------------------------------------------------------------------
    # Phase 1: bucket pipeline + Zero-Point anchor
    # ------------------------------------------------------------------
    def _on_scan_time_changed(self) -> None:
        """User moved the Zero Point: flush geometry and redraw from the new anchor."""
        self.clear_scanner_canvas()
        self._scanner_bucket_sig = None       # force a fresh bucket rebuild
        self._scanner_needs_autofit = True    # re-fit once to the new window
        self._on_timer()                      # immediate manual redraw

    def clear_scanner_canvas(self) -> None:
        """Aggressive teardown of all scanner geometry + heavy-mode scene objects.

        Safe to call in any state. Steps: (1) sweep tracked items; (2) Mode 4
        secondary ViewBox teardown; (3) Mode 10 lower-pane + COB-column teardown.
        """
        self.price_tag.hide()   # A2: drop the cursor price tag on any mode switch (no orphan)
        self.stats.hide()       # A3a: drop the hover readout too (no orphan across modes)
        # 1. sweep every tracked scanner item off the plot
        for item in self.active_scanner_items:
            try:
                self.plot.removeItem(item)
            except Exception:
                pass
        self.active_scanner_items.clear()
        self._scan_handles = {}   # stale after removeItem — modes recreate on next draw
        self._scan_trackers = {}  # drop redock records (their items were just swept)
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
                self.bc_absorption.label_pool.clear(self.plot)   # leak guard: absorption $-labels
                self.bc_fp.clear_text(self.plot)   # leak guard: footprint TextPools (not in active_scanner_items)
                # §6.2 — index-space drawings are session-only; wipe them on exit
                self.drawer.flush_index_drawings()
                self.lower_plot.getViewBox().setXLink(None)
                # reparent self.plot back to the horizontal splitter at index 0, then restore the
                # COB beside it (out of cob_col) and drop the COB column — i.e. restore [plot, cob]
                self.splitter.insertWidget(0, self.plot)
                self.splitter.setStretchFactor(0, 1)
                self.cob.setParent(None)
                self.splitter.insertWidget(1, self.cob)
                self.cob.setVisible(self._cob_want)   # restore intent (it was force-shown inside cob_col)
                if self.cob_col is not None:
                    self.cob_col.setParent(None)
                    self.cob_col.deleteLater()   # also deletes the spacer child
                self.lower_plot.setParent(None)
                if self.splitter_v is not None:
                    self.splitter_v.setParent(None)
                    self.splitter_v.deleteLater()
                self.lower_plot.deleteLater()
            except Exception:
                pass
            self.lower_plot = None
            self.splitter_v = None
            self.cob_col = None

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
        # Mode-10 absorption marks must repaint on a lifecycle/geometry change even when the bucket
        # set and live-edge volume are static (a QUIET market): without this, an active->dead flip
        # leaves (len, curr_vol, scan_start, mode) identical, the redraw is skipped, and the dead
        # band stays drawn OPEN to the live edge. (Path 1 will also align deaths to bucket closes.)
        abs_sig = tuple((m.get("id"), m.get("active"), m.get("end"),
                         round(float(m.get("kappa", 0.0)), 2), m.get("price"))
                        for m in sorted(snap.get("absorptions", []), key=lambda m: m.get("id", "")))
        current_sig = (len(closed), active.get("curr_vol", 0.0),
                       self.menu.scan_start_unix(), self.scanner_mode, abs_sig)
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

    def _add_scanner_item(self, item: object, ignore_bounds: bool = False) -> object:
        """Add a plot item and track it for teardown. All modes route through here.

        ``ignore_bounds=True`` keeps the item OUT of the viewbox autoRange — for derived
        overlays (e.g. OB zones) whose stale/clamped X must never drag the X fit back to 0
        and float them into the corner under view-follow."""
        self.plot.addItem(item, ignoreBounds=ignore_bounds)
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

    def _roll_to_live_edge(self, n: int, lows: list, highs: list) -> None:
        """View-follow (Mode 10): slide the X window to the live edge + candle-frame Y
        over the visible window. The forecast cloud (bull_fc/bear_fc) is DELIBERATELY
        excluded from the Y fit (the old A0 goal) so candles aren't squished; re-fitting
        the visible window every draw also means an extreme in-window bucket can't
        overflow. Cadence per FOLLOW_*_PER_TICK (per-tick vs only on a bucket close)."""
        if n <= 0:
            return
        new_bucket = (n != self._follow_last_n)
        if self._follow_x and (FOLLOW_X_PER_TICK or new_bucket):
            self.vb.setXRange(max(-0.5, n - FOLLOW_WINDOW - 0.5),
                              (n - 1) + FOLLOW_MARGIN + 0.5, padding=0)
        if self._follow_y and (FOLLOW_Y_PER_TICK or new_bucket):
            w0 = max(0, n - FOLLOW_WINDOW)
            lo, hi = min(lows[w0:n]), max(highs[w0:n])
            if not (hi > lo):
                hi = lo + 1.0
            pad = (hi - lo) * FOLLOW_PAD_FRAC
            self.vb.setYRange(lo - pad, hi + pad, padding=0)
        self._follow_last_n = n

    def _on_scanner_manual_range(self, *args) -> None:
        """View-follow PER-AXIS unlock (Mode 10 only). Fires only on mouse-driven range
        changes; the code-driven roll emits the generic sigRangeChanged, so it never trips
        this. The manual payload can't be trusted for the axis (inconsistent across
        gestures), so we diff the new range vs the last displayed one and unlock whichever
        axis ACTUALLY moved, beyond a tolerance that absorbs float noise + off-axis drift on
        a wobbly drag. Scroll-zoom moves both -> unlocks both; a horizontal drag moves X
        only; a Y-axis wheel moves Y only. Re-lock is a double-click (_on_scene_click)."""
        if self.scanner_mode != "bucket_canvas" or self._follow_prev_range is None:
            return
        (nx0, nx1), (ny0, ny1) = self.vb.viewRange()
        (px0, px1), (py0, py1) = self._follow_prev_range
        tol_x = FOLLOW_AXIS_TOL_FRAC * max(1e-9, px1 - px0)
        tol_y = FOLLOW_AXIS_TOL_FRAC * max(1e-9, py1 - py0)
        if abs(nx0 - px0) > tol_x or abs(nx1 - px1) > tol_x:
            self._follow_x = False                           # horizontal pan/zoom -> unlock X
        if abs(ny0 - py0) > tol_y or abs(ny1 - py1) > tol_y:
            self._follow_y = False                           # price pan/zoom -> unlock Y
        self._follow_prev_range = ((nx0, nx1), (ny0, ny1))

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
    def _sync_pane_split(self, *args) -> None:
        """Keep the COB/spacer divider in lock-step with the price/VPIN divider so the COB's
        bottom always coincides with the price-pane bottom (B-fix). Mirrors sizes from whichever
        splitter the user dragged onto the other; guarded so the programmatic setSizes can't
        recurse (setSizes does not re-emit splitterMoved, but the guard is cheap insurance)."""
        if self._syncing_split or self.splitter_v is None or self.cob_col is None:
            return
        self._syncing_split = True
        try:
            if self.sender() is self.cob_col:
                self.splitter_v.setSizes(self.cob_col.sizes())
            else:
                self.cob_col.setSizes(self.splitter_v.sizes())
        finally:
            self._syncing_split = False

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
        self.lower_plot.setViewportUpdateMode(   # Group C: same anti-trail policy as the main pane
            QtWidgets.QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)
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

        # COB column (B-fix): the COB must occupy ONLY the price-pane height so sync_y aligns it
        # with the chart pixel-for-pixel BY CONSTRUCTION. Put it as the TOP child of a 3:1 vertical
        # splitter (same ratio as splitter_v) with a dark spacer beneath (beside the VPIN pane) —
        # the spacer is the cost of keeping VPIN the same WIDTH as the price pane (X-aligned
        # candles/heatmap; a full-width VPIN would re-break that in X). Reparent the COB in here.
        self.cob_col = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.cob_col.setMinimumWidth(self.cob.minimumWidth())   # track the COB's own width bounds
        self.cob_col.setMaximumWidth(self.cob.maximumWidth())
        self.cob.setParent(None)
        self.cob_col.addWidget(self.cob)
        spacer = QtWidgets.QWidget()                             # dead strip beside the VPIN pane
        spacer.setAutoFillBackground(True)
        _sp_pal = spacer.palette(); _sp_pal.setColor(QtGui.QPalette.Window, QtGui.QColor("#141414"))
        spacer.setPalette(_sp_pal)
        self.cob_col.addWidget(spacer)
        self.cob_col.setStretchFactor(0, 3)   # COB matches the price pane's 75%
        self.cob_col.setStretchFactor(1, 1)   # spacer matches the VPIN pane's 25%
        self.splitter.insertWidget(1, self.cob_col)
        self.cob.setVisible(True)                       # gated by cob_col's visibility below
        self.cob_col.setVisible(self._cob_want)         # honor the user's COB toggle
        # Linked dividers: dragging either the price/VPIN handle or the COB/spacer handle moves the
        # other in lock-step, so the COB bottom always coincides with the price-pane bottom.
        self.splitter_v.splitterMoved.connect(self._sync_pane_split)
        self.cob_col.splitterMoved.connect(self._sync_pane_split)

        # Horizontal lock is enforced deterministically every frame in
        # _scan_bucket_canvas (mirror main X -> lower X). We deliberately do NOT
        # use setXLink here: combined with the per-frame mirror it double-controls
        # the range and leaves a padding mismatch, and its propagation is unreliable
        # under offscreen/deferred-paint conditions. The explicit mirror is exact.

    def _neon_v2_brush(self, opL: float, opS: float, clL: float, clS: float,
                       curr_vol: float, vel_ratio: float) -> "pg.QtGui.QBrush":
        """Candle-body brush from the dominant 4-vector + dominance opacity, with
        the HFT (vel_ratio >= 2.5) neon override (Neon Engine V2)."""
        vectors = {"opL": opL, "opS": opS, "clL": clL, "clS": clS}
        pair = {"opL": "opS", "opS": "opL", "clS": "clL", "clL": "clS"}
        main_key = max(vectors, key=vectors.get)
        main_val = vectors[main_key]
        opp_val = vectors[pair[main_key]]
        total = main_val + opp_val
        dom = (main_val - opp_val) / total if total > 0 else 0.0   # conviction OPACITY (unchanged)
        # CHURN / no-conviction gate. Measure NET positioning (main-opp) as a fraction of VOLUME,
        # NOT of the pair sum: dividing by `total` inflates a rounding-error vector to dom~1.0
        # (clS=6.4 on 2.9K vol -> dom=1.0, yet that's 0.2% of volume = a lie). (main-opp)/curr_vol
        # is the honest "did a meaningful fraction of the bucket take a side" — it also catches the
        # balanced bucket (opL~opS -> net~0). Below the floor a candle must NOT borrow a conviction
        # color (the max() tiebreak defaults to opL/green) nor escalate to neon: return the muted
        # neutral BEFORE the palette + the vel>=2.5 override. Conviction candles are untouched —
        # their opacity still rides `dom`.
        conv = (main_val - opp_val) / curr_vol if curr_vol > 0 else 0.0
        if conv < CHURN_VOL_FRAC:
            return pg.mkBrush(CHURN_RGBA)
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

        opens, highs, lows, closes, brushes, pocs = [], [], [], [], [], []
        baseline_arr, bull_fc_arr, bear_fc_arr = [], [], []
        baseline = 0.0
        bull_stretch = 0.0
        bear_stretch = 0.0

        for i, b in enumerate(buckets):
            opens.append(b.get("open", 0.0))
            highs.append(b.get("high", 0.0))
            lows.append(b.get("low", 0.0))
            closes.append(b.get("close", 0.0))
            pocs.append(b.get("poc_price", 0.0))   # STAGE 0: true per-bucket POC (render-only)
            brushes.append(self._neon_v2_brush(
                b.get("opL", 0.0), b.get("opS", 0.0), b.get("clL", 0.0),
                b.get("clS", 0.0), b.get("curr_vol", 0.0), ratios[i]))

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

        # --- STAGE 0: true per-bucket POC marker (render-only, zero data change) ---
        # poc_price is already finalized in every BucketSnapshot (and computed on the
        # fly for the live edge in live_snapshot), so this draws what the engine already
        # ships. Gold matches the time-chart footprint POC ring (footprint_layers.py).
        # Guarded to within the bucket's [low, high] so a degenerate/cold poc_price=0
        # can't drop a dot at y=0 and skew the eye (or the one-shot Y-fit).
        # A4 draw-gate: skip create+update entirely when m10_poc is off; when on, ensure
        # the dot exists, is visible, and carries fresh data (the toggle handler does the
        # immediate hide on off + forces this repaint on).
        if self.menu.layer_state("m10_poc"):
            poc_x, poc_y = [], []
            for i in range(len(buckets)):
                pv = pocs[i]
                if pv > 0.0 and lows[i] <= pv <= highs[i]:
                    poc_x.append(x[i]); poc_y.append(pv)
            if "bc_poc" not in self._scan_handles:
                self._scan_handles["bc_poc"] = self._add_scanner_item(pg.ScatterPlotItem(
                    size=7, symbol="o", pen=pg.mkPen("#141414", width=0.5),
                    brush=pg.mkBrush("#f1c40f")))
                self._scan_handles["bc_poc"].setZValue(6)   # POC dots ride above the candles
            self._scan_handles["bc_poc"].setVisible(True)
            self._scan_handles["bc_poc"].setData(poc_x, poc_y)

        # --- STAGE 1: per-bucket footprint ladder from b["levels"] (wire-additive) ---
        # levels now ride on the BucketSnapshot (quant_engine._assemble), so the
        # footprint is a property of the BUCKET, drawn in its ordinal column. Ordinal
        # twin of the time-chart FootprintLayer; the POC is marked by the separate gold
        # dot (bc_poc above), so the ladder draws only the volume distribution. px_per_*
        # drive the bubble/number switch + pixel-round bubble radii; recomputed each
        # bucket-change frame.
        (vx0, vx1), (vy0, vy1) = self.vb.viewRange()
        px_per_x = self.vb.width() / max(1e-9, vx1 - vx0)
        px_per_y = self.vb.height() / max(1e-9, vy1 - vy0)
        # A4 draw-gate: footprint (bubbles/numbers) gated by m10_footprint. Toggle-off
        # teardown is in _set_scanner_overlay (setVisible + clear_text for the TextPools,
        # which are not in active_scanner_items). px_per_* are computed above regardless
        # (cheap, footprint-only) so nothing downstream can break when this is off.
        if self.menu.layer_state("m10_footprint"):
            levels_list = [b.get("levels", {}) for b in buckets]
            if "bc_fp" not in self._scan_handles:
                self.bc_fp.setZValue(5)            # ladder above candles (z0), below the POC dot (z6)
                self._add_scanner_item(self.bc_fp)
                self._scan_handles["bc_fp"] = self.bc_fp
            self.bc_fp.setVisible(True)
            self.bc_fp.update_data(x, levels_list, vx0, vx1, 0.8, px_per_x, px_per_y)  # vx0/vx1: viewport cull

        # --- order blocks mapped onto the integer bucket grid (§6.1) ---
        # A4 draw-gate: OB zones gated by m10_obs. Toggle-off teardown is in
        # _set_scanner_overlay (setVisible + tier_pool.clear for the tier labels, which
        # are not in active_scanner_items).
        # Shared index mapper for the OB + absorption overlays (both map an exact epoch -> the
        # bucket ordinal active at that time). Hoisted out of the m10_obs gate so the absorption
        # overlay can reuse it even when OB zones are toggled off.
        _scan_start_times = [b.get("start_time", 0.0) for b in buckets]

        def _ts_to_idx(ts: float) -> int:
            i = bisect.bisect_right(_scan_start_times, ts) - 1
            return -1 if i < 0 else min(i, len(_scan_start_times) - 1)

        if self.menu.layer_state("m10_obs"):
            if "bc_obs" not in self._scan_handles:
                self.bc_obs.setZValue(-5)          # zones render behind the candles
                self._add_scanner_item(self.bc_obs, ignore_bounds=True)  # derived overlay: never drive the X/Y fit
                self._scan_handles["bc_obs"] = self.bc_obs
            self.bc_obs.setVisible(True)
            # Min-Mult slider writes bc_obs.visible_filter directly now (relocated off the dormant
            # time-chart ob_item, Phase C step 1); bc_obs.update_data_indexed reads it.
            vx0, vx1 = self.vb.viewRange()[0]   # clamp OB spans to the visible window (no corner-float)
            self.bc_obs.update_data_indexed(
                self._last_snap.get("order_blocks", []), float(x[-1]), _ts_to_idx, (vx0, vx1),
                self.menu.layer_state("m10_dead_obs"))   # show mitigated OBs as faded lifespan boxes (toggle)

        # Mode 10 whale-absorption bands (phase c) — gated by m10_icebergs (relabeled "Absorption").
        if self.menu.layer_state("m10_icebergs"):
            if "bc_absorption" not in self._scan_handles:
                self.bc_absorption.setZValue(-6)   # behind the OB zones + candles
                self._add_scanner_item(self.bc_absorption, ignore_bounds=True)
                self._scan_handles["bc_absorption"] = self.bc_absorption
            self.bc_absorption.setVisible(True)
            vx0, vx1 = self.vb.viewRange()[0]
            self.bc_absorption.update_data_indexed(
                self._last_snap.get("absorptions", []), float(x[-1]), _ts_to_idx, (vx0, vx1))

        # --- liquidation marks (A4 step 4) — per-bucket forced volume from A3b-pre.
        # liq_short = shorts liquidated (forced buys) -> mark at the bucket HIGH (price
        # ran up into stops); liq_long = longs liquidated (forced sells) -> at the LOW.
        # Distinct triangles so the cyan/magenta don't read as candles/OB zones (that
        # color collision is a recorded Phase-3 cleanup). Gated by m10_liq; teardown is
        # a plain setVisible in _set_scanner_overlay (scatter, no sub-pools).
        if self.menu.layer_state("m10_liq"):
            def _lsz(v): return max(9.0, min(30.0, 7.0 + v ** 0.5))   # px ~ sqrt(forced vol)
            spots = []
            for i, b in enumerate(buckets):
                ls, ll = b.get("liq_short", 0.0), b.get("liq_long", 0.0)
                if ls > 0.0:
                    spots.append({"pos": (x[i], highs[i]), "symbol": "t1", "pen": None,
                                  "brush": pg.mkBrush(config.COLOR_LIQ_SHORT), "size": _lsz(ls)})
                if ll > 0.0:
                    spots.append({"pos": (x[i], lows[i]), "symbol": "t", "pen": None,
                                  "brush": pg.mkBrush(config.COLOR_LIQ_LONG), "size": _lsz(ll)})
            if "bc_liq" not in self._scan_handles:
                self._scan_handles["bc_liq"] = self._add_scanner_item(pg.ScatterPlotItem())
                self._scan_handles["bc_liq"].setZValue(7)   # above the POC dots (z6)
            self._scan_handles["bc_liq"].setVisible(True)
            self._scan_handles["bc_liq"].setData(spots)

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

        # --- view-follow (replaces the one-shot fit). A mode/tf/Zero-Point re-arm
        # (_scanner_needs_autofit) re-locks BOTH axes + drops us on the live edge, consuming
        # that flag exactly as _fit_scanner_y used to. The Y fit uses candles only
        # (lows/highs) — bull_fc/bear_fc EXCLUDED so they can't squish the candles (A0). The
        # roll runs whenever either axis is locked (each axis gated inside). After the draw
        # we snapshot the displayed range so the per-axis unlock can diff against it. ---
        if self._scanner_needs_autofit:
            self._follow_x = self._follow_y = True
            self._scanner_needs_autofit = False
        if self._follow_x or self._follow_y:
            self._roll_to_live_edge(len(x), lows, highs)
        self.lower_plot.getViewBox().setYRange(0.0, 1.05, padding=0)
        self._follow_prev_range = self.vb.viewRange()

        # §5 right-edge spot price + active-bucket fill badge, plus forecast tags
        # (all on the upper price pane; stacked + left-padded to avoid clipping).
        x_edge = x[-1]
        fill = self._active_fill_pct()
        spot = closes[-1]
        # §5.2 — spot price and EMA baseline both sit near the POC, so two separate
        # badges overlapped whenever close ≈ baseline. Fold the baseline readout into
        # the spot badge (one element can't self-overlap); the gray dashed baseline
        # curve still shows its position. Anchor the block "up" (bottom edge at spot)
        # so all three rows stack ABOVE the spanning price line — "mid" centered them
        # on it, striking the price line straight through the "% Fill" row.
        self._scanner_tracker("t_spot", spot, "#dcdcdc",
            f"Price ${spot:.2f}<br>({fill:.0f}% Fill)<br>"
            f"<span style='color:#b4b4b4'>Base ${baseline_arr[-1]:.2f}</span>",
            x_edge, "up", span=True)
        self._scanner_tracker("t_bc_bull", bull_fc_arr[-1], "#2ecc71",
            f"Bull ${bull_fc_arr[-1]:.2f}", x_edge, "up", line=False)
        self._scanner_tracker("t_bc_bear", bear_fc_arr[-1], "#e74c3c",
            f"Bear ${bear_fc_arr[-1]:.2f}", x_edge, "down", line=False)

        # Deterministic horizontal lock: mirror the main X range onto the lower
        # pane every frame so the dual panes stay in pixel-perfect lock-step.
        main_xr = self.plot.getViewBox().viewRange()[0]
        self.lower_plot.getViewBox().setXRange(main_xr[0], main_xr[1], padding=0)


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
