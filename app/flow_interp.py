"""Interpretation pane: name what happened in each cycle, one row per cycle, newest first.

The user's state table (2026-09-11) has seven states across five columns -- buy vol, sell vol, bid book, ask
book, price. The states come from the QUADRANT MAP at the top of that same picture, which is the part that
survives contact with live tape:

        aggressive volume x price displacement

        light + big move   VACUUM        book pulled away, price gaps on little flow
        heavy + big move   BREAKOUT      one side overwhelms, price reprices
        heavy + small      ABSORPTION    heavy flow, price holds -- someone is refilling
        light + small      QUIET         nobody transacting

MEASURED FIRST on 20 h of live tape, n=670 cycles (669 finished), before any of this was drawn:

  * Aggressive volume is $ PER SECOND, never total $. Total-$ against the previous cycles shares 49% (buy) and
    53% (sell) of its variance with cycle DURATION -- a long cycle would read "heavy" on both sides at once and
    every long cycle would drift into breakout/absorption purely by being long. The rate drops that to 2-3%.
    This is the same trap the Volume pane fell into; it is checked now rather than discovered later.
  * Price displacement is the Speed pane's own ratio -- |ticks/s| against the same side's previous 5 -- so the
    two panes can never disagree about whether a cycle moved fast.
  * Splitting each axis at its own baseline (ratio > 1.0) populates the four quadrants 28 / 22 / 21 / 29%, and
    the seven named states 9.5-14.4% each. No cell is degenerate and nothing is forced.
  * CONFIDENCE is real and reported: the median cycle sits 0.49 log2 units from the crosshair, but 36% sit
    inside 0.35. Those are drawn DIM. A cycle that is 2% heavier and 2% faster than its baseline is not a
    breakout and the pane must not present it as one.

⚠ What did NOT reproduce, stated plainly: the table's full five-cell signature. Matching each labelled cycle
against its row's own (buy, sell, bid, ask) pattern scored 1.00 of 4 cells, where chance alone gives ~1.33 --
and that check had only ~42 usable cycles, so it is weak evidence either way. Either way it is not evidence
FOR the pattern. So the book and per-side volume are shown as EVIDENCE next to the state, never as inputs to
it. The two classifier axes are the ones that were measured to work.

⚠ Book coverage depends on ZOOM. A cycle needs LOB_MIN_COLS depth columns inside it; over a 24 h view the
liquidity window's columns are wider than a 77 s cycle, so only ~26% of cycles get a book reading. Zoomed in,
nearly all do. A missing reading prints "-", never a fabricated one.
"""
from __future__ import annotations

import time

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

# The four states carry the user's own palette from the state-space picture: green absorption, amber breakout,
# red vacuum, grey quiet.
ST_ABSORB, ST_BREAK, ST_VACUUM, ST_QUIET, ST_FORMING = 0, 1, 2, 3, 4
STATE_NAME = ("ABSORPTION", "BREAKOUT", "VACUUM", "QUIET", "forming")

# COLOUR is keyed on state AND side, because BREAKOUT is the aggressive state and the user wants that legible
# at a glance (2026-09-11): buy vivid green, sell vivid red, instead of the state-space picture's single
# amber. Absorption keeps its calmer teal and vacuum its coral, so all four stay separable by SATURATION as
# well as by the name written beside them.
C_ABSORB, C_BREAK_BUY, C_BREAK_SELL, C_VACUUM, C_QUIET, C_FORMING = 0, 1, 2, 3, 4, 5
BAR_COL = ("#1FB183", "#00C853", "#FF1F1F", "#E2574C", "#6B7A82", "#4E5C64")
# ... and TEXT is per THEME. It was not: on the white Simple BW ground every name drew in a pale dark-theme
# colour and was barely readable.
TXT_DARK = ("#3FD3A2", "#2BE86B", "#FF5A5A", "#F0857C", "#9AAAB2", "#6B7A82")
TXT_LIGHT = ("#0E7A57", "#00822F", "#C40D0D", "#A8382F", "#5A666D", "#6B7A82")

# the price move, coloured by the move itself: green up, red down, grey when it ended where it started
MOVE_DARK = ("#FF5A5A", "#7A828C", "#2BE86B")
MOVE_LIGHT = ("#C40D0D", "#77808A", "#00822F")

