"""Library page — folder pick, drag-drop, and pipeline run controls."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from gui.pipeline_exe import managed_python
from gui.widgets.collapsible import bind_collapsible
from gui.widgets.progress_panel import ProgressPanel
from gui.workers.cli_worker import CliWorker
from settings import LibraryEntry, load_settings, save_settings


class LibraryPage(QWidget):
    run_finished = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._worker = CliWorker(self)
        self._pipeline_ready = False
        self._running = False

        self.banner = QLabel("")
        self.banner.setWordWrap(True)
        self.banner.setObjectName("PageSubtitle")

        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("Drop a folder here or browse…")
        self.browse_btn = QPushButton("Browse…")
        self.browse_btn.setObjectName("SecondaryButton")
        self.save_lib_btn = QPushButton("Save folder to libraries")
        self.save_lib_btn.setObjectName("SecondaryButton")

        self.libraries = QListWidget()
        self.libraries.setMinimumHeight(100)

        self.recursive = QCheckBox("Include subfolders")
        self.force = QCheckBox("Force reprocess (ignore resume)")
        self.limit_enabled = QCheckBox("Limit images")
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(1, 100000)
        self.limit_spin.setValue(10)
        self.limit_spin.setEnabled(False)
        self.limit_note = QLabel(
            "Limit re-scores mid-stages for the capped set — omit it to resume cleanly."
        )
        self.limit_note.setObjectName("PageSubtitle")
        self.limit_note.setWordWrap(True)

        self.start_btn = QPushButton("Start")
        self.continue_btn = QPushButton("Continue")
        self.continue_btn.setObjectName("SecondaryButton")
        self.continue_btn.setToolTip("Resume unfinished work in this folder")
        self.open_report_btn = QPushButton("Open report.html")
        self.open_report_btn.setObjectName("SecondaryButton")
        self.open_report_btn.setEnabled(False)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.progress = ProgressPanel()

        self.browse_btn.clicked.connect(self._browse)
        self.save_lib_btn.clicked.connect(self._save_to_libraries)
        self.libraries.itemClicked.connect(self._on_library_clicked)
        self.limit_enabled.toggled.connect(self.limit_spin.setEnabled)
        self.folder_edit.textChanged.connect(self._update_action_state)
        self.start_btn.clicked.connect(self._start_run)
        self.continue_btn.clicked.connect(self._continue_run)
        self.open_report_btn.clicked.connect(self._open_report)
        self.progress.cancel_requested.connect(self._worker.cancel)

        self._worker.line_received.connect(self.progress.handle_event)
        self._worker.text_received.connect(self.progress.append_text)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_finished)

        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        title = QLabel("Library")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "Choose a shoot folder, then Start the pipeline. Continue resumes unfinished work."
        )
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)

        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(self.browse_btn)

        folder_box = QGroupBox("Folder")
        folder_layout = QVBoxLayout(folder_box)
        folder_layout.addLayout(folder_row)
        folder_layout.addWidget(self.save_lib_btn)

        libs_box = QGroupBox("Saved libraries")
        libs_layout = QVBoxLayout(libs_box)
        libs_layout.addWidget(self.libraries)

        options = QGroupBox("Options")
        options_layout = QVBoxLayout(options)
        options_layout.addWidget(self.recursive)

        advanced = QGroupBox("Advanced")
        advanced.setCheckable(True)
        advanced.setChecked(False)
        adv_form = QFormLayout(advanced)
        limit_row = QHBoxLayout()
        limit_row.addWidget(self.limit_enabled)
        limit_row.addWidget(self.limit_spin)
        limit_row.addStretch(1)
        adv_form.addRow(limit_row)
        adv_form.addRow(self.limit_note)
        adv_form.addRow(self.force)
        bind_collapsible(advanced)

        controls = QHBoxLayout()
        controls.addWidget(self.start_btn)
        controls.addWidget(self.continue_btn)
        controls.addWidget(self.open_report_btn)
        controls.addStretch(1)

        run_box = QGroupBox("Run")
        run_layout = QVBoxLayout(run_box)
        run_layout.addLayout(controls)
        run_layout.addWidget(self.progress)
        run_layout.addWidget(self.status)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(self.banner)
        layout.addWidget(folder_box)
        layout.addWidget(libs_box)
        layout.addWidget(options)
        layout.addWidget(advanced)
        layout.addWidget(run_box)
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(inner)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

    def refresh(self) -> None:
        settings = load_settings()
        self.recursive.setChecked(bool(settings.recursive_default))
        self.libraries.clear()
        for lib in settings.libraries:
            item = QListWidgetItem(f"{lib.name}  —  {lib.path}")
            item.setData(Qt.UserRole, lib.path)
            self.libraries.addItem(item)

        exe = managed_python()
        self._pipeline_ready = exe is not None
        if self._pipeline_ready:
            self.banner.setText(f"Pipeline Python: {exe}")
        else:
            self.banner.setText(
                "Managed pipeline Python is not set. Open Setup and run setup before starting a review."
            )

        self._refresh_report_button()
        self._update_action_state()

    def _active_folder(self) -> str:
        return self.folder_edit.text().strip()

    def _update_action_state(self) -> None:
        folder = self._active_folder()
        ok_folder = bool(folder) and Path(folder).is_dir()
        busy = self._running or self._worker.busy
        can_run = self._pipeline_ready and ok_folder and not busy
        self.start_btn.setEnabled(can_run)
        self.continue_btn.setEnabled(can_run)
        self.save_lib_btn.setEnabled(ok_folder and not busy)
        self.browse_btn.setEnabled(not busy)
        self.folder_edit.setEnabled(not busy)
        self.libraries.setEnabled(not busy)
        self.progress.set_running(busy)

    def _browse(self) -> None:
        start = self._active_folder() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Choose photo folder", start)
        if chosen:
            self.folder_edit.setText(chosen)

    def _on_library_clicked(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.UserRole)
        if path:
            self.folder_edit.setText(str(path))

    def _save_to_libraries(self) -> None:
        folder = self._active_folder()
        path = Path(folder)
        if not path.is_dir():
            self.status.setText("Choose a valid folder first.")
            return
        settings = load_settings()
        resolved = str(path.resolve()) if path.exists() else str(path)
        name = path.name or "library"
        updated = False
        for lib in settings.libraries:
            if Path(lib.path).as_posix().lower() == Path(resolved).as_posix().lower():
                lib.name = name
                lib.path = resolved
                updated = True
                break
        if not updated:
            settings.libraries.append(LibraryEntry(name=name, path=resolved))
        save_settings(settings)
        self.status.setText(f"Saved library: {name}")
        self.refresh()
        self.folder_edit.setText(resolved)

    def _parse_limit(self) -> int | None:
        if self.limit_enabled.isChecked():
            return int(self.limit_spin.value())
        return None

    def _start_run(self) -> None:
        self._launch(resume=False)

    def _continue_run(self) -> None:
        self._launch(resume=True)

    def _launch(self, *, resume: bool) -> None:
        if self._worker.busy:
            return
        folder = self._active_folder()
        if not folder or not Path(folder).is_dir():
            self.status.setText("Choose a valid folder first.")
            return
        if managed_python() is None:
            self.status.setText("Run setup first (pipeline Python missing).")
            self._update_action_state()
            return

        limit = None if resume else self._parse_limit()
        force = False if resume else self.force.isChecked()
        self._running = True
        self.progress.reset(stages=[])
        self.status.setText(
            "Resuming unfinished work…"
            if resume
            else "Running pipeline — cancel is safe; use Continue to resume."
        )
        self._update_action_state()
        self._worker.run_pipeline(
            folder,
            recursive=self.recursive.isChecked(),
            limit=limit,
            force=force,
        )

    def _on_failed(self, message: str) -> None:
        self.status.setText(message)
        self.progress.append_text(f"[error] {message}")
        if not self._worker.busy:
            self._running = False
            self._update_action_state()

    def _on_finished(self, code: int) -> None:
        self._running = False
        ok = code == 0
        if ok:
            report = self._report_path()
            self.status.setText(f"Done. Report: {report}")
            self._refresh_report_button()
        else:
            self.status.setText(
                f"Run exited with code {code}. Partial work is kept — use Continue to resume."
            )
        self._update_action_state()
        self.run_finished.emit(ok)

    def _report_path(self) -> Path:
        settings = load_settings()
        return Path(settings.data_dir) / "report.html"

    def _refresh_report_button(self) -> None:
        report = self._report_path()
        exists = report.is_file()
        self.open_report_btn.setEnabled(exists)
        self.open_report_btn.setToolTip(str(report) if exists else "No report.html yet.")

    def _open_report(self) -> None:
        report = self._report_path()
        if report.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(report)))

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile() and Path(url.toLocalFile()).is_dir():
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_dir():
                self.folder_edit.setText(str(path))
                event.acceptProposedAction()
                return
        event.ignore()
