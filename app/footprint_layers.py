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
ICON_MIN_PX_PER_CANDLE = 22.0  # hide iceberg icons when candles get this narrow


def _node_levels(node: dict) -> Dict[str, dict]:
    return node.get("levels", {}) if isinstance(node, dict) else {}


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
        self.buy_pool = TextPool(anchor=(0, 0.5), font_size=9, bold=True, z=22)
        self.sell_pool = TextPool(anchor=(1, 0.5), font_size=9, bold=True, z=22)

    def attach_text(self, plot) -> None:
        self.buy_pool.attach(plot); self.sell_pool.attach(plot)

    def clear_text(self, plot) -> None:
        self.buy_pool.clear(plot); self.sell_pool.clear(plot)

    def setVisible(self, v: bool) -> None:  # noqa: N802
        super().setVisible(v)
        self.buy_pool.set_enabled(v); self.sell_pool.set_enabled(v)

    def update_data(self, x: list, levels_list: list, x0: float, x1: float,
                    width: float, px_per_x: float, px_per_y: float) -> None:
        self.picture = QtGui.QPicture()
        p = QtGui.QPainter(self.picture)
        px_per_x = max(1e-9, px_per_x); px_per_y = max(1e-9, px_per_y)
        detailed = (px_per_y * config.TICK_SIZE) >= DETAIL_PX_PER_TICK   # kept: 12px legibility gate
        half = width / 2.0
        buy_specs, sell_specs = [], []

        # FIX 1 -- cull to the visible X viewport (the x0/x1 pattern) so the bubble scale
        # AND the 600-label budget serve only the ON-SCREEN buckets. Root fix: the live edge
        # stops being starved by the oldest off-screen buckets eating the cap.
        visible = [(float(xi), levels) for xi, levels in zip(x, levels_list)
                   if levels and x0 - width <= float(xi) <= x1 + width]

        max_vol = 1.0
        for _xi, levels in visible:
            for v in levels.values():
                max_vol = max(max_vol, v.get("b", 0.0) + v.get("s", 0.0))

        # FIX 3 -- in numbers mode fill the label budget NEWEST-first (reverse the
        # visible order) so the live edge is labeled before the cap is spent.
        # FIX 2 -- any level that can't get a number (rows too short = bubble mode, OR
        # the cap is spent) falls back to a BUBBLE, so no visible bucket is ever blank.
        lo_all = hi_all = None
        for xi, levels in (reversed(visible) if detailed else visible):
            for ps, v in levels.items():
                price = float(ps); buy = v.get("b", 0.0); sell = v.get("s", 0.0)
                tot = buy + sell
                if tot <= 0:
                    continue
                lo_all = price if lo_all is None else min(lo_all, price)
                hi_all = price if hi_all is None else max(hi_all, price)
                if detailed and len(buy_specs) < _FP_TEXT_CAP:
                    buy_specs.append((xi + width * 0.10, price, f"{buy:.0f}", QtGui.QColor(20, 110, 50)))
                    sell_specs.append((xi - width * 0.10, price, f"{sell:.0f}", QtGui.QColor(150, 30, 25)))
                else:
                    frac = tot / max_vol
                    r_px = 2.5 + 11.0 * frac
                    rgb = config.RGB_GREEN_STD if buy >= sell else config.RGB_RED_STD
                    col = QtGui.QColor(*rgb); col.setAlphaF(0.30 + 0.55 * frac)
                    p.setBrush(QtGui.QBrush(col)); p.setPen(QtCore.Qt.NoPen)
                    p.drawEllipse(QtCore.QPointF(xi, price), r_px / px_per_x, r_px / px_per_y)
        p.end()
        self.buy_pool.update(buy_specs); self.sell_pool.update(sell_specs)
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

    def update_data(self, depth: dict, x0: float, x1: float) -> None:
        self.picture = QtGui.QPicture()
        p = QtGui.QPainter(self.picture)
        # Draw each resting level >= threshold SOL at its LITERAL book price (NO clustering) — so
        # the walls align price-for-price with the COB, and each wall = one real level (its hover
        # value = that level's true SOL qty, not a cluster sum). Mirrors the old version's render.
        rows = []
        for side, base in (("bids", (46, 160, 67)), ("asks", (248, 81, 73))):
            for lvl in depth.get(side, []):
                try:
                    price, qty = float(lvl[0]), float(lvl[1])
                except (ValueError, IndexError):
                    continue
                if qty >= self.threshold:
                    rows.append((price, qty, side, base))
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
