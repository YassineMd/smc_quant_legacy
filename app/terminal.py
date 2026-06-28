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
import time
from datetime import datetime
from typing import List, Optional

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from .heatmap import (HeatmapCache, TradeBubbleCache, decode_col, decode_grid,
                      decode_trades, neon_diverging_lut, percentile_levels)

from . import bucket_state, config, region_state, vpin_adaptive
from .region_state import EXH_WINDOW, exhaustion_mults as _exhaustion_mults
from .alerts import AlertsLedger
from .chart_widgets import (
    AbsorptionLayer, AbsorptionZoneLayer, BucketCandleItem, ExhaustionStripLayer, LocalTimeAxis,
    OrderBlockLayer, PanelSeparatorLayer, PriceAxis, _RGB_ABS_BEAR, _RGB_ABS_BULL, _RGB_EFF_BEAR,
    _RGB_EFF_BULL, _RGB_ER_BEAR, _RGB_ER_BULL, _RGB_EXH_BEAR, _RGB_EXH_BULL,
)
from .cob_panel import CobPanel
from .drawing_tools import DrawingController, DrawingToolbar
from .footprint_layers import BucketFootprintItem, DepthWallLayer, detail_visible
from .hamburger import FloatingOverlayMenu, HamburgerButton, scale_label
from .pipe_client import PipeClientWorker
from .stats_overlay import AbsorptionZoneSlider, EffAggZoneSlider, HeatmapContrastBar, StatsOverlay

_OPEN_WINDOWS: List["MinimalTerminalWindow"] = []
_TUNNEL: "Optional[SSHTunnelManager]" = None   # set in main(); the refresh button relaunches a dead tunnel


# Step-5 exhaustion knobs + _exh_z_mult / _exhaustion_mults now live in app/region_state.py
# (re-imported above, _exhaustion_mults under its original name, so call sites are unchanged and
# the headless accumulator can share the exact same math).

# View-follow (Mode 10) — the live-edge tracking that replaces the one-shot fit.
# All tunable by eye. The FOLLOW_*_PER_TICK pair picks the refit cadence: both True =
# per-tick (truest follow), both False = per-close, X-True/Y-False = track-X / stable-Y.
FOLLOW_WINDOW = 100       # buckets shown in the live window
FOLLOW_MARGIN = 8         # buckets of right padding so the live edge isn't flush to the axis
FOLLOW_PAD_FRAC = 0.08    # Y padding as a fraction of the visible candle range
FOLLOW_AXIS_TOL_FRAC = 0.01  # per-axis "did it move?" threshold as a fraction of that axis's span —
                             # absorbs float noise + off-axis drift on a wobbly horizontal drag (tunable)
HM_FOLLOW_LEAD_FRAC = 0.15   # heatmap follow: keep this fraction of the view BLANK to the right of 'now', so the
                             # live edge sits at ~85% across (lines + bubbles fill the left 85%, not jammed on the axis)
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


# Adaptive-VPIN tier -> display colour. Shared by EVERY VPIN site (Mode 6 bars, bucket-canvas
# heatmap, hover label, selection box) so 'toxic'/'warn' read identically everywhere. Bars/heatmap
# use the charcoal 'normal'; text surfaces (hover/selection) swap in a lighter gray below.
_VPIN_TIER_HEX = {vpin_adaptive.TOXIC: "#ff073a", vpin_adaptive.WARN: "#f1c40f",
                  vpin_adaptive.NORMAL: "#555555"}


def _split_curve_by_sign(x: np.ndarray, y: np.ndarray):
    """Split a polyline into its >=0 and <=0 portions for sign-colouring, INSERTING the exact zero-crossing
    point on each sign change so the two coloured curves meet ON the baseline (no gap, no overshoot past 0).
    Returns ``(xs, y_pos, y_neg)`` over a shared, crossing-augmented x; the off-side samples are NaN so a
    ``connect='finite'`` curve breaks there instead of drawing through the wrong-coloured region."""
    x = np.asarray(x, dtype=float); y = np.asarray(y, dtype=float)
    if x.size == 0:
        return np.empty(0), np.empty(0), np.empty(0)
    xs = [x[0]]; ys = [y[0]]
    for i in range(1, x.size):
        y0, y1 = y[i - 1], y[i]
        if (y0 < 0) != (y1 < 0) and (y1 - y0) != 0.0:        # straddles zero -> insert the crossing at y==0
            t = -y0 / (y1 - y0)
            xs.append(x[i - 1] + t * (x[i] - x[i - 1])); ys.append(0.0)
        xs.append(x[i]); ys.append(y[i])
    xs = np.asarray(xs); ys = np.asarray(ys)
    y_pos = np.where(ys >= 0.0, ys, np.nan)                  # >=0 keeps the crossing point (0) -> connects to red
    y_neg = np.where(ys <= 0.0, ys, np.nan)
    return xs, y_pos, y_neg


