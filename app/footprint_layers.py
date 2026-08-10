"""Tier-3 order-flow analytics layers (spec §4, §8.2).

QPicture-backed shapes + pooled ``pg.TextItem`` labels (text in a QPicture would
be flipped/zoom-scaled by the inverted price ViewBox).

    BucketFootprintItem — Mode-10 per-bucket footprint ladder (bubbles / numbers / POC)
    DepthWallLayer      — resting DOM walls, literal book price + size-intensity (§8.2)

(The time-chart layers FootprintLayer / ImbalanceLayer / IcebergLayer were removed with the
time chart — Mode 10 is the only surface now.)
"""

from __future__ import annotations

from typing import Dict

import pyqtgraph as pg
from PySide6 import QtCore, QtGui

from . import config
from .chart_widgets import TextPool

_FP_TEXT_CAP = 600              # bound detailed footprint labels
DETAIL_PX_PER_TICK = 12.0      # show side-by-side rows once a tick row is this tall
MAX_DETAIL_BUCKETS = 20        # NUMBERS (full per-level ladder) only in a tight study view
MAX_BUBBLE_BUCKETS = 200       # TOP-3 bubbles up to here (3 x buckets <= 600 ellipses); wider -> none
BUBBLE_LABEL_MIN_PX_PER_X = 24.0   # print the bubble's volume value once a bucket column is this wide (zoomed-in); wider-out -> bubble only
_FP_BUB_LBL = QtGui.QColor(240, 240, 240)   # bubble volume value: near-white, centred on the bubble

# Footprint NUMBER colors: neon green (buy) / neon red (sell). An IMBALANCED level (a side's volume
# >= FOOTPRINT_IMB_ER_MULT x the bucket's 30b E/R) inverts to BLACK text on a neon background (carried as
# the optional 5th TextPool spec element).
_FP_NEON_BUY = QtGui.QColor(0, 255, 127)
_FP_NEON_SELL = QtGui.QColor(255, 7, 58)
_FP_BLACK = QtGui.QColor(0, 0, 0)
# Imbalance line colours (the horizontal under-number line): neon BLUE = buyer / neon ORANGE = seller.
_FP_IMB_BUY = QtGui.QColor(0, 153, 255)
_FP_IMB_SELL = QtGui.QColor(255, 128, 0)
_EMPTY: dict = {}
ICON_MIN_PX_PER_CANDLE = 22.0  # hide iceberg icons when candles get this narrow


def _node_levels(node: dict) -> Dict[str, dict]:
    return node.get("levels", {}) if isinstance(node, dict) else {}


def numbers_visible(n_vis: float, px_per_y: float) -> bool:
    """NUMBERS-regime gate (full per-level side-by-side numbers): a TIGHT view
    (<= MAX_DETAIL_BUCKETS visible buckets) with rows tall enough to read (>= DETAIL_PX_PER_TICK).
    Wider/shorter -> bubbles instead (see detail_visible)."""
    return n_vis <= MAX_DETAIL_BUCKETS and (px_per_y * config.TICK_SIZE) >= DETAIL_PX_PER_TICK


def detail_visible(n_vis: float) -> bool:
    """Is ANY per-level footprint detail shown (numbers OR bubbles)? -> a view up to
    MAX_BUBBLE_BUCKETS wide. The per-bucket POC dots ride THIS whole detail regime (single source
    of truth) -- so they stay with the bubbles and vanish only when you zoom out past them. Row
    height only decides numbers-vs-bubbles (not detail-vs-none), so it is NOT part of this gate."""
    return n_vis <= MAX_BUBBLE_BUCKETS


def _draw_bubble(p, xi, price, tot, buy, sell, max_vol, px_per_x, px_per_y):
    """Pixel-round volume bubble at (xi, price); radius ~ volume fraction, color = buy/sell
    dominance. Shared by the numbers-overflow fallback and the top-3 bubble regime."""
    frac = tot / max_vol
    r_px = 2.5 + 11.0 * frac
    rgb = config.RGB_GREEN_STD if buy >= sell else config.RGB_RED_STD
    col = QtGui.QColor(*rgb); col.setAlphaF(0.30 + 0.55 * frac)
    p.setBrush(QtGui.QBrush(col)); p.setPen(QtCore.Qt.NoPen)
    p.drawEllipse(QtCore.QPointF(xi, price), r_px / px_per_x, r_px / px_per_y)


