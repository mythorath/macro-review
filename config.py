"""Configuration for the macro photo review pipeline."""

from __future__ import annotations

import os
from pathlib import Path

from paths import pipeline_root
from settings import AppSettings, load_settings

# ---------------------------------------------------------------------------
# Product constants (not user settings)
# ---------------------------------------------------------------------------

# Image extensions to analyze (lowercase, with leading dot)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".tif", ".tiff", ".cr3", ".dng"}
RAW_EXTENSIONS = {".cr3", ".dng"}
SKIP_EXTENSIONS = {".xmp", ".mp4", ".mov", ".avi"}

# Preview generation
PREVIEW_MAX_DIM = 1024
PREVIEW_JPEG_QUALITY = 85

OLLAMA_TIMEOUT_SEC = 300
OPENAI_TIMEOUT_SEC = 120
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")

# Crop export
CROP_SCORE_THRESHOLD = 7.0
TOP_CROP_LIMIT = 50

# Heuristics
OVEREXPOSE_THRESHOLD = 250
UNDEREXPOSE_THRESHOLD = 5

# Vision prompt version (bump when schema/prompt changes; triggers re-analyze)
PROMPT_VERSION = "macro_v3"

# Dedup
PHASH_HAMMING_MAX = 10
BURST_GAP_SECONDS = 20.0

# Rank v2
RANK_VERSION = "blend_v2"

TECH_WEIGHTS = {
    "topiq_nr": 0.30,
    "clipiqa+": 0.20,
    "maniqa": 0.15,
    "qrealign_quality": 0.25,
    "eye_sharpness": 0.10,
}
AES_WEIGHTS = {
    "qrealign_aesthetic": 0.30,
    "topiq_iaa": 0.25,
    "laion_aes": 0.20,
    "nima": 0.25,
}
VLM_WEIGHTS = {
    "overall": 0.60,
    "eye_focus": 0.25,
    "pose": 0.15,
}
COMP_WEIGHTS = {
    "vlm_composition": 0.40,
    "thirds": 0.20,
    "bg_separation": 0.25,
    "clutter": 0.15,
}
SHARE_WEIGHTS_V2 = {
    "tech": 0.35,
    "aes": 0.25,
    "vlm": 0.25,
    "comp": 0.15,
}

# Legacy v1 weights (kept for reference / old rankings)
SHARE_WEIGHTS = {
    "maniqa": 0.35,
    "nima": 0.20,
    "vlm_overall": 0.25,
    "eye_focus": 0.10,
    "tech": 0.10,
}

IQA_FAST_KEYS = {"topiq_nr", "clipiqa+", "maniqa", "laion_aes", "topiq_iaa", "nima"}

# ---------------------------------------------------------------------------
# Runtime paths / knobs (populated by apply_settings)
# ---------------------------------------------------------------------------

CODE_ROOT = pipeline_root()

# Mutable module attrs — always access as config.X so apply_settings() works.
DATA_DIR: Path
PROJECT_ROOT: Path  # alias of DATA_DIR (legacy name)
CACHE_DIR: Path
PREVIEW_DIR: Path
DB_PATH: Path
REPORT_PATH: Path
CSV_PATH: Path
CROP_DIR: Path
LOG_DIR: Path
SOURCE_DIRS: list[tuple[str, Path]]
OLLAMA_HOST: str
VISION_MODEL: str
BACKEND: str
IQA_DEVICE: str
QREALIGN_VARIANT: str
IQA_METRICS: list[tuple[str, str, str | None, str]]
RECURSIVE_DEFAULT: bool
PIPELINE_PYTHON: str
_ACTIVE_SETTINGS: AppSettings | None = None


def _normalize_ollama_host(raw: str) -> str:
    host = raw.strip()
    if host and "://" not in host:
        host = "http://" + host
    host = host.replace("://0.0.0.0", "://localhost").replace("://[::]", "://localhost")
    return host.rstrip("/") or "http://localhost:11435"


def _env_or(settings_value: str, *env_keys: str) -> str:
    for key in env_keys:
        val = os.environ.get(key)
        if val is not None and str(val).strip() != "":
            return str(val).strip()
    return settings_value