class MinimalTerminalWindow(QtWidgets.QMainWindow):
    def __init__(self, tf: str = config.DEFAULT_TF):
        super().__init__()
        self.setWindowTitle(f"Order Flow Terminal — {config.SYMBOL} {config.TF_SECONDS.get(tf, 60) // 60}×")
        self._title_scale = None   # last-rendered title scale portion (flicker-free updates)
        self.resize(1280, 760)

        self._sig_candles = None
        self._sig_obs = None
        self._sig_fp = None
        self._autoranged = False
        self._last_snap: dict = {}
        self._m10_cc = None   # #3 static closed-bucket compute cache (must exist before the first
                              # render — the scanner-entry path calls _on_timer() immediately)

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
        self._depth_needs_calibration: bool = True       # one-shot depth-slider 90%-of-max baseline (§1)
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
        # Mode 10 per-bucket footprint ladder (Stage 1; index-space, per-bucket levels).
        # Persistent object; added to the plot lazily in
        # _scan_bucket_canvas, swept on teardown; its TextPools are attached here and
        # cleared in clear_scanner_canvas (leak guard — pool items aren't tracked).
        self.bc_fp = BucketFootprintItem()
        self.bc_fp.attach_text(self.plot)
        # Mode-10 absorption-zone bands (within a selection): sustained heavy-bull/bear runs -> green/red
        # price x time bands. Above candles (z2), below the flip overlays; shown only when a selection has
        # a qualifying run. Persistent item on the plot; hidden when no selection.
        self.bc_absorp_zones = AbsorptionZoneLayer(self.plot)
        self.bc_absorp_zones.setZValue(2)
        self.plot.addItem(self.bc_absorp_zones, ignoreBounds=True)
        self.bc_absorp_zones.attach_text(self.plot)
        self.bc_absorp_zones.setVisible(False)
        # Floorless s-threshold slider for those bands (rides the suppression score). Auto-defaults to the
        # selection's median nonzero-s each time the selection changes; a manual drag pins an OVERRIDE that
        # holds until the selection identity changes. Yellow dot on the track = validated-strength floor.
        self.zone_slider = AbsorptionZoneSlider(self, config.ABSORP_ZONE_FLOOR_S)
        self.zone_slider.changed.connect(self._on_zone_s_changed)
        self.zone_slider.hide()
        self._zone_sel_id = None       # identity of the live selection; on change -> re-seed adaptive default
        # EFFECTIVE-AGGRESSION zones — the validated mirror (heavy volume that MOVED price), NEON green/red,
        # its OWN slider riding force f = eff_agg/vol_norm. Same layer machinery, distinct colours.
        self.bc_eff_zones = AbsorptionZoneLayer(self.plot, rgb_bull=_RGB_EFF_BULL, rgb_bear=_RGB_EFF_BEAR)
        self.bc_eff_zones.setZValue(2)
        self.plot.addItem(self.bc_eff_zones, ignoreBounds=True)
        self.bc_eff_zones.attach_text(self.plot)
        self.bc_eff_zones.setVisible(False)
        self.eff_slider = EffAggZoneSlider(self, config.EFF_AGG_ZONE_DOT_F)
        self.eff_slider.changed.connect(self._on_eff_f_changed)
        self.eff_slider.hide()
        self._eff_sel_id = None        # identity of the live selection; on change -> re-seed adaptive default
        self._sel_sig = None           # Fix 1: change-detection signature of the last selection refresh (skip
                                       # the heavy recompute when nothing that affects the output changed)
        # SELECTION-SCOPED EXHAUSTION STRIP — two smoothed lines (blue bull / red bear gated exhaustion)
        # across the selected buckets, in a panel hanging below the selection; gold diamonds mark crossovers
        # (the exhausted side swaps). Persistent plot item; hidden when no selection. zValue 2 like the zones.
        self.bc_exh_strip = ExhaustionStripLayer(self.plot)
        self.bc_exh_strip.setZValue(2)
        self.plot.addItem(self.bc_exh_strip, ignoreBounds=True)
        self.bc_exh_strip.setVisible(False)
        # dashed GOLD 50% reference line for the exhaustion panel (the band midline = 50% exhaustion)
        self.bc_exh_mid = pg.PlotDataItem(pen=pg.mkPen("#ffd700", width=1, style=QtCore.Qt.DashLine))
        self.bc_exh_mid.setZValue(3); self.bc_exh_mid.setVisible(False)
        self.plot.addItem(self.bc_exh_mid, ignoreBounds=True)
        # SELECTION-SCOPED EFF-AGG EVOLUTION STRIP — bull/bear per-bucket effective aggression as two
        # SYMMETRICALLY-smoothed NEON green/red lines, in a SECOND panel STACKED just below the exhaustion
        # strip ('2' toggles). Reuses the same parametrised layer + the eff_bull/bear arrays built for the
        # eff-agg zones; no crossover diamonds (it's a forcing-magnitude evolution, not an exhaustion swap).
        self.bc_eff_strip = ExhaustionStripLayer(self.plot, rgb_bull=_RGB_EFF_BULL, rgb_bear=_RGB_EFF_BEAR)
        self.bc_eff_strip.setZValue(2)
        self.plot.addItem(self.bc_eff_strip, ignoreBounds=True)
        self.bc_eff_strip.setVisible(False)
        # SELECTION-SCOPED EFFORT/RESULT STRIP — buyer/seller E/R as two SYMMETRICALLY-smoothed green/red lines,
        # in a THIRD panel STACKED below the eff-agg strip ('3' toggles). Promoted out of the FLOW TRAJECTORY
        # sparkline. Same parametrised layer; no crossover diamonds (the balance-flip detector marks the shift).
        self.bc_er_strip = ExhaustionStripLayer(self.plot, rgb_bull=_RGB_ER_BULL, rgb_bear=_RGB_ER_BEAR)
        self.bc_er_strip.setZValue(2)
        self.plot.addItem(self.bc_er_strip, ignoreBounds=True)
        self.bc_er_strip.setVisible(False)
        # SELECTION-SCOPED ABSORPTION STRIP — bull% vs bear% LEAN (each side's cumulative share of absorption),
        # NEON green (bull) / NEON purple (bear), crossing at the 50% midline. FIRST/top panel ('1' toggles).
        # Computed SELECTION-PURE (sliced) — unlike the zones, it never reaches before the box.
        self.bc_abs_strip = ExhaustionStripLayer(self.plot, rgb_bull=_RGB_ABS_BULL, rgb_bear=_RGB_ABS_BEAR)
        self.bc_abs_strip.setZValue(2)
        self.plot.addItem(self.bc_abs_strip, ignoreBounds=True)
        self.bc_abs_strip.setVisible(False)
        # Minimalist hairline dividers between the stacked panels (centre-fading, dark-friendly).
        self.bc_panel_sep = PanelSeparatorLayer(self.plot)
        self.bc_panel_sep.setZValue(3)            # just above the panels
        self.plot.addItem(self.bc_panel_sep, ignoreBounds=True)
        self.bc_panel_sep.setVisible(False)

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
        _HM_GREEN = (0, 255, 110); _HM_PURPLE = (190, 70, 255)   # neon by side (bids green / asks purple)
        self.price_tag.textItem.setFont(_ptf)
        self.price_tag.setZValue(16)            # above the crosshair (z=15)
        self.plot.addItem(self.price_tag, ignoreBounds=True)
        self.price_tag.hide()
        # X-axis TIME tag at the crosshair — heatmap mode only (x = epoch seconds -> HH:MM:SS, matching the
        # LocalTimeAxis). Anchored bottom-centre so it sits just above the time axis at the cursor's X.
        self.time_tag = pg.TextItem(anchor=(0.5, 1.0), color="#141414", fill=pg.mkBrush("#dcdcdc"))
        self.time_tag.textItem.setFont(_ptf)
        self.time_tag.setZValue(16)
        self.plot.addItem(self.time_tag, ignoreBounds=True)
        self.time_tag.hide()
        # Heatmap RESTING-liquidity readout: SHIFT+hover a heatmap cell -> its raw resting size, BLACK text on a
        # neon green (bid) / purple (ask) pill, anchored at the cursor. Only fires with Shift held (so a plain
        # hover stays clean). Driven by _on_mouse_move; reads the cache's raw column at the nearest cell.
        self.hm_vol_tip = pg.TextItem(color="#000000", anchor=(0.5, 1.4), fill=pg.mkBrush(*_HM_GREEN, 235))
        self.hm_vol_tip.textItem.setFont(_ptf); self.hm_vol_tip.setZValue(23)
        self.plot.addItem(self.hm_vol_tip, ignoreBounds=True); self.hm_vol_tip.hide()
        # Mode-10 DOM hover-volume tooltip: the color-matched size of the depth wall
        # nearest the cursor (green=bid / red=ask). Driven by _hover_dom_wall. Anchor
        # (0, 1.0) = bottom-left at the point, so the text sits ABOVE the wall line it
        # labels (never straddling it).
        self.dom_tooltip = pg.TextItem(anchor=(0, 1.0))
        self.dom_tooltip.setZValue(60)
        self.plot.addItem(self.dom_tooltip, ignoreBounds=True)
        self.dom_tooltip.hide()
        # Selection-PANEL hover: a single cursor label showing the hovered bucket's RAW (un-smoothed) values
        # for WHICHEVER stacked panel the cursor is over (exhaustion / eff-agg / E/R), prefixed with that
        # panel's label so it's unambiguous. Only fires inside a panel's y-band; never over the candles.
        self.panel_tooltip = pg.TextItem(anchor=(0.5, 1.0))
        _etf = QtGui.QFont("Consolas", 12); _etf.setBold(True)
        self.panel_tooltip.textItem.setFont(_etf)
        self.panel_tooltip.setZValue(61)
        self.plot.addItem(self.panel_tooltip, ignoreBounds=True)
        self.panel_tooltip.hide()
        self._panel_hovers = []   # per-refresh list of visible panels: {label, lo, yb, yt, bull, bear, ...}
        # Per-panel SPREAD badge (one per lean panel): the dominant side's current lead at the right edge —
        # black value on a NEON green (bull strongest) / NEON red (bear strongest) fill.
        self._spread_badges = {}
        for _k in ("ABSORPTION", "EFF-AGG", "E/R"):
            _bd = pg.TextItem(anchor=(0, 0.5), color=(0, 0, 0))
            _bf = QtGui.QFont("Consolas", 11); _bf.setBold(True)
            _bd.textItem.setFont(_bf)
            _bd.setZValue(62)
            self.plot.addItem(_bd, ignoreBounds=True)
            _bd.hide()
            self._spread_badges[_k] = _bd
        # Live PHASE TABLE — beside the panels. Classifies the selection as before/start/during/end of a move
        # (lights the matching phase row + confidence), driven by the selection's aggregate spreads vs config
        # PHASE_BOXES. HTML rendered in a screen-fixed TextItem, top-left anchored just right of the panels.
        self.phase_tbl = pg.TextItem(anchor=(0, 0), color=(220, 224, 230))
        self.phase_tbl.setZValue(62)
        self.plot.addItem(self.phase_tbl, ignoreBounds=True)
        self.phase_tbl.hide()
        # PHASE PANELS ('5'-'7') — one per merged phase (BEFORE / START/DURING / END); two lines = that phase's
        # running opacity, UP (green) / DOWN (red). They mirror the phase table's rows as lines. Stack under 1-4.
        self._PHASES = ("BEFORE", "START/DURING", "END")   # START+DURING merged into one (posteriors summed)
        self.bc_phase = {}
        self.show_phase = {}
        for _ph in self._PHASES:
            _ly = ExhaustionStripLayer(self.plot, rgb_bull=_RGB_ER_BULL, rgb_bear=_RGB_ER_BEAR)
            _ly.setZValue(2)
            self.plot.addItem(_ly, ignoreBounds=True)
            _ly.setVisible(False)
            self.bc_phase[_ph] = _ly
            self.show_phase[_ph] = False   # phase panels 5/6/7 HIDDEN by default ('5'-'7' toggle)
        self.show_phase_table = False      # phase TABLE shown on its own via 't' (no panel needed)
        self._proxy = pg.SignalProxy(self.plot.scene().sigMouseMoved,
                                     rateLimit=60, slot=self._on_mouse_move)
        # last cursor scene pos while inside the plot — drives the A3a live-breathe
        # re-fire so a hovered forming bucket updates each frame, not just on motion.
        self._last_hover_pos = None
        self.show_state = False   # STATE verdict + debug lines hidden until 'y' (both stats boxes)
        self.show_vel_abn = True  # abnormal-velocity DIAMONDS ON by default ('v' toggles; 2px border always on)
        self.show_sel_stats = False  # Mode-10 selection stats box HIDDEN by default ('h' toggles)
        self.show_abs_strip = True   # Mode-10 selection ABSORPTION panel ON by default ('1' toggles) — slot 1
        self.show_eff_strip = True   # Mode-10 selection eff-agg evolution panel ON by default ('2' toggles)
        self.show_er_strip = True    # Mode-10 selection effort/result panel ON by default ('3' toggles)
        self.show_exh_strip = True   # Mode-10 selection exhaustion panel ON by default ('4' toggles) — slot 4
        # NOTE: the 3 PHASE panels (5/6/7) stay ON by default (show_phase[...] = True above); the 4 MEASURE
        # panels above default OFF — so the default session computes only the always-on zones + the phase path.
        # ── Phase 2b: depth/liquidity HEATMAP (scanner mode "depth_heatmap"). The ImageItem + BBO lines live on
        # the main plot but are HIDDEN/empty in every other mode (zero cost), and the ~MB grid is pulled only via
        # worker.depth_heatmap_state() while this mode is active — it NEVER enters the 20Hz snapshot(). ──
        self.hm_img = pg.ImageItem(axisOrder="col-major")   # array[col=time][bin=price] -> x=time, y=price
        self.hm_img.setZValue(1); self.hm_img.setVisible(False)
        self.plot.addItem(self.hm_img, ignoreBounds=True)
        # BBO trace colors MATCH the liquidity palette: best-bid = neon GREEN (buy side), best-ask = neon
        # PURPLE (sell side). (_HM_GREEN / _HM_PURPLE defined above, with the price-tag setup.)
        self.hm_bid_line = pg.PlotDataItem(pen=pg.mkPen(_HM_GREEN, width=2))   # best-bid @ its price (y)
        self.hm_ask_line = pg.PlotDataItem(pen=pg.mkPen(_HM_PURPLE, width=2))  # best-ask @ its price (y)
        for _l in (self.hm_bid_line, self.hm_ask_line):
            _l.setZValue(20); _l.setVisible(False); self.plot.addItem(_l, ignoreBounds=True)   # above the image
        # CURRENT bid/ask: DASHED segments projecting FORWARD from the live edge to the right (the un-formed
        # future), so past 'now' you see only these dashed lines; they connect to where the solid (formed)
        # trace ends. Plus Y-axis price tags (Bookmap LLT-style).
        self.hm_bid_dash = pg.PlotDataItem(pen=pg.mkPen(_HM_GREEN, width=1, style=QtCore.Qt.DashLine))
        self.hm_ask_dash = pg.PlotDataItem(pen=pg.mkPen(_HM_PURPLE, width=1, style=QtCore.Qt.DashLine))
        for _l in (self.hm_bid_dash, self.hm_ask_dash):
            _l.setZValue(19); _l.setVisible(False); self.plot.addItem(_l, ignoreBounds=True)
        self.hm_bid_axtag = pg.TextItem(anchor=(1, 0.5), color="#03200b", fill=pg.mkBrush(*_HM_GREEN))
        self.hm_ask_axtag = pg.TextItem(anchor=(1, 0.5), color="#1a0330", fill=pg.mkBrush(*_HM_PURPLE))
        for _t in (self.hm_bid_axtag, self.hm_ask_axtag):
            _t.textItem.setFont(_ptf); _t.setZValue(21); self.plot.addItem(_t, ignoreBounds=True); _t.hide()
        self._hm_lut = None; self._hm_lut_key = None; self.hm_grey = False   # diverging LUT, (re)built on contrast
        self.hm_cache = HeatmapCache()
        # Phase 3: executed-trade bubbles overlay — buy (green) / sell (red) ScatterPlotItems, size by total
        # cell qty, sat by net side. Own cache + delivery buffer (never on snapshot()); pxMode so bubbles are a
        # fixed px size regardless of zoom.
        _HM_BLUE = (0, 180, 255); _HM_ORANGE = (255, 145, 0)    # ICEBERG bubbles: buy=ELECTRIC blue, sell=orange
        # tip=None disables pyqtgraph's built-in "x/y/data" hover box; our own sigHovered pill replaces it.
        self.hm_bubbles_buy = pg.ScatterPlotItem(pxMode=True, pen=None, tip=None,
                                                 brush=pg.mkBrush(*_HM_GREEN, 170), hoverable=True)
        self.hm_bubbles_sell = pg.ScatterPlotItem(pxMode=True, pen=None, tip=None,
                                                  brush=pg.mkBrush(*_HM_PURPLE, 170), hoverable=True)
        # Iceberg bubbles: a cell sitting on an active absorption/iceberg level is recolored (its qty rode a
        # refilling wall) — ELECTRIC BLUE for a BUY iceberg (bid wall), ORANGE for a SELL iceberg (ask wall).
        self.hm_bubbles_ice_buy = pg.ScatterPlotItem(pxMode=True, pen=None, tip=None,
                                                     brush=pg.mkBrush(*_HM_BLUE, 210), hoverable=True)
        self.hm_bubbles_ice_sell = pg.ScatterPlotItem(pxMode=True, pen=None, tip=None,
                                                      brush=pg.mkBrush(*_HM_ORANGE, 210), hoverable=True)
        for _b in (self.hm_bubbles_buy, self.hm_bubbles_sell,
                   self.hm_bubbles_ice_buy, self.hm_bubbles_ice_sell):
            _b.setZValue(18); _b.setVisible(False); self.plot.addItem(_b, ignoreBounds=True)
            _b.sigHovered.connect(self._on_bubble_hover)
        self.hm_bubbles_ice_buy.setZValue(19); self.hm_bubbles_ice_sell.setZValue(19)   # icebergs ride on top
        # per-scatter hover-pill color (BLACK text on the matching neon fill)
        self._hm_bubble_pill = {
            self.hm_bubbles_buy: (0, 255, 110), self.hm_bubbles_sell: (190, 70, 255),
            self.hm_bubbles_ice_buy: (0, 180, 255), self.hm_bubbles_ice_sell: (255, 145, 0)}
        # hover readout: the cell's total volume, BLACK text on the scatter's neon pill (set per-hover)
        self.hm_bubble_tip = pg.TextItem(color="#000000", anchor=(0.5, 1.4), fill=pg.mkBrush(*_HM_GREEN, 235))
        self.hm_bubble_tip.textItem.setFont(_ptf); self.hm_bubble_tip.setZValue(22)
        self.plot.addItem(self.hm_bubble_tip, ignoreBounds=True); self.hm_bubble_tip.hide()
        self._hm_tip_plot = None                      # which scatter currently owns the tip
        self.hm_tb_cache = TradeBubbleCache()
        self.hm_bubbles_on = True                    # default ON ('b' toggles)
        self.hm_bubble_min = config.HEATMAP_BUBBLE_MIN_QTY   # min cell qty to draw a bubble (declutter)
        self.hm_pending_tb: "Optional[str]" = None   # role of the next trades_window response: 'reset'|'prepend'
        self.hm_levels: "Optional[tuple]" = None   # (lo,hi) raw-size cutoffs (auto p20/p99 + 60s renorm)
        self._hm_sizes = None                       # sorted nonzero sizes of the loaded grid (pctile->size map)
        self.hm_manual = False                     # True once the user drags a cutoff slider (auto-renorm pauses)
        self.hm_contrast = HeatmapContrastBar(self, config.HEATMAP_LO_PCT, config.HEATMAP_HI_PCT)
        self.hm_contrast.changed.connect(self._hm_contrast_changed)
        self.hm_contrast.reset_clicked.connect(self._hm_contrast_reset)
        self.hm_contrast.hide()
        self.hm_renorm_t = 0.0
        self.hm_band: "Optional[tuple]" = None      # (ylo,yhi) currently loaded price band
        self.hm_floor_ms = 0                         # hard left-time boundary (Scan Start) — no data before it
        self.hm_pending: "Optional[str]" = None     # role of the next depth_window response: 'reset'|'prepend'
        self.hm_last_view: "Optional[tuple]" = None
        self.hm_follow = True                       # track the live edge (smooth view-follow); off on manual pan
        self._hm_prev_w = None                       # last view WIDTH (pan vs zoom discriminator for follow-detach)
        self._hm_debounce = QtCore.QTimer(self); self._hm_debounce.setSingleShot(True)
        self._hm_debounce.setInterval(130); self._hm_debounce.timeout.connect(self._hm_request_visible)
        self._flip_line = None    # Mode-10 balance-flip overlay (dashed yellow vline + sustain% label)
        self._flip_label = None
        self._forming_line = None   # tentative "forming" overlay (dim dotted amber + 'unconfirmed' label)
        self._forming_label = None

        # --- floating overlays (top-level children) ---
        self.stats = StatsOverlay(self)
        self.sel_stats = StatsOverlay(self)   # Magic-Selection aggregated-stats box (its own instance)
        # LIQUIDATION PRESSURE panel ('l') — the cascade "wave" (BOTTOM selection strip). A fixed-window rolling
        # sum of NET liquidation flow = (liq_short − liq_long) per bucket: forced BUYS (shorts liquidated) push
        # the wave UP (NEON CYAN), forced SELLS (longs liquidated) push it DOWN (HOT MAGENTA), about a dashed zero
        # baseline. Signed-log compressed (liquidations are spiky). Watch it RISE to surf a building cascade;
        # watch it HOLD/FADE for "is my wave still going?". Default ON.
        self.show_liq = True
        self.bc_liq_zero = pg.PlotDataItem(pen=pg.mkPen("#555555", width=1, style=QtCore.Qt.DashLine))
        self.bc_liq_pos = pg.PlotDataItem(pen=pg.mkPen("#00f3ff", width=2.4), connect="finite")   # forced BUYS (up)
        self.bc_liq_neg = pg.PlotDataItem(pen=pg.mkPen("#ff00a2", width=2.4), connect="finite")   # forced SELLS (down)
        for _it in (self.bc_liq_zero, self.bc_liq_neg, self.bc_liq_pos):
            _it.setZValue(3); _it.setVisible(False); self.plot.addItem(_it, ignoreBounds=True)
        self.alerts = AlertsLedger(self)
        self.drawbar = DrawingToolbar(self)
        self.menu = FloatingOverlayMenu(self)
        self.stats.keep_under = self.menu   # stats overlay stays below an open menu (z-order)
        self.sel_stats.keep_under = self.menu
        self.menu_btn = HamburgerButton(self)
        self.menu_btn.clicked.connect(self.menu.toggle_panel)
        self.menu.toggle_button = self.menu_btn   # so the menu's outside-click filter ignores this btn

        # fix #8: dedicated floating 🔔 button next to the hamburger
        self.bell_btn = HamburgerButton(self)
        self.bell_btn.setText("🔔")
        self.bell_btn.clicked.connect(self.alerts.toggle)

        # Refresh button — re-establish a frozen feed (net blip / stale socket / dead tunnel)
        # WITHOUT restarting the window. Sits just left of the bell.
        self.refresh_btn = HamburgerButton(self)
        self.refresh_btn.setText("🔄")
        self.refresh_btn.setToolTip("Refresh — reconnect the data feed if the chart froze")
        self.refresh_btn.clicked.connect(self._refresh)

        # Audio Feed — speak NEW icebergs/OBs aloud (via self.alerts.audio, gated by the
        # "Audio Feed" sub-widget; default OFF). The announce seeds silently on first data /
        # tf-change so the history backlog is never read out — only live events after.
        self._tf = config.DEFAULT_TF
        self._announced_obs: set = set()
        self._announced_icebergs: set = set()
        self._audio_seeded = False

        # fix #10: double-click anywhere on the chart resets/auto-fits the view
        self.plot.scene().sigMouseClicked.connect(self._on_scene_click)

        # --- drawing controller ---
        self.drawer = DrawingController(self.plot)
        self.drawer.toolbar = self.drawbar         # §7.3 — enables auto-revert
        self.drawbar.toolSelected.connect(self.drawer.set_tool)
        self.drawbar.show()    # toolbar ON by default — the menu's 'drawing' checkbox is checked, but its
                               # toggled signal isn't wired yet at build, so show it explicitly (resizeEvent
                               # positions it top-centre once the window lays out).
        self.drawer.selectionChanged.connect(self._refresh_selection_stats)   # Magic Selection -> stats
        QtGui.QShortcut(QtGui.QKeySequence("Escape"), self, activated=self.drawer.cancel)
        # quick toggles: 's' = Stats Box overlay, 'd' = Vector Drawing toolbar. Flip the menu
        # checkbox so the menu stays in sync and the existing show/hide + teardown logic runs.
        QtGui.QShortcut(QtGui.QKeySequence("S"), self,
                        activated=lambda: self.menu.layer_checks["m10_stats"].toggle())
        QtGui.QShortcut(QtGui.QKeySequence("D"), self,
                        activated=lambda: self.menu.sub_checks["drawing"].toggle())
        QtGui.QShortcut(QtGui.QKeySequence("P"), self,
                        activated=lambda: self.menu.layer_checks["m10_poc"].toggle())
        QtGui.QShortcut(QtGui.QKeySequence("L"), self,
                        activated=lambda: self.menu.layer_checks["m10_liq"].toggle())
        QtGui.QShortcut(QtGui.QKeySequence("F"), self,
                        activated=lambda: self.menu.layer_checks["m10_footprint"].toggle())
        QtGui.QShortcut(QtGui.QKeySequence("A"), self,
                        activated=lambda: self.menu.sub_checks["audio"].toggle())
        # 'o' = Order Blocks + Absorption/Iceberg overlays TOGETHER (both hidden by default)
        QtGui.QShortcut(QtGui.QKeySequence("O"), self, activated=self._toggle_ob_iceberg)
        # 'y' = show/hide the STATE verdict + debug lines in BOTH stats boxes (hidden by default)
        QtGui.QShortcut(QtGui.QKeySequence("Y"), self, activated=self._toggle_states)
        # 'v' = abnormal-velocity DIAMONDS (the 2px border is always on); drawing-cancel moved to Escape
        QtGui.QShortcut(QtGui.QKeySequence("V"), self, activated=self._toggle_vel_abn)
        # 'g' = greyscale toggle for the Liquidity Heatmap (depth_heatmap mode only)
        QtGui.QShortcut(QtGui.QKeySequence("G"), self, activated=self._toggle_heatmap_grey)
        # 'b' = trade-bubbles overlay toggle (Phase 3; depth_heatmap mode only)
        QtGui.QShortcut(QtGui.QKeySequence("B"), self, activated=self._toggle_heatmap_bubbles)
        # 'h' = show/hide the Mode-10 Magic-Selection stats box (chart overlays like the flip line stay)
        QtGui.QShortcut(QtGui.QKeySequence("H"), self, activated=self._toggle_sel_stats)
        # Mode-10 selection panels, STACKED below the box in this order: 1 ABSORPTION, 2 EFF-AGG, 3 E/R, 4 EXHAUSTION
        QtGui.QShortcut(QtGui.QKeySequence("1"), self, activated=self._toggle_abs_strip)
        QtGui.QShortcut(QtGui.QKeySequence("2"), self, activated=self._toggle_eff_strip)
        QtGui.QShortcut(QtGui.QKeySequence("3"), self, activated=self._toggle_er_strip)
        QtGui.QShortcut(QtGui.QKeySequence("4"), self, activated=self._toggle_exh_strip)
        # '5'-'7' = the per-phase panels (BEFORE / START/DURING / END), UP green / DOWN red running opacity
        for _key, _ph in (("5", "BEFORE"), ("6", "START/DURING"), ("7", "END")):
            QtGui.QShortcut(QtGui.QKeySequence(_key), self,
                            activated=lambda p=_ph: self._toggle_phase(p))
        QtGui.QShortcut(QtGui.QKeySequence("8"), self, activated=self._toggle_liq)       # panel 8: liquidation WAVE
        QtGui.QShortcut(QtGui.QKeySequence("T"), self, activated=self._toggle_phase_table)  # phase table (no panel needed)
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

        # Modifier mouse-wheel over the chart: Ctrl nudges the Scan Start (Zero Point) anchor ±1 min
        # (debounced — title scrubs live, one coalesced redraw); Shift zooms the X axis only.
        self._orig_vb_wheel = self.vb.wheelEvent
        self.vb.wheelEvent = self._vb_wheel
        self._scan_nudge_timer = QtCore.QTimer(self)
        self._scan_nudge_timer.setSingleShot(True)
        self._scan_nudge_timer.setInterval(90)
        self._scan_nudge_timer.timeout.connect(self._on_scan_time_changed)

    def _set_scanner(self, mode: str) -> None:
        """Route between the bucket-native modes (Mode 10 canvas + the 9 metric scanners). Order:
        set mode -> teardown -> hide the (dormant) time-scene items + flip the axis to bucket-index.
        Per-mode geometry is drawn by the 50ms loop via :meth:`_draw_scanner`. (Time chart removed
        in Phase B — every mode is a scanner mode now.)
        """
        prev_mode = self.scanner_mode
        self.scanner_mode = mode
        self.clear_scanner_canvas()   # teardown first
        if prev_mode == "depth_heatmap":
            self._hm_exit()           # Phase 2b: tear down the heatmap (unsubscribe live push, free grid)
        # §6.2 — index-space drawings are session-only + index-anchored. Keep them in memory for the
        # whole session: SHOW on Mode 10, HIDE on the metric scanners (where a price-anchored shape is
        # off-axis), restore on return — so drawings survive every mode switch AND scan-time change.
        if mode == "bucket_canvas":
            self.drawer.set_index_visible(True)
        elif prev_mode == "bucket_canvas":
            self.drawer.set_index_visible(False)
            self.drawer.clear_selection()   # the transient Magic Selection is Mode-10-only

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
        # Mode-appropriate Scan Start window: Mode 10 (candle canvas) keeps the 24h anchor; the metric
        # scanners default to a tighter 1h window. Signal blocked so the _on_timer below redraws from
        # the new anchor without firing an extra _on_scan_time_changed teardown.
        _anchor_secs = -86400 if is_canvas else -3600
        self.menu.scan_time_edit.blockSignals(True)
        self.menu.scan_time_edit.setDateTime(QtCore.QDateTime.currentDateTime().addSecs(_anchor_secs))
        self.menu.scan_time_edit.blockSignals(False)
        # (Mode 10 COB lives in cob_col, built + shown by _ensure_canvas_panes from _cob_want.)
        if mode == "depth_heatmap":
            self._hm_enter()   # Phase 2b: time axis + request the recent window (after the generic setup)
            self.cob.set_palette((0, 255, 110, 0.6), (190, 70, 255, 0.6))   # neon green bids / purple asks
            self._cob_want = True                      # DOM ladder default ON in heatmap mode
            self.cob.setVisible(True)                  # force-show (overrides the _hide_price_overlays above)
            cob_cb = self.menu.sub_checks.get("cob")   # keep the menu checkbox in sync (no re-emit)
            if cob_cb is not None and not cob_cb.isChecked():
                cob_cb.blockSignals(True); cob_cb.setChecked(True); cob_cb.blockSignals(False)
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
        self._tf = tf
        self._audio_seeded = False        # new tf -> re-seed; don't read out its backlog
        self._announced_obs = set(); self._announced_icebergs = set()
        self._title_scale = None   # force the title to re-render with this tf's ~vol next tick
        self.setWindowTitle(f"Order Flow Terminal — {config.SYMBOL} {config.TF_SECONDS.get(tf, 60) // 60}×")
        self._sig_candles = self._sig_obs = self._sig_fp = None
        self._autoranged = False
        self._scanner_needs_autofit = True    # new tf -> refit the scanner once
        self._scanner_bucket_sig = self._last_scanner_sig = None
        self._m10_cc = None   # #3 static closed-bucket compute cache (see _compute_bucket_arrays)
        self._depth_needs_calibration = True  # new tf -> re-baseline the depth slider (§1)
        self.worker.request_timeframe(tf)

    def _refresh(self) -> None:
        """Manual chart refresh — re-establish the data feed after a freeze (net blip / stale
        socket / dead tunnel) WITHOUT restarting the window. Mode-agnostic: it reconnects the
        data layer + re-pulls the catch-up that EVERY scanner mode draws from, then invalidates
        the render signatures so whichever mode is active repaints from the fresh data."""
        if _TUNNEL is not None:
            try:
                _TUNNEL.ensure()        # relaunch the gcloud tunnel only if its port died (no-op if live)
            except Exception:
                pass
        self.worker.refresh()           # drop a stale socket -> reconnect -> re-request catch-up
        # force a clean repaint in whatever mode is active + don't read out the re-pulled backlog
        self._sig_candles = self._sig_obs = self._sig_fp = None
        self._scanner_bucket_sig = self._last_scanner_sig = None
        self._audio_seeded = False
        self._announced_obs = set(); self._announced_icebergs = set()

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
        if self.scanner_mode == "depth_heatmap":
            self.hm_follow = True              # double-click anywhere -> re-lock onto the live edge (the next
            return                             # frame snaps X to [now-w, now]; the cache already holds it)
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
        """§1 — one-shot: default the depth-wall slider to an absolute-SOL value = 90% of the
        largest resting order, on the first valid book payload after connect / tf-change. So only
        walls >= 90% of the biggest current wall show by default (just the dominant ones), while the
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
        target_default = int(max(qtys) * 0.90)   # default: only walls >= 90% of the largest
        target_default = max(config.CHART_FILTER_MIN,
                             min(config.CHART_FILTER_MAX, target_default))
        self.menu.chart_slider.setValue(target_default)
        self._depth_needs_calibration = False

    def _on_mouse_move(self, evt) -> None:
        pos = evt[0]
        if not self.plot.sceneBoundingRect().contains(pos):
            self.price_tag.hide()
            self.time_tag.hide()
            self.hm_vol_tip.hide()
            self.dom_tooltip.hide()
            self.panel_tooltip.hide()
            self._last_hover_pos = None      # left the plot -> stop the hover re-fire
            if self.scanner_mode == "bucket_canvas":
                self._show_forming_stats()   # keep the live candle's readout on by default
            else:
                self.stats.hide()            # metric modes are hover-only
            return
        self._last_hover_pos = pos           # park here for the live-breathe re-fire
        pt = self.vb.mapSceneToView(pos)
        self.vline.setPos(pt.x()); self.hline.setPos(pt.y())
        # A2: right-axis price tag tracks the cursor Y (all modes); PRICE_DECIMALS
        # matches PriceAxis so the badge value lines up with the axis ticks.
        self.price_tag.setText(f"{pt.y():.{config.PRICE_DECIMALS}f}")
        self.price_tag.setPos(self.vb.viewRange()[0][1], pt.y())
        self.price_tag.show()
        # X-axis time readout at the crosshair (heatmap mode only; x = epoch seconds)
        if self.scanner_mode == "depth_heatmap":
            try:
                lbl = datetime.fromtimestamp(pt.x()).strftime("%H:%M:%S")
            except (ValueError, OSError, OverflowError):
                lbl = ""
            self.time_tag.setText(lbl)
            self.time_tag.setPos(pt.x(), self.vb.viewRange()[1][0])   # bottom edge of the view, at cursor X
            self.time_tag.show()
            if self.cob.isVisible():
                self.cob.mark_price(pt.y())     # mirror the crosshair price into the DOM ladder (size readout)
            # SHIFT+hover -> resting-liquidity readout at the hovered cell (a plain hover stays clean). Black
            # text on a neon green (bid) / purple (ask) pill, mirroring the bubble tip.
            if QtWidgets.QApplication.keyboardModifiers() & QtCore.Qt.ShiftModifier:
                hit = self.hm_cache.raw_at(pt.x() * 1000.0, pt.y())
                if hit is not None:
                    size, side = hit
                    self.hm_vol_tip.fill = pg.mkBrush(0, 255, 110, 235) if side == "bid" \
                        else pg.mkBrush(190, 70, 255, 235)
                    self.hm_vol_tip.setText(f"{size/1000:.1f}K" if size >= 1000 else f"{size:.0f}")
                    self.hm_vol_tip.setPos(pt.x(), pt.y())
                    self.hm_vol_tip.show()
                else:
                    self.hm_vol_tip.hide()
            else:
                self.hm_vol_tip.hide()
        else:
            self.time_tag.hide()
            self.hm_vol_tip.hide()

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
        self._hover_panels(pt.x(), pt.y())  # cursor label: RAW values of whichever stacked panel is hovered

    def _hover_panels(self, x: float, y: float) -> None:
        """Cursor label of the hovered bucket's RAW (un-smoothed) values for WHICHEVER stacked selection panel
        the cursor's Y falls inside (exhaustion / eff-agg / E/R), prefixed with that panel's label. Only fires
        within a panel's y-band — over the candles or in the gaps between panels it shows nothing. The panels
        don't overlap, so at most one matches."""
        if self.scanner_mode != "bucket_canvas" or not self._panel_hovers:
            self.panel_tooltip.hide()
            return
        for ph in self._panel_hovers:
            if not (ph["yb"] <= y <= ph["yt"]):
                continue
            k = int(round(x)) - ph["lo"]
            if not (0 <= k < len(ph["bull"])):
                continue
            bv, rv = ph["bull"][k], ph["bear"][k]
            if ph["fmt"] == "pct":
                bs, rs = f"{bv * 100:.0f}%", f"{rv * 100:.0f}%"
            else:                                   # volume / effort -> compact K formatting
                bs, rs = self._fmt_k(bv), self._fmt_k(rv)
            self.panel_tooltip.setHtml(
                f"<span style='color:#9aa0aa; font-weight:bold'>{ph['label']}</span>"
                f"<span style='color:#888'> &nbsp; </span>"
                f"<span style='color:rgb{ph['bcol']}; font-weight:bold'>{ph['blbl']} {bs}</span>"
                f"<span style='color:#888'> · </span>"
                f"<span style='color:rgb{ph['rcol']}; font-weight:bold'>{ph['rlbl']} {rs}</span>")
            self.panel_tooltip.setPos(x, y)
            self.panel_tooltip.show()
            return
        self.panel_tooltip.hide()

    def _hover_scanner(self, x: float, scene_pos) -> None:
        """Rich, mode-specific HUD readout for the hovered volume bucket (§4)."""
        if not self.menu.layer_state("m10_stats"):   # A4 step 3: stats-box toggle
            self.stats.hide()                         # gates mouse-move AND live-breathe paths
            return
        filtered, _x, _a = self._build_scanner_buckets()
        idx = int(round(x))
        if not (0 <= idx < len(filtered)):
            self._show_forming_stats()   # cursor over empty space -> fall back to the live-candle readout
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

    def _show_forming_stats(self) -> None:
        """Always-on readout for the FORMING (live, present, not-yet-closed) candle -- the right-most
        bucket. Shown by default whenever the Stats Box is on and you're NOT hovering a specific
        bucket, pinned to that candle's LOW point (data -> screen) like a hover readout but automatic.
        Mode 10 only; the hover readout (cursor-anchored, a specific bucket) is left untouched."""
        if self.scanner_mode != "bucket_canvas" or not self.menu.layer_state("m10_stats"):
            return
        filtered, _x, _a = self._build_scanner_buckets()
        if not filtered:
            return
        idx = len(filtered) - 1
        b = filtered[idx]
        end_time = b.get("end_time", 0.0)
        try:
            clock = datetime.fromtimestamp(end_time).strftime("%Y-%m-%d %H:%M:%S")
        except (OSError, ValueError, OverflowError):
            clock = "--"
        lines = [f"<b>Idx: {idx}</b>"] + self._hover_context(self.scanner_mode, filtered, idx)
        anchor = self.vb.mapViewToScene(QtCore.QPointF(float(idx), float(b.get("low", 0.0))))
        gp = self.plot.mapToGlobal(self.plot.mapFromScene(anchor))
        wp = self.mapFromGlobal(gp)
        self.stats.show_stats(lines, clock, wp.x(), wp.y())

    def _refresh_parked_hover(self) -> None:
        """A3a live-breathe — re-run the readout each redraw frame. With a parked cursor it
        re-renders the hovered bucket tick-by-tick; with NO hover it falls back to the always-on
        FORMING-candle readout (Mode 10), so the live candle's stats stay visible without hovering.
        Cheap: _build_scanner_buckets is signature-gated on the live edge volume, so a static frame
        just re-emits the cached readout."""
        pos = self._last_hover_pos
        if pos is None:
            self._show_forming_stats()   # not hovering -> the live candle's readout stays on by default
            return
        pt = self.vb.mapSceneToView(pos)
        self._hover_scanner(pt.x(), pos)
        self._hover_dom_wall(pt.x(), pt.y())   # live-update the hovered wall's volume as the book pulses

    def _toggle_states(self) -> None:
        """'y' — flip STATE-verdict + debug-line visibility in BOTH the per-bucket stats box and the
        Mode-10 selection box (hidden by default), and re-render both immediately."""
        self.show_state = not self.show_state
        self._refresh_parked_hover()       # per-bucket / forming-candle readout
        self._refresh_selection_stats()    # Mode-10 selection readout

    def _toggle_vel_abn(self) -> None:
        """'v' — flip the abnormal-velocity DIAMONDS (white, or gold on divergence) above buckets whose
        velocity is >= VEL_ABN_RATIO x its trailing-30 mean. The 2px candle border is ALWAYS on, so this
        toggles only the diamonds. DESCRIPTIVE study flag, not a signal. Flip the layer's visibility
        immediately for snappy response; spots refresh each scanner frame."""
        self.show_vel_abn = not self.show_vel_abn
        h = self._scan_handles.get("bc_vel_abn")
        if h is not None:
            h.setVisible(self.show_vel_abn)

    def _phase_post(self, key: str, vals) -> list:
        """Naive-Bayes posterior over the MERGED phases [BEFORE, START/DURING, END] for a direction. vals =
        (abs, er, eff) signed with-move spreads; gauss per (4-way) phase from config.PHASE_STATS. The raw
        START and DURING posteriors are SUMMED into one merged phase (the operator's Start/During merge)."""
        import math
        ll = []
        for _n, *g in config.PHASE_STATS[key]:             # 4-way classification: BEFORE/START/DURING/END
            s = 0.0
            for x, (m, sd) in zip(vals, g):
                sd = max(8.0, sd); s += -0.5 * ((x - m) / sd) ** 2 - math.log(sd)
            ll.append(s)
        mx = max(ll); ex = [math.exp(l - mx) for l in ll]; t = sum(ex) or 1.0
        p = [e / t for e in ex]
        return [p[0], p[1] + p[2], p[3]]                   # -> [BEFORE, START/DURING, END]

    def _phase_conf_traj(self, key: str, abs_sh: list, eff_sh: list, er_sh: list) -> list:
        """Per-bucket TRAJECTORY of SMOOTHED phase confidence: a list of [BEFORE, START/DURING, END] 3-vectors.
        Each bucket's raw posterior% (from the rolling-window lean spreads) is glided by an EMA
        op = λ·op + (1-λ)·posterior%, λ = config.PHASE_EMA_LAMBDA. The EMA is SEEDED at the selection's first
        bucket (op := conf[0]) and replayed ONLY across the selection, so it carries no state from outside [lo,hi]
        and resets for every selection. Sums to 100 (a convex blend of vectors that each sum to 100). The phase
        TABLE uses the last vector; the phase PANELS plot the full trajectory (UP green / DOWN red per phase)."""
        sgn = 1.0 if key == "up" else -1.0
        lam = config.PHASE_EMA_LAMBDA
        op = None; traj = []
        for k in range(len(abs_sh)):
            vals = (sgn * (abs_sh[k] - 0.5) * 200.0,   # (abs, er, eff) — match PHASE_STATS order
                    sgn * (er_sh[k] - 0.5) * 200.0,
                    sgn * (eff_sh[k] - 0.5) * 200.0)
            conf = [x * 100.0 for x in self._phase_post(key, vals)]   # merged posterior %, sums to 100
            op = conf[:] if op is None else [lam * op[j] + (1 - lam) * conf[j] for j in range(len(conf))]
            traj.append(op[:])
        return traj

    def _phase_table_html(self, op_up: list, op_dn: list) -> str:
        """UP (green) + DOWN (red) phase tables side by side. Each row's background OPACITY = that phase's
        live CONFIDENCE (posterior%, 0..100, sums to 100), blended over the dark canvas; the % is shown and the
        brightest (most-likely) phase is bold. DESCRIPTIVE — the glow traces which phase the lean now resembles."""
        names = list(self._PHASES)                         # merged: BEFORE / START/DURING / END

        def _blend(rgb, a, base=(20, 22, 26)):
            a = max(0.0, min(1.0, a))
            return "#%02x%02x%02x" % tuple(int(rgb[i] * a + base[i] * (1 - a)) for i in range(3))

        def _one(ops, rgb, dirhdr):
            top = ops.index(max(ops)) if max(ops) > 0 else -1
            out = ["<table cellspacing='0' cellpadding='5' style='font-family:Consolas; font-size:12px; color:#eef1f5'>",
                   f"<tr><td colspan='2' style='background-color:#%02x%02x%02x; color:#000; font-weight:bold'>{dirhdr}</td></tr>" % rgb]
            for k, nm in enumerate(names):
                bg = _blend(rgb, ops[k] / 100.0); w = "bold" if k == top else "normal"
                out.append(f"<tr><td style='background-color:{bg}; font-weight:{w}'>{nm}</td>"
                           f"<td style='background-color:{bg}; font-weight:{w}; text-align:right'>{ops[k]:.0f}%</td></tr>")
            out.append("</table>"); return "".join(out)
        up_html = _one(op_up, (46, 204, 113), "&#9650; UP")
        dn_html = _one(op_dn, (231, 76, 60), "&#9660; DOWN")
        return (f"<table cellspacing='0'><tr><td valign='top'>{up_html}</td>"
                f"<td>&nbsp;&nbsp;</td><td valign='top'>{dn_html}</td></tr></table>")

    def _set_spread_badge(self, key: str, bull_last: float, bear_last: float,
                          strong_is_bull: bool, x: float, y: float) -> None:
        """Place a panel's SPREAD badge: the dominant side's lead (|bull-bear| of the share lines, in points),
        black text on a NEON green (bull strongest) / NEON red (bear strongest) fill, at the panel's right."""
        bd = self._spread_badges[key]
        spread = abs(bull_last - bear_last) * 100.0
        bd.fill = pg.mkBrush(40, 230, 90) if strong_is_bull else pg.mkBrush(255, 45, 70)
        bd.setText(f" {spread:.0f}% ")
        bd.setPos(x, y)
        bd.show()

    def _toggle_abs_strip(self) -> None:
        """'1' — show/hide the Mode-10 selection ABSORPTION panel (green bull / red bear absorption lines, the
        TOP panel). Flips the layer immediately; it repopulates on the next selection refresh."""
        self.show_abs_strip = not self.show_abs_strip
        if not self.show_abs_strip:
            self.bc_abs_strip.setVisible(False)
            self.panel_tooltip.hide()
        self._refresh_selection_stats()

    def _toggle_exh_strip(self) -> None:
        """'4' — show/hide the Mode-10 selection exhaustion panel (bull/bear lines, the BOTTOM panel). Flips the
        layer immediately; it repopulates on the next selection refresh."""
        self.show_exh_strip = not self.show_exh_strip
        if not self.show_exh_strip:
            self.bc_exh_strip.setVisible(False); self.bc_exh_mid.setVisible(False)
            self.panel_tooltip.hide()
        self._refresh_selection_stats()

    def _toggle_eff_strip(self) -> None:
        """'2' — show/hide the Mode-10 selection eff-agg evolution panel (NEON bull/bear lines below the
        exhaustion strip). Flips the layer immediately; it repopulates on the next selection refresh."""
        self.show_eff_strip = not self.show_eff_strip
        if not self.show_eff_strip:
            self.bc_eff_strip.setVisible(False)
            self.panel_tooltip.hide()
        self._refresh_selection_stats()

    def _toggle_er_strip(self) -> None:
        """'3' — show/hide the Mode-10 selection effort/result panel (green buyer / red seller E/R lines below
        the eff-agg strip). Flips the layer immediately; it repopulates on the next selection refresh."""
        self.show_er_strip = not self.show_er_strip
        if not self.show_er_strip:
            self.bc_er_strip.setVisible(False)
            self.panel_tooltip.hide()
        self._refresh_selection_stats()

    def _toggle_phase(self, ph: str) -> None:
        """'5'-'7' — show/hide a per-phase panel (BEFORE / START/DURING / END): UP green / DOWN red running opacity."""
        self.show_phase[ph] = not self.show_phase[ph]
        if not self.show_phase[ph]:
            self.bc_phase[ph].setVisible(False)
        self._refresh_selection_stats()

    def _toggle_liq(self) -> None:
        """'l' — show/hide the Liquidation Pressure wave panel. OFF clears + hides it; ON repopulates next refresh."""
        self.show_liq = not self.show_liq
        if not self.show_liq:
            self._clear_liq_panel()
        self._refresh_selection_stats()

    def _clear_liq_panel(self) -> None:
        """Liquidation-panel tear-down: wipe + hide the line halves + baseline (items reused via setData -> no leak)."""
        for _it in (self.bc_liq_zero, self.bc_liq_pos, self.bc_liq_neg):
            _it.setData([], []); _it.setVisible(False)

    def _toggle_phase_table(self) -> None:
        """'t' — show/hide the live PHASE TABLE on its own (no need to turn on a phase panel 5/6/7)."""
        self.show_phase_table = not self.show_phase_table
        if not self.show_phase_table and not any(self.show_phase.values()):
            self.phase_tbl.hide()
        self._refresh_selection_stats()

    def _toggle_ob_iceberg(self) -> None:
        """'o' — toggle the Order Blocks + Absorption/Iceberg overlays TOGETHER (both hidden by default).
        Flips both menu checkboxes (driving _set_scanner_overlay -> hide/show + repaint); OB's state is the
        master so one press always lands them in the same on/off state even if they drift apart in the menu."""
        obs = self.menu.layer_checks.get("m10_obs")
        ice = self.menu.layer_checks.get("m10_icebergs")
        new_on = not (obs.isChecked() if obs else False)
        for cb in (obs, ice):
            if cb is not None and cb.isChecked() != new_on:
                cb.setChecked(new_on)   # emits layerToggled -> _set_scanner_overlay

    def _toggle_sel_stats(self) -> None:
        """'h' — show/hide the Mode-10 Magic-Selection stats box ONLY. The selection's chart overlays
        (flip line, absorption boxes, velocity markers) keep rendering; just the floating box is gated."""
        self.show_sel_stats = not self.show_sel_stats
        if self.show_sel_stats:
            self._refresh_selection_stats()    # re-place + re-show if a selection is active
        else:
            self.sel_stats.hide()
            self.zone_slider.hide()
            self.eff_slider.hide()

    def _on_zone_s_changed(self, _s: float) -> None:
        """User dragged the absorption-zone slider — recompute the bands live at the new threshold."""
        self._refresh_selection_stats()

    def _on_eff_f_changed(self, _f: float) -> None:
        """User dragged the effective-aggression slider — recompute the neon bands live."""
        self._refresh_selection_stats()

    # ------------------------------------------------------------------
    # Magic Selection (Mode 10) — aggregate the buckets inside the box
    # ------------------------------------------------------------------
    @staticmethod
    def _aggregate_selection(filtered: list, x0: float, y0: float, x1: float, y1: float,
                             target_vol: float) -> dict:
        """Aggregate every selection stat from the buckets inside the box. FLOW stats are
        price-band-filtered from each bucket's ``levels`` (truly 'in the box'); positioning / liq /
        flow-rate stats are SPAN-level (bucket scalars have no per-price split, so they aggregate the
        whole buckets in the x-span). Buckets selected by centre index in [x0, x1]. {} if empty."""
        n_all = len(filtered)
        lo_i = max(0, int(math.ceil(x0)))
        hi_i = min(n_all - 1, int(math.floor(x1)))
        if hi_i < lo_i:
            return {}
        sel = filtered[lo_i:hi_i + 1]
        band_lo, band_hi = (y0, y1) if y0 <= y1 else (y1, y0)

        # FLOW — price-band-filtered from each bucket's levels ladder
        buy = sell = 0.0
        poc_map: dict = {}
        for b in sel:
            for ps, lv in (b.get("levels", {}) or {}).items():
                try:
                    p = float(ps)
                except (ValueError, TypeError):
                    continue
                if band_lo <= p <= band_hi:
                    bb = float(lv.get("b", 0.0)); ss = float(lv.get("s", 0.0))
                    buy += bb; sell += ss
                    poc_map[p] = poc_map.get(p, 0.0) + bb + ss
        vol = buy + sell
        delta = buy - sell
        poc = max(poc_map, key=poc_map.get) if poc_map else None

        # SPAN — whole buckets in the x-span (no per-price data for these scalars)
        def S(k): return sum(float(b.get(k, 0.0)) for b in sel)
        opL, opS, clL, clS = S("opL"), S("opS"), S("clL"), S("clS")
        full_buy, full_sell, full_vol = S("buy_vol"), S("sell_vol"), S("curr_vol")
        dur = sum(max(0.0, float(b.get("end_time", 0.0)) - float(b.get("start_time", 0.0))) for b in sel)
        imb = sum(abs(float(b.get("buy_vol", 0.0)) - float(b.get("sell_vol", 0.0))) for b in sel)
        ticks = sum(max(1.0, abs(float(b.get("high", 0.0)) - float(b.get("low", 0.0))) / config.TICK_SIZE)
                    for b in sel)
        highs = [float(b.get("high", 0.0)) for b in sel if b.get("high")]
        lows = [float(b.get("low", 0.0)) for b in sel if b.get("low")]
        return {
            "vol": vol, "buy": buy, "sell": sell, "delta": delta,
            "delta_pct": (delta / vol * 100.0) if vol else 0.0, "poc": poc,
            "opL": opL, "opS": opS, "clL": clL, "clS": clS, "oi_delta": (opL + opS) - (clL + clS),
            "liq_short": S("liq_short"), "liq_long": S("liq_long"),
            "cvd": full_buy - full_sell,
            "vel": (full_vol / dur) if dur > 0 else 0.0,
            "vpin": (imb / (len(sel) * target_vol)) if (target_vol and sel) else 0.0,
            "buyer_er": (full_buy / ticks) if ticks else 0.0,
            "seller_er": (full_sell / ticks) if ticks else 0.0,
            "n": len(sel),
            "t_span": max(0.0, float(sel[-1].get("end_time", 0.0)) - float(sel[0].get("start_time", 0.0))),
            "band_lo": band_lo, "band_hi": band_hi,
            "open": float(sel[0].get("open", 0.0)), "close": float(sel[-1].get("close", 0.0)),
            "high": max(highs) if highs else 0.0, "low": min(lows) if lows else 0.0,
            "price_range": (max(highs) - min(lows)) if (highs and lows) else 0.0,
            "displacement": float(sel[-1].get("close", 0.0)) - float(sel[0].get("open", 0.0)),
            "_lo_i": lo_i, "_hi_i": hi_i,   # span indices for the STATE adapter (priors live before lo_i)
        }

    def _selection_state(self, filtered: list, lo_i: int, hi_i: int):
        """Region STATE for the Magic Selection — delegates to the pure ``region_state.selection_state``
        (shared with the headless accumulator). Returns ``(state, conf, dbg)``."""
        return region_state.selection_state(filtered, lo_i, hi_i)

    def _vec_sparkline(self, filtered, lo_i, hi_i, val_fn, pos_color, neg_color) -> "str | None":
        """General per-bucket BALANCE trajectory sparkline across the selection, left-to-right.
        DESCRIPTIVE — shows WHERE balance shifted, not a forecast (1m flow is descriptive-not-predictive).

        Per-bucket value is the RAW signed difference ``val_fn(b)`` (buyer_er-seller_er, opL-opS, or
        clL-clS) — NOT normalized: normalizing E/R cancels the ticks and collapses to the delta fraction
        (already shown as Delta), and keeping it raw preserves the magnitude nuance (effort-vs-result for
        E/R — sellers pushing HARD while price stalls reads as a deep block; how lopsided opening/closing
        is for the vectors). Heights AUTO-SCALE to the selection's own range with zero PINNED to a fixed
        midline — positives scale to the max positive, negatives to the max |negative| (full height both
        sides; one spiky bucket can't compress the other side) — so the ``pos_color``→``neg_color`` flip
        always sits at TRUE zero, never at the selection's mean. ``pos_color`` when val>0, ``neg_color``
        <0, gray =0. < SPARK_MIN buckets → None; > SPARK_WIDTH → vol-weighted downsample to that many bins.

        HONEST: the positioning vectors (opL/opS, clL/clS) are NOISIER than E/R — the 4-vector is built
        from a 5s OI poll sprayed across trades by timing (not exact per-trade), so those trajectories may
        be choppier. Shown honestly, NOT smoothed."""
        sel = filtered[lo_i:hi_i + 1]
        if len(sel) < config.SPARK_MIN:
            return None

        pts = [(val_fn(b), float(b.get("curr_vol", 0.0))) for b in sel]
        W = config.SPARK_WIDTH
        if len(pts) > W:                                   # vol-weighted downsample of the RAW diff
            n = len(pts)
            vals = []
            for w in range(W):
                grp = pts[w * n // W:(w + 1) * n // W] or [pts[min(w * n // W, n - 1)]]
                vsum = sum(v for _, v in grp)
                vals.append(sum(x * v for x, v in grp) / vsum if vsum > 0
                            else sum(x for x, _ in grp) / len(grp))
        else:
            vals = [x for x, _ in pts]

        # auto-scale each side to its own extreme (full height both sides), zero pinned to the midline.
        pos_max = max((v for v in vals if v > 0), default=0.0)
        neg_max = max((-v for v in vals if v < 0), default=0.0)
        blocks = "▁▂▃▄▅▆▇█"
        gray = "#9aa0aa"
        out = []
        for v in vals:
            if v > 0:
                scaled = (v / pos_max) if pos_max > 0 else 0.0   # (0, +1]  -> upper half (pos_color)
            elif v < 0:
                scaled = (v / neg_max) if neg_max > 0 else 0.0   # [-1, 0)  -> lower half (neg_color)
                col = neg_color
            else:
                scaled = 0.0
            # near-balanced -> a flat gray ▄ baseline so the zero band is VISIBLE. Block chars rise from
            # the bottom and can't draw a true horizontal midline, so this gray baseline + the colour
            # flip ARE the crossover indicator (an honest approximation, not a faked line).
            if abs(scaled) < config.SPARK_ZERO_BAND:
                out.append(f"<span style='color:{gray}'>▄</span>")
                continue
            col = pos_color if v > 0 else neg_color
            lvl = max(0, min(7, round((scaled + 1) / 2 * 7)))    # -max -> ▁ … +max -> █
            out.append(f"<span style='color:{col}'>{blocks[lvl]}</span>")
        return "".join(out)

    def _selection_stat_lines(self, d: dict, state=None, conf=None, dbg=None, vpin_tier_=None,
                              spark_op=None, spark_cl=None, flip=None,
                              absorp=None, eff_agg=None) -> "list[str]":
        """Format the aggregate into the StatsOverlay box, mirroring the forming-bucket readout's
        structure (O H L C header + FLOW / POSITIONING / EFFORT / READ sections, same line formats
        and colours). Section tags note the honesty split: FLOW = price-filtered (in the box);
        POSITIONING / EFFORT / READ = whole buckets in the x-span."""
        K = self._fmt_k
        PD = config.PRICE_DECIMALS
        g, r, gold, blu, pu, gray = "#2ecc71", "#e74c3c", "#f1c40f", "#3498db", "#9b59b6", "#9aa0aa"
        cyan, mag = "#00e5ff", "#ff4dff"
        def span(t, c): return f"<span style='color:{c}'>{t}</span>"
        def sep(t): return f"<span style='color:#5a6170;font-size:9px;letter-spacing:2px'>{t}</span>"
        def pf(v): return f"{v:.{PD}f}"
        def sk(v): return ("+" if v >= 0 else "-") + K(abs(v))
        o, h, l, c = d["open"], d["high"], d["low"], d["close"]
        poc = pf(d["poc"]) if d.get("poc") is not None else "--"
        dl = sk(d["delta"]) + f" ({d['delta_pct']:+.0f}%)"
        vel, vpin = f"{d['vel']:.0f}/s", f"{d['vpin']:.2f}"
        ber, ser = f"{d['buyer_er']:.1f}", f"{d['seller_er']:.1f}"
        nb = f"{d['n']} buckets · {d['t_span']:.0f}s"
        cc = g if c >= o else r
        # 4-vector: colour ONLY the two dominant vectors (the ones that drove the span); the other
        # two render dim; a zero vector never lights up — exactly as the forming-bucket box does.
        vmag = {"opL": d["opL"], "opS": d["opS"], "clS": d["clS"], "clL": d["clL"]}
        vclr = {"opL": g, "opS": r, "clS": blu, "clL": pu}
        top2 = set(sorted(vmag, key=lambda k: vmag[k], reverse=True)[:2])
        def vc(name): return vclr[name] if (name in top2 and vmag[name] > 0) else gray
        lines = [
            f"O {pf(o)}  H {pf(h)}  L {pf(l)}  {span('C ' + pf(c), cc)}",
            f"{nb}   {span('POC ' + poc, gold)}",
            f"Band {span(pf(d['band_lo']) + '–' + pf(d['band_hi']), gold)}",
            sep("FLOW · IN BOX"),
            f"Volume {K(d['vol'])}",
            f"{span('Sell ' + K(d['sell']), r if d['sell'] > d['buy'] else gray)} | "
            f"{span('Buy ' + K(d['buy']), g if d['buy'] > d['sell'] else gray)}",
            f"Delta {span(dl, g if d['delta'] >= 0 else r)}",
            f"OI Δ {span(sk(d['oi_delta']), g if d['oi_delta'] >= 0 else r)}",
            f"CVD {span(sk(d['cvd']), g if d['cvd'] >= 0 else r)}",
            sep("POSITIONING · SPAN"),
            f"{span('OpL ' + K(d['opL']), vc('opL'))} | {span('OpS ' + K(d['opS']), vc('opS'))}",
            f"{span('ClS ' + K(d['clS']), vc('clS'))} | {span('ClL ' + K(d['clL']), vc('clL'))}",
            f"{span('LiqSh ' + K(d['liq_short']), cyan)} | {span('LiqLn ' + K(d['liq_long']), mag)}",
            sep("EFFORT · SPAN"),
            span("Buyer E/R " + ber, g if d['buyer_er'] > d['seller_er'] else gray),
            span("Seller E/R " + ser, r if d['seller_er'] > d['buyer_er'] else gray),
            sep("READ · SPAN"),
            # VPIN coloured by the ADAPTIVE tier (same percentile mechanism as the other VPIN
            # sites, ranked vs same-length windows): toxic=crimson, warn=gold, normal=gray.
            f"VEL {span(vel, gold)}   "
            f"VPIN {span(vpin, {vpin_adaptive.TOXIC: r, vpin_adaptive.WARN: gold}.get(vpin_tier_, gray))}",
        ]
        # BULL/BEAR absorption summed over the selection (volume). Read the bull:bear RATIO for the
        # directional lean — the raw totals scale with how many buckets are selected. DESCRIPTIVE.
        if absorp:
            abu, abe = absorp
            if abu > 0 or abe > 0:
                if abu >= abe:
                    lean = f"{abu / abe:.2f}× bull" if abe > 0 else "bull-only"
                else:
                    lean = f"{abe / abu:.2f}× bear" if abu > 0 else "bear-only"
                lines.append(sep("ABSORPTION · VOL"))
                lines.append(f"{span('Bull ' + K(abu), g)} · {span('Bear ' + K(abe), r)} · "
                             f"{span(lean, gold)}")
        # EFFECTIVE AGGRESSION summed over the selection (the mirror: heavy volume that MOVED price). NEON
        # green/red to match its zones. Read the bull:bear lean; totals scale with selection length.
        if eff_agg:
            ebu, ebe = eff_agg
            if ebu > 0 or ebe > 0:
                neon_g, neon_r = "#00ff80", "#ff2d6b"
                if ebu >= ebe:
                    elean = f"{ebu / ebe:.2f}× bull" if ebe > 0 else "bull-only"
                else:
                    elean = f"{ebe / ebu:.2f}× bear" if ebu > 0 else "bear-only"
                lines.append(sep("EFF-AGG · VOL"))
                lines.append(f"{span('Bull ' + K(ebu), neon_g)} · {span('Bear ' + K(ebe), neon_r)} · "
                             f"{span(elean, gold)}")
        # STATE — the same 12-state classifier the per-bucket hover box uses, run on the region,
        # followed by the same calibration debug lines (top-3 states + winner factor breakdown).
        if state is not None:
            lines.append(f"STATE {bucket_state.render_state_line(state, conf)}")
        if dbg:
            lines += dbg
        # the per-bucket positioning trajectories grouped in their OWN section at the very bottom. DESCRIPTIVE
        # (where balance shifted, not a forecast). Crossover = colour flip at true zero; near-balanced buckets
        # render as a gray ▄ baseline (block chars can't draw a real midline). Op/Cl are noisy (5s-OI
        # attribution), shown unsmoothed. Labels nbsp-padded so the columns align. (E/R is now its own panel.)
        nbsp = " "
        def traj(lbl, sp):
            return f"{span(lbl.ljust(6).replace(' ', nbsp) + nbsp + '→' + nbsp, gray)}{sp}"
        # a thin spacer line between sparklines so they breathe (the gray ▄ baseline reads clearer with
        # rows separated).
        spacer = "<span style='font-size:6px'>&nbsp;</span>"
        traj_lines = []
        for lbl, sp in (("Op L/S", spark_op), ("Cl L/S", spark_cl)):
            if not sp:
                continue
            if traj_lines:
                traj_lines.append(spacer)
            traj_lines.append(traj(lbl, sp))
        if traj_lines or flip is not None:
            lines.append(sep("FLOW TRAJECTORY →"))
            lines += traj_lines
            # direction-aware balance-flip, headline = SUSTAIN ('held X% of the remainder'), matching the
            # dashed yellow vline. Descriptive, not a forecast. no-flip shows the direction it looked for;
            # ·messy = choppy settle (absorption); ·AMBIG = net move wasn't cleanly directional.
            if flip is not None:
                if flip["no_flip"]:
                    fv, flbl, fcol = f"no flip {flip['dir']}", "Flip →", gold
                elif flip["forming"]:
                    # TENTATIVE: held-so-far % + maturity (p/N) + explicit 'unconfirmed'. dim amber to
                    # match the dotted chart marker — a WATCH heads-up, never a confirmed signal.
                    fv = (f"{flip['dir']} {round(flip['sustain'] * 100)}% · "
                          f"{flip['post_n']}/{flip['need']} · unconfirmed")
                    flbl, fcol = "Forming →", "#b8932f"
                else:
                    fv = f"{flip['dir']} {round(flip['sustain'] * 100)}% held @+{flip['idx']}"
                    if flip["messy"]:
                        fv += " ·messy"
                    flbl, fcol = "Flip →", gold
                if flip["ambig"]:
                    fv += " ·AMBIG"
                lines.append(f"{span(flbl, gray)} {span(fv, fcol)}")
        return lines

    @staticmethod
    def _selection_signature(rect, filtered: list, lo_i: int, hi_i: int,
                             toggles: tuple, sliders: tuple, tv: float, vpin_window: int) -> tuple:
        """Fix 1 change-detection key: everything the selection readout's OUTPUT depends on. Two frames with
        an equal signature produce bit-for-bit identical overlays, so the heavy recompute can be skipped.

        The closed buckets in [lo_i, hi_i] are immutable, so only the LIVE EDGE can change them — and it only
        affects this selection's output when (a) the selection TOUCHES the live edge, or (b) the adaptive-VPIN
        baseline is active (``window_vpin_samples`` non-empty, i.e. n_sel <= min(vpin_window, n_all)), since
        that baseline's last sample is the live edge. When neither holds, the live edge is irrelevant and is
        dropped from the key — so a static selection that doesn't touch the live edge gets a STABLE key across
        ticks and is skipped (provably exact: identical inputs -> identical output). ``rect`` values are kept
        EXACT (no rounding) so a real geometry change can never collide with a skip."""
        n_all = len(filtered)
        n_sel = hi_i - lo_i + 1
        touches_live = hi_i >= n_all - 1
        vpin_active = (min(vpin_window, n_all) >= n_sel) and (tv > 0)
        if touches_live or vpin_active:
            lb = filtered[-1]                              # the live edge (last bucket); its mutable scalars
            live_fp = (lb.get("curr_vol", 0.0), lb.get("buy_vol", 0.0), lb.get("sell_vol", 0.0),
                       lb.get("opL", 0.0), lb.get("opS", 0.0), lb.get("clL", 0.0), lb.get("clS", 0.0),
                       lb.get("open", 0.0), lb.get("close", 0.0), lb.get("high", 0.0), lb.get("low", 0.0))
        else:
            live_fp = None
        return (tuple(rect), n_all, lo_i, hi_i, touches_live, live_fp, toggles, sliders, tv)

    def _reposition_sel_box(self, rect) -> None:
        """Place the screen-space stats box + the two zone sliders at a free selection corner. View-dependent
        and CHEAP, so it runs every frame (even when the heavy compute is skipped) — keeps the box glued to the
        selection while the chart pans / follows the live edge."""
        x0, y0, x1, y1 = rect

        def to_self(dx, dy):
            sc = self.vb.mapViewToScene(QtCore.QPointF(float(dx), float(dy)))
            return self.mapFromGlobal(self.plot.mapToGlobal(self.plot.mapFromScene(sc)))
        p1, p2 = to_self(x0, y1), to_self(x1, y0)   # the selection's screen-space rect
        sx0, sx1 = min(p1.x(), p2.x()), max(p1.x(), p2.x())
        sy0, sy1 = min(p1.y(), p2.y()), max(p1.y(), p2.y())
        if self.show_sel_stats:                # 'h' toggles ONLY the box; overlays above already drawn
            bx, by = self._best_box_pos(sx0, sy0, sx1, sy1,
                                        self.sel_stats.width(), self.sel_stats.height())
            self.sel_stats.move(bx, by)
            self.sel_stats.show_raise()
            # the two zone-threshold sliders ride STACKED (absorption above eff-agg) just under the box;
            # if the pair would run off the bottom, stack them ABOVE the box instead (same order).
            gap, sh = 3, self.zone_slider.height()
            below_zone = by + self.sel_stats.height() + gap
            if below_zone + 2 * sh + gap <= self.height():
                zone_y = below_zone
            else:
                zone_y = max(0, by - gap - sh - (sh + gap))   # pair above the box, zone on top
            self.zone_slider.move(bx, zone_y)
            self.zone_slider.show(); self.zone_slider.raise_()
            self.eff_slider.move(bx, zone_y + sh + gap)
            self.eff_slider.show(); self.eff_slider.raise_()
        else:
            self.sel_stats.hide()
            self.zone_slider.hide()
            self.eff_slider.hide()

    def _refresh_selection_stats(self) -> None:
        """Live Magic-Selection readout: aggregate the buckets inside the box + show the stats box.
        Runs each frame, so a selection reaching the live edge updates as buckets form."""
        rect = self.drawer.selection_rect()
        if rect is None or self.scanner_mode != "bucket_canvas":
            self.sel_stats.hide()
            self.zone_slider.hide()
            self.eff_slider.hide()
            self._hide_flip()
            self.bc_absorp_zones.setVisible(False)
            self.bc_eff_zones.setVisible(False)
            self.bc_abs_strip.setVisible(False)
            self.bc_exh_strip.setVisible(False); self.bc_exh_mid.setVisible(False)
            self.bc_eff_strip.setVisible(False)
            self.bc_er_strip.setVisible(False)
            self.bc_panel_sep.setVisible(False)
            self._clear_liq_panel()                                           # liquidation panel: clear on teardown
            for _b in self._spread_badges.values():
                _b.hide()
            self.phase_tbl.hide()
            for _ly in self.bc_phase.values():
                _ly.setVisible(False)
            self._panel_hovers = []
            self._sel_sig = None        # Fix 1: hidden -> force a full recompute when a selection returns
            return
        filtered, _x, _a = self._build_scanner_buckets()
        if not filtered:
            self.sel_stats.hide()
            self.zone_slider.hide()
            self.eff_slider.hide()
            self._hide_flip()
            self.bc_absorp_zones.setVisible(False)
            self.bc_eff_zones.setVisible(False)
            self.bc_abs_strip.setVisible(False)
            self.bc_exh_strip.setVisible(False); self.bc_exh_mid.setVisible(False)
            self.bc_eff_strip.setVisible(False)
            self.bc_er_strip.setVisible(False)
            self.bc_panel_sep.setVisible(False)
            self._clear_liq_panel()                                           # liquidation panel: clear on teardown
            for _b in self._spread_badges.values():
                _b.hide()
            self.phase_tbl.hide()
            for _ly in self.bc_phase.values():
                _ly.setVisible(False)
            self._panel_hovers = []
            self._sel_sig = None        # Fix 1: hidden -> force a full recompute when a selection returns
            return
        x0, y0, x1, y1 = rect
        tv = (self._last_snap or {}).get("target_vol") or config.DEFAULT_TARGET_VOL
        # Fix 1 — change-detection: skip the entire heavy recompute when nothing that affects the output
        # changed since last frame (only the cheap view-follow box reposition still runs). lo_i/hi_i match
        # _aggregate_selection's exactly, so the signature is computed before the aggregate's O(N) work too.
        n_all = len(filtered)
        lo_i = max(0, int(math.ceil(x0))); hi_i = min(n_all - 1, int(math.floor(x1)))
        if hi_i >= lo_i:
            sig = self._selection_signature(
                rect, filtered, lo_i, hi_i,
                (self.show_abs_strip, self.show_eff_strip, self.show_er_strip, self.show_exh_strip,
                 tuple(self.show_phase[p] for p in self._PHASES), self.show_state, self.show_sel_stats,
                 self.show_liq, self.show_phase_table),
                (self.zone_slider.value_s(), self.eff_slider.value_s()), tv, config.VPIN_ADAPT_WINDOW)
            if sig == self._sel_sig:
                self._reposition_sel_box(rect)   # reuse last frame's overlays; just keep the box glued
                return
            self._sel_sig = sig
        else:
            self._sel_sig = None
        agg = self._aggregate_selection(filtered, x0, y0, x1, y1, tv)
        if not agg:
            self.sel_stats.hide()
            self.zone_slider.hide()
            self.eff_slider.hide()
            self._hide_flip()
            self.bc_absorp_zones.setVisible(False)
            self.bc_eff_zones.setVisible(False)
            self.bc_abs_strip.setVisible(False)
            self.bc_exh_strip.setVisible(False); self.bc_exh_mid.setVisible(False)
            self.bc_eff_strip.setVisible(False)
            self.bc_er_strip.setVisible(False)
            self.bc_panel_sep.setVisible(False)
            self._clear_liq_panel()                                           # liquidation panel: clear on teardown
            for _b in self._spread_badges.values():
                _b.hide()
            self.phase_tbl.hide()
            for _ly in self.bc_phase.values():
                _ly.setVisible(False)
            self._panel_hovers = []
            self._sel_sig = None        # Fix 1: hidden -> force a full recompute when a selection returns
            return
        # classify the region with the SAME 12-state engine the per-bucket box uses (only when the
        # STATE lines are visible — 'y' toggles; hidden by default), then size the box and place it
        # at whichever selection corner has room (so it never hides off-screen).
        if self.show_state:
            state, conf, dbg = self._selection_state(filtered, agg["_lo_i"], agg["_hi_i"])
        else:
            state, conf, dbg = None, None, []
        # adaptive VPIN tier for the selection — ranked against same-length windows over the recent
        # baseline (apples-to-apples regardless of selection size), via the shared percentile helper.
        n_sel = agg["_hi_i"] - agg["_lo_i"] + 1
        v_warn, v_toxic = vpin_adaptive.vpin_cutpoints(
            vpin_adaptive.window_vpin_samples(filtered[-config.VPIN_ADAPT_WINDOW:], n_sel, tv))
        vtier = vpin_adaptive.vpin_tier(agg["vpin"], v_warn, v_toxic)
        # per-bucket trajectory sparklines (raw signed diff, auto-scaled, zero-pinned midline): Op L/S (opL-opS
        # init, green/red), Cl L/S (clL-clS exit, purple/blue — distinct palette). E/R was promoted out of here
        # into its OWN selection panel ('3'). Box colours reused: opL=green/opS=red, clL=purple/clS=blue.
        lo, hi = agg["_lo_i"], agg["_hi_i"]
        spark_op = self._vec_sparkline(filtered, lo, hi,
                                       lambda b: b.get("opL", 0.0) - b.get("opS", 0.0),
                                       "#2ecc71", "#e74c3c")
        spark_cl = self._vec_sparkline(filtered, lo, hi,
                                       lambda b: b.get("clL", 0.0) - b.get("clS", 0.0),
                                       "#9b59b6", "#3498db")
        # DIRECTION-AWARE balance-flip detector: net move (last_close - first_open, normalised by range)
        # picks the relevant crossing — DOWN move -> sellers-lose-control S→B, UP -> buyers-lose B→S,
        # ambiguous (|disp_frac| < band) -> best crossing + ·AMBIG. CLARITY score, NOT a reversal forecast.
        er_seq = [filtered[i].get("buyer_er", 0.0) - filtered[i].get("seller_er", 0.0)
                  for i in range(lo, hi + 1)]
        rng = agg.get("price_range", 0.0)
        disp_frac = (agg["displacement"] / rng) if rng > 0 else 0.0
        net_dir = 0 if abs(disp_frac) < config.FLIP_AMBIG_BAND else (1 if disp_frac > 0 else -1)
        flip = (region_state.balance_flip(er_seq, net_dir)
                if (hi - lo + 1) >= config.SPARK_MIN else None)
        self._update_flip_line(flip, lo, rect)
        # BULL/BEAR absorption over the selected buckets (volume) + the absorbing ZONES, all from ONE shared
        # per-bucket pass (bull, bear, s arrays). Totals = the bull:bear regional lean (scale with length).
        abs_bull_arr, abs_bear_arr, abs_sval = region_state.absorption_series(
            filtered, lo, hi, config.ABSORP_VOL_WINDOW)
        abs_bull, abs_bear = sum(abs_bull_arr), sum(abs_bear_arr)
        # ADAPTIVE zone threshold: on a FRESH selection re-seed the slider to the median nonzero-s
        # (defended -> high, quiet -> low); a manual drag then pins it until the selection changes. The
        # slider's current value IS the live threshold; a clean trend stays empty even at the bottom.
        sel = getattr(self.drawer, "_selection", None)
        sel_id = id(sel) if sel is not None else None
        if sel_id != self._zone_sel_id:
            self._zone_sel_id = sel_id
            self.zone_slider.set_value(region_state.absorption_default_s(
                abs_bull_arr, abs_bear_arr, abs_sval))
        s_thr = self.zone_slider.value_s()
        # ABSORPTION ZONES — sustained consecutive heavy-bull/bear runs (>= ABSORP_ZONE_MIN_RUN, s >= slider)
        # -> green/red price x time bands at the run's price range, labelled with absorbed volume, projected
        # (dashed) to the selection's right edge. DESCRIPTIVE; rare (no band = no sustained defense there).
        x_right = hi + 0.5
        zspecs = [(z["start"] - 0.5, z["end"] + 0.5, x_right, z["plo"], z["phi"], z["side"],
                   f"{z['side'].upper()} {self._fmt_k(z['vol'])}")
                  for z in region_state.zones_from_series(
                      abs_bull_arr, abs_bear_arr, abs_sval, lo, filtered,
                      s_thr, config.ABSORP_ZONE_MIN_RUN)]
        self.bc_absorp_zones.update_zones(zspecs)
        self.bc_absorp_zones.setVisible(bool(zspecs))
        # EFFECTIVE-AGGRESSION zones — the MIRROR (heavy volume that MOVED price its way): eff_bull/bear =
        # V*(1-s) directional; NEON green/red bands. Own slider rides force f = eff_agg/vol_norm, re-seeded
        # per selection to the median nonzero-f (forceful -> high, ordinary -> low). Dot = forceful boundary.
        eff_bull_arr, eff_bear_arr, eff_fval = region_state.eff_agg_from_absorption(  # Fix 3: reuse abs_sval
            filtered, lo, hi, config.EFF_AGG_FORCE_WINDOW, abs_sval)
        eff_bull, eff_bear = sum(eff_bull_arr), sum(eff_bear_arr)
        if sel_id != self._eff_sel_id:
            self._eff_sel_id = sel_id
            self.eff_slider.set_value(region_state.eff_agg_default_f(
                eff_bull_arr, eff_bear_arr, eff_fval))
        f_thr = self.eff_slider.value_s()
        especs = [(z["start"] - 0.5, z["end"] + 0.5, x_right, z["plo"], z["phi"], z["side"],
                   f"{z['side'].upper()} {self._fmt_k(z['vol'])}")
                  for z in region_state.eff_zones_from_series(
                      eff_bull_arr, eff_bear_arr, eff_fval, lo, filtered,
                      f_thr, config.EFF_AGG_ZONE_MIN_RUN)]
        self.bc_eff_zones.update_zones(especs)
        self.bc_eff_zones.setVisible(bool(especs))
        # The selection panels stack BELOW the box — EXHAUSTION, then EFF-AGG evolution, then EFFORT/RESULT —
        # but only the VISIBLE ones take a slot: each sits directly under the previous SHOWN panel, so hiding
        # one ('1'/'2'/'3') slides the ones below it UP into the gap (no blank space).
        sel_h = max(y1 - y0, config.TICK_SIZE)
        _drawable = (hi - lo + 1) >= 3
        # LIVE / FIXED-WINDOW lean panels: a fixed rolling-share window + a PRE-ROLL into real history before the
        # selection start, so panels 1-4 read the SAME at every bar regardless of where the selection begins (no
        # more values shifting when you drag the start). The selection is just the viewport.
        _lw = config.LIVE_PANEL_WINDOW                        # FIXED smoothing window (not selection-length-scaled)
        _pre0 = min(lo, config.LIVE_PANEL_WINDOW + config.ABSORP_VOL_WINDOW)   # pre-roll: enough for the trailing
        _extp = filtered[lo - _pre0:hi + 1]; _Lp = len(_extp)                  # norm (50) + the fixed window (15)
        _badge_x = hi + 0.5 + max(1.0, (hi - lo + 1) * 0.05)   # spread-badge x: just past the panels' right edge
        abs_on = self.show_abs_strip and _drawable        # slot order top->bottom: 1 abs, 2 eff, 3 er, 4 exh,
        eff_on = self.show_eff_strip and _drawable         # then 5 BEFORE, 6 START/DURING, 7 END (phase panels),
        er_on = self.show_er_strip and _drawable           # then 5-7 phase panels, then the LIQUIDATION wave (BOTTOM)
        exh_on = self.show_exh_strip and _drawable
        ph_on = {p: self.show_phase[p] and _drawable for p in self._PHASES}
        liq_on = self.show_liq and _drawable
        ph_geom = {}
        _cur = y0                                           # running bottom edge of the last placed panel
        if abs_on:
            abs_top = _cur - config.ABS_STRIP_GAP * sel_h; abs_bot = abs_top - config.ABS_STRIP_FRAC * sel_h
            _cur = abs_bot
        if eff_on:
            eff_top = _cur - config.EFF_STRIP_GAP * sel_h; eff_bot = eff_top - config.EFF_STRIP_FRAC * sel_h
            _cur = eff_bot
        if er_on:
            er_top = _cur - config.ER_STRIP_GAP * sel_h; er_bot = er_top - config.ER_STRIP_FRAC * sel_h
            _cur = er_bot
        if exh_on:
            exh_top = _cur - config.EXH_STRIP_GAP * sel_h; exh_bot = exh_top - config.EXH_STRIP_FRAC * sel_h
            _cur = exh_bot
        for _p in self._PHASES:                             # 5-7 phase panels, stacked under 1-4
            if ph_on[_p]:
                _t = _cur - config.PHASE_PANEL_GAP * sel_h; _b = _t - config.PHASE_PANEL_FRAC * sel_h
                ph_geom[_p] = (_t, _b); _cur = _b
        if liq_on:                                          # LIQUIDATION wave panel (BOTTOM)
            liq_top = _cur - config.EXH_STRIP_GAP * sel_h; liq_bot = liq_top - config.EXH_STRIP_FRAC * sel_h
            _cur = liq_bot
        # minimalist hairline divider in each gap BETWEEN consecutive visible panels (stack order)
        _bands = []
        if abs_on: _bands.append((abs_top, abs_bot))
        if eff_on: _bands.append((eff_top, eff_bot))
        if er_on: _bands.append((er_top, er_bot))
        if exh_on: _bands.append((exh_top, exh_bot))
        for _p in self._PHASES:
            if _p in ph_geom: _bands.append(ph_geom[_p])
        if liq_on: _bands.append((liq_top, liq_bot))
        _sep_ys = [(_bands[i][1] + _bands[i + 1][0]) / 2.0 for i in range(len(_bands) - 1)]
        self.bc_panel_sep.update_data(lo - 0.5, hi + 0.5, _sep_ys)
        self.bc_panel_sep.setVisible(bool(_sep_ys))
        self._panel_hovers = []   # rebuilt each refresh; each visible panel registers its y-band + raw values
        # SELECTION ABSORPTION STRIP ('1', TOP) — bull% vs bear% LEAN, NEON green (bull) / NEON purple (bear).
        # Absorption is one-sided per bucket, so there's no instantaneous ratio; we plot each side's ROLLING
        # share over a centered window (config.LEAN_WINDOW_*) — the two shares sum to 1, cross at the 50% midline
        # (even), and track the LOCAL lean as it SHIFTS across the selection (non-cumulative). SELECTION-PURE
        # (sliced; the zones keep the full-history norm). No envelope, no crossover diamonds.
        if abs_on:                                        # '1' toggles the panel
            absb, absr, _asv = region_state.absorption_series(
                _extp, 0, _Lp - 1, config.ABSORP_VOL_WINDOW)   # fixed trailing norm over real history (pre-rolled)
            bull_sh = region_state.rolling_share(absb, absr, _lw)[_pre0:]   # drop the pre-roll -> the [lo,hi] view
            bear_sh = [1.0 - s for s in bull_sh]

            def _ay(v):
                return abs_bot + v * (abs_top - abs_bot)  # share 0..1 -> panel y (0% bottom, 50% mid, 100% top)
            xs_a = list(range(lo, hi + 1))
            self.bc_abs_strip.update_data(xs_a, [_ay(v) for v in bull_sh], [_ay(v) for v in bear_sh],
                                          lo - 0.5, hi + 0.5, abs_bot, abs_top, [])
            self.bc_abs_strip.setVisible(True)
            self._panel_hovers.append({                # hover -> running bull/bear share %, labelled
                "label": "ABSORPTION", "lo": lo, "yb": abs_bot, "yt": abs_top,
                "bull": bull_sh, "bear": bear_sh, "bcol": _RGB_ABS_BULL, "rcol": _RGB_ABS_BEAR,
                "blbl": "BULL", "rlbl": "BEAR", "fmt": "pct"})
            # absorption: strongest = the LOWER share (per operator) -> bull strong when its share is lower
            self._set_spread_badge("ABSORPTION", bull_sh[-1], bear_sh[-1], bull_sh[-1] < bear_sh[-1],
                                   _badge_x, (abs_top + abs_bot) / 2.0)
        else:
            self.bc_abs_strip.setVisible(False)
            self._spread_badges["ABSORPTION"].hide()
        # SELECTION EXHAUSTION STRIP ('4', BOTTOM) — bull/bear gated exhaustion as two SYMMETRICALLY-smoothed
        # lines (0/50/100% scale); gold diamonds mark crossovers (the exhausted side swaps). Selection-scoped
        # (bounded), recomputed each frame like the sparklines/zones above.
        if exh_on:                                        # '4' toggles the panel
            # FIXED trailing baseline (LIVE_PANEL_WINDOW), pre-rolled into real history — z-baseline anchored to
            # each bar, NOT expanding-from-selection-start, so the exhaustion read is selection-independent.
            sel_exh = region_state.trailing_exhaustion(
                _extp, _pre0, _Lp - 1, _lw, config.EXH_MEASURE, config.EXH_SEL_MIN_WINDOW)
            ex_bull = [e[0] for e in sel_exh]; ex_bear = [e[1] for e in sel_exh]
            sb = region_state.envelope_symmetric(ex_bull, config.EXH_RELEASE)
            sr = region_state.envelope_symmetric(ex_bear, config.EXH_RELEASE)

            def _ey(v):
                return exh_bot + v * (exh_top - exh_bot)    # map exhaustion 0..1 -> panel price-y
            xs = list(range(lo, hi + 1))
            bull_y = [_ey(v) for v in sb]; bear_y = [_ey(v) for v in sr]
            # crossovers: sign change of (bull - bear), kept only if the NEW side holds >= EXH_CROSS_PERSIST
            d = [sb[k] - sr[k] for k in range(len(sb))]
            crosses, P = [], config.EXH_CROSS_PERSIST
            for k in range(1, len(d)):
                if d[k - 1] == 0 or d[k] == 0 or (d[k - 1] < 0) == (d[k] < 0):
                    continue
                newneg = d[k] < 0
                if all((d[j] < 0) == newneg for j in range(k, min(len(d), k + P))):
                    frac = d[k - 1] / (d[k - 1] - d[k])     # zero-crossing between k-1 and k
                    cval = sb[k - 1] + frac * (sb[k] - sb[k - 1])
                    crosses.append(((lo + k - 1) + frac, _ey(cval)))
            self.bc_exh_strip.update_data(xs, bull_y, bear_y, lo - 0.5, hi + 0.5, exh_bot, exh_top, crosses)
            self.bc_exh_strip.setVisible(True)
            self.bc_exh_mid.setData([lo - 0.5, hi + 0.5], [_ey(0.5), _ey(0.5)])   # dashed gold 50% reference
            self.bc_exh_mid.setVisible(True)
            self._panel_hovers.append({                # hover -> RAW per-bucket exhaustion %, labelled
                "label": "EXHAUSTION", "lo": lo, "yb": exh_bot, "yt": exh_top,
                "bull": ex_bull, "bear": ex_bear, "bcol": _RGB_EXH_BULL, "rcol": _RGB_EXH_BEAR,
                "blbl": "BULL", "rlbl": "BEAR", "fmt": "pct"})
        else:
            self.bc_exh_strip.setVisible(False); self.bc_exh_mid.setVisible(False)
        # SELECTION EFF-AGG STRIP ('2') — bull% vs bear% LEAN (each side's ROLLING share of effective
        # aggression over a centered window), NEON green / NEON red, crossing at the 50% midline; tracks the
        # LOCAL forcing lean as it shifts. One-sided per bucket (like absorption). SELECTION-PURE (sliced; zones
        # keep the full-history norm). No envelope, no crossover diamonds.
        if eff_on:                                        # '2' toggles the panel
            effb, effr, _efv = region_state.eff_agg_series(
                _extp, 0, _Lp - 1, config.ABSORP_VOL_WINDOW, config.EFF_AGG_FORCE_WINDOW)
            bull_sh = region_state.rolling_share(effb, effr, _lw)[_pre0:]   # drop the pre-roll -> the [lo,hi] view
            bear_sh = [1.0 - s for s in bull_sh]

            def _fy(v):
                return eff_bot + v * (eff_top - eff_bot)   # share 0..1 -> panel y (0% bottom, 50% mid, 100% top)
            xs_e = list(range(lo, hi + 1))
            self.bc_eff_strip.update_data(xs_e, [_fy(v) for v in bull_sh], [_fy(v) for v in bear_sh],
                                          lo - 0.5, hi + 0.5, eff_bot, eff_top, [])
            self.bc_eff_strip.setVisible(True)
            self._panel_hovers.append({                # hover -> running bull/bear share %, labelled
                "label": "EFF-AGG", "lo": lo, "yb": eff_bot, "yt": eff_top,
                "bull": bull_sh, "bear": bear_sh, "bcol": _RGB_EFF_BULL, "rcol": _RGB_EFF_BEAR,
                "blbl": "BULL", "rlbl": "BEAR", "fmt": "pct"})
            # eff-agg: strongest = the HIGHER share -> bull strong when its share is higher
            self._set_spread_badge("EFF-AGG", bull_sh[-1], bear_sh[-1], bull_sh[-1] > bear_sh[-1],
                                   _badge_x, (eff_top + eff_bot) / 2.0)
        else:
            self.bc_eff_strip.setVisible(False)
            self._spread_badges["EFF-AGG"].hide()
        # SELECTION EFFORT/RESULT STRIP ('3') — buy% vs sell% LEAN (each side's ROLLING share of E/R effort over
        # a centered window), green buyer / red seller, crossing at the 50% midline. E/R is two-sided (both
        # nonzero every bucket), so the rolling share reads the LOCAL effort balance as it shifts. Selection-only
        # (intrinsic per-bucket scalars). No envelope, no crossover diamonds.
        if er_on:                                         # '3' toggles the panel
            ber = [b.get("buyer_er", 0.0) for b in _extp]   # pre-rolled so the fixed window reaches real history
            ser = [b.get("seller_er", 0.0) for b in _extp]
            buy_sh = region_state.rolling_share(ber, ser, _lw)[_pre0:]   # drop the pre-roll -> the [lo,hi] view
            sell_sh = [1.0 - s for s in buy_sh]

            def _ry(v):
                z = 0.5 + (v - 0.5) * config.ER_LEAN_GAIN  # ZOOM the deviation from 50% (E/R hugs the midline)
                z = 0.0 if z < 0.0 else 1.0 if z > 1.0 else z
                return er_bot + z * (er_top - er_bot)      # zoomed share -> panel y (50% = even midline)
            xs_r = list(range(lo, hi + 1))
            self.bc_er_strip.update_data(xs_r, [_ry(v) for v in buy_sh], [_ry(v) for v in sell_sh],
                                         lo - 0.5, hi + 0.5, er_bot, er_top, [])
            self.bc_er_strip.setVisible(True)
            self._panel_hovers.append({                # hover -> running buy/sell share %, labelled
                "label": "E/R", "lo": lo, "yb": er_bot, "yt": er_top,
                "bull": buy_sh, "bear": sell_sh, "bcol": _RGB_ER_BULL, "rcol": _RGB_ER_BEAR,
                "blbl": "BUY", "rlbl": "SELL", "fmt": "pct"})
            # E/R: strongest = the HIGHER share (spread on the TRUE shares, not the zoomed display)
            self._set_spread_badge("E/R", buy_sh[-1], sell_sh[-1], buy_sh[-1] > sell_sh[-1],
                                   _badge_x, (er_top + er_bot) / 2.0)
        else:
            self.bc_er_strip.setVisible(False)
            self._spread_badges["E/R"].hide()
        # LIVE PHASE TABLES beside the panels — UP + DOWN side by side. Each phase's row OPACITY = the live
        # CONFIDENCE (posterior% from the ROLLING trailing-_lw lean spreads), smoothed by the selection's EMA.
        # The rolling window + EMA are WARMED through the _lw (~15) buckets just BEFORE the selection, so the
        # left edge is already settled instead of cold-starting. Only that warm-up pre-roll reaches outside
        # [lo,hi]; everything displayed (the trajectory, table, panels) is the [lo,hi] portion.
        # Fix 2: the WHOLE phase block (series + rolling_share + EMA + table) is gated on at least one phase
        # panel being shown — OR the 't' phase-table toggle — so when all are off it does ZERO phase work.
        if (any(self.show_phase.values()) or self.show_phase_table) and (hi - lo + 1) >= 3:
            _pre = min(lo, _lw)                                # warm-up pre-roll: up to _lw buckets before lo
            ext = filtered[lo - _pre:hi + 1]; _em = len(ext) - 1
            _absb, _absr, _av = region_state.absorption_series(ext, 0, _em, config.ABSORP_VOL_WINDOW)
            _effb, _effr, _ev = region_state.eff_agg_from_absorption(  # Fix 3: reuse _av (the absorption s)
                ext, 0, _em, config.EFF_AGG_FORCE_WINDOW, _av)
            _ber = [ext[i].get("buyer_er", 0.0) for i in range(len(ext))]
            _ser = [ext[i].get("seller_er", 0.0) for i in range(len(ext))]
            abs_sh = region_state.rolling_share(_absb, _absr, _lw)      # per-bucket rolling bull shares (warmed)
            eff_sh = region_state.rolling_share(_effb, _effr, _lw)
            er_sh = region_state.rolling_share(_ber, _ser, _lw)
            # EMA replays over the warm-up too, then we DROP the pre-roll and keep only the [lo,hi] trajectory
            up_traj = self._phase_conf_traj("up", abs_sh, eff_sh, er_sh)[_pre:]   # [BEFORE, START/DURING, END]
            dn_traj = self._phase_conf_traj("down", abs_sh, eff_sh, er_sh)[_pre:]
            self.phase_tbl.setHtml(self._phase_table_html(up_traj[-1], dn_traj[-1]))   # table = the LAST confidence
            # sit WELL right of the spread badges (which extend rightward from ~+0.05·span) so they aren't hidden
            self.phase_tbl.setPos(hi + 0.5 + max(11.0, (hi - lo + 1) * 0.28), y0)
            self.phase_tbl.show()
            # PHASE PANELS ('5'-'7') — each merged phase's live confidence as two lines, UP (green) / DOWN (red)
            xs_p = list(range(lo, hi + 1))
            for _pi, _p in enumerate(self._PHASES):
                if _p in ph_geom:
                    _t, _b = ph_geom[_p]
                    up_line = [t[_pi] for t in up_traj]; dn_line = [t[_pi] for t in dn_traj]
                    up_y = [_b + (v / 100.0) * (_t - _b) for v in up_line]
                    dn_y = [_b + (v / 100.0) * (_t - _b) for v in dn_line]
                    self.bc_phase[_p].update_data(xs_p, up_y, dn_y, lo - 0.5, hi + 0.5, _b, _t, [])
                    self.bc_phase[_p].setVisible(True)
                    self._panel_hovers.append({            # hover -> running UP/DOWN opacity %, labelled
                        "label": _p, "lo": lo, "yb": _b, "yt": _t,
                        "bull": [v / 100.0 for v in up_line], "bear": [v / 100.0 for v in dn_line],
                        "bcol": _RGB_ER_BULL, "rcol": _RGB_ER_BEAR, "blbl": "UP", "rlbl": "DOWN", "fmt": "pct"})
                else:
                    self.bc_phase[_p].setVisible(False)
        else:
            self.phase_tbl.hide()
            for _ly in self.bc_phase.values():
                _ly.setVisible(False)
        # LIQUIDATION PRESSURE WAVE ('l', BOTTOM). Net forced flow = liq_short − liq_long per bucket (forced BUYS
        # minus forced SELLS); a FIXED-window rolling SUM (LIQ_WAVE_WINDOW, pre-rolled into real history) = the
        # "wave": CYAN up = forced buying (short squeeze building), MAGENTA down = forced selling (long flush),
        # about a dashed zero baseline. Signed-log scaled (liqs are spiky). RISING = a cascade building (surf it);
        # HOLD/FADE = is the wave still going. Drawn in place (setData) -> no ghost data.
        if liq_on:
            net = np.array([float(b.get("liq_short", 0.0)) - float(b.get("liq_long", 0.0)) for b in _extp], float)
            _w = config.LIQ_WAVE_WINDOW
            _cs = np.concatenate([[0.0], np.cumsum(net)])
            wave = np.array([_cs[k + 1] - _cs[max(0, k + 1 - _w)] for k in range(len(net))])[_pre0:]  # trailing sum
            if wave.size:
                comp = np.sign(wave) * np.log1p(np.abs(wave))                  # tame the spiky liquidation tail
                ex = np.arange(lo, hi + 1, dtype=float)
                lmid = (liq_top + liq_bot) / 2.0; lhalf = (liq_top - liq_bot) / 2.0 * 0.88
                lscale = max(float(np.max(np.abs(comp))), 1e-9)

                def _lqy(v):
                    return lmid + (np.asarray(v, dtype=float) / lscale) * lhalf
                sxn, vpos, vneg = _split_curve_by_sign(ex, comp)              # cyan/magenta halves meet ON the midline
                self.bc_liq_zero.setData([lo - 0.5, hi + 0.5], [lmid, lmid])
                self.bc_liq_pos.setData(sxn, _lqy(vpos)); self.bc_liq_neg.setData(sxn, _lqy(vneg))
                for _it in (self.bc_liq_zero, self.bc_liq_neg, self.bc_liq_pos):
                    _it.setVisible(True)
                self._panel_hovers.append({                                   # hover -> wave value + this bucket's net
                    "label": "LIQ WAVE", "lo": lo, "yb": liq_bot, "yt": liq_top,
                    "bull": list(wave), "bear": list(net[_pre0:]), "bcol": (0, 243, 255), "rcol": (255, 0, 162),
                    "blbl": "WAVE", "rlbl": "net", "fmt": "vol"})
            else:
                self._clear_liq_panel()
        else:
            self._clear_liq_panel()
        self.sel_stats.set_content(
            self._selection_stat_lines(agg, state, conf, dbg, vtier,
                                       spark_op, spark_cl, flip,
                                       (abs_bull, abs_bear), (eff_bull, eff_bear)), "")
        self._reposition_sel_box(rect)   # place the screen-space box + sliders (also runs on the skip path)

    def _update_flip_line(self, flip, lo_i: int, rect) -> None:
        """Draw/refresh the balance-flip vline at the flip bucket, spanning the selection's y-band. TWO
        mutually-exclusive treatments at the SAME x (one event, two maturities): CONFIRMED = solid DASHED
        bright yellow, tag 'FLIP dir nn% held' (the new side held >=60% over >=4 — a switch that STAYED);
        FORMING = dim DOTTED amber, thinner, tag '⋯ FORMING dir nn% · p/N · unconfirmed' (pre-run held but
        the post-run is too short to judge — a tentative WATCH heads-up, NOT a signal/forecast; it
        solidifies into the confirmed line if it holds, or vanishes if it reverts). Neither % is a reversal
        probability. Data coords (x = bucket index); ignoreBounds so it never perturbs auto-range."""
        # SUPPRESS both lines when nothing qualified — a line here is a visual claim "the balance
        # switched/is switching"; drawing one when the dominant side held would assert a turn that didn't
        # happen. Absence honestly says "nothing"; the box still shows "no flip".
        if flip is None or flip["no_flip"]:
            self._hide_flip()
            return
        x = lo_i + flip["idx"]
        ylo, yhi = (rect[1], rect[3]) if rect[1] <= rect[3] else (rect[3], rect[1])
        pct = round(flip["sustain"] * 100)
        if flip["forming"]:
            # TENTATIVE: dim dotted amber, thin, 'unconfirmed' — shows the crossing forming with honest
            # partial progress (held-so-far % + maturity p/N). z BELOW the confirmed pair.
            if self._forming_line is None:
                self._forming_line = pg.PlotCurveItem(
                    pen=pg.mkPen((241, 196, 15, 110), width=1.0, style=QtCore.Qt.DotLine))
                self._forming_line.setZValue(84)
                self.plot.addItem(self._forming_line, ignoreBounds=True)
                self._forming_label = pg.TextItem(anchor=(0.5, 1.0))
                self._forming_label.setZValue(85)
                self.plot.addItem(self._forming_label, ignoreBounds=True)
            self._forming_line.setData([x, x], [ylo, yhi])
            self._forming_line.setVisible(True)
            tag = (f"⋯ FORMING {flip['dir']} {pct}% · {flip['post_n']}/{flip['need']} · unconfirmed"
                   + (" ·AMBIG" if flip["ambig"] else ""))
            self._forming_label.setHtml(f"<span style='color:#b8932f;font-style:italic'>{tag}</span>")
            self._forming_label.setPos(x, yhi)
            self._forming_label.setVisible(True)
            if self._flip_line is not None:        # confirmed pair off (mutually exclusive)
                self._flip_line.setVisible(False)
                self._flip_label.setVisible(False)
            return
        # CONFIRMED: solid dashed bright yellow. headline = SUSTAIN ('held X% of the remainder'); '·messy'
        # = choppy settle (e.g. absorption); '·AMBIG' = net move wasn't cleanly directional.
        if self._flip_line is None:
            self._flip_line = pg.PlotCurveItem(
                pen=pg.mkPen("#f1c40f", width=1.5, style=QtCore.Qt.DashLine))
            self._flip_line.setZValue(86)
            self.plot.addItem(self._flip_line, ignoreBounds=True)
            self._flip_label = pg.TextItem(color="#f1c40f", anchor=(0.5, 1.0))
            self._flip_label.setZValue(87)
            self.plot.addItem(self._flip_label, ignoreBounds=True)
        self._flip_line.setData([x, x], [ylo, yhi])
        self._flip_line.setVisible(True)
        tag = (f"FLIP {flip['dir']} {pct}% held"
               + (" ·messy" if flip["messy"] else "") + (" ·AMBIG" if flip["ambig"] else ""))
        self._flip_label.setText(tag)
        self._flip_label.setPos(x, yhi)
        self._flip_label.setVisible(True)
        if self._forming_line is not None:         # forming pair off (mutually exclusive)
            self._forming_line.setVisible(False)
            self._forming_label.setVisible(False)

    def _hide_flip(self) -> None:
        for ln, lb in ((self._flip_line, self._flip_label),
                       (self._forming_line, self._forming_label)):
            if ln is not None:
                ln.setVisible(False)
                lb.setVisible(False)

    def _best_box_pos(self, sx0: float, sy0: float, sx1: float, sy1: float, bw: int, bh: int):
        """Place the stats box on whichever side of the selection has room, so it stays visible as
        the chart moves. Candidates sit just OUTSIDE the box: beside it (right/left, corner-aligned)
        OR above/below it (top/bottom, corner-aligned) — 8 options around all four corners. First
        that fully fits the window wins; if none fits, clamp the first candidate into view."""
        m = 8
        W, H = self.width(), self.height()
        cands = [
            (sx1 + m, sy0),            # right, top-aligned
            (sx0 - bw - m, sy0),       # left, top-aligned
            (sx1 + m, sy1 - bh),       # right, bottom-aligned
            (sx0 - bw - m, sy1 - bh),  # left, bottom-aligned
            (sx0, sy1 + m),            # below, left-aligned
            (sx1 - bw, sy1 + m),       # below, right-aligned
            (sx0, sy0 - bh - m),       # above, left-aligned
            (sx1 - bw, sy0 - bh - m),  # above, right-aligned
        ]
        for bx, by in cands:
            if bx >= 0 and by >= 0 and bx + bw <= W and by + bh <= H:
                return int(bx), int(by)
        bx, by = cands[0]
        return int(max(0, min(bx, W - bw))), int(max(0, min(by, H - bh)))

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
            # 30b velocity ratio — current velocity vs the trailing-30 MEAN velocity (same 30b basis as
            # 30b BER/SER above). Matches the abnormal-velocity chart flag; >= VEL_ABN_RATIO == flagged.
            def _bvel(bb):
                return ((bb.get("buy_vol", 0.0) + bb.get("sell_vol", 0.0)) /
                        max(1.0, bb.get("end_time", 0.0) - bb.get("start_time", 0.0)))
            v30 = (sum(_bvel(w) for w in win) / len(win)) if win else 0.0
            vabn = (_bvel(b) / v30) if v30 > 0 else 0.0
            # BULL/BEAR absorption (volume) — aggressive volume that FAILED to move price, vs the region's
            # trailing-norm. Directional: only the heavier aggressor that failed gets credit.
            bull_abs, bear_abs, _sabs = region_state.absorption_vol(buckets, idx, config.ABSORP_VOL_WINDOW)
            # EFFECTIVE AGGRESSION (the mirror): heavy directional volume that MOVED price its way = V*(1-s).
            eff_bull_b, eff_bear_b, _se = region_state.effective_aggression(buckets, idx, config.ABSORP_VOL_WINDOW)
            # color ONLY the two dominant 4-vectors (the ones that drove the move); the
            # other two render dim. A zero vector never lights up even if it lands "top 2".
            vmag = {"opL": opL, "opS": opS, "clS": clS, "clL": clL}
            vclr = {"opL": g, "opS": r, "clS": bl, "clL": pu}
            top2 = set(sorted(vmag, key=lambda k: vmag[k], reverse=True)[:2])
            def vc(name): return vclr[name] if (name in top2 and vmag[name] > 0) else gray
            lines = [
                f"O {pf(o)}  H {pf(h)}  L {pf(l)}  {span('C '+pf(c), g if c >= o else r)}",
                f"Elapsed {dur:.1f}s   {span('POC '+pf(poc), gold)}",
                sep("FLOW"),
                f"Volume {K(cv)}",
                # colour ONLY the dominant side (sell>buy -> sell red, buy>sell -> buy green); the
                # lesser side renders dim.
                f"{span('Sell '+K(sv), r if sv > bv else gray)} | "
                f"{span('Buy '+K(bv), g if bv > sv else gray)}",
                f"Delta {span(sk(delta)+f' ({dpct:+.0f}%)', g if delta >= 0 else r)}",
                f"OI Δ {span(sk(oi_d), g if oi_d >= 0 else r)}",
                sep("POSITIONING"),
                f"{span('OpL '+K(opL), vc('opL'))} | {span('OpS '+K(opS), vc('opS'))}",
                f"{span('ClS '+K(clS), vc('clS'))} | {span('ClL '+K(clL), vc('clL'))}",
                sep("EFFORT"),
                span(f"Buyer E/R {ber:.1f} [{(bm - 1.0) * 100:+.0f}%]", g if ber > ser else gray),
                span(f"Seller E/R {ser:.1f} [{(sm - 1.0) * 100:+.0f}%]", r if ser > ber else gray),
                span(f"30b Buyer E/R {b30:.1f}", g if b30 > s30 else gray),
                span(f"30b Seller E/R {s30:.1f}", r if s30 > b30 else gray),
                sep("ABSORPTION · VOL"),
                span(f"Bull Absorp {K(bull_abs)}", g if bull_abs > 0 else gray),
                span(f"Bear Absorp {K(bear_abs)}", r if bear_abs > 0 else gray),
                sep("EFF-AGG · VOL"),
                span(f"Bull Eff {K(eff_bull_b)}", "#00ff80" if eff_bull_b > 0 else gray),
                span(f"Bear Eff {K(eff_bear_b)}", "#ff2d6b" if eff_bear_b > 0 else gray),
                sep("READ"),
                f"VEL {span(f'{vel:.2f}x', gold)}",
                f"30b VEL {span(f'{vabn:.1f}×', gold if vabn >= config.VEL_ABN_RATIO else gray)}",
            ]
            # A3b — STATE verdict + its calibration debug lines (top-3 states + winner factors).
            # Hidden by default; 'y' toggles (self.show_state).
            if self.show_state:
                state, conf = bucket_state.classify_bucket(buckets, idx, bm, sm)
                lines.append(f"STATE {bucket_state.render_state_line(state, conf)}")
                lines += bucket_state.render_debug_lines(buckets, idx, bm, sm)
            return lines
        if mode == "vpin":
            window = buckets[max(0, idx - 49): idx + 1]
            ti = sum(abs(x.get("buy_vol", 0.0) - x.get("sell_vol", 0.0)) for x in window)
            tv = sum(x.get("curr_vol", 0.0) for x in window)
            v = ti / tv if tv > 0 else 0.0
            # adaptive tiers — SAME percentile mechanism + same rolling-50 series as Mode 6, so
            # the hovered bucket's label agrees with the bar/heatmap colour at that bucket.
            warn_cut, toxic_cut = vpin_adaptive.vpin_cutpoints(
                vpin_adaptive.rolling_vpin(buckets)[-config.VPIN_ADAPT_WINDOW:])
            cls, col = {
                vpin_adaptive.TOXIC: ("HFT Liquidity Trap", r),
                vpin_adaptive.WARN: ("Institutional Accumulation", gold),
                vpin_adaptive.NORMAL: ("Normal Balancing", gray),
            }[vpin_adaptive.vpin_tier(v, warn_cut, toxic_cut)]
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
        self._audio_announce(snap)
        self._refresh_scale_labels(snap)

        # Every mode is bucket-native now (time chart removed, Phase B): draw the scanner, refresh
        # Mode-10 DOM (ungated, bucket_canvas-only — depth pulses independently of the sig-gated
        # _draw_scanner), re-dock the axis badges, breathe the hovered bucket.
        self._draw_scanner()
        if self.scanner_mode == "bucket_canvas":
            self._update_m10_dom(snap)
        elif self.scanner_mode == "depth_heatmap" and self.cob.isVisible():
            # Aggregate the ladder to the CURRENT view zoom (~one bar per 2px of the panel), NOT the loaded
            # grid's ybins — so it coarsens as you zoom out / refines toward 1 tick as you zoom in, EVERY
            # frame (the old loaded-band/ybins only changed on a re-request, so it never tracked a zoom).
            (_, _), (vy0, vy1) = self.vb.viewRange()
            bars = max(20, int(self.cob.height()) // 2)
            self.cob.bars.bin_h = max(config.DOM_BIN_STEP, (vy1 - vy0) / bars)
            self.cob.update_depth(snap.get("depth") or {})   # DOM ladder = live book snapshot, price-aligned
            self.cob.autoscale_x(vy0, vy1)                    # bar length scales to the IN-VIEW max wall, so a far
            self._sync_cob()                                  # wall can't flatten the zoomed-in ladder; Y -> band
        self._redock_trackers()
        self._refresh_parked_hover()
        self._refresh_selection_stats()   # live Magic-Selection aggregate

    _TF_SPOKEN = {"1m": "1 minute", "5m": "5 minute", "15m": "15 minute",
                  "1h": "1 hour", "4h": "4 hour"}

    def _audio_announce(self, snap) -> None:
        """Speak NEW icebergs/OBs aloud (gated by the armed Audio Feed). Seeds silently on
        first data / tf-change so the history backlog is never read out — only live events."""
        obs = snap.get("order_blocks", []) if snap else []
        ice = snap.get("absorptions", []) if snap else []
        if not self._audio_seeded:
            if obs or ice:                # first real data -> seed silently
                self._announced_obs = {o.get("ob_id") for o in obs}
                self._announced_icebergs = {m.get("id") for m in ice}
                self._audio_seeded = True
            return
        scale = self._TF_SPOKEN.get(self._tf, self._tf)
        for o in obs:
            oid = o.get("ob_id")
            if oid and oid not in self._announced_obs:
                self._announced_obs.add(oid)
                side = "Long" if o.get("type") == "bullish" else "Short"
                self.alerts.audio.speak(f"{scale} {side} Order Block")
        for m in ice:
            mid = m.get("id")
            if mid and mid not in self._announced_icebergs:
                self._announced_icebergs.add(mid)
                side = "Buy" if m.get("side") == "BUY" else "Sell"
                self.alerts.audio.speak(f"{scale} {side} Iceberg")

    def _refresh_scale_labels(self, snap) -> None:
        """Push live per-tf bucket ~volumes into the Bucket Scale selector + window title. All
        five derive from the single anchor the terminal holds (the current tf's target_vol, since
        target_vol[tf] = anchor * tf_seconds/60), so one number sizes the whole honest ladder.
        Flicker-free: the menu + title re-render only when a rounded label changes."""
        tv = snap.get("target_vol") if snap else None
        cur_sec = config.TF_SECONDS.get(self._tf, 60)
        if tv and tv > 0 and cur_sec > 0:
            per_min = tv / (cur_sec / 60.0)           # the tf-invariant 1-minute anchor
            vols = {tf: per_min * (config.TF_SECONDS[tf] / 60.0) for tf in config.TIMEFRAMES}
            self.menu.update_scale_volumes(vols)
            label = scale_label(self._tf, vols.get(self._tf, 0.0))
        else:
            label = scale_label(self._tf, 0.0)        # "N× (~--)" until the first data arrives
        # Title carries the Bucket Scale + the live Scan Start (Zero Point) anchor date/time;
        # rebuilt only when the combined string changes (flicker-free). Reading the edit each tick
        # makes the title scrub live as Ctrl+wheel nudges the anchor.
        scan_dt = self.menu.scan_time_edit.dateTime().toString("yyyy-MM-dd HH:mm")
        title = f"Order Flow Terminal — {config.SYMBOL} {label} · Scan {scan_dt}"
        if title != self._title_scale:
            self._title_scale = title
            self.setWindowTitle(title)

    # ------------------------------------------------------------------
    # Phase 1: bucket pipeline + Zero-Point anchor
    # ------------------------------------------------------------------
    def _on_scan_time_changed(self) -> None:
        """User moved the Zero Point: flush geometry and redraw from the new anchor."""
        self.clear_scanner_canvas()
        self._scanner_bucket_sig = None       # force a fresh bucket rebuild
        self._scanner_needs_autofit = True    # re-fit once to the new window
        if self.scanner_mode == "depth_heatmap":
            self._hm_enter()                  # re-request the heatmap from the new Scan Start Time
        self._on_timer()                      # immediate manual redraw

    def _vb_wheel(self, ev, axis=None):
        """Modifier wheel over the chart: Ctrl -> nudge the Scan Start anchor ±1 min (consumed, no
        zoom); Shift -> zoom the X axis only; Alt -> zoom the Y axis only; otherwise the native zoom.
        Wrapped so a fault can never break chart zoom."""
        try:
            mods = ev.modifiers()
            if mods & QtCore.Qt.ControlModifier:
                self._nudge_scan_start(1 if ev.delta() > 0 else -1)
                ev.accept()
                return
            if mods & QtCore.Qt.ShiftModifier:
                return self._orig_vb_wheel(ev, axis=0)   # X-axis-only zoom
            if mods & QtCore.Qt.AltModifier:
                return self._orig_vb_wheel(ev, axis=1)   # Y-axis-only zoom
        except Exception:
            pass
        return self._orig_vb_wheel(ev, axis)

    def _nudge_scan_start(self, minutes: int) -> None:
        """Shift the Scan Start (Zero Point) by N minutes. The menu's datetime edit is updated with
        its signal blocked (so the title scrubs live each tick without a synchronous redraw per
        notch), then one full redraw is debounced for once the wheel burst settles."""
        edit = self.menu.scan_time_edit
        edit.blockSignals(True)
        edit.setDateTime(edit.dateTime().addSecs(minutes * 60))
        edit.blockSignals(False)
        self._scan_nudge_timer.start()

    def clear_scanner_canvas(self) -> None:
        """Aggressive teardown of all scanner geometry + heavy-mode scene objects.

        Safe to call in any state. Steps: (1) sweep tracked items; (2) Mode 4
        secondary ViewBox teardown; (3) Mode 10 lower-pane + COB-column teardown.
        """
        self.price_tag.hide()   # A2: drop the cursor price tag on any mode switch (no orphan)
        self.time_tag.hide()    # heatmap crosshair time tag — drop on any mode switch
        self.stats.hide()       # A3a: drop the hover readout too (no orphan across modes)
        self.panel_tooltip.hide()  # exhaustion-lines hover label
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
                # NOTE: index-space drawings are NOT flushed here. clear_scanner_canvas also runs on a
                # scan-time change (while STAYING in Mode 10), so flushing here wiped every drawing on
                # each time change. The flush now lives in _set_scanner, gated to actually LEAVING Mode 10.
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
        if self.scanner_mode == "depth_heatmap":
            self._scan_depth_heatmap()   # time-driven, its own canvas — bypass the bucket pipeline entirely
            return
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

    # ------------------------------------------------------------------
    # Phase 2b — depth/liquidity heatmap (scanner-mode-gated, own canvas)
    # ------------------------------------------------------------------
    def _hm_ybins(self, ylo: float, yhi: float) -> int:
        """Y-resolution = ONE bin per tick, so each price level fills its full tick cell (not a hairline when
        you zoom in). Capped at HEATMAP_YBINS so a wide band doesn't explode the grid (bins coarsen to
        multi-tick only when zoomed far out, where individual ticks aren't visible anyway)."""
        n = int(round((yhi - ylo) / config.TICK_SIZE))
        return max(1, min(config.HEATMAP_YBINS, n))

    def _hm_enter(self) -> None:
        """Enter heatmap mode: chronological x-axis, show items, request the RECENT window (fast cold-open)."""
        self.drawer.cancel()                              # opening the heatmap auto-toggles OFF any armed draw tool
        self.axis_bottom.set_scanner_active(False)        # time labels, not bucket ordinals
        self.hm_cache = HeatmapCache(); self.hm_levels = None; self.hm_pending = "reset"; self.hm_last_view = None
        self._hm_sizes = None; self.hm_manual = False; self.hm_follow = True; self._hm_prev_w = None
        self.hm_contrast.set_values(config.HEATMAP_LO_PCT, config.HEATMAP_HI_PCT)
        self.hm_contrast.adjustSize()                     # size to its full content (floating child) before placing
        self.hm_contrast.move(10, 10); self.hm_contrast.show(); self.hm_contrast.raise_()   # extreme top-left, small pad
        snap = self._last_snap or self.worker.snapshot()
        mid = snap.get("latest_price") or 0.0
        if mid <= 0:
            return
        ylo = mid * (1 - config.HEATMAP_BAND_PCT / 100.0); yhi = mid * (1 + config.HEATMAP_BAND_PCT / 100.0)
        t1 = int(time.time() * 1000)
        # Window start = the hamburger's Scan Start Time (defaults to now-1h for scanner modes), capped at the
        # 6h depth retention (no older data is captured). So: 1h by default, follows a user-chosen Scan Start.
        retention_ms = int(config.DEPTH_RETENTION_HOURS * 3600 * 1000)
        t0 = max(int(self.menu.scan_start_unix() * 1000), t1 - retention_ms)
        self.hm_floor_ms = t0          # HARD left boundary: never request/back-fill data before the Scan Start
        px = max(200, min(config.HEATMAP_MAX_COLS, int(self.plot.width()) or 1000))
        self.hm_band = (ylo, yhi)
        self.worker.request_depth_window(t0, t1, px, ylo, yhi, self._hm_ybins(ylo, yhi))
        self.hm_tb_cache = TradeBubbleCache(); self.hm_pending_tb = "reset"   # Phase 3 bubbles, same window
        self.worker.request_trades_window(t0, t1, ylo, yhi)
        self.vb.setXRange(t0 / 1000.0, t1 / 1000.0, padding=0.0)
        self.vb.setYRange(ylo, yhi, padding=0.0)

    def _hm_exit(self) -> None:
        """Leave heatmap mode: unsubscribe the live push, hide + free everything (full teardown)."""
        self.worker.stop_depth_window()
        self.hm_img.setVisible(False); self.hm_img.clear()
        for it in (self.hm_bid_line, self.hm_ask_line, self.hm_bid_dash, self.hm_ask_dash,
                   self.hm_bid_axtag, self.hm_ask_axtag, self.hm_bubbles_buy, self.hm_bubbles_sell,
                   self.hm_bubbles_ice_buy, self.hm_bubbles_ice_sell, self.hm_bubble_tip, self.hm_vol_tip):
            it.setVisible(False)
        self.hm_tb_cache = TradeBubbleCache(); self.hm_pending_tb = None
        self.hm_contrast.hide()
        self.cob.set_palette(config.RGBA_COB_BID, config.RGBA_COB_ASK)   # restore default ladder colors for other modes
        self.cob.bars.bin_h = config.DOM_BIN_STEP
        self.hm_cache = HeatmapCache(); self.hm_levels = None; self.hm_band = None; self.hm_pending = None
        self._hm_sizes = None

    def _scan_depth_heatmap(self) -> None:
        """Per-frame heatmap update — ONLY dispatched while this mode is active. Drains the delivery buffer
        (the ~MB grid, never via snapshot()), updates the cache, renorms contrast, re-renders on change/view."""
        _ver, window, livecols = self.worker.depth_heatmap_state()
        changed = False
        if window is not None:
            grid = decode_grid(window.grid_b64, window.cols, window.ybins)
            reset_now = not (self.hm_pending == "prepend" and self.hm_cache.cols)
            if reset_now:
                self.hm_cache.reset(grid, window.t0, window.t1, window.bbo_bid, window.bbo_ask,
                                    window.ylo, window.yhi)
                self.hm_band = (window.ylo, window.yhi)
            else:
                self.hm_cache.prepend_older(grid, window.t0, window.t1, window.bbo_bid, window.bbo_ask)
            self.hm_pending = None; changed = True
            self._hm_resample()                            # rebuild the pctile->size map for the new data
            if not self.hm_manual:
                self.hm_levels = self._hm_levels_from_pct(config.HEATMAP_LO_PCT, config.HEATMAP_HI_PCT)
            self.hm_renorm_t = time.time()
        for cp in livecols:
            if self.hm_cache.append_live(decode_col(cp.col_b64, cp.ybins), cp.ts, cp.bid, cp.ask):
                changed = True
        # Phase 3 bubbles: drain the trades delivery buffer (consume-once, never on snapshot()) -> bubble cache
        if self.hm_bubbles_on:
            _tv, tw, tbatches = self.worker.trades_state()
            if tw is not None:
                tt = decode_trades(tw.ts_b64, tw.price_b64, tw.qty_b64, tw.side_b64)
                if self.hm_pending_tb == "prepend" and len(self.hm_tb_cache.ts):
                    self.hm_tb_cache.prepend_older(*tt)
                else:
                    self.hm_tb_cache.reset(*tt)
                self.hm_pending_tb = None; changed = True
            for tbp in tbatches:
                if self.hm_tb_cache.append_batch(*decode_trades(tbp.ts_b64, tbp.price_b64, tbp.qty_b64, tbp.side_b64)):
                    changed = True
        if not self.hm_cache.cols:
            return
        now = time.time()
        if self.hm_levels is None or (not self.hm_manual and now - self.hm_renorm_t > config.HEATMAP_RENORM_SECS):
            self._hm_resample()
            self.hm_levels = self._hm_levels_from_pct(config.HEATMAP_LO_PCT, config.HEATMAP_HI_PCT)
            self.hm_renorm_t = now; changed = True
        # SMOOTH SCROLL: advance the view to track 'now' EVERY frame (cheap GPU pan of the existing image —
        # no setImage). This is decoupled from the image rebuild below, which fires only on a data change.
        if self.hm_follow:
            (vx0, vx1), _ = self.vb.viewRange()
            w = vx1 - vx0
            now_s = time.time()
            lead = w * HM_FOLLOW_LEAD_FRAC               # blank gutter to the RIGHT of 'now' (live edge at ~85%)
            self.vb.setXRange(now_s - w + lead, now_s + lead, padding=0.0)
            self._hm_prev_w = w                          # so the next manual gesture diffs against the live width
        view = self.vb.viewRange()
        vkey = (tuple(view[0]), tuple(view[1]))
        view_moved = vkey != self.hm_last_view
        # Rebuild the image ONLY on a data change (new column / window) — or on a MANUAL pan (re-slice the
        # loaded range). While following, our own setXRange advance is NOT a manual move, so we don't rebuild
        # per frame; the pan above shows the existing image translating. (Profile: avoids 20Hz×6.6ms churn.)
        if changed or (view_moved and not self.hm_follow):
            self._hm_render()
        self.hm_last_view = vkey
        self._hm_update_bbo_markers()                      # cheap per-frame: pin current bid/ask + axis tags

    def _hm_update_bbo_markers(self) -> None:
        """CURRENT bid/ask, Bookmap-LLT style: a DASHED segment projecting FORWARD from the live edge (where
        the solid formed trace ends) to the right edge of the view — so panning past 'now' shows only these
        dashed lines, connecting to the formed trace — plus a Y-axis price tag at the current value. Cheap
        (setData/setPos only) so it runs every frame. Hidden when zoomed out past the band, or when viewing
        pure history (the live edge is off-screen to the right)."""
        items = (self.hm_bid_dash, self.hm_ask_dash, self.hm_bid_axtag, self.hm_ask_axtag)
        if not self.hm_cache.cols or self.hm_band is None:
            for it in items:
                it.setVisible(False)
            return
        (vx0, vx1), (vy0, vy1) = self.vb.viewRange()
        if (vy1 - vy0) > (self.hm_band[1] - self.hm_band[0]) * 1.5:   # zoomed out -> hide (matches the trace)
            for it in items:
                it.setVisible(False)
            return
        # Anchor the forward projection to the FINE BBO trace's end (where the solid line now stops), not the
        # coarse grid column — so the dash joins the line seamlessly at the live price instead of behind it.
        tail = self.hm_cache.bbo[-1] if self.hm_cache.bbo else None
        if tail is None:
            for it in items:
                it.setVisible(False)
            return
        live_x = tail[0] / 1000.0                                      # where the formed (solid) trace ends
        step_s = (self.hm_cache.step_ms or 0) / 1000.0
        show = vx1 >= live_x - step_s                                  # the live edge is at / left of the view's right
        x0 = max(live_x, vx0)                                          # project from the live edge (or the left edge)
        cb = tail[1]; ca = tail[2]
        for dash, tag, val in ((self.hm_bid_dash, self.hm_bid_axtag, cb),
                               (self.hm_ask_dash, self.hm_ask_axtag, ca)):
            if show and val and val > 0:
                dash.setData([x0, vx1], [val, val]); dash.setVisible(vx1 > x0)   # 0-length while glued to 'now'
                tag.setText(f"{val:.{config.PRICE_DECIMALS}f}"); tag.setPos(vx1, val); tag.setVisible(True)
            else:
                dash.setVisible(False); tag.setVisible(False)

    def _hm_render(self) -> None:
        """Re-slice the cache to the visible time-range and paint the ImageItem + BBO lines (log+LUT+levels)."""
        if not self.hm_cache.cols or self.hm_levels is None or self.hm_band is None:
            return
        (vx0, vx1), (vy0, vy1) = self.vb.viewRange()
        vis = self.hm_cache.visible(int(vx0 * 1000), int(vx1 * 1000))
        if vis is None:
            self.hm_img.setVisible(False); return
        grid, ts, bid, ask, tf, tl = vis                  # grid is SIGNED pre-log (±log10(size+1), by side)
        self.hm_img.setImage(grid, autoLevels=False)
        ylo, yhi = self.hm_band
        self.hm_img.setRect(QtCore.QRectF(tf / 1000.0, ylo, max(1e-6, (tl - tf) / 1000.0), yhi - ylo))
        # Symmetric levels [-M, M] center the empty (0) bins; the lower cutoff becomes the LUT's transparent
        # center band (fraction lo_frac), so the diverging green/purple LUT is rebuilt only when it changes.
        lo, hi = self.hm_levels
        M = math.log10(hi + 1.0)
        lo_frac = (math.log10(lo + 1.0) / M) if M > 1e-9 else 0.0
        key = (round(lo_frac, 3), self.hm_grey)
        if key != self._hm_lut_key:
            self._hm_lut = neon_diverging_lut(lo_frac, grey=self.hm_grey); self._hm_lut_key = key
        self.hm_img.setLookupTable(self._hm_lut)
        self.hm_img.setLevels([-M, M])
        self.hm_img.setVisible(True)
        show_bbo = (vy1 - vy0) <= (yhi - ylo) * 1.5       # auto-hide BBO when zoomed out past the band
        # BBO lines come from the FINE per-pulse trace (not the binned grid columns), so they follow the live
        # price at pulse cadence and stay glued to the bubbles on a fast move (the binned grid collapses
        # intra-bin moves to one point per step_ms, which made the lines visibly lag the per-trade bubbles).
        fine = self.hm_cache.visible_bbo(int(vx0 * 1000), int(vx1 * 1000))
        if fine is not None:
            bts, bbid, bask = fine
            bxs = bts / 1000.0
            fb = bbid > 0; fa = bask > 0                     # plot only in-book points (skip empty edges)
            self.hm_bid_line.setData(bxs[fb], bbid[fb])
            self.hm_ask_line.setData(bxs[fa], bask[fa])
        else:
            self.hm_bid_line.setData([], []); self.hm_ask_line.setData([], [])
        self.hm_bid_line.setVisible(show_bbo); self.hm_ask_line.setVisible(show_bbo)
        self._hm_render_bubbles(vx0, vx1)

    def _hm_render_bubbles(self, vx0: float, vx1: float) -> None:
        """Phase 3: aggregate the visible trades into the heatmap's cells and paint the bubbles — diameter
        ∝ √(total cell qty), scaled so the biggest visible cell ≈ MAX_PX (clamped). Cells sitting on an active
        absorption/iceberg level are recolored (blue buy / orange sell); the rest split green (net buy) /
        purple (net sell). pxMode keeps bubbles a fixed size through zoom."""
        scatters = (self.hm_bubbles_buy, self.hm_bubbles_sell,
                    self.hm_bubbles_ice_buy, self.hm_bubbles_ice_sell)
        if not self.hm_bubbles_on or self.hm_band is None or not len(self.hm_tb_cache.ts):
            for s in scatters:
                s.setVisible(False)
            return
        ylo, yhi = self.hm_band
        cols = max(50, min(config.HEATMAP_MAX_COLS, int(self.plot.width()) or 1000))
        cells = self.hm_tb_cache.visible_cells(int(vx0 * 1000), int(vx1 * 1000), ylo, yhi,
                                               cols, self._hm_ybins(ylo, yhi), self.hm_bubble_min)
        if cells is None:
            for s in scatters:
                s.setVisible(False)
            return
        x, y, total, net = cells
        mx = float(total.max()) or 1.0
        lo, hi = config.HEATMAP_BUBBLE_MIN_PX, config.HEATMAP_BUBBLE_MAX_PX
        size = lo + (hi - lo) * np.sqrt(np.clip(total / mx, 0.0, 1.0))   # diameter px, area ~ qty
        ice = self._hm_iceberg_side(y)                                    # 0=none, 1=BUY iceberg, 2=SELL iceberg
        ice_buy = ice == 1; ice_sell = ice == 2
        plain = ice == 0
        buy = plain & (net > 0); sell = plain & ~(net > 0)               # net==0 -> drawn as sell (rare)
        for scat, m in ((self.hm_bubbles_buy, buy), (self.hm_bubbles_sell, sell),
                        (self.hm_bubbles_ice_buy, ice_buy), (self.hm_bubbles_ice_sell, ice_sell)):
            scat.setData(x=x[m], y=y[m], size=size[m], data=total[m])
            scat.setVisible(True)

    def _hm_iceberg_side(self, y: np.ndarray) -> np.ndarray:
        """Per-cell iceberg classification from the live absorption marks: 0 = none, 1 = BUY iceberg (a bid
        wall held), 2 = SELL iceberg (an ask wall held). A cell is an iceberg if its price falls in a mark's
        [plo, phi] break-range (± a tick of slop). Marks are the SAME standing levels the bucket chart draws."""
        out = np.zeros(len(y), dtype=np.uint8)
        marks = (self._last_snap or {}).get("absorptions") or []
        if not marks or not len(y):
            return out
        tol = config.TICK_SIZE
        for m in marks:
            try:
                plo = float(m.get("plo", m.get("price", 0.0))); phi = float(m.get("phi", plo))
            except (TypeError, ValueError):
                continue
            if phi < plo:
                plo, phi = phi, plo
            hitmask = (y >= plo - tol) & (y <= phi + tol)
            if hitmask.any():
                out[hitmask] = 1 if m.get("side") == "BUY" else 2
        return out

    def _on_bubble_hover(self, plot, points) -> None:
        """Hover a bubble -> show its cell's total volume: BLACK text on a neon pill (green = buy, purple =
        sell). Both scatters emit sigHovered, so the OTHER one fires empty when you hover a bubble — only the
        owning scatter is allowed to clear the tip (else the purple tip is killed by the buy scatter's empty
        hover, which is why the purple value never showed)."""
        if not self.hm_bubbles_on or self.scanner_mode != "depth_heatmap":
            self.hm_bubble_tip.hide(); self._hm_tip_plot = None; return
        if not len(points):
            if plot is self._hm_tip_plot:                # only the owner clears it
                self.hm_bubble_tip.hide(); self._hm_tip_plot = None
            return
        vol = points[0].data()
        if vol is None:
            return
        pill = self._hm_bubble_pill.get(plot, (0, 255, 110))
        self.hm_bubble_tip.fill = pg.mkBrush(*pill, 235)
        self.hm_bubble_tip.setText(f"{vol/1000:.1f}K" if vol >= 1000 else f"{vol:.0f}")
        self.hm_bubble_tip.setPos(points[0].pos().x(), points[0].pos().y())
        self.hm_bubble_tip.show(); self._hm_tip_plot = plot

    def _hm_request_visible(self) -> None:
        """Debounced lazy-load: free re-slice when the view is inside the loaded range at a compatible
        resolution; contiguous OLDER prepend on a same-band scroll-back; else request + reset (zoom / band /
        jump). Worst case = a redundant fetch, never wrong data."""
        if self.scanner_mode != "depth_heatmap":
            return
        (vx0, vx1), (vy0, vy1) = self.vb.viewRange()
        t_lo, t_hi = int(vx0 * 1000), int(vx1 * 1000)      # NOTE: no auto snap-back to live — once you pan
        #                                                     (incl. past 'now') you stay free; re-select the
        #                                                     mode to go live again.
        t_lo = max(t_lo, self.hm_floor_ms)                 # never load/back-fill before the Scan Start floor
        # The DATA window NEVER extends past 'now'. The view MAY show blank space to the right (so you can pan
        # ahead and watch the live edge develop into it) — but requesting future columns makes the daemon carry
        # the last book forward as flat lines to the extreme right and pushes live development off-screen.
        now_ms = int(time.time() * 1000)
        data_hi = min(t_hi, now_ms)
        if data_hi - t_lo < 1000:                          # visible range is essentially all-future -> nothing to load
            return
        px = max(200, min(config.HEATMAP_MAX_COLS, int(self.plot.width()) or 1000))
        need_step = (t_hi - t_lo) / px                     # step matches the VISIBLE pixel density (incl. blank)
        bspan = (self.hm_band[1] - self.hm_band[0]) if self.hm_band else 1.0
        band_ok = (self.hm_band is not None and abs(vy0 - self.hm_band[0]) < bspan * 0.05
                   and abs(vy1 - self.hm_band[1]) < bspan * 0.05)
        c_lo, c_hi = self.hm_cache.span()
        step_ok = (self.hm_cache.step_ms is not None and abs(self.hm_cache.step_ms - need_step) < need_step * 0.25)
        if band_ok and step_ok and c_lo is not None and c_lo <= t_lo and data_hi <= c_hi:
            return                                        # all in-view DATA loaded (future is just blank) -> free re-slice
        if band_ok and step_ok and c_lo is not None and t_lo < c_lo and data_hi <= c_hi:
            ocols = max(50, int((c_lo - t_lo) / need_step))
            self.hm_pending = "prepend"                          # same band -> reuse the cache's bin count
            self.worker.request_depth_window(t_lo, int(c_lo), ocols, self.hm_band[0], self.hm_band[1],
                                             self.hm_cache.ybins or self._hm_ybins(self.hm_band[0], self.hm_band[1]))
            self.hm_pending_tb = "prepend"                       # Phase 3 bubbles: same older range
            self.worker.request_trades_window(t_lo, int(c_lo), self.hm_band[0], self.hm_band[1])
            return
        cols_req = max(50, int((data_hi - t_lo) / need_step))   # cols scaled to the DATA span at the visible step
        self.hm_pending = "reset"; self.hm_band = (vy0, vy1)
        self.worker.request_depth_window(t_lo, data_hi, cols_req, vy0, vy1, self._hm_ybins(vy0, vy1))
        self.hm_pending_tb = "reset"                             # Phase 3 bubbles: same window
        self.worker.request_trades_window(t_lo, data_hi, vy0, vy1)

    def _toggle_heatmap_grey(self) -> None:
        """'g' — swap the Bookmap LUT for greyscale (heatmap mode only); instant re-color, no re-request."""
        if self.scanner_mode != "depth_heatmap":
            return
        self.hm_grey = not self.hm_grey
        self._hm_render()

    def _toggle_heatmap_bubbles(self) -> None:
        """'b' — toggle the Phase 3 trade-bubbles overlay (heatmap mode only). OFF hides them; ON re-requests
        the trades for the current view so they reappear without leaving the mode."""
        if self.scanner_mode != "depth_heatmap":
            return
        self.hm_bubbles_on = not self.hm_bubbles_on
        if not self.hm_bubbles_on:
            for s in (self.hm_bubbles_buy, self.hm_bubbles_sell,
                      self.hm_bubbles_ice_buy, self.hm_bubbles_ice_sell):
                s.setVisible(False)
            self.hm_bubble_tip.hide()
        else:
            (vx0, vx1), (vy0, vy1) = self.vb.viewRange()
            t_lo = max(int(vx0 * 1000), self.hm_floor_ms)
            data_hi = min(int(vx1 * 1000), int(time.time() * 1000))
            if data_hi - t_lo >= 1000:
                self.hm_tb_cache = TradeBubbleCache(); self.hm_pending_tb = "reset"
                self.worker.request_trades_window(t_lo, data_hi, vy0, vy1)
        self._hm_render()

    def _hm_resample(self) -> None:
        """Rebuild the sorted nonzero-size array of the loaded grid (the percentile->size map). Done on a
        window reset + every renorm, NOT per slider-drag — so dragging the cutoffs is an O(1) index lookup."""
        if not self.hm_cache.cols:
            self._hm_sizes = None; return
        allv = np.concatenate([c[1] for c in self.hm_cache.cols])
        nz = allv[allv > 0]
        self._hm_sizes = np.sort(nz) if nz.size else None

    def _hm_levels_from_pct(self, lo_pct: float, hi_pct: float) -> tuple:
        """(lo_size, hi_size) raw cutoffs from the loaded grid's sorted sizes at the given percentiles."""
        s = self._hm_sizes
        if s is None or s.size == 0:
            return (1.0, 10.0)
        n = s.size
        lo = float(s[min(n - 1, max(0, int(lo_pct / 100.0 * (n - 1))))])
        hi = float(s[min(n - 1, max(0, int(hi_pct / 100.0 * (n - 1))))])
        if hi <= lo:
            hi = lo * 10.0 + 1.0
        return (lo, hi)

    def _hm_contrast_changed(self, lo_pct: float, hi_pct: float) -> None:
        """User dragged a cutoff slider: percentiles -> size cutoffs, pause auto-renorm, re-color instantly."""
        if self.scanner_mode != "depth_heatmap":
            return
        self.hm_manual = True
        self.hm_levels = self._hm_levels_from_pct(lo_pct, hi_pct)
        self._hm_render()

    def _hm_contrast_reset(self) -> None:
        """'Reset → auto': drop the manual override, snap the sliders to p20/p99, re-enable the 60s renorm."""
        self.hm_manual = False
        self.hm_contrast.set_values(config.HEATMAP_LO_PCT, config.HEATMAP_HI_PCT)
        self._hm_resample()
        self.hm_levels = self._hm_levels_from_pct(config.HEATMAP_LO_PCT, config.HEATMAP_HI_PCT)
        self.hm_renorm_t = time.time()
        self._hm_render()

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
        over the visible window. The Y fit uses the candles (lows/highs); re-fitting
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
        if self.scanner_mode == "depth_heatmap":
            (vx0, vx1), _ = self.vb.viewRange()
            w = max(1e-6, vx1 - vx0)
            prev_w = self._hm_prev_w
            self._hm_prev_w = w
            if prev_w is not None and abs(w - prev_w) < prev_w * 0.005:
                # WIDTH ~unchanged => the user grabbed and DRAGGED (a pan to move around) => detach NOW, so the
                # view stays exactly where they put it and never snaps back to the live edge mid-gesture.
                self.hm_follow = False
            else:
                # WIDTH changed => a ZOOM. Keep SMOOTH-following only if still glued near the live edge (the lead
                # gutter puts the right edge at now+lead, so dt ~= -lead; +0.5s tol). Zoom into history => detach.
                dt = time.time() - vx1
                self.hm_follow = (-(w * HM_FOLLOW_LEAD_FRAC + 0.5) <= dt < max(1.5, w * 0.08))
            self._hm_debounce.start()   # Phase 2b: debounced lazy-load (re-slice / prepend / re-request)
            return
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
        # Candle viewport re-cull: redraw ONLY the new visible range from the cached series
        # (O(visible), not O(N)) so a manual pan/zoom refreshes the on-screen candles instantly
        # instead of waiting for the next live tick to fire update_data. Fires on both pan
        # (drag) and zoom (wheel/axis-drag) — sigRangeChangedManually covers both.
        bc = self._scan_handles.get("bc_candles")
        if bc is not None:
            bc.set_view(nx0, nx1)

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
        """Mode 6 — true rolling N=50 VPIN, bars + risk line keyed to the ADAPTIVE percentile
        tiers (the dead fixed 0.85 line is gone; the line now sits at the live toxic cutpoint)."""
        vpin_arr = vpin_adaptive.rolling_vpin(buckets)
        warn_cut, toxic_cut = vpin_adaptive.vpin_cutpoints(vpin_arr[-config.VPIN_ADAPT_WINDOW:])
        _br = {t: pg.mkBrush(h) for t, h in _VPIN_TIER_HEX.items()}
        brushes = [_br[vpin_adaptive.vpin_tier(v, warn_cut, toxic_cut)] for v in vpin_arr]

        if "vpin" not in self._scan_handles:
            self._scan_handles["vpin"] = self._add_scanner_item(
                pg.BarGraphItem(x=x, height=vpin_arr, width=0.8, brushes=brushes, pen=None))
            self._scan_handles["vpin_line"] = self._add_scanner_item(
                pg.InfiniteLine(pos=0.0, angle=0,
                                pen=pg.mkPen("#ff073a", style=QtCore.Qt.DashLine, width=2)))
        else:
            self._scan_handles["vpin"].setOpts(x=x, height=vpin_arr, width=0.8,
                                               brushes=brushes, pen=None)
        self._set_vpin_line("vpin_line", toxic_cut)
        self._fit_scanner_y(len(x), clamp=(0.0, 1.05))
        v = vpin_arr[-1]
        tier = vpin_adaptive.vpin_tier(v, warn_cut, toxic_cut)
        col = {vpin_adaptive.TOXIC: "#ff073a", vpin_adaptive.WARN: "#f1c40f"}.get(tier, "#999999")
        self._scanner_tracker("t_vpin", v, col, f"VPIN {v:.2f}<br>({v * 100:.0f}%)",
                              x[-1], "mid")

    def _set_vpin_line(self, handle_key: str, toxic_cut) -> None:
        """Position the adaptive VPIN risk line at the live toxic cutpoint (or hide it during
        warm-up when there aren't enough samples for a percentile)."""
        line = self._scan_handles.get(handle_key)
        if line is None:
            return
        if toxic_cut is None:
            line.setVisible(False)
        else:
            line.setPos(toxic_cut)
            line.setVisible(True)

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
        # VPIN sub-pane COLLAPSED by default (divider dragged all the way down) — the price pane gets the full
        # height; drag the handle up to reveal the toxicity heatmap. Deferred so the splitter is laid out
        # first; allow full collapse and set BOTH linked splitters (setSizes doesn't emit splitterMoved).
        self.lower_plot.setMinimumHeight(0)
        QtCore.QTimer.singleShot(0, self._collapse_vpin_pane)

        # Horizontal lock is enforced deterministically every frame in
        # _scan_bucket_canvas (mirror main X -> lower X). We deliberately do NOT
        # use setXLink here: combined with the per-frame mirror it double-controls
        # the range and leaves a padding mismatch, and its propagation is unreliable
        # under offscreen/deferred-paint conditions. The explicit mirror is exact.

    def _collapse_vpin_pane(self) -> None:
        """Drag the Mode-10 VPIN sub-pane fully DOWN by default — the price pane takes the whole height;
        the user can drag the handle back up to reveal the toxicity heatmap. The ``[big, 0]`` ratio collapses
        the lower pane regardless of the splitter's current pixel height (it rescales the ratio to fit). Both
        linked vertical splitters (price/VPIN and COB/spacer) set together so their dividers stay aligned."""
        if self.splitter_v is None:
            return
        self.splitter_v.setSizes([10_000, 0])
        if self.cob_col is not None:
            self.cob_col.setSizes([10_000, 0])

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

    def _bucket_row(self, buckets: list, i: int, vels: list, fold_prev):
        """Compute ONE bucket's Mode-10 render row at index ``i`` (#3 compute cache).

        ``vels`` must be populated for 0..i (``vels[i]`` = this bucket's velocity);
        ``fold_prev`` = the kinetic-forecast EMA state ``(baseline, bull_stretch,
        bear_stretch)`` after index i-1, or ``None`` for i == 0. Returns
        ``(row, new_fold)``. A pure function of immutable per-bucket fields + the
        TRAILING windows (vel-20, VPIN-50) + the prior fold — so a closed bucket's row
        is final the instant it closes (the live edge is never in a closed window)."""
        b = buckets[i]
        # trailing-20 velocity ratio -> neon dominance brush
        win = vels[max(0, i - 19): i + 1]
        base_vel = (sum(win) / len(win)) if win else 1.0
        ratio = vels[i] / max(0.1, base_vel)
        brush = self._neon_v2_brush(b.get("opL", 0.0), b.get("opS", 0.0),
                                    b.get("clL", 0.0), b.get("clS", 0.0),
                                    b.get("curr_vol", 0.0), ratio)
        # baseline EMA — the smoothed POC center line (slow 5%/95% EMA of the bucket POC).
        # ``fold_prev`` is the prior bucket's baseline scalar (None for i == 0 = seed).
        poc = b.get("poc_price", 0.0)
        baseline = poc if fold_prev is None else (poc * 0.05 + fold_prev * 0.95)
        # trailing-50 VPIN VALUE only — the heatmap brush is assigned at RENDER time from the
        # adaptive percentile cutpoints (which shift as buckets arrive), so it must NOT be cached
        # here per closed bucket or it would go stale.
        ti = tv = 0.0
        for bb in buckets[max(0, i - 49): i + 1]:
            ti += abs(bb.get("buy_vol", 0.0) - bb.get("sell_vol", 0.0))
            tv += bb.get("curr_vol", 0.0)
        vpin = ti / tv if tv > 0 else 0.0
        # wick + body-border pen — colored by FLOW dominance (buy_vol vs sell_vol): green when buy
        # leads, red when sell leads, gray when even; >50% lead (1.5x) -> NEON (green 0,255,127 /
        # red 255,7,58) + a touch thicker (0.7 vs 0.3). DIVERGENCE override (absorbed flow, the
        # strongest tell -> width 1): buy-led but closed DOWN -> neon ORANGE; sell-led but closed
        # UP -> neon BLUE. Cosmetic so width is px, not data units.
        bv, sv = b.get("buy_vol", 0.0), b.get("sell_vol", 0.0)
        op, cl = b.get("open", 0.0), b.get("close", 0.0)
        if bv > sv:
            if cl < op:                                  # buy-led but closed DOWN -> absorbed buying
                _wc, _w = (255, 128, 0), 1.0             # neon ORANGE
            else:
                _neon = bv > 1.5 * sv
                _wc = (0, 255, 127) if _neon else config.RGB_GREEN_STD
                _w = 0.7 if _neon else 0.3
        elif sv > bv:
            if cl > op:                                  # sell-led but closed UP -> absorbed selling
                _wc, _w = (0, 153, 255), 1.0             # neon BLUE
            else:
                _neon = sv > 1.5 * bv
                _wc = (255, 7, 58) if _neon else config.RGB_RED_STD
                _w = 0.7 if _neon else 0.3
        else:
            _wc, _w = (136, 136, 136), 0.3
        # E/R EXHAUSTION border (descriptive): when a side's E/R exhaustion-% — the stats-box [+N%]
        # bracket = (mult-1)*100, i.e. the E/R z vs the trailing-30 window — is >= ER_BORDER_EXH_PCT,
        # override the border (takes precedence over the volume-flow colour). WIDTH: 3px if BOTH sides
        # elevated, else 2px. COLOUR: neon ORANGE (buy-led closed down) / BLUE (sell-led closed up) on a
        # DIVERGENT close (absorbed flow), otherwise neon GREEN (closed up) / RED (closed down) by the
        # CLOSE direction.
        _bm, _sm, _ = _exhaustion_mults(buckets, i)
        _erthr = 1.0 + config.ER_BORDER_EXH_PCT / 100.0
        _div_orange = bv > sv and cl < op    # buy-led but closed DOWN
        _div_blue = sv > bv and cl > op      # sell-led but closed UP
        if _bm >= _erthr or _sm >= _erthr:
            _w = 3.0 if (_bm >= _erthr and _sm >= _erthr) else 2.0
            if _div_orange:
                _wc = (255, 128, 0)
            elif _div_blue:
                _wc = (0, 153, 255)
            else:
                _wc = (0, 255, 127) if cl >= op else (255, 7, 58)   # green = closed up, red = closed down
        wick_pen = pg.mkPen(_wc, width=_w); wick_pen.setCosmetic(True)
        row = (b.get("open", 0.0), b.get("high", 0.0), b.get("low", 0.0),
               b.get("close", 0.0), poc, brush,
               baseline, vpin, wick_pen)
        return row, baseline

    # The parallel render arrays, in row-tuple order (see _bucket_row). vbrush is NOT here:
    # the VPIN heatmap brush is render-time (adaptive percentile), recomputed each frame.
    _M10_ARR_KEYS = ("opens", "highs", "lows", "closes", "pocs", "brushes",
                     "baseline", "vpin", "pens")

    def _compute_bucket_arrays(self, buckets: list, anchor_unix: float) -> dict:
        """Static closed-bucket compute cache (#3): return the 10 per-bucket render
        arrays, recomputing ONLY the live edge (``buckets[-1]``) + any newly-closed
        buckets — closed buckets are immutable so their rows are cached and reused.

        Correctness (provable, not merely careful): ``buckets[-1]`` is the live edge
        (always recomputed, NEVER cached). For a closed bucket ``i <= L-2`` the trailing
        windows span ``[i-W+1 .. i]`` whose MAX index ``i < L-1`` — so the live edge is
        never inside any closed bucket's window, and a closed row is FINAL the instant
        the bucket closes. The cache grows ONLY at the closed end; any front change
        (history load / Zero-Point move / tf switch -> ``filtered[0]`` or the anchor
        changes) forces a clean full rebuild via the ``front_id`` fingerprint, never a
        stale reuse."""
        L = len(buckets)
        n_closed = max(0, L - 1)               # 0..L-2 closed; L-1 is the live edge
        front_id = ((buckets[0].get("start_time", 0.0),
                     buckets[0].get("curr_vol", 0.0)) if buckets else None)
        cc = self._m10_cc
        reuse = (cc is not None and cc["front_id"] == front_id
                 and cc["anchor"] == anchor_unix and cc["n"] <= n_closed)
        if not reuse:                          # full rebuild (front/anchor change or first run)
            cc = {k: [] for k in self._M10_ARR_KEYS}
            cc.update(vels=[], fold=None, n=0, front_id=front_id, anchor=anchor_unix)

        # extend velocities (per-bucket, immutable) + finalize newly-closed rows ONCE each
        for i in range(cc["n"], n_closed):
            b = buckets[i]
            dur = max(1.0, b.get("end_time", 0.0) - b.get("start_time", 0.0))
            cc["vels"].append((b.get("buy_vol", 0.0) + b.get("sell_vol", 0.0)) / dur)
            row, cc["fold"] = self._bucket_row(buckets, i, cc["vels"], cc["fold"])
            for k, v in zip(self._M10_ARR_KEYS, row):
                cc[k].append(v)
        cc["n"] = n_closed
        self._m10_cc = cc                      # cache holds exactly the closed prefix

        # full arrays = cached closed prefix (O(N) pointer copy) + the FRESH live edge
        out = {k: list(cc[k]) for k in self._M10_ARR_KEYS}
        if L >= 1:
            b = buckets[L - 1]
            dur = max(1.0, b.get("end_time", 0.0) - b.get("start_time", 0.0))
            live_vel = (b.get("buy_vol", 0.0) + b.get("sell_vol", 0.0)) / dur
            row, _ = self._bucket_row(buckets, L - 1, cc["vels"] + [live_vel], cc["fold"])
            for k, v in zip(self._M10_ARR_KEYS, row):
                out[k].append(v)
        return out

    def _scan_bucket_canvas(self, buckets: list, x: list) -> None:
        """Mode 10 — neon-graded bucket candles + gray baseline (upper pane)
        synchronized with a rolling-50 VPIN toxicity heatmap (lower pane)."""
        self._ensure_canvas_panes()
        # #3 static closed-bucket compute cache: closed buckets are immutable, so their
        # OHLC/poc/brush + baseline EMA + rolling-50 VPIN rows are computed ONCE
        # (on close) and reused; only the live edge (buckets[-1]) is recomputed each frame.
        arr = self._compute_bucket_arrays(buckets, self.menu.scan_start_unix())
        opens, highs, lows, closes = arr["opens"], arr["highs"], arr["lows"], arr["closes"]
        pocs, brushes = arr["pocs"], arr["brushes"]
        baseline_arr = arr["baseline"]
        vpin_arr = arr["vpin"]
        # adaptive VPIN heatmap brushes (same percentile mechanism as Mode 6 / hover / selection)
        v_warn, v_toxic = vpin_adaptive.vpin_cutpoints(vpin_arr[-config.VPIN_ADAPT_WINDOW:])
        _vbr = {t: pg.mkBrush(h) for t, h in _VPIN_TIER_HEX.items()}
        vbrushes = [_vbr[vpin_adaptive.vpin_tier(v, v_warn, v_toxic)] for v in vpin_arr]
        wick_pens = arr["pens"]   # per-candle flow-colored wick/border pens

        # Abnormal-velocity flag — a bucket whose velocity (curr_vol/dur) is >= VEL_ABN_RATIO x its
        # trailing-VEL_ABN_WINDOW MEAN (the SAME 30b basis as the stats box's 30b BER/SER). Computed ONCE
        # here for BOTH cues on a flagged candle: a 2px wick/border (vs the 0.3-1.0 flow width, KEEPING
        # the flow colour) — ALWAYS ON — plus a diamond above it ('v' toggles; neon green=buyer /
        # red=seller dominated, GOLD on divergence). DESCRIPTIVE study marker, not a signal.
        _bvel = [(b.get("buy_vol", 0.0) + b.get("sell_vol", 0.0)) /
                 max(1.0, b.get("end_time", 0.0) - b.get("start_time", 0.0)) for b in buckets]
        vel_abn = []
        for i in range(len(buckets)):
            w = _bvel[max(0, i - config.VEL_ABN_WINDOW):i]
            base = (sum(w) / len(w)) if w else 0.0
            vel_abn.append((_bvel[i] / base) if base > 0 else 0.0)
        wick_pens = list(wick_pens)            # copy before swapping entries (never mutate the #3 cache)
        for i, r in enumerate(vel_abn):
            if r >= config.VEL_ABN_RATIO:
                # at-least-2px, never REDUCE (so the E/R both-sides white 3px border survives a coincident
                # velocity flag); keep whatever colour the candle already carries.
                tp = pg.mkPen(wick_pens[i].color(), width=max(wick_pens[i].widthF(), 2.0))
                tp.setCosmetic(True)
                wick_pens[i] = tp

        # Viewport (hoisted): drives the candle viewport cull (Edit below) AND the footprint
        # cull + bubble/number px_per_* (used further down). One viewRange() call serves both.
        (vx0, vx1), (vy0, vy1) = self.vb.viewRange()
        px_per_x = self.vb.width() / max(1e-9, vx1 - vx0)
        px_per_y = self.vb.height() / max(1e-9, vy1 - vy0)

        # --- upper pane: candles + the gray baseline (create-once / update-after) ---
        if "bc_candles" not in self._scan_handles:
            self._scan_handles["bc_candles"] = self._add_scanner_item(BucketCandleItem())
            self._scan_handles["bc_baseline"] = self._add_scanner_item(
                pg.PlotCurveItem(pen=pg.mkPen((180, 180, 180, 150), width=1.5,
                                              style=QtCore.Qt.DashLine)))
        # vx0/vx1: viewport cull — paint ONLY the visible candles (O(visible), not O(N)).
        self._scan_handles["bc_candles"].update_data(x, opens, highs, lows, closes, brushes, wick_pens, 0.8, vx0, vx1)
        self._scan_handles["bc_baseline"].setData(x, baseline_arr)

        # --- STAGE 0: true per-bucket POC marker (gold dot) — rides the whole DETAIL regime ---
        # poc_price is already finalized in every BucketSnapshot (and computed on the fly for the
        # live edge), so this draws what the engine ships, guarded to within [low, high] so a
        # cold poc=0 can't drop a dot at y=0. Visibility is the SHARED detail gate (detail_visible:
        # <= MAX_BUBBLE_BUCKETS visible) AND the m10_poc toggle — so the POC stays with ANY footprint
        # detail (numbers AND bubbles) and vanishes only when you zoom out past the bubbles (>200).
        # NO row-height check: rows only decide numbers-vs-bubbles, and the POC rides both. The
        # scatter is cheap, so we gate VISIBILITY rather than cull the dot set (culling without a
        # pan re-cull hook would drop leading-edge dots on a drag).
        poc_show = (self.menu.layer_state("m10_poc")
                    and detail_visible(vx1 - vx0))
        if "bc_poc" not in self._scan_handles:
            self._scan_handles["bc_poc"] = self._add_scanner_item(pg.ScatterPlotItem(
                size=7, symbol="o", pen=pg.mkPen("#141414", width=0.5),
                brush=pg.mkBrush("#f1c40f")))
            self._scan_handles["bc_poc"].setZValue(6)   # POC dots ride above the candles
        if poc_show:
            poc_x, poc_y = [], []
            for i in range(len(buckets)):
                pv = pocs[i]
                if pv > 0.0 and lows[i] <= pv <= highs[i]:
                    poc_x.append(x[i]); poc_y.append(pv)
            self._scan_handles["bc_poc"].setVisible(True)
            self._scan_handles["bc_poc"].setData(poc_x, poc_y)
        else:
            self._scan_handles["bc_poc"].setVisible(False)

        # --- STAGE 1: per-bucket footprint ladder from b["levels"] (wire-additive) ---
        # levels now ride on the BucketSnapshot (quant_engine._assemble), so the
        # footprint is a property of the BUCKET, drawn in its ordinal column. The POC is
        # marked by the separate gold
        # dot (bc_poc above), so the ladder draws only the volume distribution. px_per_*
        # drive the bubble/number switch + pixel-round bubble radii; recomputed each
        # bucket-change frame.
        # (vx0/vx1/px_per_* hoisted above — single viewRange() call shared with the candle cull.)
        # A4 draw-gate: footprint (bubbles/numbers) gated by m10_footprint. Toggle-off
        # teardown is in _set_scanner_overlay (setVisible + clear_text for the TextPools,
        # which are not in active_scanner_items). px_per_* are computed above regardless
        # (cheap, footprint-only) so nothing downstream can break when this is off.
        # per-bucket 30b BER/SER baseline (trailing-30 mean of buyer_er/seller_er — the SAME window as the
        # stats-box 30b BER/SER). Used by BOTH the footprint number highlight (gated) AND the imbalance
        # lines (always-on), so compute it once here regardless of the footprint toggle.
        levels_list = [b.get("levels", {}) for b in buckets]
        ber30s, ser30s = [], []
        for i in range(len(buckets)):
            w = buckets[max(0, i - EXH_WINDOW):i]
            if w:
                ber30s.append(sum(bb.get("buyer_er", 0.0) for bb in w) / len(w))
                ser30s.append(sum(bb.get("seller_er", 0.0) for bb in w) / len(w))
            else:
                ber30s.append(0.0); ser30s.append(0.0)

        # IMBALANCE LINES — ALWAYS drawn (independent of the footprint toggle): a horizontal neon line the
        # candle's width AT an imbalanced level's EXACT price (buy >= 30b BER -> neon BLUE; sell >= 30b SER
        # -> neon ORANGE; both at a level -> split). Price-anchored, so it scales with the candles/grid on
        # zoom and never drifts. Two PlotCurveItems (connect='pairs'), one per side. NOT a signal.
        if "bc_imb_sell" not in self._scan_handles:
            _ps = pg.mkPen((255, 128, 0), width=2.0); _ps.setCosmetic(True)
            _pb = pg.mkPen((0, 153, 255), width=2.0); _pb.setCosmetic(True)
            self._scan_handles["bc_imb_sell"] = self._add_scanner_item(pg.PlotCurveItem(pen=_ps))
            self._scan_handles["bc_imb_sell"].setZValue(6)
            self._scan_handles["bc_imb_buy"] = self._add_scanner_item(pg.PlotCurveItem(pen=_pb))
            self._scan_handles["bc_imb_buy"].setZValue(6)
        mult = config.FOOTPRINT_IMB_ER_MULT
        hw = 0.8 / 2.0
        sxs, sys_, bxs, bys = [], [], [], []
        for i, b in enumerate(buckets):
            xi = x[i]
            if xi < vx0 - 1.0 or xi > vx1 + 1.0:
                continue
            lv = b.get("levels") or {}
            if not lv:
                continue
            buy_thr = mult * ber30s[i] if ber30s[i] > 0 else None
            sell_thr = mult * ser30s[i] if ser30s[i] > 0 else None
            if buy_thr is None and sell_thr is None:
                continue
            for ps2, v in lv.items():
                buy_imb = buy_thr is not None and v.get("b", 0.0) >= buy_thr
                sell_imb = sell_thr is not None and v.get("s", 0.0) >= sell_thr
                if not (buy_imb or sell_imb):
                    continue
                yy = float(ps2)            # EXACTLY at the level's price -> zoom-stable (no pixel offset)
                if buy_imb and sell_imb:   # split: sell (orange) left half, buy (blue) right half
                    sxs += [xi - hw, xi]; sys_ += [yy, yy]
                    bxs += [xi, xi + hw]; bys += [yy, yy]
                elif sell_imb:
                    sxs += [xi - hw, xi + hw]; sys_ += [yy, yy]
                else:
                    bxs += [xi - hw, xi + hw]; bys += [yy, yy]
        self._scan_handles["bc_imb_sell"].setData(sxs, sys_, connect="pairs")
        self._scan_handles["bc_imb_buy"].setData(bxs, bys, connect="pairs")
        self._scan_handles["bc_imb_sell"].setVisible(True)
        self._scan_handles["bc_imb_buy"].setVisible(True)

        if self.menu.layer_state("m10_footprint"):
            if "bc_fp" not in self._scan_handles:
                self.bc_fp.setZValue(5)            # ladder above candles (z0), below the POC dot (z6)
                self._add_scanner_item(self.bc_fp)
                self._scan_handles["bc_fp"] = self.bc_fp
            self.bc_fp.setVisible(True)
            self.bc_fp.update_data(x, levels_list, ber30s, ser30s,
                                   vx0, vx1, 0.8, px_per_x, px_per_y)   # vx0/vx1: viewport cull

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
                self._last_snap.get("order_blocks", []), float(x[-1]), _ts_to_idx, (vx0, vx1), px_per_y)

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

        # Abnormal-velocity flag (descriptive, NON-directional study marker — not a signal). A bucket is
        # flagged when its velocity (curr_vol/duration) is >= VEL_ABN_RATIO x the MEAN velocity over the
        # trailing VEL_ABN_WINDOW buckets — the SAME trailing-30-mean basis as the stats box's 30b
        # BER/SER. White diamond above the bar; alpha+size scale with the ratio, capped at VEL_ABN_CAP so
        # the fat tail (up to ~76x) just maxes out. Shift+V toggles. z8 = above the liq scatter.
        if "bc_vel_abn" not in self._scan_handles:
            self._scan_handles["bc_vel_abn"] = self._add_scanner_item(pg.ScatterPlotItem())
            self._scan_handles["bc_vel_abn"].setZValue(8)
        vabn_item = self._scan_handles["bc_vel_abn"]
        if self.show_vel_abn:
            Rv, capv = config.VEL_ABN_RATIO, config.VEL_ABN_CAP
            vspots = []
            for i, ratio in enumerate(vel_abn):    # ratios computed once with the candle pens, above
                if ratio >= Rv:
                    t = min(1.0, (ratio - Rv) / max(1e-9, capv - Rv))   # cutoff->faint, >=cap->full
                    a = int(255 * (0.40 + 0.60 * t))
                    b = buckets[i]
                    bv, sv = b.get("buy_vol", 0.0), b.get("sell_vol", 0.0)
                    op, cl = b.get("open", 0.0), b.get("close", 0.0)
                    # Diamond colour: neon GREEN = buyer-dominated, neon RED = seller-dominated; GOLD
                    # overrides on DIVERGENCE (absorbed flow: buy-led closed DOWN, or sell-led closed UP).
                    if (bv > sv and cl < op) or (sv > bv and cl > op):
                        col = (241, 196, 15)            # gold — divergence
                    elif bv > sv:
                        col = (0, 255, 127)             # neon green — buyer dominated
                    elif sv > bv:
                        col = (255, 7, 58)              # neon red — seller dominated
                    else:
                        col = (255, 255, 255)           # white — even (rare)
                    vspots.append({"pos": (x[i], highs[i]), "symbol": "d", "pen": None,
                                   "brush": pg.mkBrush(col[0], col[1], col[2], a), "size": 9.0 + 9.0 * t})
            vabn_item.setData(vspots)
            vabn_item.setVisible(True)
        else:
            vabn_item.setVisible(False)

        # --- lower pane: VPIN heatmap + ADAPTIVE risk line (live toxic cutpoint) on lower_plot ---
        if "bc_vpin" not in self._scan_handles:
            self._scan_handles["bc_vpin"] = pg.BarGraphItem(
                x=x, height=vpin_arr, width=0.8, brushes=vbrushes, pen=None)
            self.lower_plot.addItem(self._scan_handles["bc_vpin"])
            line = pg.InfiniteLine(pos=0.0, angle=0,
                                   pen=pg.mkPen("#ff073a", style=QtCore.Qt.DashLine, width=2))
            self.lower_plot.addItem(line)
            self._scan_handles["bc_vpin_line"] = line
        else:
            self._scan_handles["bc_vpin"].setOpts(x=x, height=vpin_arr, width=0.8,
                                                  brushes=vbrushes, pen=None)
        self._set_vpin_line("bc_vpin_line", v_toxic)

        # --- view-follow (replaces the one-shot fit). A mode/tf/Zero-Point re-arm
        # (_scanner_needs_autofit) re-locks BOTH axes + drops us on the live edge, consuming
        # that flag exactly as _fit_scanner_y used to. The Y fit uses candles only
        # (lows/highs) — re-fit every draw so an extreme in-window bucket can't squish them. The
        # roll runs whenever either axis is locked (each axis gated inside). After the draw
        # we snapshot the displayed range so the per-axis unlock can diff against it. ---
        if self._scanner_needs_autofit:
            self._follow_x = self._follow_y = True
            self._scanner_needs_autofit = False
        if self._follow_x or self._follow_y:
            self._roll_to_live_edge(len(x), lows, highs)
        self.lower_plot.getViewBox().setYRange(0.0, 1.05, padding=0)
        self._follow_prev_range = self.vb.viewRange()

        # §5 right-edge spot price + active-bucket fill badge, plus the baseline readout
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
        # refresh sits just left of the bell
        self.refresh_btn.move(
            self.width() - self.menu_btn.width() - self.bell_btn.width() - self.refresh_btn.width() - 20, 8)
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
    global _TUNNEL
    _TUNNEL = tunnel        # expose to windows' refresh button (relaunches a dead tunnel)
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
