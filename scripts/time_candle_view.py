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


def fetch_live_candles(tf, host=None, port=None, window=1.5):
    """Fetch gap-filled clock candles for `tf` from a RUNNING daemon (get_time_candles over the IPC socket / SSH
    tunnel). Reads for `window`s, collecting TIME_CANDLES frames (ignores other broadcasts). [] on any error."""
    import socket, time
    from app import protocol as _p
    host = host or config.IPC_HOST; port = port or config.IPC_PORT
    try:
        s = socket.create_connection((host, port), timeout=3.0)
    except OSError as e:
        print("  live: cannot connect %s:%d (%s) -- is the daemon / SSH tunnel up?" % (host, port, e)); return []
    try:
        s.sendall((_p.json.dumps({"action": "get_time_candles", "tf": tf}) + "\n").encode())
        s.settimeout(window); buf = b""; out = []; deadline = time.monotonic() + window
        while time.monotonic() < deadline:
            try:
                chunk = s.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                pkt = _p.parse_line(line.decode("utf-8", "ignore"))
                if isinstance(pkt, _p.TimeCandlesPacket) and pkt.tf == tf:
                    out.extend(pkt.candles)
        return out
    finally:
        try:
            s.close()
        except OSError:
            pass


class Viewer(QtWidgets.QMainWindow):
    def __init__(self, trades=None, live=False):
        super().__init__()
        self.trades = trades or []; self.live = live; self.tf = "1m"; self.mode = "footprint"
        self.setWindowTitle("Time-candle viewer (spike)" + (" - LIVE" if live else ""))
        self.resize(1200, 720)
        bar = QtWidgets.QToolBar(); self.addToolBar(bar)
        for tf in config.TIMEFRAMES:                                   # 1m/5m/15m/30m/1h/4h — every daemon-served tf
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
        if live:                                                       # keep a live chart fresh (re-fetch every 2s)
            self._timer = QtCore.QTimer(self); self._timer.timeout.connect(self._render); self._timer.start(2000)
        self._render()

    def _set_tf(self, tf):
        self.tf = tf; self._render()

    def _toggle_mode(self):
        self.mode = "candle" if self.mode == "footprint" else "footprint"; self._render()

    def _render(self):
        secs = config.TF_SECONDS[self.tf]
        candles = fetch_live_candles(self.tf) if self.live else build_time_candles(self.trades, secs)
        if not candles:
            self._lbl.setText("  no candles" + ("  (live: no daemon data / tunnel?)" if self.live else "")); return
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
    args = sys.argv[1:]
    if "--live" in args:                                              # fetch from the running daemon (needs the SSH tunnel)
        print("LIVE: fetching clock candles from daemon %s:%d (SSH tunnel must be up)" % (config.IPC_HOST, config.IPC_PORT))
        app = QtWidgets.QApplication(sys.argv)
        v = Viewer(live=True); v.show()
        sys.exit(app.exec())
    path = next((a for a in args if not a.startswith("-")), None)
    if not path:
        cands = sorted(glob.glob(os.path.join(config.PROJECT_DIR, "data", "aggtrade_tape_*.jsonl")))
        if not cands:
            print("No aggTrade tape found in data/. Pass a path, or --live for the daemon.")
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
