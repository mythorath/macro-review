"""Resolve managed pipeline interpreter from settings."""

from __future__ import annotations

from pathlib import Path

from settings import load_settings


def managed_python() -> Path | None:
    """Return pipeline_python if it exists on disk; otherwise None."""
    settings = load_settings()
    raw = (settings.pipeline_python or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if path.is_file():
        return path
    return None
