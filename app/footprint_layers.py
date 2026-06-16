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
    now carried on the ``BucketSnapshot`` wire). Pixel-round bubbles when zoomed
    out, side-by-side buy/sell numbers when a tick row is tall enough. The POC is
    marked by the separate gold dot (``bc_poc``), so this layer draws only the
    volume distribution (A1 dropped the redundant in-ladder POC ring).
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

    def update_data(self, x: list, levels_list: list, width: float,
                    px_per_x: float, px_per_y: float) -> None:
        self.picture = QtGui.QPicture()
        p = QtGui.QPainter(self.picture)
        px_per_x = max(1e-9, px_per_x); px_per_y = max(1e-9, px_per_y)
        detailed = (px_per_y * config.TICK_SIZE) >= DETAIL_PX_PER_TICK
        half = width / 2.0
        buy_specs, sell_specs = [], []

        # bubble scale = max single-row volume across the visible buckets (same as
        # FootprintLayer, but over per-bucket levels rather than per-candle nodes)
        max_vol = 1.0
        for levels in levels_list:
            for v in (levels or {}).values():
                max_vol = max(max_vol, v.get("b", 0.0) + v.get("s", 0.0))

        lo_all = hi_all = None
        for xi, levels in zip(x, levels_list):
            if not levels:
                continue
            xi = float(xi)
            for ps, v in levels.items():
                price = float(ps); buy = v.get("b", 0.0); sell = v.get("s", 0.0)
                tot = buy + sell
                if tot <= 0:
                    continue
                lo_all = price if lo_all is None else min(lo_all, price)
                hi_all = price if hi_all is None else max(hi_all, price)
                if detailed:
                    if len(buy_specs) < _FP_TEXT_CAP:
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
        x0 = (float(x[0]) - half) if x else 0.0
        x1 = (float(x[-1]) + half) if x else 1.0
        # bounds are the real level range (within candle [low,high]) so the item never
        # extends the one-shot Mode-10 Y-fit beyond the candles.
        self._rect = QtCore.QRectF(x0, lo_all, max(1.0, x1 - x0), max(1e-6, hi_all - lo_all))
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
# Depth-map walls (spec §8.2) — clustered + intensity-scaled
# ---------------------------------------------------------------------------
class DepthWallLayer(pg.GraphicsObject):
    CLUSTER_TICKS = 2

    def __init__(self):
        super().__init__()
        self.picture = QtGui.QPicture()
        self._rect = QtCore.QRectF()
        self.threshold = 1000.0

    def _cluster(self, levels: list) -> list:
        rows = []
        for lvl in levels:
            try:
                price, qty = float(lvl[0]), float(lvl[1])
            except (ValueError, IndexError):
                continue
            if qty >= self.threshold:
                rows.append((price, qty))
        if not rows:
            return []
        rows.sort()
        gap = self.CLUSTER_TICKS * config.TICK_SIZE
        merged = []
        cur_p, cur_q, n = rows[0][0], rows[0][1], 1
        for price, qty in rows[1:]:
            if price - cur_p <= gap:
                cur_p = (cur_p * n + price) / (n + 1); cur_q += qty; n += 1
            else:
                merged.append((cur_p, cur_q)); cur_p, cur_q, n = price, qty, 1
        merged.append((cur_p, cur_q))
        return merged

    def update_data(self, depth: dict, x0: float, x1: float) -> None:
        self.picture = QtGui.QPicture()
        p = QtGui.QPainter(self.picture)
        all_q = []
        for side in ("bids", "asks"):
            all_q += [q for _, q in self._cluster(depth.get(side, []))]
        max_q = max(all_q) if all_q else 1.0

        for side, base in (("bids", (46, 160, 67)), ("asks", (248, 81, 73))):
            for price, qty in self._cluster(depth.get(side, [])):
                frac = min(1.0, qty / max_q)
                col = QtGui.QColor(*base); col.setAlphaF(0.35 + 0.55 * frac)
                pen = QtGui.QPen(col); pen.setCosmetic(True); pen.setWidthF(1.0 + 3.0 * frac)
                p.setPen(pen)
                p.drawLine(QtCore.QPointF(x0, price), QtCore.QPointF(x1, price))
        p.end()
        self._rect = QtCore.QRectF(x0, -1e6, max(1.0, x1 - x0), 2e6)
        self.prepareGeometryChange(); self.update()

    def paint(self, p, *a): p.drawPicture(0, 0, self.picture)
    def boundingRect(self): return self._rect
