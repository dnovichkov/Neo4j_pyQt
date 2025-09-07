# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all
import os

config_file = "config.json"

# Собираем ресурсы PyQt5
pyqt5_datas, pyqt5_binaries, pyqt5_hiddenimports = collect_all("PyQt5")

# Путь к шаблонам pyvis
import pyvis
pyvis_templates = os.path.join(os.path.dirname(pyvis.__file__), "templates")

datas = [
    (str(config_file), "."),  # config.json в корень exe
    (pyvis_templates, "pyvis/templates")  # шаблоны pyvis
] + pyqt5_datas

binaries = pyqt5_binaries

hiddenimports = [
    "win32ctypes.pywin32",
    "win32ctypes.pywin32.pywintypes",
    "win32ctypes.pywin32.win32api",
    "PyQt5.QtWebEngineWidgets",
    "PyQt5.QtWebEngineCore",
    "PyQt5.QtWebChannel"
]

block_cipher = None

a = Analysis(
    [
        'main.py',
        'dialogs.py',
        'log_utils.py',
        'neo_4j_client.py',
        'property_editor.py',
    ],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="Neo4jPyQtApp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # False чтобы окно без консоли
    icon=None,
)
