"""Resumable vision scoring loop."""

from __future__ import annotations

import json
import traceback
from datetime import datetime, timezone
from pathlib import Path

from tqdm import tqdm

import config
from db import db, fetchall, init_db
from vision_backend import PROMPT_VERSION, VisionBackend, get_backend


def _pending_rows(
    limit: int | None,
    force: bool,
    path_dirs: list[Path] | None = None,
) -> list:
    with db() as conn:
        if force:
            sql = """
                SELECT i.id, i.path, p.preview_path
                FROM images i
                JOIN previews p ON p.image_id = i.id
                WHERE p.error IS NULL
                ORDER BY i.source_library, i.filename
            """
            rows = fetchall(conn, sql)
        else:
            sql = """
                SELECT i.id, i.path, p.preview_path
                FROM images i
                JOIN previews p ON p.image_id = i.id
                LEFT JOIN scores s ON s.image_id = i.id
                WHERE p.error IS NULL
                  AND (
                    s.image_id IS NULL
                    OR s.error IS NOT NULL
                    OR s.prompt_version IS NULL
                    OR s.prompt_version != ?
                  )
                ORDER BY i.source_library, i.filename
            """
            rows = fetchall(conn, sql, (PROMPT_VERSION,))
    if path_dirs:
        rows = [r for r in rows if config.path_under_dirs(r["path"], path_dirs)]
    if limit is not None:
        rows = rows[:limit]
    return rows


def _save_score(image_id: str, result, error: str | None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    crop_box_json = json.dumps(result.crop_box) if result and result.crop_box else None
    subject_box_json = json.dumps(result.subject_box) if result and result.subject_box else None
    eye_box_json = json.dumps(result.eye_box) if result and result.eye_box else None
    with db() as conn:
        conn.execute(
            """
            INSERT INTO scores (
                image_id, overall_score, sharpness_score, composition_score,
                subject, crop_worthy, crop_box, crop_reason, comment,
                model, backend, raw_response, scored_at, error,
                eye_focus_score, dof_quality, background_score, lighting_score,
                distractions, pose_score, share_recommendation, prompt_version,
                subject_box, eye_box
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(image_id) DO UPDATE SET
                overall_score=excluded.overall_score,
                sharpness_score=excluded.sharpness_score,
                composition_score=excluded.composition_score,
                subject=excluded.subject,
                crop_worthy=excluded.crop_worthy,
                crop_box=excluded.crop_box,
                crop_reason=excluded.crop_reason,
                comment=excluded.comment,
                model=excluded.model,
                backend=excluded.backend,
                raw_response=excluded.raw_response,
                scored_at=excluded.scored_at,
                error=excluded.error,
                eye_focus_score=excluded.eye_focus_score,
                dof_quality=excluded.dof_quality,
                background_score=excluded.background_score,
                lighting_score=excluded.lighting_score,
                distractions=excluded.distractions,
                pose_score=excluded.pose_score,
                share_recommendation=excluded.share_recommendation,
                prompt_version=excluded.prompt_version,
                subject_box=excluded.subject_box,
                eye_box=excluded.eye_box
            """,
            (
                image_id,
                None if result is None else result.overall_score,
                None if result is None else result.sharpness_score,
                None if result is None else result.composition_score,
                None if result is None else result.subject,
                None if result is None else (1 if result.crop_worthy else 0),
                crop_box_json,
                None if result is None else result.crop_reason,
                None if result is None else result.comment,
                None if result is None else result.model,
                None if result is None else result.backend,
                None if result is None else result.raw_response,
                now,
                error,
                None if result is None else result.eye_focus_score,
                None if result is None else result.dof_quality,
                None if result is None else result.background_score,
                None if result is None else result.lighting_score,
                None if result is None else result.distractions,
                None if result is None else result.pose_score,
                None if result is None else result.share_recommendation,
                None if result is None else result.prompt_version,
                subject_box_json,
                eye_box_json,
            ),
        )


def analyze_images(
    *,
    limit: int | None = None,
    backend_name: str | None = None,
    force: bool = False,
    backend: VisionBackend | None = None,
    path_dirs: list[Path] | None = None,
) -> int:
    """Score unscored (or outdated-prompt) images. Returns number successfully scored."""
    init_db()
    config.ensure_dirs()
    engine = backend or get_backend(backend_name)
    rows = _pending_rows(limit, force, path_dirs=path_dirs)
    if not rows:
        print("No images pending analysis.")
        return 0

    log_path = config.LOG_DIR / "analyze_errors.log"
    scored = 0
    for row in tqdm(rows, desc="Analyzing", unit="img"):
        image_id = row["id"]
        preview = Path(row["preview_path"])
        try:
            result = engine.score(preview)
            _save_score(image_id, result, None)
            scored += 1
        except Exception as exc:
            _save_score(image_id, None, str(exc))
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(f"{datetime.now(timezone.utc).isoformat()} | {row['path']} | {exc}\n")
                fh.write(traceback.format_exc() + "\n")
            print(f"WARNING: score failed for {row['path']}: {exc}")

    print(f"Scored {scored}/{len(rows)} images (prompt {PROMPT_VERSION}).")
    return scored


if __name__ == "__main__":
    analyze_images(limit=20)
