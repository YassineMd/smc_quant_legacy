"""Tier-3 order-flow analytics layers (spec §4, §8.2).

QPicture-backed shapes + pooled ``pg.TextItem`` labels (text in a QPicture would
be flipped/zoom-scaled by the inverted price ViewBox).

    FootprintLayer  — pixel-round volume bubbles, POC highlight, side-by-side rows
                      with in-column imbalance highlighting (§4.1, fixes #3-#7)
    ImbalanceLayer  — stacked-imbalance GAP zones with live mitigation (fix #6)
    IcebergLayer    — 4%/65% absorption marks, dashed projection to the right
                      edge, icons that hide when zoomed out (fixes #9, #13)
    DepthWallLayer  — resting DOM walls, clustered + intensity-scaled (§8.2)
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
# Footprint bubbles + detailed rows (fixes #3, #4, #5, #7)
# ---------------------------------------------------------------------------
class FootprintLayer(pg.GraphicsObject):
    def __init__(self):
        super().__init__()
        self.picture = QtGui.QPicture()
        self._rect = QtCore.QRectF()
        self.buy_pool = TextPool(anchor=(0, 0.5), font_size=10, bold=True, z=22)
        self.sell_pool = TextPool(anchor=(1, 0.5), font_size=10, bold=True, z=22)

    def attach_text(self, plot) -> None:
        self.buy_pool.attach(plot); self.sell_pool.attach(plot)

    def setVisible(self, v: bool) -> None:  # noqa: N802
        super().setVisible(v)
        self.buy_pool.set_enabled(v); self.sell_pool.set_enabled(v)

    def update_data(self, footprints: dict, x0: float, x1: float, width: float,
                    px_per_x: float, px_per_y: float) -> None:
        self.picture = QtGui.QPicture()
        p = QtGui.QPainter(self.picture)
        px_per_x = max(1e-9, px_per_x); px_per_y = max(1e-9, px_per_y)
        px_per_tick = px_per_y * config.TICK_SIZE
        detailed = px_per_tick >= DETAIL_PX_PER_TICK
        ts = config.TICK_SIZE
        buy_specs, sell_specs = [], []

        max_vol = 1.0
        for node in footprints.values():
            for v in _node_levels(node).values():
                max_vol = max(max_vol, v.get("b", 0) + v.get("s", 0))

        for utime, node in footprints.items():
            x = float(utime)
            if x < x0 - width or x > x1 + width:
                continue
            levels = _node_levels(node)
            if not levels:
                continue

            # POC = highest-volume price row in this candle (fix #4)
            poc_price, poc_v = None, 0.0
            for ps, v in levels.items():
                t = v.get("b", 0.0) + v.get("s", 0.0)
                if t > poc_v:
                    poc_v, poc_price = t, float(ps)

            for ps, v in levels.items():
                price = float(ps)
                buy = v.get("b", 0.0); sell = v.get("s", 0.0)
                tot = buy + sell
                if tot <= 0:
                    continue

                if detailed:
                    # diagonal imbalance -> highlight box behind the number (fix #7)
                    below = levels.get(f"{price - ts:.2f}")
                    above = levels.get(f"{price + ts:.2f}")
                    s_below = below.get("s", 0.0) if below else 0.0
                    b_above = above.get("b", 0.0) if above else 0.0
                    buy_imb = buy >= config.IMBALANCE_RATIO * s_below and buy > 0
                    sell_imb = sell >= config.IMBALANCE_RATIO * b_above and sell > 0
                    hh = ts * 0.9
                    if buy_imb:
                        col = QtGui.QColor(*config.COLOR_IMB_BUY); col.setAlphaF(0.45)
                        p.setBrush(col); p.setPen(QtCore.Qt.NoPen)
                        p.drawRect(QtCore.QRectF(x + width * 0.05, price - hh / 2, width * 0.42, hh))
                    if sell_imb:
                        col = QtGui.QColor(*config.COLOR_IMB_SELL); col.setAlphaF(0.45)
                        p.setBrush(col); p.setPen(QtCore.Qt.NoPen)
                        p.drawRect(QtCore.QRectF(x - width * 0.47, price - hh / 2, width * 0.42, hh))
                    if len(buy_specs) < _FP_TEXT_CAP:
                        buy_specs.append((x + width * 0.10, price, f"{buy:.0f}", QtGui.QColor(20, 110, 50)))
                        sell_specs.append((x - width * 0.10, price, f"{sell:.0f}", QtGui.QColor(150, 30, 25)))
                else:
                    # pixel-round bubble (fix #3): equal pixel radii despite x=sec, y=price
                    frac = tot / max_vol
                    r_px = 2.5 + 11.0 * frac
                    rx = r_px / px_per_x
                    ry = r_px / px_per_y
                    rgb = config.RGB_GREEN_STD if buy >= sell else config.RGB_RED_STD
                    col = QtGui.QColor(*rgb); col.setAlphaF(0.30 + 0.55 * frac)
                    p.setBrush(QtGui.QBrush(col)); p.setPen(QtCore.Qt.NoPen)
                    p.drawEllipse(QtCore.QPointF(x, price), rx, ry)

                # POC highlight ring — gold, drawn over bubble/row (fix #4)
                if price == poc_price:
                    pen = QtGui.QPen(QtGui.QColor("#f1c40f")); pen.setCosmetic(True); pen.setWidth(2)
                    p.setPen(pen); p.setBrush(QtCore.Qt.NoBrush)
                    if detailed:
                        p.drawRect(QtCore.QRectF(x - width / 2, price - ts / 2, width, ts))
                    else:
                        rpx = 7.0
                        p.drawEllipse(QtCore.QPointF(x, price), rpx / px_per_x, rpx / px_per_y)
        p.end()
        self.buy_pool.update(buy_specs); self.sell_pool.update(sell_specs)
        self._rect = QtCore.QRectF(x0, -1e6, max(1.0, x1 - x0), 2e6)
        self.prepareGeometryChange(); self.update()

    def paint(self, p, *a): p.drawPicture(0, 0, self.picture)
    def boundingRect(self): return self._rect


# ---------------------------------------------------------------------------
# Mode-10 per-bucket footprint ladder (Stage 1) — ordinal-axis twin of
# FootprintLayer: the footprint is a property of the BUCKET, not the time-candle.
# ---------------------------------------------------------------------------
class BucketFootprintItem(pg.GraphicsObject):
    """Per-bucket footprint ladder for the Mode-10 volume canvas.

    Identical bubble/number/POC logic to :class:`FootprintLayer`; the ONLY
    difference is the X mapping — ``x`` is the integer bucket ordinal (not the
    candle uTime) and the levels arrive per-bucket from ``b["levels"]`` (Stage 1:
    now carried on the ``BucketSnapshot`` wire). Culls to the visible X viewport
    (``x0``/``x1``, the same pattern FootprintLayer already uses) so the bubble scale
    and the 600-label budget serve ONLY the on-screen buckets -- which is what lets
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

        # FIX 1 -- cull to the visible X viewport (the same x0/x1 pattern FootprintLayer
        # already uses) so the bubble scale AND the 600-label budget serve only the
        # ON-SCREEN buckets. Root fix: the live edge stops being starved by the oldest
        # off-screen buckets eating the cap.
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
# Imbalance GAP zones with mitigation (fix #6)
# ---------------------------------------------------------------------------
class ImbalanceLayer(pg.GraphicsObject):
    def __init__(self):
        super().__init__()
        self.picture = QtGui.QPicture()
        self._rect = QtCore.QRectF()
        self.tag_pool = TextPool(anchor=(1, 0.5), font_size=9, z=23)

    def attach_text(self, plot) -> None:
        self.tag_pool.attach(plot)

    def setVisible(self, v: bool) -> None:  # noqa: N802
        super().setVisible(v)
        self.tag_pool.set_enabled(v)

    def update_data(self, footprints: dict, candles_by_t: dict,
                    x0: float, x1: float, x_right: float) -> None:
        self.picture = QtGui.QPicture()
        p = QtGui.QPainter(self.picture)
        ts = config.TICK_SIZE
        tag_specs = []

        # 1. collect stacked-imbalance gap zones
        gaps = []  # (x, top, bottom, is_buy)
        for utime, node in footprints.items():
            x = float(utime)
            levels = _node_levels(node)
            if not levels:
                continue
            prices = sorted(float(k) for k in levels.keys())

            def vol(pr):
                n = levels.get(f"{pr:.2f}")
                return (n.get("b", 0.0), n.get("s", 0.0)) if n else (0.0, 0.0)

            run_buy, run_sell = [], []
            for pr in prices:
                b, _ = vol(pr)
                _, s_below = vol(round(pr - ts, 2))
                b_above, _ = vol(round(pr + ts, 2))
                s = vol(pr)[1]
                if b >= config.IMBALANCE_RATIO * s_below and b > 0:
                    run_buy.append(pr); run_sell = []
                elif s >= config.IMBALANCE_RATIO * b_above and s > 0:
                    run_sell.append(pr); run_buy = []
                else:
                    if len(run_buy) >= config.STACKED_IMBALANCE_MIN:
                        gaps.append((x, max(run_buy), min(run_buy), True))
                    if len(run_sell) >= config.STACKED_IMBALANCE_MIN:
                        gaps.append((x, max(run_sell), min(run_sell), False))
                    run_buy, run_sell = [], []
            if len(run_buy) >= config.STACKED_IMBALANCE_MIN:
                gaps.append((x, max(run_buy), min(run_buy), True))
            if len(run_sell) >= config.STACKED_IMBALANCE_MIN:
                gaps.append((x, max(run_sell), min(run_sell), False))

        # 2. draw only UNMITIGATED gaps; a fill makes them vanish (fix #6)
        for gx, top, bottom, is_buy in gaps:
            mitigated = False
            for ct, c in candles_by_t.items():
                if ct <= gx:
                    continue
                low, high = c[2], c[1]
                # price returning into the zone fills it
                if is_buy and low <= top:
                    mitigated = True; break
                if (not is_buy) and high >= bottom:
                    mitigated = True; break
            if mitigated:
                continue
            rgba = config.RGBA_CHANNEL_BUY if is_buy else config.RGBA_CHANNEL_SELL
            fill = QtGui.QColor(int(rgba[0]), int(rgba[1]), int(rgba[2])); fill.setAlphaF(0.18)
            pen = QtGui.QPen(QtGui.QColor(int(rgba[0]), int(rgba[1]), int(rgba[2]))); pen.setCosmetic(True)
            p.setBrush(fill); p.setPen(pen)
            p.drawRect(QtCore.QRectF(gx, bottom, x_right - gx, top - bottom))
            label = "⚡ Buy Imb Gap" if is_buy else "⚡ Sell Imb Gap"
            col = QtGui.QColor(46, 160, 67) if is_buy else QtGui.QColor(231, 76, 60)
            tag_specs.append((x_right, (top + bottom) / 2, label, col))
        p.end()
        self.tag_pool.update(tag_specs)
        self._rect = QtCore.QRectF(x0, -1e6, max(1.0, x_right - x0), 2e6)
        self.prepareGeometryChange(); self.update()

    def paint(self, p, *a): p.drawPicture(0, 0, self.picture)
    def boundingRect(self): return self._rect