# kept for anything still importing the old names
STATE_COL = (BAR_COL[C_ABSORB], BAR_COL[C_BREAK_BUY], BAR_COL[C_VACUUM], BAR_COL[C_QUIET], BAR_COL[C_FORMING])
STATE_TXT = (TXT_DARK[C_ABSORB], TXT_DARK[C_BREAK_BUY], TXT_DARK[C_VACUUM], TXT_DARK[C_QUIET],
             TXT_DARK[C_FORMING])


def colour_of(st, side):
    """Which colour a row draws in. Only BREAKOUT splits by side."""
    if st == ST_BREAK:
        return C_BREAK_BUY if side == "buy" else C_BREAK_SELL
    return (C_ABSORB, None, C_VACUUM, C_QUIET, C_FORMING)[st]


def move_text(px0, px1, mv, flat, big, dec):
    """`100.01 -> 97.30  (-271t fast)` and which way it went: -1 down, 0 nowhere, +1 up.

    The sign is taken from the ROUNDED tick count -- the same number printed in the brackets -- so a row can
    never show "+0t" in a colour that claims a direction. That is the rule the cycle badges already follow."""
    t = int(round(float(mv))) if np.isfinite(mv) else 0
    sign = 0 if t == 0 else (1 if t > 0 else -1)
    spd = "flat" if flat else ("fast" if big else "slow")
    if not (np.isfinite(px0) and np.isfinite(px1)):
        return "(%+dt %s)" % (t, spd), sign
    f = "%%.%df" % int(dec)
    return (f + " -> " + f + "  (%+dt %s)") % (px0, px1, t, spd), sign


def dur_text(secs: float) -> str:
    """15s / 1m5s / 1h2m -- the user's format."""
    s = int(round(max(0.0, float(secs))))
    if s < 60:
        return "%ds" % s
    if s < 3600:
        m, r = divmod(s, 60)
        return "%dm%ds" % (m, r) if r else "%dm" % m
    h, r = divmod(s, 3600)
    m = r // 60
    return "%dh%dm" % (h, m) if m else "%dh" % h


def _clock(ts: float) -> str:
    lt = time.localtime(float(ts))
    return "%02d:%02d:%02d" % (lt.tm_hour, lt.tm_min, lt.tm_sec)


def _at(arr, k) -> float:
    """A column the caller did not supply reads as missing, never as zero."""
    if arr is None:
        return float("nan")
    try:
        return float(arr[k])
    except Exception:
        return float("nan")


def _ratio_text(r: float) -> str:
    if not np.isfinite(r):
        return "-"
    return ("%.2f" % r) if r < 10 else ("%.0f" % r)


def prev_ratio(vals, done, n_base: int, min_n: int, include_open: bool = False):
    """Each cycle's value over the MEDIAN of the PREVIOUS n_base cycles.

    Not same-side: total aggressive flow and the book both exist in every cycle, so the natural baseline is
    simply what came before -- the Book pane's rule.

    `include_open` rates the cycle STILL FORMING against that same baseline, from what has accumulated so
    far, so the feed can name a state while it is happening (user 2026-09-11). An unfinished cycle is never
    APPENDED to the history whichever way the flag is set: a partial cycle is not a normal, and letting one
    in would drag every later reading toward a half-formed value."""
    n = int(np.size(vals))
    out = np.full(n, np.nan)
    if n == 0:
        return out
    v = np.asarray(vals, dtype=np.float64)
    dn = np.asarray(done, dtype=bool)
    hist = []
    nb = max(1, int(n_base)); mn = max(1, int(min_n))
    for k in range(n):
        if not (bool(dn[k]) or include_open):
            continue
        if not (np.isfinite(v[k]) and v[k] > 0):
            continue
        if len(hist) >= mn:
            base = float(np.median(hist[-nb:]))
            if base > 0:
                out[k] = v[k] / base
        if dn[k]:
            hist.append(float(v[k]))
    return out


