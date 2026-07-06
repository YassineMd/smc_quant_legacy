"""Tier-3 alerts ledger + audio hooks (spec §8.4, §7.4).

A floating scrolling ledger that aggregates four color-coded institutional event
feeds, plus a low-latency audio engine. The window calls :meth:`AlertsLedger.feed`
each frame with a snapshot; the ledger diffs against what it has already logged
and appends only genuinely new events, time-stamped to the second.

    Bullish OB ignition  -> green   (#27ae60)
    Bearish OB ignition  -> red     (#e74c3c)
    Short liquidated/Buy  -> cyan    (#00ffff)
    Long  liquidated/Sell -> magenta (#ff00ff)
"""

from __future__ import annotations

import time
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from . import config

try:
    from PySide6.QtMultimedia import QSoundEffect
    _HAS_AUDIO = True
except Exception:  # QtMultimedia not present in this build
    _HAS_AUDIO = False


class AudioEngine:
    """Wraps QSoundEffect; silently no-ops if audio is unavailable (spec §7.4.1)."""

    def __init__(self):
        self.armed = True          # Audio Feed ON by default (the menu toggle starts checked to match)
        self._effect = None
        if _HAS_AUDIO:
            import os
            path = os.path.join(config.PROJECT_DIR, "notif.mp3")
            if os.path.exists(path):
                self._effect = QSoundEffect()
                self._effect.setSource(QtCore.QUrl.fromLocalFile(path))
                self._effect.setVolume(0.6)
        # Text-to-speech for spoken alerts (QtTextToSpeech; native SAPI/WinRT on Windows).
        self._tts = None
        try:
            from PySide6.QtTextToSpeech import QTextToSpeech
            self._tts = QTextToSpeech()
        except Exception:
            self._tts = None

    def set_armed(self, armed: bool) -> None:
        self.armed = armed

    def play(self) -> None:
        if self.armed and self._effect is not None:
            self._effect.play()

    def speak(self, text: str, gated: bool = True) -> None:
        """Speak ``text`` aloud. gated=True honours the master Audio Feed arm (OB/Iceberg); gated=False speaks
        regardless — for feeds with their OWN toggle (the Pivot alert)."""
        if (self.armed or not gated) and self._tts is not None:
            self._tts.say(text)


_QSS = """
QFrame#alerts { background-color: rgba(16,18,24,0.97); border:1px solid #2a2e39; }
QLabel#title { color:#fff; font-weight:bold; font-family:Consolas; padding:6px; }
QListWidget { background:#0c0e12; color:#ddd; font-family:Consolas; font-size:11px; border:none; }
"""


class AlertsLedger(QtWidgets.QFrame):
    unreadChanged = QtCore.Signal(int)

    def __init__(self, parent: QtWidgets.QWidget):
        super().__init__(parent)
        self.setObjectName("alerts")
        self.setStyleSheet(_QSS)
        self.setFixedWidth(260)
        self.hide()

        self._seen_obs: set[str] = set()
        self._seen_liqs: set[tuple] = set()
        self._unread = 0
        self.audio = AudioEngine()

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        title = QtWidgets.QLabel("Institutional Alerts")
        title.setObjectName("title")
        lay.addWidget(title)
        self.list = QtWidgets.QListWidget()
        lay.addWidget(self.list)

    # ------------------------------------------------------------------
    def _add(self, text: str, color: str) -> None:
        ts = time.strftime("%H:%M:%S")
        item = QtWidgets.QListWidgetItem(f"{ts}  {text}")
        item.setForeground(QtGui.QColor(color))
        self.list.insertItem(0, item)
        while self.list.count() > 300:
            self.list.takeItem(self.list.count() - 1)
        if not self.isVisible():
            self._unread += 1
            self.unreadChanged.emit(self._unread)

    def feed(self, snap: dict) -> None:
        for ob in snap.get("order_blocks", []):
            oid = ob.get("ob_id")
            if not oid or oid in self._seen_obs:
                continue
            self._seen_obs.add(oid)
            if ob.get("type") == "bullish":
                self._add(f"🟢 Bull OB  {ob['bottom']:.2f}-{ob['top']:.2f}  x{ob.get('vol_mult',0):.1f}",
                          config.COLOR_ALERT_BULL_OB)
            else:
                self._add(f"🔴 Bear OB  {ob['bottom']:.2f}-{ob['top']:.2f}  x{ob.get('vol_mult',0):.1f}",
                          config.COLOR_ALERT_BEAR_OB)
            if ob.get("power_score", 0) > 300:
                self.audio.play()

        for lq in snap.get("liquidations", []):
            key = (lq["time"], lq["price"], lq["qty"])
            if key in self._seen_liqs:
                continue
            self._seen_liqs.add(key)
            if lq["side"] == "BUY":  # shorts liquidated -> forced buys
                self._add(f"⚡ Short Liq  {lq['qty']:.0f} @ {lq['price']:.2f}",
                          config.COLOR_ALERT_SHORT_LIQ)
            else:                    # longs liquidated -> forced sells
                self._add(f"⚡ Long Liq  {lq['qty']:.0f} @ {lq['price']:.2f}",
                          config.COLOR_ALERT_LONG_LIQ)
            if lq["qty"] >= 1000:
                self.audio.play()
        if len(self._seen_liqs) > 2000:
            self._seen_liqs = set(list(self._seen_liqs)[-1000:])

    def toggle(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self._unread = 0
            self.unreadChanged.emit(0)
            self.show(); self.raise_()
