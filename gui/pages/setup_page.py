"""Setup wizard page — doctor cards, advanced options, run setup."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gui.widgets.collapsible import bind_collapsible
from gui.widgets.progress_panel import ProgressPanel
from gui.workers.cli_worker import CliWorker
from settings import load_settings, save_settings


class SetupPage(QWidget):
    readiness_changed = Signal(bool, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._worker = CliWorker(self)
        self._mode = "idle"  # idle | doctor | setup | verify
        self._last_ready = False

        self._gpu_value = QLabel("—")
        self._torch_value = QLabel("—")
        self._ollama_value = QLabel("—")
        self._disk_value = QLabel("—")
        self._ready_value = QLabel("—")
        self._recs_label = QLabel("Run Check system to see recommendations.")
        self._recs_label.setWordWrap(True)
        self._recs_label.setObjectName("PageSubtitle")
        self._status = QLabel("")
        self._status.setWordWrap(True)

        self.backend = QComboBox()
        self.backend.addItems(["ollama", "openai"])
        self.vision_model = QLineEdit()
        self.ollama_host = QLineEdit()
        self.skip_ollama = QCheckBox("Skip Ollama install")
        self.skip_model = QCheckBox("Skip model pull")
        self.force_recreate = QCheckBox("Recreate venv")

        self.check_btn = QPushButton("Check system")
        self.check_btn.setObjectName("SecondaryButton")
        self.apply_btn = QPushButton("Apply advanced options")
        self.apply_btn.setObjectName("SecondaryButton")
        self.setup_btn = QPushButton("Run setup")
        self.verify_btn = QPushButton("Verify pipeline")
        self.verify_btn.setObjectName("SecondaryButton")

        self.progress = ProgressPanel()

        self.check_btn.clicked.connect(self.run_doctor)
        self.apply_btn.clicked.connect(self._apply_advanced)
        self.setup_btn.clicked.connect(self.run_setup)
        self.verify_btn.clicked.connect(self.run_verify)
        self.progress.cancel_requested.connect(self._worker.cancel)

        self._worker.line_received.connect(self.progress.handle_event)
        self._worker.text_received.connect(self.progress.append_text)
        self._worker.json_finished.connect(self.apply_doctor_payload)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_finished)

        self._load_advanced_from_settings()
        self._build_ui()

    def _build_ui(self) -> None:
        title = QLabel("Setup")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "Check this machine, then install recommended components into the managed environment."
        )
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)

        cards = QGridLayout()
        cards.setSpacing(10)
        cards.addWidget(self._make_card("GPU", self._gpu_value), 0, 0)
        cards.addWidget(self._make_card("Torch", self._torch_value), 0, 1)
        cards.addWidget(self._make_card("Ollama", self._ollama_value), 1, 0)
        cards.addWidget(self._make_card("Disk", self._disk_value), 1, 1)
        cards.addWidget(self._make_card("Ready", self._ready_value), 2, 0, 1, 2)

        detect_box = QGroupBox("1. Detect")
        detect_layout = QVBoxLayout(detect_box)
        detect_row = QHBoxLayout()
        detect_row.addWidget(self.check_btn)
        detect_row.addStretch(1)
        detect_layout.addLayout(detect_row)
        detect_layout.addLayout(cards)

        recs_box = QGroupBox("2. Recommendations")
        recs_layout = QVBoxLayout(recs_box)
        recs_layout.addWidget(self._recs_label)

        advanced = QGroupBox("3. Advanced")
        advanced.setCheckable(True)
        advanced.setChecked(False)
        form = QFormLayout(advanced)
        form.addRow("Backend", self.backend)
        form.addRow("Vision model", self.vision_model)
        form.addRow("Ollama host", self.ollama_host)
        form.addRow(self.skip_ollama)
        form.addRow(self.skip_model)
        form.addRow(self.force_recreate)
        form.addRow(self.apply_btn)
        bind_collapsible(advanced)

        install_box = QGroupBox("4. Install")
        install_layout = QVBoxLayout(install_box)
        install_row = QHBoxLayout()
        install_row.addWidget(self.setup_btn)
        install_row.addStretch(1)
        install_layout.addLayout(install_row)
        install_layout.addWidget(self.progress)

        verify_box = QGroupBox("5. Verify")
        verify_layout = QVBoxLayout(verify_box)
        verify_row = QHBoxLayout()
        verify_row.addWidget(self.verify_btn)
        verify_row.addStretch(1)
        verify_layout.addLayout(verify_row)
        verify_layout.addWidget(self._status)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(detect_box)
        layout.addWidget(recs_box)
        layout.addWidget(advanced)
        layout.addWidget(install_box)
        layout.addWidget(verify_box)
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(inner)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

    def _make_card(self, title: str, value: QLabel) -> QFrame:
        frame = QFrame()
        frame.setObjectName("InfoCard")
        layout = QVBoxLayout(frame)
        heading = QLabel(title)
        heading.setObjectName("CardTitle")
        value.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(value)
        return frame

    def _load_advanced_from_settings(self) -> None:
        s = load_settings()
        idx = self.backend.findText(s.backend)
        if idx >= 0:
            self.backend.setCurrentIndex(idx)
        self.vision_model.setText(s.vision_model)
        self.ollama_host.setText(s.ollama_host)

    def _apply_advanced(self) -> None:
        s = load_settings()
        s.backend = self.backend.currentText().strip() or s.backend
        s.vision_model = self.vision_model.text().strip() or s.vision_model
        s.ollama_host = self.ollama_host.text().strip() or s.ollama_host
        path = save_settings(s)
        self._status.setText(f"Saved advanced options to {path}")

    def _set_busy(self, busy: bool) -> None:
        self.check_btn.setEnabled(not busy)
        self.setup_btn.setEnabled(not busy)
        self.verify_btn.setEnabled(not busy)
        self.apply_btn.setEnabled(not busy)
        self.progress.set_running(busy)

    def run_doctor(self, *, pipeline: bool = False) -> None:
        if self._worker.busy:
            return
        self._mode = "verify" if pipeline else "doctor"
        self._set_busy(True)
        self._status.setText("Checking system…")
        self._worker.run_doctor(pipeline=pipeline)

    def run_setup(self) -> None:
        if self._worker.busy:
            return
        self._apply_advanced()
        self._mode = "setup"
        self.progress.reset()
        self._set_busy(True)
        self._status.setText("Running setup — large downloads can take several minutes. Safe to re-run.")
        self._worker.run_setup(
            skip_ollama=self.skip_ollama.isChecked(),
            skip_model=self.skip_model.isChecked(),
            force_recreate=self.force_recreate.isChecked(),
        )

    def run_verify(self) -> None:
        self.run_doctor(pipeline=True)

    def apply_doctor_payload(self, payload: dict) -> None:
        profile = payload.get("profile") or {}
        recs = payload.get("recommendations") or {}

        gpus = profile.get("gpus") or []
        if gpus:
            names = []
            for g in gpus:
                name = g.get("name") or "GPU"
                vram = g.get("vram_mb")
                if vram:
                    names.append(f"{name} ({vram} MiB)")
                else:
                    names.append(str(name))
            self._gpu_value.setText("; ".join(names))
        else:
            self._gpu_value.setText("None detected")

        torch_info = profile.get("torch") or {}
        if torch_info.get("installed"):
            cuda = "CUDA" if torch_info.get("cuda_available") else "CPU"
            self._torch_value.setText(f"{torch_info.get('version')} ({cuda})")
        else:
            self._torch_value.setText("Not installed")

        ollama = profile.get("ollama") or {}
        if ollama.get("http_ok"):
            host = ollama.get("host") or ""
            model_ok = "model ok" if ollama.get("vision_model_present") else "model missing"
            self._ollama_value.setText(f"Reachable ({model_ok}) {host}".strip())
        elif ollama.get("binary_path"):
            self._ollama_value.setText(f"Installed, not reachable ({ollama.get('binary_path')})")
        else:
            self._ollama_value.setText("Not installed")

        disk = profile.get("disk") or {}
        free_bytes = disk.get("free_bytes")
        if isinstance(free_bytes, int) and free_bytes >= 0:
            self._disk_value.setText(f"{free_bytes / (1024 ** 3):.1f} GB free")
        else:
            self._disk_value.setText(disk.get("error") or "Unknown")

        ready = bool(recs.get("ready_for_pipeline"))
        self._last_ready = ready
        self._ready_value.setText("Yes" if ready else "No — setup needed")

        notes = recs.get("notes") or []
        lines = [
            f"IQA device: {recs.get('iqa_device', '—')}",
            f"Torch index: {recs.get('torch_index_url') or 'default/CPU'}",
            f"Vision model: {recs.get('vision_model', '—')}",
            f"Backend: {recs.get('backend', '—')}",
        ]
        if notes:
            lines.append("Notes: " + "; ".join(str(n) for n in notes))
        self._recs_label.setText("\n".join(lines))

        if not self.vision_model.text().strip() and recs.get("vision_model"):
            self.vision_model.setText(str(recs["vision_model"]))

        msg = "Ready for pipeline." if ready else "Setup needed."
        self._status.setText(msg)
        self.readiness_changed.emit(ready, msg)

    def _on_failed(self, message: str) -> None:
        self._status.setText(message)
        self.progress.append_text(f"[error] {message}")

    def _on_finished(self, code: int) -> None:
        mode = self._mode
        self._mode = "idle"
        self._set_busy(False)
        if mode == "setup":
            if code == 0:
                self.progress.append_text("Setup finished. Verifying managed pipeline…")
                self._status.setText("Setup finished. Verifying…")
                self.run_verify()
            else:
                self._status.setText(
                    f"Setup exited with code {code}. You can cancel mid-run and re-run safely."
                )
        elif mode in {"doctor", "verify"} and code != 0:
            self._status.setText(f"Doctor exited with code {code}")
