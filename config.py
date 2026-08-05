"""Configuration for the macro photo review pipeline."""

from __future__ import annotations

import os
from pathlib import Path

# Project roots
PROJECT_ROOT = Path(r"E:\AI Picture review")
CACHE_DIR = PROJECT_ROOT / "cache"
PREVIEW_DIR = CACHE_DIR / "previews"
DB_PATH = CACHE_DIR / "review.db"
REPORT_PATH = PROJECT_ROOT / "report.html"
CSV_PATH = PROJECT_ROOT / "results.csv"
CROP_DIR = PROJECT_ROOT / "suggested_crops"
LOG_DIR = PROJECT_ROOT / "logs"

# Source libraries
SOURCE_DIRS: list[tuple[str, Path]] = [
    ("onedrive_macro", Path(r"C:\Users\mytho\OneDrive\Pictures\MACRO")),
    ("nas_macro", Path(r"\\VasNAS\VasNAS\Andrew\MACRO")),
    ("nas_bugs", Path(r"\\VasNAS\VasNAS\Andrew\Bugs")),
]

# Image extensions to analyze (lowercase, with leading dot)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".tif", ".tiff", ".cr3", ".dng"}
RAW_EXTENSIONS = {".cr3", ".dng"}
SKIP_EXTENSIONS = {".xmp", ".mp4", ".mov", ".avi"}

# Preview generation
PREVIEW_MAX_DIM = 1024
PREVIEW_JPEG_QUALITY = 85

# Ollama / vision backend
_raw_ollama = os.environ.get("OLLAMA_HOST", "http://localhost:11435").strip()
if _raw_ollama and "://" not in _raw_ollama:
    _raw_ollama = "http://" + _raw_ollama
# Bind address 0.0.0.0 is not a valid client target — rewrite to localhost.
_raw_ollama = _raw_ollama.replace("://0.0.0.0", "://localhost").replace("://[::]", "://localhost")
OLLAMA_HOST = _raw_ollama.rstrip("/") or "http://localhost:11435"
VISION_MODEL = os.environ.get("VISION_MODEL", "qwen3.6:35b")
BACKEND = os.environ.get("BACKEND", "ollama")  # "ollama" | "openai"
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
OLLAMA_TIMEOUT_SEC = 300
OPENAI_TIMEOUT_SEC = 120

# Crop export
CROP_SCORE_THRESHOLD = 7.0
TOP_CROP_LIMIT = 50

# Heuristics
OVEREXPOSE_THRESHOLD = 250
UNDEREXPOSE_THRESHOLD = 5

# Vision prompt version (bump when schema/prompt changes; triggers re-analyze)
PROMPT_VERSION = "macro_v3"

# IQA models (pyiqa metric names)
IQA_DEVICE = os.environ.get("IQA_DEVICE", "cuda")
QREALIGN_VARIANT = os.environ.get("QREALIGN_VARIANT", "qrealign-lite")

# Ensemble metrics: (storage_key, pyiqa_name, task_or_None, normalize_mode)
# normalize_mode: "unit10" = raw*10, "nima" = clamp 0-10, "unit01" = raw already 0-1 → *10
IQA_METRICS: list[tuple[str, str, str | None, str]] = [
    ("topiq_nr", "topiq_nr", None, "unit10"),
    ("clipiqa+", "clipiqa+", None, "unit10"),
    ("maniqa", "maniqa", None, "unit10"),
    ("laion_aes", "laion_aes", None, "unit10"),
    ("topiq_iaa", "topiq_iaa", None, "unit10"),
    ("nima", "nima", None, "nima"),
    ("qrealign_quality", QREALIGN_VARIANT, "quality", "unit10"),
    ("qrealign_aesthetic", QREALIGN_VARIANT, "aesthetic", "unit10"),
]

# Fast metrics first; qrealign last (heavy VLM)
IQA_FAST_KEYS = {"topiq_nr", "clipiqa+", "maniqa", "laion_aes", "topiq_iaa", "nima"}

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


def path_under_dirs(path: str | Path, dirs: list[Path] | None) -> bool:
    """True if path is inside any of dirs (or dirs is None/empty = no filter)."""
    if not dirs:
        return True
    target = normalize_fs_path(path)
    for d in dirs:
        prefix = normalize_fs_path(d)
        if target == prefix or target.startswith(prefix + "\\"):
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
    When dirs is None/empty, returns the default SOURCE_DIRS.
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
