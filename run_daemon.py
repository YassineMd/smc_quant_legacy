"""Frozen entry point for the headless data core.

PyInstaller targets a script file, not a ``-m`` module, so this thin launcher
exists purely to give the daemon a build target. Run directly during dev with:
    python run_daemon.py
"""

from app.daemon import main

if __name__ == "__main__":
    main()
