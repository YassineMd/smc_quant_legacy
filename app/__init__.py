"""Decoupled multi-window order flow terminal.

Two entry points share one quant core:
    python -m app.daemon     # headless data core, TCP loopback :9999
    python -m app.terminal   # PySide6 visualization client (spawn N copies)
"""

import os

# Pin pyqtgraph to PySide6 BEFORE any submodule imports it. Multiple Qt bindings
# (PyQt5/PyQt6/PySide6) may be installed; without this pyqtgraph auto-picks one
# that mismatches our PySide6 widgets and QMainWindow.setCentralWidget rejects it.
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
os.environ.setdefault("QT_API", "pyside6")

__version__ = "1.0.0"