# ---------------------------------------------------------------------------
# Icebergs (fixes #9, #13)
# ---------------------------------------------------------------------------
class IcebergLayer(pg.GraphicsObject):
    def __init__(self):
        super().__init__()
        self.picture = QtGui.QPicture()
        self._rect = QtCore.QRectF()
        self.ice_pool = TextPool(anchor=(1, 0.5), font_size=12, z=24)

    def attach_text(self, plot) -> None:
        self.ice_pool.attach(plot)

    def setVisible(self, v: bool) -> None:  # noqa: N802
        super().setVisible(v)
        self.ice_pool.set_enabled(v)

    def update_data(self, footprints: dict, candles_by_t: dict,
                    x0: float, x1: float, x_right: float, show_icons: bool = True) -> None:
        self.picture = QtGui.QPicture()
        p = QtGui.QPainter(self.picture)
        ice_specs = []

        for utime, node in footprints.items():
            x = float(utime)
            levels = _node_levels(node)
            cand_vol = sum(v.get("b", 0) + v.get("s", 0) for v in levels.values())
            if cand_vol <= 0:
                continue
            for p_str, v in levels.items():
                buy, sell = v.get("b", 0.0), v.get("s", 0.0)
                tot = buy + sell
                if tot <= 0:
                    continue
                share = tot / cand_vol
                skew = abs(buy - sell) / tot
                if share >= config.ICEBERG_VOL_SHARE and skew >= config.ICEBERG_SKEW:
                    price = float(p_str)
                    is_buy = buy > sell
                    color = config.COLOR_ICEBERG_BUY if is_buy else config.COLOR_ICEBERG_SELL

                    # mitigation: a later candle closing past the level ends the track
                    x_end, mitigated = x_right, False
                    for ct, c in candles_by_t.items():
                        if ct <= x:
                            continue
                        close = c[3]
                        if (is_buy and close < price) or (not is_buy and close > price):
                            x_end, mitigated = float(ct), True
                            break

                    pen = QtGui.QPen(QtGui.QColor(color)); pen.setCosmetic(True)
                    pen.setStyle(QtCore.Qt.DashLine)
                    pen.setWidth(2 if not mitigated else 1)
                    p.setPen(pen)
                    # unmitigated projects all the way to the right edge (fix #9)
                    end_x = x_right if not mitigated else x_end
                    p.drawLine(QtCore.QPointF(x, price), QtCore.QPointF(end_x, price))
                    if show_icons:
                        ice_specs.append((x, price, "🧊", QtGui.QColor(color)))
        p.end()
        # fix #13: icons fade out when zoomed too far; lines remain
        self.ice_pool.update(ice_specs if show_icons else [])
        self._rect = QtCore.QRectF(x0, -1e6, max(1.0, x_right - x0), 2e6)
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
