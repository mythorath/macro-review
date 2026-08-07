"""Macro Review desktop GUI (PySide6)."""

from __future__ import annotations

from pathlib import Path

from paths import install_root, pipeline_root, resource_root

GUI_ROOT = Path(__file__).resolve().parent
# Pipeline source root (repo root in development, <install>/pipeline when packaged).
REPO_ROOT = pipeline_root()
INSTALL_ROOT = install_root()
RESOURCE_ROOT = resource_root()