# ---------------------------------------------------------------------------
# Mode-10 per-bucket footprint ladder (Stage 1) — the footprint is a property of
# the BUCKET (the volume bucket), drawn at its integer ordinal (not a time candle).
# ---------------------------------------------------------------------------
class BucketFootprintItem(pg.GraphicsObject):
    """Per-bucket footprint ladder for the Mode-10 volume canvas.

    Pixel-round bubble / side-by-side number / POC-highlight logic; ``x`` is the
    integer bucket ordinal (not a candle uTime) and the levels arrive per-bucket
    from ``b["levels"]`` (Stage 1: now carried on the ``BucketSnapshot`` wire).
    Culls to the visible X viewport (``x0``/``x1``) so the bubble scale and the
    600-label budget serve ONLY the on-screen buckets -- which is what lets
    the live edge get numbers instead of being starved by the oldest off-screen
    buckets eating the cap. Per visible level: a side-by-side buy/sell NUMBER when the
    tick row is tall enough (``>= DETAIL_PX_PER_TICK``, the 12px legibility gate) and
    the newest-first ``_FP_TEXT_CAP`` budget isn't spent; otherwise a pixel-round
    volume BUBBLE -- so no visible bucket is ever blank. The POC is the separate gold
    dot (``bc_poc``).
    """

    def __init__(self):
        super().__init__()
        self.picture = QtGui.QPicture()
        self._rect = QtCore.QRectF()
        self.buy_pool = TextPool(anchor=(0, 0.5), font_size=11, bold=True, z=22)
        self.sell_pool = TextPool(anchor=(1, 0.5), font_size=11, bold=True, z=22)
        self.bub_pool = TextPool(anchor=(0.5, 0.5), font_size=8, bold=True, z=23)   # volume value centred on a bubble (zoomed-in only)

    def attach_text(self, plot) -> None:
        self.buy_pool.attach(plot); self.sell_pool.attach(plot); self.bub_pool.attach(plot)

    def clear_text(self, plot) -> None:
        self.buy_pool.clear(plot); self.sell_pool.clear(plot); self.bub_pool.clear(plot)

    def setVisible(self, v: bool) -> None:  # noqa: N802
        super().setVisible(v)
        self.buy_pool.set_enabled(v); self.sell_pool.set_enabled(v); self.bub_pool.set_enabled(v)

    def update_data(self, x: list, levels_list: list, ber30s: list, ser30s: list,
                    x0: float, x1: float, width: float, px_per_x: float, px_per_y: float,
                    show_num_layer: bool = True, show_bub_layer: bool = True,
                    show_bub_vol: bool = False) -> None:
        self.picture = QtGui.QPicture()
        p = QtGui.QPainter(self.picture)
        px_per_x = max(1e-9, px_per_x); px_per_y = max(1e-9, px_per_y)
        half = width / 2.0
        buy_specs, sell_specs, bub_specs = [], [], []

        # FIX 1 -- cull to the visible X viewport (the x0/x1 pattern) so the bubble scale
        # AND the 600-label budget serve only the ON-SCREEN buckets. Root fix: the live edge
        # stops being starved by the oldest off-screen buckets eating the cap.
        visible = [(i, float(xi), levels) for i, (xi, levels) in enumerate(zip(x, levels_list))
                   if levels and x0 - width <= float(xi) <= x1 + width]

        # Part B -- a legibility+cost gradient driven by how many buckets are on screen:
        #   NUMBERS   (<= MAX_DETAIL_BUCKETS AND rows >= DETAIL_PX_PER_TICK): ALL levels as
        #             side-by-side numbers, newest-first, 600-cap -- the zoomed-in STUDY view.
        #   BUBBLES   (otherwise, <= MAX_BUBBLE_BUCKETS): the TOP-3 levels by total volume per
        #             bucket -- the significant nodes, no micro-noise. Bounded at 3 x buckets
        #             (<= 600 ellipses) BY CONSTRUCTION, so it stays cheap at wide zoom (vs the
        #             old uncapped one-ellipse-per-level).
        #   NONE      (> MAX_BUBBLE_BUCKETS): a sub-pixel blur anyway -- candles + POC carry it.
        n_vis = len(visible)
        # NUMBERS gated by the 'Footprint Ladder' layer, BUBBLES by the 'Candle Bubbles' layer. When numbers are
        # OFF (or zoomed out), bubbles fill in so there's no dead zone. (detail_visible also drives the POC.)
        show_numbers = numbers_visible(n_vis, px_per_y) and show_num_layer
        show_bubbles = (not show_numbers) and detail_visible(n_vis) and show_bub_layer

        lo_all = hi_all = None
        if show_numbers or show_bubbles:
            max_vol = 1.0
            for _i, _xi, levels in visible:
                for v in levels.values():
                    max_vol = max(max_vol, v.get("b", 0.0) + v.get("s", 0.0))

        if show_numbers:
            # FIX 3 -- fill the label budget NEWEST-first so the live edge is labeled before the
            # 600-cap is spent. FIX 2 -- cap-overflow / zero-total levels fall back to a BUBBLE so
            # no visible bucket is ever blank.
            for i, xi, levels in reversed(visible):
                ber, ser = ber30s[i], ser30s[i]
                buy_thr = config.FOOTPRINT_IMB_ER_MULT * ber if ber > 0 else None
                sell_thr = config.FOOTPRINT_IMB_ER_MULT * ser if ser > 0 else None
                for ps, v in levels.items():
                    price = float(ps); buy = v.get("b", 0.0); sell = v.get("s", 0.0)
                    tot = buy + sell
                    if tot <= 0:
                        continue
                    lo_all = price if lo_all is None else min(lo_all, price)
                    hi_all = price if hi_all is None else max(hi_all, price)
                    if len(buy_specs) < _FP_TEXT_CAP:
                        # IMBALANCE: a side's volume >= FOOTPRINT_IMB_ER_MULT x the bucket's 30b E/R ->
                        # that number inverts to BLACK on a neon bg (green buy / red sell).
                        buy_imb = buy_thr is not None and buy >= buy_thr
                        sell_imb = sell_thr is not None and sell >= sell_thr
                        buy_specs.append((xi + width * 0.10, price, f"{buy:.0f}",
                                          _FP_BLACK if buy_imb else _FP_NEON_BUY,
                                          _FP_NEON_BUY if buy_imb else None))
                        sell_specs.append((xi - width * 0.10, price, f"{sell:.0f}",
                                           _FP_BLACK if sell_imb else _FP_NEON_SELL,
                                           _FP_NEON_SELL if sell_imb else None))
                    elif show_bub_layer:            # cap-overflow level falls back to a bubble (only if bubbles on)
                        _draw_bubble(p, xi, price, tot, buy, sell, max_vol, px_per_x, px_per_y)
        elif show_bubbles:
            # TOP-3 levels by TOTAL volume (buy+sell) per bucket -- the significant nodes only. With the volume-label
            # stage on ('b' cycle stage 2) AND zoomed IN enough (a bucket column >= BUBBLE_LABEL_MIN_PX_PER_X wide),
            # each bubble also prints its total-volume VALUE, centred; otherwise it's the bubble alone.
            label_bubbles = show_bub_vol and px_per_x >= BUBBLE_LABEL_MIN_PX_PER_X
            for _i, xi, levels in visible:
                top3 = sorted(levels.items(),
                              key=lambda kv: kv[1].get("b", 0.0) + kv[1].get("s", 0.0),
                              reverse=True)[:3]
                for ps, v in top3:
                    price = float(ps); buy = v.get("b", 0.0); sell = v.get("s", 0.0)
                    tot = buy + sell
                    if tot <= 0:
                        continue
                    lo_all = price if lo_all is None else min(lo_all, price)
                    hi_all = price if hi_all is None else max(hi_all, price)
                    _draw_bubble(p, xi, price, tot, buy, sell, max_vol, px_per_x, px_per_y)
                    if label_bubbles:
                        txt = f"{tot / 1000.0:.1f}K" if tot >= 1000 else f"{tot:.0f}"
                        bub_specs.append((xi, price, txt, _FP_BUB_LBL))
        # (Imbalance lines are drawn by a SEPARATE always-on layer in the terminal — independent of the
        # footprint toggle — so only the black-on-neon number highlight lives here.)
        p.end()
        self.buy_pool.update(buy_specs); self.sell_pool.update(sell_specs); self.bub_pool.update(bub_specs)
        if lo_all is None:
            lo_all, hi_all = 0.0, 1.0
        bx0 = (float(x[0]) - half) if x else 0.0
        bx1 = (float(x[-1]) + half) if x else 1.0
        # X bounds = full bucket extent (generous, no pan-cull flicker); Y bounds = the
        # visible level range (within candle [low,high], so the item never extends the
        # one-shot Mode-10 Y-fit beyond the candles).
        self._rect = QtCore.QRectF(bx0, lo_all, max(1.0, bx1 - bx0), max(1e-6, hi_all - lo_all))
        self.prepareGeometryChange(); self.update()

    def paint(self, p, *a): p.drawPicture(0, 0, self.picture)
    def boundingRect(self): return self._rect


