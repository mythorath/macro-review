"""Generate cached preview JPEGs for vision scoring."""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

import config
from db import db, fetchall, init_db
from progress import get_reporter, track

try:
    import rawpy
except ImportError:  # pragma: no cover
    rawpy = None  # type: ignore


def _preview_path_for(image_id: str) -> Path:
    return config.PREVIEW_DIR / f"{image_id}.jpg"


def _resize_max(img: Image.Image, max_dim: int) -> Image.Image:
    img = img.convert("RGB")
    w, h = img.size
    longest = max(w, h)
    if longest <= max_dim:
        return img
    scale = max_dim / float(longest)
    new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
    return img.resize(new_size, Image.Resampling.LANCZOS)


def _load_standard(path: Path) -> Image.Image:
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        return img.copy()


def _load_raw(path: Path) -> Image.Image:
    if rawpy is None:
        raise RuntimeError("rawpy is not installed; cannot decode RAW files")
    with rawpy.imread(str(path)) as raw:
        try:
            thumb = raw.extract_thumb()
            if thumb.format == rawpy.ThumbFormat.JPEG:
                img = Image.open(BytesIO(thumb.data))
                img = ImageOps.exif_transpose(img)
                return img.copy()
            if thumb.format == rawpy.ThumbFormat.BITMAP:
                # bitmap thumb: shape (h, w, 3)
                import numpy as np

                arr = np.asarray(thumb.data)
                return Image.fromarray(arr)
        except Exception:
            pass
        # Fallback: full postprocess (slow)
        rgb = raw.postprocess(use_camera_wb=True, no_auto_bright=True, output_bps=8)
        return Image.fromarray(rgb)


def generate_preview(path: Path, dest: Path) -> tuple[int, int]:
    """Write a resized JPEG preview. Returns (width, height)."""
    ext = path.suffix.lower()
    if ext in config.RAW_EXTENSIONS:
        img = _load_raw(path)
    else:
        img = _load_standard(path)
    img = _resize_max(img, config.PREVIEW_MAX_DIM)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, format="JPEG", quality=config.PREVIEW_JPEG_QUALITY, optimize=True)
    return img.size


def build_previews(
    *,
    force: bool = False,
    limit: int | None = None,
    path_dirs: list[Path] | None = None,
    recursive: bool = True,
) -> int:
    """Generate missing/stale previews. Returns number newly written."""
    init_db()
    config.ensure_dirs()
    with db() as conn:
        rows = fetchall(
            conn,
            """
            SELECT i.id, i.path, i.mtime, p.preview_path, p.generated_at
            FROM images i
            LEFT JOIN previews p ON p.image_id = i.id
            ORDER BY i.source_library, i.filename
            """,
        )

    if path_dirs:
        rows = [
            r
            for r in rows
            if config.path_under_dirs(r["path"], path_dirs, recursive=recursive)
        ]

    # When limiting, prefer images that still need a usable preview.
    if limit is not None:
        pending = []
        for row in rows:
            dest = _preview_path_for(row["id"])
            needs = force or not dest.exists() or not row["preview_path"]
            if not needs:
                try:
                    if dest.stat().st_mtime < float(row["mtime"]):
                        needs = True
                except OSError:
                    needs = True
            if needs:
                pending.append(row)
        rows = pending[:limit]

    reporter = get_reporter()
    reporter.stage_start("preview", total=len(rows))
    written = 0
    failed = 0
    now = datetime.now(timezone.utc).isoformat()
    for row in track(rows, stage="preview", total=len(rows), desc="Previews"):
        image_id = row["id"]
        src = Path(row["path"])
        dest = _preview_path_for(image_id)

        if not force and dest.exists() and row["preview_path"]:
            try:
                if dest.stat().st_mtime >= float(row["mtime"]):
                    continue
            except OSError:
                pass

        error: str | None = None
        width = height = None
        try:
            if not src.exists():
                raise FileNotFoundError(f"source missing: {src}")
            width, height = generate_preview(src, dest)
            written += 1
        except Exception as exc:
            error = str(exc)
            failed += 1
            reporter.warning("preview", f"preview failed for {src}: {exc}")

        with db() as conn:
            conn.execute(
                """
                INSERT INTO previews (image_id, preview_path, width, height, generated_at, error)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(image_id) DO UPDATE SET
                    preview_path=excluded.preview_path,
                    width=excluded.width,
                    height=excluded.height,
                    generated_at=excluded.generated_at,
                    error=excluded.error
                """,
                (image_id, str(dest), width, height, now, error),
            )

    reporter.stage_done(
        "preview",
        ok=written,
        failed=failed,
        message=f"Generated {written} previews.",
    )
    return written


if __name__ == "__main__":
    build_previews()
