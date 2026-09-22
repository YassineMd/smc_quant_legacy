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
             rate}                                                          rows i0.. replace the tail from i0
  -> live    {now, px, fcol}                                                every tick
  -> iimp    {mode, n, x0, x1 (f64), v, mult, ..., liib, liis, pliib, pliis, vac, quiet, up, contra, good, form}
  -> interp  {rows: [[t0, t1, head, name, d1, [d2a, d2b], st, strong, forming, col, mv_txt, mv_sign, mv_word] ...]}
  -> liq     {x (f64), b, a, live_t, live_b, live_a, radius}               the LIMIT ORDERS curves as drawn
  -> tko     {buy (f64 keys), sell, form: [x, y, buy] | null}              the Takeover marks as drawn
  -> explain {k, html}                                                      the I x I click panel for cycle k
  -> hlh     {on, note, pics: [{k, x0, x1, ops?}], labels, dashes}         the HLH Volume Profile geometry (a pic's
             ops are sent once per pic identity; the tablet keeps them by k) -- see RecPainter
  -> bp      {on, sw, bub: [[x, price, usd, side, px]], dia: [[x, lo, hi, usd, buy, px]], lmax}  Big Player marks
  <- hi      {}                                                             first line from the tablet
  <- view    {x0, x1, follow}                                               the tablet's x range (epoch seconds)
  <- mode    {v}                                                            the I x I dropdown
  <- tog     {k, v}                                                         k: lines | hlh | bigplayer | takeover
  <- explain {k}                                                            k = the cycle's start (x0)