# ---------------------------------------------------------------------------
# Depth-map walls (spec §8.2) — literal book price + size-intensity (no clustering)
# ---------------------------------------------------------------------------
class DepthWallLayer(pg.GraphicsObject):
    def __init__(self):
        super().__init__()
        self.picture = QtGui.QPicture()
        self._rect = QtCore.QRectF()
        self.threshold = 1000.0
        self._walls = []   # drawn (>= threshold SOL) walls as (price, qty, side) for hover hit-test
        self._sig = None   # #2 gate: last (viewport, threshold, drawn-walls) fingerprint

    def update_data(self, depth: dict, x0: float, x1: float) -> None:
        # Build the drawable rows (levels >= threshold) FIRST — also the gate fingerprint. Each wall
        # is one real level at its LITERAL book price (NO clustering) so the walls align price-for-
        # price with the COB and the hover value = that level's true SOL qty.
        rows = []
        for side, base in (("bids", (46, 160, 67)), ("asks", (248, 81, 73))):
            for lvl in depth.get(side, []):
                try:
                    price, qty = float(lvl[0]), float(lvl[1])
                except (ValueError, IndexError):
                    continue
                if qty >= self.threshold:
                    rows.append((price, qty, side, base))
        # #2 gate: the 20Hz DOM loop calls this every frame, but the book pulses ~2.5Hz and a quiet
        # view is static. Rebuild + repaint ONLY when the DRAWN walls, the viewport, or the threshold
        # changed — so a static book/view stops dirtying the scene every frame (removes the ~62ms idle
        # floor). self._walls is preserved on skip, so the hover hit-test still resolves. A purely
        # sub-threshold change doesn't enter `rows` -> correctly skips; a threshold CROSSING changes
        # the `rows` set -> correctly redraws.
        sig = (round(x0, 4), round(x1, 4), self.threshold,
               tuple((r[0], r[1], r[2]) for r in rows))
        if sig == self._sig:
            return
        self._sig = sig

        self.picture = QtGui.QPicture()
        p = QtGui.QPainter(self.picture)
        max_q = max((q for _, q, _, _ in rows), default=1.0)
        walls = []
        for price, qty, side, base in rows:
            frac = min(1.0, qty / max_q)                       # size -> alpha + line width
            col = QtGui.QColor(*base); col.setAlphaF(0.35 + 0.55 * frac)
            pen = QtGui.QPen(col); pen.setCosmetic(True); pen.setWidthF(1.0 + 3.0 * frac)
            p.setPen(pen)
            p.drawLine(QtCore.QPointF(x0, price), QtCore.QPointF(x1, price))
            walls.append((price, qty, "bid" if side == "bids" else "ask"))
        p.end()
        self._walls = walls
        self._rect = QtCore.QRectF(x0, -1e6, max(1.0, x1 - x0), 2e6)
        self.prepareGeometryChange(); self.update()

    def nearest_wall(self, price: float, tol: float):
        """Nearest DRAWN wall (>= threshold SOL, literal price) to ``price`` within ``tol`` price
        units. Returns (price, qty, side) or None — for the Mode-10 hover-volume tooltip."""
        best = None
        for wp, qty, side in self._walls:
            d = abs(wp - price)
            if d <= tol and (best is None or d < best[0]):
                best = (d, wp, qty, side)
        return (best[1], best[2], best[3]) if best else None

    def paint(self, p, *a): p.drawPicture(0, 0, self.picture)
    def boundingRect(self): return self._rect
