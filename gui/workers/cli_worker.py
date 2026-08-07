"""Subprocess bridge to main.py doctor/setup/run CLI."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from gui import REPO_ROOT
from gui.pipeline_exe import managed_python


class CliWorker(QObject):
    line_received = Signal(dict)
    text_received = Signal(str)
    json_finished = Signal(dict)
    failed = Signal(str)
    finished = Signal(int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process: QProcess | None = None
        self._json_mode = False
        self._stdout_buf = ""

    @property
    def busy(self) -> bool:
        return self._process is not None and self._process.state() != QProcess.NotRunning

    def cancel(self) -> None:
        if self._process and self._process.state() != QProcess.NotRunning:
            self._process.kill()

    def run_doctor(self, *, pipeline: bool = False) -> None:
        args = ["doctor", "--json"]
        if pipeline:
            args.append("--pipeline")
        self._start(args, json_mode=True, executable=sys.executable)

    def run_setup(
        self,
        *,
        skip_ollama: bool = False,
        skip_model: bool = False,
        force_recreate: bool = False,
    ) -> None:
        args = ["setup", "--yes", "--progress", "jsonl"]
        if skip_ollama:
            args.append("--skip-ollama")
        if skip_model:
            args.append("--skip-model")
        if force_recreate:
            args.append("--force-recreate-venv")
        self._start(args, json_mode=False, executable=sys.executable)

    def run_pipeline(
        self,
        dir_path: str,
        *,
        recursive: bool = False,
        limit: int | None = None,
        force: bool = False,
    ) -> None:
        exe = managed_python()
        if exe is None:
            self.failed.emit("pipeline_python is not set. Run setup first.")
            return
        folder = Path(dir_path).expanduser()
        if not folder.is_dir():
            self.failed.emit(f"Folder not found: {folder}")
            return
        args = ["run", "--dir", str(folder), "--progress", "jsonl"]
        if recursive:
            args.append("--recursive")
        if limit is not None and limit > 0:
            args.extend(["--limit", str(int(limit))])
        if force:
            args.append("--force")
        self._start(args, json_mode=False, executable=str(exe))

    def run_crop_export(
        self,
        *,
        threshold: float | None = None,
        limit: int | None = None,
        dir_path: str | None = None,
        recursive: bool = False,
    ) -> None:
        exe = managed_python()
        if exe is None:
            self.failed.emit("pipeline_python is not set. Run setup first.")
            return
        args = ["crop-export", "--progress", "jsonl"]
        if dir_path:
            folder = Path(dir_path).expanduser()
            if not folder.is_dir():
                self.failed.emit(f"Folder not found: {folder}")
                return
            args.extend(["--dir", str(folder)])
            if recursive:
                args.append("--recursive")
        if threshold is not None:
            args.extend(["--threshold", str(float(threshold))])
        if limit is not None and limit > 0:
            args.extend(["--limit", str(int(limit))])
        self._start(args, json_mode=False, executable=str(exe))

    def _start(self, args: list[str], *, json_mode: bool, executable: str) -> None:
        if self.busy:
            self.failed.emit("Another command is already running.")
            return

        self._json_mode = json_mode
        self._stdout_buf = ""
        main_py = REPO_ROOT / "main.py"
        if not main_py.is_file():
            self.failed.emit(f"main.py not found at {main_py}")
            return

        proc = QProcess(self)
        self._process = proc
        proc.setProgram(executable)
        proc.setArguments([str(main_py), *args])
        proc.setWorkingDirectory(str(REPO_ROOT))
        env = QProcessEnvironment.systemEnvironment()
        if not json_mode:
            env.insert("MACROREVIEW_PROGRESS", "jsonl")
        pythonpath = env.value("PYTHONPATH")
        root = str(REPO_ROOT)
        env.insert(
            "PYTHONPATH",
            root if not pythonpath else f"{root}{os.pathsep}{pythonpath}",
        )
        proc.setProcessEnvironment(env)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        proc.start()

    def _on_stdout(self) -> None:
        if not self._process:
            return
        raw = bytes(self._process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if self._json_mode:
            self._stdout_buf += raw
            return
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                self.text_received.emit(line)
                continue
            if isinstance(payload, dict):
                self.line_received.emit(payload)
            else:
                self.text_received.emit(line)

    def _on_finished(self, code: int, _status) -> None:
        if self._json_mode:
            text = self._stdout_buf.strip()
            if text:
                try:
                    payload = json.loads(text)
                    if isinstance(payload, dict):
                        self.json_finished.emit(payload)
                    else:
                        self.failed.emit("Doctor returned non-object JSON.")
                except json.JSONDecodeError as exc:
                    self.failed.emit(f"Doctor JSON parse failed: {exc}")
            elif code != 0:
                self.failed.emit(f"Doctor exited with code {code}")
        self.finished.emit(int(code))
        self._process = None

    def _on_error(self, error) -> None:
        if error == QProcess.FailedToStart:
            self.failed.emit("Failed to start CLI process.")
            self._process = None
        else:
            self.failed.emit(f"Process error: {error}")
