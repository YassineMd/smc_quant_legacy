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
import json
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
from . import bar_quantiles
from . import archive          # local cold-archive reader — extends the scanner frame past the daemon's cap
from . import structure        # market-structure swing labels (HH/HL/LH/LL)
from .chart_widgets import (
    WhiskerBarItem, FootprintCandleItem, DeltaCandleItem, ForceCandleItem, DeltaForceCandleItem,
    AbsorptionLayer, AbsorptionZoneLayer, BucketCandleItem, ExhaustionStripLayer, LocalTimeAxis,
    OrderBlockLayer, PanelSeparatorLayer, PriceAxis, _RGB_ABS_BEAR, _RGB_ABS_BULL, _RGB_EFF_BEAR,
    _RGB_EFF_BULL, _RGB_ER_BEAR, _RGB_ER_BULL, _RGB_EXH_BEAR, _RGB_EXH_BULL,
)
from .cob_panel import CobPanel
from .footprint_panel import FootprintPanel
from .drawing_tools import DrawingController, DrawingToolbar
from .footprint_layers import BucketFootprintItem, DepthWallLayer, detail_visible
from .hamburger import FloatingOverlayMenu, HamburgerButton, scale_label
from .pipe_client import PipeClientWorker
from .session_perf import SessionProfiler, rss_mb
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
# PIVOT D-badge fill thresholds on the aligned LIVE eff-agg (panel-2) spread AT D — where entering at D beats
# waiting for E (study/pivot_backtest, Jul2-5 tape, IN-SAMPLE; retune as the forward tape grows). Top-1/3 of
# the live spread (>63) = D beats E; >80 = the biggest win-rate edge. Below these, E is the better entry.
PIVOT_P2D_HIGH = 63.0
PIVOT_P2D_VHIGH = 80.0
# E greys out when panel-2 FLIPPED (aligned live spread @E <= 0 or it breached -50 over [D,E]); the indicator
# then hunts E2 = the first later bar (within 1h) where the live spread RE-CONFIRMS to >= this. Study: the
# p2@E<0 (flipped) setups go 29% -> 69% TP if entered on the >=+30 recovery (Jul2-5 tape, in-sample).
PIVOT_E2_MIN = 30.0
# --- PIVOT-ZZTRAIL-v2 overlay FADE: dim the pivot setups the v2 strategy would SKIP (zone TAKE filter on the
# non-merged 4h wick + hollow AVOID list). OVERLAY-ONLY — does not change any trade/entry logic. Take if the D
# zone OR the entry (E2/E-held) zone qualifies. tier names here use the STUDY spelling (cyan/orange, red/green).
PIVOT_V2_AVOID = {("buy", "inzone-sell", "body"), ("sell", "inzone-sell", "inzone-sell"),
                  ("buy", "beyond-down", "beyond-down"), ("sell", "beyond-up", "beyond-up")}
PIVOT_FADE_RGB = (110, 115, 125)   # dim grey for faded (skipped) setup glyphs


def _pivot_zone5(px, low, vlo, vhi, high):
    if px < low:
        return "beyond-down"
    if px <= vlo:
        return "inzone-buy"
    if px < vhi:
        return "body"
    if px <= high:
        return "inzone-sell"
    return "beyond-up"


def _pivot_v2_take_rule(zone, buy, tier):
    own_in = (zone == "inzone-buy") if buy else (zone == "inzone-sell")
    rev_in = (zone == "inzone-sell") if buy else (zone == "inzone-buy")
    own_bey = (zone == "beyond-down") if buy else (zone == "beyond-up")
    if tier == "hollow":
        return rev_in or (zone == "body") or own_bey
    if tier == "cyan/orange":
        return own_in
    return own_in or own_bey


def _pivot_v3_take(buy, tier, zone):
    """PIVOT V3 Step-3 entry: take the D only if tier is cyan/orange AND its 4H-zone position CONFIRMS its
    direction — Buy in the buy area or above the sell area; Sell in the sell area or below the buy area.
    (`tier` here is the terminal's fill name: 'cyan' == the >80 cyan/orange tier.)"""
    if tier != "cyan":
        return False
    if buy:
        return zone in ("inzone-buy", "beyond-up")            # buy area / above sell area (breakout up)
    return zone in ("inzone-sell", "beyond-down")             # sell area / below buy area (breakdown down)


def _pivot_v3_e_take(buy, tier, d_zone, e_zone):
    """PIVOT V3 Step-4 E-entry SELECTION: take the New-E only on these (side, D-zone, E-zone) combos. First 4 =
    any tier; last 2 = cyan/orange only. Zone names per _pivot_zone5 (inzone-buy=buy area, inzone-sell=sell area,
    beyond-down=below buy, beyond-up=above sell)."""
    c = (("buy" if buy else "sell"), d_zone, e_zone)
    if c in (("buy", "inzone-buy", "body"), ("sell", "inzone-sell", "body"),
             ("buy", "beyond-down", "inzone-buy"), ("sell", "beyond-up", "inzone-sell")):
        return True
    return tier == "cyan" and c in (("buy", "body", "inzone-sell"), ("sell", "body", "inzone-buy"))


LIQ_MAX_LABELS = 40       # Ctrl+L labels: hard cap on simultaneously-drawn labels (Tier-A first, then even spread)
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


class StackedBarLayer(pg.GraphicsObject):
    """Per-bucket dominance bars (LARGE/SMALL panels). Each bucket = one upward bar from the panel floor: the
    LOSER side (smaller volume) at the bottom in a MUTED tint, the WINNER (larger) stacked on top in FULL
    colour — so bar height = total large activity and the TOP colour shows who dominated (and by how much).
    The caller passes per-bucket geometry already mapped into panel y-coords."""

    def __init__(self):
        super().__init__()
        self.picture = QtGui.QPicture()
        self._rect = QtCore.QRectF()

    def paint(self, p, *args):
        p.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        return self._rect

    def update_bars(self, x0s, x1s, floor, lo_h, hi_h, lo_rgba, hi_rgba, vx0, vx1, yb, yt):
        """x0s/x1s: per-bar left/right x edges (a MERGED run spans several buckets); floor: panel-bottom y
        (bars rise from here); lo_h/hi_h: muted (loser) and full (winner) segment heights in panel-y units;
        lo_rgba/hi_rgba: per-bar (r,g,b,a) for each segment; [vx0,vx1]x[yb,yt]: panel bounds."""
        self.picture = QtGui.QPicture()
        if not len(x0s):
            self._rect = QtCore.QRectF(); self.prepareGeometryChange(); self.update(); return
        p = QtGui.QPainter(self.picture)
        p.setRenderHint(QtGui.QPainter.Antialiasing, False)   # crisp bar edges
        p.setPen(QtCore.Qt.NoPen)
        for i in range(len(x0s)):
            w = x1s[i] - x0s[i]
            if lo_h[i] > 0:                                    # loser (gray) at the bottom
                p.setBrush(QtGui.QColor(*lo_rgba[i]))
                p.drawRect(QtCore.QRectF(x0s[i], floor, w, lo_h[i]))
            if hi_h[i] > 0:                                    # winner (full colour) stacked on top
                p.setBrush(QtGui.QColor(*hi_rgba[i]))
                p.drawRect(QtCore.QRectF(x0s[i], floor + lo_h[i], w, hi_h[i]))
        p.end()
        self._rect = QtCore.QRectF(vx0, yb, max(1.0, vx1 - vx0), max(1e-6, yt - yb))
        self.prepareGeometryChange(); self.update()


class _IdxJumpEdit(QtWidgets.QLineEdit):
    """Ctrl+F jump-to-Idx box (Mode-10 index space). Enter -> jump (returnPressed); Escape -> dismiss."""

    def keyPressEvent(self, ev):  # noqa: N802 (Qt casing)
        if ev.key() == QtCore.Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(ev)


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
        self.fp_panel = FootprintPanel()   # live forming-candle footprint side pane ('Live Footprint' toggle, Mode 10)
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.splitter.setHandleWidth(6)
        self.splitter.addWidget(self.plot)
        self.splitter.addWidget(self.cob)
        self.splitter.addWidget(self.fp_panel)   # rightmost; rides the Mode-10 reparenting, gated by _fp_want + mode
        self.splitter.setStretchFactor(0, 1)
        self.cob.hide()
        self.fp_panel.hide()
        # footprint pane hover -> its own crosshair + mirror the price into the chart (shared like the VPIN pane)
        self._fp_proxy = pg.SignalProxy(self.fp_panel.scene().sigMouseMoved,
                                        rateLimit=60, slot=self._on_fp_mouse_move)
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
        self._replay_on = False            # REPLAY MODE: causal historical playback from the Start Date (default OFF)
        self._replay_edge_t: Optional[float] = None   # replay cursor = live-edge bucket end_time; Right arrow advances it
        self._replay_start_t: Optional[float] = None  # FIXED replay start (Start Date); the frame's LEFT edge = start-24h,
        #                                               so stepping GROWS the window to the right (left history stays)
        self._replay_saved_edge_t: Optional[float] = None   # last replay position, persisted -> restored on next toggle-on
        self._replay_dbg = False           # replay diagnostics -> console + data/replay_debug.log (flip True to debug)
        self._replay_save_timer = QtCore.QTimer(self)       # debounce persisting the cursor (holding the arrow -> 1 write)
        self._replay_save_timer.setSingleShot(True); self._replay_save_timer.setInterval(800)
        self._replay_save_timer.timeout.connect(self._save_ui_state)
        self._replay_autoplay_timer = QtCore.QTimer(self)   # Ctrl+Right auto-play: reveal one candle per tick (Left/Right stops it)
        self._replay_autoplay_timer.setInterval(config.REPLAY_AUTOPLAY_MS)
        self._replay_autoplay_timer.timeout.connect(self._replay_autoplay_tick)
        # ABSOLUTE bucket index: add this to a filtered-local idx to get the bucket's permanent history.db id
        # (stable all-time index; first bucket ever saved = 1). 0 = legacy local idx (daemon hasn't shipped
        # total_closed yet). Recomputed in _build_scanner_buckets.
        self._global_idx_offset: int = 0
        # ARCHIVE EXTEND: seamlessly prepend older buckets from the local cold-archive mirror when the Zero
        # Point reaches before the daemon's live window. _arch_win caches the walked run per (tf, anchor, edge).
        self._archive_extend: bool = True
        self._arch_win_key = None
        self._arch_win: list = []
        # GCS FETCH-IF-MISSING: when a date's history isn't in the local mirror, rsync it from the bucket on demand
        # (background QProcess) and re-render when it lands. Throttled so a genuinely-unavailable range can't tight-loop.
        self._arch_pull_proc = None
        self._arch_pull_active: bool = False
        self._arch_pull_last: float = 0.0     # wall time of the last GCS fetch (time-gated, one rsync grabs all of GCS)
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
        self._fp_want = False          # user's Live-Footprint-pane intent (right-docked, Mode 10 only)
        self._fp_sig = None            # last forming-bucket signature -> skip redundant footprint re-renders
        self._syncing_split = False    # reentrancy guard for the linked splitter-divider sync
        # Mode 10 order-block layer (index-space). Persistent object; added to the
        # plot lazily in _scan_bucket_canvas and swept on teardown. Tiers forced on.
        self.bc_obs = OrderBlockLayer(self.plot, show_tiers=True)
        self._ob_unmitig_only = False           # 'o' cycle stage 2: hide mitigated OBs, keep only unmitigated (live)
        # REPLAY: OB + absorption re-detected CAUSALLY from the clipped frame (mirrors the daemon). The re-detect is
        # ~300ms (calc_absorption dominates), so it's DEFERRED off the step (debounced) — the step repaints with the
        # last marks, the fresh ones land ~130ms after you stop; QuantBuckets are cached across steps (only the new
        # bar is rebuilt), so a step's reconstruction is ~free.
        self._replay_oba_key = None
        self._replay_oba_cache: tuple = ([], [])
        self._replay_oba_pending = None
        self._replay_oba_timer = QtCore.QTimer(self); self._replay_oba_timer.setSingleShot(True)
        self._replay_oba_timer.setInterval(130); self._replay_oba_timer.timeout.connect(self._replay_oba_recompute)
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
        self.zone_slider.side_changed.connect(self._on_zone_side_filter)   # Bull/Bear zone filter
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
        self.eff_slider.side_changed.connect(self._on_zone_side_filter)    # Bull/Bear zone filter
        self.eff_slider.hide()
        self._eff_sel_id = None        # identity of the live selection; on change -> re-seed adaptive default
        # Volume-Profile-over-selection toggle: a standalone checkbox card that rides in the SAME stack as the two
        # zone/force sliders under the 'h' stats box (grouped WITH them — the sliders are NOT reparented or touched).
        # Independent of the stats box: once ticked, the VP stays on even when the box is hidden with 'h'.
        self.show_sel_vp = False
        self.sel_vp_chk = QtWidgets.QCheckBox("  Volume Profile", self)
        self.sel_vp_chk.setFixedWidth(210)
        self.sel_vp_chk.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.sel_vp_chk.setStyleSheet(
            "QCheckBox{background:rgba(17,19,26,235); border:1px solid #2a2e39; border-radius:5px;"
            " color:#d2d7df; font-family:Consolas; font-size:11px; padding:6px 8px; spacing:7px;}")
        self.sel_vp_chk.toggled.connect(self._on_sel_vp_toggled)
        self.sel_vp_chk.hide()
        # Volume-profile-over-selection chart items: force-coloured horizontal histogram + POC/VAH/VAL/median lines.
        self.bc_sel_vp = pg.BarGraphItem(x0=[0.0], width=[0.0], y=[0.0], height=[0.0], pen=None)
        self.bc_sel_vp.setZValue(2); self.plot.addItem(self.bc_sel_vp, ignoreBounds=True); self.bc_sel_vp.setVisible(False)
        self.bc_sel_vp_lines = []
        # VAH red / VAL green / POC yellow / median white / LVN electric-purple (dashed)
        for _vpc, _dash in (((255, 45, 70), False), ((40, 230, 90), False), ((255, 215, 0), False),
                            ((235, 235, 245), False), ((178, 70, 255), True)):
            _pen = pg.mkPen(_vpc, width=1.3)
            if _dash:
                _pen.setDashPattern([3.0, 6.0])
            _ln = pg.PlotDataItem(pen=_pen); _ln.setZValue(3)
            self.plot.addItem(_ln, ignoreBounds=True); _ln.setVisible(False)
            self.bc_sel_vp_lines.append(_ln)
        # PERSISTED manual slider overrides (None = use the per-selection adaptive seed). Once the user drags a
        # slider, that value sticks across selections AND sessions (saved to terminal_ui.json).
        self._zone_user_s = None
        self._eff_user_f = None
        # LARGE / SMALL market-order cutoffs are FULLY AUTOMATIC: large = the daemon's rolling p95, small = p50
        # (size_thr on the pulse), auto-updating every recompute. No manual slider, nothing to pin or reset —
        # the broad daemon distribution drives them (see _largesmall_thresholds).
        self._sel_sig = None           # Fix 1: change-detection signature of the last selection refresh (skip
                                       # the heavy recompute when nothing that affects the output changed)
        # SELECTION CONFLUENCE ALERT (Mode-10): beep when panel-0 (+50/-50 both green|red) + panel-2 eff-agg
        # confirmed spread >=65% (same colour) + panel-6 START/DURING phase confirmed spread >=15% (same
        # colour) ALL align — on a bucket close (LIVE, edge-triggered) or a RIGHT-arrow move (once/press).
        # Signals are captured at draw time -> panels 0, 2, 6 must be toggled ON.
        self._alert_sig = {}           # {badge_key: (spread_pct, strong_is_bull)} captured in _set_spread_badge
        self._alert_p0 = (0, 0)        # panel-0 +50/-50 cross colours: (n_green, n_red)
        self._alert_armed = True       # edge-trigger for the LIVE close path (re-arms when alignment drops)
        self._alert_last_tc = None     # last total_closed seen -> new-bucket-close detection
        self._alert_right_pending = False  # set by the RIGHT-arrow handler; consumed once at the next eval
        # SELECTION-SCOPED EXHAUSTION STRIP — two smoothed lines (blue bull / red bear gated exhaustion)
        # across the selected buckets, in a panel hanging below the selection; gold diamonds mark crossovers
        # (the exhausted side swaps). Persistent plot item; hidden when no selection. zValue 2 like the zones.
        self.bc_exh_strip = ExhaustionStripLayer(self.plot)
        self.bc_exh_strip.setZValue(2)
        self.plot.addItem(self.bc_exh_strip, ignoreBounds=True)
        self.bc_exh_strip.setVisible(False)
        # dashed GOLD 50% reference line for the exhaustion panel (the band midline = 50% exhaustion)
        # Reference lines. Panel 4: 50% orange. Panels 1/2/3: 50% LIGHT-GRAY midline + 25%/75% ORANGE quarter
        # lines. The 1/2/3 lines all use panel-0's dash spacing (cosmetic [5,10]).
        _ORANGE = "#ff9800"

        def _dpen(c, w=0.8):
            _p = pg.mkPen(c, width=w); _p.setCosmetic(True); _p.setDashPattern([5.0, 10.0]); return _p
        self.bc_exh_mid = pg.PlotDataItem(pen=pg.mkPen(_ORANGE, width=1, style=QtCore.Qt.DashLine))   # panel 4 (50%)
        self.bc_abs_mid = pg.PlotDataItem(pen=_dpen((150, 150, 150)))   # panel 1 (50%, light gray)
        self.bc_eff_mid = pg.PlotDataItem(pen=_dpen((150, 150, 150)))   # panel 2 (50%)
        self.bc_er_mid = pg.PlotDataItem(pen=_dpen((150, 150, 150)))    # panel 3 (50%)
        self.bc_lg_mid = pg.PlotDataItem(pen=_dpen((150, 150, 150)))   # panel 8a LARGE (50%)
        self.bc_sm_mid = pg.PlotDataItem(pen=_dpen((150, 150, 150)))   # panel 8b SMALL (50%)
        self.bc_abs_q = pg.PlotDataItem(pen=_dpen(_ORANGE), connect="finite")   # panel 1 (25%/75%, orange)
        self.bc_eff_q = pg.PlotDataItem(pen=_dpen(_ORANGE), connect="finite")   # panel 2 (25%/75%)
        self.bc_er_q = pg.PlotDataItem(pen=_dpen(_ORANGE), connect="finite")    # panel 3 (25%/75%)
        self.bc_lg_q = pg.PlotDataItem(pen=_dpen((255, 255, 255, 90), 0.5), connect="finite")   # panel 8a (25%/75%, faint white)
        self.bc_sm_q = pg.PlotDataItem(pen=_dpen((255, 255, 255, 90), 0.5), connect="finite")   # panel 8b (25%/75%, faint white)
        for _m in (self.bc_exh_mid, self.bc_abs_mid, self.bc_eff_mid, self.bc_er_mid,
                   self.bc_lg_mid, self.bc_sm_mid,
                   self.bc_abs_q, self.bc_eff_q, self.bc_er_q, self.bc_lg_q, self.bc_sm_q):
            _m.setZValue(3); _m.setVisible(False); self.plot.addItem(_m, ignoreBounds=True)
        # SELECTION-SCOPED EFF-AGG EVOLUTION STRIP — bull/bear per-bucket effective aggression as two
        # SYMMETRICALLY-smoothed NEON green/red lines, in a SECOND panel STACKED just below the exhaustion
        # strip ('2' toggles). Reuses the same parametrised layer + the eff_bull/bear arrays built for the
        # eff-agg zones; no crossover diamonds (it's a forcing-magnitude evolution, not an exhaustion swap).
        self.bc_eff_strip = ExhaustionStripLayer(self.plot, rgb_bull=_RGB_EFF_BULL, rgb_bear=_RGB_EFF_BEAR)
        self.bc_eff_strip.setZValue(2)
        self.plot.addItem(self.bc_eff_strip, ignoreBounds=True)
        self.bc_eff_strip.setVisible(False)
        # HARMONIC-MEAN sub-panel, STACKED just under P2 (toggles with P2/'2'): the HM of the bull share (green)
        # and bear share (red) PER CYCLE, drawn as STEP lines held flat across each cycle, crossing at the 50%
        # midline like P2 but cycle-averaged. A cycle = a run where one force stays dominant (share one side of 50%).
        self.bc_hm_bull = pg.PlotCurveItem(pen=pg.mkPen(_RGB_EFF_BULL, width=1.6), connect="finite")
        self.bc_hm_bear = pg.PlotCurveItem(pen=pg.mkPen(_RGB_EFF_BEAR, width=1.6), connect="finite")
        self.bc_hm_mid = pg.PlotDataItem(pen=_dpen((150, 150, 150)))
        self.bc_hm_lock = pg.PlotDataItem(pen=pg.mkPen((150, 150, 150), width=1, style=QtCore.Qt.DashLine))   # unlocked-region divider (dashed)
        self.bc_hm_calc = pg.PlotDataItem(pen=pg.mkPen((235, 200, 90), width=1.3))                            # HMS calc-window start (solid amber)
        for _h in (self.bc_hm_mid, self.bc_hm_lock, self.bc_hm_calc, self.bc_hm_bull, self.bc_hm_bear):
            _h.setZValue(3); _h.setVisible(False); self.plot.addItem(_h, ignoreBounds=True)
        # ABSORPTION HM sub-panel (rides Panel 1 / '1') — SAME machinery as the eff-agg HM (per-cycle step lines +
        # 50% midline + dashed non-locked divider + right-edge %-spread box over the last 2 LOCKED cycles), fed by
        # the absorption bull share. Green(bull)/purple(bear) to match Panel 1's identity.
        self.bc_abshm_bull = pg.PlotCurveItem(pen=pg.mkPen(_RGB_ABS_BULL, width=1.6), connect="finite")
        self.bc_abshm_bear = pg.PlotCurveItem(pen=pg.mkPen(_RGB_ABS_BEAR, width=1.6), connect="finite")
        self.bc_abshm_mid = pg.PlotDataItem(pen=_dpen((150, 150, 150)))
        self.bc_abshm_lock = pg.PlotDataItem(pen=pg.mkPen((150, 150, 150), width=1, style=QtCore.Qt.DashLine))  # unlocked-region divider (dashed)
        self.bc_abshm_calc = pg.PlotDataItem(pen=pg.mkPen((235, 200, 90), width=1.3))                           # HMS calc-window start (solid amber)
        for _h in (self.bc_abshm_mid, self.bc_abshm_lock, self.bc_abshm_calc, self.bc_abshm_bull, self.bc_abshm_bear):
            _h.setZValue(3); _h.setVisible(False); self.plot.addItem(_h, ignoreBounds=True)
        # DRAGGABLE HMS-window START handle for the P1 HM (amber): grab + slide it across locked-cycle boundaries to
        # widen/narrow the span the %-box averages; snaps to the nearest cycle on release; double-click the P1 HM
        # band resets to the default 2 cycles. _abshm_ncyc = user-chosen # of locked cycles (None -> default).
        self._abshm_ncyc = None; self._abshm_snap = []; self._abshm_hit = None
        self.bc_abshm_drag = pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen((235, 200, 90), width=1.6),
                                             hoverPen=pg.mkPen((255, 232, 120), width=2.6))
        self.bc_abshm_drag.setZValue(5); self.bc_abshm_drag.setVisible(False)
        self.plot.addItem(self.bc_abshm_drag, ignoreBounds=True)
        self.bc_abshm_drag.sigPositionChangeFinished.connect(self._abshm_drag_done)
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
        # LIQUIDITY-SWEEP LABELS (Ctrl+L) — detector-v1 event markers on the candles: a dashed leader from
        # the swept bar to a filled label ("S L. Sweep" red / "B L. Sweep" green, black text). Offline events
        # from study/out/liq_sweeps.csv; live = app.liq_detect (SAME frozen function) at bucket close. The
        # "Pull" label types exist for Phase 3 but no Pull events are produced yet. UNCALIBRATED (see tooltip).
        self.show_liq = True         # 15m sweeps ON by default (Ctrl+L toggles; persisted in terminal_ui.json)
        # Event set is built ONCE (CSV on load + live appended at bucket close) into gid-sorted parallel
        # lists so the draw path culls to the visible range by bisect — NEVER iterates all rows, NEVER
        # re-detects per frame (that was the 250->1700-item / 0.55->572ms regression).
        self._liq_events = []; self._liq_gids = []; self._liq_ts = []; self._liq_seen = set()
        self._load_liq_csv()                                     # offline 15m set (historical fallback); live = daemon
        self.bc_liq_leader = pg.PlotDataItem(
            pen=pg.mkPen((160, 160, 160, 180), width=1, style=QtCore.Qt.DashLine))
        self.bc_liq_leader.setZValue(30); self.plot.addItem(self.bc_liq_leader, ignoreBounds=True)
        self.bc_liq_leader.setVisible(False)
        self._liq_label_pool = []                                # BOUNDED reuse pool (<= LIQ_MAX_LABELS), freed when empty
        self._struct_label_pool = []                             # market-structure HH/HL/LH/LL label pool (scalp ZigZag)
        self._struct_sig = None; self._struct_labels = []; self._struct_idx = []   # +sorted bar-index for bisect cull
        self._struct_rsig = None                                 # render-skip guard (visible slice + y-scale)
        self._struct_label_pool_sw = []                          # swing-structure (coarse ZigZag) label pool
        self._struct_labels_sw = []; self._struct_idx_sw = []    # cached coarse swings (shares the _struct_sig gate)
        self._struct_pct_pool_sw = []                            # swing % -change sub-label pool (below each HH/HL/LH/LL)
        self._struct_pct_sw = []                                 # cached per-swing % move from the previous swing
        self._swing_pct = structure.ZIGZAG_SWING_PCT             # swing-ZigZag threshold %, live-set by the hamburger slider
        self._kc_scale = float(config.KELTNER_SCALE_DEFAULT)     # 1m-KC smooth-approx effective-TF scale (hamburger slider; 1.0 = native)
        _cbp = pg.mkPen((70, 200, 255), width=1.3, style=QtCore.Qt.DashLine); _cbp.setCosmetic(True)   # CHoCH bull
        _crp = pg.mkPen((255, 120, 90), width=1.3, style=QtCore.Qt.DashLine); _crp.setCosmetic(True)   # CHoCH bear
        self._choch_bull = pg.PlotCurveItem(pen=_cbp, connect="finite"); self._choch_bull.setZValue(28)
        self._choch_bear = pg.PlotCurveItem(pen=_crp, connect="finite"); self._choch_bear.setZValue(28)
        self._choch_added = False; self._choch_sig = None; self._choch_events = []   # CHoCH dashed lines (m10_choch)
        self._choch_bbar = []; self._choch_rsig = None           # sorted break-bar for bisect + render-skip guard
        # 4h BUY/SELL wick zones (hamburger m10_4hzone): last COMPLETED 4h bucket's lower wick (low..vq_lo GREEN
        # buy) + upper wick (vq_hi..high RED sell), as filled bands running from the 1m bar where that 4h bucket
        # STARTED out to the live edge (follows price). Live source = the secondary 4h worker; archive fallback.
        _G4 = (40, 230, 90); _R4 = (255, 45, 70)
        self._z4_buy_t = pg.PlotCurveItem(pen=None); self._z4_buy_b = pg.PlotCurveItem(pen=None)     # no border
        self._z4_sell_t = pg.PlotCurveItem(pen=None); self._z4_sell_b = pg.PlotCurveItem(pen=None)
        self._z4_buy_f = pg.FillBetweenItem(self._z4_buy_t, self._z4_buy_b, brush=pg.mkBrush((*_G4, 22)))
        self._z4_sell_f = pg.FillBetweenItem(self._z4_sell_t, self._z4_sell_b, brush=pg.mkBrush((*_R4, 22)))
        self._z4_items = (self._z4_buy_f, self._z4_sell_f, self._z4_buy_t, self._z4_buy_b,
                          self._z4_sell_t, self._z4_sell_b)
        for _z in self._z4_items:
            _z.setZValue(1); self.plot.addItem(_z, ignoreBounds=True); _z.setVisible(False)
        # 'B' overlay: the 4h candle's ABNORMAL-ORDER lines (imbalanced footprint levels of the 4h bucket, drawn
        # across its span). Two batched curves (connect='pairs'): buy = neon BLUE, sell = neon ORANGE — same
        # colours as the 1m imbalance lines, but sourced from the 4h bucket's ladder. Independent of the 1m lines.
        _pib = pg.mkPen((0, 153, 255), width=2.0); _pib.setCosmetic(True)
        _pis = pg.mkPen((255, 128, 0), width=2.0); _pis.setCosmetic(True)
        self._z4_imb_buy = pg.PlotCurveItem(pen=_pib); self._z4_imb_buy.setZValue(5)
        self._z4_imb_sell = pg.PlotCurveItem(pen=_pis); self._z4_imb_sell.setZValue(5)
        for _z in (self._z4_imb_buy, self._z4_imb_sell):
            self.plot.addItem(_z, ignoreBounds=True); _z.setVisible(False)
        self._zone4h_bid = None; self._zone4h_data = None
        self._zone4h_starts = []; self._zone4h_starts_sig = None
        # top-right readout: how full the FORMING 4h bucket is (curr_vol / target_vol) -> when the zones update
        self._z4_fill_lbl = pg.TextItem(anchor=(1.0, 0.0), color=(200, 205, 215))
        _zff = QtGui.QFont("Consolas", 11); _zff.setBold(True); self._z4_fill_lbl.textItem.setFont(_zff)
        self._z4_fill_lbl.setZValue(60); self.plot.addItem(self._z4_fill_lbl, ignoreBounds=True)
        self._z4_fill_lbl.setVisible(False)
        # Per-4h-bucket V (volume profile) / Z (zone) DISPLAY, driven by small buttons above the x-axis (detached
        # from the selection tool). Pools grown lazily; state = explicit user toggles keyed by (bucket end_time, kind).
        self._z4_curve_pool = []      # overlay curves: zone bands (fillLevel rects) + VP level lines
        self._z4_sep_pool = []        # 4h bucket-start separators (full-height dashed vlines, completed buckets only)
        self._z4_hist_pool = []       # volume-profile histograms (horizontal BarGraphItems, one per shown V)
        self._z4_btn_pool = []        # per-span 'V'/'Z' button TextItems
        self._z4_user = {}            # {(round(end_time,3), 'V'|'Z'): bool}  (absent -> default: last bucket's Z on)
        self._z4_btn_hits = []        # [(x, y, end_time, kind, on)] rebuilt each draw, for click hit-testing
        self._z4_last_buckets = []    # cached canvas buckets so a button click can redraw the layer immediately
        self._liq_status = pg.TextItem(anchor=(0, 0), color=(235, 225, 140))   # corner note: empty-by-location / zoom-in
        _lsf = QtGui.QFont("Consolas", 9); self._liq_status.textItem.setFont(_lsf)
        self._liq_status.setZValue(33); self.plot.addItem(self._liq_status, ignoreBounds=True)
        self._liq_status.setVisible(False); self._liq_status_txt = None
        # PIVOT INDICATOR (Ctrl+P) — S5j-r5 confluence detection + entry, SELECTION-SCOPED (only inside a drawn
        # Mode-10 selection; app.pivot_detect). Marks each setup's detection + its entry as two circular BADGES —
        # a bold "D" (detection) and "E" (entry) coin, GREEN for buys / RED for sells — with a DASHED faded leader
        # from each candle to its badge (+ a 2nd back to the leg-5 N=60..100 candle) and a SOLID line joining
        # detection<->entry. Leaders/connectors are faded GREEN (buy) / RED (sell), split per side. Buys below
        # the candles, sells mirrored above. Hovering a badge pops a stats box (buy -> below, sell -> above).
        self.show_pivot = False
        self.pivot_d_only = True     # PIVOT V3: draw ONLY the D detections (tier-filled). No E/E2/E3, no connectors,
                                     # no entry-line overlay. The E-stage code is kept but gated off until V3 re-adds entries.
        self.pivot_v3_filter = True  # PIVOT V3 Step-3: FADE the D's that fail the entry rule (cyan/orange + directional
                                     # 4H zone), so only the tradeable D's stand out. Applied in D-only mode.
        self.pivot_new_e = True      # PIVOT V3 NEW E: the recorded-combo E's are BRIGHT (real entries); every OTHER
                                     # (faded) D also gets its E drawn DIM + a click-to-study position sim, for eyeballing.
        self.pivot_causal = False   # 'N' — NO-LOOK-AHEAD: truncate pivot detection at the selection's right edge so
                                    # the D-tier / E-held / E2 badges read only data up to the edge (true LIVE
                                    # values), not the +FWD future window that settles them. Panels are already
                                    # causal-to-edge; this makes the pivot honest while scrubbing the Right arrow.
        self._sel_hi_t = None       # end_time of the selection's right edge (the scrub 'as-of' point) — set each
                                    # time the selection draws; the 4h zone reads it in causal mode so it, too, shows
                                    # the wick that was live AS OF the edge instead of the newest 4h bucket.
        _grn = (40, 230, 90, 140); _red = (255, 45, 70, 140)     # faded side colours (alpha keeps them behind)
        self.bc_pivot_leaders = {}               # side -> DASHED PlotDataItem (candle<->label + label->leg-5)
        self.bc_pivot_conn = {}                  # side -> SOLID PlotDataItem (detection<->entry)
        for _sd, _c in (("long", _grn), ("short", _red)):
            _ld = pg.PlotDataItem(pen=pg.mkPen(_c, width=1, style=QtCore.Qt.DashLine), connect="finite")
            _ld.setZValue(30); self.plot.addItem(_ld, ignoreBounds=True); _ld.setVisible(False)
            _cn = pg.PlotDataItem(pen=pg.mkPen(_c, width=1.4), connect="finite")
            _cn.setZValue(30); self.plot.addItem(_cn, ignoreBounds=True); _cn.setVisible(False)
            self.bc_pivot_leaders[_sd] = _ld; self.bc_pivot_conn[_sd] = _cn
        # circular D/E badges — green (buy) / red (sell) coins at a fixed pixel size (read the same at any zoom),
        # with the bold letter drawn on top via the pooled TextItems.
        self.bc_pivot_dots = pg.ScatterPlotItem(pxMode=True, symbol="o", size=20,
                                                pen=pg.mkPen(0, 0, 0, 180, width=1))
        self.bc_pivot_dots.setZValue(31); self.plot.addItem(self.bc_pivot_dots, ignoreBounds=True)
        self.bc_pivot_dots.setVisible(False)
        # GOLDEN STAR for Buy-D's whose forming-VP meets the criteria (VPFADE overlay). Drawn just off the D coin,
        # its own scatter layer so it never touches the badges. Pure highlight — no effect on fade/entry/detection.
        self.bc_pivot_stars = pg.ScatterPlotItem(pxMode=True, symbol="star", size=18,
                                                 brush=pg.mkBrush(255, 205, 40),
                                                 pen=pg.mkPen(140, 100, 0, width=1))
        self.bc_pivot_stars.setZValue(33); self.plot.addItem(self.bc_pivot_stars, ignoreBounds=True)
        self.bc_pivot_stars.setVisible(False)
        # RED cross ✕ = the TRAP marker for E-entries in the WRONG value-half (m10_estar overlay; UNVALIDATED).
        self.bc_pivot_traps = pg.ScatterPlotItem(pxMode=True, symbol="x", size=15,
                                                 brush=pg.mkBrush(235, 60, 60),
                                                 pen=pg.mkPen(120, 0, 0, width=1))
        self.bc_pivot_traps.setZValue(33); self.plot.addItem(self.bc_pivot_traps, ignoreBounds=True)
        self.bc_pivot_traps.setVisible(False)
        # CLOCK = wait-marker for cyan/orange D's that fail Step 3 (non-directional zone -> Path-B E-hunter). Register
        # a custom 'clock' symbol once (circle face + hour/minute hands), then a light-blue hollow scatter for it.
        from pyqtgraph.graphicsItems.ScatterPlotItem import Symbols as _SYM
        if "clock" not in _SYM:
            from pyqtgraph.Qt import QtGui as _QtGui
            _clk = _QtGui.QPainterPath()
            _clk.addEllipse(-0.5, -0.5, 1.0, 1.0)                 # face
            _clk.moveTo(0.0, 0.0); _clk.lineTo(0.0, -0.34)        # hour hand (up)
            _clk.moveTo(0.0, 0.0); _clk.lineTo(0.26, 0.0)         # minute hand (right)
            _SYM["clock"] = _clk
        self.bc_pivot_clocks = pg.ScatterPlotItem(pxMode=True, symbol="clock", size=16,
                                                  brush=pg.mkBrush(0, 0, 0, 0),
                                                  pen=pg.mkPen(150, 205, 255, width=1.4))
        self.bc_pivot_clocks.setZValue(33); self.plot.addItem(self.bc_pivot_clocks, ignoreBounds=True)
        self.bc_pivot_clocks.setVisible(False)
        # ELECTRIC-PURPLE ring around a V3 entry (D or E) that ALSO has VPIN confluence (VPIN >= its warn line at the
        # entry bar, ratio >= 1.0). Hollow ring larger than the badge so it encircles it. Toggle m10_vpinring.
        self.bc_pivot_vpin = pg.ScatterPlotItem(pxMode=True, symbol="o", size=30,
                                                brush=pg.mkBrush(0, 0, 0, 0),
                                                pen=pg.mkPen(178, 70, 255, width=2.4))
        self.bc_pivot_vpin.setZValue(32); self.plot.addItem(self.bc_pivot_vpin, ignoreBounds=True)
        self.bc_pivot_vpin.setVisible(False)
        self.bc_entry_active = pg.ScatterPlotItem(pxMode=True, symbol="o", size=30,   # electric-magenta ring on
                                                  pen=pg.mkPen(255, 0, 230, width=2.6),  # the entry being viewed
                                                  brush=pg.mkBrush(0, 0, 0, 0))
        self.bc_entry_active.setZValue(34); self.plot.addItem(self.bc_entry_active, ignoreBounds=True)
        self.bc_entry_active.setVisible(False)
        self._pivot_label_pool = []              # reused TextItems (the bold D/E glyphs), grown lazily
        self._eff_cyc_labels = []                # P2 per-cycle harmonic-mean % labels (reused TextItems), grown lazily
        self._eff_cyc_min = 3                    # min bars in a P2 cycle to bother labelling its harmonic mean
        self._hm_time_labels = []                # HM sub-panel per-cycle ELAPSED-TIME labels (locked on, always shown)
        self._hm_time_n = 0                      # count of active (positioned) HM time labels this refresh
        self._hm_min_cyc = 4                     # P2 cycles shorter than this (buckets) are NOISE -> merged out of cycle detection
        self._hm_ncyc = 2                        # HMS box + backdrop span the last N LOCKED cycles
        self._pivot_hovers = []                  # [(x, y, stats_html, is_buy)] badge centres -> the hover box
        self._pivot_sig = None
        self._psc = None            # no-selection Pivot V3 detection cache (incremental: settled prefix + frontier)
        # clickable per-entry exit-line overlay (V3 D-EXIT): click a trade entry to toggle its lines — entry
        # (white), fixed structural SL (yellow), MAX-reached (green), same-D stop ratchets (blue dash) — plus a
        # TP@opp-D / stop tag at the exit bar. Default (see _entry_default_key): the last RECORDED entry is ON,
        # else — if only faded study D/E's are in view — the last one anyway, so it's never blank.
        self._pivot_entries = []                 # [(key, eb, shelf, epx, sl, reason, tp, buy, ratchets/trails, maxpx, exit_bar, gross, fade, path)]
        self._entry_lines_user = {}              # global key -> explicit user on/off (absent = default: last-on)
        self._entry_line_pool = []; self._entry_lbl_pool = []; self._pivot_n = 0
        self._entry_zone_pool = []               # Path-B light-blue TP zones (filled, no border)
        self.pivot_tooltip = pg.TextItem(anchor=(0.5, 0.0), fill=pg.mkBrush(18, 20, 26, 238),
                                         border=pg.mkPen(90, 96, 108, 220))
        self.pivot_tooltip.setZValue(62); self.plot.addItem(self.pivot_tooltip, ignoreBounds=True)
        self.pivot_tooltip.hide()
        # LARGE / SMALL MARKET-ORDER STRIPS (slot 8, replacing the old liquidation wave). Two share-style
        # panels like 1/2/3: LARGE = large-BUY vs large-SELL VOLUME share (blue buy / orange sell, matching the
        # heatmap large-order bubbles); SMALL = small-BUY vs small-SELL trade-COUNT share (green / red). Each
        # bucket's size histogram (sz_*) is thresholded LIVE at the slider qty -> retroactive cutoff.
        _RGB_LG_BULL = (0, 180, 255); _RGB_LG_BEAR = (255, 145, 0)     # large: electric blue buy / orange sell
        _RGB_SM_BULL = (0, 230, 118); _RGB_SM_BEAR = (255, 82, 82)     # small: green buy / red sell
        self.bc_lg_strip = ExhaustionStripLayer(self.plot, rgb_bull=_RGB_LG_BULL, rgb_bear=_RGB_LG_BEAR)
        self.bc_sm_strip = ExhaustionStripLayer(self.plot, rgb_bull=_RGB_SM_BULL, rgb_bear=_RGB_SM_BEAR)
        for _st in (self.bc_lg_strip, self.bc_sm_strip):
            _st.setZValue(2); self.plot.addItem(_st, ignoreBounds=True); _st.setVisible(False)
        self._RGB_LG = (_RGB_LG_BULL, _RGB_LG_BEAR); self._RGB_SM = (_RGB_SM_BULL, _RGB_SM_BEAR)
        # net BUY−SELL signed-log "wave" lines (liquidation-wave style): up half = net buying (buy colour),
        # down half = net selling (sell colour), meeting on the dashed zero midline (bc_lg_mid/bc_sm_mid).
        self.bc_lg_pos = pg.PlotDataItem(pen=pg.mkPen(_RGB_LG_BULL, width=2.4), connect="finite")
        self.bc_lg_neg = pg.PlotDataItem(pen=pg.mkPen(_RGB_LG_BEAR, width=2.4), connect="finite")
        self.bc_sm_pos = pg.PlotDataItem(pen=pg.mkPen(_RGB_SM_BULL, width=2.4), connect="finite")
        self.bc_sm_neg = pg.PlotDataItem(pen=pg.mkPen(_RGB_SM_BEAR, width=2.4), connect="finite")
        for _it in (self.bc_lg_pos, self.bc_lg_neg, self.bc_sm_pos, self.bc_sm_neg):
            _it.setZValue(3); _it.setVisible(False); self.plot.addItem(_it, ignoreBounds=True)
        # per-bucket DOMINANCE HISTOGRAM bars (the current LARGE/SMALL render): winner colour on top, loser
        # muted underneath, height = total large activity.
        self.bc_lg_bars = StackedBarLayer(); self.bc_sm_bars = StackedBarLayer()
        for _it in (self.bc_lg_bars, self.bc_sm_bars):
            _it.setZValue(2); _it.setVisible(False); self.plot.addItem(_it, ignoreBounds=True)
        # Minimalist hairline dividers between the stacked panels (centre-fading, dark-friendly).
        self.bc_panel_sep = PanelSeparatorLayer(self.plot)
        self.bc_panel_sep.setZValue(3)            # just above the panels
        self.plot.addItem(self.bc_panel_sep, ignoreBounds=True)
        self.bc_panel_sep.setVisible(False)

        # --- crosshair (patch §13): faded gray, wider-spaced dashes ---
        pen = pg.mkPen(color=(170, 170, 170, 150), width=1)   # alpha 150 -> lightly faded
        pen.setCosmetic(True); pen.setDashPattern([4.0, 8.0])   # short dash, wider gap (device-pixel constant)
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
        for _k in ("ABSORPTION", "ABS-HM", "EFF-AGG", "EFF-HM", "E/R", "EXHAUSTION", "LARGE MKT", "SMALL MKT",
                   "BEFORE", "START/DURING", "END",        # phase panels 5/6/7 — UP/DOWN spread badge
                   "PANEL9_BULL", "PANEL9_BEAR", "PANEL9_SUM",
                   "PANEL0_BULL", "PANEL0_BEAR", "PANEL0_SUM"):
            _bd = pg.TextItem(anchor=(0, 0.5), color=(0, 0, 0))
            _bf = QtGui.QFont("Consolas", 11); _bf.setBold(True)
            _bd.textItem.setFont(_bf)
            _bd.setZValue(62)
            self.plot.addItem(_bd, ignoreBounds=True)
            _bd.hide()
            self._spread_badges[_k] = _bd
        # LARGE/SMALL panels get a SECOND label UNDER the winner badge: the selection-total B/S split
        # (B: buy | S: sell), dominant side bolded + the fill in the dominant side's colour. Own non-bold
        # items (HTML controls the per-side weight); black text like the badges.
        self.bc_lg_tot = pg.TextItem(anchor=(0, 0.5), color=(0, 0, 0))
        self.bc_sm_tot = pg.TextItem(anchor=(0, 0.5), color=(0, 0, 0))
        _totf = QtGui.QFont("Consolas", 10)
        for _t in (self.bc_lg_tot, self.bc_sm_tot):
            _t.textItem.setFont(_totf); _t.setZValue(62)
            self.plot.addItem(_t, ignoreBounds=True); _t.hide()
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
        self.bc_phase_lock = {}            # per-phase LOCK-IN divider (vertical light-gray dashed)
        self.show_phase = {}
        for _ph in self._PHASES:
            _ly = ExhaustionStripLayer(self.plot, rgb_bull=_RGB_ER_BULL, rgb_bear=_RGB_ER_BEAR)
            _ly.setZValue(2)
            self.plot.addItem(_ly, ignoreBounds=True)
            _ly.setVisible(False)
            self.bc_phase[_ph] = _ly
            _plk = pg.PlotDataItem(pen=pg.mkPen((150, 150, 150), width=1, style=QtCore.Qt.DashLine))
            _plk.setZValue(3); _plk.setVisible(False); self.plot.addItem(_plk, ignoreBounds=True)
            self.bc_phase_lock[_ph] = _plk
            self.show_phase[_ph] = False   # phase panels 5/6/7 HIDDEN by default ('5'-'7' toggle)
        self.show_phase_table = False      # phase TABLE shown on its own via 't' (no panel needed)
        self._proxy = pg.SignalProxy(self.plot.scene().sigMouseMoved,
                                     rateLimit=60, slot=self._on_mouse_move)
        # last cursor scene pos while inside the plot — drives the A3a live-breathe
        # re-fire so a hovered forming bucket updates each frame, not just on motion.
        self._last_hover_pos = None
        self.show_state = False   # STATE verdict + debug lines hidden until 'y' (both stats boxes)
        self.show_vel_abn = False  # abnormal-velocity DIAMONDS OFF by default ('v' toggles; 2px border always on)
        self.show_sel_stats = False  # Mode-10 selection stats box HIDDEN by default ('h' toggles)
        self.show_abs_strip = False  # Mode-10 selection ABSORPTION panel — HIDDEN by default ('1' toggles) — slot 1
        self.show_eff_strip = True   # Mode-10 selection eff-agg evolution panel ON by default ('2' toggles)
        self.show_abs_hm = True      # P1 HM sub-panel — INDEPENDENT slot (Ctrl+1); shows even if P1 panel is hidden
        self.show_eff_hm = True      # P2 HM sub-panel — INDEPENDENT slot (Ctrl+2); shows even if P2 panel is hidden
        self.show_er_strip = False   # Mode-10 selection effort/result panel — HIDDEN by default ('3' toggles)
        self.show_exh_strip = False  # Mode-10 selection exhaustion panel — HIDDEN by default ('4' toggles) — slot 4
        self._ls_mode = 0  # Mode-10 market-order panels (slot 8), '8' cycles: 0 hidden / 1 LARGE only / 2 LARGE+SMALL
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
        # NOTE: slot 8 was the Liquidation Pressure WAVE; it was REPLACED by the LARGE/SMALL market-order panels
        # (bc_lg_strip/bc_sm_strip, created above). The liquidation DATA path is untouched — liq_short/liq_long
        # still feed the 12-state classifier, the 'L' Liquidation Marks, the stats box, and alerts.
        # PANEL 9 — COMPOSITE LEAN ('9', very bottom): ONE line = per-bucket AVERAGE of the four panels' signed
        # spreads. GREEN above the zero baseline (net bullish lean), RED below (net bearish) — same sign-split
        # treatment as the other lean panels.
        self.show_panel9 = True
        _GREY9 = (140, 140, 140)
        self.bc_p9_zero = pg.PlotDataItem(pen=pg.mkPen("#555555", width=1, style=QtCore.Qt.DashLine))
        # thin gold dashed +/-50% refs with WIDER dash spacing (custom pattern, cosmetic = crisp at any zoom)
        _gp_hi = pg.mkPen("#ff9800", width=0.8); _gp_hi.setCosmetic(True); _gp_hi.setDashPattern([5.0, 10.0])
        _gp_lo = pg.mkPen("#ff9800", width=0.8); _gp_lo.setCosmetic(True); _gp_lo.setDashPattern([5.0, 10.0])
        self.bc_p9_gold_hi = pg.PlotDataItem(pen=_gp_hi)  # +50%
        self.bc_p9_gold_lo = pg.PlotDataItem(pen=_gp_lo)  # -50%
        # BULL-trend line: green when >0, muted grey when <0
        self.bc_p9_bull_g = pg.PlotDataItem(pen=pg.mkPen("#28e65a", width=2.4), connect="finite")
        self.bc_p9_bull_x = pg.PlotDataItem(pen=pg.mkPen(_GREY9, width=2.0), connect="finite")
        # BEAR-trend line: red when <0, muted grey when >0
        self.bc_p9_bear_r = pg.PlotDataItem(pen=pg.mkPen("#ff2d46", width=2.4), connect="finite")
        self.bc_p9_bear_x = pg.PlotDataItem(pen=pg.mkPen(_GREY9, width=2.0), connect="finite")
        self.bc_p9_sum = pg.PlotDataItem(pen=pg.mkPen("#2d9cff", width=1.3))                  # NEON-BLUE sum (bull+bear)
        self.bc_p9_lock = pg.PlotDataItem(pen=pg.mkPen((150, 150, 150), width=1, style=QtCore.Qt.DashLine))  # LOCK-IN divider (vertical)
        # add refs + grey halves FIRST, colored/sum lines LAST so they paint on top (same zValue)
        self._bc_p9_items = (self.bc_p9_zero, self.bc_p9_gold_hi, self.bc_p9_gold_lo, self.bc_p9_lock,
                             self.bc_p9_bull_x, self.bc_p9_bear_x, self.bc_p9_bull_g, self.bc_p9_bear_r,
                             self.bc_p9_sum)
        for _it in self._bc_p9_items:
            _it.setZValue(3); _it.setVisible(False); self.plot.addItem(_it, ignoreBounds=True)
        # PANEL 0 ('0') — a SMOOTHED twin of Panel 9: each line = (current + locked)/2. Identical items/colors.
        self.show_panel0 = True
        self._candle_mode = 0            # 'W' cycle: 0 normal>1 whisker>2 footprint>3 delta>4 force>5 delta-force (persisted)
        self._vp_mode = 1                # volume-profile mode 0..7 (default 1 = Force, the existing 4h VP look; persisted)
        self._hide_candles = False       # Ctrl+H — hide the candle glyphs (see the VP / zones without candle noise; persisted)
        _gp0_hi = pg.mkPen("#ff9800", width=0.8); _gp0_hi.setCosmetic(True); _gp0_hi.setDashPattern([5.0, 10.0])
        _gp0_lo = pg.mkPen("#ff9800", width=0.8); _gp0_lo.setCosmetic(True); _gp0_lo.setDashPattern([5.0, 10.0])
        self.bc_p0_zero = pg.PlotDataItem(pen=pg.mkPen("#555555", width=1, style=QtCore.Qt.DashLine))
        self.bc_p0_gold_hi = pg.PlotDataItem(pen=_gp0_hi)
        self.bc_p0_gold_lo = pg.PlotDataItem(pen=_gp0_lo)
        self.bc_p0_bull_g = pg.PlotDataItem(pen=pg.mkPen("#28e65a", width=2.4), connect="finite")
        self.bc_p0_bull_x = pg.PlotDataItem(pen=pg.mkPen(_GREY9, width=2.0), connect="finite")
        self.bc_p0_bear_r = pg.PlotDataItem(pen=pg.mkPen("#ff2d46", width=2.4), connect="finite")
        self.bc_p0_bear_x = pg.PlotDataItem(pen=pg.mkPen(_GREY9, width=2.0), connect="finite")
        self.bc_p0_sum = pg.PlotDataItem(pen=pg.mkPen("#2d9cff", width=1.3))
        self.bc_p0_lock = pg.PlotDataItem(pen=pg.mkPen((150, 150, 150), width=1, style=QtCore.Qt.DashLine))
        self._bc_p0_items = (self.bc_p0_zero, self.bc_p0_gold_hi, self.bc_p0_gold_lo, self.bc_p0_lock,
                             self.bc_p0_bull_x, self.bc_p0_bear_x, self.bc_p0_bull_g, self.bc_p0_bear_r,
                             self.bc_p0_sum)
        for _it in self._bc_p0_items:
            _it.setZValue(3); _it.setVisible(False); self.plot.addItem(_it, ignoreBounds=True)
        # PANEL 0 non-locked TAIL: light-grey dashed continuation of the blue line over the settling buckets
        self.bc_p0_sum_tail = pg.PlotDataItem(
            pen=pg.mkPen((150, 150, 150), width=1.3, style=QtCore.Qt.DashLine), connect="finite")
        self.bc_p0_sum_tail.setZValue(3); self.bc_p0_sum_tail.setVisible(False)
        self.plot.addItem(self.bc_p0_sum_tail, ignoreBounds=True)
        # PANEL 0 level-CROSS markers: thin bright 'x' at the LAST confirmed cross of +50/0/-50 (per-spot colour)
        self.bc_p0_cross = pg.ScatterPlotItem(symbol="x", size=11, brush=None)
        self.bc_p0_cross.setZValue(4); self.bc_p0_cross.setVisible(False)
        self.plot.addItem(self.bc_p0_cross, ignoreBounds=True)
        # per-panel LOCK-IN dividers (vertical light-gray dashed) for panels 1-4: LEFT of the line = fully
        # formed (locked), right = still settling. 1/2/3 = centered-window forward half; 4 = envelope tail.
        self.bc_abs_lock = pg.PlotDataItem(pen=pg.mkPen((150, 150, 150), width=1, style=QtCore.Qt.DashLine))
        self.bc_eff_lock = pg.PlotDataItem(pen=pg.mkPen((150, 150, 150), width=1, style=QtCore.Qt.DashLine))
        self.bc_er_lock = pg.PlotDataItem(pen=pg.mkPen((150, 150, 150), width=1, style=QtCore.Qt.DashLine))
        self.bc_exh_lock = pg.PlotDataItem(pen=pg.mkPen((150, 150, 150), width=1, style=QtCore.Qt.DashLine))
        self.bc_lg_lock = pg.PlotDataItem(pen=pg.mkPen((150, 150, 150), width=1, style=QtCore.Qt.DashLine))
        self.bc_sm_lock = pg.PlotDataItem(pen=pg.mkPen((150, 150, 150), width=1, style=QtCore.Qt.DashLine))
        for _it in (self.bc_abs_lock, self.bc_eff_lock, self.bc_er_lock, self.bc_exh_lock,
                    self.bc_lg_lock, self.bc_sm_lock):
            _it.setZValue(3); _it.setVisible(False); self.plot.addItem(_it, ignoreBounds=True)
        # LIVE pivot audio ('Pivot Alert' sub-toggle, OWN voice, independent of the master Audio Feed).
        # MUST be set before _load_ui_state so the persisted "pivot_audio" value can override this default.
        self._pivot_audio_on = False
        self._pivot_audio_seeded = False
        self._pivot_audio_last_et = 0.0
        self._pivot_e_seeded = False              # "Enter E now" audio (Path-B New-E at the live edge), seed + dedup
        self._pivot_e_spoken = set()              # bucket end_times already announced as an E entry
        # Persisted hamburger toggles (Sub-Widgets + Mode 10 Overlays): {key: checked}. Loaded here,
        # applied to the checkboxes once the menu is wired (_apply_saved_toggles). _loading_ui suppresses
        # save churn while restoring.
        self._saved_toggles: dict = {}
        self._loading_ui: bool = False
        self._load_ui_state()   # restore the panel toggles saved by a prior session (overrides the defaults above)
        self.alerts = AlertsLedger(self)
        self.drawbar = DrawingToolbar(self)
        self.menu = FloatingOverlayMenu(self)
        self.menu.set_swing_pct(self._swing_pct)   # sync the swing slider to the restored/default sensitivity
        self.menu.set_kc_scale(self._kc_scale)     # sync the Keltner-scale slider to the restored/default value
        self.menu.set_candle_mode(self._candle_mode)   # sync the Candle-Mode dropdown to the restored/default
        self.menu.set_vp_mode(self._vp_mode)           # sync the Volume-Profile-Mode dropdown
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
        # (LIVE pivot-audio state — _pivot_audio_on / _seeded / _last_et — is initialized above, BEFORE
        # _load_ui_state, so the persisted 'pivot_audio' toggle can override it. Tracking uses the last
        # bucket's end_time since closed_buckets is a rolling-capped list — len() is constant.)

        # fix #10: double-click anywhere on the chart resets/auto-fits the view
        self.plot.scene().sigMouseClicked.connect(self._on_scene_click)

        # --- connection watchdog: on-chart banner + tunnel/socket auto-heal ---
        # The pipe worker already retries its socket forever, but a DEAD SSH TUNNEL left the retry loop
        # spinning against a closed port with the only evidence in the console. This 1s watchdog shows a
        # banner ON THE CHART while disconnected and re-heals every 5s (tunnel relaunch if its port died
        # + immediate socket retry, exactly what the manual refresh button does).
        self._conn_banner = QtWidgets.QLabel(self)   # child of the WINDOW -> centred + rises above the drawing toolbar
        self._conn_banner.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self._conn_banner.setAlignment(QtCore.Qt.AlignCenter)
        self._conn_banner.setStyleSheet(
            "background:rgba(122,31,31,235); color:#ffffff; font: bold 15px 'Consolas';"
            "border:1px solid #b03030; border-radius:10px; padding:14px 24px;")
        self._conn_banner.hide()
        self._conn_down_s = 0
        self._conn_timer = QtCore.QTimer(self)
        self._conn_timer.timeout.connect(self._conn_watchdog)
        self._conn_timer.start(1000)

        # --- drawing controller ---
        self.drawer = DrawingController(self.plot)
        self.drawer.toolbar = self.drawbar         # §7.3 — enables auto-revert
        self.drawbar.toolSelected.connect(self.drawer.set_tool)
        self.drawbar.show()    # toolbar ON by default — the menu's 'drawing' checkbox is checked, but its
                               # toggled signal isn't wired yet at build, so show it explicitly (resizeEvent
                               # positions it top-centre once the window lays out).
        self.drawer.selectionChanged.connect(self._refresh_selection_stats)   # Magic Selection -> stats
        QtGui.QShortcut(QtGui.QKeySequence("Escape"), self, activated=self.drawer.cancel)
        # Both arrows move the Magic Selection's RIGHT edge only: Right = +1 bucket (extend), Left = -1
        # (pull back). Left edge stays; clamped to >= 1 bucket of width. No-op without a selection.
        QtGui.QShortcut(QtGui.QKeySequence("Right"), self, activated=self._on_sel_right)   # +1 bucket / replay: next candle
        QtGui.QShortcut(QtGui.QKeySequence("Left"), self, activated=self._on_sel_left)     # -1 bucket / replay: prev candle
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Right"), self, activated=self._toggle_replay_autoplay)  # replay: auto-play (Left/Right stops)
        # quick toggles: 's' = Stats Box overlay, 'd' = Vector Drawing toolbar. Flip the menu
        # checkbox so the menu stays in sync and the existing show/hide + teardown logic runs.
        QtGui.QShortcut(QtGui.QKeySequence("S"), self,
                        activated=lambda: self.menu.layer_checks["m10_stats"].toggle())
        QtGui.QShortcut(QtGui.QKeySequence("D"), self,
                        activated=lambda: self.menu.sub_checks["drawing"].toggle())
        QtGui.QShortcut(QtGui.QKeySequence("P"), self,
                        activated=lambda: self.menu.layer_checks["m10_poc"].toggle())
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+P"), self, activated=self._toggle_pivot)  # PIVOT INDICATOR (selection-scoped)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Z"), self, activated=self._toggle_sel_vp)  # selection Volume Profile on/off
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+H"), self, activated=self._toggle_hide_candles)  # hide candle glyphs (VP/zones only)
        QtGui.QShortcut(QtGui.QKeySequence("N"), self, activated=self._toggle_pivot_causal)  # No-look-ahead pivot badges
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
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+1"), self, activated=self._toggle_abs_hm)   # P1 HM sub-panel
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+2"), self, activated=self._toggle_eff_hm)   # P2 HM sub-panel
        QtGui.QShortcut(QtGui.QKeySequence("3"), self, activated=self._toggle_er_strip)
        QtGui.QShortcut(QtGui.QKeySequence("4"), self, activated=self._toggle_exh_strip)
        # '5'-'7' = the per-phase panels (BEFORE / START/DURING / END), UP green / DOWN red running opacity
        for _key, _ph in (("5", "BEFORE"), ("6", "START/DURING"), ("7", "END")):
            QtGui.QShortcut(QtGui.QKeySequence(_key), self,
                            activated=lambda p=_ph: self._toggle_phase(p))
        QtGui.QShortcut(QtGui.QKeySequence("8"), self, activated=self._toggle_largesmall)  # panel 8: LARGE+SMALL mkt orders
        QtGui.QShortcut(QtGui.QKeySequence("9"), self, activated=self._toggle_panel9)    # panel 9: COMPOSITE lean
        QtGui.QShortcut(QtGui.QKeySequence("0"), self, activated=self._toggle_panel0)    # panel 0: smoothed P9
        QtGui.QShortcut(QtGui.QKeySequence("W"), self, activated=self._toggle_whisker)   # volume-quantile whisker bars
        QtGui.QShortcut(QtGui.QKeySequence("Z"), self, activated=self._z4_deactivate_all)   # turn OFF all 4h V/Z overlays
        QtGui.QShortcut(QtGui.QKeySequence("Delete"), self, activated=lambda: self.drawer.delete_selected())
        QtGui.QShortcut(QtGui.QKeySequence("Backspace"), self, activated=lambda: self.drawer.delete_selected())
        QtGui.QShortcut(QtGui.QKeySequence("T"), self, activated=self._toggle_phase_table)  # phase table (no panel needed)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+N"), self, activated=spawn_window)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+S"), self, activated=self._save_drawings_now)  # force-flush drawings + confirm
        # Ctrl+F = jump-to-Idx: type/paste a bucket Idx (tooltip format fine, e.g. "20.977"),
        # Enter centers that bucket on screen (index modes; unlocks view-follow like a manual pan)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+F"), self, activated=self._idx_jump_show)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+L"), self, activated=self._toggle_liq)  # liquidity-sweep labels

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
        # SECOND lightweight worker subscribed to the LIVE 4h stream (the daemon streams every timeframe) — feeds
        # the 4h buy/sell wick zones so they update automatically, no manual archive pull. No baseline needed.
        self.worker_4h = PipeClientWorker(tf="4h")
        self.worker_4h.start()

        # --- 20Hz master loop ---
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._on_timer)
        self.timer.start(config.GUI_TIMER_MS)

        # --- session profiler (default-on, client-side lag hunt; negligible overhead, read-only) ---
        self._perf = None
        if getattr(config, "SESSION_PERF", False):
            try:
                self._perf = SessionProfiler(os.path.join(config.DATA_DIR, "session_perf.log"))
                self._perf_timer = QtCore.QTimer(self)
                self._perf_timer.timeout.connect(self._perf_flush)
                self._perf_timer.start(int(getattr(config, "SESSION_PERF_SECS", 10.0) * 1000))
            except Exception:
                self._perf = None

        # A5 — open straight onto Mode 10 (the primary surface), never the time chart. Same
        # path the combo uses: hides time components, applies the dark scanner theme, and
        # _on_timer paints Mode 10 directly. The reordered combo already shows it at index 0.
        # initial=True -> a HARD last-24h window on open (never auto-extend back to an old study/replay drawing,
        # which would strand the Start Date weeks back and make the first load crawl). Replay still resumes its
        # own remembered cursor on toggle-on, independently of this.
        self._set_scanner("bucket_canvas", initial=True)

    # ------------------------------------------------------------------
    def _wire_menu(self) -> None:
        self.menu.tfChanged.connect(self._change_tf)
        self.menu.multiplierChanged.connect(lambda v: setattr(self.bc_obs, "visible_filter", v))
        self.menu.chartFilterChanged.connect(lambda v: setattr(self.depthwall_item, "threshold", float(v)))
        self.menu.layerToggled.connect(self._toggle_layer)
        self.menu.subWidgetToggled.connect(self._toggle_subwidget)
        self.menu.helpRequested.connect(self._show_shortcuts)
        self._apply_saved_toggles()                # restore EVERY hamburger toggle from the saved state
        self.menu.scannerChanged.connect(self._set_scanner)
        self.menu.scan_time_changed.connect(self._on_scan_time_changed)
        self.menu.replayToggled.connect(self._on_replay_toggled)
        self.menu.swingSensitivityChanged.connect(self._on_swing_sensitivity)   # swing-ZigZag threshold slider
        self.menu.keltnerScaleChanged.connect(self._on_kc_scale)   # 1m-KC smooth-approx effective-TF scale slider
        self.menu.candleModeChanged.connect(self._on_candle_mode)  # Candle Mode dropdown (mirrors 'W')
        self.menu.vpModeChanged.connect(self._on_vp_mode)          # Volume Profile Mode dropdown
        self.menu.scan_time_edit.set_range_provider(self._data_date_range)   # calendar: disable no-data days

        # Modifier mouse-wheel over the chart: Ctrl nudges the Scan Start (Zero Point) anchor ±1 min
        # (debounced — title scrubs live, one coalesced redraw); Shift zooms the X axis only.
        self._orig_vb_wheel = self.vb.wheelEvent
        self.vb.wheelEvent = self._vb_wheel
        self._scan_nudge_timer = QtCore.QTimer(self)
        self._scan_nudge_timer.setSingleShot(True)
        self._scan_nudge_timer.setInterval(90)
        self._scan_nudge_timer.timeout.connect(self._on_scan_time_changed)

    def _set_scanner(self, mode: str, initial: bool = False) -> None:
        """Route between the bucket-native modes (Mode 10 canvas + the 9 metric scanners). Order:
        set mode -> teardown -> hide the (dormant) time-scene items + flip the axis to bucket-index.
        Per-mode geometry is drawn by the 50ms loop via :meth:`_draw_scanner`. (Time chart removed
        in Phase B — every mode is a scanner mode now.) initial=True (launch only) pins a HARD last-24h
        window and skips the saved-drawing auto-extend, so a stranded old drawing can't slow the first load.
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
        target_dt = QtCore.QDateTime.currentDateTime().addSecs(_anchor_secs)
        if is_canvas and not initial:                   # auto-extend the window back to cover saved drawings
            floor = self._drawing_scan_floor(self.worker.tf)
            if floor is not None:
                floor_dt = QtCore.QDateTime.fromSecsSinceEpoch(int(floor))
                if floor_dt < target_dt:
                    target_dt = floor_dt
        self.menu.scan_time_edit.blockSignals(True)
        self.menu.scan_time_edit.setDateTime(target_dt)
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
        self._update_fp_pane_visibility()   # live-footprint pane rides Mode 10 only
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
        self._pivot_audio_seeded = False
        self._announced_obs = set(); self._announced_icebergs = set()
        self._title_scale = None   # force the title to re-render with this tf's ~vol next tick
        self.setWindowTitle(f"Order Flow Terminal — {config.SYMBOL} {config.TF_SECONDS.get(tf, 60) // 60}×")
        self._sig_candles = self._sig_obs = self._sig_fp = None
        self._autoranged = False
        self._scanner_needs_autofit = True    # new tf -> refit the scanner once
        self._scanner_bucket_sig = self._last_scanner_sig = None
        self._m10_cc = None   # #3 static closed-bucket compute cache (see _compute_bucket_arrays)
        self._depth_needs_calibration = True  # new tf -> re-baseline the depth slider (§1)
        self._clear_liq()                     # drop the drawn 15m labels; they re-place (by ts) on the new chart
        self._clear_structure()               # HH/HL labels re-detect on the new tf's buckets
        self._clear_choch()                   # CHoCH lines re-detect on the new tf's buckets
        self.worker.request_timeframe(tf)
        if self.scanner_mode == "bucket_canvas":        # keep THIS tf's saved drawings in view
            floor = self._drawing_scan_floor(tf)
            if floor is not None:
                floor_dt = QtCore.QDateTime.fromSecsSinceEpoch(int(floor))
                if floor_dt < self.menu.scan_time_edit.dateTime():   # only ever pull back, never forward
                    self.menu.scan_time_edit.blockSignals(True)      # the tf-switch redraw reads the new anchor
                    self.menu.scan_time_edit.setDateTime(floor_dt)
                    self.menu.scan_time_edit.blockSignals(False)

    def _drawing_scan_floor(self, tf: str):
        """Earliest saved-drawing anchor for `tf` as a Unix epoch, pulled back a tf-sized margin so
        the whole drawing (not just its right edge) lands inside the frame — or None when this tf has
        no anchored drawings. Lets the scan Zero Point auto-extend to keep persisted drawings visible
        instead of stranding them in hidden `pending` outside the default 24h window."""
        if getattr(self, "drawer", None) is None:
            return None
        ts = self.drawer.earliest_drawing_ts(tf)
        if ts is None:
            return None
        return ts - max(2 * config.TF_SECONDS.get(tf, 60), 300)

    def _save_drawings_now(self) -> None:
        """Ctrl+S — force an immediate flush of all drawings (idx-anchored + time-space) past the
        400ms debounce, with a brief on-screen confirmation. Drawings already auto-save on every
        edit; this is a manual belt-and-braces plus a visible acknowledgement."""
        ok = True
        try:
            self.drawer._save_idx()
            self.drawer._save()
        except Exception:
            ok = False
        QtWidgets.QToolTip.showText(QtGui.QCursor.pos(),
                                    "Drawings saved" if ok else "Save failed", self)

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
        self._pivot_audio_seeded = False
        self._announced_obs = set(); self._announced_icebergs = set()

    def _toggle_layer(self, key: str, on: bool) -> None:
        # Only Mode-10 overlays carry toggles now — the time-chart "Technical Layers" section was
        # removed with the time chart, so every layer key is an m10_ key -> the overlay dispatch.
        if key.startswith("m10_"):
            self._set_scanner_overlay(key, on)
        if not self._loading_ui:
            self._save_ui_state()               # persist the overlay toggle across sessions

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
        elif key in ("m10_structure", "m10_structure_swing"):
            if not on:                              # pool-managed labels -> hide now (draw-gate re-adds on ON)
                self._clear_structure()             # resets the sig -> next frame re-renders whichever set stays on
        elif key == "m10_choch":
            if not on:                              # dashed CHoCH lines -> hide now (draw-gate re-adds on ON)
                self._clear_choch()
        elif key == "m10_4hzone":
            if not on:                              # 4h wick zones -> hide now (draw-gate re-adds on ON)
                self._hide_4h_zone()
        elif key in ("m10_vpfade", "m10_estar", "m10_vpinring"):
            self._pivot_sig = None                  # VP-edge star/trap or VPIN ring toggled -> re-run the pivot draw
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
        elif key == "fp_pane":
            self._fp_want = on
            self._update_fp_pane_visibility()
        elif key == "audio":
            self.alerts.audio.set_armed(on)
        elif key == "pivot_audio":
            self._pivot_audio_on = on
            if on:
                self._pivot_audio_seeded = False    # re-seed on enable -> only NEW live fires speak, not the edge
                self._pivot_e_seeded = False; self._pivot_e_spoken = set()   # same for the "Enter E now" audio
        if not self._loading_ui:
            self._save_ui_state()               # persist the sub-widget toggle across sessions

    def _update_fp_pane_visibility(self) -> None:
        """Show the live-footprint side pane only when the user wants it AND we're in Mode 10 (bucket_canvas), the
        only mode with a per-bucket footprint. Clears the pane when hidden so it doesn't keep a stale forming candle."""
        on = bool(self._fp_want) and self.scanner_mode == "bucket_canvas"
        self.fp_panel.setVisible(on)
        if on:
            self._fp_sig = None                 # force a fresh render on (re)show
        else:
            self.fp_panel.clear_panel()

    def _apply_saved_toggles(self) -> None:
        """Restore EVERY hamburger toggle (Sub-Widgets + Mode 10 Overlays) from the saved state, so the exact
        menu the user left comes back — POC, footprint, alerts, DOM … all sticky across sessions. Overlay
        checkboxes are re-read by the draw-gate each frame (set the box; the next draw applies it); sub-widgets
        drive live widgets, so their effect is applied explicitly. _loading_ui suppresses the save each
        setChecked would otherwise trigger."""
        self._loading_ui = True
        try:
            saved = self._saved_toggles or {}
            for key, cb in self.menu.sub_checks.items():
                target = bool(saved.get(key, cb.isChecked()))
                cb.blockSignals(True); cb.setChecked(target); cb.blockSignals(False)
                try:
                    self._toggle_subwidget(key, target)     # apply the live-widget effect (not draw-gated)
                except Exception:
                    pass
            for key, cb in self.menu.layer_checks.items():
                if not cb.isEnabled():
                    continue                                 # Phase-3 placeholder — nothing to restore
                target = bool(saved.get(key, cb.isChecked()))
                cb.blockSignals(True); cb.setChecked(target); cb.blockSignals(False)   # draw-gate applies it next frame
        finally:
            self._loading_ui = False

    def _show_shortcuts(self) -> None:
        """Top-right '?' in the menu — a grouped, styled cheatsheet of every keyboard shortcut. Built once."""
        dlg = getattr(self, "_shortcuts_dlg", None)
        if dlg is None:
            groups = [
                ("Drawing &amp; selection", [
                    ("D", "Vector drawing toolbar"), ("Esc", "Cancel drawing / clear selection"),
                    ("Del / Backspace", "Delete selected drawing"),
                    ("&larr; / &rarr;", "Move selection right edge &minus;/+ 1 bucket"),
                    ("Ctrl+S", "Save drawings now")]),
                ("Mode 10 overlays", [
                    ("P", "POC dot"), ("F", "Footprint ladder"),
                    ("O", "Order Blocks + Absorption/Iceberg"), ("L", "Liquidation marks"),
                    ("V", "Abnormal-velocity diamonds"), ("W", "Candle: normal/whisker/footprint/delta/force/delta-force"),
                    ("Ctrl+P", "Pivot indicator (selection-scoped)"),
                    ("Ctrl+Z", "Selection Volume Profile"),
                    ("Ctrl+H", "Hide candles (VP / zones only)")]),
                ("Stats &amp; panels", [
                    ("S", "Stats box overlay"), ("H", "Selection stats box"),
                    ("Y", "State verdict / debug lines"),
                    ("1 2 3 4", "Absorption / Eff-Agg / E-R / Exhaustion"),
                    ("5 6 7", "Phase BEFORE / START-DURING / END"),
                    ("8", "Large + Small market orders"), ("9 / 0", "Composite / smoothed lean"),
                    ("T", "Phase table")]),
                ("Navigation", [
                    ("Double-click", "Reset / auto-fit the view"),
                    ("Ctrl+wheel", "Nudge the Scan Start (Zero Point)"),
                    ("Shift+wheel", "Zoom the X axis only"),
                    ("Ctrl+F", "Jump to a bucket Idx"), ("Ctrl+N", "New terminal window")]),
                ("Replay Mode", [
                    ("&larr; / &rarr;", "Step back / forward one candle"),
                    ("Ctrl+&rarr;", "Auto-play forward (&larr;/&rarr; or Ctrl+&rarr; stops)")]),
                ("Heatmap mode", [("G", "Greyscale toggle"), ("B", "Trade bubbles")]),
            ]
            rows = []
            for title, items in groups:
                rows.append("<tr><td colspan='2' style='padding:14px 0 4px 0; color:#8fd6ff;"
                            " font-weight:bold; font-size:13px;'>%s</td></tr>" % title)
                for key, desc in items:
                    rows.append(
                        "<tr><td style='padding:4px 14px 4px 0; white-space:nowrap;'>"
                        "<span style='background-color:#20242e; color:#e6e6e6; font-family:Consolas;"
                        " font-weight:bold;'>&nbsp;%s&nbsp;</span></td>"
                        "<td style='padding:4px 0; color:#cfd3da;'>%s</td></tr>" % (key, desc))
            html = ("<div style='color:#ffffff; font-weight:bold; font-size:16px;"
                    " padding:4px 0 8px 0;'>Keyboard Shortcuts</div>"
                    "<table style='border-collapse:collapse;'>%s</table>" % "".join(rows))
            dlg = QtWidgets.QDialog(self)
            dlg.setWindowTitle("Keyboard Shortcuts")
            dlg.setMinimumSize(430, 580)
            dlg.setStyleSheet("QDialog { background:#0c0e12; }")
            lay = QtWidgets.QVBoxLayout(dlg); lay.setContentsMargins(16, 12, 16, 12)
            view = QtWidgets.QTextBrowser()
            view.setStyleSheet("QTextBrowser { background:#0c0e12; border:none; }")
            view.setHtml(html)
            lay.addWidget(view)
            self._shortcuts_dlg = dlg
        dlg.show(); dlg.raise_(); dlg.activateWindow()

    def _on_scene_click(self, ev) -> None:
        """Double-click handling. Mode 10: per-axis follow LOCK — double-clicking the X axis
        locks X, the Y axis locks Y, the plot body locks both (snap to full follow). The
        bottom/right axis strips are distinct scene rects, so the hit-test is unambiguous.
        Every other mode keeps the reset + auto-fit (fix #10, TradingView parity)."""
        # SINGLE click on a 4h V/Z button (just above the x-axis) toggles that bucket's volume-profile / zone overlay.
        if (not ev.double() and self.scanner_mode == "bucket_canvas" and self._z4_btn_hits):
            try:
                pt = self.vb.mapSceneToView(ev.scenePos()); xc, yc = pt.x(), pt.y()
                (_zx0, _zx1), (_zy0, _zy1) = self.vb.viewRange()
                xtol = (_zx1 - _zx0) * 0.02; ytol = (_zy1 - _zy0) * 0.05
                best = None; bestd = 1e18
                for _bx, _by, _key, _kind, _on in self._z4_btn_hits:
                    if abs(xc - _bx) <= xtol and abs(yc - _by) <= ytol and abs(xc - _bx) < bestd:
                        bestd = abs(xc - _bx); best = (_key, _kind, _on)   # _key already final (rounded or 'live')
                if best is not None:
                    self._z4_user[(best[0], best[1])] = not best[2]   # flip THIS bucket's V / Z / B
                    if self._z4_last_buckets:
                        self._draw_4h_zone(self._z4_last_buckets)      # redraw the layer immediately
                    ev.accept(); return
            except Exception:
                pass
        # SINGLE click near an actual trade entry (E-held-hollow / E2) toggles its SL/+0.10%/+0.40% line overlay.
        if (not ev.double() and self.scanner_mode == "bucket_canvas" and self.show_pivot and self._pivot_entries):
            try:
                pt = self.vb.mapSceneToView(ev.scenePos()); xc, yc = pt.x(), pt.y()
                (_a, _b), (vy0, vy1) = self.vb.viewRange(); ytol = (vy1 - vy0) * 0.12
                best = None; bestdx = 2.5
                for e in self._pivot_entries:
                    key, eb, shelf = e[0], e[1], e[2]
                    dx = abs(xc - eb)
                    if dx <= bestdx and abs(yc - shelf) <= ytol:
                        best = key; bestdx = dx
                if best is not None:
                    last_key = self._entry_default_key()   # match _draw_entry_lines' default
                    self._entry_lines_user[best] = not self._entry_lines_user.get(best, best == last_key)
                    self._draw_entry_lines(); ev.accept(); return
            except Exception:
                pass
        if not ev.double():
            return
        if self.scanner_mode == "bucket_canvas" and self._abshm_hit is not None:
            try:                               # double-click inside the P1 HM band -> reset the span to 2 cycles
                pt = self.vb.mapSceneToView(ev.scenePos()); xc, yc = pt.x(), pt.y()
                _hx0, _hx1, _hyb, _hyt = self._abshm_hit
                if _hx0 <= xc <= _hx1 and _hyb <= yc <= _hyt:
                    self._abshm_ncyc = None
                    self._refresh_selection_stats(); ev.accept(); return
            except Exception:
                pass
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
            self.pivot_tooltip.hide()
            self._last_hover_pos = None      # left the plot -> stop the hover re-fire
            if self.scanner_mode == "bucket_canvas":
                self._show_forming_stats()   # keep the live candle's readout on by default
            else:
                self.stats.hide()            # metric modes are hover-only
            return
        self._last_hover_pos = pos           # park here for the live-breathe re-fire
        pt = self.vb.mapSceneToView(pos)
        self.vline.setPos(pt.x()); self.hline.setPos(pt.y()); self.hline.show()
        # A2: right-axis price tag tracks the cursor Y (all modes); PRICE_DECIMALS
        # matches PriceAxis so the badge value lines up with the axis ticks.
        self.price_tag.setText(f"{pt.y():.{config.PRICE_DECIMALS}f}")
        self.price_tag.setPos(self.vb.viewRange()[0][1], pt.y())
        self.price_tag.show()
        if getattr(self, "lower_vline", None) is not None:   # sync the SHARED vertical crosshair into the VPIN pane
            self.lower_vline.setPos(pt.x())
            self.lower_hline.hide(); self.vpin_tag.hide()     # cursor is over the price pane -> no VPIN y readout
        if self._fp_want and self.fp_panel.isVisible():       # mirror the cursor PRICE into the footprint pane
            self.fp_panel.show_price_line(pt.y())
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
        self._hover_pivot(pos)              # PIVOT label hover -> setup stats box (scene coords for hit-test)

    def _on_fp_mouse_move(self, evt) -> None:
        """Cursor over the live-footprint pane: drive its OWN crosshair (volume x-line + price y-line + right-axis
        price tag) and mirror the PRICE into the main chart's horizontal crosshair + price tag (the footprint pane
        shares the crosshair by PRICE, the way the VPIN pane shares it by time)."""
        if not self._fp_want or not self.fp_panel.isVisible():
            return
        pos = evt[0]
        if not self.fp_panel.sceneBoundingRect().contains(pos):
            self.fp_panel.hide_crosshair()
            return
        pt = self.fp_panel.getViewBox().mapSceneToView(pos)
        self.fp_panel.set_crosshair(pt.x(), pt.y())
        self.hline.setPos(pt.y()); self.hline.show()          # mirror the PRICE into the chart's horizontal crosshair
        self.price_tag.setText(f"{pt.y():.{config.PRICE_DECIMALS}f}")
        self.price_tag.setPos(self.vb.viewRange()[0][1], pt.y()); self.price_tag.show()

    def _on_lower_mouse_move(self, evt) -> None:
        """Cursor over the VPIN pane: drive its OWN crosshair (x+y lines) + a right-axis VPIN value badge, and sync
        the SHARED vertical crosshair into the main chart (same X-link, so they line up). The price pane's horizontal
        line + price tag hide, since the cursor isn't there. Mirrors the main _on_mouse_move for the lower pane."""
        if getattr(self, "lower_vb", None) is None:
            return
        pos = evt[0]
        if not self.lower_plot.sceneBoundingRect().contains(pos):
            self.vpin_tag.hide()               # left the VPIN pane -> drop its badge (the lines linger, like the main)
            return
        pt = self.lower_vb.mapSceneToView(pos)
        self.lower_vline.setPos(pt.x()); self.lower_hline.setPos(pt.y()); self.lower_hline.show()
        self.vline.setPos(pt.x())              # shared vertical crosshair -> mirror the X into the price pane
        self.hline.hide(); self.price_tag.hide()   # cursor isn't over the price pane -> no price-y readout there
        self.vpin_tag.setText(f"{pt.y():.3f}")     # VPIN is 0..1 -> 3 decimals; sits on the pane's right axis
        self.vpin_tag.setPos(self.lower_vb.viewRange()[0][1], pt.y())
        self.vpin_tag.show()

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
            _html = (
                f"<span style='color:#9aa0aa; font-weight:bold'>{ph['label']}</span>"
                f"<span style='color:#888'> &nbsp; </span>"
                f"<span style='color:rgb{ph['bcol']}; font-weight:bold'>{ph['blbl']} {bs}</span>"
                f"<span style='color:#888'> · </span>"
                f"<span style='color:rgb{ph['rcol']}; font-weight:bold'>{ph['rlbl']} {rs}</span>")
            _ex = ph.get("extra")                   # optional THIRD value (e.g. Panel-9 SUM line)
            if _ex is not None and 0 <= k < len(_ex):
                ev = _ex[k]
                es = f"{ev * 100:.0f}%" if ph["fmt"] == "pct" else self._fmt_k(ev)
                _html += (f"<span style='color:#888'> · </span>"
                          f"<span style='color:rgb{ph['ecol']}; font-weight:bold'>{ph['elbl']} {es}</span>")
            self.panel_tooltip.setHtml(_html)
            self.panel_tooltip.setPos(x, y)
            self.panel_tooltip.show()
            return
        self.panel_tooltip.hide()

    @staticmethod
    def _fmt_idx(n: int) -> str:
        """Format a bucket index with a DOT thousands separator (20000 -> '20.000', 250493 -> '250.493')."""
        return f"{n:,}".replace(",", ".")

    @staticmethod
    def _fmt_dur(seconds: float) -> str:
        """Cycle duration, whole units: < 60s -> '45s' (seconds); >= 60s -> MINUTES only, no seconds ('13m'); >= 1h
        -> '1h08m'. Seconds are dropped once past a minute so the P1/P2 (Ctrl+1/Ctrl+2) cycle labels stay compact."""
        s = int(round(seconds))
        if s < 60:
            return f"{s}s"
        if s < 3600:
            return f"{s // 60}m"
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"

    @staticmethod
    def _fmt_elapsed(seconds: float) -> str:
        """Bucket elapsed time: < 60s keeps sub-second precision ('45.3s'); >= 1min -> '1m15s'
        (minute+second); >= 1h -> '1h35' (hour+minute, no seconds)."""
        if seconds < 60:
            return f"{seconds:.1f}s"
        s = int(round(seconds))
        if s < 3600:
            return f"{s // 60}m{s % 60:02d}s"
        return f"{s // 3600}h{(s % 3600) // 60:02d}"

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
        lines = [f"<b>Idx: {self._fmt_idx(self._global_idx_offset + idx)}</b>"] + self._hover_context(self.scanner_mode, filtered, idx)
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
        lines = [f"<b>Idx: {self._fmt_idx(self._global_idx_offset + idx)}</b>"] + self._hover_context(self.scanner_mode, filtered, idx)
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

    def _toggle_whisker(self) -> None:
        """'W' — cycle the candle RENDER MODE: 0 normal candles -> 1 volume-quantile WHISKER BARS -> 2 FOOTPRINT
        CANDLES (mini centred buy/sell volume profile, like the live pane) -> 3 DELTA CANDLES (one bar per level =
        net buy-sell delta, green right / red left) -> 4 FORCE CANDLES (footprint split into the 4 forces: opL green
        + clS cyan right, opS red + clL magenta left) -> 5 DELTA-FORCE (the delta bar coloured by the level's
        DOMINANT force) -> back to 0. Ladder-less buckets and zoomed-out views (whisker <~3 px/bar, the per-level
        modes <~ FP_CANDLE_MIN_PX) fall back to normal candles. Mirrored by the hamburger 'Candle Mode' dropdown."""
        self._set_candle_mode((self._candle_mode + 1) % 6)

    def _on_candle_mode(self, m: int) -> None:
        """Hamburger 'Candle Mode' dropdown changed -> apply it ('W' and the dropdown stay in lock-step)."""
        self._set_candle_mode(int(m))

    def _set_candle_mode(self, m: int) -> None:
        """Apply a candle render mode (from 'W' or the dropdown): sync the dropdown, persist, repaint immediately."""
        self._candle_mode = int(m) % 6
        self.menu.set_candle_mode(self._candle_mode)
        self._save_ui_state()
        self._last_scanner_sig = None   # force the sig-gated scanner redraw to repaint immediately
        self._draw_scanner()

    def _toggle_hide_candles(self) -> None:
        """Ctrl+H — hide/show the candle glyphs so the volume profile / zones can be read without the candle 'noise'.
        Hides the candles, the Keltner Channel, the abnormal-order lines AND the gray POC baseline; VP, zones,
        POC dots and the other overlays stay."""
        self._hide_candles = not self._hide_candles
        self._save_ui_state()
        self._last_scanner_sig = None
        self._draw_scanner()

    def _on_vp_mode(self, m: int) -> None:
        """Hamburger 'Volume Profile Mode' dropdown changed -> re-render the selection VP + the 4h 'V' overlay."""
        self._vp_mode = int(m) % 8       # 8 VP modes (0..7)
        self._save_ui_state()
        self._sel_sig = None                         # force the Mode-10 selection VP to redraw
        if self._z4_last_buckets:                    # re-render the 4h V overlay immediately
            try:
                self._draw_4h_zone(self._z4_last_buckets)
            except Exception:
                pass
        self._last_scanner_sig = None
        self._draw_scanner()

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
                bg = _blend(rgb, ops[k] / 100.0); _top = (k == top)
                w = "bold" if _top else "normal"
                fg = "#eef1f5" if _top else "#80868f"      # non-leading (non-bold) rows: dimmer text (lower opacity)
                out.append(f"<tr><td style='background-color:{bg}; color:{fg}; font-weight:{w}'>{nm}</td>"
                           f"<td style='background-color:{bg}; color:{fg}; font-weight:{w}; text-align:right'>{ops[k]:.0f}%</td></tr>")
            out.append("</table>"); return "".join(out)
        up_html = _one(op_up, (46, 204, 113), "&#9650; UP")
        dn_html = _one(op_dn, (231, 76, 60), "&#9660; DOWN")
        return (f"<table cellspacing='0'><tr><td valign='top'>{up_html}</td>"
                f"<td>&nbsp;&nbsp;</td><td valign='top'>{dn_html}</td></tr></table>")

    def _set_spread_badge(self, key: str, bull_last: float, bear_last: float,
                          strong_is_bull: bool, x: float, y: float,
                          bull_rgb=(40, 230, 90), bear_rgb=(255, 45, 70)) -> None:
        """Place a panel's SPREAD badge: the dominant side's lead (|bull-bear|, in points), black text on the
        dominant side's fill (default NEON green bull / NEON red bear; pass bull_rgb/bear_rgb to match a panel whose
        LINES use other colours, e.g. absorption's green/purple), at the panel's right."""
        bd = self._spread_badges[key]
        spread = abs(bull_last - bear_last) * 100.0
        self._alert_sig[key] = (spread, bool(strong_is_bull))   # confluence alert: locked spread + dominant side
        bd.fill = pg.mkBrush(*bull_rgb) if strong_is_bull else pg.mkBrush(*bear_rgb)
        bd.setText(f" {spread:.0f}% ")
        bd.setPos(x, y)
        bd.show()

    # ---------------------------------------------------------------- selection confluence alert
    def _on_sel_right(self) -> None:
        """RIGHT arrow. Replay Mode: reveal the NEXT candle (advance the cursor). Otherwise: move the selection
        +1 bucket AND arm the confluence-alert eval (LEFT deliberately does not — only forward scrubbing fires)."""
        if self._replay_on:
            self._replay_stop_autoplay()    # a manual step halts Ctrl+Right auto-play
            self._advance_replay(1)
            return
        self._alert_right_pending = True
        self.drawer.extend_selection("right", 1.0)

    def _on_sel_left(self) -> None:
        """LEFT arrow. Replay Mode: step BACK one candle. Otherwise: pull the selection's right edge -1 bucket."""
        if self._replay_on:
            self._replay_stop_autoplay()    # a manual step halts Ctrl+Right auto-play
            self._advance_replay(-1)
            return
        self.drawer.extend_selection("right", -1.0)

    def _confluence_state(self):
        """(aligned, direction) for the Mode-10 selection confluence, read from the draw-time signals — so
        panels 0, 2, 6 must be toggled ON. BULL = panel-0 +50 & -50 both GREEN + eff-agg bull-dominant
        spread >=65% + START/DURING UP-dominant spread >=15%; BEAR mirrors (RED / bear / DOWN)."""
        ng, nr = self._alert_p0
        direction = "bull" if ng >= 2 else ("bear" if nr >= 2 else None)
        if direction is None:
            return False, None
        eff = self._alert_sig.get("EFF-AGG"); ph = self._alert_sig.get("START/DURING")
        if eff is None or ph is None:                        # panel 2 or 6 not drawn -> can't confirm
            return False, direction
        eff_spread, eff_bull = eff; ph_spread, ph_up = ph
        if direction == "bull":
            ok = eff_bull and eff_spread >= 65.0 and ph_up and ph_spread >= 15.0
        else:
            ok = (not eff_bull) and eff_spread >= 65.0 and (not ph_up) and ph_spread >= 15.0
        return bool(ok), direction

    def _eval_confluence_alert(self, live_follow: bool) -> None:
        """Fire once per alignment episode: on a NEW bucket close while LIVE-following, or on a RIGHT-arrow
        move. Edge-triggered — re-arms only after the confluence drops, so a held trend doesn't beep repeatedly."""
        aligned, _dir = self._confluence_state()
        tc = (self._last_snap or {}).get("total_closed")
        new_close = tc is not None and tc != self._alert_last_tc
        self._alert_last_tc = tc
        right = self._alert_right_pending
        self._alert_right_pending = False
        if not aligned:
            self._alert_armed = True                         # re-arm for the next episode
            return
        if self._alert_armed and ((live_follow and new_close) or right):
            self._alert_beep()
            self._alert_armed = False

    def _alert_beep(self) -> None:
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)   # default Windows notification sound (no file needed)
        except Exception:
            pass

    # ---------------------------------------------------------------- Ctrl+F jump-to-Idx
    _IDXJUMP_CSS = ("QLineEdit { background: #14161a; color: #eef1f5; border: 1px solid %s; "
                    "border-radius: 4px; padding: 4px 10px; font: 12px Consolas; }")

    def _idx_jump_show(self) -> None:
        """Ctrl+F: overlay input top-center of the window. Accepts a bucket Idx in any format the
        tooltip prints (dots/spaces/commas stripped); Enter centers that bucket, Escape dismisses.
        Index-space scanner modes only (the heatmap x-axis is time, not Idx)."""
        if self.scanner_mode == "depth_heatmap":
            return
        if getattr(self, "_idx_jump", None) is None:
            self._idx_jump = _IdxJumpEdit(self)
            self._idx_jump.setPlaceholderText("jump to Idx…")
            self._idx_jump.setFixedWidth(190)
            self._idx_jump.setStyleSheet(self._IDXJUMP_CSS % "#3a404a")
            self._idx_jump.returnPressed.connect(self._idx_jump_go)
            # any edit clears a previous out-of-range flash back to the neutral border
            self._idx_jump.textEdited.connect(
                lambda _t: self._idx_jump.setStyleSheet(self._IDXJUMP_CSS % "#3a404a"))
        self._idx_jump.setText("")
        self._idx_jump.setStyleSheet(self._IDXJUMP_CSS % "#3a404a")
        self._idx_jump.move((self.width() - self._idx_jump.width()) // 2, 34)
        self._idx_jump.show()
        self._idx_jump.raise_()
        self._idx_jump.setFocus()

    def _idx_jump_go(self) -> None:
        """Enter in the jump box: map the global Idx to the filtered-local x (tooltip inverse:
        local = Idx - _global_idx_offset), center X there keeping the current zoom width, refit Y
        over the buckets that become visible, and unlock view-follow exactly like a manual pan
        (double-click re-locks onto the live edge, as always)."""
        digits = "".join(ch for ch in self._idx_jump.text() if ch.isdigit())
        cache = getattr(self, "_scanner_bucket_cache", None)   # None until the first scanner draw
        if not digits or cache is None:
            self._idx_jump.setStyleSheet(self._IDXJUMP_CSS % "#ff2d46")
            return
        filtered = cache[0]; n = len(filtered)
        local = int(digits) - self._global_idx_offset
        if not (0 <= local < n):
            lo_id = self._global_idx_offset; hi_id = self._global_idx_offset + n - 1
            self._idx_jump.setStyleSheet(self._IDXJUMP_CSS % "#ff2d46")
            self._idx_jump.setText("")
            self._idx_jump.setPlaceholderText("loaded: %d … %d" % (lo_id, hi_id))
            return
        (vx0, vx1), _ = self.vb.viewRange()
        w = vx1 - vx0
        if not (1.0 <= w <= max(1.0, float(n))):
            w = float(min(FOLLOW_WINDOW, n))            # degenerate zoom -> sane default width
        self.vb.setXRange(local - w / 2.0, local + w / 2.0, padding=0)
        j0 = max(0, int(local - w / 2.0)); j1 = min(n, int(local + w / 2.0) + 1)
        lows = [float(b.get("low", 0.0)) for b in filtered[j0:j1]]
        highs = [float(b.get("high", 0.0)) for b in filtered[j0:j1]]
        if lows and highs:
            lo, hi = min(lows), max(highs)
            if not (hi > lo):
                hi = lo + 1.0
            pad = (hi - lo) * FOLLOW_PAD_FRAC
            self.vb.setYRange(lo - pad, hi + pad, padding=0)
        self._follow_x = self._follow_y = False         # stay put; double-click re-locks the follow
        self._idx_jump.setPlaceholderText("jump to Idx…")
        self._idx_jump.hide()
        self.setFocus()

    def _toggle_abs_strip(self) -> None:
        """'1' — show/hide the Mode-10 selection ABSORPTION panel (green bull / red bear absorption lines, the
        TOP panel). Flips the layer immediately; it repopulates on the next selection refresh."""
        self.show_abs_strip = not self.show_abs_strip
        if not self.show_abs_strip:
            self.bc_abs_strip.setVisible(False)
            self.panel_tooltip.hide()
        self._save_ui_state()
        self._refresh_selection_stats()

    def _toggle_exh_strip(self) -> None:
        """'4' — show/hide the Mode-10 selection exhaustion panel (bull/bear lines, the BOTTOM panel). Flips the
        layer immediately; it repopulates on the next selection refresh."""
        self.show_exh_strip = not self.show_exh_strip
        if not self.show_exh_strip:
            self.bc_exh_strip.setVisible(False); self.bc_exh_mid.setVisible(False)
            self.panel_tooltip.hide()
        self._save_ui_state()
        self._refresh_selection_stats()

    def _toggle_eff_strip(self) -> None:
        """'2' — show/hide the Mode-10 selection eff-agg evolution panel (NEON bull/bear lines below the
        exhaustion strip). Flips the layer immediately; it repopulates on the next selection refresh."""
        self.show_eff_strip = not self.show_eff_strip
        if not self.show_eff_strip:
            self.bc_eff_strip.setVisible(False)
            self.panel_tooltip.hide()
        self._save_ui_state()
        self._refresh_selection_stats()

    def _toggle_abs_hm(self) -> None:
        """Ctrl+1 — show/hide the P1 (absorption) HM sub-panel, FULLY independently of the parent absorption panel
        ('1'). It takes its own slot, so it can show with P1 hidden and vice-versa."""
        self.show_abs_hm = not self.show_abs_hm
        if not self.show_abs_hm:
            self._hide_abs_cycles(); self.panel_tooltip.hide()
        self._save_ui_state()
        self._refresh_selection_stats()

    def _toggle_eff_hm(self) -> None:
        """Ctrl+2 — show/hide the P2 (eff-agg) HM sub-panel, independently of the parent eff-agg panel ('2')."""
        self.show_eff_hm = not self.show_eff_hm
        if not self.show_eff_hm:
            self._hide_eff_hm(); self.panel_tooltip.hide()   # HM only — leave the panel's per-cycle labels
        self._save_ui_state()
        self._refresh_selection_stats()

    def _toggle_er_strip(self) -> None:
        """'3' — show/hide the Mode-10 selection effort/result panel (green buyer / red seller E/R lines below
        the eff-agg strip). Flips the layer immediately; it repopulates on the next selection refresh."""
        self.show_er_strip = not self.show_er_strip
        if not self.show_er_strip:
            self.bc_er_strip.setVisible(False)
            self.panel_tooltip.hide()
        self._save_ui_state()
        self._refresh_selection_stats()

    def _toggle_phase(self, ph: str) -> None:
        """'5'-'7' — show/hide a per-phase panel (BEFORE / START/DURING / END): UP green / DOWN red running opacity."""
        self.show_phase[ph] = not self.show_phase[ph]
        if not self.show_phase[ph]:
            self.bc_phase[ph].setVisible(False)
        self._save_ui_state()
        self._refresh_selection_stats()

    def _draw_panel_lock(self, item, lock_dist: int, lo: int, hi: int, ybot: float, ytop: float) -> None:
        """Vertical light-gray dashed LOCK-IN divider at ``lock_dist`` buckets left of the panel's right edge:
        left of it the value is fully formed (locked), right of it still settling. Hidden when no locked
        region exists (selection narrower than the lock distance)."""
        x = hi - lock_dist + 0.5
        if x > lo - 0.5:
            item.setData([x, x], [ybot, ytop]); item.setVisible(True)
        else:
            item.setData([], []); item.setVisible(False)

    def _draw_panel_refs(self, mid_item, q_item, lo, hi, ybot, ytop) -> None:
        """Panels 1/2/3 reference lines: a 50% light-gray midline (``mid_item``) + 25%/75% orange quarter lines
        (``q_item``, two segments via a NaN gap), positioned at band height (so they read the same on the
        zoomed E/R panel too)."""
        _h = ytop - ybot
        _m = ybot + 0.5 * _h
        mid_item.setData([lo - 0.5, hi + 0.5], [_m, _m]); mid_item.setVisible(True)
        _q1 = ybot + 0.25 * _h; _q3 = ybot + 0.75 * _h
        q_item.setData([lo - 0.5, hi + 0.5, float("nan"), lo - 0.5, hi + 0.5],
                       [_q1, _q1, float("nan"), _q3, _q3]); q_item.setVisible(True)

    def _draw_lean_lines(self, bull, bear, items, bkeys, lo, hi, ytop, ybot,
                         hover_label, badge_x, sum_badge_x, show_lock=True, sum_only=False,
                         clip_lock=False, tail_item=None, cross_item=None) -> None:
        """Render a Panel-9-style set into the band [ybot,ytop]: green/grey BULL + red/grey BEAR sign-split
        lines, a neon-blue SUM (bull+bear), dashed zero + orange +/-50% refs, the lock-in divider, three
        curr-(locked) badges and the hover. ``items`` = the panel's _bc_*_items tuple (Panel-9 field order);
        ``bkeys`` = (bull_badge, bear_badge, sum_badge) keys into ``self._spread_badges``. Used by panels 9 + 0."""
        zero, gold_hi, gold_lo, lock, bull_x, bear_x, bull_g, bear_r, ssum = items
        bk, rk, sk = bkeys
        sum_line = bull + bear
        ex = np.arange(lo, hi + 1, dtype=float)
        mid = (ytop + ybot) / 2.0; half = (ytop - ybot) / 2.0 * 0.92
        _R = float(config.PANEL9_SCALE)

        def _y(v):
            return mid + np.clip(np.asarray(v, dtype=float) / _R, -1.0, 1.0) * half
        zero.setData([lo - 0.5, hi + 0.5], [mid, mid])
        gold_hi.setData([lo - 0.5, hi + 0.5], [float(_y(50.0))] * 2)
        gold_lo.setData([lo - 0.5, hi + 0.5], [float(_y(-50.0))] * 2)
        if show_lock:
            _lx = hi - (config.LIVE_PANEL_WINDOW // 2) + 0.5
            lock.setData([_lx, _lx], [ybot, ytop]) if _lx > lo - 0.5 else lock.setData([], [])
        else:
            lock.setData([], [])
        if not sum_only:                                                     # bull/bear sign-split lines
            bx, b_pos, b_neg = _split_curve_by_sign(ex, bull)
            bull_g.setData(bx, _y(b_pos)); bull_x.setData(bx, _y(b_neg))
            rx, r_pos, r_neg = _split_curve_by_sign(ex, bear)
            bear_r.setData(rx, _y(r_neg)); bear_x.setData(rx, _y(r_pos))
        _lk = config.LIVE_PANEL_WINDOW // 2
        if clip_lock and len(sum_line) > _lk:        # PANEL 0: LOCKED region solid; settling tail as a grey dash
            _sd = np.array(sum_line, dtype=float); _sd[-_lk:] = np.nan
            ssum.setData(ex, _y(_sd))
            if tail_item is not None:                # grey dashed continuation over the last lk+1 buckets (overlaps the join)
                tail_item.setData(ex[-(_lk + 1):], _y(sum_line[-(_lk + 1):])); tail_item.setVisible(True)
        else:
            ssum.setData(ex, _y(sum_line))
            if tail_item is not None:
                tail_item.setData([], []); tail_item.setVisible(False)
        if cross_item is not None:                   # PANEL 0: X markers at the last confirmed +50/0/-50 crosses
            self._draw_level_crosses(cross_item, sum_line, ex, _y, _lk)
        for _it in items:
            _it.setVisible(True)
        if not show_lock:
            lock.setVisible(False)
        if sum_only:                                                         # PANEL 0: keep ONLY the blue sum line
            for _it in (bull_g, bull_x, bear_r, bear_x):
                _it.setVisible(False)
        _vs = float(sum_line[-(_lk + 1)]) if (clip_lock and len(sum_line) > _lk) else float(sum_line[-1])
        if not sum_only:
            _vb, _vr = float(bull[-1]), float(bear[-1])
            _bdb = self._spread_badges[bk]
            _bdb.fill = pg.mkBrush(40, 230, 90) if _vb >= 0 else pg.mkBrush(140, 140, 140)
            _bdb.setText(f" {_vb:+.1f}% ")
            _bdb.setPos(badge_x, mid + half * 0.45); _bdb.show()
            _bdr = self._spread_badges[rk]
            _bdr.fill = pg.mkBrush(255, 45, 70) if _vr <= 0 else pg.mkBrush(140, 140, 140)
            _bdr.setText(f" {_vr:+.1f}% ")
            _bdr.setPos(badge_x, mid - half * 0.45); _bdr.show()
        else:
            self._spread_badges[bk].hide(); self._spread_badges[rk].hide()
        _bds = self._spread_badges[sk]
        _bds.fill = pg.mkBrush(40, 230, 90) if _vs >= 0 else pg.mkBrush(255, 45, 70)   # green/red by sign (line stays blue)
        _bds.setText(f" {_vs:+.1f}% ")
        _bds.setPos(badge_x if sum_only else sum_badge_x, mid); _bds.show()
        self._panel_hovers.append({
            "label": hover_label, "lo": lo, "yb": ybot, "yt": ytop,
            "bull": [v / 100.0 for v in bull], "bear": [v / 100.0 for v in bear],
            "extra": [v / 100.0 for v in sum_line], "ecol": (45, 156, 255), "elbl": "SUM",
            "bcol": (40, 230, 90), "rcol": (255, 45, 70), "blbl": "BULL", "rlbl": "BEAR", "fmt": "pct"})

    def _draw_level_crosses(self, item, vals, ex, yfn, lk) -> None:
        """Panel-0 'X' markers at the LAST CONFIRMED cross of each reference level. LOCKED region
        (vals[:n-lk]): solid full-size X — these are final and feed the confluence alert. SETTLING region
        (the last lk buckets): the same detection drawn as a SMALL FILLED DOT (full opacity) — provisional,
        may still move/vanish as the smoothing tail firms up; NEVER counted by the alert. ALL levels (incl. the 0 line): up-cross GREEN,
        down-cross RED. Confirmed = the line holds the new side >= 2 buckets (or all buckets that
        exist yet, at the live edge). One most-recent cross per level per region."""
        n = len(vals); end = n - lk                       # locked region = vals[:end]
        if end < 2:
            item.setData(spots=[]); item.setVisible(False); self._alert_p0 = (0, 0); return
        _G, _R = (40, 230, 90), (255, 45, 70)          # 0-line now green/red too (no more white)

        def _last_cross(lo_k, hi_k, L, up_c, dn_c, confirm_end):
            last = None
            for k in range(lo_k, hi_k):
                a = float(vals[k - 1]) - L; b = float(vals[k]) - L
                if a == 0.0 or b == 0.0 or (a < 0) == (b < 0):
                    continue                              # no sign change across L
                newpos = b > 0                            # crossed to ABOVE L (upward)
                if all((float(vals[j]) - L > 0) == newpos for j in range(k, min(confirm_end, k + 2))):
                    frac = a / (a - b)                    # interpolate the crossing x
                    last = (ex[k - 1] + frac * (ex[k] - ex[k - 1]), float(yfn(L)), up_c if newpos else dn_c)
            return last

        spots = []; _cols = []                            # _cols: LOCKED ±50 cross colours (alert semantics
        for (L, up_c, dn_c) in ((50.0, _G, _R), (0.0, _G, _R), (-50.0, _G, _R)):   # unchanged: 0-line excluded)
            last = _last_cross(1, end, L, up_c, dn_c, end)
            if last is not None:
                spots.append({"pos": (last[0], last[1]), "pen": pg.mkPen(last[2], width=1.3),
                              "brush": pg.mkBrush(0, 0, 0, 0), "symbol": "x", "size": 11})
                if L != 0.0:                              # the alert keeps counting ±50 only, as always
                    _cols.append(last[2])
            prov = _last_cross(max(1, end), n, L, up_c, dn_c, n)   # SETTLING tail: small filled DOT
            if prov is not None:
                spots.append({"pos": (prov[0], prov[1]),
                              "pen": pg.mkPen(prov[2], width=1.0),
                              "brush": pg.mkBrush(*prov[2]), "symbol": "o", "size": 6})
        self._alert_p0 = (_cols.count(_G), _cols.count(_R))   # LOCKED only — the alert never sees settling crosses
        item.setData(spots=spots); item.setVisible(bool(spots))

    def _toggle_largesmall(self) -> None:
        """'8' — CYCLE the market-order panels: hidden (0) -> LARGE only (1) -> LARGE + SMALL (2) -> hidden ->
        ... The next selection refresh shows/clears each panel from the derived lg_on/sm_on."""
        self._ls_mode = (self._ls_mode + 1) % 3
        if self._ls_mode == 0:
            self.panel_tooltip.hide()
        self._save_ui_state()
        self._refresh_selection_stats()

    def _clear_lg_panel(self) -> None:
        """LARGE panel tear-down: hide its items + badges (reused via setData -> no leak)."""
        for _it in (self.bc_lg_strip, self.bc_lg_mid, self.bc_lg_q, self.bc_lg_lock,
                    self.bc_lg_pos, self.bc_lg_neg, self.bc_lg_bars, self.bc_lg_tot):
            _it.setVisible(False)
        self._spread_badges["LARGE MKT"].hide()

    def _clear_sm_panel(self) -> None:
        """SMALL panel tear-down."""
        for _it in (self.bc_sm_strip, self.bc_sm_mid, self.bc_sm_q, self.bc_sm_lock,
                    self.bc_sm_pos, self.bc_sm_neg, self.bc_sm_bars, self.bc_sm_tot):
            _it.setVisible(False)
        self._spread_badges["SMALL MKT"].hide()

    def _clear_largesmall_panels(self) -> None:
        """Hide BOTH market-order panels (slot 8)."""
        self._clear_lg_panel(); self._clear_sm_panel()

    def _largesmall_thr_sig(self):
        """Change-detection component so the panels recompute as the daemon's adaptive size_thr drifts — but
        ONLY while at least one is visible (otherwise None, so they add no per-frame churn)."""
        if self._ls_mode == 0:
            return None
        st = (self._last_snap or {}).get("size_thr") or []
        return (round(st[0], 2), round(st[2], 2)) if len(st) > 2 else "cold"

    def _largesmall_thresholds(self):
        """The active LARGE / SMALL cutoffs (contracts), FULLY AUTOMATIC from the daemon's rolling percentile:
        large = p95, small = p50 (the broad 60-min distribution, not selection-local). Falls back to the
        cold-start config defaults only until size_thr warms."""
        st = (self._last_snap or {}).get("size_thr") or []
        large_thr = st[2] if len(st) > 2 else config.SIZE_DEFAULT_LARGE
        small_thr = st[0] if len(st) > 0 else config.SIZE_DEFAULT_SMALL
        return large_thr, small_thr

    def _draw_hist_panel(self, bars, tot_item, hide_items, buy_series, sell_series, lo, hi, ybot, ytop,
                         label, rgb_pair, pre0, badge_x) -> None:
        """Per-bucket DOMINANCE HISTOGRAM into [ybot,ytop]: one bar per bucket, total height = (buy+sell) large
        activity (LARGE = contracts, SMALL = trade count), auto-scaled to the selection's busiest bucket. The
        LOSER side is the bottom segment in a MUTED tint; the WINNER (larger) is stacked on top in FULL colour
        — so the TOP colour tells you who dominated and the full-vs-muted split shows by how much. FACTUAL per
        bucket (no window, no lock line). Hover -> BUY / SELL / NET; badge -> the live-edge winner."""
        buy_v = list(buy_series[pre0:]); sell_v = list(sell_series[pre0:])
        H = (ytop - ybot) * 0.92                                # headroom so the tallest bar doesn't touch the top
        buy_rgb, sell_rgb = rgb_pair[0], rgb_pair[1]
        _GRAY = (140, 140, 140, 205)                            # muted loser segment (gray, not a side tint)
        # MERGE consecutive buckets that share the same dominant side into ONE wider bar (sustained runs read
        # as a single block). A balanced/empty bucket (buy == sell, incl. no large trades) is a neutral gap:
        # it draws nothing and breaks the run.
        n = len(buy_v)
        runs = []                                              # (start_i, end_i, dom +1 buy / -1 sell, win_vol, lose_vol)
        i = 0
        while i < n:
            d = 1 if buy_v[i] > sell_v[i] else (-1 if sell_v[i] > buy_v[i] else 0)
            if d == 0:
                i += 1; continue
            j = i; wv = lv = 0.0
            while j < n:
                b, s = buy_v[j], sell_v[j]
                dj = 1 if b > s else (-1 if s > b else 0)
                if dj != d:
                    break
                wv += (b if d == 1 else s); lv += (s if d == 1 else b)
                j += 1
            runs.append((i, j - 1, d, wv, lv)); i = j
        scale = max(max((wv + lv for (_a, _z, _d, wv, lv) in runs), default=0.0), 1e-9)
        x0s, x1s, lo_h, hi_h, lo_rgba, hi_rgba = [], [], [], [], [], []
        for (a, z, d, wv, lv) in runs:
            x0s.append((lo + a) - 0.4); x1s.append((lo + z) + 0.4)   # span the merged buckets, 0.1 gap to neighbours
            lo_h.append((lv / scale) * H); hi_h.append((wv / scale) * H)
            hi_rgba.append((*buy_rgb, 240) if d == 1 else (*sell_rgb, 240)); lo_rgba.append(_GRAY)
        bars.update_bars(x0s, x1s, ybot, lo_h, hi_h, lo_rgba, hi_rgba, lo - 0.5, hi + 0.5, ybot, ytop)
        bars.setVisible(True)
        for _it in hide_items:                                  # retire the line/strip render of these panels
            _it.setVisible(False)
        # Hover shows the MERGED bar's SUM: every bucket in a run carries that run's summed buy/sell, so
        # hovering anywhere on a merged bar reads the whole block's totals (a single-bucket bar = its own
        # value). Neutral gap buckets keep their own (balanced) value.
        hov_buy = list(buy_v); hov_sell = list(sell_v)
        for (a, z, d, wv, lv) in runs:
            bsum = wv if d == 1 else lv; ssum = lv if d == 1 else wv
            for k in range(a, z + 1):
                hov_buy[k] = bsum; hov_sell[k] = ssum
        self._panel_hovers.append({                            # hover -> the bar's BUY / SELL / NET (run sum)
            "label": label, "lo": lo, "yb": ybot, "yt": ytop,
            "bull": hov_buy, "bear": hov_sell, "bcol": buy_rgb, "rcol": sell_rgb,
            "blbl": "BUY", "rlbl": "SELL", "fmt": "vol",
            "extra": [b - s for b, s in zip(hov_buy, hov_sell)], "ecol": (200, 200, 200), "elbl": "NET"})
        _cy = (ytop + ybot) / 2.0
        bd = self._spread_badges[label]                        # top badge = the latest bar's (run's) winner total
        if runs:
            _d, _wv = runs[-1][2], runs[-1][3]
            bd.fill = pg.mkBrush(*buy_rgb) if _d == 1 else pg.mkBrush(*sell_rgb)
            bd.setText(f" {self._fmt_k(_wv)} ")
            bd.setPos(badge_x, _cy); bd.show()
        else:
            bd.hide()
        # SECOND label below it — SELECTION TOTAL buy vs sell (sum over the whole selection): dominant side
        # bolded, fill = the dominant side's colour. (LARGE = contracts, SMALL = trade count.)
        _bt = sum(buy_v); _st = sum(sell_v); _buy_dom = _bt >= _st
        _bp = f"<b>{self._fmt_k(_bt)}</b>" if _buy_dom else self._fmt_k(_bt)
        _sp = self._fmt_k(_st) if _buy_dom else f"<b>{self._fmt_k(_st)}</b>"
        tot_item.fill = pg.mkBrush(*(buy_rgb if _buy_dom else sell_rgb))
        tot_item.setHtml(f"<span style='color:#000000'>&nbsp;B: {_bp} | S: {_sp}&nbsp;</span>")
        tot_item.setPos(badge_x, _cy - (ytop - ybot) * 0.32); tot_item.show()

    def _toggle_panel9(self) -> None:
        """'9' — show/hide the bull/bear-trend lean panel (lean +/- own-side exhaustion). OFF clears + hides it."""
        self.show_panel9 = not self.show_panel9
        if not self.show_panel9:
            self._clear_panel9()
        self._save_ui_state()
        self._refresh_selection_stats()

    def _clear_panel9(self) -> None:
        """Panel-9 tear-down: wipe + hide both lines (colored + grey halves) + refs + both badges (setData -> no leak)."""
        for _it in self._bc_p9_items:
            _it.setData([], []); _it.setVisible(False)
        self._spread_badges["PANEL9_BULL"].hide()
        self._spread_badges["PANEL9_BEAR"].hide()
        self._spread_badges["PANEL9_SUM"].hide()

    def _toggle_panel0(self) -> None:
        """'0' — show/hide Panel 0 (the smoothed twin of Panel 9: each line = (current+locked)/2). OFF clears it."""
        self.show_panel0 = not self.show_panel0
        if not self.show_panel0:
            self._clear_panel0()
        self._save_ui_state()
        self._refresh_selection_stats()

    def _clear_panel0(self) -> None:
        """Panel-0 tear-down: wipe + hide all lines/refs + the grey tail + the three badges (setData -> no leak)."""
        for _it in self._bc_p0_items:
            _it.setData([], []); _it.setVisible(False)
        self.bc_p0_sum_tail.setData([], []); self.bc_p0_sum_tail.setVisible(False)
        self.bc_p0_cross.setData(spots=[]); self.bc_p0_cross.setVisible(False)
        self._spread_badges["PANEL0_BULL"].hide()
        self._spread_badges["PANEL0_BEAR"].hide()
        self._spread_badges["PANEL0_SUM"].hide()

    # ---------------------------------------------------------------- liquidity-sweep labels (Ctrl+L)
    # The chart ALWAYS shows the 15m sweeps (they grade far cleaner than 1m) — on EVERY timeframe. Each event
    # is placed by TIMESTAMP onto whatever bar it happened on (a 15m sweep pins to the 1m/5m/etc. bar covering
    # its close), so it lands correctly regardless of the chart's own bucket Idx. LIVE sweeps arrive from the
    # daemon (broadcast_all, tf-agnostic) via _merge_liq_sweeps; the offline CSV is a historical fallback.
    def _load_liq_csv(self) -> None:
        """Load the 15m Tier-A sweep set (historical fallback) into gid- AND ts-sorted parallel lists. LIVE
        sweeps now arrive from the DAEMON (_merge_liq_sweeps); this offline set just seeds history for when the
        daemon's set doesn't reach back far enough (or it's disconnected). ts drives DRAW placement (any chart);
        gid is the 15m Idx, used only for (gid,side) dedup against the daemon feed."""
        import csv as _csv
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "study", "out", "liq_sweeps_15m.csv")
        evs = []
        try:
            with open(path, encoding="utf-8") as f:
                first = f.tell(); ln = f.readline()
                if not ln.startswith("#"):
                    f.seek(first)
                for r in _csv.DictReader(f):
                    if r["tier"] != "A":                    # TIER-A ONLY — Tier-B are calibration decoys
                        continue
                    evs.append(dict(gid=int(r["bucket_id"]), ts=float(r["ts"]), side=r["side_label"],
                                    kind="Sweep", level=float(r["swept_level"]), tier="A"))
        except (OSError, KeyError, ValueError):
            evs = []
        evs.sort(key=lambda e: e["gid"])
        self._liq_events = evs
        self._liq_gids = [e["gid"] for e in evs]
        self._liq_ts = [e["ts"] for e in evs]
        self._liq_seen = {(e["gid"], e["side"]) for e in evs}

    def _merge_liq_sweeps(self, sweeps) -> None:
        """Merge the daemon-pushed 15m sweeps (snapshot['liq_sweeps']) into the layer, deduped by (idx, side)
        against the offline set and each other. Cheap: dozens of tiny dicts, and a re-sort only when a genuinely
        new sweep arrives — which then forces one redraw so it appears the instant the 15m bar closes."""
        if not sweeps:
            return
        added = False
        for s in sweeps:
            gid = int(s.get("idx", 0)); side = s.get("side")
            key = (gid, side)
            if key in self._liq_seen:
                continue
            self._liq_seen.add(key)
            self._liq_events.append(dict(gid=gid, ts=float(s.get("ts", 0.0)), side=side, kind="Sweep",
                                         level=float(s.get("level", 0.0)), tier="A"))
            added = True
        if added:
            self._liq_events.sort(key=lambda e: e["gid"])
            self._liq_gids = [e["gid"] for e in self._liq_events]
            self._liq_ts = [e["ts"] for e in self._liq_events]
            self._last_scanner_sig = None                   # a new live sweep -> repaint now

    def _toggle_liq(self) -> None:
        """Ctrl+L — show/hide the liquidity-sweep labels (detector v1, uncalibrated)."""
        self.show_liq = not self.show_liq
        if not self.show_liq:
            self._clear_liq()
        self._save_ui_state()
        self._last_scanner_sig = None                # force a re-render so labels appear/disappear now
        self._draw_scanner()

    def _free_liq_pool(self) -> None:
        """Remove the pooled label TextItems from the scene (not just hide) so the item count returns to
        baseline when labels are off / out of view — the scene must not carry a dormant pool."""
        if self._liq_label_pool:
            for _it in self._liq_label_pool:
                self.plot.removeItem(_it)
            self._liq_label_pool = []

    def _liq_hide_marks(self) -> None:
        self._free_liq_pool()
        self.bc_liq_leader.setData([], []); self.bc_liq_leader.setVisible(False)

    def _clear_liq(self) -> None:
        self._liq_hide_marks(); self._liq_note(None)

    def _liq_note(self, text, vx0=None, vy1=None) -> None:
        """Corner status line (top-left of the view). Shown when labels are on but nothing is drawable in
        view — so an empty screen reads as 'empty-by-location', not 'broken'."""
        if not text:
            if self._liq_status_txt is not None:
                self._liq_status.setVisible(False); self._liq_status_txt = None
            return
        if text != self._liq_status_txt:
            self._liq_status.setText(text); self._liq_status_txt = text
        if vx0 is not None:
            self._liq_status.setPos(vx0, vy1)
        self._liq_status.setVisible(True)

    def _liq_empty_msg(self, t_lo: float, t_hi: float):
        """Nothing in view -> name the nearest 15m sweep by time (Idx is per-15m, useless for Ctrl+F on a 1m
        chart, so we give the clock instead)."""
        import bisect as _bi, time as _t
        ts = self._liq_ts
        if not ts:
            return None
        i = _bi.bisect_left(ts, t_lo)
        cand = ([ts[i]] if i < len(ts) else []) + ([ts[i - 1]] if i > 0 else [])
        if not cand:
            return "%d 15m sweeps loaded" % len(ts)
        nearest = min(cand, key=lambda c: min(abs(c - t_lo), abs(c - t_hi)))
        rel = "back" if nearest < t_lo else "ahead"
        return "%d 15m sweeps loaded — none in view; nearest %s UTC (%s)" % (
            len(ts), _t.strftime("%m-%d %H:%M", _t.gmtime(nearest)), rel)

    def _draw_liq(self, buckets, x, vx0, vx1, vy0, vy1) -> None:
        """15m sweep labels on the ACTIVE chart, placed by TIMESTAMP (so 15m sweeps land on the 1m bar they
        happened on). Cull the ts-sorted set to the visible time window, cap at LIQ_MAX_LABELS (even spread),
        reuse a bounded pool. Each label sits at the SWEPT PRICE level of that sweep. On/off is purely
        self.show_liq. Detection is NOT done here (offline CSV + daemon feed populate the set)."""
        import bisect as _bi
        if not self.show_liq or not buckets or not self._liq_ts:
            self._clear_liq(); return
        n = len(buckets)
        lo_local = max(0, int(vx0)); hi_local = min(n - 1, int(vx1) + 1)
        bt = [float(b.get("start_time", 0.0)) for b in buckets]            # bar START times, ascending
        t_lo = bt[lo_local]; t_hi = float(buckets[hi_local].get("end_time", bt[hi_local]))
        a = _bi.bisect_left(self._liq_ts, t_lo); b = _bi.bisect_right(self._liq_ts, t_hi)
        vis = self._liq_events[a:b]
        if not vis:                                          # nothing in this time window -> nearest by clock
            self._liq_hide_marks(); self._liq_note(self._liq_empty_msg(t_lo, t_hi), vx0, vy1)
            return
        self._liq_note(None)
        if len(vis) > LIQ_MAX_LABELS:                        # rarely hit (15m sweeps are sparse) -> even spread
            step = max(1, len(vis) // LIQ_MAX_LABELS)
            vis = vis[::step][:LIQ_MAX_LABELS]
        dy = (vy1 - vy0) * 0.035
        lx, ly = [], []; used = 0
        for ev in vis:
            li = _bi.bisect_right(bt, ev["ts"]) - 1          # the bar covering this sweep's time
            if li < 0:
                li = 0
            elif li >= n:
                li = n - 1
            up = ev["side"] == "S"
            lvl = ev["level"]                                 # anchor at the SWEPT PRICE (correct cross-tf)
            lab_y = lvl + dy if up else lvl - dy
            lx += [x[li], x[li], float("nan")]; ly += [lvl, lab_y, float("nan")]
            if used >= len(self._liq_label_pool):            # grow lazily, but bounded by LIQ_MAX_LABELS
                _t = pg.TextItem(anchor=(0.5, 0.5), color=(0, 0, 0))
                _t.setZValue(31)
                _bf = QtGui.QFont("Consolas", 9); _bf.setBold(True); _t.textItem.setFont(_bf)
                _t.setToolTip("15m detector v1 — uncalibrated")
                self.plot.addItem(_t, ignoreBounds=True); self._liq_label_pool.append(_t)
            _lab = self._liq_label_pool[used]; used += 1
            _lab.fill = pg.mkBrush(255, 45, 70) if up else pg.mkBrush(40, 230, 90)   # S red / B green
            _lab.setText(" %s 15m Sweep " % ev["side"])
            _lab.setPos(x[li], lab_y); _lab.setVisible(True)
        for _j in range(used, len(self._liq_label_pool)):
            self._liq_label_pool[_j].setVisible(False)
        self.bc_liq_leader.setData(lx, ly, connect="finite"); self.bc_liq_leader.setVisible(bool(lx))

    def _on_swing_sensitivity(self, pct: float) -> None:
        """Hamburger swing-ZigZag slider moved — set the threshold %, re-detect the swing structure at it, persist,
        and repaint now (the threshold is in _struct_sig, so this forces a fresh detection)."""
        self._swing_pct = float(pct)
        self._struct_sig = None; self._struct_rsig = None
        self._save_ui_state()
        self._last_scanner_sig = None
        self._draw_scanner()

    def _on_kc_scale(self, scale: float) -> None:
        """Hamburger Keltner-scale slider moved — set the smooth-approx effective-TF scale (KC EMA/ATR period ×scale,
        band ×sqrt(scale); POC-baseline EMA period ×scale). KC + baseline live in the #3 closed-bucket cache, so
        drop it to force a clean recompute at the new scale, then persist + repaint. 1.0 = native (unchanged)."""
        self._kc_scale = max(1.0, min(float(config.KELTNER_SCALE_MAX), float(scale)))
        self._m10_cc = None                    # cached KC + baseline rows are scale-dependent -> rebuild
        self._save_ui_state()
        self._last_scanner_sig = None
        self._draw_scanner()

    def _clear_structure(self) -> None:
        for _l in self._struct_label_pool:
            _l.setVisible(False)
        for _l in self._struct_label_pool_sw:
            _l.setVisible(False)
        for _l in self._struct_pct_pool_sw:
            _l.setVisible(False)
        self._struct_sig = None; self._struct_rsig = None

    def _draw_structure(self, buckets, x, vx0, vx1, vy0, vy1) -> None:
        """Market-structure labels (HH/HL/LH/LL) at each ZigZag swing on the Mode-10 canvas. TWO independent
        hamburger toggles, no keyboard shortcut: m10_structure = FINE ZigZag (ZIGZAG_PCT, scalping, small
        green/red); m10_structure_swing = COARSE ZigZag (ZIGZAG_SWING_PCT, swings, large gold/magenta). Highs
        label above the wick, lows below. DETECTION is cached on the bucket-set signature (recomputes ONLY on a
        new close / cap-roll — never on pan/zoom, and swings settle on CLOSE so no mid-bar flicker); RENDER is
        skipped unless the visible swing SLICE or the y-scale changed, since labels live in data coords and
        auto-follow pan/zoom. Cull is a bisect on the sorted swing indices, not an O(all) scan."""
        scalp = self.menu.layer_state("m10_structure")
        swing = self.menu.layer_state("m10_structure_swing")
        if (not scalp and not swing) or not buckets:
            self._clear_structure(); return
        n = len(buckets)
        # DETECTION key changes only when the data/frame changes: total_closed (increments on close), n (cap-roll),
        # the forming bucket's start_time (new each close), the first bucket's start_time (front rolls off), + the
        # scalp/swing on-state. Panning/zooming leaves all of these fixed, so it never re-detects.
        tc = int((self._last_snap or {}).get("total_closed", 0))
        sig = (n, tc, float(buckets[-1].get("start_time", 0.0)), float(buckets[0].get("start_time", 0.0)),
               scalp, swing, self._swing_pct)   # swing threshold in the key -> a slider drag re-detects
        if sig != self._struct_sig:
            H = [float(b.get("high", 0.0)) for b in buckets]; L = [float(b.get("low", 0.0)) for b in buckets]
            self._struct_labels = structure.detect_structure_zigzag(H, L) if scalp else []
            self._struct_labels_sw = (structure.detect_structure_zigzag(H, L, self._swing_pct / 100.0)
                                      if swing else [])
            self._struct_idx = [s[0] for s in self._struct_labels]           # sorted bar-index -> bisect cull
            self._struct_idx_sw = [s[0] for s in self._struct_labels_sw]
            self._struct_pct_sw = self._swing_pcts(self._struct_labels_sw)   # % move of each swing from the previous one
            self._struct_sig = sig
        lo_i = max(0, int(vx0) - 1); hi_i = min(n - 1, int(vx1) + 1)
        a = bisect.bisect_left(self._struct_idx, lo_i); b = bisect.bisect_right(self._struct_idx, hi_i)
        asw = bisect.bisect_left(self._struct_idx_sw, lo_i); bsw = bisect.bisect_right(self._struct_idx_sw, hi_i)
        rsig = (a, b, asw, bsw, self._struct_sig, round(vy1 - vy0, 5))        # RENDER-skip: slice + y-scale
        if rsig == self._struct_rsig:
            return                                                            # nothing visible changed -> auto-follows
        self._struct_rsig = rsig
        self._render_struct(self._struct_labels[a:b], self._struct_label_pool, x, vy0, vy1, swing=False)
        self._render_struct(self._struct_labels_sw[asw:bsw], self._struct_label_pool_sw, x, vy0, vy1, swing=True,
                            pcts=self._struct_pct_sw[asw:bsw])

    @staticmethod
    def _swing_pcts(swings) -> list:
        """Per-swing % price move FROM THE PREVIOUS swing (the leg): (price - prev) / prev * 100. First swing -> None
        (no prior leg). Parallel to ``swings`` so it culls with the same slice."""
        out = []; prev = None
        for s in swings:
            price = float(s[1])
            out.append(((price - prev) / prev * 100.0) if (prev not in (None, 0.0)) else None)
            prev = price
        return out

    def _render_struct(self, vis, pool, x, vy0, vy1, swing, pcts=None) -> None:
        """Paint the already-culled swing slice into its own label pool. swing=True -> coarse (large gold/magenta,
        further off the wick); swing=False -> scalp (small green/red). SWING labels also get a smaller % sub-label
        (the leg's move from the previous swing) just below each HH/HL/LH/LL, from _struct_pct_pool_sw. Cap 120."""
        if len(vis) > 120:                                       # keep only the most RECENT on a zoomed-out view
            if pcts is not None:
                pcts = pcts[-120:]
            vis = vis[-120:]
        dy = (vy1 - vy0) * (0.05 if swing else 0.03)
        used = 0
        for k, (i, price, lab, is_high) in enumerate(vis):
            if swing:
                col = (255, 205, 50) if lab in ("HH", "HL") else (235, 90, 200)   # bullish gold / bearish magenta
            else:
                col = (40, 230, 90) if lab in ("HH", "HL") else (255, 45, 70)     # bullish green / bearish red
            y = price + dy if is_high else price - dy
            if used >= len(pool):
                _t = pg.TextItem(anchor=(0.5, 0.5)); _t.setZValue(29 if swing else 30)
                _bf = QtGui.QFont("Consolas", 11 if swing else 8); _bf.setBold(True); _t.textItem.setFont(_bf)
                self.plot.addItem(_t, ignoreBounds=True); pool.append(_t)
            _lab = pool[used]
            _lab.setColor(col); _lab.setText(lab); _lab.setPos(x[i], y); _lab.setVisible(True)
            # SWING only: the leg % move, one line below the HH/HL/LH/LL (same colour, smaller font)
            if swing and pcts is not None:
                ppool = self._struct_pct_pool_sw
                if used >= len(ppool):
                    _pt = pg.TextItem(anchor=(0.5, 0.5)); _pt.setZValue(29)
                    _pf = QtGui.QFont("Consolas", 8); _pf.setBold(True); _pt.textItem.setFont(_pf)
                    self.plot.addItem(_pt, ignoreBounds=True); ppool.append(_pt)
                _pt = ppool[used]
                pc = pcts[k] if k < len(pcts) else None
                if pc is None:
                    _pt.setVisible(False)
                else:
                    _pt.setColor(col); _pt.setText("%+.2f%%" % pc)
                    _pt.setPos(x[i], y - dy * 0.5); _pt.setVisible(True)   # just below the label
            used += 1
        for _j in range(used, len(pool)):
            pool[_j].setVisible(False)
        if swing:                                                # hide any leftover % sub-labels
            for _j in range(used, len(self._struct_pct_pool_sw)):
                self._struct_pct_pool_sw[_j].setVisible(False)

    def _clear_choch(self) -> None:
        if self._choch_added:
            self._choch_bull.setVisible(False); self._choch_bear.setVisible(False)
        self._choch_sig = None; self._choch_rsig = None

    def _draw_choch(self, buckets, x, vx0, vx1) -> None:
        """Change-of-Character: a dashed line from each broken SCALP-ZigZag swing to the bar where price CLOSED
        through it (structure.detect_choch). Bullish CHoCH cyan, bearish orange-red. Hamburger toggle m10_choch,
        no shortcut. DETECTION cached on the bucket-set signature (recomputes only on a new close, never on pan);
        the two dashed curves live in data coords and auto-follow pan/zoom, so setData is skipped unless the
        visible event slice changed. Cull is a bisect on the sorted break-bars, not an O(all) scan."""
        if not self.menu.layer_state("m10_choch") or not buckets:
            self._clear_choch(); return
        if not self._choch_added:                            # lazy-add once (self.plot exists by draw time)
            self.plot.addItem(self._choch_bull, ignoreBounds=True)
            self.plot.addItem(self._choch_bear, ignoreBounds=True)
            self._choch_added = True
        n = len(buckets)
        tc = int((self._last_snap or {}).get("total_closed", 0))
        sig = (n, tc, float(buckets[-1].get("start_time", 0.0)), float(buckets[0].get("start_time", 0.0)))
        if sig != self._choch_sig:
            H = [float(b.get("high", 0.0)) for b in buckets]; L = [float(b.get("low", 0.0)) for b in buckets]
            C = [float(b.get("close", 0.0)) for b in buckets]
            self._choch_events = structure.detect_choch(H, L, C)      # scalp ZIGZAG_PCT
            self._choch_bbar = [e[2] for e in self._choch_events]     # sorted break-bar -> bisect cull
            self._choch_sig = sig
        lo_i = max(0, int(vx0) - 1); hi_i = min(n - 1, int(vx1) + 1)
        a = bisect.bisect_left(self._choch_bbar, lo_i); b = bisect.bisect_right(self._choch_bbar, hi_i)
        rsig = (a, b, self._choch_sig)                       # curves are in data coords -> no y-scale dependence
        if rsig == self._choch_rsig:
            return                                           # visible event slice unchanged -> auto-follows
        self._choch_rsig = rsig
        xb = []; yb = []; xr = []; yr = []
        for sb, sp, bb, direction in self._choch_events[a:b][-40:]:    # most-recent 40 visible
            if direction == "bull":
                xb += [x[sb], x[bb], np.nan]; yb += [sp, sp, np.nan]
            else:
                xr += [x[sb], x[bb], np.nan]; yr += [sp, sp, np.nan]
        self._choch_bull.setData(xb, yb); self._choch_bull.setVisible(bool(xb))
        self._choch_bear.setData(xr, yr); self._choch_bear.setVisible(bool(xr))

    def _hide_4h_bands(self) -> None:
        for _z in self._z4_items:
            _z.setVisible(False)

    def _hide_4h_zone(self) -> None:
        self._hide_4h_bands()
        self._z4_fill_lbl.setVisible(False)
        for _c in self._z4_curve_pool:
            _c.setVisible(False)
        for _sp in self._z4_sep_pool:
            _sp.setVisible(False)
        for _h in self._z4_hist_pool:
            _h.setVisible(False)
        for _b in self._z4_btn_pool:
            _b.setVisible(False)
        self._z4_btn_hits = []
        self._z4_imb_buy.setVisible(False); self._z4_imb_sell.setVisible(False)   # 4h abnormal-order lines off

    @staticmethod
    def _z4_profile(bb):
        """One 4h profile row from a raw bucket dict (closed OR the live active bucket), or None if unusable:
        {lo, vlo, vmed, vhi, hi, s(start), e(end), poc, val, vah, levels}."""
        try:
            lv = bb.get("levels") or {}
            vlo, vmed, vhi = bar_quantiles.vq(lv)
            low = bb.get("low"); high = bb.get("high")
            if low is None or high is None or not (vlo and vhi):
                return None
            low = float(low); high = float(high)
            if not (high > low):
                return None
            val, vah = bar_quantiles.value_area(lv)
            return {"lo": low, "vlo": vlo, "vmed": vmed, "vhi": vhi, "hi": high,
                    "s": float(bb.get("start_time", 0.0)), "e": float(bb.get("end_time", 0.0) or 0.0),
                    "poc": bar_quantiles.poc(lv), "val": val, "vah": vah, "lvn": bar_quantiles.lvn(lv), "levels": lv,
                    "opL": float(bb.get("opL", 0.0)), "opS": float(bb.get("opS", 0.0)),   # bucket force totals
                    "clL": float(bb.get("clL", 0.0)), "clS": float(bb.get("clS", 0.0))}   # (per-level split by ratio)
        except Exception:
            return None

    def _z4_lut(self):
        """Cached 4h profile lookup: (ets, rows) with rows[i] a dict for the completed 4h bucket ending at ets[i]:
        {lo, vlo, vmed, vhi, hi, s(start), e(end), poc, val, vah}. vlo/vhi = the q25/q75 wick bounds (Z zone);
        poc/val/vah/vmed = the volume profile (V). Rebuilt only when the newest 4h bucket changes. Source = the
        secondary 4h worker's closed buckets (+ archive fallback). Read by the pivot fade AND the 4h display."""
        snap4 = self.worker_4h.snapshot() if getattr(self, "worker_4h", None) else None
        cb = (snap4 or {}).get("closed_buckets") or []
        key = ("live", float(cb[-1].get("end_time", 0.0))) if cb else None
        if not cb and archive.available("4h"):
            try:
                d = archive._load("4h"); ks = sorted(d); cb = [d[k] for k in ks]
                key = ("arch", ks[-1] if ks else 0.0)
            except Exception:
                cb = []
        if key is not None and key == getattr(self, "_z4_lut_key", None):
            return self._z4_lut_cache
        pairs = []
        for bb in cb:
            r = self._z4_profile(bb)
            if r is not None:
                pairs.append((r["e"], r))
        pairs.sort(key=lambda p: p[0])          # key on end_time ONLY — the rows are dicts (not orderable) so a
        self._z4_lut_key = key                  # plain sort() would TypeError on any end_time tie
        self._z4_lut_cache = ([p[0] for p in pairs], [p[1] for p in pairs])
        return self._z4_lut_cache

    def _zone5_at(self, t, price):
        ets, rows = self._z4_lut()
        if not ets:
            return None
        i = bisect.bisect_right(ets, t) - 1
        if i < 0:
            return None
        r = rows[i]
        return _pivot_zone5(float(price), r["lo"], r["vlo"], r["vhi"], r["hi"])

    def _vpform_bin_at(self, filtered, det):
        """CAUSAL forming-VP 4-bin of the D's close vs the CURRENTLY-FORMING 4h bucket — reconstructed from the
        primary buckets' footprints since the last completed 4h close, up to and INCLUDING the D bar. Mirrors the
        study de_zone_effectiveness.forming()/vp_bin() exactly (bar_quantiles.value_area/vq on the summed b/s
        levels). Returns 'above VAH' / 'upper VA' / 'lower VA' / 'below VAL', or None when it can't be
        reconstructed (no 4h close yet, degenerate profile, or the forming window reaches before the loaded set)
        -> None means DON'T fade. Read only in _draw_pivot for the Buy-D VP-edge overlay; V3 detection untouched."""
        try:
            ets, _rows = self._z4_lut()
            if not ets:
                return None
            t = float(filtered[det].get("end_time", 0.0))
            i4 = bisect.bisect_right(ets, t) - 1          # last COMPLETED 4h close as-of the D (causal)
            start_t = ets[i4] if i4 >= 0 else -1.0
            agg = {}; j = det
            while j >= 0 and float(filtered[j].get("end_time", 0.0)) > start_t:   # primary bars in (last 4h close, D]
                for ps, vv in (filtered[j].get("levels") or {}).items():
                    a = agg.get(ps)
                    if a is None:
                        a = [0.0, 0.0]; agg[ps] = a
                    a[0] += float(vv.get("b", 0.0)); a[1] += float(vv.get("s", 0.0))
                j -= 1
            if j < 0 and start_t >= 0.0 and float(filtered[0].get("end_time", 0.0)) > start_t:
                return None                               # forming bucket starts before the loaded window -> incomplete
            if len(agg) < 2:
                return None
            lv = {p: {"b": v[0], "s": v[1]} for p, v in agg.items()}
            _q = bar_quantiles.vq(lv); _va = bar_quantiles.value_area(lv)
            val, med, vah = float(_va[0]), float(_q[1]), float(_va[1])
            if not (val == val and vah == vah and med == med and vah > val):
                return None                               # degenerate value area -> can't bin -> don't fade
            px = float(filtered[det].get("close", filtered[det].get("close_price", 0.0)))
            if px > vah:
                return "above VAH"
            if px > med:
                return "upper VA"
            if px >= val:
                return "lower VA"
            return "below VAL"
        except Exception:
            return None

    def _pivot_v2_taken(self, filtered, det, ent, buy, e_sh, a, n, b_end):
        """True if PIVOT-ZZTRAIL-v2 would TAKE this setup (D-fill tier + E-held-else-E2 entry + zone TAKE filter
        at the D zone OR the entry zone + hollow AVOID). False -> the overlay FADES the setup. Overlay-only."""
        di = det - a
        sd = ((2.0 * float(e_sh[di]) - 1.0) * 100.0) if 0 <= di < len(e_sh) else 0.0
        p2d = sd if buy else -sd
        tier = "cyan/orange" if p2d > PIVOT_P2D_VHIGH else ("red/green" if p2d > PIVOT_P2D_HIGH else "hollow")
        sg = 1.0 if buy else -1.0; _di, _ei = det - a, ent - a
        if 0 <= _di < len(e_sh) and 0 <= _ei < len(e_sh):
            liv = [sg * (2.0 * float(e_sh[k]) - 1.0) * 100.0 for k in range(_di, _ei + 1)]
            e_held = liv[-1] > 0.0 and min(liv) > -50.0
        else:
            e_held = True
        j0 = None
        if e_held:
            if tier == "hollow":
                j0 = ent
        else:
            et_e = float(filtered[ent].get("end_time", 0.0))
            for j in range(ent + 1, b_end):
                if et_e and float(filtered[j].get("end_time", 0.0)) > et_e + 3600.0:
                    break
                jj = j - a
                if 0 <= jj < len(e_sh):
                    v = (2.0 * float(e_sh[jj]) - 1.0) * 100.0
                    if (v if buy else -v) >= PIVOT_E2_MIN:
                        j0 = j; break
        if j0 is None or j0 >= n:
            return False                                          # no valid v2 entry -> skip -> fade
        px = lambda i: float(filtered[i].get("close", filtered[i].get("close_price", 0.0)))
        zD = self._zone5_at(float(filtered[det].get("end_time", 0.0)), px(det))
        zE = self._zone5_at(float(filtered[j0].get("end_time", 0.0)), px(j0))
        if zD is None or zE is None:
            return True                                           # no 4h data to judge -> don't fade
        take = _pivot_v2_take_rule(zD, buy, tier) or _pivot_v2_take_rule(zE, buy, tier)
        if tier == "hollow" and (("buy" if buy else "sell"), zD, zE) in PIVOT_V2_AVOID:
            take = False
        return take

    @staticmethod
    def _pivot_fade_spot(spot):
        """Dim a scatter spot's brush+pen (translucent) to signal 'v2 skips this setup'."""
        br = spot.get("brush"); pn = spot.get("pen")
        if br is not None:
            c = br.color(); spot["brush"] = pg.mkBrush(c.red(), c.green(), c.blue(), 35)
        if pn is not None:
            c = pn.color(); w = max(1.0, pn.widthF())
            spot["pen"] = pg.mkPen(c.red(), c.green(), c.blue(), 70, width=w)
        return spot

    def _z4_curve(self, used):                                   # pooled overlay curve (zone band via fillLevel, or VP line)
        if used >= len(self._z4_curve_pool):
            _c = pg.PlotCurveItem(); _c.setZValue(1)
            self.plot.addItem(_c, ignoreBounds=True); self._z4_curve_pool.append(_c)
        return self._z4_curve_pool[used]

    def _z4_button(self, used):                                  # pooled 'V'/'Z' button (bottom-anchored, on the axis)
        if used >= len(self._z4_btn_pool):
            _t = pg.TextItem(anchor=(0.5, 1.0), color=(0, 0, 0),   # anchor = bottom-centre -> sits ON the x-axis
                             border=pg.mkPen((165, 175, 190), width=1))   # always-visible outline (on OR off)
            _t.setZValue(60)
            _f = QtGui.QFont("Consolas", 10); _f.setBold(True); _t.textItem.setFont(_f)
            self.plot.addItem(_t, ignoreBounds=True); self._z4_btn_pool.append(_t)
        return self._z4_btn_pool[used]

    def _z4_deactivate_all(self) -> None:
        """'z' — turn OFF every currently-active 4h V / Z / B overlay (only deactivates; never activates). Sets each
        explicit toggle False and force-closes the default-on last-bucket Z (read off the drawn buttons)."""
        for _k in list(self._z4_user):
            self._z4_user[_k] = False
        for _bx, _by, _key, _kind, _on in self._z4_btn_hits:     # catches the default-ON (last completed bucket's Z)
            if _on:
                self._z4_user[(_key, _kind)] = False
        if self._z4_last_buckets:
            try:
                self._draw_4h_zone(self._z4_last_buckets)         # redraw immediately
            except Exception:
                pass

    def _z4_sep(self, used):                                     # pooled 4h bucket-start separator (dashed vline)
        if used >= len(self._z4_sep_pool):
            _pn = pg.mkPen(color=(170, 170, 170, 150), width=1); _pn.setCosmetic(True)   # match the crosshair style
            _pn.setDashPattern([4.0, 8.0])
            _ln = pg.InfiniteLine(angle=90, movable=False, pen=_pn); _ln.setZValue(14)
            self.plot.addItem(_ln, ignoreBounds=True); self._z4_sep_pool.append(_ln)
        return self._z4_sep_pool[used]

    def _z4_hist(self, used, **opts):                            # pooled horizontal volume-profile histogram
        if used >= len(self._z4_hist_pool):
            _h = pg.BarGraphItem(x0=[0.0], width=[0.0], y=[0.0], height=[0.0], pen=None)
            _h.setZValue(1); self.plot.addItem(_h, ignoreBounds=True); self._z4_hist_pool.append(_h)
        _hb = self._z4_hist_pool[used]; _hb.setOpts(**opts)
        return _hb

    @staticmethod
    def _z4_force_hist(buckets, starts, s_t, e_t):
        """Per-price {price_str: [opL, opS, clL, clS]} aggregated over the 1m buckets in [s_t, e_t) — each 1m
        bucket's per-level buy(=OPL+CLS)/sell(=OPS+CLL) volume split by ITS OWN force ratios. Far finer than one
        4h ratio: different 1m buckets tag the same price with different forces, so the profile shows real detail."""
        i0 = bisect.bisect_left(starts, s_t); i1 = bisect.bisect_left(starts, e_t)
        agg = {}
        for m in buckets[i0:i1]:
            lv = m.get("levels") or {}
            if not lv:
                continue
            oL = float(m.get("opL", 0.0)); oS = float(m.get("opS", 0.0))
            cL = float(m.get("clL", 0.0)); cS = float(m.get("clS", 0.0))
            bd = oL + cS; sd = oS + cL
            fOL = oL / bd if bd > 0 else 0.5; fCS = cS / bd if bd > 0 else 0.5
            fOS = oS / sd if sd > 0 else 0.5; fCL = cL / sd if sd > 0 else 0.5
            for ps, vv in lv.items():
                _b = float(vv.get("b", 0.0)); _s = float(vv.get("s", 0.0))
                a = agg.get(ps)
                if a is None:
                    a = [0.0, 0.0, 0.0, 0.0]; agg[ps] = a
                a[0] += _b * fOL; a[1] += _s * fOS; a[2] += _s * fCL; a[3] += _b * fCS
        return agg

    def _draw_4h_zone(self, buckets) -> None:
        """Per-4h-bucket VOLUME-PROFILE ('V': VAH/VAL/POC/median), ZONE ('Z': buy/sell wick bands), and ABNORMAL-ORDER
        ('B') overlays, DETACHED from the selection tool. Each completed 4h bucket gets a small V/Z/B button trio just
        above the x-axis, under its DISPLAY span = the NEXT 4h window — so bucket N's levels overlay the 1m candles that
        formed AFTER it (study the reaction). The LAST completed bucket's span is the live forming region; its Z shows
        there by default. B (default OFF, click to show) draws the 4h candle's own abnormal orders: each level in the 4h
        bucket's ladder whose buy/sell volume >= FOOTPRINT_IMB_ER_MULT_4H x the bucket's AVERAGE per-level volume, as a
        horizontal blue(buy)/orange(sell) line across the span — the 4h analog of the always-on 1m imbalance lines
        (which are UNCHANGED). Master gate m10_4hzone. Pivot V3 reads _zone5_at separately, so this is display-only."""
        if not self.menu.layer_state("m10_4hzone") or not buckets:
            self._hide_4h_zone(); return
        ets, rows = self._z4_lut()
        n = len(buckets)
        sig = (n, float(buckets[0].get("start_time", 0.0)), float(buckets[-1].get("start_time", 0.0)))
        if sig != self._zone4h_starts_sig:                        # cache the 1m start-times (rebuild only on frame change)
            self._zone4h_starts = [float(bk.get("start_time", 0.0)) for bk in buckets]; self._zone4h_starts_sig = sig
        starts = self._zone4h_starts
        self._z4_last_buckets = buckets                           # a button click redraws off this cached frame
        if not ets or not starts:
            self._hide_4h_zone(); return
        now_t = float(buckets[-1].get("end_time", 0.0)) or starts[-1]
        canvas_lo = starts[0]

        def _xt(t):                                               # time -> visible bar index
            return max(0, min(bisect.bisect_left(starts, t), n - 1))
        (_vx0, _vx1), (vy0, vy1) = self.vb.viewRange()
        yb_btn = vy0 + (vy1 - vy0) * 0.008                        # button row: sits ON the x-axis (bottom-anchored)
        _vbw = max(1.0, float(self.vb.width()))                   # px -> data-x so V/Z stay adjacent at ANY zoom
        _hw = 13.0 * (_vx1 - _vx0) / _vbw                         # half a button's width, in data-x units
        last_i = len(rows) - 1
        snap4 = self.worker_4h.snapshot() if getattr(self, "worker_4h", None) else None
        active = (snap4 or {}).get("active_bucket") or {}
        # Render list: every entry draws over its OWN span. The LAST completed bucket's bands+lines EXTEND right into
        # the live forming region (the reference for the live candles; default ON). The LIVE forming bucket gets its
        # OWN entry+button (in-progress profile). Tuple = (r, x0, x1_own, x1_ext, bx_left, key, default_z).
        entries = []
        for i, r in enumerate(rows):
            s_t = r["s"]; ext_hi = now_t if i == last_i else r["e"]   # last completed extends to the live edge
            if ext_hi <= canvas_lo or s_t >= now_t:
                continue
            _x0 = _xt(max(s_t, canvas_lo))
            entries.append((r, _x0, _xt(min(r["e"], now_t)), _xt(min(ext_hi, now_t)), _x0, round(r["e"], 3),
                            i == last_i, r["s"], r["e"]))                # + the bucket's OWN [start,end] time window
        if rows:                                                  # the LIVE forming bucket -> its own partial profile
            lr = self._z4_profile(active); s_t = rows[-1]["e"]    # live region begins at the last close
            if lr is not None and s_t < now_t:
                _x0 = _xt(max(s_t, canvas_lo))
                entries.append((lr, _x0, n - 1, n - 1, _x0, "live", False, s_t, now_t))
        uc = uh = ub = us = 0; self._z4_btn_hits = []
        _imb_bx = []; _imb_by = []; _imb_sx = []; _imb_sy = []    # 4h abnormal-order segments (buy blue / sell orange)
        _mult4h = config.FOOTPRINT_IMB_ER_MULT_4H
        _OFF = (52, 58, 72); _OFF_TXT = (205, 212, 225)   # OFF button: dark-slate fill + light text (+ the outline)
        for r, x0, x1o, x1e, bx0, key, default_z, own_s, own_e in entries:
            if x1e <= x0:
                x1e = min(n - 1, x0 + 1)
            x1o = max(x0 + 1, min(x1o, x1e))
            z_on = self._z4_user.get((key, "Z"), default_z)       # default: last completed bucket's Z (extended)
            v_on = self._z4_user.get((key, "V"), False)
            b_on = self._z4_user.get((key, "B"), False)          # 4h abnormal-order lines: OFF by default (click to show)
            if b_on:                                             # ABNORMAL ORDERS of the 4h candle: imbalanced ladder
                _lv = r.get("levels") or {}                      # levels whose buy/sell vol >> the bucket's per-level avg,
                if len(_lv) >= 2:                                # drawn as a horizontal line across this span [x0, x1e]
                    _tb = _ts = 0.0
                    for _v in _lv.values():
                        _tb += float(_v.get("b", 0.0)); _ts += float(_v.get("s", 0.0))
                    _nl = len(_lv)
                    _bthr = _mult4h * (_tb / _nl) if _tb > 0 else None   # mult x AVERAGE per-level buy (= buyer_er scale)
                    _sthr = _mult4h * (_ts / _nl) if _ts > 0 else None
                    _mid = (x0 + x1e) / 2.0
                    for _ps, _v in _lv.items():
                        _bi = _bthr is not None and float(_v.get("b", 0.0)) >= _bthr
                        _si = _sthr is not None and float(_v.get("s", 0.0)) >= _sthr
                        if not (_bi or _si):
                            continue
                        _yy = float(_ps)
                        if _bi and _si:                          # both -> split: sell (orange) left, buy (blue) right
                            _imb_sx += [x0, _mid]; _imb_sy += [_yy, _yy]
                            _imb_bx += [_mid, x1e]; _imb_by += [_yy, _yy]
                        elif _si:
                            _imb_sx += [x0, x1e]; _imb_sy += [_yy, _yy]
                        else:
                            _imb_bx += [x0, x1e]; _imb_by += [_yy, _yy]
            if z_on and r["hi"] > r["lo"]:                        # Z bands (green buy wick / red sell wick) -> x1_ext
                _cb = self._z4_curve(uc); uc += 1
                _cb.setData([x0, x1e], [r["vlo"], r["vlo"]]); _cb.setFillLevel(r["lo"])
                _cb.setBrush(pg.mkBrush(40, 230, 90, 30)); _cb.setPen(pg.mkPen(None)); _cb.setVisible(True)
                _cs = self._z4_curve(uc); uc += 1
                _cs.setData([x0, x1e], [r["hi"], r["hi"]]); _cs.setFillLevel(r["vhi"])
                _cs.setBrush(pg.mkBrush(255, 45, 70, 30)); _cs.setPen(pg.mkPen(None)); _cs.setVisible(True)
            if v_on:                                              # VOLUME PROFILE histogram (style = 'Volume Profile Mode' dropdown)
                try:                                              # a VP-histogram failure must NOT hide the V/Z/B buttons + lines
                    _agg = self._z4_force_hist(buckets, starts, own_s, own_e)   # {price: [opL, opS, clL, clS]}
                    _rw = sorted(((float(_ps), _a) for _ps, _a in _agg.items()), key=lambda t: t[0])
                    if len(_rw) >= 2 and max((sum(_a) for _, _a in _rw), default=0.0) > 0:
                        _prices = [p for p, _ in _rw]
                        _gaps = sorted(_prices[k + 1] - _prices[k] for k in range(len(_prices) - 1))
                        _thick = _gaps[len(_gaps) // 2] if _gaps else (_prices[-1] - _prices[0]) / len(_prices)
                        _vpx, _vpw, _vpy, _vph, _vpb = self._vp_segments(_rw, self._vp_mode, float(x0), float(x1o - x0), _thick)
                        if _vpw:                          # NOTE: do NOT reuse _hw here — it's the button half-width used below
                            self._z4_hist(uh, x0=_vpx, width=_vpw, y=_vpy, height=_vph, brushes=_vpb, pen=None).setVisible(True); uh += 1
                except Exception:
                    pass                                      # a VP-histogram glitch must never hide the V/Z/B buttons + lines
                for lvl, col, dash in ((r["vah"], (255, 30, 70), False),      # VAH  neon red
                                       (r["val"], (0, 255, 120), False),        # VAL  neon green
                                       (r["poc"], (255, 240, 0), False),        # POC  neon yellow
                                       (r.get("lvn"), (178, 70, 255), True),    # LVN  electric purple (dashed)
                                       (r["vmed"], (255, 255, 255), True)):     # median neon white (spaced dashes)
                    if not (lvl and lvl == lvl):                  # skip 0 / NaN
                        continue
                    _ln = self._z4_curve(uc); uc += 1             # level lines -> x1_ext (no labels — colour = identity)
                    _pn = pg.mkPen(col, width=1.5); _pn.setCosmetic(True)
                    if dash:
                        _pn.setDashPattern([3.0, 8.0])            # median: short dash, wide gap
                    _ln.setData([x0, x1e], [lvl, lvl]); _ln.setBrush(None); _ln.setPen(_pn); _ln.setVisible(True)
            if key != "live" and self.menu.layer_state("m10_4hsep"):   # dashed bucket-START separator at the V button's
                _sp = self._z4_sep(us); us += 1                        # left edge (bx0); skip the live forming bucket
                _sp.setValue(bx0); _sp.setVisible(True)
            for kind, on, rgb, mul in (("V", v_on, (90, 190, 255), 1.0), ("Z", z_on, (40, 230, 90), 3.0),
                                       ("B", b_on, (255, 128, 0), 5.0)):   # B = abnormal-order (imbalance) lines, orange
                _bt = self._z4_button(ub); ub += 1                # trio at the LEFT edge (where the bucket started)
                _bx = bx0 + mul * _hw
                _bt.fill = pg.mkBrush(*rgb) if on else pg.mkBrush(*_OFF)   # fill BEFORE setText so the repaint uses it
                _bt.setColor((10, 12, 16) if on else _OFF_TXT); _bt.setText(f" {kind} ")
                _bt.setPos(_bx, yb_btn); _bt.setVisible(True)
                self._z4_btn_hits.append((_bx, yb_btn, key, kind, on))
        self._z4_imb_buy.setData(_imb_bx, _imb_by, connect="pairs")    # 4h abnormal-order lines (batched across B-on spans)
        self._z4_imb_sell.setData(_imb_sx, _imb_sy, connect="pairs")
        self._z4_imb_buy.setVisible(bool(_imb_bx)); self._z4_imb_sell.setVisible(bool(_imb_sx))
        # FORMING 4h bucket fill-% at the live region's top-right (live only)
        target = float((snap4 or {}).get("target_vol") or 0.0)
        if target > 0.0 and rows:
            fill = min(99.9, max(0.0, float(active.get("curr_vol", 0.0)) / target * 100.0))
            (_zvx0, zvx1), _zvy = self.vb.viewRange()
            self._z4_fill_lbl.setText("4h fill %.0f%%" % fill)
            self._z4_fill_lbl.setColor((255, 170, 60) if fill >= 85.0 else (200, 205, 215))
            self._z4_fill_lbl.setPos(min(n - 1, zvx1), rows[-1]["hi"]); self._z4_fill_lbl.setVisible(True)
        else:
            self._z4_fill_lbl.setVisible(False)
        for _j in range(uc, len(self._z4_curve_pool)):
            self._z4_curve_pool[_j].setVisible(False)
        for _j in range(us, len(self._z4_sep_pool)):
            self._z4_sep_pool[_j].setVisible(False)
        for _j in range(uh, len(self._z4_hist_pool)):
            self._z4_hist_pool[_j].setVisible(False)
        for _j in range(ub, len(self._z4_btn_pool)):
            self._z4_btn_pool[_j].setVisible(False)

    def _toggle_phase_table(self) -> None:
        """'t' — show/hide the live PHASE TABLE on its own (no need to turn on a phase panel 5/6/7)."""
        self.show_phase_table = not self.show_phase_table
        if not self.show_phase_table and not any(self.show_phase.values()):
            self.phase_tbl.hide()
        self._save_ui_state()
        self._refresh_selection_stats()

    def _save_ui_state(self) -> None:
        """Persist the Mode-10 panel toggle states so a reopened session restores the same layout — the user
        sets panels on/off once and it sticks across sessions. Best-effort; a write failure is ignored."""
        try:
            config.ensure_data_dir()
            state = {
                "abs": self.show_abs_strip, "eff": self.show_eff_strip,
                "abs_hm": self.show_abs_hm, "eff_hm": self.show_eff_hm,
                "er": self.show_er_strip, "exh": self.show_exh_strip,
                "ls_mode": self._ls_mode, "panel9": self.show_panel9, "panel0": self.show_panel0,
                "candle_mode": self._candle_mode,
                "vp_mode": self._vp_mode,
                "hide_candles": self._hide_candles,
                "phase_table": self.show_phase_table,
                "phase": {k: bool(v) for k, v in self.show_phase.items()},
                "liq_labels": self.show_liq,
                "pivot": self.show_pivot, "pivot_causal": self.pivot_causal,
                "zone_s": self._zone_user_s, "eff_f": self._eff_user_f,   # persisted slider overrides (None = adaptive)
                "zone_sides": list(self.zone_slider.sides()),             # Bull/Bear zone filters (both = default)
                "eff_sides": list(self.eff_slider.sides()),
                "replay_edge_t": self._replay_saved_edge_t,               # last replay cursor -> resume here on toggle-on
                "swing_pct": self._swing_pct,                             # swing-ZigZag sensitivity slider (%)
                "kc_scale": self._kc_scale,                               # 1m-KC smooth-approx effective-TF scale slider
                "ob_unmitig_only": self._ob_unmitig_only,                 # 'o' cycle stage-2: unmitigated OBs only
            }
            # EVERY hamburger toggle (Sub-Widgets + Mode 10 Overlays), keyed by its menu key, so a reopened
            # session restores the exact menu the user left (POC, footprint, alerts, … all sticky).
            _m = getattr(self, "menu", None)
            if _m is not None:
                state["toggles"] = {
                    **{k: cb.isChecked() for k, cb in _m.sub_checks.items()},
                    **{k: cb.isChecked() for k, cb in _m.layer_checks.items() if cb.isEnabled()},
                }
            with open(os.path.join(config.DATA_DIR, "terminal_ui.json"), "w") as f:
                json.dump(state, f)
        except OSError:
            pass

    def _load_ui_state(self) -> None:
        """Restore the panel toggles a prior session saved. Missing keys keep the code default, so a panel
        added later still gets its built-in default until the user toggles it. Best-effort."""
        path = os.path.join(config.DATA_DIR, "terminal_ui.json")
        if not os.path.exists(path):
            return
        try:
            with open(path) as f:
                s = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        self.show_abs_strip = bool(s.get("abs", self.show_abs_strip))
        self.show_eff_strip = bool(s.get("eff", self.show_eff_strip))
        self.show_abs_hm = bool(s.get("abs_hm", self.show_abs_hm))
        self.show_eff_hm = bool(s.get("eff_hm", self.show_eff_hm))
        self.show_er_strip = bool(s.get("er", self.show_er_strip))
        self.show_exh_strip = bool(s.get("exh", self.show_exh_strip))
        _lm = s.get("ls_mode")
        if _lm is None:                                       # migrate the old boolean: True (both) -> 2, else 0
            _lm = 2 if s.get("largesmall") else 0
        self._ls_mode = _lm if _lm in (0, 1, 2) else 0
        self.show_panel9 = bool(s.get("panel9", self.show_panel9))
        self.show_panel0 = bool(s.get("panel0", self.show_panel0))
        self.show_liq = bool(s.get("liq_labels", self.show_liq))
        self.show_pivot = bool(s.get("pivot", self.show_pivot))
        self.pivot_causal = bool(s.get("pivot_causal", self.pivot_causal))
        self._saved_toggles = dict(s.get("toggles") or {})   # applied to the menu checkboxes in _apply_saved_toggles
        _cm = s.get("candle_mode")                            # 0 normal / 1 whisker / 2 footprint (back-compat: old "whisker" bool)
        if _cm is None:
            _cm = 1 if s.get("whisker") else 0
        self._candle_mode = int(_cm) % 6
        _vm = s.get("vp_mode")
        if isinstance(_vm, (int, float)):
            self._vp_mode = int(_vm) % 8
        self._hide_candles = bool(s.get("hide_candles", self._hide_candles))
        self.show_phase_table = bool(s.get("phase_table", self.show_phase_table))
        for _k, _v in (s.get("phase") or {}).items():
            if _k in self.show_phase:
                self.show_phase[_k] = bool(_v)
        _zs = s.get("zone_s"); _ef = s.get("eff_f")          # restore persisted slider overrides (float or None)
        self._zone_user_s = float(_zs) if isinstance(_zs, (int, float)) else None
        self._eff_user_f = float(_ef) if isinstance(_ef, (int, float)) else None
        for _sl, _key in ((self.zone_slider, "zone_sides"), (self.eff_slider, "eff_sides")):
            _sd = s.get(_key)                                 # restore Bull/Bear zone filters (default both on)
            if isinstance(_sd, (list, tuple)) and len(_sd) == 2:
                _sl.set_sides(bool(_sd[0]), bool(_sd[1]))
        _ret = s.get("replay_edge_t")                         # remembered replay position (restored on next toggle-on)
        self._replay_saved_edge_t = float(_ret) if isinstance(_ret, (int, float)) else None
        _sp = s.get("swing_pct")                              # restore the swing-ZigZag sensitivity (the menu slider is
        if isinstance(_sp, (int, float)):                     # synced to this right after the menu is built — see __init__)
            self._swing_pct = float(_sp)
        _kcs = s.get("kc_scale")                              # restore the Keltner smooth-approx scale (menu slider synced after build)
        if isinstance(_kcs, (int, float)):
            self._kc_scale = max(1.0, min(float(config.KELTNER_SCALE_MAX), float(_kcs)))
        self._ob_unmitig_only = bool(s.get("ob_unmitig_only", self._ob_unmitig_only))   # 'o' cycle stage-2 filter

    def _set_ob_ice(self, on: bool) -> None:
        """Flip the Order Blocks + Absorption/Iceberg menu checkboxes together (emits layerToggled -> show/hide)."""
        for cb in (self.menu.layer_checks.get("m10_obs"), self.menu.layer_checks.get("m10_icebergs")):
            if cb is not None and cb.isChecked() != on:
                cb.setChecked(on)

    def _toggle_ob_iceberg(self) -> None:
        """'o' — 3-stage cycle for Order Blocks + Absorption/Iceberg:
        OFF -> ON (mitigated + unmitigated) -> ON (UNMITIGATED only) -> OFF.
        Stages 1<->2 keep the overlays ON and just flip the mitigated filter (a repaint, no checkbox re-emit)."""
        obs = self.menu.layer_checks.get("m10_obs")
        on = obs.isChecked() if obs else False
        if not on:                              # stage 1: turn on, show ALL (mitigated + unmitigated)
            self._ob_unmitig_only = False
            self._set_ob_ice(True)
        elif not self._ob_unmitig_only:         # stage 2: stay on, show UNMITIGATED only
            self._ob_unmitig_only = True
            self._last_scanner_sig = None; self._draw_scanner()   # filter changed but checkboxes stay on -> force repaint
        else:                                   # stage 3: turn off
            self._ob_unmitig_only = False
            self._set_ob_ice(False)
        self._save_ui_state()

    def _replay_ob_abs(self, filtered: list) -> tuple:
        """Return the last causal OB + absorption marks for the REPLAY frame. On a NEW frame the ~300ms re-detect is
        NOT run inline (that made stepping lag) — it's DEBOUNCED via _replay_oba_recompute, and the step repaints
        immediately with the previous marks (fine: they live in data coords). Live mode never calls this."""
        key = (len(filtered), float(filtered[-1].get("end_time", 0.0))) if filtered else (0, 0.0)
        if key != self._replay_oba_key:
            self._replay_oba_pending = (key, filtered)
            self._replay_oba_timer.start()          # coalesces rapid steps -> one recompute after you pause
        return self._replay_oba_cache

    def _replay_qbs(self, filtered: list) -> list:
        """Reconstruct the frame's QuantBuckets fresh (no cache — content keys weren't reliably unique in the
        archive, and a stale/collided bucket silently skews the causal marks). ~75ms, absorbed by the debounce."""
        from app import persistence
        return [persistence.bucket_from_snapshot(b) for b in filtered]

    def _replay_oba_recompute(self) -> None:
        """Debounced heavy re-detect (fires ~130ms after the last step): run the daemon's exact pipeline
        (rank_obs(calc_quant_obs(...)) + calc_absorption) on the pending frame, cache it, and repaint. Any failure
        -> empty, never fatal."""
        pend = self._replay_oba_pending
        if pend is None:
            return
        key, filtered = pend
        obs, absp = [], []
        try:
            from types import SimpleNamespace
            from app import quant_engine
            qbs = self._replay_qbs(filtered)
            obs = quant_engine.rank_obs(quant_engine.calc_quant_obs(SimpleNamespace(closed_buckets=qbs), self.worker.tf))
            absp = quant_engine.calc_absorption(qbs)
        except Exception:
            obs, absp = [], []
        self._replay_oba_key = key
        self._replay_oba_cache = (obs, absp)
        self._replay_oba_pending = None
        self._last_scanner_sig = None
        self._draw_scanner()                         # repaint with the fresh marks (key now matches -> no re-schedule)

    def _toggle_sel_stats(self) -> None:
        """'h' — show/hide the Mode-10 Magic-Selection stats box ONLY. The selection's chart overlays
        (flip line, absorption boxes, velocity markers) keep rendering; just the floating box is gated."""
        self.show_sel_stats = not self.show_sel_stats
        if self.show_sel_stats:
            self._refresh_selection_stats()    # re-place + re-show if a selection is active
        else:
            self.sel_stats.hide()
            self._hide_sel_ctrls()              # hide the box + controls card; the VP overlay persists (independent of 'h')

    def _on_sel_vp_toggled(self, on: bool) -> None:
        """'h'-card 'Volume Profile' checkbox — show/hide the profile over the current selection."""
        self.show_sel_vp = bool(on)
        self._sel_sig = None                   # force a selection recompute so the VP draws/clears now
        self._refresh_selection_stats()

    def _toggle_sel_vp(self) -> None:
        """Ctrl+Z — toggle the Mode-10 selection Volume Profile on/off. Flips the 'h'-box VP checkbox (which drives
        _on_sel_vp_toggled), so the checkbox stays in sync and it works even while that box is hidden."""
        self.sel_vp_chk.setChecked(not self.sel_vp_chk.isChecked())

    @staticmethod
    def _sel_vp_hist(sel):
        """Per-price {price_str: [opL, opS, clL, clS]} over the selected buckets — the SAME 1m-force split as the
        4h VP (_z4_force_hist), so colours match: buy = opL+clS, sell = opS+clL, coloured by the dominant force."""
        agg = {}
        for m in sel:
            lv = m.get("levels") or {}
            if not lv:
                continue
            oL = float(m.get("opL", 0.0)); oS = float(m.get("opS", 0.0))
            cL = float(m.get("clL", 0.0)); cS = float(m.get("clS", 0.0))
            bd = oL + cS; sd = oS + cL
            fOL = oL / bd if bd > 0 else 0.5; fCS = cS / bd if bd > 0 else 0.5
            fOS = oS / sd if sd > 0 else 0.5; fCL = cL / sd if sd > 0 else 0.5
            for ps, vv in lv.items():
                _b = float(vv.get("b", 0.0)); _s = float(vv.get("s", 0.0))
                a = agg.get(ps)
                if a is None:
                    a = [0.0, 0.0, 0.0, 0.0]; agg[ps] = a
                a[0] += _b * fOL; a[1] += _s * fOS; a[2] += _s * fCL; a[3] += _b * fCS
        return agg

    def _hide_sel_ctrls(self) -> None:
        """Hide the 'h'-box controls group (VP checkbox + the two zone/force sliders)."""
        self.sel_vp_chk.hide(); self.zone_slider.hide(); self.eff_slider.hide()

    def _hide_selection_vp(self) -> None:
        self.bc_sel_vp.setVisible(False)
        for _ln in self.bc_sel_vp_lines:
            _ln.setVisible(False)

    _VP_FCOL = ((40, 230, 90, 165), (255, 55, 70, 165), (235, 70, 255, 165), (0, 210, 255, 165))  # opL/opS/clL/clS
    _VP_GREEN = (40, 230, 90, 165); _VP_RED = (235, 55, 70, 165)

    def _vp_segments(self, rows, mode, x0, span, thick):
        """Build BarGraphItem opts (x0s, widths, ys, heights, brushes) for a volume profile in the given VP `mode`,
        over `rows` = sorted [(price, [opL,opS,clL,clS])], anchored in the region [x0, x0+span]. Shared by the
        Mode-10 selection VP and the 4h 'V' overlay so both honour the 'Volume Profile Mode' dropdown. Modes:
        0 Basic (right, green/red by net side), 1 Force (right, dominant-force colour), 2 Split Basic (buy green
        right / sell red left), 3 Split Basic Delta (net delta signed), 4 Split Force (opL green+clS cyan right /
        opS red+clL magenta left), 5 Split Force Delta (net delta signed, dominant-force colour), 6 Basic Bulls
        (right, net-BUY delta only = bull-dominant price zones, green), 7 Basic Bears (right, net-SELL delta only
        = bear-dominant zones, red). 6 & 7 share the max|delta| scale so the two are directly comparable."""
        x0s = []; ws = []; ys = []; hs = []; brs = []

        def _add(bx, bw, by, col):
            if bw > 0:
                x0s.append(bx); ws.append(bw); ys.append(by); hs.append(thick); brs.append(pg.mkBrush(*col))

        lv = []
        for pr, a in rows:
            oL, oS, cL, cS = a
            buy = oL + cS; sell = oS + cL; tot = oL + oS + cL + cS
            lv.append((pr, oL, oS, cL, cS, buy, sell, tot, buy - sell, max(range(4), key=lambda k: a[k])))
        if not lv:
            return x0s, ws, ys, hs, brs
        if mode in (0, 1, 6, 7):                               # RIGHT-only histograms
            if mode in (6, 7):                                 # delta-filtered basic: one side's NET-dominant zones,
                vmax = max(abs(r[8]) for r in lv) or 1.0       # shared max|delta| scale -> the two are comparable
            else:                                              # 0 basic / 1 force -> TOTAL volume
                vmax = max(r[7] for r in lv) or 1.0
            sc = (0.40 * span) / vmax
            for pr, oL, oS, cL, cS, buy, sell, tot, d, dom in lv:
                if mode == 6:                                  # BULLS: only net-buy levels (d>0), green bar = delta
                    _add(x0, d * sc, pr, self._VP_GREEN)       # d<=0 -> width<=0 -> skipped by _add (not bull-dominant)
                elif mode == 7:                                # BEARS: only net-sell levels (d<0), red bar = |delta|
                    _add(x0, -d * sc, pr, self._VP_RED)        # d>=0 -> width<=0 -> skipped by _add (not bear-dominant)
                else:
                    col = (self._VP_GREEN if buy >= sell else self._VP_RED) if mode == 0 else self._VP_FCOL[dom]
                    _add(x0, tot * sc, pr, col)
        else:                                                  # SPLIT: the LEFT edge (selection's left line / 4h separator)
            cx = x0                                            # is the split -> buy RIGHT (inside), sell LEFT (outside)
            vmax = (max(max(r[5], r[6]) for r in lv) if mode in (2, 4) else max(abs(r[8]) for r in lv)) or 1.0
            sc = (0.40 * span) / vmax
            for pr, oL, oS, cL, cS, buy, sell, tot, d, dom in lv:
                if mode == 2:                                  # split basic
                    _add(cx, buy * sc, pr, self._VP_GREEN)
                    _add(cx - sell * sc, sell * sc, pr, self._VP_RED)
                elif mode == 4:                                # split force (opL green + clS cyan / opS red + clL magenta)
                    _add(cx, oL * sc, pr, self._VP_FCOL[0]); _add(cx + oL * sc, cS * sc, pr, self._VP_FCOL[3])
                    _add(cx - oS * sc, oS * sc, pr, self._VP_FCOL[1]); _add(cx - oS * sc - cL * sc, cL * sc, pr, self._VP_FCOL[2])
                else:                                          # 3 split-basic-delta / 5 split-force-delta -> net signed bar
                    # mode 5: colour by the dominant of ALL FOUR forces AT THIS LEVEL (`dom`), so a net-buy bar can
                    # still be a sell colour if that force dominates the price; mode 3: plain green/red by net side.
                    col = self._VP_FCOL[dom] if mode == 5 else (self._VP_GREEN if d >= 0 else self._VP_RED)
                    if d >= 0:
                        _add(cx, d * sc, pr, col)
                    else:
                        _add(cx - (-d) * sc, (-d) * sc, pr, col)
        return x0s, ws, ys, hs, brs

    def _draw_selection_vp(self, filtered, lo_i, hi_i) -> None:
        """Volume profile over the SELECTED buckets: a force-coloured horizontal histogram anchored at the
        selection's LEFT edge + POC/VAH/VAL/median lines across the span. Gated by the 'h' box + its VP checkbox
        (show_sel_stats AND show_sel_vp). Display-only — nothing here touches the strategy or the buckets."""
        if not self.show_sel_vp or hi_i < lo_i:   # INDEPENDENT of the 'h' box — the VP stays on while show_sel_vp
            self._hide_selection_vp(); return
        sel = filtered[lo_i:hi_i + 1]
        agg = self._sel_vp_hist(sel)
        if len(agg) < 2:
            self._hide_selection_vp(); return
        rows = sorted((float(ps), a) for ps, a in agg.items())
        prices = [p for p, _ in rows]
        if max((sum(a) for _, a in rows), default=0.0) <= 0:
            self._hide_selection_vp(); return
        gaps = sorted(prices[k + 1] - prices[k] for k in range(len(prices) - 1))
        thick = (gaps[len(gaps) // 2] if gaps else (prices[-1] - prices[0]) / max(1, len(prices))) * 0.9
        _x0s, _ws, _ys, _hs, _brs = self._vp_segments(rows, self._vp_mode, float(lo_i), float(hi_i - lo_i), thick)
        if not _ws:
            self._hide_selection_vp(); return
        self.bc_sel_vp.setOpts(x0=_x0s, width=_ws, y=_ys, height=_hs, brushes=_brs)
        self.bc_sel_vp.setVisible(True)
        raw = {}                                               # raw b+s ladder -> POC / value area / median
        for b in sel:
            for ps, vv in (b.get("levels") or {}).items():
                r = raw.get(ps)
                if r is None:
                    r = [0.0, 0.0]; raw[ps] = r
                r[0] += float(vv.get("b", 0.0)); r[1] += float(vv.get("s", 0.0))
        lvl = {ps: {"b": v[0], "s": v[1]} for ps, v in raw.items()}
        try:
            _q = bar_quantiles.vq(lvl); _va = bar_quantiles.value_area(lvl); _poc = bar_quantiles.poc(lvl)
            levels = [_va[1], _va[0], _poc, _q[1], bar_quantiles.lvn(lvl)]   # VAH, VAL, POC, median, LVN (line-colour order)
        except Exception:
            levels = [None, None, None, None, None]
        for _ln, _y in zip(self.bc_sel_vp_lines, levels):
            if _y is not None and _y == _y:
                _ln.setData([float(lo_i), float(hi_i)], [float(_y), float(_y)]); _ln.setVisible(True)
            else:
                _ln.setVisible(False)

    def _on_zone_s_changed(self, _s: float) -> None:
        """User dragged the absorption-zone slider — pin it as a PERSISTED override (sticks across selections +
        sessions; suppresses the adaptive seed) and recompute the bands live."""
        self._zone_user_s = _s
        self._save_ui_state()
        self._refresh_selection_stats()

    def _on_eff_f_changed(self, _f: float) -> None:
        """User dragged the effective-aggression slider — pin it as a PERSISTED override and recompute live."""
        self._eff_user_f = _f
        self._save_ui_state()
        self._refresh_selection_stats()

    def _on_zone_side_filter(self) -> None:
        """A Bull/Bear toggle flipped on either zone slider — persist the choice and recompute the bands now."""
        self._save_ui_state()
        self._sel_sig = None                 # side filter isn't in the aggregate; force a recompute
        self._refresh_selection_stats()


    @staticmethod
    def _hist_side(arr, thr: float, above: bool) -> float:
        """Sum one side of a per-bucket size histogram ``arr`` (over config.SIZE_HIST_EDGES bins) about the qty
        ``thr``, with LOG-LINEAR within-bin interpolation on the straddling bin (the cutoff is RETROACTIVE —
        any slider qty re-sums instantly). above=True -> trades >= thr (LARGE); above=False -> trades < thr
        (SMALL). Empty/missing arr (pre-feature bucket) -> 0.0."""
        if not arr:
            return 0.0
        edges = config.SIZE_HIST_EDGES
        b = config.size_bin(thr)              # straddling bin index (0..len(edges))
        n = len(edges)
        tot = sum(arr[b + 1:]) if above else sum(arr[:b])
        if 0 < b < n:                         # interior bin [edges[b-1], edges[b]) is finite -> interpolate
            lo_e, hi_e = edges[b - 1], edges[b]
            if hi_e > lo_e > 0.0 and hi_e > thr > 0.0:
                frac_above = min(1.0, max(0.0, math.log(hi_e / thr) / math.log(hi_e / lo_e)))
                tot += (frac_above if above else (1.0 - frac_above)) * arr[b]
        return tot

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
        nb = f"{d['n']} buckets · {self._fmt_elapsed(d['t_span'])}"
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
                lines.append(f"{span('Bull ' + K(abu), g if abu >= abe else gray)} · "
                             f"{span('Bear ' + K(abe), r if abe > abu else gray)} · "
                             f"{span(lean, gold)}")   # colour only the dominant side; mute the other
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
                lines.append(f"{span('Bull ' + K(ebu), neon_g if ebu >= ebe else gray)} · "
                             f"{span('Bear ' + K(ebe), neon_r if ebe > ebu else gray)} · "
                             f"{span(elean, gold)}")   # colour only the dominant side; mute the other
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
            # GROUP under the box: VP checkbox (top) + Zone-s slider + Force-f slider, stacked with breathing room.
            # These are hand-positioned (not in a layout), so Qt never auto-sizes them — size each to its sizeHint
            # here (else the taller Bull/Bear row gets compressed onto the slider) and stack by sizeHint, not the
            # possibly-stale .height(). Flip the whole group ABOVE the box if it'd run off the bottom.
            gap = 8
            ch = self.sel_vp_chk.sizeHint().height()
            sh = self.zone_slider.sizeHint().height()
            self.sel_vp_chk.adjustSize(); self.zone_slider.adjustSize(); self.eff_slider.adjustSize()
            total = ch + gap + sh + gap + sh
            top_y = by + self.sel_stats.height() + gap
            if top_y + total > self.height():
                top_y = max(0, by - gap - total)
            for _w, _wy in ((self.sel_vp_chk, top_y), (self.zone_slider, top_y + ch + gap),
                            (self.eff_slider, top_y + ch + gap + sh + gap)):
                _w.move(bx, _wy); _w.show(); _w.raise_()
                if self.menu.isVisible():
                    _w.stackUnder(self.menu)
        else:
            self.sel_stats.hide()
            self._hide_sel_ctrls()              # 'h' off -> hide box + card, but KEEP the VP overlay (independent)

    # ---------------------------------------------------------------- PIVOT INDICATOR (Ctrl+P)
    def _toggle_pivot(self) -> None:
        """Ctrl+P — PIVOT INDICATOR on/off (S5j-r5 confluence detection + entry). Shows over the FULL loaded window
        with no selection drawn; drawing a Mode-10 selection narrows it to that range as an optional focus."""
        self.show_pivot = not self.show_pivot
        if not self.show_pivot:
            self._clear_pivot()
        self._save_ui_state()
        self._sel_sig = None                    # force the selection readout to recompute so marks appear/vanish

    def _toggle_pivot_causal(self) -> None:
        """'N' — NO-LOOK-AHEAD pivot: truncate detection at the selection's right edge so the D-tier / E-held / E2
        badges read only data up to the edge (the true LIVE values), instead of the +FWD forward window that
        settles them. Panels are already causal-to-edge; this makes the pivot honest as you scrub the Right arrow."""
        self.pivot_causal = not self.pivot_causal
        self._pivot_sig = None; self._sel_sig = None            # force a full pivot recompute
        self._save_ui_state()

    def _clear_pivot(self) -> None:
        for _sd in ("long", "short"):
            self.bc_pivot_leaders[_sd].setData([], []); self.bc_pivot_leaders[_sd].setVisible(False)
            self.bc_pivot_conn[_sd].setData([], []); self.bc_pivot_conn[_sd].setVisible(False)
        self.bc_pivot_dots.setData([]); self.bc_pivot_dots.setVisible(False)
        self.bc_pivot_stars.setData([]); self.bc_pivot_stars.setVisible(False)
        self.bc_pivot_traps.setData([]); self.bc_pivot_traps.setVisible(False)
        self.bc_pivot_clocks.setData([]); self.bc_pivot_clocks.setVisible(False)
        self.bc_pivot_vpin.setData([]); self.bc_pivot_vpin.setVisible(False)
        for _lab in self._pivot_label_pool:
            _lab.setVisible(False)
        self._pivot_hovers = []; self.pivot_tooltip.hide()
        self._clear_entry_lines()
        self._pivot_sig = None

    def _pivot_put_label(self, used: int, x, y, text: str, color=(0, 0, 0)) -> int:
        """Set the next pooled D/E glyph (grow lazily): bold letter centred on its circle. ``color`` is the
        glyph colour — black on a FILLED badge, the side colour on a HOLLOW (border-only) D badge."""
        if used >= len(self._pivot_label_pool):
            _t = pg.TextItem(anchor=(0.5, 0.5), color=(0, 0, 0)); _t.setZValue(32)
            _f = QtGui.QFont("Consolas", 8); _f.setBold(True); _t.textItem.setFont(_f)
            self.plot.addItem(_t, ignoreBounds=True); self._pivot_label_pool.append(_t)
        lab = self._pivot_label_pool[used]
        lab.setColor(color); lab.setText(text); lab.setPos(x, y); lab.setVisible(True)
        return used + 1

    def _hm_cycles(self, share, min_len):
        """P2 cycles (runs of `share` on one side of 50%), with NOISE cycles de-noised out: any run shorter than
        min_len buckets is absorbed into its neighbours (its boundary crossings dropped, neighbours coalesced), so
        a brief flick across the midline no longer counts as a cycle. Returns [(a, b), ...] over [0, len-1]. The
        share VALUES are untouched — only which crossings count as cycle boundaries changes."""
        n = len(share)
        if n == 0:
            return []
        cyc = []; i0 = 0; dom = share[0] >= 0.5
        for k in range(1, n):
            dk = share[k] >= 0.5
            if dk != dom:
                cyc.append([i0, k - 1, dom]); i0 = k; dom = dk
        cyc.append([i0, n - 1, dom])
        while len(cyc) > 1:                                      # absorb the shortest sub-min run, repeat
            si = min(range(len(cyc)), key=lambda i: cyc[i][1] - cyc[i][0])
            if (cyc[si][1] - cyc[si][0] + 1) >= min_len:
                break
            cyc[si][2] = not cyc[si][2]                          # flip the noise run to its neighbours' side...
            merged = [cyc[0]]                                    # ...then coalesce adjacent same-side runs
            for c in cyc[1:]:
                if c[2] == merged[-1][2]:
                    merged[-1][1] = c[1]
                else:
                    merged.append(c)
            cyc = merged
        return [(c[0], c[1]) for c in cyc]

    def _render_hm(self, bull_sh, lo, hi, yb, yt, items, badge, label, bt, et, wts, show_times,
                   bcol=_RGB_EFF_BULL, rcol=_RGB_EFF_BEAR, invert_strong=False, ncyc=None, slidable=False,
                   badge_rgb=None) -> None:
        """Shared HM sub-panel renderer. Per-cycle STEP lines (bull HM green / bear HM red, crossing at the 50%
        midline) + a right-edge SPREAD box = HM of the dominant share over the LAST N LOCKED cycles, coloured by
        the net side; + optional per-cycle elapsed-time labels. `wts` = per-bucket weights (None -> equal/volume
        since buckets are equal-volume; per-bucket durations -> TIME-weighted). `items` = (bull,bear,mid,lock,calc)."""
        bull_it, bear_it, mid_it, lock_it, calc_it = items
        ny = len(bull_sh)
        if ny < 2:
            for _it in items:
                _it.setVisible(False)
            self._spread_badges[badge].hide()
            if show_times:
                for _t in self._hm_time_labels:
                    _t.setVisible(False)
                self._hm_time_n = 0
            return
        cyc = self._hm_cycles(bull_sh, self._hm_min_cyc)       # noise cycles merged out

        def _hy(v):
            return yb + v * (yt - yb)                           # share 0..1 -> panel y (50% = midline)

        def _whm(vals, ws):                                    # weighted harmonic mean (ws all-1 -> plain HM)
            num = 0.0; den = 0.0
            for v, w in zip(vals, ws):
                if v > 1e-6:
                    num += w; den += w / v
            return num / den if den > 0 else 0.5

        def _wsl(a, b):
            return [wts[k] for k in range(a, b + 1)] if wts is not None else [1.0] * (b - a + 1)

        bx = []; by = []; rx = []; ry = []
        hb = [0.5] * ny; hr = [0.5] * ny                       # per-bucket HM (the step value) for the hover
        for a, b in cyc:                                        # per-cycle step lines (bull HM green / bear HM red)
            ws = _wsl(a, b)
            bhm = _whm([bull_sh[k] for k in range(a, b + 1)], ws)
            rhm = _whm([1.0 - bull_sh[k] for k in range(a, b + 1)], ws)
            for k in range(a, b + 1):
                hb[k] = bhm; hr[k] = rhm
            x0 = lo + a - 0.5; x1 = lo + b + 0.5               # step held flat across the cycle span
            bx += [x0, x1, float("nan")]; by += [_hy(bhm), _hy(bhm), float("nan")]
            rx += [x0, x1, float("nan")]; ry += [_hy(rhm), _hy(rhm), float("nan")]
        bull_it.setData(bx, by, connect="finite"); bull_it.setVisible(True)
        bear_it.setData(rx, ry, connect="finite"); bear_it.setVisible(True)
        mid_it.setData([lo - 0.5, hi + 0.5], [_hy(0.5), _hy(0.5)]); mid_it.setVisible(True)
        self._draw_panel_lock(lock_it, config.LIVE_PANEL_WINDOW // 2, lo, hi, yb, yt)   # dashed unlocked divider
        self._panel_hovers.append({                            # hover -> the cycle's bull-HM / bear-HM at that bucket
            "label": label, "lo": lo, "yb": yb, "yt": yt, "bull": hb, "bear": hr,
            "bcol": bcol, "rcol": rcol, "blbl": "bull-HM", "rlbl": "bear-HM", "fmt": "pct"})
        # SPREAD BOX: (weighted) HM of the per-bar dominant share over the LAST N LOCKED cycles (fully settled).
        _lockidx = ny - 1 - (config.LIVE_PANEL_WINDOW // 2)    # last settled bar (P2 is a centered window)
        locked = [c for c in cyc if c[1] < _lockidx]           # cycles whose closing cross is already settled
        _ncyc = ncyc if ncyc is not None else self._hm_ncyc    # user override (draggable P1 bar) else the default 2
        if locked:
            l3 = locked[-_ncyc:]; seg0 = l3[0][0]; seg1 = l3[-1][1]   # bar span of the last N locked cycles
            ws = _wsl(seg0, seg1); _wsum = sum(ws) or 1.0
            dom_sh = _whm([bull_sh[k] if bull_sh[k] >= 0.5 else 1.0 - bull_sh[k] for k in range(seg0, seg1 + 1)], ws)
            net_bull = (sum(w * bull_sh[seg0 + i] for i, w in enumerate(ws)) / _wsum) >= 0.5   # (weighted) net side
            _strong_bull = (not net_bull) if invert_strong else net_bull   # absorption: STRONG = the LOWER share
            _bx = hi + 0.5 + max(1.0, (hi - lo + 1) * 0.05)    # just past the panels' right edge (same as the badges)
            _brgb = badge_rgb or ((40, 230, 90), (255, 45, 70))   # badge fill -> match this HM's LINE colours
            self._set_spread_badge(badge, dom_sh, 1.0 - dom_sh, _strong_bull, _bx, (yb + yt) / 2.0,
                                   bull_rgb=_brgb[0], bear_rgb=_brgb[1])
            if slidable:                                       # P1 HM: the amber marker becomes a DRAG handle
                calc_it.setVisible(False)
                self._abshm_snap = [lo + c[0] - 0.5 for c in locked]   # snap targets = each locked-cycle START
                self._abshm_hit = (lo - 0.5, hi + 0.5, yb, yt)         # panel band (double-click here -> reset)
                dl = self.bc_abshm_drag
                dl.blockSignals(True); dl.setValue(lo + seg0 - 0.5); dl.blockSignals(False)
                dl.setBounds([lo + locked[0][0] - 0.5, lo + seg1 + 0.5])
                _xr, _yr = self.vb.viewRange(); _v0, _v1 = _yr
                if _v1 > _v0:                                  # clip the handle's visible span to the HM band
                    dl.setSpan(max(0.0, (yb - _v0) / (_v1 - _v0)), min(1.0, (yt - _v0) / (_v1 - _v0)))
                dl.setVisible(True)
            else:
                calc_it.setData([lo + seg0 - 0.5, lo + seg0 - 0.5], [yb, yt]); calc_it.setVisible(True)   # span START marker
        else:
            self._spread_badges[badge].hide(); calc_it.setVisible(False)
            if slidable:
                self.bc_abshm_drag.setVisible(False); self._abshm_snap = []; self._abshm_hit = None
        if not show_times:
            return
        # PER-CYCLE ELAPSED-TIME labels along the BOTTOM of the panel (locked on; only the primary HM panel gets them).
        pool = self._hm_time_labels; tused = 0
        if bt is not None and et is not None:
            flags = self._cycle_hm_flags(bull_sh, cyc)          # SAME driver as the P2 %-labels -> colours agree
            ytl = yb + (yt - yb) * 0.07                        # bottom edge of the band (matches the P2 %-labels)
            for a, b in cyc:
                if (b - a + 1) < self._eff_cyc_min or b >= len(et) or a >= len(bt):
                    continue
                dur = float(et[b]) - float(bt[a])
                if dur <= 0:
                    continue
                if tused >= len(pool):
                    _t = pg.TextItem(anchor=(0.5, 0.5)); _t.setZValue(33)
                    _f = QtGui.QFont("Consolas", 8); _f.setBold(True); _t.textItem.setFont(_f)
                    self.plot.addItem(_t, ignoreBounds=True); pool.append(_t)
                fl = flags.get((a, b))                          # green/red (per side) if this cycle's HM rose, else white
                is_bull, rose = (fl[0], fl[2]) if fl is not None else (True, False)
                lab = pool[tused]
                lab.setColor(((40, 230, 90) if is_bull else (255, 60, 80)) if rose else (236, 239, 246))
                lab.setText(self._fmt_dur(dur))
                lab.setPos(lo + (a + b) / 2.0, ytl); lab.setVisible(True)
                tused += 1
        self._hm_time_n = tused
        for j in range(tused, len(pool)):
            pool[j].setVisible(False)

    def _draw_eff_cycles(self, bull_sh, lo, hi, yb, yt, bt=None, et=None) -> None:
        """VOLUME-weighted HM sub-panel (toggles with P2/'2'). Equal per-bucket weights + the per-cycle
        elapsed-time labels. Thin wrapper over _render_hm."""
        items = (self.bc_hm_bull, self.bc_hm_bear, self.bc_hm_mid, self.bc_hm_lock, self.bc_hm_calc)
        self._render_hm(bull_sh, lo, hi, yb, yt, items, "EFF-HM", "HM", bt, et, wts=None, show_times=True)

    def _draw_abs_cycles(self, bull_sh, lo, hi, yb, yt) -> None:
        """ABSORPTION HM sub-panel (rides Panel 1 / '1'). Same machinery as _draw_eff_cycles — per-cycle green/
        purple HM step lines, the 50% midline, the dashed NON-LOCKED divider, and the right-edge %-spread box (HM
        of the dominant share over the last 2 LOCKED cycles) — fed by the absorption bull share. No per-cycle
        elapsed-time labels (that TextItem pool is P2's; sharing it would let the two panels clobber each other)."""
        items = (self.bc_abshm_bull, self.bc_abshm_bear, self.bc_abshm_mid, self.bc_abshm_lock, self.bc_abshm_calc)
        self._render_hm(bull_sh, lo, hi, yb, yt, items, "ABS-HM", "aHM", None, None, wts=None, show_times=False,
                        bcol=_RGB_ABS_BULL, rcol=_RGB_ABS_BEAR, invert_strong=True,
                        ncyc=self._abshm_ncyc, slidable=True, badge_rgb=(_RGB_ABS_BULL, _RGB_ABS_BEAR))

    def _hide_abs_cycles(self) -> None:
        self.bc_abshm_bull.setVisible(False); self.bc_abshm_bear.setVisible(False); self.bc_abshm_mid.setVisible(False)
        self.bc_abshm_lock.setVisible(False); self.bc_abshm_calc.setVisible(False)
        self.bc_abshm_drag.setVisible(False); self._abshm_hit = None
        self._spread_badges["ABS-HM"].hide()

    def _abshm_drag_done(self) -> None:
        """P1 HM amber handle released -> snap to the nearest locked-cycle START and average the box over that many
        cycles (from there to the last locked cycle). Redraw re-seats the handle exactly on the boundary."""
        snap = self._abshm_snap
        if not snap:
            return
        x = float(self.bc_abshm_drag.value())
        i = min(range(len(snap)), key=lambda k: abs(snap[k] - x))   # nearest locked-cycle start
        self._abshm_ncyc = max(1, len(snap) - i)                    # that many cycles back to the last locked one
        self._refresh_selection_stats()

    def _cycle_hm_flags(self, bull_sh, cyc):
        """Per-cycle colour driver SHARED by the P2 %-labels and the Ctrl+2 minutes labels so the two panels always
        agree. For each cycle long enough to label (>= _eff_cyc_min bars), returns (is_bull, dominant-share HM, rose)
        keyed by (a, b), where `rose` is True iff this cycle's HM is strictly higher than the previous LABELLED
        cycle's HM (first labelled cycle -> False). Short/degenerate cycles are absent from the map."""
        flags = {}; prev_hm = None
        for a, b in cyc:
            if (b - a + 1) < self._eff_cyc_min:
                continue
            is_bull = (sum(bull_sh[a:b + 1]) / (b - a + 1)) >= 0.5   # net side of the (possibly merged) cycle
            vs = [(bull_sh[k] if is_bull else 1.0 - bull_sh[k]) for k in range(a, b + 1)]
            vs = [v for v in vs if v > 1e-6]
            if not vs:
                continue
            hm = len(vs) / sum(1.0 / v for v in vs)             # harmonic mean of the dominant share
            flags[(a, b)] = (is_bull, hm, prev_hm is not None and hm > prev_hm)
            prev_hm = hm
        return flags

    def _draw_p2_cycle_labels(self, bull_sh, lo, hi, yb, yt) -> None:
        """Per-cycle harmonic-mean % labels along the bottom of P2 ITSELF: under each cycle, the HM of the DOMINANT
        share (0.5-1.0) as a %. GREEN (bull) / RED (bear) when the cycle's HM ROSE vs the previous labelled cycle,
        else WHITE — a strengthening run of cycles lights up, a fading one greys to white. A cycle = a run where one
        force stays dominant (bull share one side of 50%). Cycles under _eff_cyc_min bars go unlabelled."""
        pool = self._eff_cyc_labels; used = 0; ny = len(bull_sh)
        if ny >= 2:
            cyc = self._hm_cycles(bull_sh, self._hm_min_cyc)    # noise cycles merged out (same as the sub-panel)
            flags = self._cycle_hm_flags(bull_sh, cyc)          # (is_bull, HM, rose) -> colour, shared with Ctrl+2
            ylab = yb + (yt - yb) * 0.07                        # just inside the bottom edge of the P2 band
            for a, b in cyc:
                fl = flags.get((a, b))
                if fl is None:
                    continue
                is_bull, hm, rose = fl
                if used >= len(pool):
                    _t = pg.TextItem(anchor=(0.5, 0.5)); _t.setZValue(33)
                    _f = QtGui.QFont("Consolas", 8); _f.setBold(True); _t.textItem.setFont(_f)
                    self.plot.addItem(_t, ignoreBounds=True); pool.append(_t)
                lab = pool[used]
                lab.setColor(((40, 230, 90) if is_bull else (255, 60, 80)) if rose else (236, 239, 246))
                lab.setText("%.0f%%" % (hm * 100.0)); lab.setPos(lo + (a + b) / 2.0, ylab); lab.setVisible(True)
                used += 1
        for j in range(used, len(pool)):
            pool[j].setVisible(False)

    def _hide_eff_hm(self) -> None:
        """Hide ONLY the P2 HM SUB-PANEL (Ctrl+2) — step lines, box, dashed divider, per-cycle time labels. Leaves
        the in-panel per-cycle %-labels alone; those ride the P2 PANEL ('2'), which is toggled independently."""
        self.bc_hm_bull.setVisible(False); self.bc_hm_bear.setVisible(False); self.bc_hm_mid.setVisible(False)
        self.bc_hm_lock.setVisible(False); self.bc_hm_calc.setVisible(False)
        self._spread_badges["EFF-HM"].hide()
        for _t in self._hm_time_labels:
            _t.setVisible(False)
        self._hm_time_n = 0

    def _hide_eff_cycles(self) -> None:
        """Full P2 teardown (selection hidden / leaving Mode 10): the HM sub-panel AND the in-panel cycle labels."""
        self._hide_eff_hm()
        for _t in self._eff_cyc_labels:
            _t.setVisible(False)

    def _pivot_scan(self, filtered):
        """Full Pivot V3 detection over the LOADED set ``filtered``, cached + INCREMENTAL: the settled prefix is
        reused and only a ~FRONT-bar live FRONTIER is re-detected on each new bucket, then spliced. Proven
        byte-identical to a fresh detect_pivots over the whole set (test: incremental eff arrays + raw fires match
        exactly). Re-scans in FULL when the Zero Point / start date moves (``off`` changes) — that's the 'scan the
        newly loaded buckets' case. Returns ``(fires, e_sh, e_sh_c, sum0)`` with fire indices ABSOLUTE into filtered."""
        from app import pivot_detect
        n = len(filtered); off = self._global_idx_offset
        LB = 220                                # detect warm-up (legs 1/5 + phase)
        SETTLE = pivot_detect.WAIT_SECS + 300.0   # a fire is FINAL once its 1h entry-WAIT has elapsed in WALL-CLOCK
        now_t = float(filtered[-1].get("end_time", 0.0)) or float(filtered[-1].get("start_time", 0.0))
        m = n                                   # frozen prefix [0, m): bars whose WAIT window fully closed (settled)
        while m > 0 and (float(filtered[m - 1].get("end_time", 0.0)) or float(filtered[m - 1].get("start_time", 0.0))) >= now_t - SETTLE:
            m -= 1
        c = self._psc
        if c is not None and c["off"] == off and 0 < m <= c["n"] <= n:   # cache covers the frozen prefix -> splice
            base = max(0, m - LB)
            f2, e2, ec2, s2 = pivot_detect.detect_pivots(filtered[base:n], return_eff=True)
            e_sh = c["e_sh"][:m] + list(e2[m - base:])
            e_shc = c["e_shc"][:m] + list(ec2[m - base:])
            sum0 = c["sum0"][:m] + list(s2[m - base:])

            def _sh(f):                          # frontier fires are 0-indexed from `base` -> shift to absolute
                g = dict(f)
                for k in ("det_i", "wait_end_i", "zref_i"):
                    g[k] = f[k] + base
                g["entry_i"] = (f["entry_i"] + base) if f["entry_i"] is not None else None
                return g
            fires = [f for f in c["fires"] if f["det_i"] < m] + [_sh(f) for f in f2 if base + f["det_i"] >= m]
        else:                                    # first draw / anchor moved / big jump -> full scan of the loaded set
            f0, e0, ec0, s0 = pivot_detect.detect_pivots(filtered, return_eff=True)
            e_sh = list(e0); e_shc = list(ec0); sum0 = list(s0)
            fires = [dict(f) for f in f0]        # indices already absolute (base == 0)
        self._psc = {"off": off, "n": n, "e_sh": e_sh, "e_shc": e_shc, "sum0": sum0, "fires": fires}
        return fires, e_sh, e_shc, sum0

    def _draw_pivot(self, filtered, off, lo_i, hi_i, incremental=False) -> None:
        """Scan the selection for a SEQUENCE of S5j-r5 setups: the first fire's detection + entry, then — right
        AFTER that entry — the next detection + entry, and so on, non-overlapping. A cancelled fire (no baseline
        touch within the 1h WAIT) is skipped past its dead hour (so a fired RUN collapses to ONE setup, not one
        mark per bar). Cached by the selection's bucket range so it re-detects only when the bars change."""
        if not self.show_pivot:
            self._clear_pivot(); return
        n = len(filtered)
        sig = (off, lo_i, hi_i, self.pivot_causal, self.menu.layer_state("m10_vpfade"),
               self.menu.layer_state("m10_estar"), self.menu.layer_state("m10_vpinring"))
        if sig == self._pivot_sig:
            return                              # same range -> marks already drawn, keep them
        self._pivot_sig = sig
        from app import pivot_detect
        LB, FWD = 220, 260                      # lookback (legs 1/5 + phase warm) / forward (1h entry scan)
        # NO-LOOK-AHEAD ('N'): stop at the selection edge so the eff-agg driving tier/E-held/E2 uses only data up
        # to hi_i (causal); an entry only shows once it has actually landed by the edge. Else keep the +FWD window.
        if incremental:                         # no-selection: full loaded set via the cached/incremental scanner
            a = 0; b_end = n                     # whole loaded set; fires come back ABSOLUTE (a == 0)
            try:
                _ps = time.perf_counter()
                fires, e_sh, e_sh_c, sum0 = self._pivot_scan(filtered)
                self._perf_note("pivot_scan", _ps)   # profiler: pivot detection cost (Start-Date-change hotspot)
            except Exception:
                self._clear_pivot(); self._pivot_sig = sig; return
        else:                                   # selection: detect only the drawn range (+ lookback), as before
            a = max(0, lo_i - LB); b_end = (hi_i + 1) if self.pivot_causal else min(n, hi_i + 1 + FWD)
            try:
                _ps = time.perf_counter()
                fires, e_sh, e_sh_c, sum0 = pivot_detect.detect_pivots(filtered[a:b_end], return_eff=True)
                self._perf_note("pivot_scan", _ps)
            except Exception:
                self._clear_pivot(); self._pivot_sig = sig; return
        fl = sorted(((a + f["det_i"], (a + f["entry_i"]) if f["entry_i"] is not None else None,
                      a + f["wait_end_i"], f["side"], a + f["zref_i"]) for f in fires), key=lambda t: (t[0], t[3]))
        # INDEPENDENT buy/sell chains: each side keeps its OWN resume pointer, so a buy setup's entry gates
        # only the next BUY and a sell's only the next SELL — the two sequences can overlap in time.
        setups = []; pending = []; scan_from = {"long": lo_i, "short": lo_i}
        _le = hi_i if self.pivot_causal else (n - 1)   # the "live edge": in no-look-ahead ('N') replay it's the
        for det, ent, we, side, zref in fl:            # SELECTION right edge, so D/E/E2 appear + settle bar-by-bar
            if det > hi_i:                             # as you scrub the Right arrow (else the true forming edge).
                break
            if det >= _le:                      # SKIP the still-FORMING edge bucket: its leg 5 reads the UNCLOSED
                continue                        # close, so the fire flickers/un-fires. It shows once the bar closes.
            if det < scan_from[side]:
                continue
            if ent is not None and ent < _le:   # entry on a CLOSED bar -> CONFIRMED fill: settled, can't move.
                setups.append((det, ent, side, zref)); scan_from[side] = ent + 1
            elif (ent is not None and ent >= _le) or we > _le:   # entry on the still-FORMING bar OR the 1h WAIT
                pending.append((det, side, zref)); scan_from[side] = we   # still runs past the edge -> PENDING
            else:                               # CANCELLED: wait fully elapsed, no touch -> don't mark
                scan_from[side] = we
        # DIAGNOSTIC (temporary): a CONFIRMED filled D must never vanish while still on-screen. Detection is
        # cached per close, so this runs only when the marks actually recompute. If a D that was filled last
        # pass is gone now yet still inside the window, append the context to data/pivot_vanish.log — including
        # whether it still RAW-fires (walk drop) or not (detection drop). Offline replay can't reproduce the
        # live case, so this captures ground truth. Tiny append-only file; delete this block once root-caused.
        try:
            raw_abs = {(off + d, s) for d, e, w, s, z in fl}
            pend_abs = {(off + d, s) for d, s, z in pending}
            filled_now = {(off + d, s): off + e for d, e, s, z in setups}
            for (ad, s), pe in getattr(self, "_pivot_shown_filled", {}).items():
                if off <= ad <= off + n - 1 and (ad, s) not in filled_now:
                    with open(os.path.join(config.DATA_DIR, "pivot_vanish.log"), "a") as _lf:
                        _lf.write("%s VANISHED %-5s filled-D idx=%d entry_idx=%d -> now=%s still_raw_fires=%s "
                                  "off=%d n=%d hi_i=%d\n" % (
                                      time.strftime("%Y-%m-%d %H:%M:%S"), s, ad, pe,
                                      "PENDING" if (ad, s) in pend_abs else "GONE",
                                      (ad, s) in raw_abs, off, n, off + hi_i))
            self._pivot_shown_filled = filled_now
        except Exception:
            pass
        if self.pivot_d_only:                          # V3: every fired D is just a D — fold pending into setups
            for _d, _s, _z in pending:                 # (ent=n placeholder -> the E/E2/E3 block is skipped below)
                setups.append((_d, n, _s, _z))
            pending = []
        if not setups and not pending:
            self._pivot_hovers = []; self.pivot_tooltip.hide()
            for _lab in self._pivot_label_pool:
                _lab.setVisible(False)
            self.bc_pivot_dots.setData([]); self.bc_pivot_dots.setVisible(False)
            self.bc_pivot_stars.setData([]); self.bc_pivot_stars.setVisible(False)
            self.bc_pivot_traps.setData([]); self.bc_pivot_traps.setVisible(False)
            self.bc_pivot_clocks.setData([]); self.bc_pivot_clocks.setVisible(False)
            self.bc_pivot_vpin.setData([]); self.bc_pivot_vpin.setVisible(False)
            for _sd in ("long", "short"):
                self.bc_pivot_leaders[_sd].setVisible(False); self.bc_pivot_conn[_sd].setVisible(False)
            self._clear_entry_lines()
            return
        (_vx0, _vx1), (vy0, vy1) = self.vb.viewRange()
        dy = (vy1 - vy0) * 0.08

        def _cl(i): return float(filtered[i].get("close", filtered[i].get("close_price", 0.0)))
        def _op(i): return float(filtered[i].get("open", filtered[i].get("open_price", 0.0)))
        # P2 BACKDROP for the E3 / E-held-2 stage — computed EXACTLY like the HM box (EFF-HM): the net-dominant side
        # of the last 3 LOCKED cycles as-of each bar (a cycle locks once its close is >= LW/2 buckets behind, so it
        # can't repaint). So E3 can only appear where the HM box itself would read in the trade's favour at that bar.
        # _hm_side(bar) -> True (net-bull) / False (net-bear) / None (no locked cycle yet -> box hidden).
        _LOCK = config.LIVE_PANEL_WINDOW // 2
        _le_c = len(e_sh)
        _cyc = self._hm_cycles(e_sh, self._hm_min_cyc)          # noise cycles merged out (same as the HM box)
        _cyc_end = [c[1] for c in _cyc]
        _csum = [0.0]
        for _v in e_sh:
            _csum.append(_csum[-1] + float(_v))
        _netbull = [None] * _le_c; _mp = 0
        for _li in range(_le_c):
            _lockidx = _li - _LOCK                               # last settled bar as-of _li
            while _mp < len(_cyc_end) and _cyc_end[_mp] < _lockidx:
                _mp += 1                                         # _mp = # of cycles whose close is locked
            if _mp == 0:
                continue                                         # no locked cycle -> no backdrop (box hidden)
            _l3 = _cyc[max(0, _mp - self._hm_ncyc):_mp]          # last N LOCKED cycles (matches the HMS box)
            _s0 = _l3[0][0]; _s1 = _l3[-1][1]; _cnt = _s1 - _s0 + 1
            if _cnt > 0:
                _netbull[_li] = ((_csum[_s1 + 1] - _csum[_s0]) / _cnt) >= 0.5

        def _hm_side(bar):
            _l = bar - a
            return _netbull[_l] if 0 <= _l < _le_c else None

        _csum_c = [0.0]                                          # prefix sums of the FIRST-PRINT share (current-HM)
        for _v in e_sh_c:
            _csum_c.append(_csum_c[-1] + float(_v))

        def _cur_side(bar):
            """The CURRENT (forming) cycle's net side as-of `bar`, causal (first-print): net-bull(True)/bear(False)."""
            _l = bar - a
            if not (0 <= _l < _le_c):
                return None
            _ci = bisect.bisect_left(_cyc_end, _l)              # cycle containing _l
            _cs = _cyc[min(_ci, len(_cyc) - 1)][0]
            _cnt = _l + 1 - _cs
            return ((_csum_c[_l + 1] - _csum_c[_cs]) / _cnt) >= 0.5 if _cnt > 0 else None

        def _new_e(det, buy):
            """V3 NEW E: first bar (<=4h from D) where the aligned LOCKED P2 eff-agg spread (settled badge, LOCK
            buckets back) >= 15 AND the HMS is in favour AND the current (forming) HM cycle is also in favour."""
            _t0 = float(filtered[det].get("end_time", 0.0))
            for j in range(det, b_end):                              # start at D to catch the same-candle case
                if float(filtered[j].get("end_time", 0.0)) > _t0 + 4 * 3600.0:
                    break
                _jl = j - a - _LOCK                                   # LOCKED value = LOCK buckets back (settled badge)
                _spr = ((1.0 if buy else -1.0) * (2.0 * float(e_sh[_jl]) - 1.0) * 100.0) if 0 <= _jl < len(e_sh) else 0.0
                if _spr >= 15.0 and _hm_side(j) == buy and _cur_side(j) == buy:
                    return j                                         # first qualifying bar (may be the D bar itself)
            return None

        seg = {"long": ([], []), "short": ([], [])}      # DASHED leaders lx/ly per side
        con = {"long": ([], []), "short": ([], [])}      # SOLID connectors cx/cy per side
        self._pivot_hovers = []; used = 0; spots = []; trade_entries = []; e_ok_live = []   # recorded E's (for live audio)
        faded_e_live = []                                    # faded study E's landing at the live edge (for the "Faded E" audio)
        star_spots = []; trap_spots = []; clock_spots = []; vpin_spots = []   # VPFADE ★/✕/clock + VPIN-confluence ring
        _vpin_on = self.menu.layer_state("m10_vpinring")     # electric-purple ring on entries with VPIN >= warn (ratio>=1)
        # ONE fast causal-tier pass over the window (byte-identical to per-bar cutpoints), then O(1) lookups per badge
        # — instead of re-sorting a 240-window at every D/E. WARN/TOXIC tier == VPIN >= warn == ratio >= 1.0.
        _vptiers = (vpin_adaptive.vpin_tiers_from_series(
            vpin_adaptive.rolling_vpin(filtered, config.VPIN_WINDOW))[0]) if _vpin_on else None

        def _vpin_elev(i):
            """VPIN ratio >= 1.0 at bar i: its causal adaptive tier is WARN or TOXIC (at/above the warn cutpoint)."""
            return _vptiers is not None and 0 <= i < len(_vptiers) and _vptiers[i] != vpin_adaptive.NORMAL

        _usedE = {"long": set(), "short": set()}   # dedup: one New-E per bar per side (many D's can converge on one E)
        _usedEf = {"long": set(), "short": set()}  # dedup for FADED study E's (kept separate so a taken E is never dropped)

        def _e3_entry(cand, buy, shelf, side, faded, html):
            """E3 / E-held-2: if the live P2 HM backdrop is AGAINST the trade at the candidate entry (E-held / E2),
            wait for the first later bar (<=4h) whose backdrop flips IN FAVOUR and mark it 'E3' — that becomes the
            trade entry, so its SL/+0.10%/+0.40% re-anchor to E3 (via _entry_sl, entry-referenced) just like E2.
            Backdrop already agreeing (or no flip within the window) -> keep the candidate as the entry."""
            nonlocal used
            _sd = _hm_side(cand)
            if _sd is None or _sd == buy:
                return cand                                       # no backdrop info, or already in favour -> no E3
            et_c = float(filtered[cand].get("end_time", 0.0)); e3 = None
            for j in range(cand + 1, b_end):                      # backdrop is AGAINST -> wait for a definite flip
                if et_c and float(filtered[j].get("end_time", 0.0)) > et_c + 4 * 3600.0:
                    break
                _sj = _hm_side(j)
                if _sj is not None and _sj == buy:
                    e3 = j; break
            if e3 is None or e3 >= n:
                return cand
            _rgb = (40, 230, 90) if buy else (255, 45, 70)
            e3_spot = {"pos": (e3, shelf), "brush": pg.mkBrush(*_rgb), "pen": pg.mkPen(0, 0, 0, 180)}
            e3_letter = (0, 0, 0)
            if faded:
                self._pivot_fade_spot(e3_spot); e3_letter = PIVOT_FADE_RGB
            used = self._pivot_put_label(used, e3, shelf, "E3", e3_letter)
            spots.append(e3_spot)
            self._pivot_hovers.append((e3, shelf, html, buy))
            _lx, _ly = seg[side]; _cx, _cy = con[side]; _fld = "low" if buy else "high"
            _lx += [e3, e3, float("nan")]; _ly += [float(filtered[e3].get(_fld, 0.0)), shelf, float("nan")]
            _cx += [cand, e3, float("nan")]; _cy += [shelf, shelf, float("nan")]
            return e3

        for det, ent, side, zref in setups:
            buy = side == "long"; fld = "low" if buy else "high"
            side_rgb = (40, 230, 90) if buy else (255, 45, 70)       # green buy / red sell
            brush = pg.mkBrush(*side_rgb)                            # E badge (+ filled very-high D) fill
            # PIVOT-ZZTRAIL-v2 fade: dim the whole setup (D + E/E2 glyphs) if the strategy would SKIP it. Overlay-only.
            faded = (ent < n) and not self.pivot_d_only and not self._pivot_v2_taken(filtered, det, ent, buy, e_sh_c, a, n, b_end)
            lx, ly = seg[side]; cx, cy = con[side]
            tips = [float(filtered[det].get(fld, 0.0))]
            if ent < n:
                tips.append(float(filtered[ent].get(fld, 0.0)))
            shelf = (min(tips) - dy) if buy else (max(tips) + dy)
            gid = off + det
            # D badge fill = the FROZEN first-print P2 spread AT D (V3 tier). HOLLOW (border only) = <=63; FILLED
            # side colour = >63 (top-1/3); cyan(buy)/orange(sell) = >80 (strongest).
            di = det - a
            sd = ((2.0 * float(e_sh_c[di]) - 1.0) * 100.0) if 0 <= di < len(e_sh_c) else 0.0   # FIRST-PRINT (frozen)
            p2d = sd if buy else -sd                                 # aligned (+ve = with the trade)
            tier = "cyan" if p2d > PIVOT_P2D_VHIGH else ("green" if p2d > PIVOT_P2D_HIGH else "hollow")  # D fill tier
            _z3 = None; step3 = False; _ne = None; e_ok = False     # V3: which D's/E's are RECORDED entries (kept bright)
            if self.pivot_d_only and self.pivot_v3_filter:
                _z3 = self._zone5_at(float(filtered[det].get("end_time", 0.0)), _cl(det))
                step3 = _pivot_v3_take(buy, tier, _z3)              # Path A: cyan/orange + directional 4H zone (enter at D)
                if self.pivot_new_e and not step3:                 # Path B: the OTHER D's hunt the New E
                    _ne = _new_e(det, buy)
                    if _ne == det:                                 # E = D (same candle) -> setup skipped entirely
                        continue
                    if _ne is not None:
                        _ez = self._zone5_at(float(filtered[_ne].get("end_time", 0.0)), _cl(_ne))
                        e_ok = _pivot_v3_e_take(buy, tier, _z3, _ez)   # is this E one of the RECORDED combos?
                        if e_ok:
                            if _ne in _usedE[side]:                # dedup: one recorded E per bar per side
                                continue
                            _usedE[side].add(_ne)
                faded = not (step3 or e_ok)                        # fade everything that is NOT a recorded D or E entry
            # D-entry VP STAR / TRAP + cyan wait-CLOCK (overlay; toggle m10_vpfade). A real D-ENTRY (Step-3 Path A =
            # cyan/orange tier + directional zone; `step3`) earns a gold ★ if its forming-VP is a good bin, or a red ✕
            # if it is the TRAP bin: BUY trap = lower-VA (data-backed), SELL trap = upper-VA (NOT data-backed — user's
            # structural choice; the study said Sell's worst bin is above-VAH). Star bins are the other three (both
            # value-area edges above-VAH/below-VAL always star; interior leans to the trade's own break). A cyan/orange
            # D that FAILS Step 3 (non-directional zone -> it drops to Path-B and hunts a New-E) gets a wait-CLOCK
            # instead. Pure highlight, no trade change; frozen V3 detection untouched. None VP -> no mark.
            _d_star = _d_trap = _d_clock = False
            if self.menu.layer_state("m10_vpfade"):
                if step3:                                       # cyan/orange + directional -> real D-entry: ★ or ✕
                    _dtrap = "lower VA" if buy else "upper VA"
                    _dvp = self._vpform_bin_at(filtered, det)
                    if _dvp == _dtrap:
                        _d_trap = True
                    elif _dvp is not None:
                        _d_star = True
                elif tier == "cyan" and self.pivot_d_only and self.pivot_v3_filter:   # cyan, non-directional -> Path-B
                    _d_clock = True                              # this cyan/orange D is an E-hunter, not a D-entry
            _tname = {"cyan": ("CYAN" if buy else "ORANGE"),        # tier colour is side-specific
                      "green": ("GREEN" if buy else "RED"),
                      "hollow": ("HOLLOW GREEN" if buy else "HOLLOW RED")}[tier]
            if self.pivot_d_only:                                   # V3: lightweight D-only hover (no E/E2 trajectory)
                html = ("<div style='font-family:Consolas; font-size:11px; color:#c8ccd4; padding:1px 3px'>"
                        "<b style='color:%s'>%s-P_%s</b> &nbsp;<b>%s</b><br><b>N</b>: "
                        "<b style='color:#e8ebf0'>%d</b> bars &nbsp; P2@<b>D</b>: <span style='color:%s'>%+.0f%%</span></div>"
                        ) % ("#28e65a" if buy else "#ff5566", "B" if buy else "S", self._fmt_idx(gid),
                             _tname, (det - zref) if 0 <= zref < n else 0,
                             "#28e65a" if p2d >= 0 else "#ff5566", p2d)
            else:
                html = self._pivot_stats_html(filtered, det, ent, zref, buy, gid, n, _cl, _op, e_sh_c, sum0, a)
            if p2d > PIVOT_P2D_VHIGH:                                # very high (strongest D) -> filled cyan/orange
                _f = (0, 200, 255) if buy else (255, 145, 0)
                d_spot = {"pos": (det, shelf), "brush": pg.mkBrush(*_f), "pen": pg.mkPen(0, 0, 0, 180)}; d_letter = (0, 0, 0)
            elif p2d > PIVOT_P2D_HIGH:                               # high (D beats E) -> filled side colour
                d_spot = {"pos": (det, shelf), "brush": brush, "pen": pg.mkPen(0, 0, 0, 180)}; d_letter = (0, 0, 0)
            else:                                                    # default (E better) -> hollow, side colour
                d_spot = {"pos": (det, shelf), "brush": pg.mkBrush(0, 0, 0, 0),
                          "pen": pg.mkPen(side_rgb, width=2)}; d_letter = side_rgb
            if faded:                                              # V3: dim any D that is not a recorded entry (v2 fade unchanged)
                self._pivot_fade_spot(d_spot); d_letter = PIVOT_FADE_RGB
            used = self._pivot_put_label(used, det, shelf, "D", d_letter)   # DETECTION badge
            spots.append(d_spot)
            if _vpin_elev(det):                  # VPIN confluence -> electric-purple ring on EVERY D badge (any tier/fade)
                vpin_spots.append({"pos": (det, shelf)})
            if _d_star or _d_trap or _d_clock:   # D mark just outside the coin, away from the candle (buy below / sell above)
                _dy_ann = (shelf - dy * 0.5) if buy else (shelf + dy * 0.5)
                (star_spots if _d_star else trap_spots if _d_trap else clock_spots).append({"pos": (det, _dy_ann)})
            self._pivot_hovers.append((det, shelf, html, buy))
            lx += [det, det, float("nan")]; ly += [float(filtered[det].get(fld, 0.0)), shelf, float("nan")]
            if 0 <= zref < n:                    # dashed leader from the badge back to the leg-5 (N=60..100)
                lx += [det, zref, float("nan")]; ly += [shelf, _op(zref), float("nan")]   # -> reference open
            # NEW E badge: BRIGHT for a RECORDED combo (e_ok, a real V3 entry); DIM for a FADED study D so its E +
            # position sim can still be eyeballed. Study E's dedup separately so a taken E is never dropped by one.
            _draw_e = None
            if e_ok and _ne is not None and det < _ne < n:
                _draw_e = _ne                                      # recorded combo (already deduped in the filter phase)
            elif (not e_ok) and _ne is not None and det < _ne < n \
                    and _ne not in _usedE[side] and _ne not in _usedEf[side]:
                _usedEf[side].add(_ne); _draw_e = _ne             # faded study E (kept dim, no cross-drop of a taken E)
            if _draw_e is not None:
                _ergb = (40, 230, 90) if buy else (255, 45, 70)
                e_spot = {"pos": (_draw_e, shelf), "brush": pg.mkBrush(*_ergb), "pen": pg.mkPen(0, 0, 0, 180)}
                e_letter = (0, 0, 0)
                if not e_ok:                                       # study E -> dim badge + grey letter
                    self._pivot_fade_spot(e_spot); e_letter = PIVOT_FADE_RGB
                used = self._pivot_put_label(used, _draw_e, shelf, "E", e_letter)
                spots.append(e_spot)
                if _vpin_elev(_draw_e):          # VPIN confluence -> ring on EVERY E badge (recorded OR faded)
                    vpin_spots.append({"pos": (_draw_e, shelf)})
                self._pivot_hovers.append((_draw_e, shelf, html, buy))
                lx += [_draw_e, _draw_e, float("nan")]; ly += [float(filtered[_draw_e].get(fld, 0.0)), shelf, float("nan")]
                cx += [det, _draw_e, float("nan")]; cy += [shelf, shelf, float("nan")]
                trade_entries.append((_draw_e, buy, shelf, not e_ok, "B"))   # Path B = fixed bracket exit; fade = study E
                if e_ok:
                    e_ok_live.append((_draw_e, buy))               # recorded E entry -> candidate for the live audio
                else:
                    faded_e_live.append((_draw_e, buy))            # faded study E -> candidate for the "Faded E" live audio
                # E VP-edge STAR / TRAP (overlay; toggle m10_estar) — UNVALIDATED study aid on EVERY drawn E (recorded
                # or faded). E is the MIRROR of the D: it wants its OWN value-half (Buy ★ = lower-VA / below-VAL;
                # Sell ★ = above-VAH / upper-VA), and the OPPOSITE half is the trap (red ✕). Pure highlight, no trade
                # change; frozen V3 untouched. NOT data-backed (E cohorts breakeven/negative) — for eyeballing only.
                if self.menu.layer_state("m10_estar"):
                    _evp = self._vpform_bin_at(filtered, _draw_e)
                    _eown = ("lower VA", "below VAL") if buy else ("above VAH", "upper VA")
                    _etrap = ("upper VA", "above VAH") if buy else ("lower VA", "below VAL")
                    _ey = (shelf - dy * 0.5) if buy else (shelf + dy * 0.5)
                    if _evp in _eown:
                        star_spots.append({"pos": (_draw_e, _ey)})
                    elif _evp in _etrap:
                        trap_spots.append({"pos": (_draw_e, _ey)})
            if step3 and det < n:                                  # V3 Step-3 (Path A) = direct entry at D
                trade_entries.append((det, buy, shelf, False, "A"))   # Path A = D-EXIT (ride opp-D), anchored at the D
            if ent < n and not self.pivot_d_only:   # ENTRY badge (E/E2/E3) — GATED OFF in V3 D-only mode
                # E is a VALID entry only if panel-2 HELD to E (aligned live spread @E > 0 AND its min over
                # [D,E] > -50). If it FLIPPED, the E badge GREYS OUT and the indicator hunts E2 = the first
                # later bar (within 1h) whose live spread RE-CONFIRMS to >= PIVOT_E2_MIN (rescues the flip).
                _di, _ei, _sg = det - a, ent - a, (1.0 if buy else -1.0)
                if 0 <= _di < len(e_sh_c) and 0 <= _ei < len(e_sh_c):
                    _liv = [_sg * (2.0 * float(e_sh_c[k]) - 1.0) * 100.0 for k in range(_di, _ei + 1)]   # first-print
                    e_held = _liv[-1] > 0.0 and min(_liv) > -50.0
                else:
                    e_held = True
                if e_held:                       # green/red fill, black border
                    e_brush, e_pen = brush, pg.mkPen(0, 0, 0, 180)
                else:                            # FLIPPED -> gray fill + ORANGE border (don't-take flag)
                    e_brush, e_pen = pg.mkBrush(120, 120, 120), pg.mkPen(255, 145, 0, width=2.5)
                e_spot = {"pos": (ent, shelf), "brush": e_brush, "pen": e_pen}; e_letter = (0, 0, 0)
                if faded:
                    self._pivot_fade_spot(e_spot); e_letter = PIVOT_FADE_RGB
                used = self._pivot_put_label(used, ent, shelf, "E", e_letter)
                spots.append(e_spot)
                self._pivot_hovers.append((ent, shelf, html, buy))
                lx += [ent, ent, float("nan")]; ly += [float(filtered[ent].get(fld, 0.0)), shelf, float("nan")]
                cx += [det, ent, float("nan")]; cy += [shelf, shelf, float("nan")]
                if e_held and tier == "hollow":  # hollow + E held -> trade entry (E, or E3 if HM backdrop is against)
                    trade_entries.append((_e3_entry(ent, buy, shelf, side, faded, html), buy, shelf, faded, "A"))
                if not e_held:                   # gray E -> look for the E2 re-confirmation entry
                    et_e = float(filtered[ent].get("end_time", 0.0)); e2 = None
                    for j in range(ent + 1, b_end):
                        if et_e and float(filtered[j].get("end_time", 0.0)) > et_e + 3600.0:
                            break
                        jj = j - a
                        if 0 <= jj < len(e_sh_c):
                            _v = (2.0 * float(e_sh_c[jj]) - 1.0) * 100.0        # first-print re-confirm
                            if (_v if buy else -_v) >= PIVOT_E2_MIN:
                                e2 = j; break
                    if e2 is not None and e2 < n:
                        html_e2 = self._pivot_stats_html(filtered, det, e2, zref, buy, gid, n, _cl, _op,
                                                         e_sh_c, sum0, a, ent)   # E2 box: flip@E -> re-conf@E2
                        e2_spot = {"pos": (e2, shelf), "brush": brush, "pen": pg.mkPen(0, 0, 0, 180)}
                        e2_letter = (0, 0, 0)
                        if faded:
                            self._pivot_fade_spot(e2_spot); e2_letter = PIVOT_FADE_RGB
                        used = self._pivot_put_label(used, e2, shelf, "E2", e2_letter)
                        spots.append(e2_spot)
                        self._pivot_hovers.append((e2, shelf, html_e2, buy))
                        lx += [e2, e2, float("nan")]; ly += [float(filtered[e2].get(fld, 0.0)), shelf, float("nan")]
                        cx += [ent, e2, float("nan")]; cy += [shelf, shelf, float("nan")]
                        # E2 = trade entry, unless the HM backdrop is against here -> then E3 (the flip) is the entry
                        trade_entries.append((_e3_entry(e2, buy, shelf, side, faded, html_e2), buy, shelf, faded, "A"))
        # PENDING live D's: a fire whose entry hasn't landed yet (1h WAIT still open at the edge). Distinct
        # AMBER ring = "waiting for entry"; it auto-completes into the full D(+E) setup on the next re-detect
        # once price touches the baseline. Side colour = direction; no E/connector yet.
        for det, side, zref in pending:
            buy = side == "long"; fld = "low" if buy else "high"
            fill = pg.mkBrush(40, 230, 90) if buy else pg.mkBrush(255, 45, 70)
            lx, ly = seg[side]
            dtip = float(filtered[det].get(fld, 0.0)); shelf = (dtip - dy) if buy else (dtip + dy)
            gid = off + det; di = det - a
            sd = ((2.0 * float(e_sh_c[di]) - 1.0) * 100.0) if 0 <= di < len(e_sh_c) else 0.0   # FIRST-PRINT (frozen)
            p2d = sd if buy else -sd
            phtml = ("<div style='font-family:Consolas; font-size:11px; color:#c8ccd4; padding:1px 3px'>"
                     "<b style='color:%s'>%s-P_%s</b><br><b>N</b>: <b style='color:#e8ebf0'>%d</b> bars<br>"
                     "P2@<b>D</b>: <span style='color:%s'>%+.0f%%</span><br>"
                     "<b style='color:#ffb400'>&#9203; waiting for entry</b></div>"
                     ) % ("#28e65a" if buy else "#ff5566", "B" if buy else "S", self._fmt_idx(gid),
                          (det - zref) if 0 <= zref < n else 0,
                          "#28e65a" if p2d >= 0 else "#ff5566", p2d)
            if self.pivot_causal:                        # replay: show the LIVE tier badge (hollow/filled, repaints
                if p2d > PIVOT_P2D_VHIGH:                # as you scrub) inside an amber ring = still waiting for entry
                    _pf = (0, 200, 255) if buy else (255, 145, 0); _pbr = pg.mkBrush(*_pf); _plt = (0, 0, 0)
                elif p2d > PIVOT_P2D_HIGH:
                    _pbr = fill; _plt = (0, 0, 0)
                else:                                    # hollow: transparent centre, side-colour letter
                    _pbr = pg.mkBrush(0, 0, 0, 0); _plt = (40, 230, 90) if buy else (255, 45, 70)
                used = self._pivot_put_label(used, det, shelf, "D", _plt)
                spots.append({"pos": (det, shelf), "brush": _pbr, "pen": pg.mkPen(255, 180, 0, width=2.5)})  # amber ring
            else:
                used = self._pivot_put_label(used, det, shelf, "D")
                spots.append({"pos": (det, shelf), "brush": fill, "pen": pg.mkPen(255, 180, 0, width=2.5)})  # amber
            self._pivot_hovers.append((det, shelf, phtml, buy))
            lx += [det, det, float("nan")]; ly += [dtip, shelf, float("nan")]
        for j in range(used, len(self._pivot_label_pool)):
            self._pivot_label_pool[j].setVisible(False)
        self.bc_pivot_dots.setData(spots); self.bc_pivot_dots.setVisible(bool(spots))
        self.bc_pivot_stars.setData(star_spots); self.bc_pivot_stars.setVisible(bool(star_spots))
        self.bc_pivot_traps.setData(trap_spots); self.bc_pivot_traps.setVisible(bool(trap_spots))
        self.bc_pivot_clocks.setData(clock_spots); self.bc_pivot_clocks.setVisible(bool(clock_spots))
        self.bc_pivot_vpin.setData(vpin_spots); self.bc_pivot_vpin.setVisible(bool(vpin_spots))
        for _sd in ("long", "short"):
            _lx, _ly = seg[_sd]; _cx, _cy = con[_sd]
            self.bc_pivot_leaders[_sd].setData(_lx, _ly, connect="finite")
            self.bc_pivot_leaders[_sd].setVisible(bool(_lx))
            self.bc_pivot_conn[_sd].setData(_cx, _cy, connect="finite")
            self.bc_pivot_conn[_sd].setVisible(bool(_cx))
        self._pivot_n = n
        # LIVE audio: "Enter Buy/Sell E now" the instant a recorded Path-B E entry lands on the just-closed
        # live-edge bucket (live mode only; causal replay truncates detection so nothing sits at the true edge).
        # Seed once on enable so a pre-existing edge E doesn't blast; then one voice per new bucket.
        if self._pivot_audio_on and not self.pivot_causal and n:
            _etn = float(filtered[-1].get("end_time", 0.0))
            if not self._pivot_e_seeded:
                self._pivot_e_seeded = True; self._pivot_e_spoken.add(_etn)
            elif _etn not in self._pivot_e_spoken:
                # ONE E voice per new bucket: a RECORDED E ("Enter E now") takes priority; else a FADED study E
                # that landed on the just-closed edge speaks "Faded Buy/Sell E".
                _said = None
                for _eb, _buy in e_ok_live:
                    if float(filtered[_eb].get("end_time", 0.0)) == _etn:
                        _said = f"Enter {'Buy' if _buy else 'Sell'} E now"; break
                if _said is None:
                    for _eb, _buy in faded_e_live:
                        if float(filtered[_eb].get("end_time", 0.0)) == _etn:
                            _said = f"Faded {'Buy' if _buy else 'Sell'} E"; break
                if _said is not None:
                    self._pivot_e_spoken.add(_etn)
                    self.alerts.audio.speak(_said, gated=False)
        d_bars = {"long": set(), "short": set()}       # D-print timeline (drives D-EXIT: opposite-D TP / same-D trail)
        for _d, _e, _s, _z in setups:
            d_bars[_s].add(_d)
        for _d, _s, _z in pending:                     # (empty in D-only mode; folded into setups above)
            d_bars[_s].add(_d)
        self._build_entry_overlay(filtered, off, trade_entries, d_bars)

    # ------------------------------------------------------------------
    @staticmethod
    def _last_swing(lab, j, buy):
        """Price of the last CONFIRMED swing low (long) / high (short) known by bar j (confirm_bar <= j), any
        label (HL or LL / HH or LH). `lab` = [(pivot_bar, price, is_high, confirm_bar, label)]."""
        want_high = not buy; lvl = None
        for _pb, p, ih, cb, _l in lab:
            if cb > j:
                continue
            if ih == want_high:
                lvl = p
        return lvl

    def _entry_sl(self, lab, eb, buy, epx):
        """D-EXIT initial stop: 0.1% below the last CONFIRMED swing low (long) / above the last confirmed swing
        high (short) known at entry (HL or LL / HH or LH, confirm_bar <= eb). Fallback 0.3%."""
        lvl = self._last_swing(lab, eb, buy)
        if lvl is None:
            return epx * (1 - 0.003) if buy else epx * (1 + 0.003)
        return lvl * (1 - 0.001) if buy else lvl * (1 + 0.001)

    def _entry_sim(self, lab, H, L, C, eb, buy, sl0, entry, d_bars):
        """Forward-simulate the V3 D-EXIT from entry eb: FIXED structural stop (NO auto-trail); TAKE PROFIT when
        an OPPOSITE-side D prints (close at that bar); when a SAME-side D prints, ratchet the stop to 0.1% below
        the last confirmed swing low / above swing high (tighten only). No fixed TP, no breakeven lock.
        -> (ratchets [(same_D_bar, level)], exit_bar, max reached price, exit gross%, reason in {oppD,SL,edge}).
        Still open at the data edge -> gross is the m2m."""
        n = len(H)
        opp = d_bars["short" if buy else "long"]; same = d_bars["long" if buy else "short"]
        sl = sl0; maxpx = entry; ratchets = []; exit_bar = n - 1; gross = None; reason = "edge"
        for j in range(eb + 1, n):
            maxpx = max(maxpx, H[j]) if buy else min(maxpx, L[j])
            if (L[j] <= sl) if buy else (H[j] >= sl):                    # fixed / same-D-trailed stop hit intrabar
                exit_bar = j; gross = ((sl - entry) if buy else (entry - sl)) / entry * 100.0; reason = "SL"; break
            if j in opp:                                                 # opposite-side D prints -> take profit at close
                exit_bar = j; gross = ((C[j] - entry) if buy else (entry - C[j])) / entry * 100.0; reason = "oppD"; break
            if j in same:                                               # same-side D prints -> ratchet the stop
                lvl = self._last_swing(lab, j, buy)
                if lvl is not None:
                    new = lvl * (1 - 0.001) if buy else lvl * (1 + 0.001)
                    new = max(sl, new) if buy else min(sl, new)          # tighten only
                    if abs(new - sl) > 1e-12:
                        ratchets.append((j, new))                        # a same-D that RAISED the stop
                    sl = new
        if gross is None:                                               # still open at the data edge -> mark to market
            gross = ((C[n - 1] - entry) if buy else (entry - C[n - 1])) / entry * 100.0
        return ratchets, exit_bar, maxpx, gross, reason

    @staticmethod
    def _entry_sim_bracket(H, L, C, eb, buy, sl, tp, entry):
        """Path-B exit: a PURE fixed bracket — flat 0.2% stop / +0.6% target from entry. SL checked first intrabar.
        -> ([], exit_bar, max reached price, gross%, reason in {TP,SL,edge}). Open at the edge -> m2m."""
        n = len(H); maxpx = entry; exit_bar = n - 1; gross = None; reason = "edge"
        for j in range(eb + 1, n):
            maxpx = max(maxpx, H[j]) if buy else min(maxpx, L[j])
            if (L[j] <= sl) if buy else (H[j] >= sl):                    # flat stop hit (checked first)
                exit_bar = j; gross = ((sl - entry) if buy else (entry - sl)) / entry * 100.0; reason = "SL"; break
            if (H[j] >= tp) if buy else (L[j] <= tp):                    # fixed take-profit hit
                exit_bar = j; gross = ((tp - entry) if buy else (entry - tp)) / entry * 100.0; reason = "TP"; break
        if gross is None:
            gross = ((C[n - 1] - entry) if buy else (entry - C[n - 1])) / entry * 100.0
        return [], exit_bar, maxpx, gross, reason

    def _build_entry_overlay(self, filtered, off, trade_entries, d_bars) -> None:
        """Per trade entry, forward-sim the PER-PATH exit + store it, then (re)draw. Path A (direct-D) = D-EXIT
        (structural stop, opposite-D take-profit, same-D ratchets; needs `d_bars` = the D-print timeline). Path B
        (New-E) = a flat 0.2% SL / +0.6% TP bracket (its TP renders as a light-blue zone). trade_entries carry the
        path tag; `d_bars` = {'long': set(bars), 'short': set(bars)}."""
        if not trade_entries:
            self._clear_entry_lines(); return
        H = [float(b.get("high", 0.0)) for b in filtered]; L = [float(b.get("low", 0.0)) for b in filtered]
        C = [float(b.get("close", b.get("close_price", 0.0))) for b in filtered]
        swc = structure._zigzag_confirmed(H, L, structure.ZIGZAG_PCT / 100.0)
        lab = []; ph = pl = None                                       # label swings + carry the confirm bar
        for pb, p, ih, cb in swc:
            if ih:
                lv = "HH" if (ph is not None and p > ph) else ("LH" if ph is not None else None); ph = p
            else:
                lv = "HL" if (pl is not None and p > pl) else ("LL" if pl is not None else None); pl = p
            lab.append((pb, p, ih, cb, lv))
        ent = []
        for eb, buy, shelf, fade, path in trade_entries:
            epx = C[eb]
            if path == "B":                                            # Path B (New-E) -> flat 0.2% SL / +0.6% TP bracket
                sl = epx * (1 - 0.002) if buy else epx * (1 + 0.002)
                tp = epx * (1 + 0.006) if buy else epx * (1 - 0.006)
                trails, exit_bar, maxpx, gross, reason = self._entry_sim_bracket(H, L, C, eb, buy, sl, tp, epx)
            else:                                                      # Path A (direct-D) -> D-EXIT (ride opp-D)
                sl = self._entry_sl(lab, eb, buy, epx); tp = None
                trails, exit_bar, maxpx, gross, reason = self._entry_sim(lab, H, L, C, eb, buy, sl, epx, d_bars)
            ent.append((off + eb, eb, shelf, epx, sl, reason, tp, buy, trails, maxpx, exit_bar, gross, fade, path))
        ent.sort(key=lambda e: e[1])
        self._pivot_entries = ent
        self._draw_entry_lines()

    def _clear_entry_lines(self) -> None:
        for _l in self._entry_line_pool:
            _l.setVisible(False)
        for _t in self._entry_lbl_pool:
            _t.setVisible(False)
        for _z in self._entry_zone_pool:
            _z.setVisible(False)
        self.bc_entry_active.setVisible(False)
        self._pivot_entries = []

    def _entry_default_key(self):
        """NO entry shows its position sim by DEFAULT — the overlay starts hidden on every D and E (returning
        None makes `key == last_key` False for every real key). Click a badge to toggle that entry's lines on;
        click again to hide. (Was: the last RECORDED non-faded entry auto-showed.)"""
        return None

    def _draw_entry_lines(self) -> None:
        """Render the PER-PATH exit overlay for each entry effectively ON (user toggle, else default = last entry).
        Common: entry white solid; MAX reached green solid (+%); outcome ring (red loser / white flat / green winner)
        + a tag. **Path A (D-EXIT):** fixed structural SL yellow; same-D stop RATCHETS blue dashed; on a definitive
        exit the EXIT line is highlighted and the rest drop to 60% opacity — TP@opposite-D = NEON PURPLE line, stop =
        yellow SL. **Path B (bracket):** flat 0.2% SL yellow + the TP drawn as an ELECTRIC-BLUE no-border BOX
        spanning +0.6%..+0.7% (fills at 0.6%). Lines run to the EXIT bar (min 60-bar)."""
        entries = self._pivot_entries
        last_key = self._entry_default_key()   # last RECORDED entry, else the last faded one (never blank)
        LINE_W = 60                                                 # minimum line width in bars (quick-stop floor)
        RTAG = {"oppD": "TP @ opp-D", "TP": "TP +0.6%", "SL": "stop", "edge": "open"}
        used_l = used_t = used_z = 0; ring_spots = []
        for e in entries:
            key, eb, epx = e[0], e[1], e[3]
            on = self._entry_lines_user.get(key, key == last_key)   # faded study E (e[12]) never defaults on -> click its badge
            if not on:
                continue
            buy = e[7]; sgn = 1.0 if buy else -1.0                  # % trade-aligned (short SL reads negative)
            gross = e[11]
            rcol = (255, 30, 70) if gross < 0.0 else ((255, 255, 255) if gross < 0.2 else (30, 255, 100))
            ring_spots.append({"pos": (eb, e[2]), "size": 30, "brush": pg.mkBrush(0, 0, 0, 0),
                               "pen": pg.mkPen(rcol, width=2.8)})    # red loser / green winner / white ~flat
            rb = min(max(e[10], eb + LINE_W), self._pivot_n - 1)    # right = exit bar, floored to a min width
            reason = e[5]; path = e[13]
            exit_px = epx * (1.0 + (1.0 if buy else -1.0) * gross / 100.0)
            # (left_bar, level, colour, dash, show_%, is_exit) — is_exit line stays full, others dim to 60% on exit
            lines = [(eb, e[3], (235, 235, 235), None, False, False),     # entry — white SOLID
                     (eb, e[4], (255, 220, 0), None, True, reason == "SL"),   # SL — yellow (exit if stopped)
                     (eb, e[9], (40, 230, 90), None, True, False)]        # MAX reached price — green SOLID (+%)
            if path == "B":                                        # Path B bracket: +0.6% TP = LIGHT-BLUE no-border ZONE
                exited = False                                     # (no dim for the bracket view — the zone is the highlight)
                if used_z >= len(self._entry_zone_pool):
                    _zn = pg.PlotCurveItem(pen=pg.mkPen(None)); _zn.setZValue(5)   # behind the price action
                    self.plot.addItem(_zn, ignoreBounds=True); self._entry_zone_pool.append(_zn)
                _zn = self._entry_zone_pool[used_z]; used_z += 1
                tp_lo = epx * (1 + sgn * 0.006); tp_hi = epx * (1 + sgn * 0.007)    # target BOX = +0.6% .. +0.7%
                _zn.setData([eb, rb], [tp_hi, tp_hi]); _zn.setFillLevel(tp_lo)      # electric-blue band between them
                _zn.setBrush(pg.mkBrush(0, 150, 255, 34)); _zn.setPen(pg.mkPen(None)); _zn.setVisible(True)
                if used_t >= len(self._entry_lbl_pool):
                    _tz = pg.TextItem(anchor=(0.0, 0.5)); _tz.setZValue(33)
                    _tf = QtGui.QFont("Consolas", 10); _tf.setBold(True); _tz.textItem.setFont(_tf)
                    self.plot.addItem(_tz, ignoreBounds=True); self._entry_lbl_pool.append(_tz)
                _tz = self._entry_lbl_pool[used_t]; used_t += 1
                _tz.setColor((70, 175, 255)); _tz.setText("TP 0.6-0.7%"); _tz.setPos(rb, tp_hi); _tz.setVisible(True)
            else:                                                  # Path A D-EXIT: ratchets + purple opp-D line + dim
                exited = reason in ("oppD", "SL")
                for pb, lvl in e[8]:                               # same-D stop ratchets — left attached to that D
                    lines.append((pb, lvl, (70, 150, 255), [4.0, 7.0], True, False))
                if reason == "oppD":                              # TAKE PROFIT @ opposite-D -> NEON PURPLE exit line
                    lines.append((eb, exit_px, (200, 80, 255), None, True, True))
            # exit-reason tag at the exit bar (on the badge shelf row, clear of the price lines)
            if used_t >= len(self._entry_lbl_pool):
                _tg = pg.TextItem(anchor=(0.0, 0.5)); _tg.setZValue(33)
                _tf = QtGui.QFont("Consolas", 10); _tf.setBold(True); _tg.textItem.setFont(_tf)
                self.plot.addItem(_tg, ignoreBounds=True); self._entry_lbl_pool.append(_tg)
            _tg = self._entry_lbl_pool[used_t]; used_t += 1
            _tgcol = (200, 80, 255) if reason == "oppD" else ((150, 200, 255) if reason == "TP" else rcol)
            _tg.setColor(_tgcol); _tg.setText(RTAG.get(reason, "")); _tg.setPos(rb, e[2]); _tg.setVisible(True)
            for lb, lvl, col, dash, pct, emph in lines:
                a = 255 if (not exited or emph) else 153            # 60% opacity on the non-exit lines once exited
                if used_l >= len(self._entry_line_pool):
                    _ln = pg.PlotCurveItem(); _ln.setZValue(27)
                    self.plot.addItem(_ln, ignoreBounds=True); self._entry_line_pool.append(_ln)
                _ln = self._entry_line_pool[used_l]; used_l += 1
                pen = pg.mkPen(col + (a,), width=(2.4 if emph else 1.6)); pen.setCosmetic(True)
                if dash:
                    pen.setDashPattern(dash)
                _ln.setPen(pen); _ln.setData([lb, rb], [lvl, lvl]); _ln.setVisible(True)
                if used_t >= len(self._entry_lbl_pool):
                    _tl = pg.TextItem(anchor=(0.0, 0.5)); _tl.setZValue(33)   # anchor left -> label on the RIGHT
                    _tf = QtGui.QFont("Consolas", 10); _tf.setBold(True); _tl.textItem.setFont(_tf)
                    self.plot.addItem(_tl, ignoreBounds=True); self._entry_lbl_pool.append(_tl)
                _tl = self._entry_lbl_pool[used_t]; used_t += 1
                txt = ("%.2f (%+.2f%%)" % (lvl, sgn * (lvl - epx) / epx * 100.0)) if pct else ("%.2f" % lvl)
                _tl.setColor(col + (a,)); _tl.setText(txt); _tl.setPos(rb, lvl); _tl.setVisible(True)
        self.bc_entry_active.setData(ring_spots); self.bc_entry_active.setVisible(bool(ring_spots))
        for _j in range(used_l, len(self._entry_line_pool)):
            self._entry_line_pool[_j].setVisible(False)
        for _j in range(used_t, len(self._entry_lbl_pool)):
            self._entry_lbl_pool[_j].setVisible(False)
        for _j in range(used_z, len(self._entry_zone_pool)):
            self._entry_zone_pool[_j].setVisible(False)

    def _pivot_stats_html(self, filtered, det, ent, zref, buy, gid, n, _cl, _op, e_sh=None, sum0=None, sl0=0,
                          e_flip=None) -> str:
        """Precomputed hover stats for a setup. N = bars back to the leg-5 reference candle; N->det = that
        reference OPEN to the detection CLOSE %; det->entry = detection CLOSE to entry CLOSE %; room ratio =
        profit-room / risk-room from the ENTRY to the leg-5 zone hi/lo, side-aware (the study's
        profit_room_ratio; >1 = favourable geometry). Then the PANEL-2 (eff-agg) spread TRAJECTORY over the
        entry wait [D,E], ALIGNED (+ve = still WITH the trade), sliced at sl0: the value AT entry E and the min
        over [D,E], plus a HELD/FLIPPED verdict. Study (in-sample): winners hold P2 spr@E positive & never
        breach -50 (p<0.001). (P0 dropped from the box -- weaker & redundant with P2.)"""
        sc = "#28e65a" if buy else "#ff5566"
        c_det = _cl(det)
        has_ref = 0 <= zref < n
        N = det - zref if has_ref else 0
        o_ref = _op(zref) if has_ref else 0.0
        d_ndet = ((c_det - o_ref) / o_ref * 100.0) if o_ref else 0.0
        d_dentry = 0.0; ratio_s = "&mdash;"
        if ent < n:
            c_ent = _cl(ent)
            d_dentry = ((c_ent - c_det) / c_det * 100.0) if c_det else 0.0
            zlo0, zhi0 = det - 99, det - 58                       # leg-5 zone [det-99, det-59], N=60..100
            if zlo0 >= 0:
                zhi = max(float(filtered[k].get("high", 0.0)) for k in range(zlo0, zhi0))
                zlo = min(float(filtered[k].get("low", 0.0)) for k in range(zlo0, zhi0))
                zhp = (zhi - c_ent) / c_ent * 100.0; zlp = (zlo - c_ent) / c_ent * 100.0
                pr, ar = (zhp, -zlp) if buy else (-zlp, zhp)      # profit room / risk room, side-aware
                if abs(ar) > 1e-9:
                    ratio_s = "<b style='color:#e8ebf0'>%.2f : 1</b>" % (pr / ar)

        def _pc(v):
            return "<span style='color:%s'>%+.2f%%</span>" % ("#28e65a" if v >= 0 else "#ff5566", v)

        # spread TRAJECTORY over the entry wait [D, E], aligned (+ve = still WITH the trade), for P2 (eff-agg)
        # and P0 (composite SUM). value @E + min over [D,E] + a HELD/FLIPPED verdict. P2 verdict = @E>0 AND
        # min>-50 (the strong signal); P0 verdict = min>-50 (the operator's floor; weaker, redundant w/ P2).
        di, ei = det - sl0, ent - sl0

        def _traj(series, tag, kind):
            if series is None or not (ent >= det and 0 <= di < len(series) and 0 <= ei < len(series)):
                return ""
            sg = 1.0 if buy else -1.0
            if kind == "eff":                                    # P2 eff-agg share -> spread
                vals = [sg * (2.0 * float(series[k]) - 1.0) * 100.0 for k in range(di, ei + 1)]
                held = vals[-1] > 0.0 and min(vals) > -50.0
            else:                                                # P0 composite SUM (already a spread)
                vals = [sg * float(series[k]) for k in range(di, ei + 1)]
                held = min(vals) > -50.0
            v_e, v_min = vals[-1], min(vals)
            ecol = "#28e65a" if v_e > 0 else "#ff5566"
            mcol = "#ff5566" if v_min <= -50.0 else "#c8ccd4"
            vcol = "#28e65a" if held else "#ff5566"
            vtxt = ("%s HELD &#10003;" % tag) if held else ("%s FLIPPED &#10007;" % tag)
            return ("%s @<b>E</b>: <b style='color:%s'>%+.1f%%</b> &nbsp; min: <b style='color:%s'>%+.1f%%</b>"
                    "<br><b style='color:%s'>%s</b><br>") % (tag, ecol, v_e, mcol, v_min, vcol, vtxt)

        traj = _traj(e_sh, "P2", "eff")          # P0 dropped from the box (weaker + redundant with P2)
        if traj:
            traj = "<span style='color:#5a6070'>&#8213;&#8213;&#8213;</span><br>" + traj
        # E2 box (this entry is a RE-CONFIRMATION after a flip at the original E): the [D,E2] verdict would
        # still read FLIPPED (it dipped past -50 before recovering), so show the honest two-part story instead.
        if e_flip is not None and e_sh is not None:
            _di, _fi, _e2i = det - sl0, e_flip - sl0, ent - sl0
            if 0 <= _di <= _fi < len(e_sh) and 0 <= _e2i < len(e_sh):
                _sg = 1.0 if buy else -1.0
                _dip = min(_sg * (2.0 * float(e_sh[k]) - 1.0) * 100.0 for k in range(_di, _fi + 1))  # [D,E] low
                _pr = _sg * (2.0 * float(e_sh[_e2i]) - 1.0) * 100.0                                   # spr @E2
                traj = ("<span style='color:#5a6070'>&#8213;&#8213;&#8213;</span><br>"
                        "P2: dipped %s &rarr; re-conf @<b>E2</b> %s") % (_pc(_dip), _pc(_pr))

        # D->E WAIT (time + buckets). Study (in-sample, n=116): the farther E is from D the likelier a LOSS
        # (time strongest, r=-0.25 p=0.007). Fast entry -> green, slow -> red (soft readout, not a hard gate).
        et_d = float(filtered[det].get("end_time", 0.0)); et_e = float(filtered[ent].get("end_time", 0.0))
        wmin = (et_e - et_d) / 60.0 if (ent >= det and et_e and et_d) else 0.0
        wcol = "#28e65a" if wmin <= 7.0 else ("#ff5566" if wmin > 15.0 else "#c8ccd4")

        elab = "E2" if e_flip is not None else "E"   # this box describes the E2 entry when e_flip is set
        return ("<div style='font-family:Consolas; font-size:11px; color:#c8ccd4; padding:1px 3px'>"
                "<b style='color:%s'>%s-P_%s</b><br>"
                "<b>N</b>: <b style='color:#e8ebf0'>%d</b> bars<br>"
                "<b>N</b>&rarr;<b>D</b>: %s<br><b>D</b>&rarr;<b>%s</b>: %s<br>"
                "wait: <b style='color:%s'>%.1fm &middot; %d bk</b><br>room ratio: %s<br>%s</div>"
                ) % (sc, "B" if buy else "S", self._fmt_idx(gid), N, _pc(d_ndet), elab, _pc(d_dentry),
                     wcol, wmin, ent - det, ratio_s, traj)

    def _hover_pivot(self, scene_pos) -> None:
        """Stats box for whichever D/E badge the cursor is over — buy -> box BELOW the badge, sell -> box ABOVE.
        Hit-tests each badge centre within a pixel radius (matches the circle); hidden when none is hovered."""
        if not self.show_pivot or not self._pivot_hovers:
            self.pivot_tooltip.hide(); return
        R = 13.0                                  # px hit radius (~the coin)
        for x, y, html, buy in self._pivot_hovers:
            c = self.vb.mapViewToScene(QtCore.QPointF(float(x), float(y)))
            if (c.x() - scene_pos.x()) ** 2 + (c.y() - scene_pos.y()) ** 2 > R * R:
                continue
            if buy:                              # box hangs BELOW the badge (down the screen)
                self.pivot_tooltip.anchor = pg.Point(0.5, 0.0)
                p = self.vb.mapSceneToView(QtCore.QPointF(c.x(), c.y() + R + 2))
            else:                                # box sits ABOVE the badge
                self.pivot_tooltip.anchor = pg.Point(0.5, 1.0)
                p = self.vb.mapSceneToView(QtCore.QPointF(c.x(), c.y() - R - 2))
            self.pivot_tooltip.setHtml(html)
            self.pivot_tooltip.setPos(p.x(), p.y())
            self.pivot_tooltip.show()
            return
        self.pivot_tooltip.hide()

    def _refresh_selection_stats(self) -> None:
        """Live Magic-Selection readout: aggregate the buckets inside the box + show the stats box.
        Runs each frame, so a selection reaching the live edge updates as buckets form."""
        rect = self.drawer.selection_rect()
        if rect is None or self.scanner_mode != "bucket_canvas":
            self.sel_stats.hide()
            self._hide_sel_ctrls()
            self._hide_selection_vp()
            self._hide_flip()
            self.bc_absorp_zones.setVisible(False)
            self.bc_eff_zones.setVisible(False)
            self.bc_abs_strip.setVisible(False)
            self.bc_exh_strip.setVisible(False); self.bc_exh_mid.setVisible(False)
            self.bc_eff_strip.setVisible(False)
            self.bc_er_strip.setVisible(False)
            for _lk in (self.bc_abs_lock, self.bc_eff_lock, self.bc_er_lock, self.bc_exh_lock,
                        self.bc_abs_mid, self.bc_eff_mid, self.bc_er_mid,
                        self.bc_abs_q, self.bc_eff_q, self.bc_er_q):
                _lk.setVisible(False)
            self.bc_panel_sep.setVisible(False)
            self._clear_largesmall_panels()                                  # LARGE/SMALL panels: clear on teardown
            self._clear_panel9()                                              # composite panel: clear on teardown
            self._clear_panel0()        # smoothed twin: clear on teardown (pivot handled below so its sig-gate holds)
            self._hide_eff_cycles(); self._hide_abs_cycles()   # P2 + P1 HM sub-panels: clear on selection teardown too
            for _b in self._spread_badges.values():
                _b.hide()
            self.phase_tbl.hide()
            for _ly in self.bc_phase.values():
                _ly.setVisible(False)
            for _plk in self.bc_phase_lock.values():
                _plk.setVisible(False)
            self._panel_hovers = []
            self._sel_sig = None        # Fix 1: hidden -> force a full recompute when a selection returns
            self._sel_hi_t = None       # no selection -> the 4h zone reverts to the LIVE newest wick (+ fill%)
            # Pivot V3 shows WITHOUT a selection: scan the loaded set incrementally. Do NOT _clear_pivot() first —
            # that resets _pivot_sig EVERY frame and forces a full re-detect each frame (the lag). Left intact, the
            # sig-gate holds, so this re-detects ONLY when a new bucket closes.
            if self.scanner_mode == "bucket_canvas" and self.show_pivot:
                _pf, _, _ = self._build_scanner_buckets()
                if _pf:
                    try:
                        self._draw_pivot(_pf, self._global_idx_offset, 0, len(_pf) - 1, incremental=True)
                    except Exception:
                        self._clear_pivot()
            else:
                self._clear_pivot()      # pivot toggled off / not Mode 10 -> clear it (sig reset here is fine)
            return
        filtered, _x, _a = self._build_scanner_buckets()
        if not filtered:
            self.sel_stats.hide()
            self._hide_sel_ctrls()
            self._hide_selection_vp()
            self._hide_flip()
            self.bc_absorp_zones.setVisible(False)
            self.bc_eff_zones.setVisible(False)
            self.bc_abs_strip.setVisible(False)
            self.bc_exh_strip.setVisible(False); self.bc_exh_mid.setVisible(False)
            self.bc_eff_strip.setVisible(False)
            self.bc_er_strip.setVisible(False)
            for _lk in (self.bc_abs_lock, self.bc_eff_lock, self.bc_er_lock, self.bc_exh_lock,
                        self.bc_abs_mid, self.bc_eff_mid, self.bc_er_mid,
                        self.bc_abs_q, self.bc_eff_q, self.bc_er_q):
                _lk.setVisible(False)
            self.bc_panel_sep.setVisible(False)
            self._clear_largesmall_panels()                                  # LARGE/SMALL panels: clear on teardown
            self._clear_panel9()                                              # composite panel: clear on teardown
            self._clear_panel0(); self._clear_pivot()   # smoothed twin + pivot marks: clear on selection teardown
            self._hide_eff_cycles(); self._hide_abs_cycles()   # P2 + P1 HM sub-panels: clear on selection teardown too
            for _b in self._spread_badges.values():
                _b.hide()
            self.phase_tbl.hide()
            for _ly in self.bc_phase.values():
                _ly.setVisible(False)
            for _plk in self.bc_phase_lock.values():
                _plk.setVisible(False)
            self._panel_hovers = []
            self._sel_sig = None        # Fix 1: hidden -> force a full recompute when a selection returns
            self._sel_hi_t = None       # no selection -> the 4h zone reverts to the LIVE newest wick (+ fill%)
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
                 self._ls_mode, self.show_phase_table, self.show_panel9, self.show_panel0,
                 self.show_abs_hm, self.show_eff_hm, self._abshm_ncyc),   # HM toggles + P1 span drag -> re-render
                (self.zone_slider.value_s(), self.eff_slider.value_s(),
                 self.zone_slider.sides(), self.eff_slider.sides(),   # Bull/Bear zone filters -> re-render on toggle
                 self._largesmall_thr_sig()), tv, config.VPIN_ADAPT_WINDOW)
            if sig == self._sel_sig:
                self._reposition_sel_box(rect)   # reuse last frame's overlays; just keep the box glued
                return
            self._sel_sig = sig
            self._sel_hi_t = float(filtered[hi_i].get("end_time", 0.0))   # scrub 'as-of' edge -> the 4h zone reads it
            try:
                self._draw_pivot(filtered, self._global_idx_offset, lo_i, hi_i)   # PIVOT INDICATOR (Ctrl+P)
            except Exception:
                self._clear_pivot()
            try:
                self._draw_selection_vp(filtered, lo_i, hi_i)   # 'h'-card Volume-Profile-over-selection overlay
            except Exception:
                self._hide_selection_vp()
        else:
            self._sel_sig = None
            self._clear_pivot()
            self._hide_selection_vp()
        agg = self._aggregate_selection(filtered, x0, y0, x1, y1, tv)
        if not agg:
            self.sel_stats.hide()
            self._hide_sel_ctrls()
            self._hide_selection_vp()
            self._hide_flip()
            self.bc_absorp_zones.setVisible(False)
            self.bc_eff_zones.setVisible(False)
            self.bc_abs_strip.setVisible(False)
            self.bc_exh_strip.setVisible(False); self.bc_exh_mid.setVisible(False)
            self.bc_eff_strip.setVisible(False)
            self.bc_er_strip.setVisible(False)
            for _lk in (self.bc_abs_lock, self.bc_eff_lock, self.bc_er_lock, self.bc_exh_lock,
                        self.bc_abs_mid, self.bc_eff_mid, self.bc_er_mid,
                        self.bc_abs_q, self.bc_eff_q, self.bc_er_q):
                _lk.setVisible(False)
            self.bc_panel_sep.setVisible(False)
            self._clear_largesmall_panels()                                  # LARGE/SMALL panels: clear on teardown
            self._clear_panel9()                                              # composite panel: clear on teardown
            self._clear_panel0(); self._clear_pivot()   # smoothed twin + pivot marks: clear on selection teardown
            self._hide_eff_cycles(); self._hide_abs_cycles()   # P2 + P1 HM sub-panels: clear on selection teardown too
            for _b in self._spread_badges.values():
                _b.hide()
            self.phase_tbl.hide()
            for _ly in self.bc_phase.values():
                _ly.setVisible(False)
            for _plk in self.bc_phase_lock.values():
                _plk.setVisible(False)
            self._panel_hovers = []
            self._sel_sig = None        # Fix 1: hidden -> force a full recompute when a selection returns
            self._sel_hi_t = None       # no selection -> the 4h zone reverts to the LIVE newest wick (+ fill%)
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
            self.zone_slider.set_value(self._zone_user_s if self._zone_user_s is not None
                                       else region_state.absorption_default_s(abs_bull_arr, abs_bear_arr, abs_sval))
        s_thr = self.zone_slider.value_s()
        # ABSORPTION ZONES — sustained consecutive heavy-bull/bear runs (>= ABSORP_ZONE_MIN_RUN, s >= slider)
        # -> green/red price x time bands at the run's price range, labelled with absorbed volume, projected
        # (dashed) to the selection's right edge. DESCRIPTIVE; rare (no band = no sustained defense there).
        x_right = hi + 0.5
        zbull, zbear = self.zone_slider.sides()               # Bull/Bear filter for the absorption-zone layer
        zspecs = [(z["start"] - 0.5, z["end"] + 0.5, x_right, z["plo"], z["phi"], z["side"],
                   f"{z['side'].upper()} {self._fmt_k(z['vol'])}")
                  for z in region_state.zones_from_series(
                      abs_bull_arr, abs_bear_arr, abs_sval, lo, filtered,
                      s_thr, config.ABSORP_ZONE_MIN_RUN)
                  if (zbull if z["side"] == "bull" else zbear)]
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
            self.eff_slider.set_value(self._eff_user_f if self._eff_user_f is not None
                                      else region_state.eff_agg_default_f(eff_bull_arr, eff_bear_arr, eff_fval))
        f_thr = self.eff_slider.value_s()
        ebull, ebear = self.eff_slider.sides()                # Bull/Bear filter for the effective-aggression layer
        especs = [(z["start"] - 0.5, z["end"] + 0.5, x_right, z["plo"], z["phi"], z["side"],
                   f"{z['side'].upper()} {self._fmt_k(z['vol'])}")
                  for z in region_state.eff_zones_from_series(
                      eff_bull_arr, eff_bear_arr, eff_fval, lo, filtered,
                      f_thr, config.EFF_AGG_ZONE_MIN_RUN)
                  if (ebull if z["side"] == "bull" else ebear)]
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
        abshm_on = self.show_abs_hm and _drawable         # HM sub-panels are INDEPENDENT of their parent panels
        effhm_on = self.show_eff_hm and _drawable          # (Ctrl+1/Ctrl+2); each takes its own slot in the stack
        er_on = self.show_er_strip and _drawable           # then 5-7 phase panels, 8 LARGE+SMALL mkt, 9 COMPOSITE (BOTTOM)
        exh_on = self.show_exh_strip and _drawable
        ph_on = {p: self.show_phase[p] and _drawable for p in self._PHASES}
        lg_on = self._ls_mode >= 1 and _drawable            # slot 8: '8' cycles 0 hidden / 1 LARGE / 2 LARGE+SMALL
        sm_on = self._ls_mode >= 2 and _drawable            # SMALL only ever shows alongside LARGE
        p9_on = self.show_panel9 and _drawable
        p0_on = self.show_panel0 and _drawable
        ph_geom = {}
        _cur = y0                                           # running bottom edge of the last placed panel
        if abs_on:
            abs_top = _cur - config.ABS_STRIP_GAP * sel_h; abs_bot = abs_top - config.ABS_STRIP_FRAC * sel_h
            _cur = abs_bot
        if abshm_on:                                           # own slot (may show with P1 hidden) — sits where P1's HM goes
            abshm_top = _cur - config.ABS_STRIP_GAP * sel_h
            abshm_bot = abshm_top - config.ABS_STRIP_FRAC * 0.55 * sel_h; _cur = abshm_bot
        if eff_on:
            eff_top = _cur - config.EFF_STRIP_GAP * sel_h; eff_bot = eff_top - config.EFF_STRIP_FRAC * sel_h
            _cur = eff_bot
        if effhm_on:                                           # own slot (may show with P2 hidden)
            hm_top = _cur - config.EFF_STRIP_GAP * sel_h
            hm_bot = hm_top - config.EFF_STRIP_FRAC * 0.55 * sel_h; _cur = hm_bot
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
        if lg_on:                                           # LARGE market-order panel (slot 8a)
            lg_top = _cur - config.EXH_STRIP_GAP * sel_h; lg_bot = lg_top - config.EXH_STRIP_FRAC * sel_h
            _cur = lg_bot
        if sm_on:                                           # SMALL market-order panel (slot 8b, under LARGE)
            sm_top = _cur - config.EXH_STRIP_GAP * sel_h; sm_bot = sm_top - config.EXH_STRIP_FRAC * sel_h
            _cur = sm_bot
        if p9_on:                                           # COMPOSITE lean panel
            p9_top = _cur - config.EXH_STRIP_GAP * sel_h; p9_bot = p9_top - config.EXH_STRIP_FRAC * sel_h
            _cur = p9_bot
        if p0_on:                                           # SMOOTHED twin of panel 9 (very BOTTOM)
            p0_top = _cur - config.EXH_STRIP_GAP * sel_h; p0_bot = p0_top - config.EXH_STRIP_FRAC * sel_h
            _cur = p0_bot
        # minimalist hairline divider in each gap BETWEEN consecutive visible panels (stack order)
        _bands = []
        if abs_on: _bands.append((abs_top, abs_bot))
        if abshm_on: _bands.append((abshm_top, abshm_bot))
        if eff_on: _bands.append((eff_top, eff_bot))
        if effhm_on: _bands.append((hm_top, hm_bot))
        if er_on: _bands.append((er_top, er_bot))
        if exh_on: _bands.append((exh_top, exh_bot))
        for _p in self._PHASES:
            if _p in ph_geom: _bands.append(ph_geom[_p])
        if lg_on: _bands.append((lg_top, lg_bot))
        if sm_on: _bands.append((sm_top, sm_bot))
        if p9_on: _bands.append((p9_top, p9_bot))
        if p0_on: _bands.append((p0_top, p0_bot))
        _sep_ys = [(_bands[i][1] + _bands[i + 1][0]) / 2.0 for i in range(len(_bands) - 1)]
        self.bc_panel_sep.update_data(lo - 0.5, hi + 0.5, _sep_ys)
        self.bc_panel_sep.setVisible(bool(_sep_ys))
        self._panel_hovers = []   # rebuilt each refresh; each visible panel registers its y-band + raw values
        # SELECTION ABSORPTION STRIP ('1', TOP) — bull% vs bear% LEAN, NEON green (bull) / NEON purple (bear).
        # Absorption is one-sided per bucket, so there's no instantaneous ratio; we plot each side's ROLLING
        # share over a centered window (config.LEAN_WINDOW_*) — the two shares sum to 1, cross at the 50% midline
        # (even), and track the LOCAL lean as it SHIFTS across the selection (non-cumulative). SELECTION-PURE
        # (sliced; the zones keep the full-history norm). No envelope, no crossover diamonds.
        # '1' toggles the PANEL, Ctrl+1 the HM — fully independent, so compute the share whenever EITHER is on.
        if abs_on or abshm_on:
            absb, absr, _asv = region_state.absorption_series(
                _extp, 0, _Lp - 1, config.ABSORP_VOL_WINDOW)   # fixed trailing norm over real history (pre-rolled)
            bull_sh = region_state.rolling_share(absb, absr, _lw)[_pre0:]   # drop the pre-roll -> the [lo,hi] view
            bear_sh = [1.0 - s for s in bull_sh]
        if abs_on:                                        # '1' — the absorption lean panel
            def _ay(v):
                return abs_bot + v * (abs_top - abs_bot)  # share 0..1 -> panel y (0% bottom, 50% mid, 100% top)
            xs_a = list(range(lo, hi + 1))
            _ali = len(bull_sh) - 1 - (config.LIVE_PANEL_WINDOW // 2)   # last locked idx (< 0 = selection too short)
            self.bc_abs_strip.update_data(xs_a, [_ay(v) for v in bull_sh], [_ay(v) for v in bear_sh],
                                          lo - 0.5, hi + 0.5, abs_bot, abs_top, [],
                                          _ali if _ali >= 0 else None)
            self.bc_abs_strip.setVisible(True)
            self._draw_panel_refs(self.bc_abs_mid, self.bc_abs_q, lo, hi, abs_bot, abs_top)
            self._draw_panel_lock(self.bc_abs_lock, config.LIVE_PANEL_WINDOW // 2, lo, hi, abs_bot, abs_top)
            self._panel_hovers.append({                # hover -> running bull/bear share %, labelled
                "label": "ABSORPTION", "lo": lo, "yb": abs_bot, "yt": abs_top,
                "bull": bull_sh, "bear": bear_sh, "bcol": _RGB_ABS_BULL, "rcol": _RGB_ABS_BEAR,
                "blbl": "BULL", "rlbl": "BEAR", "fmt": "pct"})
            # absorption: strongest = the LOWER share (per operator) -> bull strong when its share is lower.
            # Badge reads the LOCKED value (last fully-formed bucket), not the settling live edge.
            _abi = _ali if _ali >= 0 else len(bull_sh) - 1
            self._set_spread_badge("ABSORPTION", bull_sh[_abi], bear_sh[_abi], bull_sh[_abi] < bear_sh[_abi],
                                   _badge_x, (abs_top + abs_bot) / 2.0,
                                   bull_rgb=_RGB_ABS_BULL, bear_rgb=_RGB_ABS_BEAR)   # badge = green/purple lines
        else:
            self.bc_abs_strip.setVisible(False)
            self.bc_abs_lock.setVisible(False); self.bc_abs_mid.setVisible(False); self.bc_abs_q.setVisible(False)
            self._spread_badges["ABSORPTION"].hide()
        if abshm_on:                                      # Ctrl+1 — HM step-lines + non-locked line + draggable %-box
            self._draw_abs_cycles(bull_sh, lo, hi, abshm_bot, abshm_top)
        else:
            self._hide_abs_cycles()
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
            self.bc_exh_mid.setData([lo - 0.5, hi + 0.5], [_ey(0.5), _ey(0.5)])   # dashed orange 50% reference
            self.bc_exh_mid.setVisible(True)
            # panel-4 lock-in lag = the symmetric-envelope tail (buckets for a future spike's back-decay to fade)
            _exh_lock = max(1, math.ceil(math.log(0.10) / math.log(config.EXH_RELEASE)))
            self._draw_panel_lock(self.bc_exh_lock, _exh_lock, lo, hi, exh_bot, exh_top)
            # panel-4 badge: dominant exhaustion lead, colored by side (blue bull-exh / red bear-exh)
            _e1, _e2 = sb[-1], sr[-1]
            _bd4 = self._spread_badges["EXHAUSTION"]
            _bd4.fill = pg.mkBrush(*_RGB_EXH_BULL) if _e1 > _e2 else pg.mkBrush(*_RGB_EXH_BEAR)
            _bd4.setText(f" {abs(_e1 - _e2) * 100:.0f}% ")
            _bd4.setPos(_badge_x, (exh_top + exh_bot) / 2.0); _bd4.show()
            self._panel_hovers.append({                # hover -> RAW per-bucket exhaustion %, labelled
                "label": "EXHAUSTION", "lo": lo, "yb": exh_bot, "yt": exh_top,
                "bull": ex_bull, "bear": ex_bear, "bcol": _RGB_EXH_BULL, "rcol": _RGB_EXH_BEAR,
                "blbl": "BULL", "rlbl": "BEAR", "fmt": "pct"})
        else:
            self.bc_exh_strip.setVisible(False); self.bc_exh_mid.setVisible(False); self.bc_exh_lock.setVisible(False)
            self._spread_badges["EXHAUSTION"].hide()
        # SELECTION EFF-AGG STRIP ('2') — bull% vs bear% LEAN (each side's ROLLING share of effective
        # aggression over a centered window), NEON green / NEON red, crossing at the 50% midline; tracks the
        # LOCAL forcing lean as it shifts. One-sided per bucket (like absorption). SELECTION-PURE (sliced; zones
        # keep the full-history norm). No envelope, no crossover diamonds.
        # '2' toggles the PANEL, Ctrl+2 the HM — fully independent, so compute the share whenever EITHER is on.
        if eff_on or effhm_on:
            effb, effr, _efv = region_state.eff_agg_series(
                _extp, 0, _Lp - 1, config.ABSORP_VOL_WINDOW, config.EFF_AGG_FORCE_WINDOW)
            bull_sh = region_state.rolling_share(effb, effr, _lw)[_pre0:]   # drop the pre-roll -> the [lo,hi] view
            bear_sh = [1.0 - s for s in bull_sh]
        if eff_on:                                        # '2' — the eff-agg lean panel (+ its in-panel cycle labels)
            def _fy(v):
                return eff_bot + v * (eff_top - eff_bot)   # share 0..1 -> panel y (0% bottom, 50% mid, 100% top)
            xs_e = list(range(lo, hi + 1))
            _eli = len(bull_sh) - 1 - (config.LIVE_PANEL_WINDOW // 2)   # last locked idx
            self.bc_eff_strip.update_data(xs_e, [_fy(v) for v in bull_sh], [_fy(v) for v in bear_sh],
                                          lo - 0.5, hi + 0.5, eff_bot, eff_top, [],
                                          _eli if _eli >= 0 else None)
            self.bc_eff_strip.setVisible(True)
            self._draw_p2_cycle_labels(bull_sh, lo, hi, eff_bot, eff_top)   # per-cycle HM % under each cycle in P2
            self._draw_panel_refs(self.bc_eff_mid, self.bc_eff_q, lo, hi, eff_bot, eff_top)
            self._draw_panel_lock(self.bc_eff_lock, config.LIVE_PANEL_WINDOW // 2, lo, hi, eff_bot, eff_top)
            self._panel_hovers.append({                # hover -> running bull/bear share %, labelled
                "label": "EFF-AGG", "lo": lo, "yb": eff_bot, "yt": eff_top,
                "bull": bull_sh, "bear": bear_sh, "bcol": _RGB_EFF_BULL, "rcol": _RGB_EFF_BEAR,
                "blbl": "BULL", "rlbl": "BEAR", "fmt": "pct"})
            # eff-agg: strongest = the HIGHER share -> bull strong when its share is higher (LOCKED value)
            _ebi = _eli if _eli >= 0 else len(bull_sh) - 1
            self._set_spread_badge("EFF-AGG", bull_sh[_ebi], bear_sh[_ebi], bull_sh[_ebi] > bear_sh[_ebi],
                                   _badge_x, (eff_top + eff_bot) / 2.0)
        else:
            self.bc_eff_strip.setVisible(False)
            self.bc_eff_lock.setVisible(False); self.bc_eff_mid.setVisible(False); self.bc_eff_q.setVisible(False)
            self._spread_badges["EFF-AGG"].hide()
            for _t in self._eff_cyc_labels:               # in-panel per-cycle %-labels ride the PANEL, not the HM
                _t.setVisible(False)
        if effhm_on:                                      # Ctrl+2 — HM step-lines + box + per-cycle time labels
            _hm_bt = [float(_extp[_pre0 + k].get("start_time", 0.0)) for k in range(len(bull_sh))]   # per-bucket times
            _hm_et = [float(_extp[_pre0 + k].get("end_time", 0.0)) for k in range(len(bull_sh))]     # (cycle durations)
            self._draw_eff_cycles(bull_sh, lo, hi, hm_bot, hm_top, _hm_bt, _hm_et)
        else:
            self._hide_eff_hm()
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
            _rli = len(buy_sh) - 1 - (config.LIVE_PANEL_WINDOW // 2)   # last locked idx
            self.bc_er_strip.update_data(xs_r, [_ry(v) for v in buy_sh], [_ry(v) for v in sell_sh],
                                         lo - 0.5, hi + 0.5, er_bot, er_top, [],
                                         _rli if _rli >= 0 else None)
            self.bc_er_strip.setVisible(True)
            self._draw_panel_refs(self.bc_er_mid, self.bc_er_q, lo, hi, er_bot, er_top)
            self._draw_panel_lock(self.bc_er_lock, config.LIVE_PANEL_WINDOW // 2, lo, hi, er_bot, er_top)
            self._panel_hovers.append({                # hover -> running buy/sell share %, labelled
                "label": "E/R", "lo": lo, "yb": er_bot, "yt": er_top,
                "bull": buy_sh, "bear": sell_sh, "bcol": _RGB_ER_BULL, "rcol": _RGB_ER_BEAR,
                "blbl": "BUY", "rlbl": "SELL", "fmt": "pct"})
            # E/R: strongest = the HIGHER share (spread on the TRUE shares, not the zoomed display) — LOCKED value
            _rbi = _rli if _rli >= 0 else len(buy_sh) - 1
            self._set_spread_badge("E/R", buy_sh[_rbi], sell_sh[_rbi], buy_sh[_rbi] > sell_sh[_rbi],
                                   _badge_x, (er_top + er_bot) / 2.0)
        else:
            self.bc_er_strip.setVisible(False)
            self.bc_er_lock.setVisible(False); self.bc_er_mid.setVisible(False); self.bc_er_q.setVisible(False)
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
            _ti = len(up_traj) - 1 - (config.LIVE_PANEL_WINDOW // 2)   # table shows the LOCKED row (not the settling edge)
            _ti = _ti if _ti >= 0 else -1
            self.phase_tbl.setHtml(self._phase_table_html(up_traj[_ti], dn_traj[_ti]))
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
                    _pli = len(xs_p) - 1 - (config.LIVE_PANEL_WINDOW // 2)   # last locked idx (< 0 = too short)
                    self.bc_phase[_p].update_data(xs_p, up_y, dn_y, lo - 0.5, hi + 0.5, _b, _t, [],
                                                  _pli if _pli >= 0 else None)
                    self.bc_phase[_p].setVisible(True)
                    self._draw_panel_lock(self.bc_phase_lock[_p], config.LIVE_PANEL_WINDOW // 2, lo, hi, _b, _t)
                    self._panel_hovers.append({            # hover -> running UP/DOWN opacity %, labelled
                        "label": _p, "lo": lo, "yb": _b, "yt": _t,
                        "bull": [v / 100.0 for v in up_line], "bear": [v / 100.0 for v in dn_line],
                        "bcol": _RGB_ER_BULL, "rcol": _RGB_ER_BEAR, "blbl": "UP", "rlbl": "DOWN", "fmt": "pct"})
                    # % spread badge (like 1/2/3): the LOCKED UP-vs-DOWN lead, green if UP strongest / red if DOWN
                    _pbi = _pli if _pli >= 0 else len(up_line) - 1
                    self._set_spread_badge(_p, up_line[_pbi] / 100.0, dn_line[_pbi] / 100.0,
                                           up_line[_pbi] >= dn_line[_pbi], _badge_x, (_t + _b) / 2.0)
                else:
                    self.bc_phase[_p].setVisible(False)
                    self.bc_phase_lock[_p].setVisible(False)
                    self._spread_badges[_p].hide()
        else:
            self.phase_tbl.hide()
            for _ly in self.bc_phase.values():
                _ly.setVisible(False)
            for _plk in self.bc_phase_lock.values():
                _plk.setVisible(False)
            for _p in self._PHASES:
                self._spread_badges[_p].hide()
        # LARGE / SMALL MARKET-ORDER PANELS ('8' cycles: hidden / LARGE only / LARGE+SMALL). Each bucket's size
        # histogram (sz_vb/sz_vs = per-side VOLUME, sz_cb/sz_cs = per-side COUNT, over config.SIZE_HIST_EDGES) is
        # thresholded at the daemon's broad 60-min percentile (large=p95, small=p50, NOT selection-local) with
        # log-linear within-bin interpolation, then drawn as a per-bucket DOMINANCE HISTOGRAM. LARGE = large-BUY
        # vs large-SELL VOLUME (blue buy / orange sell, matching the heatmap bubbles); SMALL = small-BUY vs
        # small-SELL trade COUNT (retail breadth; green/red). Cutoffs are FULLY AUTOMATIC (daemon p95 / p50).
        if lg_on:
            large_thr, small_thr = self._largesmall_thresholds()   # FULLY AUTOMATIC: daemon p95 / p50
            lg_buy = [self._hist_side(b.get("sz_vb"), large_thr, True) for b in _extp]
            lg_sell = [self._hist_side(b.get("sz_vs"), large_thr, True) for b in _extp]
            self._draw_hist_panel(self.bc_lg_bars, self.bc_lg_tot,
                                  (self.bc_lg_strip, self.bc_lg_mid, self.bc_lg_q, self.bc_lg_lock,
                                   self.bc_lg_pos, self.bc_lg_neg),
                                  lg_buy, lg_sell, lo, hi, lg_bot, lg_top, "LARGE MKT", self._RGB_LG,
                                  _pre0, _badge_x)
            if sm_on:
                sm_buy = [self._hist_side(b.get("sz_cb"), small_thr, False) for b in _extp]
                sm_sell = [self._hist_side(b.get("sz_cs"), small_thr, False) for b in _extp]
                self._draw_hist_panel(self.bc_sm_bars, self.bc_sm_tot,
                                      (self.bc_sm_strip, self.bc_sm_mid, self.bc_sm_q, self.bc_sm_lock,
                                       self.bc_sm_pos, self.bc_sm_neg),
                                      sm_buy, sm_sell, lo, hi, sm_bot, sm_top, "SMALL MKT", self._RGB_SM,
                                      _pre0, _badge_x)
            else:
                self._clear_sm_panel()
        else:
            self._clear_largesmall_panels()
        # PANEL 9 — COMPOSITE LEAN ('9', very BOTTOM). ONE line = the per-bucket AVERAGE of the FOUR panels'
        # SIGNED spreads (in points). Each spread carries that panel's own badge sign — GREEN(strong-bull)=+,
        # RED(strong-bear)=−: absorption's strong side is the LOWER share -> (bear-bull); eff-agg & E/R's strong
        # side is the HIGHER share -> (bull-bear)/(buy-sell). Panel 4 (exhaustion, GATED so 0 most buckets)
        # contributes +bear% / −bull% and CARRIES FORWARD its last non-zero reading. avg/4, then the SAME sign-
        # split treatment as the liquidation wave: GREEN above the zero line, RED below + a green/red % badge.
        # Recomputed from the same pre-rolled _extp the lean panels use, so it's selection-independent (panels 1-4).
        if p9_on or p0_on:
            _ab, _ar, _ = region_state.absorption_series(_extp, 0, _Lp - 1, config.ABSORP_VOL_WINDOW)
            _a_sh = np.array(region_state.rolling_share(_ab, _ar, _lw)[_pre0:], float)
            s_abs = (1.0 - 2.0 * _a_sh) * 100.0                                       # (bear-bull): lower share strong
            _eb, _erv, _ = region_state.eff_agg_series(_extp, 0, _Lp - 1, config.ABSORP_VOL_WINDOW,
                                                       config.EFF_AGG_FORCE_WINDOW)
            _e_sh = np.array(region_state.rolling_share(_eb, _erv, _lw)[_pre0:], float)
            s_eff = (2.0 * _e_sh - 1.0) * 100.0                                       # (bull-bear): higher share strong
            _rb = [b.get("buyer_er", 0.0) for b in _extp]; _rs = [b.get("seller_er", 0.0) for b in _extp]
            _r_sh = np.array(region_state.rolling_share(_rb, _rs, _lw)[_pre0:], float)
            s_er = (2.0 * _r_sh - 1.0) * 100.0                                        # (buy-sell): higher share strong
            lean = s_abs + s_eff + s_er                                              # SHARED lean (positive = bullish)
            # exhaustion: ONE signed term E = (seller-exh − buyer-exh)·100, carry-forward the last non-zero EVENT
            # (whichever side last fired). The bull line ADDS E, the bear line SUBTRACTS it — so a BUYER (bull)
            # exhaustion (E<0) drags the bull line down AND lifts the bear line; a seller exhaustion does the reverse.
            _ex9 = region_state.trailing_exhaustion(_extp, 0, _Lp - 1, _lw, config.EXH_MEASURE,
                                                    config.EXH_SEL_MIN_WINDOW)
            s_p4 = np.empty(len(_ex9), float); _hold = 0.0
            for _k, (_eb4, _es4) in enumerate(_ex9):
                _inst = (_es4 - _eb4) * 100.0                                         # +seller(bear) / −buyer(bull)
                if abs(_inst) > 1e-9:
                    _hold = _inst
                s_p4[_k] = _hold
            s_p4 = s_p4[_pre0:]
            bull_line = (lean + s_p4) / 4.0                                          # + seller-exh / − buyer-exh
            bear_line = (lean - s_p4) / 4.0                                          # mirror of the exhaustion term
            _sumx = hi + 0.5 + max(6.0, (hi - lo + 1) * 0.18)   # sum-badge x (well right of the pair; shared by 9 + 0)
            if bull_line.size:
                if p9_on:
                    self._draw_lean_lines(bull_line, bear_line, self._bc_p9_items,
                                          ("PANEL9_BULL", "PANEL9_BEAR", "PANEL9_SUM"),
                                          lo, hi, p9_top, p9_bot, "P9", _badge_x, _sumx)
                else:
                    self._clear_panel9()
                if p0_on:
                    # PANEL 0 = each Panel-9 line AVERAGED with its LOCKED (lk-back) value -> a smoothed P9.
                    _lk0 = config.LIVE_PANEL_WINDOW // 2
                    _ix0 = np.maximum(np.arange(len(bull_line)) - _lk0, 0)            # each bucket's locked index (clamped)
                    bull0 = (bull_line + bull_line[_ix0]) / 2.0
                    bear0 = (bear_line + bear_line[_ix0]) / 2.0
                    self._draw_lean_lines(bull0, bear0, self._bc_p0_items,
                                          ("PANEL0_BULL", "PANEL0_BEAR", "PANEL0_SUM"),
                                          lo, hi, p0_top, p0_bot, "P0", _badge_x, _sumx,
                                          sum_only=True, clip_lock=True, tail_item=self.bc_p0_sum_tail,
                                          cross_item=self.bc_p0_cross)
                else:
                    self._clear_panel0()
            else:
                self._clear_panel9(); self._clear_panel0()
        else:
            self._clear_panel9(); self._clear_panel0()
        self.sel_stats.set_content(
            self._selection_stat_lines(agg, state, conf, dbg, vtier,
                                       spark_op, spark_cl, flip,
                                       (abs_bull, abs_bear), (eff_bull, eff_bear)), "")
        try:                                     # confluence alert — panel signals are fresh here; runs only on a
            self._eval_confluence_alert(hi_i >= n_all - 1)   # real change (skip path already returned above)
        except Exception:
            pass
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
                f"Elapsed {self._fmt_elapsed(dur)}   {span('POC '+pf(poc), gold)}",
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
            # adaptive tiers — CAUSAL as-of the hovered bucket (cutpoints from the trailing window ENDING at idx,
            # not the latest window), so the label matches the causally-coloured bar at that bucket and, like the
            # bars, never repaints as later buckets shift the distribution.
            warn_cut, toxic_cut = vpin_adaptive.vpin_cutpoints(
                vpin_adaptive.rolling_vpin(buckets)[max(0, idx - config.VPIN_ADAPT_WINDOW + 1): idx + 1])
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
        _pc = time.perf_counter; _t0 = _pc()                 # session profiler: frame total (negligible)
        snap = self.worker.snapshot()
        self._last_snap = snap
        self._merge_liq_sweeps(snap.get("liq_sweeps"))   # fold in any daemon-pushed 15m sweeps (tf-agnostic)
        _s = _pc(); self._audio_announce(snap); self._audio_announce_pivot(snap)
        self._refresh_scale_labels(snap); self._perf_note("audio_scale", _s)

        # Every mode is bucket-native now (time chart removed, Phase B): draw the scanner, refresh
        # Mode-10 DOM (ungated, bucket_canvas-only — depth pulses independently of the sig-gated
        # _draw_scanner), re-dock the axis badges, breathe the hovered bucket.
        _s = _pc(); self._draw_scanner(); self._perf_note("draw_scanner", _s)
        if self.scanner_mode == "bucket_canvas":
            _s = _pc(); self._update_m10_dom(snap); self._perf_note("m10_dom", _s)
        elif self.scanner_mode == "depth_heatmap" and self.cob.isVisible():
            _s = _pc()
            # Aggregate the ladder to the CURRENT view zoom (~one bar per 2px of the panel), NOT the loaded
            # grid's ybins — so it coarsens as you zoom out / refines toward 1 tick as you zoom in, EVERY
            # frame (the old loaded-band/ybins only changed on a re-request, so it never tracked a zoom).
            (_, _), (vy0, vy1) = self.vb.viewRange()
            bars = max(20, int(self.cob.height()) // 2)
            self.cob.bars.bin_h = max(config.DOM_BIN_STEP, (vy1 - vy0) / bars)
            self.cob.update_depth(snap.get("depth") or {})   # DOM ladder = live book snapshot, price-aligned
            self.cob.autoscale_x(vy0, vy1)                    # bar length scales to the IN-VIEW max wall, so a far
            self._sync_cob()                                  # wall can't flatten the zoomed-in ladder; Y -> band
            self._perf_note("heatmap_cob", _s)
        _s = _pc(); self._redock_trackers(); self._perf_note("redock", _s)
        _s = _pc(); self._refresh_parked_hover(); self._perf_note("parked_hover", _s)
        _s = _pc(); self._refresh_selection_stats(); self._perf_note("selection_stats", _s)   # live Magic-Selection aggregate
        if self._perf is not None:
            self._perf.note_frame((_pc() - _t0) * 1000.0)

    # -- session profiler helpers (client-side lag hunt; all guarded, never crash the UI) ----------
    def _perf_note(self, name: str, start: float) -> None:
        p = self._perf
        if p is not None:
            p.note_section(name, (time.perf_counter() - start) * 1000.0)

    def _perf_flush(self) -> None:
        if self._perf is None:
            return
        try:
            self._perf.flush(self._perf_sample())
        except Exception:
            pass

    def _perf_sample(self) -> dict:
        """Snapshot the session-living accumulators the lag could be hiding in (all best-effort)."""
        s = {"mode": getattr(self, "scanner_mode", "")}
        try:
            s["rss_mb"] = rss_mb()
        except Exception:
            pass
        try:
            s["scene_items"] = len(self.plot.scene().items())      # PRIME suspect: unbounded item growth
        except Exception:
            pass
        try:
            cols = self.hm_cache.cols; yb = int(getattr(self.hm_cache, "ybins", 0) or 0)
            s["hm_cols"] = len(cols); s["hm_cols_kb"] = int(len(cols) * yb * 4 * 2 / 1024)   # col + slog float32
        except Exception:
            pass
        try:
            n = int(len(self.hm_tb_cache.ts)); s["hm_bubbles"] = n; s["hm_bubbles_kb"] = int(n * 25 / 1024)
        except Exception:
            pass
        try:
            s["closed_buckets"] = len((self._last_snap or {}).get("closed_buckets", []))
        except Exception:
            pass
        try:
            d = self.drawer
            s["draw_items"] = len(getattr(d, "shapes", []) or []) + len(getattr(d, "brackets", []) or [])
        except Exception:
            pass
        try:
            s["extra"] = "handles=%d trackers=%d hovers=%d" % (
                len(getattr(self, "_scan_handles", {}) or {}),
                len(getattr(self, "_scan_trackers", {}) or {}),
                len(getattr(self, "_panel_hovers", []) or []))
        except Exception:
            pass
        return s

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

    def _audio_announce_pivot(self, snap) -> None:
        """Speak a LIVE pivot the instant the S5j-r5 confluence FIRES on the just-closed bucket — at D. V3-aware:
        a cyan/orange D (first-print P2>80) in a directional 4H zone = a Path-A DIRECT-D entry -> 'Enter Buy/Sell
        D now'; any other D is drawn FADED (grey) -> 'Faded Buy/Sell D'. (The 'Enter Buy/Sell E now' cue for recorded
        Path-B New-E entries AND the 'Faded Buy/Sell E' cue for faded study E's are spoken from _draw_pivot, which
        computes the E.) Its OWN 'Pivot Alert' toggle, INDEPENDENT of the Audio
        Feed (speaks ungated). LIVE-ONLY: seeded silently on enable / first data / tf-change and gated on a NEW
        bucket close (tracked by the last bucket's end_time); a fire continuing the previous bar's run is skipped."""
        if not self._pivot_audio_on:
            return
        closed = (snap.get("closed_buckets") if snap else None) or []
        if not closed:
            return
        last_et = float(closed[-1].get("end_time", 0.0))
        if not self._pivot_audio_seeded:       # first data / just enabled -> record the edge, DON'T read backlog
            self._pivot_audio_last_et = last_et; self._pivot_audio_seeded = True
            return
        if last_et == self._pivot_audio_last_et:
            return                             # no NEW bucket closed since last check
        self._pivot_audio_last_et = last_et
        win = closed[-350:]                    # live window with the legs-1/5 + phase warm-up context
        if len(win) < 120:
            return
        try:
            from app import pivot_detect
            out, _esh, e_sh_c, _s0 = pivot_detect.detect_pivots(win, return_eff=True)
            fired = {(f["det_i"], f["side"]) for f in out}
        except Exception:
            return
        L = len(win) - 1                       # the just-closed (live-edge) bucket
        for side, spoken in (("long", "Buy"), ("short", "Sell")):
            if (L, side) in fired and (L - 1, side) not in fired:   # NEW fire (not a run continuation)
                buy = side == "long"
                # V3 classify the live D: cyan/orange (first-print P2>80) + directional 4H zone = Path-A DIRECT-D
                # entry -> "Enter D now"; otherwise the D is drawn faded -> "Faded Buy/Sell D".
                p2d = (1.0 if buy else -1.0) * (2.0 * float(e_sh_c[L]) - 1.0) * 100.0
                tier = "cyan" if p2d > PIVOT_P2D_VHIGH else ("green" if p2d > PIVOT_P2D_HIGH else "hollow")
                zone = self._zone5_at(float(win[L].get("end_time", 0.0)),
                                      float(win[L].get("close", win[L].get("close_price", 0.0))))
                if zone is not None and _pivot_v3_take(buy, tier, zone):
                    self.alerts.audio.speak(f"Enter {spoken} D now", gated=False)
                else:
                    self.alerts.audio.speak(f"Faded {spoken} D", gated=False)   # non-take D = a FADED D on screen

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
        """User moved the Zero Point: flush geometry and redraw from the new anchor. In Replay Mode the Start Date
        is the replay START, so re-seat the cursor there before the rebuild."""
        if self._replay_on:
            self._replay_edge_t = self._replay_snap_to_bucket(float(self.menu.scan_start_unix()))
            self._replay_start_t = self._replay_edge_t   # anchor the LEFT edge here; a step moves only the cursor
            self._replay_remember()           # persist the new replay position (debounced)
            self._rdbg("SCAN_TIME_CHANGED replay_on=1 scan_start=%d -> snapped cursor=%s"
                       % (int(self.menu.scan_start_unix()), int(self._replay_edge_t) if self._replay_edge_t else None))
        self.clear_scanner_canvas()
        # The loaded set moved, so EVERYTHING derived from it must re-derive — same invalidation the replay step does.
        # Without this the Pivot D/E marks (sig-gated on offset/range) and the selection kept their last values, so a
        # Start-Date / replay-cursor change only visibly took effect on the next right-arrow step.
        self._scanner_bucket_sig = None       # force a fresh bucket rebuild
        self._last_scanner_sig = None         # force _draw_scanner past its render-skip gate
        self._pivot_sig = None; self._sel_sig = None   # force the Pivot D/E + selection to re-detect on the new frame
        self._psc = None                      # drop the incremental pivot-scan cache (prefix belongs to the old frame)
        self._scanner_needs_autofit = True    # re-fit once to the new window
        if self.scanner_mode == "depth_heatmap":
            self._hm_enter()                  # re-request the heatmap from the new Scan Start Time
        self._on_timer()                      # immediate manual redraw

    def _data_date_range(self):
        """(min QDate, max QDate) of the data the terminal can actually load — the earliest cold-archived bucket
        (falling back to the daemon's oldest live bucket) through today. The date picker disables everything outside
        this so no-data days can't be selected in either normal or replay mode. Bounds are host-local, matching
        scan_start_unix()'s toSecsSinceEpoch()."""
        tf = self.worker.tf if getattr(self, "worker", None) else "1m"
        min_ts = None
        try:
            if archive.available(tf):
                min_ts = archive.earliest_start(tf)
        except Exception:
            min_ts = None
        if min_ts is None:                                   # archive empty -> oldest bucket the daemon still holds
            snap = self._last_snap or (self.worker.snapshot() if getattr(self, "worker", None) else None)
            cb = (snap or {}).get("closed_buckets") or []
            if cb:
                min_ts = float(cb[0].get("start_time", 0.0))
        min_d = QtCore.QDateTime.fromSecsSinceEpoch(int(min_ts)).date() if min_ts else None
        return min_d, QtCore.QDate.currentDate()

    def _on_replay_toggled(self, on: bool) -> None:
        """Replay Mode on/off (default OFF). ON: RESTORE the last replay position (persisted across sessions) into
        the Start Date, then seat the cursor there and redraw a CAUSAL frame ending at it — the whole pipeline
        (candles, VPIN, pivot, selection, HM) reads the clipped frame, so it behaves exactly as live did at that
        moment. OFF: back to the real live edge. Right arrow steps one candle (_on_sel_right)."""
        self._replay_on = bool(on)
        if not on:
            self._replay_stop_autoplay()     # leaving Replay Mode halts any running auto-play
        if on and self._replay_saved_edge_t is not None:
            # pick up exactly where you left off: set the Start Date field SILENTLY (blockSignals so it doesn't
            # double-fire _on_scan_time_changed) so scan_start_unix() returns the remembered time.
            edit = self.menu.scan_time_edit
            edit.blockSignals(True)
            edit.setDateTime(QtCore.QDateTime.fromSecsSinceEpoch(int(self._replay_saved_edge_t)))
            edit.blockSignals(False)
        if not on:
            self._replay_edge_t = None
        # _on_scan_time_changed seats the cursor at the Start Date (when on) and forces the full re-derive + refit.
        self._on_scan_time_changed()
        if on:
            self._save_ui_state()            # persist immediately (also records the just-restored cursor)

    def _replay_remember(self) -> None:
        """Remember the current replay cursor so the next session (after a full restart) picks up exactly here on the
        next toggle-on. Debounced so holding the arrow key coalesces into one disk write."""
        if self._replay_on and self._replay_edge_t is not None:
            self._replay_saved_edge_t = self._replay_edge_t
            self._replay_save_timer.start()

    def _rdbg(self, msg: str) -> None:
        """TEMP replay diagnostics -> the console (stderr) AND data/replay_debug.log. Only when self._replay_dbg is on."""
        if not getattr(self, "_replay_dbg", False):
            return
        try:
            import sys
            print("[REPLAY] " + msg, file=sys.stderr, flush=True)
        except Exception:
            pass
        try:
            import os
            import datetime as _dt
            p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "replay_debug.log")
            with open(p, "a", encoding="utf-8") as f:
                f.write("%s  %s\n" % (_dt.datetime.now().strftime("%H:%M:%S"), msg))
        except Exception:
            pass

    def _replay_snap_to_bucket(self, t: float) -> float:
        """Snap a wall time to the newest CLOSED bucket end_time <= t, so the cursor always sits on a real bar edge."""
        snap = self._last_snap or (self.worker.snapshot() if getattr(self, "worker", None) else None)
        cand = [float(b.get("end_time", 0.0)) for b in ((snap or {}).get("closed_buckets") or [])
                if float(b.get("end_time", 0.0)) <= t]
        return cand[-1] if cand else float(t)

    def _advance_replay(self, step: int = 1) -> None:
        """Reveal the next/prev candle in Replay Mode: move the cursor by `step` closed buckets (Right +1 / Left -1),
        clamped so you can't step past the real live edge. Everything downstream re-derives from the newly-clipped
        frame, so it stays causal."""
        if not self._replay_on or self._replay_edge_t is None:
            return
        snap = self._last_snap or self.worker.snapshot()
        # step through the FULL available sequence — the loaded cold-archive window PLUS the live closed buckets —
        # so an archive-region replay advances bar-by-bar instead of jumping to (or stalling at) the live buffer.
        seq = list(self._arch_win or []) + list(snap.get("closed_buckets") or [])
        ets = sorted({float(b.get("end_time", 0.0)) for b in seq})
        if not ets:
            return
        i = bisect.bisect_left(ets, self._replay_edge_t)   # current cursor bucket (cursor is always a bar edge)
        j = min(max(i + step, 0), len(ets) - 1)
        self._rdbg("ADVANCE step=%d seq=%d(arch=%d) i=%d j=%d cursor %d->%d"
                   % (step, len(ets), len(self._arch_win or []), i, j,
                      int(self._replay_edge_t), int(ets[j])))
        if j != i:
            self._replay_edge_t = ets[j]
            self._replay_remember()           # persist the new position (debounced) so a restart resumes here
            self._scanner_bucket_sig = None; self._last_scanner_sig = None
            self._pivot_sig = None; self._sel_sig = None
            self._on_timer()

    def _toggle_replay_autoplay(self) -> None:
        """Ctrl+Right in Replay Mode: START auto-playing forward (reveal a candle every REPLAY_AUTOPLAY_MS). Pressing
        Ctrl+Right again — or a manual Left/Right step — STOPS it. No-op outside Replay Mode."""
        if not self._replay_on:
            return
        if self._replay_autoplay_timer.isActive():
            self._replay_stop_autoplay()
        else:
            self._replay_autoplay_timer.start()

    def _replay_autoplay_tick(self) -> None:
        """One auto-play frame: advance the cursor +1 (direct, so it doesn't self-stop via _on_sel_right). If the
        cursor didn't move (we hit the real live edge), there's nothing left to play -> stop."""
        before = self._replay_edge_t
        self._advance_replay(1)
        if not self._replay_on or self._replay_edge_t == before:   # replay off, or reached the live edge -> done
            self._replay_stop_autoplay()

    def _replay_stop_autoplay(self) -> None:
        """Halt Ctrl+Right auto-play (idempotent)."""
        if self._replay_autoplay_timer.isActive():
            self._replay_autoplay_timer.stop()

    # ------------------------------------------------------------------
    # Cold-archive GCS fetch-if-missing (on-demand history download)
    # ------------------------------------------------------------------
    def _maybe_pull_archive(self) -> None:
        """The bridging history isn't in the local mirror -> rsync it down from the GCS bucket in the BACKGROUND
        (reusing study/pull_archive.ps1, the tested puller), then re-render. Throttled to ONE pull per
        ARCHIVE_FETCH_COOLDOWN_S on wall time (NOT per-miss — a per-miss key including before_bid drifts on every
        live bucket close and would bypass the cooldown, tight-looping the ~1.8s archive reload). One rsync fetches
        everything on GCS, so time-gating is sufficient. Needs gsutil + gcloud auth; any failure is surfaced, non-fatal."""
        import os
        import time as _t
        if self._arch_pull_active:
            return
        now = _t.monotonic()
        if (now - self._arch_pull_last) < config.ARCHIVE_FETCH_COOLDOWN_S:
            return                                           # a full rsync in the last cooldown already fetched all of GCS
        self._arch_pull_last = now
        self._arch_pull_active = True
        self._show_arch_status("⬇  fetching history from cloud…")
        try:
            os.makedirs(archive.local_dir(), exist_ok=True)
            script = os.path.join(config.PROJECT_DIR, "study", "pull_archive.ps1")
            proc = QtCore.QProcess(self)
            proc.setProgram("powershell")
            proc.setArguments(["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script])
            proc.finished.connect(self._on_archive_pulled)
            proc.errorOccurred.connect(lambda _e: self._on_archive_pulled(-1, None))
            self._arch_pull_proc = proc
            proc.start()
        except Exception:
            self._arch_pull_active = False
            self._show_arch_status("cloud fetch failed to start (gsutil / powershell?)", err=True, timeout=6000)

    def _on_archive_pulled(self, code: int = 0, status=None) -> None:
        """rsync finished. On success: drop the archive cache, force a fresh walk + full re-derive, and re-render so
        the just-downloaded history appears. Idempotent (finished + errorOccurred can both fire)."""
        if not self._arch_pull_active:
            return
        self._arch_pull_active = False
        self._arch_pull_proc = None
        if code == 0:
            archive.invalidate()                             # re-read the freshly-pulled chunks from disk
            self._arch_win_key = None                        # force a new archive walk with the new coverage
            self._scanner_bucket_sig = None; self._last_scanner_sig = None
            self._pivot_sig = None; self._sel_sig = None; self._psc = None
            self._hide_arch_status()
            self._on_timer()                                 # repaint with the now-available 24h/history
        else:
            self._show_arch_status("cloud fetch failed — check gsutil / gcloud auth", err=True, timeout=7000)

    def _center_over_plot(self, widget) -> None:
        """Place `widget` (a child of the main window) at the exact CENTRE of the chart, raised above the drawing
        toolbar and every other overlay."""
        widget.adjustSize()
        tl = self.plot.mapTo(self, QtCore.QPoint(0, 0))
        widget.move(tl.x() + (self.plot.width() - widget.width()) // 2,
                    tl.y() + (self.plot.height() - widget.height()) // 2)
        widget.show(); widget.raise_()

    def _show_status_backdrop(self) -> None:
        """Blur + dim the chart behind a centred system message so it reads even under the drawing toolbar. Cheap
        downscale/upscale blur of a ONE-SHOT snapshot + a dark tint; a child of the WINDOW so it covers the toolbar
        too. Idempotent: keeps the existing snapshot while a message is already up (no re-capture of the message)."""
        if getattr(self, "_status_scrim", None) is None:
            self._status_scrim = QtWidgets.QLabel(self)
            self._status_scrim.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        scrim = self._status_scrim
        if scrim.isVisible():
            return
        w, h = self.plot.width(), self.plot.height()
        if w <= 0 or h <= 0:
            return
        pix = self.plot.grab()                                          # snapshot of the current chart (scrim/msg hidden)
        small = pix.scaled(max(1, w // 12), max(1, h // 12), QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation)
        blur = small.scaled(w, h, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation)   # gaussian-ish blur
        _p = QtGui.QPainter(blur); _p.fillRect(blur.rect(), QtGui.QColor(10, 12, 16, 150)); _p.end()   # dark tint
        scrim.setPixmap(blur)
        tl = self.plot.mapTo(self, QtCore.QPoint(0, 0))
        scrim.setGeometry(tl.x(), tl.y(), w, h)
        scrim.show(); scrim.raise_()

    def _maybe_hide_status_backdrop(self) -> None:
        """Drop the blurred backdrop once NO centred system message is on screen."""
        a = getattr(self, "arch_status", None); c = getattr(self, "_conn_banner", None)
        if (a is None or not a.isVisible()) and (c is None or not c.isVisible()):
            s = getattr(self, "_status_scrim", None)
            if s is not None:
                s.hide()

    def _show_arch_status(self, text: str, err: bool = False, timeout: int = 0) -> None:
        """Centred system message over a blurred chart while history is being fetched (blue) or on failure (red)."""
        if getattr(self, "arch_status", None) is None:
            self.arch_status = QtWidgets.QLabel("", self)   # child of the WINDOW -> rises above the drawing toolbar
            self.arch_status.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
            self.arch_status.setAlignment(QtCore.Qt.AlignCenter)
        col = "#ff6b6b" if err else "#7ec2ff"
        self.arch_status.setStyleSheet(
            "QLabel{color:%s; background:rgba(18,20,26,235); padding:14px 24px; border:1px solid %s;"
            "border-radius:10px; font-family:Consolas; font-size:15px; font-weight:bold;}" % (col, col))
        self.arch_status.setText(text)
        self._show_status_backdrop()
        self._center_over_plot(self.arch_status)
        if timeout > 0:
            QtCore.QTimer.singleShot(timeout, self._hide_arch_status)

    def _hide_arch_status(self) -> None:
        if getattr(self, "arch_status", None) is not None:
            self.arch_status.hide()
        self._maybe_hide_status_backdrop()

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
        if getattr(self, "vpin_tag", None) is not None:
            self.vpin_tag.hide()   # VPIN-pane value badge — drop on any mode switch too (no orphan)
        self.time_tag.hide()    # heatmap crosshair time tag — drop on any mode switch
        self.stats.hide()       # A3a: drop the hover readout too (no orphan across modes)
        self.panel_tooltip.hide()  # exhaustion-lines hover label
        self._clear_structure()    # hide HH/HL/LH/LL labels when leaving Mode 10 (pool-managed, not swept)
        self._clear_choch()        # hide CHoCH dashed lines when leaving Mode 10
        self._hide_4h_zone()       # hide the 4h buy/sell wick bands when leaving Mode 10
        self._hide_eff_cycles(); self._hide_abs_cycles()   # hide the P2 + P1 HM sub-panels when leaving Mode 10
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
            # the VPIN-pane crosshair/badge items were CHILDREN of the just-deleted lower_plot — null the Python refs
            # so _on_mouse_move / _on_lower_mouse_move skip them (else 'C++ object already deleted' on the next hover).
            # _ensure_canvas_panes recreates them when the pane is rebuilt.
            self.lower_vline = None; self.lower_hline = None; self.vpin_tag = None
            self.lower_vb = None; self._lower_proxy = None

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
        # REPLAY fast-path: the frame is fully determined by the cursor (clip is <= cursor and the archive is stable),
        # so if the cursor hasn't moved just return the cache — skipping the archive walk + ~11k-element concat + clip
        # that otherwise run EVERY frame here via the always-on selection refresh. A step/date change nulls the sig, so
        # this only short-circuits genuine idle. (Normal mode is untouched.)
        if (self._replay_on and self._replay_edge_t is not None
                and self._scanner_bucket_cache is not None and self._scanner_bucket_sig is not None
                and self._scanner_bucket_sig[-1] == self._replay_edge_t):
            return self._scanner_bucket_cache
        snap = self._last_snap or self.worker.snapshot()
        closed_list: list[dict] = snap.get("closed_buckets", []) or []
        active: dict = snap.get("active_bucket") or {}
        anchor_unix = self.menu.scan_start_unix()
        total_closed = int(snap.get("total_closed", 0) or 0)   # DB-id of closed_list[-1] (0 = pre-redeploy daemon)
        _replay = self._replay_on and self._replay_edge_t is not None
        if _replay:                                            # REPLAY: reach cold-archive context BEFORE the cursor
            anchor_unix = int(self._replay_edge_t) - config.REPLAY_LOOKBACK_SECS
        if _replay and getattr(self, "_replay_dbg", False):
            _o0 = float(closed_list[0].get("start_time", 0.0)) if closed_list else 0.0
            self._rdbg("BUILD cursor=%d anchor=%d wk_closed=%d wk_oldest_start=%d total_closed=%d arch_ext=%s gate=%s"
                       % (int(self._replay_edge_t), anchor_unix, len(closed_list), int(_o0), total_closed,
                          self._archive_extend, anchor_unix < _o0))

        # ARCHIVE EXTEND: when the Zero Point reaches before the daemon's oldest LIVE bucket, prepend the
        # contiguous older run from the LOCAL cold-archive mirror. Archive bids == absolute Idx (both from
        # total_closed), so the offset below stays exact — offset lands on the oldest archived bid. Gated to
        # the scrolled-back case + cached per (tf, anchor, edge) + guarded: normal use and any archive failure
        # fall straight through to the live-only frame, so this can never break the live path.
        if (self._archive_extend and total_closed > 0 and closed_list
                and anchor_unix < float(closed_list[0].get("start_time", 0.0))):
            before_bid = total_closed - len(closed_list) + 1
            akey = (self.worker.tf, anchor_unix, before_bid)
            if akey != self._arch_win_key:
                try:
                    self._arch_win = archive.window(self.worker.tf, anchor_unix, before_bid)
                except Exception:
                    self._arch_win = []
                self._arch_win_key = akey
            if self._arch_win:
                closed_list = self._arch_win + closed_list
            # FETCH-IF-MISSING: pull from GCS ONLY when the BRIDGE bid (before_bid-1) is genuinely absent locally —
            # an EMPTY walk == the local mirror is stale vs GCS (the gap the user first hit). A non-empty-but-short
            # walk just means we've reached the archive's earliest; pulling can't add older data, so DON'T (that
            # 48h-lookback false-alarm was re-firing the pull every live close -> a ~1.8s archive reload loop).
            if not self._arch_win:
                self._maybe_pull_archive()

        combined: list[dict] = list(closed_list)
        _rk = 0
        if _replay:
            # REPLAY causal clip: keep bars whose bucket CLOSED at/before the cursor (right edge), back to a FIXED
            # left edge = (replay START - 24h). The left edge does NOT move with the cursor, so a Right-arrow step
            # GROWS the window (reveals a new bar on the right) instead of sliding it (which dropped the leftmost
            # bar). Floored at REPLAY_MIN_BUCKETS (lookback on quiet starts) and capped at REPLAY_WINDOW (perf — only
            # then does the oldest slide off). The real-'now' active is dropped; closed_list is ascending -> causal.
            et = float(self._replay_edge_t); m = 0
            for b in closed_list:
                if float(b.get("end_time", 0.0)) <= et:
                    m += 1
                else:
                    break
            left_t = float(self._replay_start_t if self._replay_start_t is not None else et) - config.REPLAY_SPAN_SECS
            _lo = 0
            for b in closed_list[:m]:
                if float(b.get("start_time", 0.0)) < left_t:
                    _lo += 1
                else:
                    break
            keep = min(max(m - _lo, min(m, config.REPLAY_MIN_BUCKETS)), config.REPLAY_WINDOW)
            _rk = m - keep
            combined = closed_list[_rk:m]
            if getattr(self, "_replay_dbg", False):
                _wk = len(closed_list) - len(self._arch_win or [])
                _sp = ((float(combined[-1].get('end_time', 0)) - float(combined[0].get('end_time', 0))) / 3600.0) if combined else 0.0
                self._rdbg("CLIP cursor=%d closed=%d(arch=%d wk=%d) m=%d n24=%d keep=%d frame=%d span=%.1fh"
                           % (int(et), len(closed_list), len(self._arch_win or []), _wk, m, n24, keep, len(combined), _sp))
        # Append the live edge — but guard the ~1-frame window right after a close
        # where the just-closed bucket is in closed_list AND still the stale active
        # (until the next TICK ships a fresh active), which would double-count it.
        #
        # NOTE: start_time is NOT unique — several buckets fill within one busy
        # minute and share it — so the fingerprint must include curr_vol. The
        # stale active is identical to closed[-1] (start_time AND a full curr_vol);
        # a fresh same-minute bucket has a smaller, differing curr_vol and is kept.
        elif active and active.get("curr_vol", 0.0) > 0:
            last = closed_list[-1] if closed_list else None
            is_stale_dup = (
                last is not None
                and active.get("start_time") == last.get("start_time")
                and active.get("curr_vol") == last.get("curr_vol")
            )
            if not is_stale_dup:
                combined.append(active)

        # signature gate: rebuild only when the bucket set, the live edge volume, the anchor, or the absolute
        # index base (total_closed — which moves at the 10k cap even when len(combined) doesn't) changes.
        # In REPLAY the frame is a FROZEN historical clip: the live active vol AND total_closed are irrelevant
        # (both drift on every live bucket close but change nothing in [cursor-24h, cursor]). Keying on them would
        # rebuild the frame + re-run the pivot scan ~every live close (the lag). Key ONLY on the cursor + clip length
        # + anchor, so replay rebuilds exclusively on a step/date change. (offset == the fixed archive bid either way.)
        sig = (len(combined), (0.0 if _replay else round(active.get("curr_vol", 0.0), 1)),
               anchor_unix, (None if _replay else total_closed), (self._replay_edge_t if _replay else None))
        if sig == self._scanner_bucket_sig:
            return self._scanner_bucket_cache

        if _replay:                                # the clipped frame IS exactly the causal window
            filtered: list[dict] = list(combined)
            anchor_idx: Optional[int] = _rk        # clip start's index into closed_list -> exact Idx offset below
        else:
            anchor_idx = None
            filtered = []
            for i, b in enumerate(combined):
                if float(b.get("start_time", 0.0)) >= anchor_unix:
                    if anchor_idx is None:
                        anchor_idx = i
                    filtered.append(b)
            if anchor_idx is None:
                anchor_idx = len(combined)

        # ABSOLUTE index base: a filtered-local idx maps to history.db id = offset + idx. Since combined =
        # closed_list (+ live active), db_id(closed_list[-1]) = total_closed, so db_id(combined[j]) =
        # total_closed - len(closed_list) + 1 + j, and j = anchor_idx + local_idx. (0 -> legacy local idx.)
        self._global_idx_offset = (total_closed - len(closed_list) + 1 + anchor_idx) if total_closed > 0 else 0

        # LIVE liquidity detection — bucket-close / window-growth hook, NOT the draw path and NOT keyed on
        # curr_vol. Keyed on (total_closed, #closed loaded): fires on a new close AND as the pipe streams the
        # window in behind a known total_closed (so live events aren't skipped on a partial first frame).
        x_indices: list[int] = list(range(len(filtered)))
        result = (filtered, x_indices, anchor_idx)
        self._scanner_bucket_sig = sig
        self._scanner_bucket_cache = result
        # Idx-anchored drawings: keep Mode-10 shapes/brackets/selection glued to their BUCKETS across cap
        # trims / anchor moves / restarts (drawer no-ops outside index mode). AFTER the cache commit, so a
        # re-entrant _build_scanner_buckets (selectionChanged -> stats refresh) hits the cache, not a rebuild.
        if getattr(self, "drawer", None) is not None:
            self.drawer.set_idx_frame(self._global_idx_offset, len(filtered), self.worker.tf, filtered)
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
        if self._replay_on:
            # REPLAY is a FROZEN historical frame — nothing live (len(closed), curr_vol, absorptions) belongs on it,
            # and keying on those made the full recompute fire at 20 Hz for off-screen data (the lag). Key ONLY on
            # what actually changes the replay frame: the cursor, the Start Date, and the mode. Idle replay => skip,
            # so a step is the only work. (A step / date change also clears _last_scanner_sig, so it never sticks.)
            current_sig = ("replay", self._replay_edge_t, self.menu.scan_start_unix(), self.scanner_mode)
        else:
            # Mode-10 absorption marks must repaint on a lifecycle/geometry change even when the bucket set and
            # live-edge volume are static (a QUIET market): without this, an active->dead flip leaves (len,
            # curr_vol, scan_start, mode) identical, the redraw is skipped, and the dead band stays drawn OPEN.
            abs_sig = tuple((m.get("id"), m.get("active"), m.get("end"),
                             round(float(m.get("kappa", 0.0)), 2), m.get("price"))
                            for m in sorted(snap.get("absorptions", []), key=lambda m: m.get("id", "")))
            current_sig = (len(closed), active.get("curr_vol", 0.0),
                           self.menu.scan_start_unix(), self.scanner_mode, abs_sig)
        if current_sig == self._last_scanner_sig:
            return   # nothing changed — skip the heavy recompute
        self._last_scanner_sig = current_sig

        _sb = time.perf_counter()
        filtered, x_indices, _anchor = self._build_scanner_buckets()
        self._perf_note("scan_build", _sb)       # profiler: bucket assembly + archive fetch (Start-Date-change hotspot)
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
        ∝ √(total cell qty), scaled so the biggest visible cell ≈ MAX_PX (clamped). Cells holding a LARGE
        market order (a single taker trade >= the live large cutoff) are recolored (electric blue large BUY /
        orange large SELL); the rest split green (net buy) / purple (net sell). pxMode keeps bubbles a fixed
        size through zoom."""
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
        x, y, total, net, max_buy, max_sell = cells
        mx = float(total.max()) or 1.0
        lo, hi = config.HEATMAP_BUBBLE_MIN_PX, config.HEATMAP_BUBBLE_MAX_PX
        size = lo + (hi - lo) * np.sqrt(np.clip(total / mx, 0.0, 1.0))   # diameter px, area ~ qty
        large = self._hm_largeorder_side(max_buy, max_sell)              # 0=none, 1=large BUY, 2=large SELL
        lg_buy = large == 1; lg_sell = large == 2
        plain = large == 0
        buy = plain & (net > 0); sell = plain & ~(net > 0)               # net==0 -> drawn as sell (rare)
        for scat, m in ((self.hm_bubbles_buy, buy), (self.hm_bubbles_sell, sell),
                        (self.hm_bubbles_ice_buy, lg_buy), (self.hm_bubbles_ice_sell, lg_sell)):
            scat.setData(x=x[m], y=y[m], size=size[m], data=total[m])
            scat.setVisible(True)

    def _hm_largeorder_side(self, max_buy: np.ndarray, max_sell: np.ndarray) -> np.ndarray:
        """Per-cell LARGE market-order classification: 0 = none, 1 = a large BUY (the cell holds a single
        taker-buy >= the live large cutoff), 2 = a large SELL. The cutoff is the SAME ``large_thr`` the LARGE
        panel uses (slider override, else the daemon's broad p95) — so the bubbles + the panel agree, and a
        drag recolors both instantly. A cell with BOTH a large buy and a large sell goes to the bigger trade.
        Replaced the old absorption-band iceberg classifier; the four scatters + colours are unchanged."""
        n = len(max_buy)
        out = np.zeros(n, dtype=np.uint8)
        if not n:
            return out
        large_thr, _ = self._largesmall_thresholds()
        big_b = max_buy >= large_thr
        big_s = max_sell >= large_thr
        out[big_b] = 1
        out[big_s] = 2                                  # sell wins over buy here; the tie-break below corrects
        both = big_b & big_s
        if both.any():
            out[both] = np.where(max_buy[both] >= max_sell[both], 1, 2)
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
        # In replay the whole frame slides on each step (usually with the SAME bucket count), so the plain
        # 'n changed' gate would skip the Y re-fit and clip the freshly-revealed candle — force the re-fit while
        # following. (When the user zooms/pans, follow unlocks and this method doesn't run, so their zoom is kept.)
        new_bucket = (n != self._follow_last_n) or self._replay_on
        # In Replay Mode the loaded frame IS the 24h window the user asked for, so show ALL of it (window = n)
        # rather than the narrow live FOLLOW_WINDOW; the newest kept bucket (the replay cursor) sits at the right.
        win = n if self._replay_on else FOLLOW_WINDOW
        if self._follow_x and (FOLLOW_X_PER_TICK or new_bucket):
            self.vb.setXRange(max(-0.5, n - win - 0.5),
                              (n - 1) + FOLLOW_MARGIN + 0.5, padding=0)
        if self._follow_y and (FOLLOW_Y_PER_TICK or new_bucket):
            w0 = max(0, n - win)
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
        # A manual pan/zoom changes which swing (HH/HL/LH/LL) / liquidity / imbalance / footprint labels are
        # ON-SCREEN, but those cull-to-view overlays are only re-culled INSIDE _draw_scanner — which the sig-gate
        # SKIPS in an idle Replay Mode (nothing new streams). Without this the structure labels stay culled to the
        # PRIOR view (zoom in -> they hide most of the window; zoom out -> stay hidden until you step). Force one
        # redraw so every overlay re-culls to the new viewport. (Live mode already redraws each tick — harmless there.)
        self._last_scanner_sig = None
        # Candle viewport re-cull: redraw ONLY the new visible range from the cached series
        # (O(visible), not O(N)) so a manual pan/zoom refreshes the on-screen candles instantly
        # instead of waiting for the next live tick to fire update_data. Fires on both pan
        # (drag) and zoom (wheel/axis-drag) — sigRangeChangedManually covers both.
        bc = self._scan_handles.get("bc_candles")
        if bc is not None:
            bc.set_view(nx0, nx1)
        wb = self._scan_handles.get("bc_whisker")
        if wb is not None:
            wb.set_view(nx0, nx1)
        fpc = self._scan_handles.get("bc_fpcandle")
        if fpc is not None:
            fpc.set_view(nx0, nx1)
        dc = self._scan_handles.get("bc_deltacandle")
        if dc is not None:
            dc.set_view(nx0, nx1)
        fcc = self._scan_handles.get("bc_forcecandle")
        if fcc is not None:
            fcc.set_view(nx0, nx1)
        dfc = self._scan_handles.get("bc_deltaforce")
        if dfc is not None:
            dfc.set_view(nx0, nx1)
        if self._z4_last_buckets:                            # keep the 4h V/Z buttons glued just above the axis on
            try:                                             # a manual zoom/pan (their y is view-relative)
                self._draw_4h_zone(self._z4_last_buckets)
            except Exception:
                pass

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
                         span: bool = False, fill_bg=None) -> None:
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
                        pen=pg.mkPen(color, width=0.8, style=QtCore.Qt.DashLine))
                else:
                    rule = pg.PlotCurveItem(x=[x_data, x_max], y=[value, value],
                                            pen=_rule_pen())
                rule.setZValue(55)
                if target_vb is not None:
                    target_vb.addItem(rule)
                else:
                    self._add_scanner_item(rule)
                self._scan_handles[ln_key] = rule
            tag = pg.TextItem(anchor=anchor, fill=pg.mkBrush(fill_bg)) if fill_bg \
                else pg.TextItem(anchor=anchor)
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
                    rule.setPen(pg.mkPen(color, width=0.8, style=QtCore.Qt.DashLine))
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
        tiers, toxics = vpin_adaptive.vpin_tiers_from_series(vpin_arr)   # CAUSAL per-bucket tier (frozen, no repaint)
        _br = {t: pg.mkBrush(h) for t, h in _VPIN_TIER_HEX.items()}
        brushes = [_br[t] for t in tiers]

        if "vpin" not in self._scan_handles:
            self._scan_handles["vpin"] = self._add_scanner_item(
                pg.BarGraphItem(x=x, height=vpin_arr, width=0.8, brushes=brushes, pen=None))
            self._scan_handles["vpin_line"] = self._add_scanner_item(
                pg.PlotDataItem(pen=pg.mkPen("#ff073a", style=QtCore.Qt.DashLine, width=2)))
        else:
            self._scan_handles["vpin"].setOpts(x=x, height=vpin_arr, width=0.8,
                                               brushes=brushes, pen=None)
        self._set_vpin_line("vpin_line", x, toxics)
        self._fit_scanner_y(len(x), clamp=(0.0, 1.05))
        v = vpin_arr[-1]
        tier = tiers[-1]
        col = {vpin_adaptive.TOXIC: "#ff073a", vpin_adaptive.WARN: "#f1c40f"}.get(tier, "#999999")
        self._scanner_tracker("t_vpin", v, col, f"VPIN {v:.2f}<br>({v * 100:.0f}%)",
                              x[-1], "mid")

    def _set_vpin_line(self, handle_key: str, x, toxics) -> None:
        """Draw the adaptive toxic threshold as a per-bucket CAUSAL line: at each x it sits at THAT bucket's own
        as-of-that-bucket toxic cutpoint, so it tracks the bars (a bar is red iff it pokes above the line at its
        own x) and — like the bars — never repaints as later buckets shift the distribution. Warm-up buckets (no
        percentile yet) leave a gap; the whole line hides if every bucket is warm-up."""
        line = self._scan_handles.get(handle_key)
        if line is None:
            return
        if not toxics or all(t is None for t in toxics):
            line.setVisible(False)
            return
        ys = [float(t) if t is not None else float("nan") for t in toxics]
        line.setData(x=list(x), y=ys, connect="finite")
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
        # Shared crosshair + VPIN value badge on the VPIN pane: the VERTICAL (x) line is shared with the main chart
        # (same X-link, so the two align), while the HORIZONTAL (y) line + a right-axis value badge are the pane's
        # own — mirroring the main chart's price tag. Driven by both scenes' sigMouseMoved (main -> just sync x;
        # lower -> full x+y readout + VPIN badge). Lines linger like the main crosshair; the badge hides on leave.
        _xc = pg.mkPen(color=(170, 170, 170, 150), width=1); _xc.setCosmetic(True); _xc.setDashPattern([4.0, 8.0])
        self.lower_vline = pg.InfiniteLine(angle=90, movable=False, pen=_xc)
        self.lower_hline = pg.InfiniteLine(angle=0, movable=False, pen=_xc)
        self.lower_vline.setZValue(15); self.lower_hline.setZValue(15)
        self.lower_plot.addItem(self.lower_vline, ignoreBounds=True)
        self.lower_plot.addItem(self.lower_hline, ignoreBounds=True)
        self.lower_hline.hide()
        self.vpin_tag = pg.TextItem(anchor=(1, 0.5), color="#141414", fill=pg.mkBrush("#dcdcdc"))
        _vtf = QtGui.QFont("Consolas", 9); _vtf.setBold(True)
        self.vpin_tag.textItem.setFont(_vtf); self.vpin_tag.setZValue(16)
        self.lower_plot.addItem(self.vpin_tag, ignoreBounds=True); self.vpin_tag.hide()
        self.lower_vb = self.lower_plot.getViewBox()
        self._lower_proxy = pg.SignalProxy(self.lower_plot.scene().sigMouseMoved,
                                           rateLimit=60, slot=self._on_lower_mouse_move)
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

    def _bucket_row(self, buckets: list, i: int, vels: list, fold_prev, poc_ab=(0.05, 0.95)):
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
        # baseline EMA — the smoothed POC center line (slow EMA of the bucket POC). ``poc_ab`` = (α, 1-α) weights:
        # native (0.05, 0.95) at scale 1×, stretched to a longer period by the Keltner-scale slider (period ×scale).
        # ``fold_prev`` is the prior bucket's baseline scalar (None for i == 0 = seed).
        poc = b.get("poc_price", 0.0)
        baseline = poc if fold_prev is None else (poc * poc_ab[0] + fold_prev * poc_ab[1])
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
        # volume-quantile whisker encoding ('W' render mode): box = [C>=25%V, C>=75%V] of the ladder's
        # cumulative volume, median = C>=50%V — computed by the SHARED app.bar_quantiles module (the same
        # implementation the S4-GEO study reads, so M/P mean one thing everywhere). NaN when the ladder is
        # missing/degenerate (old rows) -> the candle path draws those bars. Cached here (#3).
        vq_lo, vq_med, vq_hi = bar_quantiles.vq(b.get("levels") or {})
        # trailing-30 BER/SER means (the renderer's footprint/imbalance thresholds) + the abnormal-velocity
        # ratio — pure trailing-window functions of 0..i-1 closed data (+ this bucket's own value), so they
        # obey the SAME #3 cache contract as everything above (final the instant the bucket closes). Hoisted
        # here from _scan_bucket_canvas, where they were re-averaged O(N·30) per FRAME (~106ms at 10k buckets).
        w30 = buckets[max(0, i - EXH_WINDOW):i]
        if w30:
            ber30 = sum(bb.get("buyer_er", 0.0) for bb in w30) / len(w30)
            ser30 = sum(bb.get("seller_er", 0.0) for bb in w30) / len(w30)
        else:
            ber30 = ser30 = 0.0
        wv = vels[max(0, i - config.VEL_ABN_WINDOW):i]
        vbase = (sum(wv) / len(wv)) if wv else 0.0
        velabn = (vels[i] / vbase) if vbase > 0 else 0.0
        row = (b.get("open", 0.0), b.get("high", 0.0), b.get("low", 0.0),
               b.get("close", 0.0), poc, brush,
               baseline, vpin, wick_pen, ber30, ser30, velabn, vq_lo, vq_med, vq_hi)
        return row, baseline

    # The parallel render arrays, in row-tuple order (see _bucket_row). vbrush is NOT here:
    # the VPIN heatmap brush is render-time (adaptive percentile), recomputed each frame.
    _M10_ARR_KEYS = ("opens", "highs", "lows", "closes", "pocs", "brushes",
                     "baseline", "vpin", "pens", "ber30", "ser30", "velabn",
                     "vq_lo", "vq_med", "vq_hi")

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
        _S = float(self._kc_scale)             # smooth-approx effective-TF scale (KC period ×S, band ×sqrt(S); baseline period ×S)
        cc = self._m10_cc
        reuse = (cc is not None and cc["front_id"] == front_id
                 and cc["anchor"] == anchor_unix and cc["n"] <= n_closed
                 and cc.get("kc_s") == _S)     # a scale change invalidates the cached KC + baseline rows
        if not reuse:                          # full rebuild (front/anchor change, scale change, or first run)
            cc = {k: [] for k in self._M10_ARR_KEYS}
            cc.update(vels=[], fold=None, n=0, front_id=front_id, anchor=anchor_unix,
                      kc_up=[], kc_lo=[], kc_fold=None, kc_s=_S)

        # Keltner fold: same sequential arithmetic as _keltner_bands (EMA basis + Wilder RMA ATR), carried
        # as (e, a) after the last computed bucket — a closed bucket's KC is final at close (the recurrence
        # only reads backwards), so the O(N) full-history walk is paid once, not per frame.
        _kl = config.KELTNER_LENGTH * _S       # EMA basis + ATR period stretched ×scale (float ok in both recurrences)
        _km = config.KELTNER_ATR_MULT * math.sqrt(_S)   # band half-width widens ~sqrt(scale) -> approximates the higher-TF range
        _kk = 2.0 / (_kl + 1)
        if _S == 1.0:                          # baseline EMA weights: native literals at 1× -> byte-identical POC baseline
            _b_alpha, _b_beta = 0.05, 0.95
        else:                                  # else stretch the native ≈39-bucket period ×scale
            _b_alpha = 2.0 / (config.KELTNER_BASELINE_PERIOD * _S + 1.0); _b_beta = 1.0 - _b_alpha
        _bab = (_b_alpha, _b_beta)

        def _kc_step(fold, h, l, c, c_prev):
            if fold is None:                                   # seed exactly like _keltner_bands i == 0
                e, a = float(c), float(h) - float(l)
            else:
                e0, a0 = fold
                e = float(c) * _kk + e0 * (1.0 - _kk)
                tr = max(float(h) - float(l), abs(float(h) - float(c_prev)), abs(float(l) - float(c_prev)))
                a = (a0 * (_kl - 1) + tr) / _kl
            return (e, a), e + _km * a, e - _km * a

        # extend velocities (per-bucket, immutable) + finalize newly-closed rows ONCE each
        for i in range(cc["n"], n_closed):
            b = buckets[i]
            dur = max(1.0, b.get("end_time", 0.0) - b.get("start_time", 0.0))
            cc["vels"].append((b.get("buy_vol", 0.0) + b.get("sell_vol", 0.0)) / dur)
            row, cc["fold"] = self._bucket_row(buckets, i, cc["vels"], cc["fold"], _bab)
            for k, v in zip(self._M10_ARR_KEYS, row):
                cc[k].append(v)
            cc["kc_fold"], _u, _l = _kc_step(cc["kc_fold"], cc["highs"][i], cc["lows"][i], cc["closes"][i],
                                             cc["closes"][i - 1] if i > 0 else None)
            cc["kc_up"].append(_u); cc["kc_lo"].append(_l)
        cc["n"] = n_closed
        self._m10_cc = cc                      # cache holds exactly the closed prefix

        # full arrays = cached closed prefix (O(N) pointer copy) + the FRESH live edge
        out = {k: list(cc[k]) for k in self._M10_ARR_KEYS}
        out["kc_up"] = list(cc["kc_up"]); out["kc_lo"] = list(cc["kc_lo"])
        if L >= 1:
            b = buckets[L - 1]
            dur = max(1.0, b.get("end_time", 0.0) - b.get("start_time", 0.0))
            live_vel = (b.get("buy_vol", 0.0) + b.get("sell_vol", 0.0)) / dur
            row, _ = self._bucket_row(buckets, L - 1, cc["vels"] + [live_vel], cc["fold"], _bab)
            for k, v in zip(self._M10_ARR_KEYS, row):
                out[k].append(v)
            _, _u, _l = _kc_step(cc["kc_fold"], b.get("high", 0.0), b.get("low", 0.0), b.get("close", 0.0),
                                 cc["closes"][-1] if cc["closes"] else None)
            out["kc_up"].append(_u); out["kc_lo"].append(_l)
        return out

    @staticmethod
    def _keltner_bands(highs, lows, closes, length: int, mult: float):
        """Keltner Channel: EMA(close, length) basis ± mult·ATR(length). ATR = Wilder's RMA of the True
        Range. Returns (upper, mid, lower) aligned to the inputs (the left edge warms up like any MA)."""
        n = len(closes)
        if n == 0:
            return [], [], []
        k = 2.0 / (length + 1)
        mid = [0.0] * n
        atr = [0.0] * n
        e = float(closes[0]); a = float(highs[0]) - float(lows[0])
        mid[0] = e; atr[0] = a
        for i in range(1, n):
            e = float(closes[i]) * k + e * (1.0 - k)                       # EMA basis
            tr = max(float(highs[i]) - float(lows[i]),
                     abs(float(highs[i]) - float(closes[i - 1])),
                     abs(float(lows[i]) - float(closes[i - 1])))           # True Range
            a = (a * (length - 1) + tr) / length                          # Wilder RMA → ATR
            mid[i] = e; atr[i] = a
        upper = [mid[i] + mult * atr[i] for i in range(n)]
        lower = [mid[i] - mult * atr[i] for i in range(n)]
        return upper, mid, lower

    def _scan_bucket_canvas(self, buckets: list, x: list) -> None:
        """Mode 10 — neon-graded bucket candles + gray baseline (upper pane)
        synchronized with a rolling-50 VPIN toxicity heatmap (lower pane)."""
        self._ensure_canvas_panes()
        # #3 static closed-bucket compute cache: closed buckets are immutable, so their
        # OHLC/poc/brush + baseline EMA + rolling-50 VPIN rows are computed ONCE
        # (on close) and reused; only the live edge (buckets[-1]) is recomputed each frame.
        _ba = time.perf_counter()
        arr = self._compute_bucket_arrays(buckets, self.menu.scan_start_unix())
        self._perf_note("bc_arrays", _ba)        # profiler: candle/OHLC/baseline/rolling-VPIN cache build
        opens, highs, lows, closes = arr["opens"], arr["highs"], arr["lows"], arr["closes"]
        pocs, brushes = arr["pocs"], arr["brushes"]
        baseline_arr = arr["baseline"]
        vpin_arr = arr["vpin"]
        # adaptive VPIN brushes — CAUSAL per-bucket tier (each bar judged against ONLY its own trailing window),
        # so a bar's colour FREEZES when it closes and never repaints as the recent distribution drifts later.
        _vt = time.perf_counter()
        vtiers, vtoxics = vpin_adaptive.vpin_tiers_from_series(vpin_arr)
        self._perf_note("vpin_tiers", _vt)       # profiler: causal VPIN tier pass (per redraw, now rolling-window)
        _vbr = {t: pg.mkBrush(h) for t, h in _VPIN_TIER_HEX.items()}
        vbrushes = [_vbr[t] for t in vtiers]
        wick_pens = arr["pens"]   # per-candle flow-colored wick/border pens

        # Abnormal-velocity flag — a bucket whose velocity (curr_vol/dur) is >= VEL_ABN_RATIO x its
        # trailing-VEL_ABN_WINDOW MEAN (the SAME 30b basis as the stats box's 30b BER/SER). Ratios come from
        # the #3 cache (arr["velabn"], computed once per closed bucket — was re-averaged O(N·30) per frame).
        # Used for BOTH cues on a flagged candle: a 2px wick/border (vs the 0.3-1.0 flow width, KEEPING
        # the flow colour) — ALWAYS ON — plus a diamond above it ('v' toggles; neon green=buyer /
        # red=seller dominated, GOLD on divergence). DESCRIPTIVE study marker, not a signal.
        vel_abn = arr["velabn"]
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
        # vx0/vx1: viewport cull — paint ONLY the visible candles (O(visible), not O(N)). 'W' cycles the render
        # mode: 0 normal candles -> 1 volume-quantile WHISKER bars -> 2 FOOTPRINT candles (each bucket a mini
        # centred buy/sell volume profile, like the live pane). Both alt modes use the #3 cache / per-bucket ladder
        # (no extra per-frame compute) + viewport cull. DEGRADATION: below the per-mode px/bar floor the WHOLE view
        # falls back to candles; ladder-less buckets fall back individually.
        if "bc_whisker" not in self._scan_handles:
            self._scan_handles["bc_whisker"] = self._add_scanner_item(WhiskerBarItem())
        if "bc_fpcandle" not in self._scan_handles:
            self._scan_handles["bc_fpcandle"] = self._add_scanner_item(FootprintCandleItem())
        if "bc_deltacandle" not in self._scan_handles:
            self._scan_handles["bc_deltacandle"] = self._add_scanner_item(DeltaCandleItem())
        if "bc_forcecandle" not in self._scan_handles:
            self._scan_handles["bc_forcecandle"] = self._add_scanner_item(ForceCandleItem())
        if "bc_deltaforce" not in self._scan_handles:
            self._scan_handles["bc_deltaforce"] = self._add_scanner_item(DeltaForceCandleItem())
        _wb = self._scan_handles["bc_whisker"]
        _fpc = self._scan_handles["bc_fpcandle"]; _dc = self._scan_handles["bc_deltacandle"]
        _fcc = self._scan_handles["bc_forcecandle"]; _dfc = self._scan_handles["bc_deltaforce"]
        _wq_lo, _wq_med, _wq_hi = arr["vq_lo"], arr["vq_med"], arr["vq_hi"]
        _cw = px_per_x * 0.8                                                # on-screen candle width in px
        _force_items = (_fcc, _dfc)                                         # per-level modes that take a forces list

        def _blank_fp(active=None):                                        # free the per-level candle pictures (except `active`)
            for _it in (_fpc, _dc, _fcc, _dfc):
                if _it is not active:
                    _it.setVisible(False)
                    _it.update_data([], [], [], [], []) if _it in _force_items else _it.update_data([], [], [], [])

        def _fallback_candles(idxs):                                       # draw only the given (ladder-less) buckets
            self._scan_handles["bc_candles"].update_data(
                [x[i] for i in idxs], [opens[i] for i in idxs], [highs[i] for i in idxs],
                [lows[i] for i in idxs], [closes[i] for i in idxs],
                [brushes[i] for i in idxs], [wick_pens[i] for i in idxs], 0.8, vx0, vx1)

        if self._hide_candles:                                             # Ctrl+H — hide every candle glyph (VP / zones only)
            self._scan_handles["bc_candles"].update_data([], [], [], [], [], [], [])
            _wb.setVisible(False); _wb.update_data([], [], [], [], [], [], [], [])
            _blank_fp()
        elif self._candle_mode in (2, 3, 4, 5) and _cw >= config.FP_CANDLE_MIN_PX:   # per-level candle modes
            _ll = [b.get("levels") or {} for b in buckets]
            if self._candle_mode in (4, 5):                               # FORCE (4) / DELTA-FORCE (5): need the 4-vector
                _fl = [(float(b.get("opL", 0.0)), float(b.get("opS", 0.0)),
                        float(b.get("clL", 0.0)), float(b.get("clS", 0.0))) for b in buckets]
                _on = _fcc if self._candle_mode == 4 else _dfc
                _on.update_data(x, _ll, _fl, highs, lows, 0.8, vx0, vx1); _on.setVisible(True)
            else:                                                         # FOOTPRINT (2) / DELTA (3)
                _on = _fpc if self._candle_mode == 2 else _dc
                _on.update_data(x, _ll, highs, lows, config.FOOTPRINT_IMB_ER_MULT, 0.8, vx0, vx1); _on.setVisible(True)
            _blank_fp(active=_on)                                          # hide the other per-level items
            _wb.setVisible(False); _wb.update_data([], [], [], [], [], [], [], [])
            _fallback_candles([i for i in range(len(x)) if not _ll[i]])    # ladder-less bucket -> normal candle
        elif self._candle_mode == 1 and _cw >= 3.0:                        # WHISKER bars
            _blank_fp()
            _wb.update_data(x, _wq_lo, _wq_med, _wq_hi, highs, lows, opens, closes,
                            brushes, wick_pens, 0.8, vx0, vx1,
                            show_med=self.menu.layer_state("m10_poc"))   # median rides the 'P' POC toggle
            _wb.setVisible(True)
            _fallback_candles([i for i in range(len(x)) if _wq_med[i] != _wq_med[i]])   # NaN ladder -> candle fallback
        else:                                                              # NORMAL candles
            _wb.setVisible(False); _wb.update_data([], [], [], [], [], [], [], [])   # free the pictures
            _blank_fp()
            self._scan_handles["bc_candles"].update_data(x, opens, highs, lows, closes, brushes, wick_pens, 0.8, vx0, vx1)
        self._scan_handles["bc_baseline"].setData(x, baseline_arr)   # gray dashed POC-center baseline
        self._scan_handles["bc_baseline"].setVisible(not self._hide_candles)   # Ctrl+H hides the POC baseline with the candles
        # liquidity-sweep labels (Ctrl+L) — cull-to-visible, density-floored, capped, bounded pool; timed as
        # its OWN profiler section ('liq') so this layer is measured directly, not inferred under draw_scanner.
        _ls = time.perf_counter()
        try:
            self._draw_liq(buckets, x, vx0, vx1, vy0, vy1)
        except Exception:
            self._clear_liq()
        self._perf_note("liq", _ls)
        try:
            self._draw_structure(buckets, x, vx0, vx1, vy0, vy1)   # HH/HL/LH/LL swing labels (m10_structure)
        except Exception:
            self._clear_structure()
        try:
            self._draw_choch(buckets, x, vx0, vx1)                 # Change-of-Character dashed lines (m10_choch)
        except Exception:
            self._clear_choch()
        try:
            self._draw_4h_zone(buckets)                            # 4h buy/sell wick zones (m10_4hzone)
        except Exception:
            self._hide_4h_zone()

        # --- Keltner Channel: EMA(close) basis ± ATR band. LIGHT GRAY upper/lower (match the POC baseline);
        #     the EMA MIDDLE line is HIDDEN (operator pref — the POC baseline is the center reference). ---
        if "kc_mid" not in self._scan_handles:
            _kc_pen = pg.mkPen((180, 180, 180, 170), width=1.0)
            self._scan_handles["kc_upper"] = self._add_scanner_item(pg.PlotCurveItem(pen=_kc_pen))
            self._scan_handles["kc_lower"] = self._add_scanner_item(pg.PlotCurveItem(pen=_kc_pen))
            self._scan_handles["kc_mid"] = self._add_scanner_item(pg.PlotCurveItem(
                pen=pg.mkPen((180, 180, 180, 120), width=1.0, style=QtCore.Qt.DashLine)))
            self._scan_handles["kc_mid"].setVisible(False)   # hide the EMA basis line
        # KC bands from the #3 cache (sequential fold extended once per close — was a full O(N) walk per frame)
        self._scan_handles["kc_upper"].setData(x, arr["kc_up"])
        self._scan_handles["kc_lower"].setData(x, arr["kc_lo"])
        self._scan_handles["kc_upper"].setVisible(not self._hide_candles)   # Ctrl+H hides the KC with the candles
        self._scan_handles["kc_lower"].setVisible(not self._hide_candles)

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
        # trailing-30 BER/SER from the #3 cache (arr["ber30"/"ser30"], computed once per closed bucket —
        # this was the single biggest per-frame cost: O(N·30) dict-gets, ~81ms at 10k buckets).
        ber30s, ser30s = arr["ber30"], arr["ser30"]

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
        _imb_vis = not self._hide_candles                     # Ctrl+H hides the abnormal-order lines with the candles
        self._scan_handles["bc_imb_sell"].setVisible(_imb_vis)
        self._scan_handles["bc_imb_buy"].setVisible(_imb_vis)

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

        # DATA SOURCE: live -> the daemon's snapshot marks; REPLAY -> re-detect CAUSALLY from the clipped frame
        # (the live marks are anchored at 'now' and fall off the historical view -> nothing shows). Same algorithm.
        # Only pay the re-detect when a layer that needs it is actually on (it's ~250ms at the frame cap, cached/step).
        _obs_on = self.menu.layer_state("m10_obs"); _ice_on = self.menu.layer_state("m10_icebergs")
        if self._replay_on and self._replay_edge_t is not None and (_obs_on or _ice_on):
            _ob_src, _abs_src = self._replay_ob_abs(buckets)
        else:
            _ob_src = self._last_snap.get("order_blocks", []); _abs_src = self._last_snap.get("absorptions", [])

        if _obs_on:
            if "bc_obs" not in self._scan_handles:
                self.bc_obs.setZValue(-5)          # zones render behind the candles
                self._add_scanner_item(self.bc_obs, ignore_bounds=True)  # derived overlay: never drive the X/Y fit
                self._scan_handles["bc_obs"] = self.bc_obs
            self.bc_obs.setVisible(True)
            # 'o' cycle stage 2: hide mitigated (dead) OBs, keep only the live unmitigated zones.
            if self._ob_unmitig_only:
                _ob_src = [ob for ob in _ob_src if ob.get("active")]
            # Min-Mult slider writes bc_obs.visible_filter directly now (relocated off the dormant
            # time-chart ob_item, Phase C step 1); bc_obs.update_data_indexed reads it.
            vx0, vx1 = self.vb.viewRange()[0]   # clamp OB spans to the visible window (no corner-float)
            self.bc_obs.update_data_indexed(_ob_src, float(x[-1]), _ts_to_idx, (vx0, vx1), px_per_y)

        # Mode 10 whale-absorption bands (phase c) — gated by m10_icebergs (relabeled "Absorption").
        if _ice_on:
            if "bc_absorption" not in self._scan_handles:
                self.bc_absorption.setZValue(-6)   # behind the OB zones + candles
                self._add_scanner_item(self.bc_absorption, ignore_bounds=True)
                self._scan_handles["bc_absorption"] = self.bc_absorption
            self.bc_absorption.setVisible(True)
            vx0, vx1 = self.vb.viewRange()[0]
            self.bc_absorption.update_data_indexed(_abs_src, float(x[-1]), _ts_to_idx, (vx0, vx1))

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
            line = pg.PlotDataItem(pen=pg.mkPen("#ff073a", style=QtCore.Qt.DashLine, width=2))
            self.lower_plot.addItem(line)
            self._scan_handles["bc_vpin_line"] = line
        else:
            self._scan_handles["bc_vpin"].setOpts(x=x, height=vpin_arr, width=0.8,
                                                  brushes=vbrushes, pen=None)
        self._set_vpin_line("bc_vpin_line", x, vtoxics)

        # --- view-follow (replaces the one-shot fit). A mode/tf/Zero-Point re-arm
        # (_scanner_needs_autofit) re-locks BOTH axes + drops us on the live edge, consuming
        # that flag exactly as _fit_scanner_y used to. The Y fit uses candles only
        # (lows/highs) — re-fit every draw so an extreme in-window bucket can't squish them. The
        # roll runs whenever either axis is locked (each axis gated inside). After the draw
        # we snapshot the displayed range so the per-axis unlock can diff against it. ---
        _vr_before = self.vb.viewRange()           # FULL range (X and Y) the candles/footprint above were sized against
        if self._scanner_needs_autofit:
            self._follow_x = self._follow_y = True
            self._scanner_needs_autofit = False
        if self._follow_x or self._follow_y:
            self._roll_to_live_edge(len(x), lows, highs)
        self.lower_plot.getViewBox().setYRange(0.0, 1.05, padding=0)
        self._follow_prev_range = self.vb.viewRange()
        # If the view JUMPED this frame (autofit / replay cursor move / mode switch), the candles were culled to the
        # OLD X range AND the footprint bubbles were sized with the OLD px_per_y (r_px/px_per_y in DATA units) — so
        # on a Y-only jump (a replay step keeps X but changes the price range) the bubbles render outside the candles.
        # The render-sig is now stable, so the 20 Hz loop would never re-draw. Force ONE re-draw next frame — and
        # check BOTH axes (the old X-only test missed the replay Y-jump that spilled the footprint outside).
        _vr_after = self.vb.viewRange()
        if (abs(_vr_after[0][0] - _vr_before[0][0]) > 1e-6 or abs(_vr_after[0][1] - _vr_before[0][1]) > 1e-6
                or abs(_vr_after[1][0] - _vr_before[1][0]) > 1e-6 or abs(_vr_after[1][1] - _vr_before[1][1]) > 1e-6):
            self._last_scanner_sig = None

        # §5 right-edge spot price + active-bucket fill badge, plus the baseline readout
        # (all on the upper price pane; stacked + left-padded to avoid clipping).
        x_edge = x[-1]
        fill = self._active_fill_pct()
        spot = closes[-1]
        # §5.2 — minimalist spot badge: a WHITE pill with the bare price (bold) over the
        # active bucket's fill% (not bold), both black. No "Price"/"$"/"Fill"/"Base" labels;
        # the gray dashed baseline curve still shows the EMA baseline's position separately.
        # Anchored "up" (bottom edge at spot) so the block stacks ABOVE the spanning price line.
        self._scanner_tracker("t_spot", spot, "#999999",
            f"<div style='text-align:center'>"
            f"<span style='color:#000'>{spot:.2f}</span><br>"
            f"<span style='color:#000; font-weight:normal'>{fill:.0f}%</span></div>",
            x_edge, "up", span=True, fill_bg="#ffffff")

        # Deterministic horizontal lock: mirror the main X range onto the lower
        # pane every frame so the dual panes stay in pixel-perfect lock-step.
        main_xr = self.plot.getViewBox().viewRange()[0]
        self.lower_plot.getViewBox().setXRange(main_xr[0], main_xr[1], padding=0)

        # Live footprint side pane: redraw the FORMING candle's developing footprint (sig-cached on its
        # curr_vol + level count, so it only re-renders when the live bucket actually changed).
        if self._fp_want and self.fp_panel.isVisible():
            # LIVE: the real-time forming candle (active_bucket). REPLAY: the candle AT the cursor = the frame's last
            # bucket (active_bucket is the live edge, unrelated to where you're scrubbing). start_time in the sig so a
            # cursor step to a same-volume bucket still repaints.
            _ab = ((buckets[-1] if buckets else None) if self._replay_on
                   else (self._last_snap or {}).get("active_bucket")) or {}
            _fsig = (round(float(_ab.get("start_time", 0.0)), 3),
                     round(float(_ab.get("curr_vol", 0.0)), 3), len(_ab.get("levels") or {}))
            if _fsig != self._fp_sig:
                self._fp_sig = _fsig
                _spot = closes[-1] if closes else _ab.get("close")   # current price -> the dashed line
                # trailing-30 buyer/seller E/R of the live-edge bucket -> the SAME abnormal-order threshold the chart
                # uses for its blue/orange imbalance lines, so the panel highlights exactly the same levels.
                _b30 = ber30s[-1] if ber30s else None; _s30 = ser30s[-1] if ser30s else None
                self.fp_panel.update_footprint(_ab, config.FOOTPRINT_IMB_ER_MULT, _spot, _b30, _s30)


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

    def _conn_watchdog(self) -> None:
        """1s tick: surface a lost connection ON THE CHART and auto-heal it. Cheap (one bool read when
        healthy). Also covers first-boot waiting (banner shows until the daemon link is up)."""
        if bool(self.worker.connected):
            if self._conn_down_s:
                self._conn_down_s = 0
                self._conn_banner.hide()
                self._maybe_hide_status_backdrop()
            return
        self._conn_down_s += 1
        if self._conn_down_s >= 2:                       # ignore sub-2s blips
            dots = "." * (self._conn_down_s % 4)
            self._conn_banner.setText("⟳ CONNECTION LOST — reconnecting%s  (down %ds)"
                                      % (dots, self._conn_down_s))
            self._show_status_backdrop()                 # blur the chart behind the centred banner (idempotent)
            self._center_over_plot(self._conn_banner)    # dead-centre, above the drawing toolbar
        if self._conn_down_s % 5 == 0:                   # heal attempt every 5s while down
            try:
                if _TUNNEL is not None:
                    _TUNNEL.ensure()                     # relaunch the gcloud tunnel ONLY if its port died
            except Exception:
                pass
            try:
                self.worker.refresh()                    # drop any half-dead socket + retry immediately
            except Exception:
                pass

    def closeEvent(self, event) -> None:
        try:                                   # final SYNCHRONOUS drawing save — covers a close/shutdown
            self.drawer._save_idx()            # landing inside the 400ms debounce window
            self.drawer._save()
        except Exception:
            pass
        try:
            self._save_ui_state()              # flush the replay position (+ toggles) inside the 800ms debounce window
        except Exception:
            pass
        self.timer.stop()
        self._conn_timer.stop()
        self.worker.stop()
        try:
            self.worker_4h.stop()
        except Exception:
            pass
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
# gcloud needs ~10-15s (cold: key check + SSH handshake + PuTTY) to bring the forwarded port up, while the
# connection watchdog re-heals every 5s. This is how long a launch we started is left alone before it is
# judged wedged — WITHOUT it, every cold start stacked 2-3 duplicate tunnels (each with its own PuTTY window).
_TUNNEL_BOOT_GRACE = 30.0


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
        self._launched_at: float = 0.0     # when the in-flight launch started (monotonic)

    def ensure(self) -> None:
        """Requirement 1 (port check) + 2 (invisible background launch with fallback).

        NEVER stacks tunnels. The watchdog calls this every 5s while disconnected, but gcloud takes
        ~10-15s to bring the port up — so a launch already in flight is left alone until it either
        starts serving, exits on its own, or blows past _TUNNEL_BOOT_GRACE (wedged -> torn down and
        relaunched here). Without this guard each cold start fired 2-3 duplicate tunnels, and every
        extra one ORPHANED a PuTTY: self._proc only ever held the LAST handle, so stop() could not
        kill the earlier trees and their windows outlived the terminal.
        """
        if _ipc_port_open():
            print(f"[tunnel] {config.IPC_HOST}:{config.IPC_PORT} already live — reusing it.")
            return
        if self._proc is not None and self._proc.poll() is None:      # a launch WE started is still alive
            if (time.monotonic() - self._launched_at) < _TUNNEL_BOOT_GRACE:
                return                                                # still booting — let it finish, don't stack
            print("[tunnel] launch wedged past the boot grace — killing it and retrying.")
            self.stop()                                               # wedged: tear the tree down, relaunch below

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
            self._launched_at = time.monotonic()   # starts the boot grace -> the 5s watchdog won't stack another
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
