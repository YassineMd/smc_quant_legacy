"""Tier-3 12-line 3D statistics overlay (spec §4.3).

On cursor hover over a candle the window calls :func:`compute_stats` with a
snapshot and the candle's open-time; it returns the 12 formatted metric lines
plus the structural state-matrix verdict (§4.3.2). :class:`StatsOverlay` is a
floating monospace QLabel the window positions near the cursor. The metrics are
derived from the per-candle footprint node, OHLCV, ΔOI, and liquidations.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from . import config

_BASELINE = 20  # rolling E/R + velocity baseline window (candles)


def _node_metrics(node: dict, ohlc: list, tf_secs: int) -> dict:
    levels = node.get("levels", {}) if isinstance(node, dict) else {}
    buy = sum(v.get("b", 0.0) for v in levels.values())
    sell = sum(v.get("s", 0.0) for v in levels.values())
    vol = buy + sell
    oi_open = node.get("oi_open", 0.0)
    oi_close = node.get("oi_close", 0.0)
    delta_oi = oi_close - oi_open

    o, h, l, c, _ = ohlc
    ticks = max(1.0, abs(h - l) / config.TICK_SIZE)
    buyer_er = buy / ticks
    seller_er = sell / ticks

    # 4-vector decomposition at candle granularity (same formula as the engine,
    # Step 3): OI-confirmed flow only, no 50/50 churn invention. `churn` is the
    # OI-neutral remainder; conservation opL+opS+clL+clS+churn == vol.
    b_ratio = buy / vol if vol else 0.0
    s_ratio = sell / vol if vol else 0.0
    oi_mag = min(abs(delta_oi), vol)
    churn = vol - oi_mag
    opL = oi_mag * b_ratio if delta_oi > 0 else 0.0
    opS = oi_mag * s_ratio if delta_oi > 0 else 0.0
    clL = oi_mag * s_ratio if delta_oi < 0 else 0.0
    clS = oi_mag * b_ratio if delta_oi < 0 else 0.0

    # POC + its position within the bar
    poc, poc_v = c, 0.0
    for p_str, v in levels.items():
        t = v.get("b", 0.0) + v.get("s", 0.0)
        if t > poc_v:
            poc_v, poc = t, float(p_str)
    poc_pos = (poc - l) / (h - l) if h > l else 0.5

    liqs = node.get("liquidations", [])
    liq_long = sum(x.get("qty", 0.0) for x in liqs if x.get("side") == "SELL")
    liq_short = sum(x.get("qty", 0.0) for x in liqs if x.get("side") == "BUY")

    return {
        "vol": vol, "buy": buy, "sell": sell, "delta": buy - sell,
        "delta_oi": delta_oi, "opL": opL, "opS": opS, "clL": clL, "clS": clS,
        "churn": churn,
        "buyer_er": buyer_er, "seller_er": seller_er,
        "vel": vol / max(1, tf_secs), "poc_pos": poc_pos,
        "liq_long": liq_long, "liq_short": liq_short,
        "green": c >= o, "ohlc": (o, h, l, c),
    }


def compute_stats(snap: dict, t: int) -> Optional[Tuple[List[str], str]]:
    times = snap["times"]
    if len(times) == 0:
        return None
    footprints = snap["footprints"]
    node = footprints.get(str(t))
    if not node:
        return None

    # locate candle index
    tlist = [int(x) for x in times]
    if t not in tlist:
        return None
    idx = tlist.index(t)
    tf_secs = config.TF_SECONDS.get(snap["tf"], 60)
    ohlcv = snap["ohlcv"]
    m = _node_metrics(node, list(ohlcv[idx]), tf_secs)

    # rolling baselines from the previous candles' footprints
    b_ers, s_ers, vels = [], [], []
    for j in range(max(0, idx - _BASELINE), idx):
        n = footprints.get(str(tlist[j]))
        if n:
            mm = _node_metrics(n, list(ohlcv[j]), tf_secs)
            b_ers.append(mm["buyer_er"]); s_ers.append(mm["seller_er"]); vels.append(mm["vel"])
    avg_b = max(0.1, sum(b_ers) / len(b_ers)) if b_ers else 0.1
    avg_s = max(0.1, sum(s_ers) / len(s_ers)) if s_ers else 0.1
    avg_vel = max(0.1, sum(vels) / len(vels)) if vels else 0.1
    speed = m["vel"] / avg_vel
    b_anom = m["buyer_er"] / avg_b
    s_anom = m["seller_er"] / avg_s

    prev = None
    if idx > 0:
        pn = footprints.get(str(tlist[idx - 1]))
        if pn:
            prev = _node_metrics(pn, list(ohlcv[idx - 1]), tf_secs)
    prev_row = ohlcv[idx - 1] if idx > 0 else ohlcv[idx]
    prev_o, prev_h, prev_l, prev_c = prev_row[0], prev_row[1], prev_row[2], prev_row[3]

    o, h, l, c = m["ohlc"]
    delta_pct = (m["delta"] / m["vol"] * 100) if m["vol"] else 0.0

    pos, neg, dim = "#2ecc71", "#e74c3c", "#9aa0aa"
    buy_c, sell_c = "#2ecc71", "#e74c3c"
    er_b = "#00e5ff" if b_anom >= 1.5 else dim
    er_s = "#ff4dff" if s_anom >= 1.5 else dim
    spd_c = "#f1c40f" if speed >= 1.2 else dim
    d_c = pos if m["delta"] >= 0 else neg
    oi_c = pos if m["delta_oi"] >= 0 else neg

    def row(label, value, color):
        return (f"<span style='color:{dim}'>{label}</span> "
                f"<span style='color:{color}'>{value}</span>")

    lines = [
        row("V    ", f"{m['vol']:.0f}", "#e6e6e6"),
        f"<span style='color:{dim}'>S|B  </span>"
        f"<span style='color:{sell_c}'>{m['sell']:.0f}</span>"
        f"<span style='color:{dim}'> | </span>"
        f"<span style='color:{buy_c}'>{m['buy']:.0f}</span>",
        row("Δ    ", f"{m['delta']:+.0f} ({delta_pct:+.1f}%)", d_c),
        row("ΔOI  ", f"{m['delta_oi']:+.0f}", oi_c),
        row("OpL  ", f"{m['opL']:.0f}", pos),
        row("OpS  ", f"{m['opS']:.0f}", neg),
        row("ClL  ", f"{m['clL']:.0f}", "#e67e22"),
        row("ClS  ", f"{m['clS']:.0f}", "#00bcd4"),
        row("B-E/R ", f"x{b_anom:.2f}", er_b),
        row("S-E/R ", f"x{s_anom:.2f}", er_s),
        row("VEL  ", f"{m['vel']:.1f}/s", "#e6e6e6"),
        f"<span style='color:{spd_c}'>x{speed:.1f} Speed</span>",
    ]
    return lines, _verdict(m, prev, speed, b_anom, s_anom, prev_c, prev_h, prev_l)


def _verdict_color(verdict: str) -> str:
    if verdict.startswith("🌟"):
        return "#f1c40f"
    if "STRONG BULL" in verdict or "SHORT SQUEEZE" in verdict or "BEAR TRAP" in verdict:
        return "#2ecc71"
    if "STRONG BEAR" in verdict or "LONG SQUEEZE" in verdict or "BULL TRAP" in verdict:
        return "#e74c3c"
    if "EXHAUSTION" in verdict or "COIL" in verdict:
        return "#e67e22"
    return "#9aa0aa"


def _verdict(m, prev, speed, b_anom, s_anom, prev_c, prev_h, prev_l) -> str:
    green = m["green"]
    o, h, l, c = m["ohlc"]
    doi = m["delta_oi"]
    liq_long, liq_short = m["liq_long"], m["liq_short"]
    liq_total = liq_long + liq_short
    delta = m["delta"]
    inside = (h <= prev_h and l >= prev_l)
    prev_green = prev_c >= (prev_h + prev_l) / 2 if prev is None else prev["green"]
    liq_share = liq_total / m["vol"] if m["vol"] else 0.0

    if green and liq_short > 0 and doi < 0 and speed > 1.2 and not prev_green:
        return "🌟 A+ SETUP: SHORT SQUEEZE"
    if (not green) and liq_long > 0 and doi < 0 and speed > 1.2 and prev_green:
        return "🌟 A+ SETUP: LONG SQUEEZE"
    if (not green) and delta > 0 and doi > 0 and b_anom > 1.5 and m["poc_pos"] > 0.6 and h > prev_h:
        return "🌟 A+ SETUP: BULL TRAP"
    if green and delta < 0 and doi > 0 and s_anom > 1.5 and m["poc_pos"] < 0.4 and l < prev_l:
        return "🌟 A+ SETUP: BEAR TRAP"
    if inside and liq_share >= 0.05:
        return "LIQUIDITY COIL"
    if green and delta > 0 and doi > 0:
        return "STRONG BULL"
    if (not green) and delta < 0 and doi > 0:
        return "STRONG BEAR"
    if green and delta > 0 and doi < 0:
        return "BULL EXHAUSTION"
    if (not green) and delta < 0 and doi < 0:
        return "BEAR EXHAUSTION"
    if inside:
        return "CHOP (INSIDE BAR)"
    return "NEUTRAL / MIXED"


_QSS = """
QLabel {
  background-color: rgba(10, 12, 16, 0.92);
  color: #e6e6e6;
  font-family: "Consolas", monospace;
  font-size: 11px;
  border: 1px solid #2a2e39;
  border-radius: 3px;
  padding: 6px 8px;
}
"""


class StatsOverlay(QtWidgets.QLabel):
    def __init__(self, parent: QtWidgets.QWidget):
        super().__init__(parent)
        self.setStyleSheet(_QSS)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setTextFormat(QtCore.Qt.RichText)   # HTML color coding (patch #2)
        # A sibling widget (the hamburger menu) this overlay must stay BELOW: show_stats
        # raises every frame, which would otherwise cover an open menu panel.
        self.keep_under: Optional[QtWidgets.QWidget] = None
        self.hide()

    def set_content(self, lines: List[str], verdict: str) -> None:
        """Render the lines (+ optional verdict) and size the box; the caller positions it."""
        body = "<br>".join(lines)
        if verdict:
            vcol = _verdict_color(verdict)
            html = (f"<div style='font-family:Consolas;font-size:11px'>"
                    f"<b style='color:{vcol}'>{verdict}</b>"
                    f"<hr style='border:0;border-top:1px solid #2a2e39'>{body}</div>")
        else:
            html = f"<div style='font-family:Consolas;font-size:11px'>{body}</div>"
        self.setText(html)
        self.adjustSize()

    def show_raise(self) -> None:
        self.show()
        self.raise_()
        if self.keep_under is not None and self.keep_under.isVisible():
            self.stackUnder(self.keep_under)   # stay below the open hamburger menu

    def show_stats(self, lines: List[str], verdict: str, x: int, y: int) -> None:
        self.set_content(lines, verdict)
        self.move(x + 16, y + 16)
        self.show_raise()


class _FloorSlider(QtWidgets.QSlider):
    """Horizontal slider that paints a DOT on its groove at the boundary ``floor`` (absorption =
    validated-strength; eff-agg = forceful). Dot colour is parametrised."""

    def __init__(self, floor: float, dot_rgb=(241, 196, 15)):
        super().__init__(QtCore.Qt.Horizontal)
        self._floor = floor
        self._dot = dot_rgb

    def paintEvent(self, ev):   # noqa: N802 (Qt override)
        super().paintEvent(ev)
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        m = 7   # ~half a handle width of inset so the dot lines up with the handle travel
        x = m + int(self._floor * max(1, self.width() - 2 * m))
        y = self.height() // 2
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(*self._dot))
        p.drawEllipse(QtCore.QPointF(x, y), 4.0, 4.0)
        p.end()


class _ZoneThresholdSlider(QtWidgets.QWidget):
    """Floorless 0.00-1.00 threshold control for a Mode-10 zone layer. A DOT on the track marks a grounded
    boundary (``floor``): at/above = the distinctive regime, below = the ordinary/weaker one (a gradient).
    Emits ``changed(v)`` only on a USER drag (programmatic ``set_value`` is silent). Subclassed per measure
    (absorption rides s; effective-aggression rides force f) — the axis label / tags / accent differ.

    Also carries a BULL/BEAR side filter (two toggles under the slider, both ON by default): the caller reads
    :meth:`sides` to show only green (bull) and/or red (bear) zones for this layer, and gets :attr:`side_changed`
    on a user toggle."""
    changed = QtCore.Signal(float)
    side_changed = QtCore.Signal()   # a Bull/Bear zone filter toggle flipped (user)

    def __init__(self, parent, floor, axis_label, above_tag, below_tag,
                 above_col, below_col, border_col="#2a2e39", dot_rgb=(241, 196, 15)):
        super().__init__(parent)
        self._floor = floor
        self._axis = axis_label
        self._above = (above_tag, above_col)
        self._below = (below_tag, below_col)
        self._silent = False
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 5, 8, 7); lay.setSpacing(3)
        self.lbl = QtWidgets.QLabel()
        self.lbl.setTextFormat(QtCore.Qt.RichText)
        self.lbl.setStyleSheet("color:#c8cdd6; background:transparent; font-family:Consolas; font-size:11px;")
        lay.addWidget(self.lbl)
        self.slider = _FloorSlider(floor, dot_rgb)
        self.slider.setRange(0, 100)
        self.slider.setFixedHeight(16)
        self.slider.valueChanged.connect(self._on_change)
        lay.addWidget(self.slider)
        # Bull/Bear side filter — independently show the green (bull) / red (bear) zones of THIS layer (default both).
        srow = QtWidgets.QHBoxLayout(); srow.setContentsMargins(0, 4, 0, 0); srow.setSpacing(6)
        self.bull_btn = self._side_btn("▲ Bull", "#2ecc71")
        self.bear_btn = self._side_btn("▼ Bear", "#e74c3c")
        self.bull_btn.toggled.connect(lambda _c: self.side_changed.emit())
        self.bear_btn.toggled.connect(lambda _c: self.side_changed.emit())
        srow.addWidget(self.bull_btn); srow.addWidget(self.bear_btn)
        lay.addLayout(srow)
        self.setFixedWidth(210)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)   # so the stylesheet bg actually paints
        self.setStyleSheet(f"{type(self).__name__}{{background:rgba(17,19,26,235);"
                           f"border:1px solid {border_col}; border-radius:5px;}}")
        self._render(0.0)

    def _side_btn(self, text: str, col: str) -> QtWidgets.QPushButton:
        b = QtWidgets.QPushButton(text)
        b.setCheckable(True); b.setChecked(True); b.setCursor(QtCore.Qt.PointingHandCursor)
        b.setFixedHeight(19)
        b.setStyleSheet(
            "QPushButton{color:%s; background:transparent; border:1px solid %s; border-radius:3px;"
            "font-family:Consolas; font-size:10px; font-weight:bold; padding:1px 4px;}"
            "QPushButton:checked{background:%s; color:#0d0f14;}"
            "QPushButton:!checked{color:#575e6b; border-color:#333844;}" % (col, col, col))
        return b

    def sides(self) -> "tuple[bool, bool]":
        """(show_bull, show_bear) — which sides of this layer's zones to display."""
        return (self.bull_btn.isChecked(), self.bear_btn.isChecked())

    def set_sides(self, bull: bool, bear: bool) -> None:
        """Restore the persisted Bull/Bear filter WITHOUT emitting side_changed."""
        for btn, on in ((self.bull_btn, bull), (self.bear_btn, bear)):
            btn.blockSignals(True); btn.setChecked(bool(on)); btn.blockSignals(False)

    def set_value(self, v: float) -> None:
        """Set the slider WITHOUT emitting (used to apply the adaptive default each selection)."""
        iv = int(round(max(0.0, min(1.0, v)) * 100))
        if iv != self.slider.value():
            self._silent = True
            self.slider.setValue(iv)
            self._silent = False
        self._render(self.value_s())

    def value_s(self) -> float:
        return self.slider.value() / 100.0

    def _on_change(self, _v: int) -> None:
        v = self.value_s()
        self._render(v)
        if not self._silent:
            self.changed.emit(v)

    def _render(self, v: float) -> None:
        tag, col = self._above if v >= self._floor else self._below
        self.lbl.setText(f"{self._axis}&nbsp;<b>{v:.2f}</b>&nbsp; "
                         f"<span style='color:{col}'>● {tag}</span>")


