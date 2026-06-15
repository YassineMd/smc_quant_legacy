"""Tier-3 indestructible margin HUD (spec §6).

A native ``QWidget`` stack — live price over candle-expiry countdown — pinned to
the right price-axis margin. It is a TOP-LEVEL child of the QMainWindow, NOT a
PyQtGraph scene item, so the canvas-reset cycle that fires on candle rollover
can never drop it (spec §6.1.2). The owning window repositions it every 50ms
frame using ``mapViewToScene`` so it tracks the live price-line pixel row
(spec §6.1.1).
"""

from __future__ import annotations

import time
from typing import Optional

from PySide6 import QtCore, QtWidgets

from . import config

_HUD_QSS = """
QWidget {
  background-color: #000000;
  color: #ffffff;
  font-family: "Consolas", "Courier New", monospace;
  font-weight: bold;
  font-size: 12px;
  padding: 6px 8px;
  border: 1px solid #000000;
  border-radius: 2px;
}
"""


class PriceHud(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget):
        super().__init__(parent)
        self.setStyleSheet(_HUD_QSS)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(0)

        self.price_lbl = QtWidgets.QLabel("--.--")
        self.timer_lbl = QtWidgets.QLabel("--:--")
        for lbl in (self.price_lbl, self.timer_lbl):
            lbl.setAlignment(QtCore.Qt.AlignCenter)  # kill digit jitter (spec §6.3.1)
            layout.addWidget(lbl)

        self.adjustSize()

    # ------------------------------------------------------------------
    def update_values(self, latest_price: float, forming_time: Optional[int], tf: str) -> None:
        """Refresh the two text rows (spec §6.2)."""
        if latest_price:
            self.price_lbl.setText(f"{latest_price:.{config.PRICE_DECIMALS}f}")

        if forming_time is not None:
            tf_secs = config.TF_SECONDS.get(tf, 60)
            remaining = int(forming_time + tf_secs - time.time())
            remaining = max(0, remaining)
            if tf_secs >= 3600:
                h, rem = divmod(remaining, 3600)
                m, s = divmod(rem, 60)
                self.timer_lbl.setText(f"{h:02d}:{m:02d}:{s:02d}")
            else:
                m, s = divmod(remaining, 60)
                self.timer_lbl.setText(f"{m:02d}:{s:02d}")
        self.adjustSize()
