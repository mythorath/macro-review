"""Main window with sidebar navigation and stacked pages."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
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
from gui.updates import UpdateCheckWorker, UpdateInfo
from gui.workers.cli_worker import CliWorker
from settings import load_settings
from version_info import load_build_info


class MainWindow(QMainWindow):
    PAGE_SETUP = 0
    PAGE_LIBRARY = 1
    PAGE_RESULTS = 2
    PAGE_SETTINGS = 3

    def __init__(self, *, skip_startup_doctor: bool = False) -> None:
        super().__init__()
        self.setWindowTitle("Macro Review")
        self.resize(1100, 720)

        self._startup_worker = CliWorker(self)
        self._nav_buttons: dict[int, QPushButton] = {}
        self._update_worker = None
        self._pending_update_url = ""

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
        self._update_btn = QPushButton("Update available")
        self._update_btn.setObjectName("SecondaryButton")
        self._update_btn.setVisible(False)
        self._update_btn.clicked.connect(self._open_update_url)
        status.addPermanentWidget(self._update_btn)

        self.setup_page.readiness_changed.connect(self._on_readiness)
        self.settings_page.readiness_changed.connect(self._on_readiness)
        self.settings_page.check_updates_requested.connect(self._check_updates_manual)
        self.library_page.run_finished.connect(self._on_library_run_finished)
        self._startup_worker.json_finished.connect(self._on_startup_doctor)
        self._startup_worker.failed.connect(self._on_startup_failed)
        self._startup_worker.finished.connect(self._on_startup_finished)

        self.show_page(self.PAGE_SETUP)
        if skip_startup_doctor:
            self._status_label.setText("Smoke mode")
        else:
            self._start_readiness_check()
            self._check_updates_auto()

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

    def _check_updates_auto(self) -> None:
        settings = load_settings()
        if not settings.check_updates:
            return
        self._start_update_check(manual=False)

    def _check_updates_manual(self) -> None:
        self.statusBar().showMessage("Checking for updates…", 4000)
        self._start_update_check(manual=True)

    def _start_update_check(self, *, manual: bool) -> None:
        if self._update_worker is not None and self._update_worker.isRunning():
            return
        worker = UpdateCheckWorker(manual=manual, parent=self)
        self._update_worker = worker
        worker.finished_info.connect(self._on_update_info)
        worker.failed.connect(self._on_update_failed)
        worker.start()

    def _on_update_info(self, info: object) -> None:
        if not isinstance(info, UpdateInfo):
            return
        if not info.available:
            if info.manual:
                self.statusBar().showMessage("You're on the latest build.", 5000)
            self._update_btn.setVisible(False)
            self._pending_update_url = ""
            return
        self._pending_update_url = info.html_url
        label = f"Update {info.tag_name}" if info.tag_name else "Update available"
        self._update_btn.setText(label)
        self._update_btn.setVisible(True)
        self.statusBar().showMessage(
            f"{label} — click to open the download page",
            8000 if info.manual else 0,
        )

    def _on_update_failed(self, message: str, manual: bool) -> None:
        if manual:
            self.statusBar().showMessage(message, 6000)

    def _open_update_url(self) -> None:
        url = getattr(self, "_pending_update_url", "") or ""
        if not url:
            info = load_build_info()
            url = f"https://github.com/{info.repo}/releases"
        QDesktopServices.openUrl(QUrl(url))