class AbsorptionZoneSlider(_ZoneThresholdSlider):
    """Rides suppression s; yellow dot = validated-strength floor (s≈0.60)."""

    def __init__(self, parent, floor: float = 0.60):
        super().__init__(parent, floor, "Zone s", "validated-strength", "weaker · sub-floor",
                         "#2ecc71", "#f1c40f")


class EffAggZoneSlider(_ZoneThresholdSlider):
    """Rides force f = eff_agg/vol_norm; NEON-green-accented; dot = forceful boundary (f≈0.75 = top-quartile
    force). Above = distinctively forceful, below = ordinary directional volume."""

    def __init__(self, parent, floor: float = 0.75):
        super().__init__(parent, floor, "Force f", "forceful", "ordinary vol",
                         "#00ff80", "#8a929e", border_col="#1f6b46", dot_rgb=(0, 255, 128))


class HeatmapContrastBar(QtWidgets.QWidget):
    """The Liquidity-Heatmap 'filter': lower/upper CUTOFF sliders in PERCENTILE of the loaded window's
    nonzero sizes (lower -> min color, upper -> max color), plus a Reset to the auto p20/p99. A user drag
    emits ``changed(lo_pct, hi_pct)`` (the terminal maps the percentiles to size cutoffs and calls
    setLevels -> instant re-color, no re-request) and pauses the auto-renorm; Reset emits ``reset_clicked``
    to return to auto. Shown only in heatmap mode (same show/hide-on-mode pattern as the zone sliders)."""
    changed = QtCore.Signal(float, float)
    reset_clicked = QtCore.Signal()

    _LBL_CSS = "color:#c8cdd6;background:transparent;font-family:Consolas;font-size:11px;"
    _SLD_CSS = ("QSlider{background:transparent;}"
                "QSlider::groove:horizontal{height:5px;background:#33384a;border-radius:2px;}"
                "QSlider::sub-page:horizontal{height:5px;background:#5a6680;border-radius:2px;}"
                "QSlider::handle:horizontal{width:11px;height:13px;background:#d7dbe2;"
                "border:1px solid #20232c;border-radius:3px;margin:-5px 0;}")

    def __init__(self, parent, lo_pct: float = 20.0, hi_pct: float = 99.0):
        super().__init__(parent)
        self._silent = False
        self._defaults = (lo_pct, hi_pct)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(11, 9, 11, 10); lay.setSpacing(5)
        title = QtWidgets.QLabel("LIQUIDITY CONTRAST")
        title.setStyleSheet("color:#8a929e;background:transparent;font-family:Consolas;font-size:10px;font-weight:bold;")
        lay.addWidget(title)
        # 0-1000 = percentile×10 (0.1% steps) so the UPPER cutoff can be dragged from ~p99 all the way to
        # p100 (max) with fine resolution — that high-end spread differentiates a 12k wall from an 80k wall.
        self.lo_lbl = QtWidgets.QLabel(); self.lo_lbl.setStyleSheet(self._LBL_CSS); lay.addWidget(self.lo_lbl)
        self.lo = QtWidgets.QSlider(QtCore.Qt.Horizontal); self.lo.setRange(0, 1000)
        self.lo.setStyleSheet(self._SLD_CSS); self.lo.setFixedHeight(18); lay.addWidget(self.lo)
        self.hi_lbl = QtWidgets.QLabel(); self.hi_lbl.setStyleSheet(self._LBL_CSS); lay.addWidget(self.hi_lbl)
        self.hi = QtWidgets.QSlider(QtCore.Qt.Horizontal); self.hi.setRange(0, 1000)
        self.hi.setStyleSheet(self._SLD_CSS); self.hi.setFixedHeight(18); lay.addWidget(self.hi)
        self.lo.valueChanged.connect(self._on_change); self.hi.valueChanged.connect(self._on_change)
        self.reset_btn = QtWidgets.QPushButton(f"Reset → auto (p{lo_pct:.1f} / p{hi_pct:.1f})")
        self.reset_btn.setStyleSheet(
            "QPushButton{color:#c8cdd6;background:#2a2e39;border:1px solid #3a3f4b;border-radius:3px;"
            "font-family:Consolas;font-size:10px;padding:4px;} QPushButton:hover{background:#3a3f4b;}")
        self.reset_btn.clicked.connect(lambda: self.reset_clicked.emit())
        lay.addSpacing(2); lay.addWidget(self.reset_btn)
        self.setFixedWidth(252)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.setStyleSheet("HeatmapContrastBar{background:rgba(17,19,26,238);"
                           "border:1px solid #2a2e39;border-radius:6px;}")
        self.set_values(lo_pct, hi_pct)
        self.adjustSize()

    def set_values(self, lo_pct: float, hi_pct: float) -> None:
        """Apply percentiles WITHOUT emitting (used for the auto p20/p99 default + Reset). Set hi FIRST so the
        lo<hi clamp in _on_change never sees a stale hi and drags lo down. (slider units = percentile×10.)"""
        self._silent = True
        self.hi.setValue(int(round(hi_pct * 10))); self.lo.setValue(int(round(lo_pct * 10)))
        self._silent = False
        self._render()

    def _on_change(self, _v: int) -> None:
        if self.lo.value() >= self.hi.value():            # keep lower strictly below upper
            self._silent = True
            if self.sender() is self.lo:
                self.lo.setValue(max(0, self.hi.value() - 1))
            else:
                self.hi.setValue(min(1000, self.lo.value() + 1))
            self._silent = False
        self._render()
        if not self._silent:
            self.changed.emit(self.lo.value() / 10.0, self.hi.value() / 10.0)

    def _render(self) -> None:
        self.lo_lbl.setText(f"Lower cutoff  ·  p{self.lo.value()/10:.1f}")
        self.hi_lbl.setText(f"Upper cutoff  ·  p{self.hi.value()/10:.1f}")
