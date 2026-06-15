# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:\\Users\\Yassine Mdouari\\Desktop\\Coding\\12. Trading Indicators\\smc_quant_legacy\\run_daemon.py'],
    pathex=['C:\\Users\\Yassine Mdouari\\Desktop\\Coding\\12. Trading Indicators\\smc_quant_legacy'],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'pyqtgraph', 'PyInstaller'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='OrderFlowDaemon',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='OrderFlowDaemon',
)
