"""Main window with sidebar navigation and stacked pages."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from gui.pages.library_page import LibraryPage
from gui.pages.results_page import ResultsPage
from gui.pages.settings_page import SettingsPage
from gui.pages.setup_page import SetupPage
from gui.workers.cli_worker import CliWorker
from settings import load_settings


class MainWindow(QMainWindow):
    PAGE_SETUP = 0
    PAGE_LIBRARY = 1
    PAGE_RESULTS = 2
    PAGE_SETTINGS = 3

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Macro Review")
        self.resize(1100, 720)

        self._startup_worker = CliWorker(self)
        self._nav_buttons: dict[int, QPushButton] = {}

        self.setup_page = SetupPage()
        self.library_page = LibraryPage()
        self.results_page = ResultsPage()
        self.settings_page = SettingsPage()

        self.stack = QStackedWidget()
        self.stack.setObjectName("ContentHost")
        self.stack.addWidget(self.setup_page)
        self.stack.addWidget(self.library_page)
        self.stack.addWidget(self.results_page)
        self.stack.addWidget(self.settings_page)

        sidebar = self._build_sidebar()
        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(sidebar)
        body_layout.addWidget(self.stack, 1)
        self.setCentralWidget(body)

        status = QStatusBar()
        self.setStatusBar(status)
        self._status_label = QLabel("Checking readiness…")
        status.addWidget(self._status_label)

        self.setup_page.readiness_changed.connect(self._on_readiness)
        self.settings_page.readiness_changed.connect(self._on_readiness)
        self.library_page.run_finished.connect(self._on_library_run_finished)
        self._startup_worker.json_finished.connect(self._on_startup_doctor)
        self._startup_worker.failed.connect(self._on_startup_failed)
        self._startup_worker.finished.connect(self._on_startup_finished)

        self.show_page(self.PAGE_SETUP)
        self._start_readiness_check()

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(8, 12, 8, 12)
        layout.setSpacing(4)

        brand = QLabel("Macro Review")
        brand.setObjectName("BrandLabel")
        layout.addWidget(brand)

        group = QButtonGroup(self)
        group.setExclusive(True)
        for index, label in (
            (self.PAGE_SETUP, "Setup"),
            (self.PAGE_LIBRARY, "Library"),
            (self.PAGE_RESULTS, "Results"),
            (self.PAGE_SETTINGS, "Settings"),
        ):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked=False, i=index: self.show_page(i))
            group.addButton(btn)
            self._nav_buttons[index] = btn
            layout.addWidget(btn)

        layout.addStretch(1)
        return sidebar

    def show_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for i, btn in self._nav_buttons.items():
            btn.setChecked(i == index)
        if index == self.PAGE_LIBRARY:
            self.library_page.refresh()
        if index == self.PAGE_RESULTS:
            self.results_page.refresh()
        if index == self.PAGE_SETTINGS:
            self.settings_page.reload()

    def _on_library_run_finished(self, ok: bool) -> None:
        self.results_page.refresh()
        if ok:
            self.statusBar().showMessage("Run finished — Results updated", 6000)

    def _start_readiness_check(self) -> None:
        settings = load_settings()
        pipeline = (settings.pipeline_python or "").strip()
        use_pipeline = bool(pipeline) and Path(pipeline).is_file()
        self._startup_worker.run_doctor(pipeline=use_pipeline)

    def _on_readiness(self, ready: bool, message: str) -> None:
        self._status_label.setText("Ready" if ready else "Setup needed")
        if message and message not in {"Ready", "Setup needed"}:
            self.statusBar().showMessage(message, 5000)

    def _on_startup_doctor(self, payload: dict) -> None:
        recs = payload.get("recommendations") or {}
        ready = bool(recs.get("ready_for_pipeline"))
        self._on_readiness(ready, "Ready" if ready else "Setup needed")
        self.setup_page.apply_doctor_payload(payload)
        if ready:
            self.show_page(self.PAGE_LIBRARY)
        else:
            self.show_page(self.PAGE_SETUP)

    def _on_startup_failed(self, message: str) -> None:
        self._status_label.setText("Setup needed")
        self.statusBar().showMessage(message, 8000)
        self.show_page(self.PAGE_SETUP)

    def _on_startup_finished(self, code: int) -> None:
        if code != 0 and self._status_label.text() == "Checking readiness…":
            self._status_label.setText("Setup needed")
            self.show_page(self.PAGE_SETUP)
