"""Shared progress UI for long-running CLI jobs."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

SETUP_STAGES = (
    "setup_venv",
    "setup_pip",
    "setup_ollama",
    "setup_model",
    "setup_settings",
    "setup_verify",
)

# Backward-compatible alias used by older call sites.
STAGE_ORDER = SETUP_STAGES


class ProgressPanel(QWidget):
    cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._stage_index: dict[str, int] = {}

        self.stage_label = QLabel("Idle")
        self.overall = QProgressBar()
        self.overall.setRange(0, 1)
        self.overall.setValue(0)
        self.item = QProgressBar()
        self.item.setRange(0, 100)
        self.item.setValue(0)
        self.item.setVisible(False)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("SecondaryButton")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_requested.emit)

        row = QHBoxLayout()
        row.addWidget(self.stage_label, 1)
        row.addWidget(self.cancel_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(row)
        layout.addWidget(QLabel("Overall"))
        layout.addWidget(self.overall)
        layout.addWidget(QLabel("Current step"))
        layout.addWidget(self.item)
        layout.addWidget(self.log, 1)

        self.set_stages(list(SETUP_STAGES))

    def set_stages(self, names: list[str]) -> None:
        clean = [str(n) for n in names if str(n).strip()]
        if not clean:
            self._stage_index = {}
            self.overall.setRange(0, 1)
            self.overall.setValue(0)
            return
        self._stage_index = {name: i for i, name in enumerate(clean)}
        self.overall.setRange(0, max(len(clean), 1))
        self.overall.setValue(0)

    def reset(self, *, stages: list[str] | None = None) -> None:
        if stages is None:
            self.set_stages(list(SETUP_STAGES))
        else:
            self.set_stages(stages)
        self.stage_label.setText("Starting…")
        self.overall.setValue(0)
        self.item.setValue(0)
        self.item.setVisible(False)
        self.log.clear()
        self.cancel_btn.setEnabled(True)

    def set_running(self, running: bool) -> None:
        self.cancel_btn.setEnabled(running)
        if not running and self.stage_label.text() in {"Starting…", "Idle"}:
            self.stage_label.setText("Idle")

    def append_text(self, text: str) -> None:
        self.log.appendPlainText(text)

    def handle_event(self, event: dict) -> None:
        etype = event.get("type")
        if etype == "run_start":
            stages = event.get("stages") or []
            if isinstance(stages, list) and stages:
                self.set_stages([str(s) for s in stages])
            self.append_text(f"[run_start] {len(self._stage_index)} stages")
        elif etype == "stage_start":
            stage = str(event.get("stage") or "")
            message = str(event.get("message") or stage)
            self.stage_label.setText(message)
            idx = self._stage_index.get(stage)
            if idx is not None:
                self.overall.setValue(idx)
            self.append_text(f"[stage] {message}")
        elif etype == "stage_done":
            stage = str(event.get("stage") or "")
            message = str(event.get("message") or f"{stage} done")
            idx = self._stage_index.get(stage)
            if idx is not None:
                self.overall.setValue(idx + 1)
            self.append_text(f"[done] {message}")
        elif etype == "item":
            current = int(event.get("current") or 0)
            total = int(event.get("total") or 0)
            message = str(event.get("message") or "")
            if total > 0:
                self.item.setVisible(True)
                self.item.setRange(0, total)
                self.item.setValue(min(current, total))
            if message:
                self.stage_label.setText(message)
                self.append_text(message)
        elif etype == "log":
            message = str(event.get("message") or "")
            stage = str(event.get("stage") or "")
            prefix = f"[{stage}] " if stage else ""
            self.append_text(f"{prefix}{message}")
        elif etype in {"error", "warning"}:
            message = str(event.get("message") or etype)
            self.append_text(f"[{etype}] {message}")
        elif etype == "run_done":
            ok = event.get("ok")
            self.append_text(f"[run_done] ok={ok}")
            if self.overall.maximum() > 0:
                self.overall.setValue(self.overall.maximum())
        else:
            self.append_text(str(event))
