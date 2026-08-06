"""Local sharpness / exposure heuristics via OpenCV."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from tqdm import tqdm

import config
from db import db, fetchall, init_db


def _laplacian_variance(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _clipping_pct(gray: np.ndarray) -> tuple[float, float]:
    total = gray.size
    if total == 0:
        return 0.0, 0.0
    over = float(np.count_nonzero(gray >= config.OVEREXPOSE_THRESHOLD)) / total * 100.0
    under = float(np.count_nonzero(gray <= config.UNDEREXPOSE_THRESHOLD)) / total * 100.0
    return over, under


def _tech_score(sharpness: float, over_pct: float, under_pct: float) -> float:
    """Map Laplacian variance + clipping into a rough 0-10 technical score."""
    # Macro shots vary a lot; log scale keeps extremes usable.
    sharp_component = min(10.0, max(0.0, np.log1p(sharpness) * 1.2))
    clip_penalty = min(5.0, (over_pct + under_pct) / 8.0)
    return float(max(0.0, min(10.0, sharp_component - clip_penalty)))


def analyze_preview(preview_path: Path) -> dict[str, float]:
    img = cv2.imread(str(preview_path), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"OpenCV could not read preview: {preview_path}")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    sharpness = _laplacian_variance(gray)
    over_pct, under_pct = _clipping_pct(gray)
    return {
        "sharpness": sharpness,
        "overexpose_pct": over_pct,
        "underexpose_pct": under_pct,
        "tech_score": _tech_score(sharpness, over_pct, under_pct),
    }


def compute_heuristics(
    *,
    force: bool = False,
    limit: int | None = None,
    path_dirs: list[Path] | None = None,
    recursive: bool = True,
) -> int:
    """Compute heuristics for images with successful previews. Returns count updated."""
    init_db()
    with db() as conn:
        if force:
            rows = fetchall(
                conn,
                """
                SELECT i.id, i.path, p.preview_path
                FROM images i
                JOIN previews p ON p.image_id = i.id
                WHERE p.error IS NULL
                ORDER BY i.source_library, i.filename
                """,
            )
        else:
            rows = fetchall(
                conn,
                """
                SELECT i.id, i.path, p.preview_path
                FROM images i
                JOIN previews p ON p.image_id = i.id
                LEFT JOIN heuristics h ON h.image_id = i.id
                WHERE p.error IS NULL AND h.image_id IS NULL
                ORDER BY i.source_library, i.filename
                """,
            )
    if path_dirs:
        rows = [
            r
            for r in rows
            if config.path_under_dirs(r["path"], path_dirs, recursive=recursive)
        ]
    if limit is not None:
        rows = rows[:limit]

    updated = 0
    now = datetime.now(timezone.utc).isoformat()
    for row in tqdm(rows, desc="Heuristics", unit="img"):
        image_id = row["id"]
        preview = Path(row["preview_path"])
        error: str | None = None
        metrics: dict[str, float | None] = {
            "sharpness": None,
            "overexpose_pct": None,
            "underexpose_pct": None,
            "tech_score": None,
        }
        try:
            metrics = analyze_preview(preview)  # type: ignore[assignment]
            updated += 1
        except Exception as exc:
            error = str(exc)
            print(f"WARNING: heuristics failed for {preview}: {exc}")

        with db() as conn:
            conn.execute(
                """
                INSERT INTO heuristics
                    (image_id, sharpness, overexpose_pct, underexpose_pct, tech_score, computed_at, error)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(image_id) DO UPDATE SET
                    sharpness=excluded.sharpness,
                    overexpose_pct=excluded.overexpose_pct,
                    underexpose_pct=excluded.underexpose_pct,
                    tech_score=excluded.tech_score,
                    computed_at=excluded.computed_at,
                    error=excluded.error
                """,
                (
                    image_id,
                    metrics["sharpness"],
                    metrics["overexpose_pct"],
                    metrics["underexpose_pct"],
                    metrics["tech_score"],
                    now,
                    error,
                ),
            )

    print(f"Computed heuristics for {updated} images.")
    return updated


if __name__ == "__main__":
    compute_heuristics()