Exit codes: 2 = no daemon."""
import os, sys, time, json, shutil, tempfile, socket, threading, queue, base64, argparse, traceback, math

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
ap = argparse.ArgumentParser()
ap.add_argument("--listen", default="127.0.0.1")
ap.add_argument("--port", type=int, default=8766)
ap.add_argument("--tick-ms", type=int, default=250, help="how often the panes are read out")
ARGS = ap.parse_args()

from app import config                                        # noqa: E402
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
    def __init__(self, sock, addr):
        self.sock = sock; self.addr = addr
        self.out = queue.Queue(maxsize=400)
        self.alive = True
        self.ready = False                                    # said "hi"
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._writer, daemon=True).start()

    def _reader(self):
        buf = b""
        try:
            while self.alive:
                chunk = self.sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if line:
                        try:
                            CMDS.put(json.loads(line.decode("utf-8")))
                        except Exception:
                            pass
        except Exception:
            pass
        self.close()

    def _writer(self):
        try:
            while self.alive:
                m = self.out.get()
                if m is None:
                    break
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
        old = CLIENT[0]
        if old is not None:
            old.close()
        CLIENT[0] = Client(s, a)
        log("tablet connected from %s" % (a,))


# ------------------------------------------------------------------ the window
tunnel = _term.SSHTunnelManager()
if not _term._ipc_port_open():
    log("no tunnel on %s:%s -- opening one" % (config.IPC_HOST, config.IPC_PORT))
    tunnel.ensure()
    _tw = time.time()
    while not _term._ipc_port_open() and time.time() - _tw < 60.0:
        time.sleep(1.0)
w = MinimalTerminalWindow("5m"); w._rr_persist_save = lambda tf: None
w.resize(1600, 1000); w.show()


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
log("flow mode up | lookback N = %d | flow window %d s" % (w._lb_n(), int(w._flow_win)))
threading.Thread(target=serve, daemon=True).start()

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
    rev_hist = -1
    iimp_id = None; interp_id = None; liq_sig = None; tko_id = None
    hlh_out = None; hlh_t = 0.0; hlh_xm = None; hlh_pics = {}
    bw = True                 # the tablet's Chart Style: the HLH labels are built for a white or a dark ground
    bp_sig = None
    view = None; follow = True
    last_live = 0.0


S = State()


def send(obj):
    c = CLIENT[0]
    if c is not None and c.alive and c.ready:
        c.send(obj)


def hello():
    send({"t": "hello", "sym": config.SYMBOL, "tick": float(config.TICK_SIZE), "dec": int(config.PRICE_DECIMALS),
          "win": float(w._flow_win), "lb": int(w._lb_n()), "now": time.time(), "cfg": CFG,
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
        if base > pbase or base + n < pbase + pn:
            full = True                                       # bins were DROPPED (a prune, a reset): start over
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
    S.bins_sent = (base, cur[1].copy(), cur[2].copy(), cur[3].copy(), cur[4].copy(), cur[5].copy())


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
    px0, px1 = st.crosses_px(A, B, *XARGS); pxh, pxl = st.crosses_hl(A, B, *XARGS)
    key = (int(t.size), round(float(t[-1]), 2), round(float(t_end[-1]), 2), round(float(np.nansum(cbuy) + np.nansum(csell)), 1))
    if key == S.cyc_key and not force:
        return
    S.cyc_key = key
    cols = w._px_state_cols(t, t_end, done, move, is_buy, strong, cbuy, csell)
    pick_b, rate, state = w._cycle_impact(is_buy, strong, move, cbuy, csell)
    total = int(t.size)
    full = force or S.cyc_full_needed or total < S.cyc_last_total
    i0 = 0 if full else max(0, total - 8)
    S.cyc_full_needed = False; S.cyc_last_total = total
    sl = slice(i0, total)
    send({"t": "cyc", "i0": i0, "total": total, "ts": b64(t[sl], "<f8"), "te": b64(t_end[sl], "<f8"),
          "side": b64(is_buy[sl], "u1"), "strong": b64(strong[sl], "u1"), "done": b64(done[sl], "u1"),
          "move": b64(np.nan_to_num(move[sl], nan=0.0)), "cbuy": b64(cbuy[sl]), "csell": b64(csell[sl]),
          "o": b64(px0[sl]), "h": b64(pxh[sl]), "l": b64(pxl[sl]), "c": b64(px1[sl]),
          "col": b64(cols[sl], "i1"), "st": b64(state[sl], "i1"), "pickb": b64(pick_b[sl], "u1"),
          "rate": b64(np.nan_to_num(rate[sl], nan=0.0))})


def tick_iimp():
    L = w.__dict__.get("_iimp_last")
    if L is None or id(L) == S.iimp_id:
        return
    S.iimp_id = id(L)
    n = int(np.size(L["x0"]))
    m = {"t": "iimp", "mode": str(L.get("mode", "None")), "n": n}
    for k in ("x0", "x1"):
        m[k] = b64(L[k], "<f8")
    for k in ("v", "mult", "score", "wall", "reach", "mv", "arb", "ars", "kept", "sbuy", "ssell", "liib", "liis", "pliib", "pliis"):
        m[k] = b64(np.nan_to_num(np.asarray(L.get(k, np.full(n, np.nan)), dtype=np.float64), nan=-999.0))
    for k in ("up", "contra", "good", "form"):
        m[k] = b64(np.asarray(L[k], dtype=bool), "u1")
    for k in ("vac", "quiet"):
        m[k] = b64(np.asarray(L.get(k, np.zeros(n)), dtype=np.int8), "i1")
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
            t0, t1, head, name, d1, d2, st, strong, forming, col, mv_txt, mv_sign, mv_word = r
            out.append([float(t0), float(t1), str(head), str(name), str(d1), [str(d2[0]), str(d2[1])] if d2 else ["", ""],
                        int(st), bool(strong), bool(forming), int(col), str(mv_txt), int(mv_sign), str(mv_word)])
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


def tick_tko():
    d = w.__dict__.get("_px_iib_last")
    if d is None or id(d) == S.tko_id:
        return
    S.tko_id = id(d)
    f = d.get("form")
    send({"t": "tko", "buy": b64(d["buy"], "<f8"), "sell": b64(d["sell"], "<f8"),
          "form": [float(f[0]), float(f[1]), bool(f[2])] if f else None})


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
    out = st.build("tab", S.hlh_xm, bloc, not S.bw, week_on, now, badges=bdg, tables=tab, poc_runs=pcr, bars=bars)
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
        S.bins_sent = None; S.cyc_full_needed = True; S.iimp_id = None; S.interp_id = None; S.liq_sig = None; S.tko_id = None
        S.hlh_out = None; S.hlh_pics = {}; S.bp_sig = None
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
    elif k == "shot":                                          # debug: the offscreen window as the terminal draws it
        try:
            w.grab().save(str(c.get("path", "engine_shot.png")))
            send({"t": "shot", "ok": True})
        except Exception as ex:
            send({"t": "shot", "ok": False, "err": str(ex)})
    elif k == "series":                                        # debug: the terminal's own flow series for a range
        try:
            t_, b_, s_ = w._flow.series(float(c["x0"]), float(c["x1"]), float(w._flow_win), int(c.get("max_pts", 4000)))
            send({"t": "series", "x": b64(t_, "<f8"), "buy": b64(b_), "sell": b64(s_), "bin": float(w._flow.bin)})
        except Exception as ex:
            send({"t": "series", "err": str(ex)})
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
    now = time.time()
    try:
        st = w._flow
        rh = int(getattr(st, "rev_hist", 0))
        if rh != S.rev_hist:
            S.rev_hist = rh; S.cyc_full_needed = True
        tick_bins()
        tick_cycles(now, force=S.cyc_full_needed)
        tick_iimp(); tick_interp(); tick_liq(); tick_tko()
        tick_hlh(now); tick_bp(now)
        tick_live(now)
    except Exception:
        traceback.print_exc()


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
