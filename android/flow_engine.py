# -*- coding: utf-8 -*-
"""FLOW ENGINE for the tablet: an OFFSCREEN Flow-mode terminal whose panes are read out over a socket, so the tablet
app draws exactly the terminal's numbers without a line of the terminal's math being rewritten.

WHY (user 2026-09-22: a tablet app "based on the Buy/Sell Flow $ scanner mode ... fast and reliable ... like on the
terminal"): the alternative -- mirroring the offscreen window as images -- measured 46 ms per grab and 30-43 ms per
JPEG at the tablet's 2560 x 1600, i.e. ~10 fps with half the screen changing every tick: a remote desktop, not an
app. So the PC keeps the math (this process) and the tablet keeps the pixels (android/flowapp), the same split as the
DOM app and android/bridge.py.

WHAT IT DOES: boots MinimalTerminalWindow offscreen on a TEMP copy of the UI state (the user's own settings -- lookback,
flow window, layer toggles -- and nothing written back), enters Flow mode with every pane on, opens the SSH tunnel only
if it is not already up, and serves ONE tablet at a time on 127.0.0.1:8766 (reach it from the tablet through
`adb reverse tcp:8766 tcp:8766`). The tablet drives the engine's VIEW; the engine's panes follow it exactly as the
terminal's would, and whatever they hold is serialized when it changes.

WIRE (newline-delimited JSON; arrays are base64 of little-endian float32 unless said):
  -> hello   {sym, tick, dec, win, lb, now, cfg{...}}                       once per connection
  -> bins    {base, n, full, buy, sell, px, pxh, pxl}                       the store's 1 s bins; base = bin index of
             the first sent (second = base * 1); full = replace the whole store, else replace [base, base+n)
  -> cyc     {i0, total, ts, te (f64), side, strong, done (u8), move, cbuy, csell, o, h, l, c, col, st, pickb (i8),
             rate, lead (i8: +1 buyers / -1 sellers / 0 unrated), cf (u8: 1 = a CONFLICT bar),
             cfh, cfl (the conflict box's high / low, NaN off a conflict bar)}
                                                                            rows i0.. replace the tail from i0
  -> live    {now, px, fcol}                                                every tick
  -> iimp    {mode, n, x0, x1 (f64), v, mult, ..., liib, liis, pliib, pliis, vac, quiet, up, contra, good, form}
  -> interp  {rows: [[t0, t1, head, name, d1, [d2a, d2b], st, strong, forming, col, mv_txt, mv_sign, mv_word] ...]}
  -> liq     {x (f64), b, a, live_t, live_b, live_a, radius}               the LIMIT ORDERS curves as drawn
  -> explain {k, html}                                                      the I x I click panel for cycle k
  -> hlh     {on, note, pics: [{k, x0, x1, ops?}], labels, dashes}         the HLH Volume Profile geometry (a pic's
             ops are sent once per pic identity; the tablet keeps them by k) -- see RecPainter
  -> bp      {on, sw, bub: [[x, price, usd, side, px]], dia: [[x, lo, hi, usd, buy, px]], lmax}  Big Player marks
  -> mpb     {bias, mid, hi, lo, vp, brk_t, brk_c, brk_vp, brk_lv}           THE MARKET POSITION BIAS: bias +1 / -1 = the last
             closed candle that broke the conflict VP closed above its high / below its low (0: none); mid = the
             CURRENT conflict VP's yellow midline, (hi + lo) / 2. The tablet greys its BUY / SELL from it and its own
             live price (PriceTools.biasMask). See tick_mpb, conflict_vp.Frozen.bias
  -> cvp     {on, vps: [[t0, t1, lo, hi, poc, vah, val, vah2, val2, live, cur, up, dn, c2t0, c2lo, ut], ...]}   the CONFLICT VPs,
             newest first, FROZEN: each drawn from its conflict 2's end (t0) to where the next newer VP begins (t1: its
             conflict 1's end, or further over conflicts it absorbed), low / high of the two boxes, the HLH VP's lines;
             cur = THE Conflict VP (the newest conflict with a VP of its own; it runs to the newest frozen
             conflict), the others the PREVIOUS ones -- the chain of conflict-2 links; live is always 0 (a forming
             conflict makes no VP); up / dn = its HIGH / its LOW was taken from its LAST conflict (conflict 1): the
             tablet's green up / red down arrow, drawn BELOW THE LOW OF CONFLICT 2 -- whose box starts at c2t0 (it ends
             at t0) and whose frozen low is c2lo (NaN once that record is dropped). ut = 1: a FINISHED VP whose expected
             area -- below its yellow midline for a green arrow, above it for a red one -- price has not traded into
             since it ended, no time limit (the UNTESTED AREAS sub-toggle keeps only these and the current VP;
             conflict_vp.Frozen.untested). 0: tested, the current VP, or not known.
             exp: [[x0 of its VP, side, t0, t1, low, high, low_cut, high_cut], ...] -- EXPECTED TEST: each VP's LINES
             IMPACT areas of its arrow's colour (lime for green, purple for red) that start inside it, only their part
             in its expected half (below the yellow line for green, above for red; *_cut = that edge is the half's);
             the tablet draws them on to the VP's t1 (conflict_vp.expected_areas).
             See tick_cvp, app/conflict_vp.py
  <- hi      {}                                                             first line from the tablet
  <- view    {x0, x1, follow}                                               the tablet's x range (epoch seconds)
  <- mode    {v}                                                            the I x I dropdown
  <- tog     {k, v}                                                         k: lines | hlh | bigplayer | takeover
  <- bpmin   {usd}                                                          the Big Player MIN PRINT slider
  <- explain {k}                                                            k = the cycle's start (x0)
  <- mark    {t0 | null}                                                    the marked card / candle changed: kept in the
             snapshot FILE the Claude connector reads (android/auction_mcp.py)
Exit codes: 2 = no daemon."""
import os, sys, time, json, shutil, tempfile, socket, threading, queue, base64, argparse, traceback, math, zlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform.startswith("win"):
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
ap = argparse.ArgumentParser()
ap.add_argument("--listen", default="127.0.0.1")
ap.add_argument("--port", type=int, default=8766)
ap.add_argument("--tick-ms", type=int, default=100, help="how often the panes are read out")
ap.add_argument("--auth", default=None, help="require this token in the client's first line ({t:auth, k, z})")
ap.add_argument("--compress", action="store_true", help="offer a zlib downstream (the client opts in with z=1)")
ap.add_argument("--debug", action="store_true", help="enable the shot / series / refetch / bfstate commands")
ap.add_argument("--lean", action="store_true", help="RAM caps for a small VM (bucket scrollback, chart cache)")
ap.add_argument("--no-tunnel", action="store_true", help="never start an SSH tunnel: wait for the daemon port instead")
ARGS = ap.parse_args()

from app import config                                        # noqa: E402
if ARGS.lean:
    # the tablet reads the FLOW panes only: the bucket scrollback and the candle-chart cache the offscreen window
    # would fill anyway are the bulk of the terminal's RAM (1.85 GB private on the PC, measured 2026-09-22)
    config.CLOSED_BUCKETS_CAP = 400
    config.CHART_CACHE_CAP = 400
TMP = tempfile.mkdtemp(prefix="flowengine_")
_ui = os.path.join(REPO, "data", "terminal_ui.json")
if os.path.exists(_ui):
    shutil.copy(_ui, os.path.join(TMP, "terminal_ui.json"))
config.DATA_DIR = TMP
import numpy as np                                            # noqa: E402
from PySide6 import QtWidgets, QtCore                         # noqa: E402
qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
from app import terminal as _term                             # noqa: E402
from app.terminal import MinimalTerminalWindow                # noqa: E402
from app import flow_interp as FI                             # noqa: E402
from app import hlh_draw as _hlh                              # noqa: E402
from app import conflict_vp as _cvp                           # noqa: E402
from PySide6 import QtGui as _QtGui                           # noqa: E402


