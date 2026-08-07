"""Frozen-aware path resolution for Macro Review.

Works in both source development (`python -m gui`) and packaged PyInstaller
one-folder layouts (`MacroReview.exe` + sibling `pipeline/`).
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def meipass_dir() -> Path | None:
    if not is_frozen():
        return None
    return Path(str(getattr(sys, "_MEIPASS")))


def executable_dir() -> Path:
    """Directory containing the launched executable (or python.exe in source)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_root() -> Path:
    """Read-only bundled resources (QSS, build_info.json, etc.)."""
    meipass = meipass_dir()
    if meipass is not None:
        return meipass
    return Path(__file__).resolve().parent


def install_root() -> Path:
    """Top-level install / portable folder (contains MacroReview.exe + pipeline/)."""
    if is_frozen():
        return executable_dir()
    return Path(__file__).resolve().parent


def pipeline_root() -> Path:
    """Directory with main.py and pipeline modules.

    Packaged layout: <install>/pipeline/
    Source layout: repository root (same as install_root).
    """
    if is_frozen():
        candidate = install_root() / "pipeline"
        if (candidate / "main.py").is_file():
            return candidate
        # Fallback: pipeline may have been collected into _MEIPASS during early builds.
        meipass = meipass_dir()
        if meipass is not None:
            bundled = meipass / "pipeline"
            if (bundled / "main.py").is_file():
                return bundled
        return candidate
    return Path(__file__).resolve().parent


def gui_resource(name: str) -> Path:
    """Path to a GUI data file such as style.qss."""
    root = resource_root()
    candidates = [
        root / "gui" / name,
        root / name,
        Path(__file__).resolve().parent / "gui" / name,
    ]
    for path in candidates:
        if path.is_file():
            return path
    return candidates[0]


def requirements_path() -> Path:
    root = pipeline_root()
    path = root / "requirements.txt"
    if path.is_file():
        return path
    return install_root() / "requirements.txt"


def build_info_path() -> Path:
    root = resource_root()
    for path in (
        root / "build_info.json",
        root / "gui" / "build_info.json",
        install_root() / "build_info.json",
    ):
        if path.is_file():
            return path
    return root / "build_info.json"
