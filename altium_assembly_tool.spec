# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for the Altium Assembly Tool.

Build with:
    .venv\\Scripts\\pyinstaller.exe altium_assembly_tool.spec

The result is a single folder  dist\\AltiumAssemblyTool\\  containing the
.exe and all dependencies.  Copy the entire folder to a flash drive.
"""
import os, sys
from pathlib import Path

block_cipher = None
ROOT = os.path.abspath('.')

a = Analysis(
    ['main.py'],
    pathex=[ROOT],
    binaries=[],
    datas=[
        # Bundle data directories so app_paths.app_dir() / "web" etc. work
        (os.path.join(ROOT, 'web'),    'web'),
        (os.path.join(ROOT, 'assets'), 'assets'),
    ],
    hiddenimports=[
        # Flask and its template engine are imported lazily
        'flask',
        'jinja2',
        'jinja2.ext',
    ],
    hookspath=['hooks'],  # picks up hooks/hook-altium_monkey.py
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim unused PySide6 modules to reduce bundle size
        'PySide6.Qt3DAnimation',
        'PySide6.Qt3DCore',
        'PySide6.Qt3DExtras',
        'PySide6.Qt3DInput',
        'PySide6.Qt3DLogic',
        'PySide6.Qt3DRender',
        'PySide6.QtBluetooth',
        'PySide6.QtCharts',
        'PySide6.QtDataVisualization',
        'PySide6.QtDesigner',
        'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets',
        'PySide6.QtNfc',
        'PySide6.QtPositioning',
        'PySide6.QtQuick',
        'PySide6.QtQuick3D',
        'PySide6.QtQuickWidgets',
        'PySide6.QtRemoteObjects',
        'PySide6.QtSensors',
        'PySide6.QtSerialPort',
        'PySide6.QtTest',
        'PySide6.QtWebChannel',
        'PySide6.QtWebEngine',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebSockets',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,          # --onedir mode (faster startup)
    name='AltiumAssemblyTool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,                  # No console window — it's a GUI app
    disable_windowed_traceback=False,
    icon=os.path.join(ROOT, 'assets', 'app_icon.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AltiumAssemblyTool',
)
