"""QApplication bootstrap for Macro Review GUI."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from gui import REPO_ROOT
from gui.main_window import MainWindow


def _ensure_repo_on_path() -> None:
    root = str(REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def _load_stylesheet() -> str:
    path = Path(__file__).resolve().parent / "style.qss"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def run() -> int:
    _ensure_repo_on_path()
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("Macro Review")
    app.setOrganizationName("MacroReview")
    style = _load_stylesheet()
    if style:
        app.setStyleSheet(style)
    window = MainWindow()
    window.showMaximized()
    return app.exec()
