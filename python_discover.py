"""Discover a 64-bit system Python 3.11+ suitable for creating the managed venv."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from paths import is_frozen


@dataclass(frozen=True)
class PythonCandidate:
    executable: Path
    version: tuple[int, int, int]
    is_64bit: bool
    source: str

    @property
    def version_str(self) -> str:
        return ".".join(str(part) for part in self.version)

    def is_usable(self) -> bool:
        return self.is_64bit and self.version >= (3, 11, 0)


_PROBE_SCRIPT = (
    "import json,platform,sys;"
    "print(json.dumps({"
    "'version':list(sys.version_info[:3]),"
    "'is_64bit':sys.maxsize>2**32,"
    "'executable':sys.executable"
    "}))"
)


def _run_probe(exe: Path) -> PythonCandidate | None:
    try:
        proc = subprocess.run(
            [str(exe), "-c", _PROBE_SCRIPT],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except json.JSONDecodeError:
        return None
    version_raw = payload.get("version") or []
    if not isinstance(version_raw, list) or len(version_raw) < 3:
        return None
    try:
        version = (int(version_raw[0]), int(version_raw[1]), int(version_raw[2]))
    except (TypeError, ValueError):
        return None
    return PythonCandidate(
        executable=Path(str(payload.get("executable") or exe)),
        version=version,
        is_64bit=bool(payload.get("is_64bit")),
        source=str(exe),
    )


def _unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        try:
            key = str(path.resolve()).lower()
        except OSError:
            key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def _candidate_executables() -> list[Path]:
    found: list[Path] = []

    # Prefer the Windows Python launcher.
    py_launcher = shutil.which("py")
    if py_launcher:
        for args in (["-3.13"], ["-3.12"], ["-3.11"], ["-3"]):
            try:
                proc = subprocess.run(
                    [py_launcher, *args, "-c", "import sys; print(sys.executable)"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if proc.returncode == 0 and proc.stdout.strip():
                found.append(Path(proc.stdout.strip()))

    for name in ("python", "python3"):
        which = shutil.which(name)
        if which:
            found.append(Path(which))

    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        roots = [
            Path(local) / "Programs" / "Python" if local else None,
            Path(program_files) / "Python313",
            Path(program_files) / "Python312",
            Path(program_files) / "Python311",
            Path(r"C:\Python313"),
            Path(r"C:\Python312"),
            Path(r"C:\Python311"),
        ]
        for root in roots:
            if root is None:
                continue
            if root.is_dir():
                # Nested installs under Local\Programs\Python\Python3xx
                for child in [root, *root.glob("Python3*")]:
                    exe = child / "python.exe"
                    if exe.is_file():
                        found.append(exe)

    # Current interpreter is usable only when not frozen.
    if not is_frozen() and sys.executable:
        found.append(Path(sys.executable))

    return _unique_paths(found)


def probe_python(exe: Path | str) -> PythonCandidate | None:
    return _run_probe(Path(exe))


def discover_base_python() -> PythonCandidate | None:
    """Return the best usable 64-bit Python 3.11+ candidate, or None."""
    usable: list[PythonCandidate] = []
    for exe in _candidate_executables():
        candidate = _run_probe(exe)
        if candidate is None:
            continue
        if candidate.is_usable():
            usable.append(candidate)
    if not usable:
        return None
    # Prefer newest version, then prefer non-venv-ish paths later via source.
    usable.sort(key=lambda c: c.version, reverse=True)
    return usable[0]


def require_base_python() -> PythonCandidate:
    candidate = discover_base_python()
    if candidate is None:
        raise RuntimeError(
            "No 64-bit Python 3.11+ found. Install Python from https://www.python.org/ "
            "and ensure `py` or `python` is on PATH, then re-run setup."
        )
    return candidate
