"""Scan source libraries and populate the images manifest."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from tqdm import tqdm

import config
from db import db, init_db, path_id, upsert_image


def _iter_images(
    source_dirs: list[tuple[str, Path]] | None = None,
) -> list[tuple[str, Path]]:
    roots = source_dirs if source_dirs is not None else config.SOURCE_DIRS
    found: list[tuple[str, Path]] = []
    for library, root in roots:
        if not root.exists():
            print(f"WARNING: source path missing, skipping: {root}")
            continue
        for path in root.rglob("*"):
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
) -> int:
    """Walk source dirs and upsert into SQLite. Returns count indexed."""
    init_db()
    files = _iter_images(source_dirs)
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    with db() as conn:
        for library, path in tqdm(files, desc="Indexing", unit="file"):
            try:
                stat = path.stat()
            except OSError as exc:
                print(f"WARNING: cannot stat {path}: {exc}")
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
    print(f"Indexed {count} images from: {label}")
    return count


if __name__ == "__main__":
    index_images()
