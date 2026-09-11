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
STATE_COL = ("#1FB183", "#E0A030", "#E2574C", "#6B7A82", "#4E5C64")
STATE_TXT = ("#3FD3A2", "#EDBB6B", "#F0857C", "#9AAAB2", "#6B7A82")
STATE_NAME = ("ABSORPTION", "BREAKOUT", "VACUUM", "QUIET", "forming")


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


def prev_ratio(vals, done, n_base: int, min_n: int):
    """Each finished cycle's value over the MEDIAN of the PREVIOUS n_base cycles.

    Not same-side: total aggressive flow and the book both exist in every cycle, so the natural baseline is
    simply what came before -- the Book pane's rule. (The SPEED ratio is same-side, and it comes in already
    computed by the Speed pane, so the two panes cannot disagree.)"""
    n = int(np.size(vals))
    out = np.full(n, np.nan)
    if n == 0:
        return out
    v = np.asarray(vals, dtype=np.float64)
    dn = np.asarray(done, dtype=bool)
    hist: list[float] = []
    nb = max(1, int(n_base)); mn = max(1, int(min_n))
    for k in range(n):
        if not (dn[k] and np.isfinite(v[k]) and v[k] > 0):
            continue
        if len(hist) >= mn:
            base = float(np.median(hist[-nb:]))
            if base > 0:
                out[k] = v[k] / base
        hist.append(float(v[k]))
    return out


def build_rows(t, t_end, done, move, side_dom, vol_ratio, speed_ratio,
               bid_ratio, ask_ratio, buy_ratio, sell_ratio, flat_ticks, weak_below, max_rows,
               now=None, live=True):
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
    ok = np.isfinite(vr) & np.isfinite(sr) & dn
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
            # still forming: elapsed time only, and NO state -- it has not finished, so nothing to classify
            el = max(0.0, (float(now) if now is not None else time.time()) - t0)
            rows.append((t0, t0 + el, "%s - ... - %s" % (_clock(t0), dur_text(el)),
                         "forming", "", "", ST_FORMING, False))
            continue
        if not ok[k]:
            rows.append((t0, t1, "%s - %s - %s" % (_clock(t0), _clock(t1), dur_text(t1 - t0)),
                         "-", "not enough history yet", "", ST_QUIET, False))
            continue
        if heavy[k] and big[k]:
            st = ST_BREAK; side = "buy" if up[k] else "sell"
        elif heavy[k]:
            # absorption names the side doing the AGGRESSING -- the one being absorbed. That is the cycle's own
            # dominance flag, which is what the vertical line's colour already shows.
            st = ST_ABSORB; side = "buy" if sd[k] else "sell"
        elif big[k]:
            st = ST_VACUUM; side = "buy" if up[k] else "sell"
        else:
            st = ST_QUIET; side = ""
        strong = bool(conf[k] >= float(weak_below))
        name = STATE_NAME[st] + ((" " + side) if side else "")
        spd = "flat" if flat[k] else ("fast" if big[k] else "slow")
        # the user's five columns, in their own order: buy vol, sell vol, bid book, ask book, price -- with the
        # TOTAL flow rate in front, because that total is what actually chose the state
        d1 = "flow %sx   buy %s  sell %s" % (_ratio_text(vr[k]),
                                             _ratio_text(_at(buy_ratio, k)), _ratio_text(_at(sell_ratio, k)))
        d2 = "bid %s  ask %s%s%+dt %s" % (_ratio_text(_at(bid_ratio, k)), _ratio_text(_at(ask_ratio, k)),
                                          " " * 4, int(round(mv[k])), spd)
        rows.append((t0, t1, "%s - %s - %s" % (_clock(t0), _clock(t1), dur_text(t1 - t0)),
                     name, d1, d2, st, strong))
    return rows


class FlowInterpPanel(QtWidgets.QAbstractScrollArea):
    """A vertical feed of cycle interpretations, newest at the top.

    Only the rows actually on screen are painted. At 400 cycles that is ~14 rows of four short strings, so a
    repaint is a handful of drawText calls whatever the history depth -- the list length never enters the
    per-frame cost."""

    cycleClicked = QtCore.Signal(float, float)      # (t_start, t_end) of the clicked row

    ROW_H = 64
    PAD = 10

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
        self.setMinimumWidth(250)
        self.setMaximumWidth(520)
        self._hint_w = 330
        self._f_head = QtGui.QFont(); self._f_head.setPointSize(8)
        self._f_name = QtGui.QFont(); self._f_name.setPointSize(10); self._f_name.setBold(True)
        self._f_det = QtGui.QFont(); self._f_det.setPointSize(8)
        self._f_title = QtGui.QFont(); self._f_title.setPointSize(8); self._f_title.setBold(True)

    def sizeHint(self):
        return QtCore.QSize(int(self._hint_w), 600)

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
            self.viewport().update()

    def _update_scroll(self) -> None:
        sb = self.verticalScrollBar()
        total = len(self._rows) * self.ROW_H + self.PAD * 2 + 22
        sb.setRange(0, max(0, total - self.viewport().height()))
        sb.setPageStep(self.viewport().height())
        sb.setSingleStep(self.ROW_H)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._update_scroll()

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
        p.drawText(self.PAD, 15, "INTERPRETATION")
        p.setPen(QtGui.QColor("#2a3138" if self._dark else "#dddddd"))
        p.drawLine(self.PAD, 20, w - self.PAD, 20)

        off = self.verticalScrollBar().value()
        y_top = self.PAD + 22 - off
        first = max(0, (off - self.PAD - 22) // self.ROW_H)
        last = min(len(self._rows), first + h // self.ROW_H + 2)
        x = self.PAD
        for i in range(int(first), int(last)):
            t0, t1, head, name, d1, d2, st, strong = self._rows[i]
            y = y_top + i * self.ROW_H
            if y > h or y + self.ROW_H < 20:
                continue
            if i == self._hover:
                p.fillRect(0, y, w, self.ROW_H - 4, hl)
            # the state's colour bar: full width and opacity for a confident cycle, thin and faded for one
            # sitting near its own baseline, which is most of the point of the confidence measurement
            bar = QtGui.QColor(STATE_COL[st])
            if not strong:
                bar.setAlpha(105)
            p.fillRect(x, y + 3, 4 if strong else 2, self.ROW_H - 12, bar)
            p.setFont(self._f_head); p.setPen(dim)
            p.drawText(x + 12, y + 14, head)
            tc = QtGui.QColor(STATE_TXT[st])
            if not strong:
                tc.setAlpha(150)
            p.setFont(self._f_name); p.setPen(tc)
            p.drawText(x + 12, y + 30, name)
            if not strong and st != ST_FORMING and name != "-":
                fm = QtGui.QFontMetrics(self._f_name)
                p.setFont(self._f_head); p.setPen(dim)
                p.drawText(x + 18 + fm.horizontalAdvance(name), y + 30, "weak")
            if d1:
                p.setFont(self._f_det); p.setPen(det)
                p.drawText(x + 12, y + 44, d1)
            if d2:
                p.setFont(self._f_det); p.setPen(dim)
                p.drawText(x + 12, y + 56, d2)
        if not self._rows:
            p.setFont(self._f_det); p.setPen(dim)
            p.drawText(self.PAD, 44, "no finished cycles in view")
        p.end()
