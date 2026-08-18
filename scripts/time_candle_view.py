"""Standalone TIME-CANDLE viewer (spike visual proof — run this, it opens a window).

Renders honest gap-filled CLOCK candles from a raw aggTrade tape, on a TIME x-axis, reusing the terminal's OWN render
items (BucketCandleItem / FootprintCandleItem / LocalTimeAxis) — the exact components the live chart uses. This proves
the "reuse everything + time axis" thesis end-to-end WITHOUT touching the live terminal. Data honesty is verified
separately by `python -m app.time_candles` (100.0000% parity). Toolbar: pick timeframe + candle/footprint mode.

    python scripts/time_candle_view.py            # uses the captured tape in data/
    python scripts/time_candle_view.py <tape.jsonl>

NOTE: the bundled capture is ~5 min (a handful of candles) — enough to SEE the render + footprint + honest time axis.
Rich history comes later, when the daemon serves clock candles (footprints_db is already keyed by clock time)."""
import os, sys, json, glob

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6 import QtWidgets, QtCore, QtGui
import pyqtgraph as pg
from app import config
from app.chart_widgets import BucketCandleItem, FootprintCandleItem, PriceAxis, _MONO
from app.time_candles import build_time_candles
from datetime import datetime, timezone

_UP = QtGui.QColor(38, 166, 154); _DN = QtGui.QColor(239, 83, 80)


class TimeIndexAxis(pg.AxisItem):
    """Candles are drawn at x = INDEX (0..N-1) -- pyqtgraph is unstable at raw-timestamp magnitudes, so the terminal
    renders at index x too. Because candles are GAP-FILLED (one per interval), index k maps LINEARLY to clock time
    (t0 + k*tf), so this prints the true UTC clock at each tick: an honest time axis on an index coordinate."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.setTickFont(_MONO); self._t0 = 0.0; self._tf = 60

    def set_map(self, t0, tf_secs):
        self._t0 = float(t0); self._tf = int(tf_secs); self.picture = None; self.update()

    def tickStrings(self, values, scale, spacing):
        out = []
        for v in values:
            try:
                d = datetime.fromtimestamp(self._t0 + v * self._tf, tz=timezone.utc)
                out.append(d.strftime("%H:%M" if self._tf < 3600 else "%m-%d %H:%M"))
            except (OSError, ValueError, OverflowError):
                out.append("")
        return out


def load_tape(path):
    trades = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("type") == "aggTrade":
                trades.append(r["data"])
    return trades


class Viewer(QtWidgets.QMainWindow):
    def __init__(self, trades):
        super().__init__()
        self.trades = trades; self.tf = "1m"; self.mode = "footprint"
        self.setWindowTitle("Time-candle viewer (spike)")
        self.resize(1200, 720)
        bar = QtWidgets.QToolBar(); self.addToolBar(bar)
        for tf in ("1m", "5m", "15m"):
            a = bar.addAction(tf); a.triggered.connect(lambda _=False, t=tf: self._set_tf(t))
        bar.addSeparator()
        self._mode_act = bar.addAction("candle / footprint")
        self._mode_act.triggered.connect(self._toggle_mode)
        self._lbl = QtWidgets.QLabel("  "); bar.addWidget(self._lbl)
        self.plot = pg.PlotWidget(axisItems={"bottom": TimeIndexAxis(orientation="bottom"),
                                              "right": PriceAxis(orientation="right")})
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.showAxis("right"); self.plot.hideAxis("left")
        self.candle = BucketCandleItem(); self.foot = FootprintCandleItem()
        self.plot.addItem(self.candle); self.plot.addItem(self.foot)
        self.setCentralWidget(self.plot)
        self._render()

    def _set_tf(self, tf):
        self.tf = tf; self._render()

    def _toggle_mode(self):
        self.mode = "candle" if self.mode == "footprint" else "footprint"; self._render()

    def _render(self):
        secs = config.TF_SECONDS[self.tf]
        candles = build_time_candles(self.trades, secs)
        if not candles:
            self._lbl.setText("  no candles"); return
        n = len(candles)
        x = list(range(n))                                             # x = INDEX (pyqtgraph-safe; the terminal does the same)
        o = [c["open_price"] for c in candles]; h = [c["high"] for c in candles]
        lo = [c["low"] for c in candles]; cl = [c["close_price"] for c in candles]
        brushes = [pg.mkBrush(_UP if cl[i] >= o[i] else _DN) for i in range(n)]
        pens = [pg.mkPen(90, 90, 90) for _ in range(n)]
        levels = [c["levels"] for c in candles]
        self.plot.getAxis("bottom").set_map(candles[0]["start_time"], secs)   # index -> true UTC clock labels
        if self.mode == "candle":
            self.candle.update_data(x, o, h, lo, cl, brushes, pens, 0.72)
            self.foot.update_data([], [], [], [])
        else:
            self.foot.update_data(x, levels, h, lo, mult=1.5, width=0.72)
            self.candle.update_data([], [], [], [], [], [], [], 0.72)
        self.plot.setXRange(-1, n, padding=0)
        self.plot.setYRange(min(lo), max(h), padding=0.08)
        real = sum(1 for c in candles if not c.get("empty"))
        self._lbl.setText("  %s · %s · %d candles (%d real, %d gap-filled)" % (
            self.tf, self.mode, len(candles), real, len(candles) - real))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        cands = sorted(glob.glob(os.path.join(config.PROJECT_DIR, "data", "aggtrade_tape_*.jsonl")))
        if not cands:
            print("No aggTrade tape found in data/. Pass a path: python scripts/time_candle_view.py <tape.jsonl>")
            return
        path = cands[-1]
    trades = load_tape(path)
    if not trades:
        print("No aggTrades in %s" % path); return
    print("Loaded %d aggTrades from %s" % (len(trades), os.path.basename(path)))
    app = QtWidgets.QApplication(sys.argv)
    v = Viewer(trades); v.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
