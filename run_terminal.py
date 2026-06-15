"""Frozen entry point for the GUI visualization client.

Thin launcher so PyInstaller has a script target for the terminal. Run directly
during dev with:
    python run_terminal.py
"""

from app.terminal import main

if __name__ == "__main__":
    main()
