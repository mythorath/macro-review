"""Settings page — edit knobs, re-run doctor/setup."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gui.widgets.progress_panel import ProgressPanel
from gui.workers.cli_worker import CliWorker
from settings import app_data_root, default_settings_path, load_settings, save_settings
from version_info import load_build_info


class SettingsPage(QWidget):
    readiness_changed = Signal(bool, str)
    check_updates_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._worker = CliWorker(self)
        self._mode = "idle"

        self.settings_path_label = QLabel("—")
        self.settings_path_label.setWordWrap(True)
        self.pipeline_python_label = QLabel("—")
        self.pipeline_python_label.setWordWrap(True)
        self.version_label = QLabel("—")
        self.version_label.setWordWrap(True)
        self.status = QLabel("")
        self.status.setWordWrap(True)

        self.data_dir = QLineEdit()
        self.backend = QComboBox()
        self.backend.addItems(["ollama", "openai"])
        self.vision_model = QLineEdit()
        self.ollama_host = QLineEdit()
        self.iqa_device = QComboBox()
        self.iqa_device.addItems(["cuda", "cpu"])
        self.qrealign_variant = QComboBox()
        self.qrealign_variant.addItems(["qrealign-lite", "qrealign-pro"])
        self.update_channel = QComboBox()
        self.update_channel.addItem("Stable", "stable")
        self.update_channel.addItem("Preview", "preview")
        self.check_updates = QCheckBox("Check for updates automatically")

        self.browse_btn = QPushButton("Browse…")
        self.browse_btn.setObjectName("SecondaryButton")
        self.save_btn = QPushButton("Save settings")
        self.doctor_btn = QPushButton("Re-run doctor")
        self.doctor_btn.setObjectName("SecondaryButton")
        self.setup_btn = QPushButton("Re-run setup")
        self.open_folder_btn = QPushButton("Open settings folder")
        self.open_folder_btn.setObjectName("SecondaryButton")
        self.updates_btn = QPushButton("Check for updates")
        self.updates_btn.setObjectName("SecondaryButton")

        self.progress = ProgressPanel()

        self.browse_btn.clicked.connect(self._browse_data_dir)
        self.save_btn.clicked.connect(self.save)
        self.doctor_btn.clicked.connect(self.run_doctor)
        self.setup_btn.clicked.connect(self.run_setup)
        self.open_folder_btn.clicked.connect(self._open_settings_folder)
        self.updates_btn.clicked.connect(self.check_updates_requested.emit)
        self.progress.cancel_requested.connect(self._worker.cancel)

        self._worker.line_received.connect(self.progress.handle_event)
        self._worker.text_received.connect(self.progress.append_text)
        self._worker.json_finished.connect(self._on_doctor_json)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_finished)

        self._build_ui()
        self.reload()

    def _build_ui(self) -> None:
        title = QLabel("Settings")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "Paths, model knobs, and update channel. Changes apply after Save."
        )
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)

        about = QGroupBox("About")
        about_form = QFormLayout(about)
        about_form.addRow("Version", self.version_label)

        paths = QGroupBox("Paths")
        paths_form = QFormLayout(paths)
        paths_form.addRow("Settings file", self.settings_path_label)
        paths_form.addRow("Pipeline Python", self.pipeline_python_label)
        data_row = QHBoxLayout()
        data_row.addWidget(self.data_dir, 1)
        data_row.addWidget(self.browse_btn)
        paths_form.addRow("Data dir", data_row)

        knobs = QGroupBox("Runtime")
        knobs_form = QFormLayout(knobs)
        knobs_form.addRow("Backend", self.backend)
        knobs_form.addRow("Vision model", self.vision_model)
        knobs_form.addRow("Ollama host", self.ollama_host)
        knobs_form.addRow("IQA device", self.iqa_device)
        knobs_form.addRow("QRealign variant", self.qrealign_variant)

        updates = QGroupBox("Updates")
        updates_form = QFormLayout(updates)
        updates_form.addRow("Channel", self.update_channel)
        updates_form.addRow(self.check_updates)
        updates_form.addRow(self.updates_btn)

        actions = QHBoxLayout()
        actions.addWidget(self.save_btn)
        actions.addWidget(self.doctor_btn)
        actions.addWidget(self.setup_btn)
        actions.addWidget(self.open_folder_btn)
        actions.addStretch(1)

        tools = QGroupBox("Tools")
        tools_layout = QVBoxLayout(tools)
        tools_layout.addLayout(actions)
        tools_layout.addWidget(self.progress)
        tools_layout.addWidget(self.status)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(about)
        layout.addWidget(paths)
        layout.addWidget(knobs)
        layout.addWidget(updates)
        layout.addWidget(tools)
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(inner)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

    def reload(self) -> None:
        s = load_settings()
        info = load_build_info()
        self.version_label.setText(info.display)
        self.settings_path_label.setText(str(default_settings_path()))
        self.pipeline_python_label.setText(s.pipeline_python or "(not set — run setup)")
        self.data_dir.setText(s.data_dir)
        idx = self.backend.findText(s.backend)
        if idx >= 0:
            self.backend.setCurrentIndex(idx)
        self.vision_model.setText(s.vision_model)
        self.ollama_host.setText(s.ollama_host)
        idx = self.iqa_device.findText(s.iqa_device)
        if idx >= 0:
            self.iqa_device.setCurrentIndex(idx)
        idx = self.qrealign_variant.findText(s.qrealign_variant)
        if idx >= 0:
            self.qrealign_variant.setCurrentIndex(idx)
        channel_idx = self.update_channel.findData(s.update_channel)
        self.update_channel.setCurrentIndex(max(0, channel_idx))
        self.check_updates.setChecked(bool(s.check_updates))

    def save(self) -> None:
        s = load_settings()
        s.data_dir = self.data_dir.text().strip() or s.data_dir
        s.backend = self.backend.currentText().strip() or s.backend
        s.vision_model = self.vision_model.text().strip() or s.vision_model
        s.ollama_host = self.ollama_host.text().strip() or s.ollama_host
        s.iqa_device = self.iqa_device.currentText().strip() or s.iqa_device
        s.qrealign_variant = self.qrealign_variant.currentText().strip() or s.qrealign_variant
        s.update_channel = str(self.update_channel.currentData() or "stable")
        s.check_updates = self.check_updates.isChecked()
        path = save_settings(s)
        self.status.setText(f"Saved {path}")
        self.reload()

    def _browse_data_dir(self) -> None:
        start = self.data_dir.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Choose data directory", start)
        if chosen:
            self.data_dir.setText(chosen)

    def _open_settings_folder(self) -> None:
        folder = app_data_root()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _set_busy(self, busy: bool) -> None:
        self.save_btn.setEnabled(not busy)
        self.doctor_btn.setEnabled(not busy)
        self.setup_btn.setEnabled(not busy)
        self.progress.set_running(busy)

    def run_doctor(self) -> None:
        if self._worker.busy:
            return
        self._mode = "doctor"
        self._set_busy(True)
        self.status.setText("Running doctor…")
        self._worker.run_doctor(pipeline=True)

    def run_setup(self) -> None:
        if self._worker.busy:
            return
        self.save()
        self._mode = "setup"
        self.progress.reset()
        self._set_busy(True)
        self.status.setText("Re-running setup…")
        self._worker.run_setup()

    def _on_doctor_json(self, payload: dict) -> None:
        recs = payload.get("recommendations") or {}
        ready = bool(recs.get("ready_for_pipeline"))
        msg = "Ready" if ready else "Setup needed"
        self.status.setText(f"Doctor: {msg}")
        self.readiness_changed.emit(ready, msg)

    def _on_failed(self, message: str) -> None:
        self.status.setText(message)
        self.progress.append_text(f"[error] {message}")

    def _on_finished(self, code: int) -> None:
        mode = self._mode
        self._mode = "idle"
        self._set_busy(False)
        if mode == "setup":
            if code == 0:
                self.status.setText("Setup finished. Re-checking…")
                self.run_doctor()
            else:
                self.status.setText(f"Setup exited with code {code}")
        elif mode == "doctor" and code != 0:
            self.status.setText(f"Doctor exited with code {code}")
        self.reload()