def apply_settings(settings: AppSettings | None = None) -> AppSettings:
    """Apply settings (+ env overrides) to module-level path/knob attributes."""
    global DATA_DIR, PROJECT_ROOT, CACHE_DIR, PREVIEW_DIR, DB_PATH
    global REPORT_PATH, CSV_PATH, CROP_DIR, LOG_DIR, SOURCE_DIRS
    global OLLAMA_HOST, VISION_MODEL, BACKEND, IQA_DEVICE, QREALIGN_VARIANT
    global IQA_METRICS, RECURSIVE_DEFAULT, PIPELINE_PYTHON, _ACTIVE_SETTINGS

    loaded = settings if settings is not None else load_settings()
    _ACTIVE_SETTINGS = loaded

    data_dir_raw = _env_or(loaded.data_dir, "MACROREVIEW_DATA_DIR")
    data_dir = Path(data_dir_raw).expanduser()
    try:
        data_dir = data_dir.resolve()
    except OSError:
        data_dir = data_dir.absolute()

    DATA_DIR = data_dir
    PROJECT_ROOT = data_dir  # legacy alias
    CACHE_DIR = DATA_DIR / "cache"
    PREVIEW_DIR = CACHE_DIR / "previews"
    DB_PATH = CACHE_DIR / "review.db"
    REPORT_PATH = DATA_DIR / "report.html"
    CSV_PATH = DATA_DIR / "results.csv"
    CROP_DIR = DATA_DIR / "suggested_crops"
    LOG_DIR = DATA_DIR / "logs"

    SOURCE_DIRS = []
    for lib in loaded.libraries:
        root = Path(lib.path).expanduser()
        try:
            root = root.resolve()
        except OSError:
            root = root.absolute()
        SOURCE_DIRS.append((lib.name, root))

    BACKEND = _env_or(loaded.backend, "BACKEND").lower()
    OLLAMA_HOST = _normalize_ollama_host(_env_or(loaded.ollama_host, "OLLAMA_HOST"))
    VISION_MODEL = _env_or(loaded.vision_model, "VISION_MODEL")
    IQA_DEVICE = _env_or(loaded.iqa_device, "IQA_DEVICE")
    QREALIGN_VARIANT = _env_or(loaded.qrealign_variant, "QREALIGN_VARIANT")
    RECURSIVE_DEFAULT = bool(loaded.recursive_default)
    PIPELINE_PYTHON = str(loaded.pipeline_python or "").strip()

    # Ensemble metrics: (storage_key, pyiqa_name, task_or_None, normalize_mode)
    IQA_METRICS = [
        ("topiq_nr", "topiq_nr", None, "unit10"),
        ("clipiqa+", "clipiqa+", None, "unit10"),
        ("maniqa", "maniqa", None, "unit10"),
        ("laion_aes", "laion_aes", None, "unit10"),
        ("topiq_iaa", "topiq_iaa", None, "unit10"),
        ("nima", "nima", None, "nima"),
        ("qrealign_quality", QREALIGN_VARIANT, "quality", "unit10"),
        ("qrealign_aesthetic", QREALIGN_VARIANT, "aesthetic", "unit10"),
    ]
    return loaded


def reload() -> AppSettings:
    """Reload settings from disk and re-apply."""
    return apply_settings(load_settings())


def active_settings() -> AppSettings:
    if _ACTIVE_SETTINGS is None:
        return apply_settings()
    return _ACTIVE_SETTINGS


def ensure_dirs() -> None:
    """Create cache / output directories if missing."""
    for path in (CACHE_DIR, PREVIEW_DIR, CROP_DIR, LOG_DIR):
        path.mkdir(parents=True, exist_ok=True)


def normalize_fs_path(path: str | Path) -> str:
    """Case-normalized absolute path string for Windows / UNC comparisons."""
    p = Path(path).expanduser()
    try:
        p = p.resolve()
    except OSError:
        p = p.absolute()
    return str(p).replace("/", "\\").lower().rstrip("\\")


def path_under_dirs(
    path: str | Path,
    dirs: list[Path] | None,
    *,
    recursive: bool = True,
) -> bool:
    """True if path is inside any of dirs (or dirs is None/empty = no filter).

    When recursive=False, only files whose parent directory equals a listed dir
    match (subfolders are excluded).
    """
    if not dirs:
        return True
    target = normalize_fs_path(path)
    parent = normalize_fs_path(Path(path).parent)
    for d in dirs:
        prefix = normalize_fs_path(d)
        if recursive:
            if target == prefix or target.startswith(prefix + "\\"):
                return True
        elif parent == prefix:
            return True
    return False


def library_name_for(root: Path) -> str:
    """Pick a stable library label for a folder (reuse known names when possible)."""
    root_norm = normalize_fs_path(root)
    for name, known in SOURCE_DIRS:
        known_norm = normalize_fs_path(known)
        if root_norm == known_norm:
            return name
        # Subfolder of a known library → keep that library name
        if root_norm.startswith(known_norm + "\\"):
            return name
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in root.name)
    safe = safe.strip("_").lower() or "custom"
    return f"custom_{safe}"


def resolve_source_dirs(
    dirs: list[str | Path] | None = None,
) -> list[tuple[str, Path]]:
    """
    Resolve CLI --dir overrides into (library_name, Path) pairs.
    When dirs is None/empty, returns the configured SOURCE_DIRS.
    """
    if not dirs:
        return list(SOURCE_DIRS)
    resolved: list[tuple[str, Path]] = []
    for raw in dirs:
        root = Path(raw).expanduser()
        try:
            root = root.resolve()
        except OSError:
            root = root.absolute()
        resolved.append((library_name_for(root), root))
    return resolved


# Apply defaults at import so `import config` always has usable paths.
apply_settings()
