# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Macro Review portable GUI (one-folder)."""

from __future__ import annotations

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

SPECDIR = Path(SPEC).resolve().parent
ROOT = SPECDIR.parent
ENTRY = SPECDIR / "entry.py"

datas = [
    (str(ROOT / "gui" / "style.qss"), "gui"),
]
build_info = ROOT / "build_info.json"
if build_info.is_file():
    datas.append((str(build_info), "."))

# Qt platform / style plugins only — avoid collect_all(PySide6) which pulls WebEngine etc.
datas += collect_data_files("PySide6", includes=["plugins/platforms/*", "plugins/styles/*", "plugins/imageformats/*"])
binaries = collect_dynamic_libs("PySide6")
binaries += collect_dynamic_libs("shiboken6")

icon_path = ROOT / "assets" / "macroreview.ico"
icon = str(icon_path) if icon_path.is_file() else None

hiddenimports = [
    "bootstrap",
    "config",
    "db",
    "hardware",
    "paths",
    "progress",
    "python_discover",
    "report",
    "settings",
    "setup_env",
    "version_info",
    "gui",
    "gui.app",
    "gui.main_window",
    "gui.updates",
    "gui.pipeline_exe",
    "gui.pages.setup_page",
    "gui.pages.library_page",
    "gui.pages.results_page",
    "gui.pages.settings_page",
    "gui.results.model",
    "gui.results.delegate",
    "gui.results.detail_pane",
    "gui.results.style",
    "gui.widgets.progress_panel",
    "gui.widgets.collapsible",
    "gui.workers.cli_worker",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtNetwork",
    "requests",
    "tqdm",
    "urllib3",
    "certifi",
    "charset_normalizer",
    "idna",
]

a = Analysis(
    [str(ENTRY)],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "torch",
        "torchvision",
        "torchaudio",
        "pyiqa",
        "cv2",
        "rawpy",
        "openai",
        "ImageHash",
        "pandas",
        "scipy",
        "sklearn",
        "matplotlib",
        "PIL",
        "numpy",
        "pyarrow",
        "IPython",
        "notebook",
        "pytest",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineQuick",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DRender",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.QtBluetooth",
        "PySide6.QtPositioning",
        "PySide6.QtLocation",
        "PySide6.QtPdf",
        "PySide6.QtPdfWidgets",
        "PySide6.QtQuick",
        "PySide6.QtQuickWidgets",
        "PySide6.QtQml",
        "tkinter",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MacroReview",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MacroReview",
)