# ------------------------------------------------------------------ the HLH recorder
class RecPainter:
    """Stands in for QPainter on a QPicture inside hlh_draw.build_period and RECORDS the calls as plain geometry
    (a QPicture cannot be read back, and the tablet draws its own pixels). Any other device gets a real QPainter,
    so the label images still render. Ops: ["P", pen_argb, pen_w, brush_argb, [x, y, ...]] polygon,
    ["R", pen_argb, pen_w, brush_argb, x, y, w, h] rect, ["L", pen_argb, pen_w, x0, y0, x1, y1] line; pen widths
    are the terminal's cosmetic pixel widths, argb 0 = none."""
    RenderHint = _QtGui.QPainter.RenderHint
    _warned = set()

    def __new__(cls, dev=None):
        if isinstance(dev, _QtGui.QPicture):
            return object.__new__(cls)
        return _QtGui.QPainter(dev) if dev is not None else _QtGui.QPainter()

    def __init__(self, dev):
        self.ops = []; dev._rec_ops = self.ops
        self.pen = (0, 0.0); self.brush = 0

    @staticmethod
    def _argb(c):
        return int(c.rgba()) if c is not None else 0

    def setRenderHint(self, *a, **k):
        pass

    def setPen(self, p):
        if isinstance(p, _QtGui.QPen):
            self.pen = (0, 0.0) if p.style() == QtCore.Qt.PenStyle.NoPen else (self._argb(p.color()), float(p.widthF()))
        elif isinstance(p, _QtGui.QColor):
            self.pen = (self._argb(p), 1.0)
        else:
            self.pen = (0, 0.0)

    def setBrush(self, b):
        if isinstance(b, _QtGui.QColor):
            self.brush = self._argb(b)
        elif isinstance(b, _QtGui.QBrush):
            self.brush = 0 if b.style() == QtCore.Qt.BrushStyle.NoBrush else self._argb(b.color())
        else:
            self.brush = 0

    def drawPolygon(self, poly):
        pts = []
        for q in poly:
            pts.append(round(q.x(), 3)); pts.append(round(q.y(), 5))
        self.ops.append(["P", self.pen[0], self.pen[1], self.brush, pts])

    def fillRect(self, r, col):
        self.ops.append(["R", 0, 0.0, self._argb(col), round(r.x(), 3), round(r.y(), 5), round(r.width(), 3), round(r.height(), 5)])

    def drawRect(self, r):
        self.ops.append(["R", self.pen[0], self.pen[1], self.brush, round(r.x(), 3), round(r.y(), 5), round(r.width(), 3), round(r.height(), 5)])

    def drawLine(self, a, b):
        self.ops.append(["L", self.pen[0], self.pen[1], round(a.x(), 3), round(a.y(), 5), round(b.x(), 3), round(b.y(), 5)])

    def end(self):
        pass

    def __getattr__(self, name):
        def _noop(*a, **k):
            if name not in RecPainter._warned:
                RecPainter._warned.add(name); log("RecPainter: %s ignored" % name)
        return _noop


class _GuiProxy:
    QPainter = RecPainter

    def __getattr__(self, n):
        return getattr(_QtGui, n)


_hlh.QtGui = _GuiProxy()                                       # only hlh_draw's QPainter is the recorder

T0 = time.time()


def log(msg):
    print("[%7.1fs] %s" % (time.time() - T0, msg), flush=True)


def b64(a, dt="<f4"):
    return base64.b64encode(np.ascontiguousarray(np.asarray(a).astype(dt)).tobytes()).decode("ascii")


