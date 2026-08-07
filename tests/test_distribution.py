"""Unit tests for distribution helpers (no GPU / no GUI required)."""

from __future__ import annotations

from pathlib import Path

from gui.updates import is_newer, parse_version, preview_commit_differs
from paths import install_root, pipeline_root, requirements_path
from python_discover import discover_base_python
from settings import default_settings, settings_from_dict
from version_info import APP_VERSION, load_build_info


def test_parse_version_basic() -> None:
    assert parse_version("1.2.3") == (1, 2, 3)
    assert parse_version("v0.1.0") == (0, 1, 0)
    assert parse_version("0.1.0-preview") == (0, 1, 0)
    assert parse_version("0.1.0+abc1234") == (0, 1, 0)


def test_is_newer() -> None:
    assert is_newer("0.2.0", "0.1.0")
    assert not is_newer("0.1.0", "0.1.0")
    assert not is_newer("0.1.0", "0.2.0")
    assert is_newer("v1.0.0", "0.9.9")


def test_preview_commit_differs() -> None:
    local = "8ce80cc7be58e1ca129a72cae03bc498658665cf"
    assert not preview_commit_differs(local, "", local)
    assert preview_commit_differs("abcdef0123456789", "", local)
    assert preview_commit_differs("main", "Commit: abcdef0123456789", local)
    assert not preview_commit_differs("main", f"Commit: {local}", local)
    assert not preview_commit_differs("main", "No commit metadata", local)


def test_fresh_settings_defaults() -> None:
    s = default_settings()
    assert "MacroReview" in s.data_dir.replace("\\", "/")
    assert s.libraries == []
    assert s.update_channel in {"stable", "preview"}
    assert s.check_updates is True


def test_settings_roundtrip_preserves_empty_libraries() -> None:
    raw = {
        "schema_version": 2,
        "data_dir": "C:/tmp/data",
        "libraries": [],
        "backend": "ollama",
        "update_channel": "preview",
        "check_updates": False,
    }
    s = settings_from_dict(raw)
    assert s.libraries == []
    assert s.update_channel == "preview"
    assert s.check_updates is False


def test_paths_source_mode() -> None:
    root = pipeline_root()
    assert (root / "main.py").is_file()
    assert requirements_path().is_file()
    assert install_root() == root


def test_discover_base_python() -> None:
    candidate = discover_base_python()
    assert candidate is not None
    assert candidate.is_usable()
    assert candidate.version >= (3, 11, 0)


def test_build_info_dev() -> None:
    info = load_build_info()
    assert info.version == APP_VERSION
    assert info.repo


def test_pipeline_files_list_complete() -> None:
    listing = Path("packaging/pipeline_files.txt").read_text(encoding="utf-8")
    names = [
        line.strip()
        for line in listing.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert "main.py" in names
    assert "requirements.txt" in names
    for name in names:
        assert Path(name).is_file(), f"missing {name}"
