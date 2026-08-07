"""Scan source libraries and populate the images manifest."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import config
from db import db, init_db, path_id, upsert_image
from progress import get_reporter, track


def _iter_images(
    source_dirs: list[tuple[str, Path]] | None = None,
    *,
    recursive: bool = True,
) -> list[tuple[str, Path]]:
    roots = source_dirs if source_dirs is not None else config.SOURCE_DIRS
    found: list[tuple[str, Path]] = []
    reporter = get_reporter()
    for library, root in roots:
        if not root.exists():
            reporter.warning("index", f"source path missing, skipping: {root}")
            continue
        paths = root.rglob("*") if recursive else root.iterdir()
        for path in paths:
            if not path.is_file():
                continue
            ext = path.suffix.lower()
            if ext in config.SKIP_EXTENSIONS:
                continue
            if ext not in config.IMAGE_EXTENSIONS:
                continue
            found.append((library, path))
    return found


def index_images(
    source_dirs: list[tuple[str, Path]] | None = None,
    *,
    recursive: bool = True,
) -> int:
    """Walk source dirs and upsert into SQLite. Returns count indexed."""
    init_db()
    reporter = get_reporter()
    files = _iter_images(source_dirs, recursive=recursive)
    reporter.stage_start("index", total=len(files))
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    failed = 0
    with db() as conn:
        for library, path in track(files, stage="index", total=len(files), unit="file", desc="Indexing"):
            try:
                stat = path.stat()
            except OSError as exc:
                reporter.warning("index", f"cannot stat {path}: {exc}")
                failed += 1
                continue
            row = {
                "id": path_id(path),
                "path": str(path),
                "source_library": library,
                "filename": path.name,
                "stem": path.stem.lower(),
                "extension": path.suffix.lower(),
                "size_bytes": int(stat.st_size),
                "mtime": float(stat.st_mtime),
                "indexed_at": now,
            }
            upsert_image(conn, row)
            count += 1
    label = ", ".join(str(p) for _, p in (source_dirs or config.SOURCE_DIRS))
    mode = "recursive" if recursive else "direct only"
    reporter.stage_done(
        "index",
        ok=count,
        failed=failed,
        message=f"Indexed {count} images from: {label} ({mode})",
    )
    return count


if __name__ == "__main__":
    index_images()
