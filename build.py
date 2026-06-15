"""Build the daemon + terminal into standalone desktop executables.

    python build.py                 # build both (one-file)
    python build.py --onedir        # faster-starting one-folder bundles
    python build.py --daemon-only   # only the headless core
    python build.py --terminal-only # only the GUI client

Output lands in ``dist/`` :
    dist/OrderFlowDaemon[.exe]    — headless data core (console, shows logs)
    dist/OrderFlowTerminal[.exe]  — GUI client (windowed, no console)

Notes
-----
* The alternate Qt bindings (PyQt5/PyQt6/PySide2) are explicitly excluded so the
  frozen app uses PySide6 only — matching the ``PYQTGRAPH_QT_LIB`` pin in
  ``app/__init__.py``. Bundling two bindings bloats the build and breaks imports.
* ``app/__init__.py`` sets the binding env var at import time, so it works frozen.
* User data (``data/server_footprints.json``, ``data/drawings.json``) is written
  next to the executable at runtime (see frozen-path logic in ``config.py``); it
  is intentionally NOT bundled.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

try:
    import PyInstaller.__main__ as pyi
except ImportError:
    sys.exit("PyInstaller is not installed. Run:  pip install -r requirements.txt")

ROOT = Path(__file__).resolve().parent
EXCLUDES = ["PyQt5", "PyQt6", "PySide2"]
# pyqtgraph dynamically imports these; PyInstaller can't see them statically.
#   QtOpenGL / QtOpenGLWidgets — pyqtgraph's accelerated curve rendering path
#   QtMultimedia               — the optional audio feed (alerts chime)
#   QtSvg                      — pyqtgraph icon/export assets
HIDDEN = [
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtMultimedia",
    "PySide6.QtSvg",
]


def _common(name: str, onefile: bool) -> list[str]:
    args = [
        "--noconfirm", "--clean",
        "--name", name,
        "--paths", str(ROOT),
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT),
    ]
    args += ["--onefile"] if onefile else ["--onedir"]
    for e in EXCLUDES:
        args += ["--exclude-module", e]
    return args


def build_daemon(onefile: bool) -> None:
    # The daemon imports NO Qt/pyqtgraph — exclude them so the headless core
    # stays lean (otherwise pyqtgraph drags all of PySide6 into it).
    print("\n=== Building OrderFlowDaemon (console) ===")
    args = _common("OrderFlowDaemon", onefile)
    for mod in ("PySide6", "pyqtgraph", "PyInstaller"):
        args += ["--exclude-module", mod]
    pyi.run(args + ["--console", str(ROOT / "run_daemon.py")])


def build_terminal(onefile: bool) -> None:
    print("\n=== Building OrderFlowTerminal (windowed) ===")
    args = _common("OrderFlowTerminal", onefile) + ["--collect-all", "pyqtgraph"]
    for h in HIDDEN:
        args += ["--hidden-import", h]
    pyi.run(args + ["--windowed", str(ROOT / "run_terminal.py")])


def main() -> None:
    ap = argparse.ArgumentParser(description="Compile the order flow terminal.")
    ap.add_argument("--onedir", action="store_true", help="one-folder bundle (faster start)")
    ap.add_argument("--daemon-only", action="store_true")
    ap.add_argument("--terminal-only", action="store_true")
    args = ap.parse_args()
    onefile = not args.onedir

    if not args.terminal_only:
        build_daemon(onefile)
    if not args.daemon_only:
        build_terminal(onefile)

    print("\nBuild complete. Executables are in:", ROOT / "dist")


if __name__ == "__main__":
    main()
