"""Export full-resolution crops for top crop-worthy images."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from tqdm import tqdm

import config
from db import db, fetchall, init_db
from image_io import box_to_pixels, load_full_image


def _apply_crop(img, box: list[float]):
    w, h = img.size
    left, top, right, bottom = box_to_pixels(box, w, h)
    return img.crop((left, top, right, bottom))


def export_crops(
    *,
    score_threshold: float | None = None,
    limit: int | None = None,
    path_dirs: list[Path] | None = None,
) -> int:
    """Crop and save top crop-worthy images. Returns number exported."""
    init_db()
    config.ensure_dirs()
    threshold = score_threshold if score_threshold is not None else config.CROP_SCORE_THRESHOLD
    cap = limit if limit is not None else config.TOP_CROP_LIMIT

    with db() as conn:
        fetch_cap = cap * 20 if path_dirs else cap
        rows = fetchall(
            conn,
            """
            SELECT i.id, i.path, i.filename,
                   COALESCE(r.share_score, s.overall_score) AS score,
                   s.crop_box
            FROM scores s
            JOIN images i ON i.id = s.image_id
            LEFT JOIN rankings r ON r.image_id = i.id
            WHERE s.error IS NULL
              AND s.crop_worthy = 1
              AND s.crop_box IS NOT NULL
              AND COALESCE(r.share_score, s.overall_score) >= ?
            ORDER BY score DESC, i.filename ASC
            LIMIT ?
            """,
            (threshold, fetch_cap),
        )
    if path_dirs:
        rows = [r for r in rows if config.path_under_dirs(r["path"], path_dirs)]
    rows = rows[:cap]

    exported = 0
    now = datetime.now(timezone.utc).isoformat()
    for row in tqdm(rows, desc="Crop export", unit="img"):
        image_id = row["id"]
        src = Path(row["path"])
        try:
            box = json.loads(row["crop_box"])
            if not isinstance(box, list) or len(box) != 4:
                raise ValueError(f"bad crop_box: {row['crop_box']}")
            img = load_full_image(src)
            cropped = _apply_crop(img, [float(v) for v in box])
            stem = Path(row["filename"]).stem
            score_tag = f"{float(row['score']):.1f}".replace(".", "_")
            dest = config.CROP_DIR / f"{score_tag}_{stem}_{image_id[:8]}.jpg"
            cropped.convert("RGB").save(dest, format="JPEG", quality=92, optimize=True)
            error = None
            export_path = str(dest)
            exported += 1
        except Exception as exc:
            error = str(exc)
            export_path = ""
            print(f"WARNING: crop failed for {src}: {exc}")

        with db() as conn:
            conn.execute(
                """
                INSERT INTO crop_exports (image_id, export_path, exported_at, error)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(image_id) DO UPDATE SET
                    export_path=excluded.export_path,
                    exported_at=excluded.exported_at,
                    error=excluded.error
                """,
                (image_id, export_path, now, error),
            )

    print(f"Exported {exported} crops to {config.CROP_DIR}")
    return exported


if __name__ == "__main__":
    export_crops()