def same_side_ratio(vals, is_dom_buy, done, n_base: int, min_n: int, include_open: bool = False):
    """The Speed pane's rule -- each cycle against the SAME side's previous n_base -- with the open cycle
    optionally rated too.

    Identical to the terminal's own _same_side_ratio on FINISHED cycles, and a test gate holds the two to
    each other. That includes its `v >= 0` guard: zero is a legitimate speed, and requiring v > 0 silently
    dropped exactly the cycles the FLAT class exists to show. The open cycle enters no side's history."""
    n = int(np.size(done))
    out = np.full(n, np.nan)
    if n == 0:
        return out
    v = np.asarray(vals, dtype=np.float64)
    dn = np.asarray(done, dtype=bool)
    db = np.asarray(is_dom_buy, dtype=bool)
    hist = {True: [], False: []}
    nb = max(1, int(n_base)); mn = max(1, int(min_n))
    for k in range(n):
        if not (bool(dn[k]) or include_open):
            continue
        if not (np.isfinite(v[k]) and v[k] >= 0):
            continue
        h = hist[bool(db[k])]
        if len(h) >= mn:
            base = float(np.median(h[-nb:]))
            if base > 0:
                out[k] = v[k] / base
        if dn[k]:
            h.append(float(v[k]))
    return out


def _quadrant(heavy, big, up, dom_buy):
    """The user's quadrant map. Breakout and vacuum name the direction PRICE went; absorption names the side
    doing the AGGRESSING -- the one being absorbed -- which is the cycle's own dominance flag, i.e. exactly
    what the vertical line's colour already shows."""
    if heavy and big:
        return ST_BREAK, ("buy" if up else "sell")
    if heavy:
        return ST_ABSORB, ("buy" if dom_buy else "sell")
    if big:
        return ST_VACUUM, ("buy" if up else "sell")
    return ST_QUIET, ""


def _line1(vr_k, buy_ratio, sell_ratio, k):
    return "flow %sx   buy %s  sell %s" % (_ratio_text(vr_k), _ratio_text(_at(buy_ratio, k)),
                                           _ratio_text(_at(sell_ratio, k)))


def _line2(bid_ratio, ask_ratio, k):
    """The user's five columns finish here: bid book and ask book. The price move moved up beside the state
    name, so it is not repeated down here."""
    return "bid %s  ask %s" % (_ratio_text(_at(bid_ratio, k)), _ratio_text(_at(ask_ratio, k)))


def build_rows(t, t_end, done, move, side_dom, vol_ratio, speed_ratio,
               bid_ratio, ask_ratio, buy_ratio, sell_ratio, flat_ticks, weak_below, max_rows,
               now=None, live=True, px_start=None, px_end=None, px_dec=2):
    """One display row per cycle, NEWEST FIRST. Pure function of arrays -- no Qt, so it is directly testable.

    Every string is built here, once per rebuild, so paintEvent only ever draws pre-made text."""
    n = int(np.size(t))
    if n == 0:
        return []
    mv = np.nan_to_num(np.asarray(move, dtype=np.float64), nan=0.0)
    dn = np.asarray(done, dtype=bool)
    vr = np.asarray(vol_ratio, dtype=np.float64)
    sr = np.asarray(speed_ratio, dtype=np.float64)
    sd = np.asarray(side_dom, dtype=bool)
    flat = np.abs(mv) < float(flat_ticks)
    # each axis is split at its OWN baseline: "heavier than this cycle's own recent normal", "faster than the
    # same side's recent normal". The measured medians were 0.963 and 0.981, i.e. within 4% of 1.0, so the
    # self-describing cut costs nothing and needs no magic number.
    heavy = vr > 1.0
    big = (sr > 1.0) & ~flat
    up = mv > 0
    rateable = np.isfinite(vr) & np.isfinite(sr)
    ok = rateable & dn
    # distance from the crosshair, in log2 units, on whichever axis is the WEAKER of the two -- a cycle only
    # earns a confident label if BOTH axes are clear of their baseline
    with np.errstate(divide="ignore", invalid="ignore"):
        conf = np.minimum(np.abs(np.log2(np.maximum(vr, 1e-9))), np.abs(np.log2(np.maximum(sr, 1e-9))))

    rows = []
    order = range(n - 1, -1, -1)
    for k in order:
        if len(rows) >= int(max_rows):
            break
        t0 = float(t[k]); t1 = float(t_end[k])
        if not dn[k]:
            # ⚠ crosses() marks the LAST cycle of any read unfinished, whether or not that read reached the
            # live edge. Panned into the past, that "forming" cycle finished hours ago AND its volume is
            # truncated at the view's right edge -- so it is dropped, not relabelled. Only when the view
            # actually contains the live edge is the open cycle real.
            if not live:
                continue
            # The RUNNING interpretation (user 2026-09-11): rate what has accumulated SO FAR against the same
            # baselines the finished cycles use, and name the state while it is happening. It is tagged
            # "forming" and never counts as settled -- a cycle that is heavy-and-fast at 30 s can still end
            # heavy-and-flat. t_end arrives clamped to now, so the elapsed here is the real one.
            el = max(0.0, t1 - t0) if t1 > t0 else max(
                0.0, (float(now) if now is not None else time.time()) - t0)
            head = "%s - ... - %s" % (_clock(t0), dur_text(el))
            if not rateable[k]:
                rows.append((t0, t0 + el, head, "forming", "", "", ST_FORMING, False, False, C_FORMING, "", 0))
                continue
            st, side = _quadrant(heavy[k], big[k], up[k], sd[k])
            _mt, _ms = move_text(_at(px_start, k), _at(px_end, k), mv[k], flat[k], big[k], px_dec)
            rows.append((t0, t0 + el, head, STATE_NAME[st] + ((" " + side) if side else ""),
                         _line1(vr[k], buy_ratio, sell_ratio, k),
                         _line2(bid_ratio, ask_ratio, k),
                         st, bool(conf[k] >= float(weak_below)), True,
                         colour_of(st, side), _mt, _ms))
            continue
        if not ok[k]:
            rows.append((t0, t1, "%s - %s - %s" % (_clock(t0), _clock(t1), dur_text(t1 - t0)),
                         "-", "not enough history yet", "", ST_QUIET, False, False, C_QUIET, "", 0))
            continue
        st, side = _quadrant(heavy[k], big[k], up[k], sd[k])
        _mt, _ms = move_text(_at(px_start, k), _at(px_end, k), mv[k], flat[k], big[k], px_dec)
        rows.append((t0, t1, "%s - %s - %s" % (_clock(t0), _clock(t1), dur_text(t1 - t0)),
                     STATE_NAME[st] + ((" " + side) if side else ""),
                     _line1(vr[k], buy_ratio, sell_ratio, k),
                     _line2(bid_ratio, ask_ratio, k),
                     st, bool(conf[k] >= float(weak_below)), False,
                     colour_of(st, side), _mt, _ms))
    return rows


