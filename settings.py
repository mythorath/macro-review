"""User settings load/save for macro-review (workspace paths + runtime knobs)."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from version_info import default_update_channel

SCHEMA_VERSION = 2

UpdateChannel = Literal["stable", "preview"]


def app_data_root() -> Path:
    """%LOCALAPPDATA%\\MacroReview on Windows; ~/.local/share/MacroReview elsewhere."""
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "MacroReview"
    return Path.home() / ".local" / "share" / "MacroReview"


def default_settings_path() -> Path:
    override = os.environ.get("MACROREVIEW_SETTINGS", "").strip()
    if override:
        return Path(override).expanduser()
    return app_data_root() / "settings.json"


def default_data_dir_for_new_install() -> Path:
    """Preferred data dir for fresh installs."""
    return app_data_root() / "data"


@dataclass
class LibraryEntry:
    name: str
    path: str


@dataclass
class AppSettings:
    schema_version: int = SCHEMA_VERSION
    data_dir: str = ""
    libraries: list[LibraryEntry] = field(default_factory=list)
    backend: str = "ollama"
    ollama_host: str = "http://localhost:11435"
    vision_model: str = "qwen3.6:35b"
    iqa_device: str = "cuda"
    qrealign_variant: str = "qrealign-lite"
    recursive_default: bool = False
    pipeline_python: str = ""
    base_python: str = ""
    update_channel: str = "stable"
    check_updates: bool = True
    last_update_check: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "data_dir": self.data_dir,
            "libraries": [asdict(lib) for lib in self.libraries],
            "backend": self.backend,
            "ollama_host": self.ollama_host,
            "vision_model": self.vision_model,
            "iqa_device": self.iqa_device,
            "qrealign_variant": self.qrealign_variant,
            "recursive_default": self.recursive_default,
            "pipeline_python": self.pipeline_python,
            "base_python": self.base_python,
            "update_channel": self.update_channel,
            "check_updates": self.check_updates,
            "last_update_check": self.last_update_check,
        }


def default_settings() -> AppSettings:
    """Fresh-install defaults (no personal libraries / machine paths)."""
    return AppSettings(
        schema_version=SCHEMA_VERSION,
        data_dir=str(default_data_dir_for_new_install()),
        libraries=[],
        backend="ollama",
        ollama_host="http://localhost:11435",
        vision_model="qwen3.6:35b",
        iqa_device="cuda",
        qrealign_variant="qrealign-lite",
        recursive_default=False,
        pipeline_python="",
        base_python="",
        update_channel=default_update_channel(),
        check_updates=True,
        last_update_check="",
    )


def _parse_libraries(raw: Any) -> list[LibraryEntry]:
    if not isinstance(raw, list):
        return []
    out: list[LibraryEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        if not name:
            name = Path(path).name or "library"
        out.append(LibraryEntry(name=name, path=path))
    return out


def _normalize_channel(raw: Any, fallback: str) -> str:
    value = str(raw or "").strip().lower()
    if value in {"stable", "preview"}:
        return value
    return fallback


def settings_from_dict(data: dict[str, Any]) -> AppSettings:
    base = default_settings()
    libraries = _parse_libraries(data.get("libraries"))
    # Empty list in file means "no libraries"; only fall back when key missing.
    if "libraries" not in data:
        libraries = list(base.libraries)
    data_dir = str(data.get("data_dir") or "").strip() or base.data_dir
    return AppSettings(
        schema_version=int(data.get("schema_version") or SCHEMA_VERSION),
        data_dir=data_dir,
        libraries=libraries,
        backend=str(data.get("backend") or base.backend),
        ollama_host=str(data.get("ollama_host") or base.ollama_host),
        vision_model=str(data.get("vision_model") or base.vision_model),
        iqa_device=str(data.get("iqa_device") or base.iqa_device),
        qrealign_variant=str(data.get("qrealign_variant") or base.qrealign_variant),
        recursive_default=bool(data.get("recursive_default", base.recursive_default)),
        pipeline_python=str(data.get("pipeline_python") or ""),
        base_python=str(data.get("base_python") or ""),
        update_channel=_normalize_channel(data.get("update_channel"), base.update_channel),
        check_updates=bool(data.get("check_updates", base.check_updates)),
        last_update_check=str(data.get("last_update_check") or ""),
    )


def load_settings(path: Path | None = None) -> AppSettings:
    settings_path = path or default_settings_path()
    if not settings_path.is_file():
        return default_settings()
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_settings()
    if not isinstance(data, dict):
        return default_settings()
    return settings_from_dict(data)


def save_settings(settings: AppSettings, path: Path | None = None) -> Path:
    settings_path = path or default_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings.schema_version = SCHEMA_VERSION
    payload = json.dumps(settings.to_dict(), indent=2, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix="settings_",
        suffix=".json",
        dir=str(settings_path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        Path(tmp_name).replace(settings_path)
    except Exception:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return settings_path


def init_settings(*, force: bool = False, path: Path | None = None) -> tuple[Path, bool]:
    """
    Write default settings if missing.
    Returns (path, created). Raises FileExistsError when present and not force.
    """
    settings_path = path or default_settings_path()
    if settings_path.is_file() and not force:
        raise FileExistsError(f"Settings already exist: {settings_path}")
    save_settings(default_settings(), settings_path)
    return settings_path, True
