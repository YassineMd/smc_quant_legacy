"""Flutter/Material-style dark date + time picker (pure PySide6, no deps).

``DateTimeField`` is a drop-in replacement for the QDateTimeEdit the hamburger used for the Scan-Start anchor:
it exposes ``dateTime()`` / ``setDateTime()`` and a ``dateTimeChanged`` signal (so ``blockSignals`` + the existing
wiring keep working), but renders as a styled field that opens ``FlutterDateTimePicker`` — a frameless, rounded,
Material-style dialog with an accent header, a month calendar (circular-selected day, today-ringed), and a clean
HH:MM time row. Self-contained; safe to import anywhere.
"""
from __future__ import annotations

from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

# --- palette (dark, Material-ish; blue accent to match the app's #3498db family) ---
ACCENT = "#3b82f6"
ACCENT_DK = "#2563eb"
BG = "#1b1e26"
CARD = "#232733"
TEXT = "#e6e9ef"
MUTED = "#8891a3"
FAINT = "#4b5361"
_WEEK = ["S", "M", "T", "W", "T", "F", "S"]   # Sunday-first header


class FlutterDateTimePicker(QtWidgets.QDialog):
    """Modal Material-style date+time picker. Use ``FlutterDateTimePicker.get(initial, parent)``."""

    def __init__(self, initial: QtCore.QDateTime, parent=None,
                 min_date: "Optional[QtCore.QDate]" = None, max_date: "Optional[QtCore.QDate]" = None):
        super().__init__(parent)
        self.setWindowFlags(QtCore.Qt.Dialog | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)   # rounded card shows through
        self.setModal(True)
        if not initial or not initial.isValid():
            initial = QtCore.QDateTime.currentDateTime()
        self._min = QtCore.QDate(min_date) if (min_date and min_date.isValid()) else None   # earliest data day
        self._max = QtCore.QDate(max_date) if (max_date and max_date.isValid()) else None    # latest data day (today)
        self._sel = QtCore.QDate(initial.date())                     # selected day
        self._sel = self._clamp(self._sel)                           # never open on a no-data day
        self._disp = QtCore.QDate(self._sel.year(), self._sel.month(), 1)   # month on screen
        self._day_btns: list[QtWidgets.QPushButton] = []
        self._drag = None
        self._build(initial.time())
        self._refresh_header()
        self._refresh_grid()

    # ---- build ----
    def _build(self, t0: QtCore.QTime) -> None:
        wrap = QtWidgets.QVBoxLayout(self)
        wrap.setContentsMargins(18, 18, 18, 18)          # room for the drop shadow
        card = QtWidgets.QFrame(self)
        card.setObjectName("card")
        card.setStyleSheet(
            "QFrame#card{background:%s; border-radius:16px;}"
            "QLabel{color:%s; font-family:Consolas;}" % (BG, TEXT))
        sh = QtWidgets.QGraphicsDropShadowEffect(self)
        sh.setBlurRadius(34); sh.setOffset(0, 8); sh.setColor(QtGui.QColor(0, 0, 0, 200))
        card.setGraphicsEffect(sh)
        wrap.addWidget(card)
        col = QtWidgets.QVBoxLayout(card)
        col.setContentsMargins(0, 0, 0, 0); col.setSpacing(0)

        # --- accent header (draggable) ---
        self._hdr = QtWidgets.QFrame(); self._hdr.setObjectName("hdr")
        self._hdr.setStyleSheet("QFrame#hdr{background:%s; border-top-left-radius:16px;"
                                "border-top-right-radius:16px;}" % ACCENT)
        hl = QtWidgets.QVBoxLayout(self._hdr); hl.setContentsMargins(22, 16, 22, 16); hl.setSpacing(2)
        cap = QtWidgets.QLabel("SELECT DATE & TIME")
        cap.setStyleSheet("color:rgba(255,255,255,0.82); font-size:10px; font-weight:bold; letter-spacing:1px;")
        self._hdr_date = QtWidgets.QLabel("—")
        self._hdr_date.setStyleSheet("color:#ffffff; font-size:26px; font-weight:bold;")
        hl.addWidget(cap); hl.addWidget(self._hdr_date)
        col.addWidget(self._hdr)

        body = QtWidgets.QVBoxLayout(); body.setContentsMargins(20, 16, 20, 18); body.setSpacing(12)
        col.addLayout(body)

        # --- month nav ---
        nav = QtWidgets.QHBoxLayout(); nav.setSpacing(4)
        self._month_lbl = QtWidgets.QLabel("—")
        self._month_lbl.setStyleSheet("font-size:14px; font-weight:bold;")
        self._prev = self._nav_btn("‹"); self._nxt = self._nav_btn("›")
        self._prev.clicked.connect(lambda: self._shift_month(-1)); self._nxt.clicked.connect(lambda: self._shift_month(1))
        nav.addWidget(self._month_lbl); nav.addStretch(1); nav.addWidget(self._prev); nav.addWidget(self._nxt)
        body.addLayout(nav)

        # --- calendar grid (row 0 = weekday header, rows 1-6 = days) ---
        grid = QtWidgets.QGridLayout(); grid.setSpacing(2)
        for c, w in enumerate(_WEEK):
            lab = QtWidgets.QLabel(w, alignment=QtCore.Qt.AlignCenter)
            lab.setStyleSheet("color:%s; font-size:11px; font-weight:bold;" % MUTED)
            grid.addWidget(lab, 0, c)
        for i in range(42):
            b = QtWidgets.QPushButton("")
            b.setFixedSize(34, 34); b.setCursor(QtCore.Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, idx=i: self._pick_cell(idx))
            self._day_btns.append(b)
            grid.addWidget(b, 1 + i // 7, i % 7)
        body.addLayout(grid)

        # --- divider ---
        line = QtWidgets.QFrame(); line.setFixedHeight(1)
        line.setStyleSheet("background:%s;" % CARD)
        body.addWidget(line)

        # --- time row ---
        tr = QtWidgets.QHBoxLayout(); tr.setSpacing(8)
        tl = QtWidgets.QLabel("TIME"); tl.setStyleSheet("color:%s; font-size:10px; font-weight:bold; letter-spacing:1px;" % MUTED)
        self.sp_h = self._time_spin(0, 23, t0.hour())
        self.sp_m = self._time_spin(0, 59, t0.minute())
        colon = QtWidgets.QLabel(":"); colon.setStyleSheet("font-size:22px; font-weight:bold; color:%s;" % TEXT)
        tr.addWidget(tl); tr.addStretch(1); tr.addWidget(self.sp_h); tr.addWidget(colon); tr.addWidget(self.sp_m)
        body.addLayout(tr)

        # --- actions ---
        act = QtWidgets.QHBoxLayout(); act.addStretch(1); act.setSpacing(4)
        cancel = self._text_btn("CANCEL", MUTED); ok = self._text_btn("OK", ACCENT)
        cancel.clicked.connect(self.reject); ok.clicked.connect(self.accept)
        act.addWidget(cancel); act.addWidget(ok)
        body.addLayout(act)

    def _nav_btn(self, ch: str) -> QtWidgets.QPushButton:
        b = QtWidgets.QPushButton(ch); b.setFixedSize(30, 30); b.setCursor(QtCore.Qt.PointingHandCursor)
        b.setStyleSheet("QPushButton{background:transparent; color:%s; border:none; border-radius:15px;"
                        "font-size:20px; font-weight:bold;}"
                        "QPushButton:hover{background:%s; color:%s;}" % (TEXT, CARD, ACCENT))
        return b

    def _text_btn(self, txt: str, col: str) -> QtWidgets.QPushButton:
        b = QtWidgets.QPushButton(txt); b.setCursor(QtCore.Qt.PointingHandCursor)
        b.setStyleSheet("QPushButton{background:transparent; color:%s; border:none; border-radius:6px;"
                        "padding:8px 14px; font-size:12px; font-weight:bold; letter-spacing:1px;}"
                        "QPushButton:hover{background:%s;}" % (col, CARD))
        return b

    def _time_spin(self, lo: int, hi: int, val: int) -> QtWidgets.QSpinBox:
        s = QtWidgets.QSpinBox(); s.setRange(lo, hi); s.setWrapping(True); s.setValue(val)
        s.setAlignment(QtCore.Qt.AlignCenter); s.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        s.setFixedWidth(58)
        s.setStyleSheet("QSpinBox{background:%s; color:%s; border:1px solid %s; border-radius:9px;"
                        "padding:6px 4px; font-size:22px; font-weight:bold; font-family:Consolas;}"
                        "QSpinBox:focus{border-color:%s; background:#2a2f3d;}" % (CARD, TEXT, "#2a2e39", ACCENT))
        return s

    # ---- range gating (disable no-data days) ----
    def _in_range(self, d: QtCore.QDate) -> bool:
        if self._min is not None and d < self._min:
            return False
        if self._max is not None and d > self._max:
            return False
        return True

    def _clamp(self, d: QtCore.QDate) -> QtCore.QDate:
        if self._min is not None and d < self._min:
            return QtCore.QDate(self._min)
        if self._max is not None and d > self._max:
            return QtCore.QDate(self._max)
        return d

    def _month_has_data(self, first: QtCore.QDate) -> bool:
        """Any in-range day this month? (last day >= min and first day <= max)."""
        last = QtCore.QDate(first.year(), first.month(), first.daysInMonth())
        return self._in_range(first) or self._in_range(last) or (
            self._min is not None and self._max is not None and first <= self._min and last >= self._max)

    # ---- state / refresh ----
    def _shift_month(self, delta: int) -> None:
        self._disp = self._disp.addMonths(delta)
        self._refresh_grid()

    def _pick_cell(self, idx: int) -> None:
        first = QtCore.QDate(self._disp.year(), self._disp.month(), 1)
        start = first.dayOfWeek() % 7                     # Mon=1..Sun=7 -> Sun-first column
        day = idx - start + 1
        if 1 <= day <= first.daysInMonth():
            d = QtCore.QDate(self._disp.year(), self._disp.month(), day)
            if not self._in_range(d):                     # no data on this day -> ignore the click
                return
            self._sel = d
            self._refresh_header(); self._refresh_grid()

    def _refresh_header(self) -> None:
        self._hdr_date.setText(self._sel.toString("ddd, MMM d"))

    def _refresh_grid(self) -> None:
        self._month_lbl.setText(self._disp.toString("MMMM yyyy"))
        first = QtCore.QDate(self._disp.year(), self._disp.month(), 1)
        start = first.dayOfWeek() % 7
        dim = first.daysInMonth()
        today = QtCore.QDate.currentDate()
        # gate month nav so the user can't wander into all-empty months
        self._prev.setEnabled(self._month_has_data(first.addMonths(-1)))
        self._nxt.setEnabled(self._month_has_data(first.addMonths(1)))
        for i, b in enumerate(self._day_btns):
            day = i - start + 1
            if day < 1 or day > dim:
                b.setText(""); b.setEnabled(False); b.setVisible(False); continue
            d = QtCore.QDate(self._disp.year(), self._disp.month(), day)
            b.setText(str(day)); b.setVisible(True)
            if not self._in_range(d):                    # no data -> faint + not clickable, no hover
                b.setEnabled(False)
                b.setStyleSheet("QPushButton{background:transparent; color:%s; border:none; border-radius:17px;"
                                "font-size:13px; font-weight:bold;}" % FAINT)
                continue
            b.setEnabled(True)
            if d == self._sel:                           # selected -> filled accent circle
                css = ("background:%s; color:#ffffff; border:none;" % ACCENT)
            elif d == today:                             # today -> accent ring
                css = ("background:transparent; color:%s; border:1.5px solid %s;" % (ACCENT, ACCENT))
            else:
                css = ("background:transparent; color:%s; border:none;" % TEXT)
            b.setStyleSheet("QPushButton{%s border-radius:17px; font-size:13px; font-weight:bold;}"
                            "QPushButton:hover{background:%s;}" % (css, CARD if d != self._sel else ACCENT_DK))

    def selected_datetime(self) -> QtCore.QDateTime:
        return QtCore.QDateTime(self._sel, QtCore.QTime(self.sp_h.value(), self.sp_m.value()))

    # ---- draggable frameless header ----
    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton and self._hdr.geometry().contains(self._hdr.mapFrom(self, e.position().toPoint())):
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag is not None and (e.buttons() & QtCore.Qt.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag = None
        super().mouseReleaseEvent(e)

    @classmethod
    def get(cls, initial: QtCore.QDateTime, parent=None,
            min_date: "Optional[QtCore.QDate]" = None,
            max_date: "Optional[QtCore.QDate]" = None) -> Optional[QtCore.QDateTime]:
        dlg = cls(initial, parent, min_date=min_date, max_date=max_date)
        dlg.adjustSize()
        if parent is not None:                           # center on the parent window
            pg = parent.window().frameGeometry()
            dlg.move(pg.center() - dlg.rect().center())
        return dlg.selected_datetime() if dlg.exec() == QtWidgets.QDialog.Accepted else None


class DateTimeField(QtWidgets.QWidget):
    """Drop-in for QDateTimeEdit: a styled field that opens the FlutterDateTimePicker on click.
    Exposes ``dateTime()`` / ``setDateTime()`` and a ``dateTimeChanged`` signal (blockSignals-aware)."""

    dateTimeChanged = QtCore.Signal(QtCore.QDateTime)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dt = QtCore.QDateTime.currentDateTime()
        self._range_provider = None            # callable -> (min QDate, max QDate); set by the terminal
        lay = QtWidgets.QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        self._btn = QtWidgets.QPushButton(self)
        self._btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._btn.setStyleSheet(
            "QPushButton{background:#11131a; color:%s; border:1px solid #2a2e39; border-radius:5px;"
            "padding:6px 10px; text-align:left; font-family:Consolas; font-size:11px;}"
            "QPushButton:hover{border-color:%s;}" % (TEXT, ACCENT))
        self._btn.clicked.connect(self._open)
        lay.addWidget(self._btn)
        self._render()

    def dateTime(self) -> QtCore.QDateTime:
        return QtCore.QDateTime(self._dt)

    def setDateTime(self, dt: QtCore.QDateTime) -> None:
        dt = QtCore.QDateTime(dt)
        changed = dt != self._dt
        self._dt = dt
        self._render()
        if changed:
            self.dateTimeChanged.emit(QtCore.QDateTime(self._dt))

    def _render(self) -> None:
        self._btn.setText("\U0001F5D3  " + self._dt.toString("yyyy-MM-dd  HH:mm"))   # 🗓 prefix

    def set_range_provider(self, fn) -> None:
        """Register a callable returning ``(min QDate, max QDate)`` — the picker disables days outside it so
        the user can't pick a date with no data. Either bound may be ``None`` (unbounded on that side)."""
        self._range_provider = fn

    def _open(self) -> None:
        min_d = max_d = None
        if self._range_provider is not None:
            try:
                min_d, max_d = self._range_provider()
            except Exception:
                min_d = max_d = None
        picked = FlutterDateTimePicker.get(self._dt, self.window(), min_date=min_d, max_date=max_d)
        if picked is not None:
            self.setDateTime(picked)