class FlowInterpPanel(QtWidgets.QAbstractScrollArea):
    """A vertical feed of cycle interpretations, newest at the top.

    Only the rows actually on screen are painted. At 400 cycles that is ~14 rows of four short strings, so a
    repaint is a handful of drawText calls whatever the history depth -- the list length never enters the
    per-frame cost."""

    cycleClicked = QtCore.Signal(float, float)      # (t_start, t_end) of the clicked row
    lookbackChanged = QtCore.Signal(int)           # the cycle lookback N, bottom-right

    ROW_H = 78
    PAD = 10
    FOOT_H = 26

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list = []
        self._dark = True
        self._top_t = None
        self._hover = -1
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.viewport().setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)
        self.viewport().setMouseTracking(True)
        self.setMinimumWidth(300)      # under this the price-move line starts to clip
        self.setMaximumWidth(620)
        self._hint_w = 330
        self._f_head = QtGui.QFont(); self._f_head.setPointSize(8)
        self._f_name = QtGui.QFont(); self._f_name.setPointSize(10); self._f_name.setBold(True)
        self._f_det = QtGui.QFont(); self._f_det.setPointSize(8)
        # the move reads as part of the interpretation, so it is bolder than the evidence lines below it
        self._f_move = QtGui.QFont(); self._f_move.setPointSize(8); self._f_move.setBold(True)
        self._f_title = QtGui.QFont(); self._f_title.setPointSize(8); self._f_title.setBold(True)
        self._build_footer()
        self.title = "INTERPRETATION"      # the terminal replaces this with config.PANE_TITLE_INTERP, the
        #                                    same string its hamburger toggle carries

    def sizeHint(self):
        return QtCore.QSize(int(self._hint_w), 600)

    # ---- the lookback control ---------------------------------------------------------------------------
    def _build_footer(self) -> None:
        """`lookback [5] cycles`, bottom-right. A child of the PANEL, not the viewport, so it stays put while
        the feed scrolls underneath it; _update_scroll reserves FOOT_H so the last row can still clear it."""
        self._foot = QtWidgets.QWidget(self)
        lay = QtWidgets.QHBoxLayout(self._foot)
        lay.setContentsMargins(6, 2, 6, 2); lay.setSpacing(6)
        self._foot_lbl = QtWidgets.QLabel("lookback")
        f = QtGui.QFont(); f.setPointSize(8)
        self._foot_lbl.setFont(f)
        self._spin = QtWidgets.QSpinBox()
        self._spin.setFont(f)
        self._spin.setRange(2, 20)
        self._spin.setValue(5)
        self._spin.setFixedWidth(52)
        self._spin.setAlignment(QtCore.Qt.AlignRight)
        self._spin.setToolTip(
            "How many previous cycles every rating is measured against.\n\n"
            "One knob for the whole family: CYCLE VOLUME, CYCLE BOOK, CYCLE SPEED and the states in this feed "
            "all compare a cycle to its last N. Volume and Speed use the last N of the SAME side, so they "
            "reach about twice as far back in time.\n\n"
            "Smaller reacts faster and is noisier; larger is steadier and slower to notice a change. The band "
            "cuts were measured at 5.")
        self._foot_sfx = QtWidgets.QLabel("cycles")
        self._foot_sfx.setFont(f)
        lay.addWidget(self._foot_lbl); lay.addWidget(self._spin); lay.addWidget(self._foot_sfx)
        self._foot.resize(self._foot.sizeHint())
        # the wheel must scroll the FEED, never nudge the number under the cursor by accident
        self._spin.wheelEvent = lambda ev: ev.ignore()
        self._spin.valueChanged.connect(lambda v: self.lookbackChanged.emit(int(v)))
        self._style_footer()

    def _style_footer(self) -> None:
        dark = self._dark
        fg = "#8a939c" if dark else "#666666"
        bg = "#1b1b1b" if dark else "#f2f2f2"
        bd = "#2f363d" if dark else "#d8d8d8"
        self._foot.setStyleSheet(
            "QWidget { background: %s; }"
            "QLabel { color: %s; background: transparent; }"
            "QSpinBox { color: %s; background: %s; border: 1px solid %s; padding: 0px 2px; }"
            % (bg, fg, "#dfe4ea" if dark else "#222222", bg, bd))

    def lookback(self) -> int:
        return int(self._spin.value())

    def setLookback(self, n: int) -> None:
        self._spin.blockSignals(True)
        self._spin.setValue(max(self._spin.minimum(), min(self._spin.maximum(), int(n))))
        self._spin.blockSignals(False)

    def _place_footer(self) -> None:
        w = self.viewport().width(); h = self.viewport().height()
        sz = self._foot.sizeHint()
        self._foot.resize(sz)
        self._foot.move(max(0, w - sz.width() - 2), max(0, h - sz.height() - 2))
        self._foot.raise_()

    # ---- data -------------------------------------------------------------------------------------------
    def setRows(self, rows) -> None:
        """Replace the feed. Keeps the reader's place: if they have scrolled down into history, the scrollbar
        moves by however many rows were prepended, so the cycle they were reading stays under the cursor."""
        sb = self.verticalScrollBar()
        old_top, val = self._top_t, sb.value()
        self._rows = rows or []
        if val > 0 and old_top is not None:
            added = 0
            for r in self._rows:
                if r[0] <= old_top + 1e-6:
                    break
                added += 1
            if added:
                val += added * self.ROW_H
        self._top_t = self._rows[0][0] if self._rows else None
        self._update_scroll()
        sb.setValue(min(val, sb.maximum()))
        self.viewport().update()

    def setDark(self, dark: bool) -> None:
        if bool(dark) != self._dark:
            self._dark = bool(dark)
            self._style_footer()
            self.viewport().update()

    def _update_scroll(self) -> None:
        sb = self.verticalScrollBar()
        # + FOOT_H so the last row can scroll clear of the lookback control rather than sitting under it
        total = len(self._rows) * self.ROW_H + self.PAD * 2 + 22 + self.FOOT_H
        sb.setRange(0, max(0, total - self.viewport().height()))
        sb.setPageStep(self.viewport().height())
        sb.setSingleStep(self.ROW_H)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._update_scroll()
        self._place_footer()

    # ---- interaction ------------------------------------------------------------------------------------
    def _row_at(self, y: int) -> int:
        i = (y + self.verticalScrollBar().value() - self.PAD - 22) // self.ROW_H
        return int(i) if 0 <= i < len(self._rows) else -1

    def mouseMoveEvent(self, ev):
        i = self._row_at(int(ev.position().y()))
        if i != self._hover:
            self._hover = i
            self.viewport().update()

    def leaveEvent(self, ev):
        if self._hover != -1:
            self._hover = -1
            self.viewport().update()

    def mousePressEvent(self, ev):
        if self._foot.geometry().contains(ev.position().toPoint()):
            return                                  # the control sits ON TOP of the feed; let it have the click
        i = self._row_at(int(ev.position().y()))
        if i >= 0:
            r = self._rows[i]
            self.cycleClicked.emit(float(r[0]), float(r[1]))

    # ---- paint ------------------------------------------------------------------------------------------
    def paintEvent(self, ev):
        p = QtGui.QPainter(self.viewport())
        w = self.viewport().width(); h = self.viewport().height()
        bg = QtGui.QColor("#141414" if self._dark else "#ffffff")
        p.fillRect(0, 0, w, h, bg)
        dim = QtGui.QColor("#6f7a82" if self._dark else "#8a8a8a")
        det = QtGui.QColor("#8FA0A8" if self._dark else "#666666")
        hl = QtGui.QColor(255, 255, 255, 14) if self._dark else QtGui.QColor(0, 0, 0, 12)

        p.setFont(self._f_title)
        p.setPen(QtGui.QColor("#7d8492"))
        p.drawText(self.PAD, 15, self.title)
        p.setPen(QtGui.QColor("#2a3138" if self._dark else "#dddddd"))
        p.drawLine(self.PAD, 20, w - self.PAD, 20)

        off = self.verticalScrollBar().value()
        y_top = self.PAD + 22 - off
        first = max(0, (off - self.PAD - 22) // self.ROW_H)
        last = min(len(self._rows), first + h // self.ROW_H + 2)
        x = self.PAD
        txt_pal = TXT_DARK if self._dark else TXT_LIGHT
        mv_pal = MOVE_DARK if self._dark else MOVE_LIGHT
        fm_name = QtGui.QFontMetrics(self._f_name)
        fm_head = QtGui.QFontMetrics(self._f_head)
        for i in range(int(first), int(last)):
            t0, t1, head, name, d1, d2, st, strong, forming, col, mv_txt, mv_sign = self._rows[i]
            y = y_top + i * self.ROW_H
            if y > h or y + self.ROW_H < 20:
                continue
            if i == self._hover:
                p.fillRect(0, y, w, self.ROW_H - 4, hl)
            # the state's colour bar: full width and opacity for a confident cycle, thin and faded for one
            # sitting near its own baseline, which is most of the point of the confidence measurement
            bar = QtGui.QColor(BAR_COL[col])
            if not strong:
                bar.setAlpha(105)
            if forming:
                # dashes, not a solid rule: the cycle is still open and this reading is not final
                _w = 4 if strong else 2
                _y = y + 3
                while _y < y + self.ROW_H - 12:
                    p.fillRect(x, _y, _w, 5, bar)
                    _y += 9
            else:
                p.fillRect(x, y + 3, 4 if strong else 2, self.ROW_H - 12, bar)
            p.setFont(self._f_head); p.setPen(dim)
            p.drawText(x + 12, y + 14, head)
            # a running read is tagged "forming" -- the live state of an unfinished cycle, which can still
            # change -- and a settled one near its own baseline is tagged "weak". Both sit at the RIGHT end of
            # the header line, because the name line now carries the price move.
            _tag = "forming" if forming else ("" if (strong or st == ST_FORMING or name == "-") else "weak")
            if _tag:
                p.setPen(QtGui.QColor(txt_pal[col]) if forming else dim)
                p.drawText(w - self.PAD - fm_head.horizontalAdvance(_tag), y + 14, _tag)
            tc = QtGui.QColor(txt_pal[col])
            if not strong:
                tc.setAlpha(165)
            p.setFont(self._f_name); p.setPen(tc)
            p.drawText(x + 12, y + 30, name)
            # the price move sits NEXT TO the state (user 2026-09-11), coloured by the move and not by the
            # state: green up, red down, grey when it ended where it started
            if mv_txt:
                p.setFont(self._f_move)
                p.setPen(QtGui.QColor(mv_pal[int(mv_sign) + 1]))
                p.drawText(x + 12, y + 46, mv_txt)
            if d1:
                p.setFont(self._f_det); p.setPen(det)
                p.drawText(x + 12, y + 60, d1)
            if d2:
                p.setFont(self._f_det); p.setPen(dim)
                p.drawText(x + 12, y + 72, d2)
        if not self._rows:
            p.setFont(self._f_det); p.setPen(dim)
            p.drawText(self.PAD, 44, "no finished cycles in view")
        p.end()
