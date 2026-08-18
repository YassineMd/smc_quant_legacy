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
from app.chart_widgets import BucketCandleItem, FootprintCandleItem, LocalTimeAxis, PriceAxis
from app.time_candles import build_time_candles

_UP = QtGui.QColor(38, 166, 91); _DN = QtGui.QColor(224, 73, 72)


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
        self.plot = pg.PlotWidget(axisItems={"bottom": LocalTimeAxis(orientation="bottom"),
                                              "right": PriceAxis(orientation="right")})
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.showAxis("right"); self.plot.hideAxis("left")
        self.plot.getAxis("bottom").set_scanner_active(False)          # chronological (real clock) labels
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
        x = [c["start_time"] for c in candles]
        o = [c["open_price"] for c in candles]; h = [c["high"] for c in candles]
        lo = [c["low"] for c in candles]; cl = [c["close_price"] for c in candles]
        width = secs * 0.72                                            # candle width in SECONDS (x is unix seconds)
        brushes = [QtGui.QBrush(_UP if cl[i] >= o[i] else _DN) for i in range(len(x))]
        pens = [QtGui.QPen(QtGui.QColor(90, 90, 90)) for _ in x]
        levels = [c["levels"] for c in candles]
        if self.mode == "candle":
            self.candle.update_data(x, o, h, lo, cl, brushes, pens, width)
            self.foot.update_data([], [], [], [])
        else:
            self.foot.update_data(x, levels, h, lo, mult=1.5, width=width)
            self.candle.update_data([], [], [], [], [], [], [], width)
        pad = width
        self.plot.setXRange(x[0] - pad, x[-1] + pad, padding=0)
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