# ------------------------------------------------------------------ the socket side (plain threads, no Qt)
class Client:
    def __init__(self, sock, addr, comp=None, initial=b""):
        self.sock = sock; self.addr = addr
        self.comp = comp                  # a zlib stream once the client opted in (see serve())
        self._initial = initial           # what arrived in the same packet as the auth line (the tablet's "hi")
        self.out = queue.Queue(maxsize=4000)   # ~3 min of ticks: a slow 4G downlink must not drop the client
        self.alive = True
        self.ready = False                                    # said "hi"
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._writer, daemon=True).start()

    def _reader(self):
        # ⚠ PARSE WHAT WE ALREADY HOLD BEFORE BLOCKING ON recv. The auth handshake consumes one packet and hands
        # the remainder here as _initial; if the client's next lines ("hi", the first "view") rode that SAME
        # segment, the old loop blocked in recv() with a complete "hi" sitting unparsed and the engine answered
        # NOTHING, forever. It only ever looked fine because a client that writes its lines separately (the
        # tablet does -- auth from run(), the rest from the writer thread) usually gets them into separate
        # segments; a coalescing link or a client that batches its handshake hit a silent dead socket.
        buf = self._initial
        try:
            while self.alive:
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if line:
                        try:
                            CMDS.put(json.loads(line.decode("utf-8")))
                        except Exception:
                            pass
                chunk = self.sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
        except Exception:
            pass
        self.close()

    def _writer(self):
        try:
            while self.alive:
                m = self.out.get()
                if m is None:
                    break
                if self.comp is not None:
                    m = self.comp.compress(m) + self.comp.flush(zlib.Z_SYNC_FLUSH)
                self.sock.sendall(m)
        except Exception:
            pass
        self.close()

    def send(self, obj):
        if not self.alive:
            return
        try:
            self.out.put_nowait((json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8"))
        except queue.Full:
            log("client %s: outbound queue full -- dropping it" % (self.addr,)); self.close()

    def close(self):
        if self.alive:
            self.alive = False
            try:
                self.out.put_nowait(None)
            except Exception:
                pass
            try:
                self.sock.close()
            except Exception:
                pass


CMDS = queue.Queue()
CLIENT = [None]


def serve():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((ARGS.listen, ARGS.port)); srv.listen(2)
    log("listening on %s:%d" % (ARGS.listen, ARGS.port))
    while True:
        s, a = srv.accept()
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        comp = None; rest = b""
        if ARGS.auth:
            # the FIRST line must be {"t":"auth","k":<token>,"z":1} within 6 s, else the socket goes (the DOM
            # bridge's handshake); z=1 opts into ONE zlib stream for everything the engine sends after it
            try:
                s.settimeout(6.0); buf = b""
                while b"\n" not in buf:
                    d = s.recv(4096)
                    if not d or len(buf) > 4096:
                        raise OSError("auth eof")
                    buf += d
                line, rest = buf.split(b"\n", 1)
                m = json.loads(line)
                if m.get("t") != "auth" or str(m.get("k", "")) != ARGS.auth:
                    raise OSError("bad auth")
                if ARGS.compress and m.get("z"):
                    comp = zlib.compressobj(6)
                s.settimeout(None)
            except Exception as ex:
                log("refused %s: %s" % (a[0], ex))
                try:
                    s.close()
                except Exception:
                    pass
                continue
        old = CLIENT[0]
        if old is not None:
            old.close()
        cl = Client(s, a, comp, rest)
        CLIENT[0] = cl
        log("tablet connected from %s%s" % (a, ", zlib" if comp else ""))


# ------------------------------------------------------------------ the window
tunnel = _term.SSHTunnelManager()
if not _term._ipc_port_open():
    if ARGS.no_tunnel:
        log("waiting for the daemon port %s:%s (a tunnel service owns it)" % (config.IPC_HOST, config.IPC_PORT))
    else:
        log("no tunnel on %s:%s -- opening one" % (config.IPC_HOST, config.IPC_PORT))
        tunnel.ensure()
    _tw = time.time()
    while not _term._ipc_port_open() and time.time() - _tw < (600.0 if ARGS.no_tunnel else 60.0):
        time.sleep(1.0)
# ⚠ THIS PROCESS MUST BE ONE DAEMON CLIENT, NOT THREE. A terminal window opens shared HELPER feeds (the
# "4h zones" and "1m detail" workers, app/terminal.py _shared_helper): each is its own TCP client that
# calls request_timeframe, so the daemon then SERIALIZES a full TickPacket per timeframe per connection --
# and a Tick carries the whole forming bucket plus its footprint ladder (measured 1m 2.7 KB, 1h 11.2 KB,
# 4h 21.9 KB). py-spy on the live daemon: JSON encoding is its single largest CPU consumer (iterencode
# 8.7% + _to_line 4.1%), and this process was 3 of its 5 clients, subscribed to 1m/1h/4h -- timeframes the
# tablet never displays. Lite workers connect but never subscribe (app/pipe_client.py:343-344).
_term._HELPERS_DEFER["on"] = True
w = MinimalTerminalWindow("5m"); w._rr_persist_save = lambda tf: None
w.resize(1600, 1000)
# ⚠⚠ WA_DontShowOnScreen: the widgets stay VISIBLE to the code (isVisible() is True, geometry and
# viewPixelSize are real, so every pane still builds its data) but Qt never delivers a paint event.
# The tablet draws its own pixels from the messages, so every paint here was pure waste on the weakest
# CPU in the chain -- py-spy caught the GUI thread inside PlotCurveItem.paint / GraphicsView.paintEvent
# / TextItem.setHtml, ~15% of all samples, on a box whose readout was stalling ~900 ms at the p90.
w.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen, True)
w.show()
# ⚠⚠ WA_DontShowOnScreen ALONE DOES NOT STOP THE PAINTING under the offscreen QPA -- measured with a
# standalone probe in exactly this setup: 184 PlotCurveItem.paint calls in 184 frames, 1.80 s of process
# CPU over a 3 s loop. setUpdatesEnabled(False) takes that to ZERO paints and 0.19 s while the widgets
# stay isVisible() with real geometry, which is all the data-building code checks.
w.setUpdatesEnabled(False)


def spin(sec):
    e = time.time() + sec
    while time.time() < e:
        qapp.processEvents(); time.sleep(0.004)


spin(8.0)
_tw = time.time()
while not w.worker.connected and time.time() - _tw < 45.0:
    spin(1.0)
if not w.worker.connected:
    log("NO DAEMON"); sys.exit(2)
w._set_scanner("flow"); spin(4.0)
for _nm in ("px_on", "iimp_on", "liq_on", "interp_on", "lines_on"):
    _cb = getattr(w.menu, _nm, None)
    if _cb is not None and not _cb.isChecked():
        _cb.setChecked(True)
if not w.menu.flow_cross_on.isChecked():
    w.menu.flow_cross_on.setChecked(True)
spin(2.0)
# ---- work the offscreen terminal does that the tablet never sees. Each of these was checked against every
# attribute the tick_* readouts consume; they write only Qt item state or caches nothing here reads.
#   _flow_cross_draw : the cycle vlines + badges. The tablet draws its own from the `cyc` message, and this is
#                      an UNCAPPED crosses() scan at the window's 20 Hz with its own memo key, so it never even
#                      hits the memo the panes fill.
#   _hlh_px_tick     : a SECOND HlhOverlay.build for canvas "px". The engine builds canvas "tab" itself
#                      (tick_hlh) from the same overlay and bars; _geom/_out are keyed per canvas, so this ran
#                      every build_period twice and kept two full sets of pictures in RAM.
#   _scanner_tracker / _redock_trackers : right-axis badges (py-spy caught the GUI thread in TextItem.setHtml
#                      under the first one).
#   _px_bp_draw      : the Big Player bubbles ON the offscreen pane. The `bp` message is built by tick_bp from
#                      _bp_events, which the TAPE fills, not this.
#   _liq_levels      : the LIMIT ORDERS badges. `liq` ships _liq_curves and _liq_live, written by _liq_tick.
for _noop in ("_flow_cross_draw", "_hlh_px_tick", "_scanner_tracker", "_redock_trackers", "_px_bp_draw", "_liq_levels"):
    if hasattr(w, _noop):
        setattr(w, _noop, (lambda *a, **k: None))
# ⚠ LINES INTEREST / LINES IMPACT default OFF in the terminal (they are new panes, 8853c1c). The engine
# has to turn them ON or _lines_tick never runs and the tablet's two panes stay empty for ever -- the tablet
# decides what it DRAWS, but only what the engine computed can be drawn.
for _nm in ("cint_on", "cimp_on"):
    _cb = getattr(w.menu, _nm, None)
    if _cb is not None and not _cb.isChecked():
        _cb.setChecked(True)
spin(1.0)
log("flow mode up | lookback N = %d | flow window %d s | lines %d/%d"
    % (w._lb_n(), int(w._flow_win), w._lines_smooth_n("cint"), w._lines_smooth_n("cimp")))
threading.Thread(target=serve, daemon=True).start()


def keepalive():
    """A ping every 10 s from a thread of its own: the GUI thread can stall for tens of seconds while a 6 h
    backfill chunk lands on a shared-core VM, and the tablet's read timeout must not take that for a dead link."""
    while True:
        time.sleep(10.0)
        cl = CLIENT[0]
        if cl is not None and cl.alive and cl.ready and cl.out.empty():
            cl.send({"t": "ping", "now": time.time()})


threading.Thread(target=keepalive, daemon=True).start()

CFG = {
    "iimp_modes": list(config.IIMP_MODES), "iimp_low": float(config.IIMP_LOW), "iimp_high": float(config.IIMP_HIGH),
    "iimp_wall_low": float(config.IIMP_WALL_LOW), "iimp_wall_high": float(config.IIMP_WALL_HIGH),
    "iimp_keep_low": float(config.IIMP_KEEP_LOW), "iimp_clip": float(config.IIMP_CLIP),
    "iimp_buy_col": config.IIMP_BUY_COL, "iimp_sell_col": config.IIMP_SELL_COL, "iimp_contra_col": config.IIMP_CONTRA_COL,
    "bar_col": list(FI.BAR_COL), "txt_dark": list(FI.TXT_DARK), "move_dark": list(FI.MOVE_DARK),
    "px_iib_buy": config.PX_IIB_BUY_COL, "px_iib_sell": config.PX_IIB_SELL_COL,
    "cross_weak_col": config.FLOW_CROSS_WEAK_COL, "candle_fill": float(config.PX_CANDLE_FILL),
    "badge_unit": float(config.FLOW_CROSS_BADGE_UNIT_USD), "badge_unit_txt": config.FLOW_CROSS_BADGE_UNIT_TXT,
    "flat_ticks": float(config.SPEED_FLAT_TICKS), "interp_span": float(config.INTERP_SPAN_SECS),
    "stale_secs": float(config.INTERP_STALE_SECS), "cross_min_px": int(config.FLOW_CROSS_MIN_PX),
}


class State:
    bins_sent = None          # (base, buy, sell, px, pxh, pxl) as last sent
    cyc_key = None; cyc_t = 0.0; cyc_full_needed = True; cyc_last_total = 0
    cyc_base = None; cyc_cols_sent = None      # the last read's arrays, and the colours the tablet last got
    rev_hist = -1
    iimp_id = None; interp_id = None; liq_sig = None
    cyc_lead_sent = None      # the leaders the tablet last got (a lookback change re-rates every cycle)
    cyc_conf_sent = None      # ... and the conflict flags (both tapes >= CONFLICT_TAPE_MIN)
    cyc_cfh_sent = None; cyc_cfl_sent = None   # ... and the conflict boxes' reach (NaN off a conflict bar)
    dom_areas = None          # LINES IMPACT's bright areas over the whole read: (side, t0, t1, low, high)
    lines_sig = {"cint": None, "cimp": None}   # the two split panes, each keyed on what it last sent
    hlh_out = None; hlh_t = 0.0; hlh_xm = None; hlh_pics = {}
    mpb_msg = None; mpb_sent = None            # the MARKET POSITION BIAS built with the cycles, and the one the tablet has
    cvp_sent = None; cvp_pair = None           # the CONFLICT VP payload the tablet last got, and the chain last logged
    cvp_save_t = 0.0                           # the last save of the store's untested-areas memory
    dom_ok = False            # dom_areas was built while the wall grid covered the whole read it needs (cvp_ready)
    bw = True                 # the tablet's Chart Style: the HLH labels are built for a white or a dark ground
    bp_sig = None
    view = None; follow = True
    last_live = 0.0


S = State()

# THE FROZEN CONFLICT VPs (user 2026-09-26: "when a conflict VP is draw it stays fix it shouldnt change"): every conflict
# is decided ONCE when it completes -- its box, its conflict 2, its VP's lines -- and kept in data/ across restarts.
CVP = _cvp.Frozen(os.path.join(REPO, "data", str(config.CVP_STATE_FILE)), float(config.CVP_KEEP_SECS))
log("conflict VP: %d frozen conflicts loaded from %s" % (len(CVP.items), CVP.path))


def grid_caught_up(now):
    """The wall grid covers everything the I x I needs for the read (terminal._wall_kmin): LINES IMPACT's areas --
    what a conflict box reaches for -- are then the real ones, not the gaps of a grid still loading."""
    try:
        lo = w.__dict__.get("_wall_lo")
        sp = w._flow.span()
        return lo is not None and sp is not None and int(lo) <= int(w._wall_kmin(now, float(sp[0])))
    except Exception:
        return False


def cvp_ready(now, t):
    """Freeze only on real boxes: the areas were built on a caught-up grid, and the read reaches back to the newest
    frozen conflict -- or, for the very first fill, the whole history has been loaded."""
    if not (S.dom_ok and grid_caught_up(now)):
        return False
    nt = CVP.newest_t0()
    if nt is None:
        return not w.__dict__.get("_flow_bf_queue") and w.__dict__.get("_flow_bf_inflight") is None
    return bool(np.size(t)) and float(t[0]) <= nt


def send(obj):
    c = CLIENT[0]
    if c is not None and c.alive and c.ready:
        c.send(obj)


def hello():
    send({"t": "hello", "sym": config.SYMBOL, "tick": float(config.TICK_SIZE), "dec": int(config.PRICE_DECIMALS),
          "win": float(w._flow_win), "lb": int(w._lb_n()), "now": time.time(), "cfg": CFG,
          "smooth": {"iimp": int(w._iimp_smooth_n()), "cint": int(w._lines_smooth_n("cint")),
                     "cimp": int(w._lines_smooth_n("cimp"))},
          "smooth_min": int(config.LINES_SMOOTH_MIN), "smooth_max": int(config.LINES_SMOOTH_MAX),
          # the PC's clock offset: the tablet labels its clock axis in the terminal's local time, whatever its own zone
          "tz": int(-time.timezone if not time.localtime().tm_isdst else -time.altzone)})


def tick_bins(force=False):
    st = w._flow
    if st.empty():
        return
    base = int(st._base); n = int(len(st._buy))
    cur = (base, np.asarray(st._buy), np.asarray(st._sell), np.asarray(st._px), np.asarray(st._pxh), np.asarray(st._pxl))
    prev = S.bins_sent
    full = prev is None or force
    if not full:
        # ⚠ the store GROWS by a bin every second and a backfill chunk PREPENDS hours: neither is a reason to resend
        # 72 h (the first cut did exactly that, 95 MB a minute). Align the two snapshots by ABSOLUTE bin index: the
        # bins outside the previous span are new, the common span is compared, and one range covers all of it.
        pbase, pn = int(prev[0]), int(prev[1].size)
        # ⚠⚠ A LEFT-EDGE PRUNE IS NOT A REASON TO RESEND ANYTHING. The store is capped at FLOW_RETAIN_SECS, so
        # once it is full it drops its oldest bin EVERY SECOND and `base` advances every second. Treating that
        # as "bins were dropped -> start over" resent all 259,200 bins x 5 arrays about twice a second:
        # MEASURED against the live VM before this fix, 366 MB of `bins` in 60 s (5,958 KB/s decompressed,
        # 2,155 KB/s on the wire = $686/month of egress), with a ~4 MB message sitting in front of every price
        # update. The client keeps the pruned bins it already holds -- they are history it can still draw --
        # and the common span below is compared by ABSOLUTE bin index, which already handles a moved base.
        # Only a store that shrank on the RIGHT (a reset) invalidates what the client holds.
        if base + n < pbase + pn:
            full = True
    if full:
        lo, hi = 0, n
    else:
        ranges = []
        if base < pbase:
            ranges.append((base, pbase))                      # prepended (a backfill chunk landed)
        if base + n > pbase + pn:
            ranges.append((pbase + pn, base + n))             # the live edge grew
        c0, c1 = max(base, pbase), min(base + n, pbase + pn)
        if c1 > c0:
            a0, p0, L = c0 - base, c0 - pbase, c1 - c0
            ch = np.zeros(L, dtype=bool)
            for k in range(1, 6):
                ch |= cur[k][a0:a0 + L] != prev[k][p0:p0 + L]
            idx = np.flatnonzero(ch)
            if idx.size:
                ranges.append((c0 + int(idx[0]), c0 + int(idx[-1]) + 1))
        if not ranges:
            return
    # ⚠ one message PER range, never their union: a backfill chunk landing at the far left in the same tick as the
    # live edge moving at the far right made the union the whole store (17 MB a minute, measured)
    for r0, r1 in ([(0, n)] if full else ranges):
        lo, hi = (r0, r1) if full else (r0 - base, r1 - base)
        send({"t": "bins", "base": base + lo, "n": hi - lo, "full": full, "buy": b64(cur[1][lo:hi]), "sell": b64(cur[2][lo:hi]),
              "px": b64(cur[3][lo:hi]), "pxh": b64(cur[4][lo:hi]), "pxl": b64(cur[5][lo:hi])})
    if full or prev is None or int(prev[0]) != base or prev[1].size != n:
        S.bins_sent = (base, cur[1].copy(), cur[2].copy(), cur[3].copy(), cur[4].copy(), cur[5].copy())
    else:
        for r0, r1 in ranges:                                # same base and size: patch the sent copies in place
            lo, hi = r0 - base, r1 - base
            for k in range(1, 6):
                prev[k][lo:hi] = cur[k][lo:hi]


def bright_areas(t, te_c, done, cbuy, csell, px0, px1, pxh, pxl, lead):
    """LINES IMPACT's BRIGHT areas (lime / purple, after the kept filter) over the WHOLE read, as the tablet boxes them
    on its PRICE pane -- the pane's own arithmetic (_iimp_rate -> _lines_values -> _lines_dom_bands), the Kept Ticks
    sides from the same leaders the tablet gets (`lead`). For the conflict boxes' reach."""
    n_lb = int(w._lb_n()); n_mn = int(w._lb_min_n())
    R = w._iimp_rate(t, te_c, done, cbuy, csell, px0, px1, pxh, pxl, n_lb, n_mn)
    _rt = np.isfinite(R["imb"]) & np.isfinite(R["score"])                  # what the I x I could rate, for the log
    S.rated_n = int(_rt.sum()); S.rated_t0 = float(t[np.flatnonzero(_rt)[0]]) if _rt.any() else float("nan")
    dn = np.asarray(done, dtype=bool)
    sm_b, sm_s, rated = w._lines_values("cimp", dn, ~dn, R["ar_b"], R["ar_s"], R["score"], R["score_pb"], R["short"])
    keep = rated & np.isfinite(sm_b) & np.isfinite(sm_s)
    dside, _gap, dgain = w._lines_dom_bands(sm_b, sm_s, keep)
    ksd = w._kept_sides(dn, lead, px0, px1, float(config.TICK_SIZE))
    return w._bright_areas(t, te_c, pxh, pxl, np.flatnonzero(keep), dside, dgain, ksd, float(config.LIMP_DOM_GAIN))


def tick_cycles(now, force=False):
    st = w._flow
    sp_ = st.span()
    if sp_ is None:
        return
    if not force and now - S.cyc_t < 1.0:
        return
    S.cyc_t = now
    A, B = float(sp_[0]), float(sp_[1])
    XARGS = (float(w._flow_win), float(config.FLOW_CROSS_MIN_SPREAD_PCT), float(config.FLOW_CROSS_MIN_HOLD_SECS),
             10 ** 6, float(config.FLOW_CROSS_CONTEXT_SECS), float(config.TICK_SIZE))
    t, is_buy, strong, move, cbuy, csell, t_end, done = st.crosses(A, B, *XARGS)
    if t.size == 0:
        return
    key = (int(t.size), round(float(t[-1]), 2), round(float(t_end[-1]), 2), round(float(np.nansum(cbuy) + np.nansum(csell)), 1))
    fresh = force or key != S.cyc_key or S.cyc_base is None
    if fresh:
        px0, px1 = st.crosses_px(A, B, *XARGS); pxh, pxl = st.crosses_hl(A, B, *XARGS)
        # THE CANDLE COLOURS carry the breakout gate (2026-09-25), which rates EVERY cycle of the 72 h read on the
        # I x I pane's numbers: MEASURED ~70 ms over 2,310 cycles. The tablet colours the FORMING candle from "live"
        # (fcol), so the closed candles only change when a cycle closes, when a late wall column lands (final within
        # ~45 s) or when the tape behind them moves (force) -- recomputed then, and at most every 10 s otherwise.
        _nfin = int(np.count_nonzero(np.asarray(done, dtype=bool)))
        _ck = (int(t.size), _nfin, round(float(t[-1]), 2), int(w._lb_n()))
        if (force or getattr(S, "cols0", None) is None or getattr(S, "cols_key", None) != _ck
                or now - getattr(S, "cols_t", 0.0) >= 10.0 or int(S.cols0.size) != int(t.size)):
            _tc = time.perf_counter()
            cols0 = w._px_state_cols(t, t_end, done, move, is_buy, strong, cbuy, csell, px0, px1, pxh, pxl)
            S.cols_ms = 1000.0 * (time.perf_counter() - _tc)
            S.cols0 = cols0; S.cols_key = _ck; S.cols_t = now
            S.cols_n = getattr(S, "cols_n", 0) + 1
            _recolour = True
        else:
            cols0 = S.cols0
            _recolour = False
        if now - getattr(S, "cols_log_t", 0.0) > 60.0:     # the breakout gate rates every cycle of the read: watch it
            S.cols_log_t = now
            log("candle colours: %d cycles in %.0f ms, rated %d times in the last minute (breakouts %d, absorbed %d, vacuum %d, "
                "normal against the leader %d)" % (
                int(t.size), getattr(S, "cols_ms", 0.0), getattr(S, "cols_n", 0),
                int(np.isin(cols0, (1, 2)).sum()), int(np.isin(cols0, (0, 6)).sum()), int(np.isin(cols0, (9, 10, 11, 12)).sum()),
                int(np.isin(cols0, (13, 14)).sum())))
            S.cols_n = 0
        pick_b, rate, state = w._cycle_impact(is_buy, strong, move, cbuy, csell)
        # THE LEADER of every cycle (user 2026-09-25, the tablet's KEPT TICKS BY LEADER pane): the I x I pane's own
        # rule (_iimp_rate's lead_buy) -- the side whose aggressive $/s runs further above the median of the previous
        # N cycles' -- over the WHOLE read (72 h), so the pane's cumulative reaches back to 00:00 UTC whatever the
        # view. The forming cycle is rated on what it has so far, its t_end clamped to now as the I x I clamps it.
        te_c = np.array(t_end, dtype=np.float64, copy=True)
        if te_c.size and not bool(done[-1]):
            te_c[-1] = max(float(t[-1]), min(float(now), float(te_c[-1])))
        dur = np.maximum(te_c - t, 1e-9)
        n_lb = int(w._lb_n()); n_mn = int(w._lb_min_n())
        ar_b = FI.prev_ratio(np.maximum(cbuy, 0.0) / dur, done, n_lb, n_mn, include_open=True)
        ar_s = FI.prev_ratio(np.maximum(csell, 0.0) / dur, done, n_lb, n_mn, include_open=True)
        ok = np.isfinite(ar_b) & np.isfinite(ar_s) & (ar_b > 0) & (ar_s > 0)
        lead = np.where(ok, np.where(ar_b >= ar_s, 1, -1), 0).astype(np.int8)
        # A CONFLICT BAR (user 2026-09-25: "conflict bars are where the tape of both side >=3x"): the same two tapes, BOTH
        # at or above CONFLICT_TAPE_MIN -- the tablet boxes that candle in red
        _cm = float(config.CONFLICT_TAPE_MIN)
        conf = (ok & (ar_b >= _cm) & (ar_s >= _cm)).astype(np.uint8)
        # ... and its REACH (user 2026-09-25): the box runs from the closest previous LIME area's low to the closest
        # previous PURPLE area's high (terminal._conflict_boxes). Those areas are LINES IMPACT's, over the WHOLE read so
        # the 24 h look-back is there whatever the tablet's view -- rebuilt with the candle colours (the I x I rating is
        # the costly part), while the boxes follow every tick (the forming bar's own high / low move them).
        if _recolour or getattr(S, "dom_areas", None) is None:
            _ta = time.perf_counter()
            _gok = grid_caught_up(now)
            try:
                S.dom_areas = bright_areas(t, te_c, done, cbuy, csell, px0, px1, pxh, pxl, lead)
                S.dom_ok = _gok
            except Exception as _e:
                log("conflict areas: %s" % _e)
                S.dom_areas = []
                S.dom_ok = False
            S.dom_ms = 1000.0 * (time.perf_counter() - _ta)
        _why = []
        cfh, cfl = w._conflict_boxes(t, pxh, pxl, conf, S.dom_areas, float(config.CONFLICT_LOOKBACK_SECS), _why)
        # THE CONFLICT VP (user 2026-09-26), FROZEN ("when a conflict VP is draw it stays fix it shouldnt change"):
        # each conflict that has COMPLETED since the newest frozen one is decided now -- its box, its conflict 2, its
        # VP's lines from the store's 1 s bins (t_end: the cycles' own seconds) -- and never again
        try:
            _hm2 = lambda x: time.strftime("%d %H:%M:%S", time.gmtime(float(x)))
            if cvp_ready(now, t) and CVP.rule < _cvp.RULE:
                # the PAIR RULE and then the PRIORITY RULE (user 2026-09-26: each conflict gives one end of the range; a
                # conflict passing over the current VP's conflict 1 takes over only beyond that VP's range) came after
                # these records were frozen: their boxes stay, their pairings are decided again, once, from what each
                # one recorded
                _nch, _nob = CVP.redecide(int(st._base), float(st.bin), st._buy, st._sell, st._pxh, st._pxl,
                                          float(config.TICK_SIZE), int(config.HLH_ROWS), float(config.HLH_VA_PCT),
                                          float(config.HLH_VA2_PCT))
                CVP.save()
                log("conflict VP: re-decided %d of %d frozen conflicts with the rule (pair + priority; boxes "
                    "unchanged%s)" % (_nch, len(CVP.items),
                                      "; %d without a VP: the bins no longer hold them" % _nob if _nob else ""))
                for _it in CVP.items:
                    if _it.get("inside"):
                        log("conflict VP: %s %.2f-%.2f INSIDE %s: no VP, part of the previous VP" % (
                            _hm2(_it["t0"]), _it["lo"], _it["hi"], _hm2(_it["inside"])))
                    if _it.get("within"):
                        log("conflict VP: %s %.2f-%.2f WITHIN the VP of %s: no VP, part of it (priority)" % (
                            _hm2(_it["t0"]), _it["lo"], _it["hi"], _hm2(_it["within"])))
            if cvp_ready(now, t):
                _add = CVP.freeze_new(t, t_end, done, conf, cfh, cfl, pxh, pxl, now, float(config.CVP_FREEZE_SETTLE_SECS),
                                      int(st._base), float(st.bin), st._buy, st._sell, st._pxh, st._pxl,
                                      float(config.TICK_SIZE), int(config.CVP_MIN_GAP_BARS), int(config.HLH_ROWS),
                                      float(config.HLH_VA_PCT), float(config.HLH_VA2_PCT))
                _pr = CVP.prune(now)
                if _add or _pr:
                    CVP.save()
                for _it in _add[-8:]:
                    _v = _it.get("vp")
                    log("conflict frozen: %s-%s %.2f-%.2f%s -> %s" % (
                        _hm2(_it["t0"]), _hm2(_it["tb"])[3:], _it["lo"], _it["hi"],
                        "".join(" | skipped %s (%s)" % (_hm2(k_)[3:], w_) for k_, w_ in _it["skip"][:4]),
                        ("conflict 2 %s: VP %.2f-%.2f POC %.2f VA %.2f-%.2f outer %.2f-%.2f $%.2fM" % (
                            _hm2(_it["c2"]), _v["lo"], _v["hi"], _v["poc"], _v["val"], _v["vah"], _v["val2"], _v["vah2"],
                            _v["usd"] / 1e6)) if _v else (
                            "INSIDE %s: no VP, part of the previous VP" % _hm2(_it["inside"]) if _it.get("inside")
                            else "WITHIN the VP of %s: no VP, part of it (priority)" % _hm2(_it["within"])
                            if _it.get("within") else "no conflict 2")))
                if len(_add) > 8:
                    log("conflict frozen: ... %d in all (the first fill of the history)" % len(_add))
        except Exception as _e:
            log("conflict VP: %s" % _e)
        if now - getattr(S, "cf_log_t", 0.0) > 60.0:          # what the boxes reach to, last 6 h (the engine's log)
            S.cf_log_t = now
            _hm = lambda x: time.strftime("%d %H:%M:%S", time.gmtime(float(x)))
            _ar = lambda a, j: ("%s-%s %.2f" % (_hm(a[1]), _hm(a[2])[3:], a[j])) if a is not None else "none"
            _k0 = {round(float(t[k]), 2): int(k) for k in np.flatnonzero(conf)}
            _g = list(getattr(w, "_wall_grid", {}) or {})
            _C = float(config.IIMP_WALL_COL_SECS)
            log("I x I rated %d of %d cycles, from %s; wall grid %d columns, %s -> %s" % (
                getattr(S, "rated_n", -1), int(t.size),
                time.strftime("%d %H:%M", time.gmtime(S.rated_t0)) if np.isfinite(getattr(S, "rated_t0", float("nan"))) else "-",
                len(_g), time.strftime("%d %H:%M", time.gmtime(min(_g) * _C)) if _g else "-",
                time.strftime("%d %H:%M", time.gmtime((max(_g) + 1) * _C)) if _g else "-"))
            log("conflict boxes: %d areas (%.0f ms);%s" % (len(S.dom_areas), getattr(S, "dom_ms", 0.0), "".join(
                " | %s %.2f-%.2f lime[%s] purple[%s]" % (_hm(t0_)[3:], float(cfl[_k0[round(t0_, 2)]]), float(cfh[_k0[round(t0_, 2)]]),
                                                        _ar(al_, 3), _ar(ap_, 4))
                for (t0_, al_, ap_) in _why if t0_ >= now - 6 * 3600.0 and round(t0_, 2) in _k0)))
        # the boxes the tablet draws: the FROZEN records over the frozen history (a box once drawn stays), the live
        # flags / reach after the newest frozen conflict
        conf, cfh, cfl = CVP.apply(t, conf, cfh, cfl)
        try:
            S.mpb_msg = mpb_msg(t, t_end, done, px1)
        except Exception:
            traceback.print_exc()
        S.cyc_base = (t, is_buy, strong, move, cbuy, csell, t_end, done, px0, px1, pxh, pxl, cols0, pick_b, rate, state,
                      lead, conf, cfh, cfl)
        S.cyc_key = key
    (t, is_buy, strong, move, cbuy, csell, t_end, done, px0, px1, pxh, pxl, cols0, pick_b, rate, state, lead,
     conf, cfh, cfl) = S.cyc_base
    # THE BRIGHT PAIR (7 / 8) needs its I x I bar ORANGE AND FILLED (user 2026-09-23), and that pane re-rates on its
    # own clock -- so the join is re-checked every tick, and any candle whose colour moved is re-sent, not just the
    # last few (a bar can fill well after its cycle closed, once the wall columns land)
    cols = w._px_bright_demote(t, cols0)
    prev = S.cyc_cols_sent
    if not fresh and prev is not None and prev.size == cols.size and np.array_equal(prev, cols):
        return
    total = int(t.size)
    full = force or S.cyc_full_needed or total < S.cyc_last_total
    i0 = 0 if full else max(0, total - 8)
    if not full and prev is not None and prev.size:
        _n = min(int(prev.size), int(cols.size))
        _d = np.flatnonzero(prev[:_n] != cols[:_n])
        if _d.size:
            i0 = min(i0, int(_d[0]))
    for pl_, cur_ in ((S.cyc_lead_sent, lead), (S.cyc_conf_sent, conf),       # a leader, a conflict flag or a box
                      (S.cyc_cfh_sent, cfh), (S.cyc_cfl_sent, cfl)):           # reach that moved
        if not full and pl_ is not None and pl_.size:
            _n = min(int(pl_.size), int(cur_.size))
            _a, _b = pl_[:_n], cur_[:_n]
            _neq = (~((_a == _b) | (np.isnan(_a) & np.isnan(_b)))) if _a.dtype.kind == "f" else (_a != _b)
            _d = np.flatnonzero(_neq)
            if _d.size:
                i0 = min(i0, int(_d[0]))
    S.cyc_full_needed = False; S.cyc_last_total = total
    S.cyc_cols_sent = np.array(cols, copy=True)
    S.cyc_lead_sent = np.array(lead, copy=True)
    S.cyc_conf_sent = np.array(conf, copy=True)
    S.cyc_cfh_sent = np.array(cfh, dtype=np.float64, copy=True); S.cyc_cfl_sent = np.array(cfl, dtype=np.float64, copy=True)
    sl = slice(i0, total)
    send({"t": "cyc", "i0": i0, "total": total, "ts": b64(t[sl], "<f8"), "te": b64(t_end[sl], "<f8"),
          "side": b64(is_buy[sl], "u1"), "strong": b64(strong[sl], "u1"), "done": b64(done[sl], "u1"),
          "move": b64(np.nan_to_num(move[sl], nan=0.0)), "cbuy": b64(cbuy[sl]), "csell": b64(csell[sl]),
          "o": b64(px0[sl]), "h": b64(pxh[sl]), "l": b64(pxl[sl]), "c": b64(px1[sl]),
          "col": b64(cols[sl], "i1"), "st": b64(state[sl], "i1"), "pickb": b64(pick_b[sl], "u1"),
          "rate": b64(np.nan_to_num(rate[sl], nan=0.0)), "lead": b64(lead[sl], "i1"), "cf": b64(conf[sl], "u1"),
          "cfh": b64(cfh[sl]), "cfl": b64(cfl[sl])})


def tick_iimp():
    L = w.__dict__.get("_iimp_last")
    if L is None or id(L) == S.iimp_id:
        return
    S.iimp_id = id(L)
    n = int(np.size(L["x0"]))
    m = {"t": "iimp", "mode": str(L.get("mode", "None")), "n": n}
    for k in ("x0", "x1"):
        m[k] = b64(L[k], "<f8")
    m["smn"] = int(L.get("smn", 1))
    for k in ("v", "mult", "score", "wall", "reach", "mv", "arb", "ars", "kept", "sbuy", "ssell", "liib", "liis", "pliib", "pliis", "lyb", "lys",
              "pback"):                         # the OTHER side's push-back multiple (2026-09-24)
        m[k] = b64(np.nan_to_num(np.asarray(L.get(k, np.full(n, np.nan)), dtype=np.float64), nan=-999.0))
    for k in ("up", "contra", "good", "form"):
        m[k] = b64(np.asarray(L[k], dtype=bool), "u1")
    for k in ("vac", "quiet"):
        m[k] = b64(np.asarray(L.get(k, np.zeros(n)), dtype=np.int8), "i1")
    send(m)


def tick_lines(kind):
    """LINES INTEREST / LINES IMPACT: the two smoothed series, plus the band arrays for impact.

    Sent whole rather than diffed -- one point per cycle over the view is a few hundred floats, and the
    pane's own signature already stops it being rebuilt when nothing moved."""
    st = w._lp_(kind)
    L = st.get("last")
    if L is None:
        return
    sig = st.get("sig")
    if sig == S.lines_sig.get(kind):
        return
    S.lines_sig[kind] = sig
    n = int(np.size(L["x0"]))
    m = {"t": kind, "n": n, "smooth": int(w._lines_smooth_n(kind))}
    for k in ("x0", "x1"):
        m[k] = b64(np.asarray(L[k], dtype=np.float64), "<f8")
    for k in ("b", "s"):
        m[k] = b64(np.nan_to_num(np.asarray(L[k], dtype=np.float64), nan=-999.0))
    m["form"] = b64(np.asarray(L["form"], dtype=bool), "u1")
    if kind == "cimp" and L.get("dside") is not None:
        m["dside"] = b64(np.asarray(L["dside"], dtype=np.int8), "i1")
        m["dgain"] = b64(np.nan_to_num(np.asarray(L["dgain"], dtype=np.float64), nan=-999.0))
        m["dgap"] = b64(np.nan_to_num(np.asarray(L["dgap"], dtype=np.float64), nan=-999.0))
        m["spread"] = float(config.LIMP_DOM_SPREAD); m["gain"] = float(config.LIMP_DOM_GAIN)
        m["step"] = float(config.LIMP_STEP)     # the step marks' threshold: the terminal and the tablet read ONE constant
    send(m)


def tick_interp():
    pnl = getattr(w, "interp_panel", None)
    if pnl is None:
        return
    rows = getattr(pnl, "_rows", None)
    if rows is None or id(rows) == S.interp_id:
        return
    S.interp_id = id(rows)
    out = []
    for r in rows:
        try:
            t0, t1, head, name, d1, d2, st, strong, forming, col, mv_txt, mv_sign, mv_word = r[:13]
            # the card redesign (2026-09-23) draws the NUMBERS behind a row -- quadrant, tape bars, book arrows,
            # give-back bar. ⚠ NaN is not JSON: Android's parser rejects it and would drop the WHOLE message.
            _raw = r[13] if len(r) > 13 and isinstance(r[13], dict) else {}
            _raw = {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in _raw.items()}
            out.append([float(t0), float(t1), str(head), str(name), str(d1), [str(d2[0]), str(d2[1])] if d2 else ["", ""],
                        int(st), bool(strong), bool(forming), int(col), str(mv_txt), int(mv_sign), str(mv_word), _raw])
        except Exception:
            continue
    # the feed rebuilds its list every tick even when no word changed: send only what reads differently
    payload = json.dumps(out, separators=(",", ":"))
    if payload == getattr(S, "interp_payload", None):
        return
    S.interp_payload = payload
    send({"t": "interp", "rows": out})


def tick_liq():
    cv = w.__dict__.get("_liq_curves"); sig = w.__dict__.get("_liq_sig")
    if cv is None or sig is None or sig == S.liq_sig:
        return
    S.liq_sig = sig
    x, b = cv[0].getData(); _x2, a = cv[1].getData()
    if x is None or b is None or a is None:
        return
    live = w.__dict__.get("_liq_live")
    send({"t": "liq", "x": b64(x, "<f8"), "b": b64(b), "a": b64(a), "radius": int(w._liq_radius),
          "live_t": float(live[0]) if live else 0.0})


def tick_live(now):
    lp = w._engine_live_px()
    # the engine's price can be STALE for the first seconds of a boot (a catch-up bucket): more than 1% off the
    # tape's last print of the last two minutes, it yields to the tape -- the terminal's own rule for market entries
    try:
        tail = np.asarray(w._flow._px[-120:], dtype=np.float64)
        ok = np.nonzero(np.isfinite(tail) & (tail > 0))[0]
        tape = float(tail[ok[-1]]) if ok.size else None
    except Exception:
        tape = None
    if tape is not None and (lp is None or abs(float(lp) - tape) / tape > 0.01):
        lp = tape
    d = w.__dict__.get("_px_data")
    fcol = int(d[9]) if (d is not None and len(d) > 9) else -1
    if fcol in (7, 8) and np.size(d[2]):             # the forming candle: the same filled-orange join
        fcol = int(w._px_bright_demote(np.array([float(d[2][-1])]), np.array([fcol]))[0])
    send({"t": "live", "now": now, "px": float(lp) if lp is not None else None, "fcol": fcol})


def tick_hlh(now):
    """The HLH Volume Profile as the terminal's overlay builds it for the PRICE pane (canvas "tab", identity x map,
    the pane's cycles as the bars for the POC runs), serialized when the overlay returns a new tuple."""
    try:
        on = bool(w._hlh_on())
    except Exception:
        on = False
    if not on:
        if S.hlh_out is not False:
            S.hlh_out = False; S.hlh_pics = {}
            send({"t": "hlh", "on": False})
        return
    if now - S.hlh_t < 1.0:
        return
    S.hlh_t = now
    st = w._hlh_state()
    _on, week_on, bloc, _dark, bdg, tab, pcr = w._hlh_toggles()
    st.ensure_feeds(week_on, now)
    w._hlh_floor_once()
    if S.hlh_xm is None:
        S.hlh_xm = _hlh.IdentityXMap()
    pxa = w.__dict__.get("_px_arr")
    bars = None if (pxa is None or np.size(pxa[0]) == 0) else (pxa[0], pxa[1], pxa[2], pxa[3], pxa[4], pxa[5])
    # ⚠ NO POC acceptance areas on the TABLET (user 2026-09-23: "remove the below/above POC area from the
    # tablette"). The shaded runs of >= HLH_POC_RUN_MIN cycles that closed on one side of a bloc's POC. Forced off
    # HERE, in the one place that builds the tablet's HLH, rather than by unticking the engine window's menu:
    # the terminal keeps its own "POC acceptance areas" option exactly as the user has it, and nothing about the
    # blocs, the POC line, the merges or the colours changes -- the option is display-only by design.
    out = st.build("tab", S.hlh_xm, bloc, not S.bw, week_on, now, badges=bdg, tables=tab, poc_runs=False, bars=bars)
    if out is S.hlh_out:
        return
    S.hlh_out = out
    pics = []; keep = {}
    for xlo, xhi, pic in out[0]:
        k = str(id(pic))
        rec = {"k": k, "x0": float(xlo), "x1": float(xhi)}
        if S.hlh_pics.get(k) is not pic:                      # a pic the tablet has not seen: its geometry goes along
            rec["ops"] = getattr(pic, "_rec_ops", None) or []
        keep[k] = pic
        pics.append(rec)
    S.hlh_pics = keep
    labels = [[round(lb.x, 3), round(lb.y, 5), lb.text, lb.anchor, int(lb.bg.rgba()) if lb.bg is not None else 0,
               int(lb.fg.rgba()), lb.font] for lb in out[1]]
    dashes = [[round(d.xa, 3), round(d.xb, 3), round(d.y, 5), int(d.col.rgba())] for d in out[3]]
    send({"t": "hlh", "on": True, "note": out[2], "pics": pics, "labels": labels, "dashes": dashes})


def mpb_msg(t, t_end, done, close):
    """THE MARKET POSITION BIAS (user 2026-09-26: "remove completely the logic of the market position buttons we
    currently have (buttons turning to gray) and replace it by this one: first we have to detect the last break of the
    conflict VP ... a candle that closes above/below a most recent high/low of the conflict VP (the thickest lines of
    the conflict VP) / if above we have a bullish bias / if below we have a bearish bias"). The break: CVP.bias --
    every closed cycle candle against the conflict VP that was the most recent one when it closed. The midline: the
    CURRENT conflict VP's (the chain's newest), (high + low) / 2, the yellow line the tablet draws. The buttons
    themselves (the price against the midline) are the tablet's, on its own live price: PriceTools.biasMask."""
    bias, k, rec = CVP.bias(t, t_end, done, close, float(config.CVP_FREEZE_SETTLE_SECS), float(config.TICK_SIZE))
    chn = CVP.chain()
    d = int(config.PRICE_DECIMALS) + 3
    msg = {"t": "mpb", "bias": int(bias), "mid": None, "hi": None, "lo": None, "vp": None}
    if chn:
        v = chn[0]["vp"]
        msg.update(hi=round(float(v["hi"]), d), lo=round(float(v["lo"]), d),
                   mid=round(0.5 * (float(v["hi"]) + float(v["lo"])), d), vp=round(float(chn[0]["t0"]), 3))
    if k >= 0 and rec is not None:
        msg.update(brk_t=round(float(t[k]), 3), brk_c=round(float(close[k]), d), brk_vp=round(float(rec["t0"]), 3),
                   brk_lv=round(float(rec["vp"]["hi"] if bias > 0 else rec["vp"]["lo"]), d))
    return msg


def tick_mpb():
    """Send the MARKET POSITION BIAS when it changed (a new break, a new current conflict VP), and say so."""
    msg = S.mpb_msg
    if msg is None or msg == S.mpb_sent:
        return
    S.mpb_sent = msg
    send(msg)
    try:
        hm = lambda x: time.strftime("%d %H:%M:%S", time.gmtime(float(x)))
        b = int(msg["bias"])
        brk = ("the %s candle closed %.2f %s the %s VP's %s %.2f" % (
            hm(msg["brk_t"]), msg["brk_c"], "above" if b > 0 else "below", hm(msg["brk_vp"]),
            "high" if b > 0 else "low", msg["brk_lv"])) if b else "no break in the read"
        cur = ("current VP %s %.2f-%.2f, midline %.3f" % (hm(msg["vp"]), msg["lo"], msg["hi"], msg["mid"])
               if msg.get("mid") is not None else "no conflict VP")
        log("market position bias: %s -- %s; %s -> %s" % (
            "BULLISH" if b > 0 else "BEARISH" if b < 0 else "none", brk, cur,
            "SELL gray, BUY green below the midline" if b > 0 else
            "BUY gray, SELL red above the midline" if b < 0 else "both gray"))
    except Exception:
        traceback.print_exc()


def tick_cvp():
    """THE CONFLICT VPs (user 2026-09-26: "This indicator creates VP from the last 2 conflicts/merged conflicts ... we
    gonna draw the same lines as HLH VP indicator", the PREVIOUS ones on a toggle, "when a conflict VP is draw it stays
    fix it shouldnt change"): the frozen chain, sent when it moved -- which only a newly frozen conflict can do. The
    tablet draws the lines (ChartView.drawCvp) and owns both toggles and the colours; the engine always sends."""
    drawn = CVP.drawn()
    chn = [q for q, _x0, _x1 in drawn]
    d = int(config.PRICE_DECIMALS) + 3
    rows = []
    tol = 0.5 * float(config.TICK_SIZE)
    by = {float(x["t0"]): x for x in CVP.items}
    # THE UNTESTED AREAS (user 2026-09-26, a sub-toggle): each FINISHED VP's expected area, until price trades into it
    ut = {}
    if S.cyc_base is not None:
        try:
            _now = time.time()
            ut, _grew = CVP.untested(drawn, S.cyc_base[0], S.cyc_base[10], S.cyc_base[11], _now, float(config.TICK_SIZE))
            if _grew or _now - S.cvp_save_t > 600.0:    # a newly tested area at once; how far each was watched, now and then
                CVP.save()
                S.cvp_save_t = _now
        except Exception:
            traceback.print_exc()
    for k_, (q, x0_, x1_) in enumerate(drawn):
        v = q["vp"]
        c2 = by.get(float(q["c2"]))           # the arrow sits under ITS box (user 2026-09-26: "below the low of C2")
        # THE ARROWS (user 2026-09-26: "down red if the low was taken from the last conflict and up green if the high
        # was taken from the last conflict"): the VP's high / low is conflict 1's own -- the pair rule gives every VP
        # one end from each conflict (never a shared high / low, never one box inside the other), so exactly one arrow
        up = 1 if abs(float(v["hi"]) - float(q["hi"])) < tol else 0
        dn = 1 if abs(float(v["lo"]) - float(q["lo"])) < tol else 0
        rows.append([round(x0_, 3), round(x1_, 3), round(float(v["lo"]), d), round(float(v["hi"]), d),
                     round(float(v["poc"]), d), round(float(v["vah"]), d), round(float(v["val"]), d),
                     round(float(v["vah2"]), d), round(float(v["val2"]), d), 0, 1 if k_ == 0 else 0, up, dn,
                     round(float(c2["t0"]), 3) if c2 else None, round(float(c2["lo"]), d) if c2 else None,
                     1 if k_ in ut else 0])
    # EXPECTED TEST (user 2026-09-26, a sub-toggle): each VP's lime (green arrow) / purple (red arrow) LINES IMPACT
    # areas, the tablet extends them to the VP's end
    exp = []
    try:
        for k_, sd_, a0_, a1_, lo_, hi_, lc_, hc_ in _cvp.expected_areas(drawn, by, S.dom_areas or [], tol):
            exp.append([round(float(drawn[k_][1]), 3), int(sd_), round(a0_, 3), round(a1_, 3), round(lo_, d), round(hi_, d),
                        1 if lc_ else 0, 1 if hc_ else 0])
    except Exception:
        traceback.print_exc()
    msg = {"t": "cvp", "on": bool(rows), "vps": rows, "exp": exp}
    if msg != S.cvp_sent:
        S.cvp_sent = msg
        send(msg)
    ukey = tuple(sorted((round(float(drawn[k][0]["t0"]), 3), a[0]) for k, a in ut.items()))
    if ukey != getattr(S, "cvp_ut_key", None):          # an area appeared (a VP ended) or was tested: say which
        S.cvp_ut_key = ukey
        try:
            hm = lambda x: time.strftime("%d %H:%M:%S", time.gmtime(float(x)))
            log("conflict VP untested areas: %d of %d previous VPs (%d tested so far) | %s" % (
                len(ut), max(0, len(drawn) - 1), len(CVP.tested), " | ".join(
                    "%s VP %s %s %.3f-%.3f since %s" % ("green" if a[0] > 0 else "red", hm(drawn[k][0]["t0"]),
                                                     "below" if a[0] > 0 else "above", a[1], a[2], hm(a[3]))
                    for k, a in sorted(ut.items())[:12])))
        except Exception:
            traceback.print_exc()
    key = tuple((q["t0"], q["c2"]) for q in chn)
    if key == S.cvp_pair:
        return
    S.cvp_pair = key
    try:
        hm = lambda x: time.strftime("%d %H:%M:%S", time.gmtime(float(x)))
        by = {float(x["t0"]): x for x in CVP.items}
        run = lambda x: "%s-%s %.2f-%.2f" % (hm(x["t0"]), hm(x["tb"])[3:], x["lo"], x["hi"])
        lines = lambda v: "%.2f-%.2f POC %.2f VA %.2f-%.2f outer %.2f-%.2f $%.2fM" % (
            v["lo"], v["hi"], v["poc"], v["val"], v["vah"], v["val2"], v["vah2"], v["usd"] / 1e6)
        parts = []
        for q in chn[:5]:
            c2 = by.get(float(q["c2"]))
            parts.append("%s%s .. %s %s" % ("CURRENT " if q is chn[0] else "", run(c2) if c2 else "?", run(q), lines(q["vp"])))
        log("conflict VP chain: %d VPs over %d frozen conflicts | %s" % (len(chn), len(CVP.items), " | ".join(parts)))
    except Exception:
        traceback.print_exc()


def tick_bp(now):
    """Big Player marks for the tablet's view (+ half a span each side, on a minute grid so a pan rarely rebuilds):
    the SAME _bp_events + merge rules as the terminal's PRICE pane, marks snapped to the middle of their cycle
    candle. Sent when the signature moves."""
    try:
        on = bool(w.menu.layer_state("m10_bigplayer"))
    except Exception:
        on = False
    if not on:
        if S.bp_sig is not False:
            S.bp_sig = False
            send({"t": "bp", "on": False})
        return
    if S.view is None:
        return
    x0, x1 = S.view; span = max(60.0, x1 - x0)
    dx0 = math.floor((x0 - 0.5 * span) / 60.0) * 60.0; dx1 = math.ceil((x1 + 0.5 * span) / 60.0) * 60.0
    thr = float(w.menu.big_player_min_usd()); sw_on = bool(w.menu.layer_state("m10_bigplayer_sweeps"))
    try:
        cs, ce = w._px_cycle_bounds(now)
    except Exception:
        cs = ce = np.zeros(0)
    sig = (dx0, dx1, thr, sw_on, getattr(w, "_bp_rev", 0), len(getattr(w, "_bp_trades", ())),
           len(getattr(w, "_bp_sweeps", ())), int(np.size(cs)), int(float(ce[-1]) // 5.0) if np.size(ce) else 0)
    if sig == S.bp_sig:
        return
    S.bp_sig = sig
    try:
        ev = w._bp_events(dx0, dx1, dx1 + 1e-6, sw_on)
    except Exception:
        ev = []

    def snap(ts):
        if np.size(cs):
            k = int(np.searchsorted(cs, float(ts), side="right")) - 1
            if k >= 0 and float(ts) <= float(ce[k]) + 1.0:
                return 0.5 * (float(cs[k]) + float(ce[k])), round(float(cs[k]), 3)
        return float(ts), round(float(ts))
    merged = {}; smerged = {}; xat = {}
    for e in ev:
        if e[3] < thr or not (dx0 <= e[0] <= dx1):
            continue
        x, kt = snap(e[0]); key = (kt, round(float(e[2]), 4), e[1]); xat[kt] = x
        if e[4] == "sw":
            m = smerged.get(key)
            smerged[key] = [e[3], e[5], e[6]] if m is None else [m[0] + e[3], min(m[1], e[5]), max(m[2], e[6])]
        else:
            merged[key] = merged.get(key, 0.0) + e[3]
    levels = sorted(merged.items())[-int(config.BIGPLAYER_MAX_LINES):]
    slevels = sorted(smerged.items())[-int(config.BIGPLAYER_SWEEP_MAX):]
    bub = [[float(xat.get(t_, t_)), float(price), float(usd), 1 if side > 0 else -1, float(w._bp_bubble_px(usd, thr))]
           for (t_, price, side), usd in levels]
    dia = ([[float(xat.get(t_, t_)), float(lo), float(hi), float(usd), bool(buy > 0), float(w._bp_bubble_px(usd, thr))]
            for (t_, price, buy), (usd, lo, hi) in slevels] if sw_on else [])
    send({"t": "bp", "on": True, "sw": sw_on, "bub": bub, "dia": dia, "lmax": int(getattr(config, "BIGPLAYER_LABEL_MAX", 60))})


def on_cmd(c):
    k = c.get("t")
    cl = CLIENT[0]
    if k == "hi":
        if cl is not None:
            cl.ready = True
        S.bins_sent = None; S.cyc_full_needed = True; S.iimp_id = None; S.interp_id = None; S.liq_sig = None
        S.cyc_lead_sent = None; S.cyc_conf_sent = None; S.cyc_cfh_sent = None; S.cyc_cfl_sent = None
        S.lines_sig = {"cint": None, "cimp": None}
        S.hlh_out = None; S.hlh_pics = {}; S.bp_sig = None; S.mpb_sent = None; S.cvp_sent = None
        hello()
        log("hi from the tablet -- sending everything")
    elif k == "view":
        x0 = float(c.get("x0", 0)); x1 = float(c.get("x1", 0)); fol = bool(c.get("follow", True))
        if x1 > x0 + 10.0:
            w._flow_follow = fol
            w.vb.setXRange(x0, x1, padding=0.0)
            w._flow_last_set = (x0, x1)
            S.view = (x0, x1); S.follow = fol
    elif k == "mode":
        v = str(c.get("v", "None")); modes = tuple(config.IIMP_MODES)
        if v in modes and w._iimp_combo is not None:
            w._iimp_combo.setCurrentIndex(modes.index(v))
    elif k == "smooth":
        # the three sliders: "iimp" is the I x I pane's Lines Buyer/Seller window, "cint" / "cimp" the two
        # split panes' own. Driven through the widgets so the terminal's own clamp and redraw run.
        key = str(c.get("k", "")); n = int(c.get("n", config.LINES_SMOOTH_N))
        n = max(int(config.LINES_SMOOTH_MIN), min(int(config.LINES_SMOOTH_MAX), n))
        if key == "iimp":
            if w._iimp_slider is not None:
                w._iimp_slider.setValue(n)
            else:
                w._iimp_smn = n; w._iimp_sig = None
        elif key in ("cint", "cimp"):
            _sl = w._lp_(key).get("slider")
            if _sl is not None:
                _sl.setValue(n)
            else:
                w._lp_(key)["smn"] = n; w._lp_(key)["sig"] = None
            S.lines_sig[key] = None
    elif k == "bpmin":                                    # the tablet's Big Player MIN PRINT slider (user 2026-09-25)
        try:
            w.menu.set_big_player_min_usd(float(c.get("usd")))  # tick_bp's signature carries the threshold: marks follow
        except Exception as ex:
            log("bpmin: %s" % ex)
    elif k == "tog":
        key = str(c.get("k", "")); v = bool(c.get("v", True))
        if key == "lines":
            w.menu.flow_cross_on.setChecked(v)
        elif key == "bw":
            S.bw = v; S.hlh_out = None
        else:
            cb = w.menu.layer_checks.get({"hlh": "m10_hlh", "bigplayer": "m10_bigplayer", "takeover": "cyc_takeover"}.get(key, ""))
            if cb is not None and cb.isChecked() != v:
                cb.setChecked(v)
    elif k == "shot" and ARGS.debug:                                          # debug: the offscreen window as the terminal draws it
        try:
            w.grab().save(str(c.get("path", "engine_shot.png")))
            send({"t": "shot", "ok": True})
        except Exception as ex:
            send({"t": "shot", "ok": False, "err": str(ex)})
    elif k == "series" and ARGS.debug:                                        # debug: the terminal's own flow series for a range
        try:
            t_, b_, s_ = w._flow.series(float(c["x0"]), float(c["x1"]), float(w._flow_win), int(c.get("max_pts", 4000)))
            send({"t": "series", "x": b64(t_, "<f8"), "buy": b64(b_), "sell": b64(s_), "bin": float(w._flow.bin)})
        except Exception as ex:
            send({"t": "series", "err": str(ex)})
    elif k == "refetch" and ARGS.debug:                                       # debug: ask the daemon for a window again (idempotence)
        w._flow_bf_queue.insert(0, (float(c["x0"]), float(c["x1"])))
        w._flow_bf_t = 0.0
        send({"t": "refetch", "ok": True})
    elif k == "bfstate" and ARGS.debug:                                       # debug: the backfill pump's queue / in-flight chunk
        _q = [[float(a), float(b)] for a, b in (w._flow_bf_queue or [])]
        _i = w.__dict__.get("_flow_bf_inflight")
        send({"t": "bfstate", "queue": _q, "inflight": [float(_i[0]), float(_i[1])] if _i else None,
              "rev_hist": int(getattr(w._flow, "rev_hist", -1)), "span": list(w._flow.span() or [])})
    elif k == "mark":
        # the user marked (or cleared) a cycle on the tablet: the Claude CONNECTOR's "the cycle I marked" (2026-09-24)
        try:
            v = c.get("t0")
            w._auction_mark_set(float(v) if v is not None else None)
        except Exception:
            traceback.print_exc()
    elif k == "explain":
        L = w.__dict__.get("_iimp_last")
        kx = float(c.get("k", 0))
        if L is not None and np.size(L["x0"]):
            i = int(np.argmin(np.abs(np.asarray(L["x0"], float) - kx)))
            if abs(float(L["x0"][i]) - kx) < 1.0:
                try:
                    send({"t": "explain", "k": kx, "html": w._iimp_explain(i)})
                except Exception as ex:
                    send({"t": "explain", "k": kx, "html": "<i>%s</i>" % ex})


def engine_tick():
    try:
        while True:
            on_cmd(CMDS.get_nowait())
    except queue.Empty:
        pass
    except Exception:
        traceback.print_exc()
    cl = CLIENT[0]
    if cl is None or not cl.alive or not cl.ready:
        return
    # ⚠ THE PRICE GOES FIRST, stamped at the moment it is sent. It used to be stamped at the top of the tick
    # and sent at the BOTTOM, after tick_hlh / tick_bp, so the tablet both received it late and was told it was
    # older than it was -- measured 2026-09-22 as 217 ms of apparent "transit", most of which was this tick's
    # own heavy work happening between the stamp and the send.
    try:
        tick_live(time.time())
    except Exception:
        traceback.print_exc()
    now = time.time()
    try:
        st = w._flow
        rh = int(getattr(st, "rev_hist", 0))
        if rh != S.rev_hist:
            S.rev_hist = rh; S.cyc_full_needed = True
        tick_bins()
        tick_cycles(now, force=S.cyc_full_needed)
        tick_cvp()
        tick_mpb()
        tick_iimp()
        w._lines_tick(now)                      # the same crosses() read, so a memo hit
        tick_lines("cint"); tick_lines("cimp")
        tick_interp(); tick_liq()
        tick_hlh(now); tick_bp(now)
    except Exception:
        traceback.print_exc()


# the Big Player store is fed whatever the tablet's toggle: the Claude connector's snapshot lists the prints of every
# cycle (user 2026-09-24). _scan_flow then drains the tape through _bp_feed, which feeds _flow too (one drainer).
w._bp_always = True
timer = QtCore.QTimer(); timer.setInterval(int(ARGS.tick_ms)); timer.timeout.connect(engine_tick); timer.start()
log("engine running -- start the tablet app (adb reverse tcp:%d tcp:%d)" % (ARGS.port, ARGS.port))
try:
    qapp.exec()
finally:
    try:
        w.close()
    except Exception:
        pass
    try:
        tunnel.stop()
    except Exception:
        pass
    shutil.rmtree(TMP, ignore_errors=True)
